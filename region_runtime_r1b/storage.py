"""Dedicated lab paths and crash-released cooperative single-owner lock."""
import os
from pathlib import Path
import sqlite3

class Owner:
    def __init__(self,path,filename):
        self.path=Path(path)
        if self.path.name!=filename:raise ValueError("Dedicated lab filename required")
        for p in (self.path,*self.path.parents):
            if p.is_symlink() or bool(getattr(p,"is_junction",lambda:False)()):raise ValueError("Linked lab path refused")
        lock=self.path.with_suffix(".lock")
        if lock.is_symlink():raise ValueError("Linked lock refused")
        self.file=lock.open("a+b")
        try:
            self.file.seek(0,2)
            if self.file.tell()==0:self.file.write(b"0");self.file.flush()
            self.file.seek(0)
            if os.name=="nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException:self.file.close();raise ValueError("Lab DB already owned")
    def close(self):self.file.close()

def check_tables(path,expected):
    if not Path(path).exists():return
    conn=sqlite3.connect(path,timeout=2)
    try:
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables!=set(expected):raise ValueError("Not an R1-B replica DB")
    finally:conn.close()
