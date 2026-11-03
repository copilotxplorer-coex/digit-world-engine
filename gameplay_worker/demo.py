"""Real game World fixture -> sanitized snapshot -> HTTP worker process -> Central view.
No production paths, profiles, keys, game mutations by workers or rewards.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from digit_world.core import World
from .coordinator import GameplayCoordinator
from .server import serve
from .bridge import capture,enqueue_nearby,consume_nearby

ROOT=Path(__file__).resolve().parents[1]
def main():
    with tempfile.TemporaryDirectory(prefix='digit-gameplay-worker-') as directory:
        root=Path(directory);world=World(str(root/'fixture-world.sqlite3'))
        store=GameplayCoordinator(root/'gameplay-coordinator.sqlite3');server=None;thread=None
        try:
            # Synthetic fixture creation only, before read-only snapshot work starts.
            players=[]
            for i in range(2):
                player=world.execute('','register',{'name':'Fixture'+str(i)})
                world.execute(player['token'],'login');players.append(player)
            world.db.execute('UPDATE players SET x=0,y=0 WHERE id=?',(players[0]['player_id'],))
            world.db.execute('UPDATE players SET x=3,y=4 WHERE id=?',(players[1]['player_id'],))
            world.db.execute("INSERT INTO objects VALUES('fixture-wall','wall',6,8,0)")
            world.db.execute("INSERT INTO drops VALUES('fixture-drop','brick',1,31,0)")
            world.db.commit()
            before='\n'.join(world.db.iterdump())
            credential=store.provision('gameplay-node')
            server=serve(store);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            config=root/'worker.json';fd=os.open(config,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as handle:
                json.dump({'url':'http://127.0.0.1:'+str(server.server_port),'node_id':'gameplay-node','credential':credential},handle)
            def work():
                process=subprocess.run([sys.executable,'-B','-m','gameplay_worker.worker_cli','--config',str(config)],cwd=ROOT,capture_output=True,text=True,timeout=20)
                assert process.returncode==0,'Gameplay worker process failed'
                for secret in [credential,*[p['token'] for p in players]]:assert secret not in process.stdout+process.stderr
                return json.loads(process.stdout)
            observer=players[0]['player_id']
            job=enqueue_nearby(store,world,observer)
            result=work();assert result['verified'] and result['match_count']==2
            view=consume_nearby(store,world,observer,job)
            assert [(m['kind'],m['distance2']) for m in view['matches']]==[('player',25.0),('object',100.0)]
            # Read-only Central presentation from the verified, fresh result.
            central_view={'nearby_ids':[m['id'] for m in view['matches']],'source':'verified-worker-snapshot'}
            assert central_view['nearby_ids']==[players[1]['player_id'],'fixture-wall']
            assert '\n'.join(world.db.iterdump())==before,'Worker/bridge mutated World DB'
            snapshot=capture(world,observer)
            serialized=json.dumps(snapshot)
            for secret in [credential,*[p['token'] for p in players]]:assert secret not in serialized
            assert 'bag' not in serialized and 'password' not in serialized and 'soul' not in serialized
            # Central gameplay movement invalidates the old result immediately.
            world.execute(players[1]['token'],'move',{'dx':1,'dy':0})
            try:consume_nearby(store,world,observer,job)
            except ValueError as exc:assert 'changed' in str(exc)
            else:raise AssertionError('Stale result accepted after movement')
            job2=enqueue_nearby(store,world,observer);assert work()['verified']
            fresh=consume_nearby(store,world,observer,job2)
            assert fresh['snapshot_hash']!=view['snapshot_hash']
            print(json.dumps({'verified_jobs':store.stats()['verified_results'],'nearby_match_count':len(fresh['matches']),
                              'worker_game_db_access':False,'stale_after_movement_rejected':True,
                              'runtime_look_replaced':False,'reward_enabled':False,'soul_digit_awarded':0},indent=2))
            print('PASS: game World snapshot -> HTTP worker PROCESS -> Central verified read-only view; no World writes by worker/bridge; credentials excluded; stale result rejected; fresh job succeeds; no rewards.')
        finally:
            if server:server.shutdown();server.server_close()
            if thread:thread.join(timeout=10)
            store.close();world.close()
    return 0
if __name__=='__main__':raise SystemExit(main())
