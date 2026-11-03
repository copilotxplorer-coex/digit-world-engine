"""Separate coordinator DB. No imports or access to game DB, player profiles or rewards."""
from contextlib import contextmanager
import hashlib
import hmac
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from .contract import identifier,validate_payload,solve,KIND,PROTOCOL

class AuthError(ValueError):pass
class LeaseError(ValueError):pass

class Coordinator:
    def __init__(self,path=':memory:',clock=time.time,lease_seconds=30,max_attempts=3):
        if type(lease_seconds) is not int or not 5<=lease_seconds<=300:raise ValueError('Lease seconds must be 5..300')
        if type(max_attempts) is not int or not 1<=max_attempts<=10:raise ValueError('Invalid max attempts')
        self.clock=clock;self.lease_seconds=lease_seconds;self.max_attempts=max_attempts;self.lock=threading.RLock()
        # Refuse to initialize on an existing unrelated database.
        self.db=sqlite3.connect(path,check_same_thread=False,timeout=5)
        self.db.row_factory=sqlite3.Row
        try:
            tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables and tables!={'coord_meta','coord_nodes','coord_jobs','coord_results'}:raise ValueError('Not a dedicated coordinator DB; refusing access')
            if tables:
                rows=self.db.execute('SELECT version FROM coord_meta').fetchall()
                if len(rows)!=1 or rows[0][0]!=1:raise ValueError('Unsupported coordinator DB version')
            else:
                self.db.executescript('''
                CREATE TABLE coord_meta(version INTEGER NOT NULL);
                INSERT INTO coord_meta VALUES(1);
                CREATE TABLE coord_nodes(id TEXT PRIMARY KEY,credential_hash TEXT NOT NULL UNIQUE,registered INTEGER NOT NULL DEFAULT 0,enabled INTEGER NOT NULL DEFAULT 1,last_seen REAL);
                CREATE TABLE coord_jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,owner TEXT,lease_hash TEXT,expires REAL,attempt INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE coord_results(job_id TEXT PRIMARY KEY,node_id TEXT NOT NULL,lease_hash TEXT NOT NULL,digest TEXT NOT NULL,verified_at REAL NOT NULL);
                ''');self.db.commit()
        except Exception:self.db.close();raise

    def close(self):
        with self.lock:self.db.close()

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:yield;self.db.commit()
            except Exception:self.db.rollback();raise

    def now(self):
        now=self.clock()
        if isinstance(now,bool) or not isinstance(now,(int,float)) or not math.isfinite(now):raise ValueError('Invalid clock')
        return now

    @staticmethod
    def hashed(value):return hashlib.sha256(value.encode()).hexdigest()

    def provision(self,node_id):
        identifier(node_id);credential=secrets.token_urlsafe(32)
        with self.transaction():
            self.db.execute('INSERT INTO coord_nodes(id,credential_hash) VALUES(?,?)',(node_id,self.hashed(credential)))
        return credential

    def revoke(self,node_id):
        with self.transaction():
            if self.db.execute('UPDATE coord_nodes SET enabled=0 WHERE id=?',(node_id,)).rowcount!=1:raise ValueError('Unknown node')

    def auth(self,node_id,credential,registered=True):
        identifier(node_id)
        if not isinstance(credential,str) or not 32<=len(credential)<=128:raise AuthError('Node authentication failed')
        row=self.db.execute('SELECT * FROM coord_nodes WHERE id=?',(node_id,)).fetchone()
        if row is None or not row['enabled'] or not hmac.compare_digest(row['credential_hash'],self.hashed(credential)):
            raise AuthError('Node authentication failed')
        if registered and not row['registered']:raise AuthError('Register node first')
        return row

    def register(self,node_id,credential,protocol):
        if type(protocol) is not int or protocol!=PROTOCOL:raise ValueError('Unsupported coordinator protocol')
        with self.transaction():
            self.auth(node_id,credential,False)
            self.db.execute('UPDATE coord_nodes SET registered=1,last_seen=? WHERE id=?',(self.now(),node_id))
        return {'node_id':node_id,'registered':True,'protocol':PROTOCOL,'reward_enabled':False}

    def enqueue(self,payload):
        validate_payload(payload);job_id=secrets.token_hex(16)
        with self.transaction():
            self.db.execute('INSERT INTO coord_jobs(id,kind,payload,state) VALUES(?,?,?,?)',(job_id,KIND,json.dumps(payload,sort_keys=True),'queued'))
        return job_id

    def expire(self,now):
        self.db.execute("UPDATE coord_jobs SET state=CASE WHEN attempt>=? THEN 'failed' ELSE 'queued' END,owner=NULL,lease_hash=NULL,expires=NULL WHERE state='leased' AND expires<=?",(self.max_attempts,now))

    def claim(self,node_id,credential):
        with self.transaction():
            self.auth(node_id,credential);now=self.now();self.expire(now)
            if self.db.execute("SELECT 1 FROM coord_jobs WHERE owner=? AND state='leased'",(node_id,)).fetchone():
                raise LeaseError('Node already has an active lease')
            row=self.db.execute("SELECT * FROM coord_jobs WHERE state='queued' ORDER BY rowid LIMIT 1").fetchone()
            self.db.execute('UPDATE coord_nodes SET last_seen=? WHERE id=?',(now,node_id))
            if row is None:return {'job':None,'reward_enabled':False}
            token=secrets.token_urlsafe(32);expires=now+self.lease_seconds
            self.db.execute("UPDATE coord_jobs SET state='leased',owner=?,lease_hash=?,expires=?,attempt=attempt+1 WHERE id=?",(node_id,self.hashed(token),expires,row['id']))
            return {'job':{'id':row['id'],'kind':row['kind'],'payload':json.loads(row['payload']),'lease_token':token,'expires':expires,'attempt':row['attempt']+1},'reward_enabled':False}

    def check_lease(self,row,node_id,token,now):
        if not isinstance(token,str) or not 32<=len(token)<=128:raise LeaseError('Invalid lease')
        if row is None or row['state']!='leased' or row['owner']!=node_id or not hmac.compare_digest(row['lease_hash'],self.hashed(token)) or row['expires']<=now:
            raise LeaseError('Lease expired, replaced or not owned by node')

    def heartbeat(self,node_id,credential,job_id,lease_token):
        identifier(job_id)
        with self.transaction():
            self.auth(node_id,credential);now=self.now()
            row=self.db.execute('SELECT * FROM coord_jobs WHERE id=?',(job_id,)).fetchone()
            self.check_lease(row,node_id,lease_token,now)
            expires=now+self.lease_seconds
            self.db.execute('UPDATE coord_jobs SET expires=? WHERE id=?',(expires,job_id))
            self.db.execute('UPDATE coord_nodes SET last_seen=? WHERE id=?',(now,node_id))
        return {'expires':expires,'reward_enabled':False}

    def submit(self,node_id,credential,job_id,lease_token,digest):
        identifier(job_id)
        if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Invalid result digest')
        if not isinstance(lease_token,str) or not 32<=len(lease_token)<=128:raise LeaseError('Invalid lease')
        with self.transaction():
            self.auth(node_id,credential);now=self.now()
            old=self.db.execute('SELECT * FROM coord_results WHERE job_id=?',(job_id,)).fetchone()
            if old:
                if old['node_id']==node_id and hmac.compare_digest(old['lease_hash'],self.hashed(lease_token)) and hmac.compare_digest(old['digest'],digest):
                    return {'accepted':True,'duplicate':True,'reward_enabled':False,'soul_digit_awarded':0}
                raise LeaseError('Result already committed with different owner/lease/digest')
            row=self.db.execute('SELECT * FROM coord_jobs WHERE id=?',(job_id,)).fetchone()
            self.check_lease(row,node_id,lease_token,now)
            if not hmac.compare_digest(solve(json.loads(row['payload'])),digest):raise ValueError('Result verification failed')
            self.db.execute('INSERT INTO coord_results VALUES(?,?,?,?,?)',(job_id,node_id,self.hashed(lease_token),digest,now))
            self.db.execute("UPDATE coord_jobs SET state='verified' WHERE id=?",(job_id,))
            self.db.execute('UPDATE coord_nodes SET last_seen=? WHERE id=?',(now,node_id))
        return {'accepted':True,'duplicate':False,'reward_enabled':False,'soul_digit_awarded':0}

    def stats(self):
        with self.lock:
            counts={r['state']:r['n'] for r in self.db.execute('SELECT state,COUNT(*) AS n FROM coord_jobs GROUP BY state')}
            return {'protocol':PROTOCOL,'nodes':self.db.execute('SELECT COUNT(*) FROM coord_nodes').fetchone()[0],
                    'jobs':counts,'verified_results':self.db.execute('SELECT COUNT(*) FROM coord_results').fetchone()[0],
                    'reward_enabled':False,'soul_digit_awarded':0,'task_scope':'synthetic-validation-only'}
