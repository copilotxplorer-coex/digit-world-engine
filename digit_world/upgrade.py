"""Copy an existing stopped-server database without overwriting destination."""
import argparse
from pathlib import Path
import sqlite3

def copy_database(source, destination):
    source=Path(source).resolve(); destination=Path(destination).resolve()
    if not source.is_file(): raise ValueError('Source database does not exist')
    if destination.exists(): raise ValueError('Destination already exists; refusing overwrite')
    if source==destination: raise ValueError('Source and destination must differ')
    destination.parent.mkdir(parents=True,exist_ok=True)
    src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
    dst=None
    try:
        if src.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise ValueError('Source integrity check failed')
        version=src.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0,1,2,3): raise ValueError('Unsupported source schema')
        # Exclusive file creation prevents accidental replacement of an existing file.
        with destination.open('xb'): pass
        dst=sqlite3.connect(destination)
        src.backup(dst)
        if dst.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise ValueError('Copied database integrity check failed')
    finally:
        src.close()
        if dst: dst.close()
    return destination

def main():
    ap=argparse.ArgumentParser(description='Stop old server first. Copy database into new project without changing source.')
    ap.add_argument('--source',required=True); ap.add_argument('--dest',default='world.sqlite3'); a=ap.parse_args()
    try: result=copy_database(a.source,a.dest)
    except (ValueError,OSError,sqlite3.Error) as exc: ap.exit(1,'Copy failed: '+str(exc)+'\n')
    print('Copied database to',result)
    print('Start v0.3.3 server. Schema 3 needs no migration; keep the source database as backup.')
if __name__=='__main__': main()
