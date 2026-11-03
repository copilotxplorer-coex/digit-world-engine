"""Explicit DB paths, cooperative process lock, read-only checks and startup backup."""
from contextlib import contextmanager
from pathlib import Path
from contextlib import closing
import os
import secrets
import sqlite3
import time
from .key_database import inspect_database,require_ready

@contextmanager
def database_lock(path):
    lock=Path(str(Path(path).resolve())+'.server.lock')
    f=lock.open('a+b')
    try:
        f.seek(0,2)
        if f.tell()==0:f.write(b'0');f.flush()
        f.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise ValueError('Database already in use by a server; stop it first') from None
        try:yield
        finally:
            f.seek(0)
            if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f.fileno(),fcntl.LOCK_UN)
    finally:f.close()

def resolve_database(value,create=False):
    if not value or value==':memory:':raise ValueError('Persistent server requires a real --db file')
    p=Path(value).expanduser().resolve()
    if not p.parent.is_dir():raise ValueError('Database parent directory must already exist')
    if create:
        if p.exists():raise ValueError('--create-db requires a new path; existing DB was not touched')
    elif not p.is_file():raise ValueError('Database not found; use --create-db only for a NEW world')
    return p

def backup_database(path):
    p=Path(path).resolve();require_ready(p)
    target=Path(str(p)+'.pre-key-only-'+time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(4)+'.bak')
    with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Database backup validation failed')
    return target
