"""Test-only persistent worker process; commands via stdin, no game DB access."""
import json
import sys
from pathlib import Path
from gameplay_worker.worker import GameplayWorker

def main():
    cfg=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    worker=GameplayWorker(cfg['url'],cfg['node_id'],cfg['credential'])
    print(json.dumps({'ready':True,'node_id':cfg['node_id']}),flush=True)
    for line in sys.stdin:
        if line.strip()=='stop':return 0
        if line.strip()!='work':raise ValueError('Invalid test command')
        result=worker.run_once()
        print(json.dumps(result),flush=True)
    return 0
if __name__=='__main__':raise SystemExit(main())
