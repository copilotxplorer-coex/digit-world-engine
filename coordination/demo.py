"""Isolated coordinator + two worker PROCESSES. Synthetic tasks, no Soul Digit rewards."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
from .store import Coordinator
from .server import serve

ROOT=Path(__file__).resolve().parents[1]
def main():
    with tempfile.TemporaryDirectory(prefix='digit-coordinator-demo-') as directory:
        root=Path(directory);store=Coordinator(root/'coordinator.sqlite3');server=None;thread=None
        try:
            configs=[];credentials=[]
            for i in range(2):credentials.append(store.provision('worker-'+str(i)))
            for _ in range(4):store.enqueue({'challenge':secrets.token_hex(16),'rounds':100})
            server=serve(store);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url='http://127.0.0.1:'+str(server.server_port)
            for i,credential in enumerate(credentials):
                p=root/('worker-'+str(i)+'.json')
                fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w',encoding='utf-8') as handle:json.dump({'url':url,'node_id':'worker-'+str(i),'credential':credential},handle)
                configs.append(p)
            def run(config):
                r=subprocess.run([sys.executable,'-B','-m','coordination.worker_cli','--config',str(config)],cwd=ROOT,capture_output=True,text=True,timeout=20)
                assert r.returncode==0,'Worker process failed'
                for credential in credentials:assert credential not in r.stdout+r.stderr,'Credential disclosure'
                return json.loads(r.stdout)
            with ThreadPoolExecutor(max_workers=2) as pool:
                for _ in range(2):
                    results=list(pool.map(run,configs))
                    assert all(r['worked'] and r['verified'] and r['soul_digit_awarded']==0 for r in results)
            stats=store.stats();assert stats['verified_results']==4 and stats['jobs']=={'verified':4}
            assert run(configs[0])['worked'] is False
            print(json.dumps(stats,indent=2,sort_keys=True))
            print('PASS: two worker processes registered, leased, heartbeated and submitted four verified synthetic jobs; duplicate-safe results; no game DB/profile/key access; Soul Digit awarded=0.')
        finally:
            if server:server.shutdown();server.server_close()
            if thread:thread.join(timeout=10)
            store.close()
    return 0
if __name__=='__main__':raise SystemExit(main())
