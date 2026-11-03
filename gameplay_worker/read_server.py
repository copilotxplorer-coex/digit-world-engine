"""Isolated game HTTP server with an explicit, default-OFF read-worker flag."""
import argparse
import json
from pathlib import Path
import tempfile
from digit_world.server import Runtime,serve
from .read_world import ReadWorld

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--enable-read-worker',action='store_true',help='Opt in to bounded asynchronous local worker prefetch')
    ap.add_argument('--port',type=int,default=0)
    args=ap.parse_args()
    if args.port and not 1024<=args.port<=65535:ap.error('Port must be 0 or 1024..65535')
    with tempfile.TemporaryDirectory(prefix='digit-read-server-') as directory:
        world=ReadWorld(str(Path(directory)/'world.sqlite3'),enable_read_worker=args.enable_read_worker)
        runtime=Runtime(world);server=None;started=False
        try:
            server=serve(world,port=args.port,runtime=runtime);runtime.start();started=True
            print('Read worker enabled: '+str(args.enable_read_worker),flush=True)
            print('Temporary test world: http://127.0.0.1:'+str(server.server_port)+'/client2d',flush=True)
            print('Use test accounts only. Ctrl+C stops server; temporary data is deleted. No production Node paths loaded.',flush=True)
            server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:pass
        finally:
            if server:server.server_close()
            if started:runtime.close()
            if world.read_service:print('Read worker metrics: '+json.dumps(world.read_service.summary(),sort_keys=True),flush=True)
            world.close()
    return 0
if __name__=='__main__':raise SystemExit(main())
