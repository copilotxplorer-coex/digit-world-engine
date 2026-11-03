"""Run one bounded validation task with an explicitly provisioned local config."""
import argparse
import json
from pathlib import Path
import sys
from .worker import GameplayWorker

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--config',required=True)
    args=ap.parse_args();p=Path(args.config)
    if p.stat().st_size>8192:raise ValueError('Worker config too large')
    data=json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(data,dict) or set(data)!={'url','node_id','credential'}:raise ValueError('Invalid worker config')
    result=GameplayWorker(data['url'],data['node_id'],data['credential']).run_once()
    print(json.dumps(result,sort_keys=True));return 0
if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:print('Worker failed; no automatic retry or reward credit. Inspect coordinator state.',file=sys.stderr);raise SystemExit(1)
