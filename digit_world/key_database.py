"""Read-only key readiness inspection; never convert or delete legacy accounts."""
from pathlib import Path
from contextlib import closing
import sqlite3

def inspect_database(path):
    p=Path(path).resolve()
    if not p.is_file():raise ValueError('Database not found: '+str(p))
    with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as db:
        names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'players' not in names:raise ValueError('Not a recognized World database')
        total=db.execute('SELECT COUNT(*) FROM players').fetchone()[0]
        active=db.execute('SELECT COUNT(*) FROM players p JOIN login_keys k ON k.owner=p.id').fetchone()[0] if 'login_keys' in names else 0
        pending=db.execute('SELECT COUNT(*) FROM players p JOIN pending_login_keys k ON k.owner=p.id WHERE NOT EXISTS(SELECT 1 FROM login_keys a WHERE a.owner=p.id)').fetchone()[0] if {'pending_login_keys','login_keys'}.issubset(names) else 0
        return {'players':total,'key_accounts':active,'pending_accounts':pending,'unconverted_accounts':total-active-pending}

def require_ready(path):
    if str(path)==':memory:':return
    p=Path(path)
    if p.exists() and p.stat().st_size>0:
        result=inspect_database(p)
        if result['unconverted_accounts']:
            raise ValueError('Key-only startup refused: '+str(result['unconverted_accounts'])+' account(s) have no Login Key. No accounts deleted. Finish migration with previous K1 source before installing K2.')
