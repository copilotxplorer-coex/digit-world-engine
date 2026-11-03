"""Test-only one Central process + two persistent Worker processes, one World.
Two independent authenticated HTTP client sessions; synthetic data only.
Deterministic worker dispatch is orchestrated by this harness, NOT production scheduling.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
import urllib.request
import urllib.error
from digit_world.config import WorldConfig
from digit_world.server import serve as game_serve,Runtime
from gameplay_worker.read_world import ReadWorld
from gameplay_worker.coordinator import GameplayCoordinator
from gameplay_worker.server import serve as worker_serve
from gameplay_worker.task import fingerprint

ROOT=Path(__file__).resolve().parents[1]

def require(condition,message):
    if not condition:raise RuntimeError(message)

def wait_for_move_ready(world,player_id,timeout=2.0):
    """Test harness only: observe authoritative cooldown; never reset game state."""
    deadline=time.monotonic()+timeout
    while True:
        with world.lock:
            remaining=world._actor(player_id)['move_after']-world.clock()
        if remaining<=0:return
        budget=deadline-time.monotonic()
        if budget<=0:raise RuntimeError('Test movement cooldown did not expire')
        time.sleep(min(remaining+.01,budget,.05))

class TestReadProvider:
    """Central-owned test provider: real HTTP workers; never waits in request path."""
    def __init__(self,store):
        self.store=store;self.jobs={};self.counts=Counter();self.closed=False
    def candidates(self,snapshot):
        if self.closed:return None
        key=fingerprint(snapshot)
        job=self.jobs.get(key)
        if job:
            try:
                result=self.store.consume(job,snapshot)
                self.counts['worker_hits']+=1
                return {(m['kind'],m['id']) for m in result['matches']}
            except ValueError:pass
        else:
            if len(self.jobs)>=64:return None
            self.jobs[key]=self.store.enqueue_snapshot(snapshot)
        self.counts['fallbacks']+=1
        return None
    def close(self):self.closed=True

class ProcessWorker:
    def __init__(self,config):
        self.lines=queue.Queue()
        self.process=subprocess.Popen([sys.executable,'-B','-m','gameplay_worker.multinode_runner',str(config)],cwd=ROOT,
                                      stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
        self.reader=threading.Thread(target=self._read,daemon=True);self.reader.start()
        try:require(self.receive().get('ready') is True,'Worker did not start')
        except Exception:self.stop();raise
    def _read(self):
        for line in self.process.stdout:self.lines.put(line)
        self.lines.put(None)
    def receive(self):
        try:line=self.lines.get(timeout=20)
        except queue.Empty:raise RuntimeError('Worker response timeout') from None
        if line is None:raise RuntimeError('Worker exited unexpectedly')
        return json.loads(line)
    def work(self):
        require(self.process.poll() is None,'Worker not alive')
        self.process.stdin.write('work\n');self.process.stdin.flush()
        result=self.receive();require(result.get('verified') is True,'Worker did not verify a job')
        return result
    def stop(self):
        if self.process.poll() is None:
            # Simulate a stopped node, terminate only this known worker process.
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        for stream in (self.process.stdin,self.process.stdout,self.process.stderr):
            if stream:stream.close()

class CommandRejected(RuntimeError):pass

class Client:
    def __init__(self,url):
        self.url=url;self.token='';self.player_id=None
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def command(self,action,args=None,request_id=None,expected=200):
        body={'action':action,'args':args or {},'request_id':request_id or secrets.token_hex(12)}
        req=urllib.request.Request(self.url+'/v1/command',json.dumps(body).encode(),{'Content-Type':'application/json','Authorization':'Bearer '+self.token})
        try:r=self.opener.open(req,timeout=5)
        except urllib.error.HTTPError as exc:r=exc
        with r:
            data=json.load(r)
            if r.status==400 and expected!=400:raise CommandRejected('Command rejected: '+action)
            require(r.status==expected,'Unexpected HTTP status for '+action)
        return data.get('result',data)
    def register(self,username):
        pending=self.command('key_register',{'name':username,'ack_key_loss':True})
        self.token=pending['token']
        result=self.command('key_confirm',{'key':pending['login_key'],'ack_saved':True})
        self.token=result['token'];self.player_id=result['player_id'];require(result['online'] is True,'Account not online')

def main():
    with tempfile.TemporaryDirectory(prefix='digit-multinode-gameplay-') as directory:
        root=Path(directory);world=ReadWorld(str(root/'world.sqlite3'),config=WorldConfig(energy_drain_per_second=0))
        store=GameplayCoordinator(root/'coordinator.sqlite3');provider=TestReadProvider(store)
        world.read_worker_enabled=True;world.read_service=provider
        servers=[];threads=[];workers=[];runtime=Runtime(world);started=False
        try:
            central=game_serve(world,port=0,runtime=runtime);coord=worker_serve(store)
            for server in (central,coord):
                servers.append(server);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();threads.append(thread)
            runtime.start();started=True
            game_url='http://127.0.0.1:'+str(central.server_port)
            coord_url='http://127.0.0.1:'+str(coord.server_port)
            credentials=[]
            for name in ('node-a','node-b'):
                credential=store.provision(name);credentials.append(credential)
                config=root/(name+'.json');fd=os.open(config,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump({'url':coord_url,'node_id':name,'credential':credential},f)
                workers.append(ProcessWorker(config))
            a,b=Client(game_url),Client(game_url);a.register('fixture_a');b.register('fixture_b')
            # Fixture funding is synthetic and explicit, not a reward or production edit.
            with world.lock:
                world.db.execute('UPDATE players SET x=0,y=0,bag=? WHERE id=?',(json.dumps({'brick':4}),a.player_id))
                world.db.execute('UPDATE players SET x=1,y=0 WHERE id=?',(b.player_id,));world.db.commit()
            # Both client reads enter the SAME Central World and enqueue real jobs.
            va=a.command('look');vb=b.command('look')
            require(b.player_id in [p['id'] for p in va['players']],'A cannot see B')
            require(a.player_id in [p['id'] for p in vb['players']],'B cannot see A')
            # Deliberate deterministic dispatch proves both workers actually participate.
            wa=workers[0].work();wb=workers[1].work()
            require(wa['job_id']!=wb['job_id'],'Workers received same job')
            require(a.command('look')==va and b.command('look')==vb,'Worker/Central view mismatch')
            require(provider.counts['worker_hits']>=2,'No actual read-path worker hits')
            key=secrets.token_hex(12);first=b.command('move',{'dx':1,'dy':0},key)
            require(b.command('move',{'dx':1,'dy':0},key)==first,'Move receipt mismatch')
            require(b.command('me')['x']==2,'Duplicate move executed twice')
            before=provider.counts['fallbacks'];moved=a.command('look')
            require(provider.counts['fallbacks']>before,'Stale geometry did not fallback')
            require(any(p['id']==b.player_id and p['x']==2 for p in moved['players']),'Movement not shared')
            a.command('say',{'text':'multi-node shared world'})
            require(any(e.get('data',{}).get('text')=='multi-node shared world' for e in b.command('events')['events']),'Chat not shared')
            wall=a.command('place',{'kind':'wall'})['object_id']
            require(wall in [o['id'] for o in b.command('look')['objects']],'Object not shared')
            a.command('build',{'object_id':wall})
            require(any(o['id']==wall and o['progress']==1 for o in b.command('look')['objects']),'Build progress not shared')
            drop=a.command('drop',{'item':'brick','qty':1})['drop_id']
            pickup_key=secrets.token_hex(12);got=b.command('pickup',{'drop_id':drop},pickup_key)
            require(b.command('pickup',{'drop_id':drop},pickup_key)==got,'Pickup receipt mismatch')
            require(b.command('me')['bag']['brick']==1,'Duplicate item credit')
            require(drop not in [d['id'] for d in a.command('look')['drops']],'Picked item still visible')
            # Competing pickup of one drop: exactly one success; no duplicate item.
            drop2=a.command('drop',{'item':'brick','qty':1})['drop_id']
            def attempt(client):
                try:return client.command('pickup',{'drop_id':drop2})
                except CommandRejected:return None
            with ThreadPoolExecutor(max_workers=2) as pool:race=list(pool.map(attempt,[a,b]))
            require(sum(r is not None for r in race)==1,'Pickup race did not have one winner')
            require(a.command('me')['bag'].get('brick',0)+b.command('me')['bag'].get('brick',0)==3,'Material conservation failed')
            # Drain only a bounded number of pending test jobs before node outage.
            for _ in range(32):
                if not store.db.execute("SELECT 1 FROM coord_jobs WHERE state='queued'").fetchone():break
                workers[1].work()
            workers[0].stop()
            wait_for_move_ready(world,b.player_id)
            b.command('move',{'dx':-1,'dy':0});a.command('look')
            survivor=workers[1].work()
            before=provider.counts['worker_hits'];a.command('look')
            require(provider.counts['worker_hits']>before,'Surviving worker not used')
            require(workers[0].process.poll() is not None and workers[1].process.poll() is None,'Worker outage state wrong')
            workers[1].stop()
            wait_for_move_ready(world,b.player_id)
            b.command('move',{'dx':0,'dy':1});before=provider.counts['fallbacks'];last=a.command('look')
            require(provider.counts['fallbacks']>before,'Both-down fallback not used')
            require(any(p['id']==b.player_id and p['x']==1 and p['y']==1 for p in last['players']),'Central unavailable after workers stopped')
            require(a.command('nearby')['players'],'Nearby failed with workers stopped')
            with store.lock:
                receipts={r['node_id']:r['n'] for r in store.db.execute('SELECT node_id,COUNT(*) n FROM coord_results GROUP BY node_id')}
            require(receipts.get('node-a',0)>=1 and receipts.get('node-b',0)>=2,'Missing per-node receipt evidence')
            output={'central_processes':1,'worker_processes_started':2,'client_sessions':2,'single_world':True,
                    'verified_by_node':receipts,'read_path_worker_hits':provider.counts['worker_hits'],
                    'movement_chat_objects_shared':True,'pickup_race_single_winner':True,'duplicate_commands_safe':True,
                    'node_a_stopped_node_b_served':True,'both_workers_stopped_central_fallback':True,
                    'automatic_worker_failover_tested':False,'network_scope':'single-machine-loopback',
                    'synthetic_fixtures':True,'production_node_changed':False,'soul_digit_awarded':0}
            serialized=json.dumps(output,indent=2)
            for secret in credentials+[a.token,b.token]:require(secret not in serialized,'Secret in report')
            print(serialized)
            print('PASS: one Central World, two persistent worker processes and two authenticated HTTP clients; both nodes verified gameplay read jobs; shared movement/chat/build/items; duplicate/racing pickup safe; survivor worker and both-down Central fallback. Test-orchestrated dispatch, not production failover.')
        finally:
            for worker in workers:worker.stop()
            for server in servers:server.shutdown();server.server_close()
            for thread in threads:thread.join(timeout=10)
            if started:runtime.close()
            world.close();store.close()
    return 0
if __name__=='__main__':raise SystemExit(main())
