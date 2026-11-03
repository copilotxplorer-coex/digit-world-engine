from digit_world.http_disconnect import disconnect_guard, is_client_disconnect, mark_disconnected
import argparse
from collections import defaultdict, deque
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from logging.handlers import RotatingFileHandler
import threading
import time
from .core import World, TICK
from .config import WorldConfig
from .federation import Federation

log=logging.getLogger('digit.server')

def configure_logging(path='server.log', level='INFO'):
    logger=logging.getLogger('digit'); logger.setLevel(getattr(logging,level))
    for h in list(logger.handlers): logger.removeHandler(h); h.close()
    formatter=logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s')
    console=logging.StreamHandler(); console.setFormatter(formatter); logger.addHandler(console)
    if path:
        file=RotatingFileHandler(path,maxBytes=2_000_000,backupCount=3,encoding='utf-8')
        file.setFormatter(formatter); logger.addHandler(file)
    logger.propagate=False

class Runtime:
    def __init__(self,world):
        self.world=world; self.stop=threading.Event(); self.healthy=True
        self.thread=threading.Thread(target=self.run,name='world-tick',daemon=True)
    def start(self): self.thread.start()
    def run(self):
        while not self.stop.wait(TICK):
            try: self.world.tick()
            except Exception:
                self.healthy=False
                log.exception('world_tick_failed; mutations disabled until restart')
                break
    def close(self): self.stop.set(); self.thread.join(timeout=10)

class Server(ThreadingHTTPServer):
    daemon_threads=False
    block_on_close=True

def serve(world,host='127.0.0.1',port=8765,runtime=None,federation=None):
    federation=federation or Federation()
    started_at=time.monotonic()
    def node_metrics():
        with world.lock:
            accounts=world.db.execute('SELECT COUNT(*) FROM login_keys').fetchone()[0]
            players=world.db.execute('SELECT COUNT(*) FROM players').fetchone()[0]
            online=world.db.execute('SELECT COUNT(*) FROM sessions WHERE online=1').fetchone()[0]
        tick_running=bool(runtime and runtime.thread.is_alive())
        return {'uptime':round(max(0.0,time.monotonic()-started_at),3),'online_players':online,
                'accounts':accounts,'players':players,'version':'0.3.5','schema':4,
                'tick_health':bool(runtime and runtime.healthy and tick_running),'tick_running':tick_running}

    claim_attempts=defaultdict(deque)
    claim_lock=threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup(); self.connection.settimeout(10)
        @disconnect_guard
        def handle_one_request(self):
            return super().handle_one_request()

        @disconnect_guard
        def reply(self,code,body):
            data=json.dumps(body,allow_nan=False).encode()
            self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(data))); self.end_headers()
            try: self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError): pass
        @disconnect_guard
        def do_GET(self):
            assets={'/client2d':('index.html','text/html; charset=utf-8'),'/client2d/':('index.html','text/html; charset=utf-8'),'/client2d/app.js':('app.js','text/javascript; charset=utf-8')}
            if self.path in assets:
                name,mime=assets[self.path];data=(Path(__file__).parent/'web'/name).read_bytes()
                self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data);return
            healthy=runtime is None or runtime.healthy
            if self.path=='/health': self.reply(200 if healthy else 503,dict({'ok':healthy,'protocol':2,'federation':federation.summary()},**node_metrics()))
            else: self.reply(404,{'ok':False,'error':'Not found'})
        @disconnect_guard
        def do_POST(self):
            if self.path in ('/federation/ping','/federation/event'):
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=8192:raise ValueError('Invalid peer body size')
                    raw=self.rfile.read(size)
                    sender,payload=federation.authenticate(self.path,raw,self.headers.get('X-Digit-Signature'))
                    if self.path=='/federation/ping':
                        return self.reply(200,{'ok':True,'node_id':federation.node_id,'version':'0.3.5','schema':4})
                    accepted=federation.accept(sender,payload)
                    return self.reply(200,{'ok':True,'node_id':federation.node_id,'accepted':accepted})
                except (ValueError,TypeError,KeyError,OverflowError):
                    return self.reply(403,{'ok':False,'error':'Peer request rejected'})
            if self.path!='/v1/command': return self.reply(404,{'ok':False,'error':'Not found'})
            action='invalid'; safe='unknown'; start=time.monotonic()
            try:
                if runtime and not runtime.healthy: return self.reply(503,{'ok':False,'error':'World tick unhealthy; restart required'})
                origin=self.headers.get('Origin')
                if origin and origin!='http://'+self.headers.get('Host',''):
                    return self.reply(403,{'ok':False,'error':'Cross-origin requests are not allowed'})
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=16384: raise ValueError('Invalid body length')
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict): raise ValueError('JSON object required')
                action=data.get('action')
                if action in ('worldnodes','worldevents','worldsay'):
                    token=self.headers.get('Authorization','').removeprefix('Bearer ')
                    me=world.execute(token,'me')
                    args=data.get('args',{})
                    if not isinstance(args,dict):raise ValueError('Arguments must be an object')
                    if action=='worldnodes':result=federation.status()
                    elif action=='worldevents':result=federation.after(args.get('cursor',0))
                    else:
                        if not me['online']:raise ValueError('Login required')
                        result=federation.broadcast(me,args.get('text'))
                    return self.reply(200,{'ok':True,'result':result})
                if action=='register':raise ValueError('Use key_register; legacy registration removed')
                if action in {'key_login','key_register','key_confirm'}:
                    with claim_lock:
                        now=time.monotonic();attempts=claim_attempts[self.client_address[0]]
                        while attempts and now-attempts[0]>=60:attempts.popleft()
                        if len(attempts)>=10:return self.reply(429,{'ok':False,'error':'Account rate limit; wait 60 seconds'})
                        attempts.append(now)
                # Only known safe command names enter logs; never log headers, body, token or results.
                safe=action if action in {'register','login','heartbeat','logout','resume','events','me','look','move','node_job','node_submit','craft','claim','cancel','jobs','place','build','dismantle','drop','pickup','lock','flee','surrender','equip','charge','aid','autocharge','say','nearby','inspect','key_login','key_register','key_confirm','key_status'} else 'unknown'
                token=self.headers.get('Authorization','').removeprefix('Bearer ')
                result=world.execute(token,action,data.get('args'),data.get('request_id'))
                level=logging.DEBUG if safe in ('heartbeat','events') else logging.INFO
                log.log(level,'command action=%s status=200 duration_ms=%.1f',safe,(time.monotonic()-start)*1000)
                self.reply(200,{'ok':True,'result':result})
            except (ValueError,KeyError,TypeError,OverflowError) as exc:
                # Generic error in logs; detailed validation returned to caller only.
                log.warning('command_rejected action=%s reason=validation',safe)
                self.reply(400,{'ok':False,'error':str(exc) if isinstance(exc,ValueError) else 'Invalid command arguments'})
            except Exception as _http_disconnect_exc:
                if is_client_disconnect(_http_disconnect_exc):
                    mark_disconnected(self)
                    return
                log.exception('command_failed reason=internal')
                self.reply(500,{'ok':False,'error':'Internal error; see server log'})
        def log_message(self,*args): pass
    return Server((host,port),Handler)

def main():
    from .persistent import resolve_database,database_lock,backup_database
    from .key_database import inspect_database,require_ready
    ap=argparse.ArgumentParser(description='Persistent key-only World (non-realtime)')
    ap.add_argument('--host',choices=['127.0.0.1','localhost','::1'],default='127.0.0.1')
    ap.add_argument('--db',required=True);ap.add_argument('--create-db',action='store_true');ap.add_argument('--check-db',action='store_true')
    ap.add_argument('--port',type=int,default=8765);ap.add_argument('--config');ap.add_argument('--federation-config')
    ap.add_argument('--log-file',default='server.log');ap.add_argument('--log-level',choices=['DEBUG','INFO','WARNING','ERROR'],default='INFO')
    a=ap.parse_args()
    if not 0<=a.port<=65535:ap.error('Port must be 0..65535')
    if a.check_db:
        if a.create_db:ap.error('--check-db cannot be combined with --create-db')
        print(json.dumps(inspect_database(a.db),indent=2));require_ready(a.db);return 0
    path=resolve_database(a.db,a.create_db)
    with database_lock(path):
        if a.create_db:
            with path.open('xb'):pass
        else:print('Verified startup DB backup: '+str(backup_database(path)),flush=True)
        configure_logging(a.log_file,a.log_level)
        world=None;runtime=None;server=None;started=False
        try:
            world=World(str(path),config=WorldConfig.load(a.config));runtime=Runtime(world)
            server=serve(world,port=a.port,runtime=runtime,federation=Federation(a.federation_config));runtime.start();started=True
            print('Persistent key-only World: http://127.0.0.1:'+str(server.server_port)+'/client2d',flush=True)
            server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:pass
        finally:
            if server:server.server_close()
            if started:runtime.close()
            if world:world.close()
    return 0
if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,OSError) as exc:raise SystemExit(str(exc))
