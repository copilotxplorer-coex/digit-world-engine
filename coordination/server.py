"""Loopback-only authenticated development API. No public enrollment/admin endpoints."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from .store import AuthError,LeaseError

class Server(ThreadingHTTPServer):
    daemon_threads=False
    block_on_close=True

def serve(store,port=0):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):super().setup();self.connection.settimeout(5)
        def log_message(self,*args):pass
        def reply(self,status,data):
            raw=json.dumps(data,allow_nan=False).encode()
            self.close_connection=True
            self.send_response(status);self.send_header('Connection','close');self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers()
            try:self.wfile.write(raw);self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError):pass
        def do_GET(self):
            if self.path=='/health':return self.reply(200,{'ok':True,'protocol':1,'scope':'development-coordinator','reward_enabled':False})
            self.reply(404,{'ok':False,'error':'Not found'})
        def do_POST(self):
            routes={'/v1/node/register':{'protocol'},'/v1/node/claim':set(),'/v1/node/heartbeat':{'job_id','lease_token'},'/v1/node/submit':{'job_id','lease_token','digest'}}
            try:
                # Read a bounded transport body BEFORE any early rejection. Leaving
                # unread request bytes can reset the connection before HTTP400 arrives.
                if self.headers.get('Transfer-Encoding'):raise ValueError('Transfer encoding not supported')
                lengths=self.headers.get_all('Content-Length',[])
                if len(lengths)!=1 or not lengths[0].isascii() or not lengths[0].isdigit():raise ValueError('Invalid content length')
                size=int(lengths[0])
                if not 0<size<=65536:raise ValueError('Transport body exceeds bounded drain limit')
                raw=self.rfile.read(size)
                if len(raw)!=size:raise ValueError('Incomplete request body')
                if self.path not in routes:return self.reply(404,{'ok':False,'error':'Not found'})
                if self.headers.get('Origin'):return self.reply(403,{'ok':False,'error':'Browser origins not supported'})
                if size>8192:raise ValueError('Invalid body size')
                data=json.loads(raw)
                if not isinstance(data,dict) or set(data)!=routes[self.path]:raise ValueError('Invalid request fields')
                auth=self.headers.get('Authorization','')
                if not auth.startswith('Bearer '):raise AuthError('Node authentication failed')
                token=auth[7:];node=self.headers.get('X-Digit-Node','')
                if self.path.endswith('/register'):result=store.register(node,token,data['protocol'])
                elif self.path.endswith('/claim'):result=store.claim(node,token)
                elif self.path.endswith('/heartbeat'):result=store.heartbeat(node,token,data['job_id'],data['lease_token'])
                else:result=store.submit(node,token,data['job_id'],data['lease_token'],data['digest'])
                self.reply(200,{'ok':True,'result':result})
            except AuthError:self.reply(403,{'ok':False,'error':'Node authentication failed or not registered'})
            except LeaseError:self.reply(409,{'ok':False,'error':'Lease conflict; inspect coordinator state'})
            except (ValueError,KeyError,TypeError,OverflowError):self.reply(400,{'ok':False,'error':'Invalid request or result verification failed'})
            except Exception:self.reply(500,{'ok':False,'error':'Coordinator internal error'})
    return Server(('127.0.0.1',port),Handler)
