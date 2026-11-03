"""Node-owned autonomous fixed-step lab loop with durable local logs.
Local SIMULATED receipts are NOT Central COMMITTED receipts or verified checkpoints.
Private test provisioning only. Shared HMAC keys do not protect against a malicious Node.
"""
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from region_runtime.contract import canonical, checked, fingerprint, fixture, identifier, integer, signed
from region_runtime.simulation import simulate
from region_runtime.verifier import reference

RULESET="r1a-fixture-v1"
ZERO="0"*64

class Runtime:
    def __init__(self,path,authority_key,**settings):
        path=Path(path)
        if path.name!="r1a-lab.sqlite3" or path.is_symlink():raise ValueError("Dedicated lab path required")
        if not isinstance(authority_key,bytes) or len(authority_key)!=32:raise ValueError("Invalid lab authority key")
        lockpath=path.with_suffix(".lock")
        if lockpath.is_symlink():raise ValueError("Linked lock refused")
        self.owner=lockpath.open("a+b")
        try:
            self.owner.seek(0,2)
            if self.owner.tell()==0:self.owner.write(b"0");self.owner.flush()
            self.owner.seek(0)
            if __import__("os").name=="nt":
                import msvcrt
                msvcrt.locking(self.owner.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.owner.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException:self.owner.close();raise ValueError("Lab runtime already owned or lock unavailable")
        try:self._open(path,authority_key,**settings)
        except BaseException:
            if hasattr(self,"db"):
                try:self.db.close()
                except Exception:pass
            self.owner.close();raise

    def _open(self,path,authority_key,*,hz=30,max_pending=64,per_tick=8,max_ticks=2000,max_commands=4096,clock=time.monotonic):
        if type(hz) not in (int,float) or not math.isfinite(hz) or not 1<=hz<=120:raise ValueError("Invalid lab Hz")
        integer(max_pending,1,4096);integer(per_tick,1,128);integer(max_ticks,1,10000);integer(max_commands,1,10000)
        path=Path(path)
        if path.name!="r1a-lab.sqlite3" or path.is_symlink():raise ValueError("Only dedicated r1a-lab.sqlite3 allowed")
        if path.exists():
            check=sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)
            try:
                tables={r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables!={"ra_meta","ra_commands","ra_ticks"}:raise ValueError("Not an R1-A lab database")
            finally:check.close()
        self.key=authority_key;self.clock=clock;self.hz=float(hz);self.interval=1/self.hz
        self.max_pending=max_pending;self.per_tick=per_tick;self.max_ticks=max_ticks;self.max_commands=max_commands
        self.lock=threading.RLock();self.stop_event=threading.Event();self.thread=None;self.sessions={}
        self.deadline=0;self.reason="NO_AUTHORITY";self.error=None;self.overruns=0;self.missed_deadlines=0
        self.db=sqlite3.connect(path,check_same_thread=False,isolation_level=None)
        try:
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS ra_meta(id INTEGER PRIMARY KEY CHECK(id=1),epoch INTEGER NOT NULL,tick INTEGER NOT NULL,state TEXT NOT NULL,head TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ra_commands(id TEXT PRIMARY KEY,hash TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL,assigned_tick INTEGER,event_sequence INTEGER,epoch INTEGER NOT NULL,session_id TEXT NOT NULL,client_seq INTEGER NOT NULL,proof TEXT NOT NULL, UNIQUE(epoch,session_id,client_seq));
            """)
        except sqlite3.OperationalError:
            self.db.close();raise
        # The complete schema is created in a single version; never a game DB migration.
        columns={r[1] for r in self.db.execute("PRAGMA table_info(ra_commands)")}
        if columns!={"id","hash","body","status","assigned_tick","event_sequence","epoch","session_id","client_seq","proof"}:
            self.db.close();raise ValueError("Unexpected lab schema")
        self.db.execute("CREATE TABLE IF NOT EXISTS ra_ticks(tick INTEGER PRIMARY KEY,body TEXT NOT NULL,hash TEXT NOT NULL)")
        self.db.execute("INSERT OR IGNORE INTO ra_meta VALUES(1,0,0,?,?)",(canonical(fixture()).decode(),ZERO))
        try:self._recover()
        except BaseException:self.db.close();raise

    def _meta(self):
        epoch,tick,raw,head=self.db.execute("SELECT epoch,tick,state,head FROM ra_meta WHERE id=1").fetchone()
        return epoch,tick,json.loads(raw),head

    def _recover(self):
        epoch,tick,state,head=self._meta();expected=fixture();previous=ZERO;last_epoch=0
        rows=self.db.execute("SELECT tick,body,hash FROM ra_ticks ORDER BY tick").fetchall()
        if len(rows)>self.max_ticks:raise ValueError("Existing log exceeds configured cap")
        seen=set()
        for index,(number,raw,h) in enumerate(rows,1):
            record=json.loads(raw)
            if set(record)!={"tick_id","region_id","region_epoch","ruleset","previous_hash","commands","state_hash"}:raise ValueError("Invalid tick schema")
            if number!=index or record["tick_id"]!=number or record["previous_hash"]!=previous or fingerprint(record)!=h:raise ValueError("Tick chain broken")
            if record["ruleset"]!=RULESET or record["region_id"]!="lab-region" or type(record["region_epoch"]) is not int or not last_epoch<=record["region_epoch"]<=epoch:raise ValueError("Invalid tick binding")
            last_epoch=record["region_epoch"]
            commands=[]
            if not isinstance(record["commands"],list) or len(record["commands"])>128:raise ValueError("Invalid logged batch")
            for entry in record["commands"]:
                if set(entry)!={"command_id","body","event_sequence","auth_proof"}:raise ValueError("Invalid log entry")
                body=entry["body"];self._validate_body(body)
                if entry["command_id"]!=body["command_id"] or body["epoch"]!=record["region_epoch"] or entry["command_id"] in seen:raise ValueError("Command replay conflict")
                seen.add(entry["command_id"])
                receipt=self.db.execute("SELECT hash,body,status,assigned_tick,event_sequence,proof FROM ra_commands WHERE id=?",(entry["command_id"],)).fetchone()
                if receipt!=(fingerprint(body),canonical(body).decode(),"SIMULATED",number,entry["event_sequence"],entry["auth_proof"]):raise ValueError("Receipt/log mismatch")
                commands.append({"seq":entry["event_sequence"],"actor":body["actor"],"dx":body["dx"],"dy":body["dy"]})
            if commands:expected=reference(expected,commands)
            if fingerprint(expected)!=record["state_hash"]:raise ValueError("Replay state mismatch")
            previous=h
        if tick!=len(rows) or head!=previous or state!=expected:raise ValueError("Local state/log mismatch")
        if self.db.execute("SELECT COUNT(*) FROM ra_commands WHERE status='SIMULATED'").fetchone()[0]!=len(seen):raise ValueError("Orphan simulated receipt")
        count=0
        for cid,raw,h,status,stored_epoch,sid,seq,assigned,event_seq,proof in self.db.execute("SELECT id,body,hash,status,epoch,session_id,client_seq,assigned_tick,event_sequence,proof FROM ra_commands"):
            count+=1;body=json.loads(raw);self._validate_body(body)
            if not isinstance(proof,str) or len(proof)!=64 or any(c not in "0123456789abcdef" for c in proof):raise ValueError("Invalid stored proof")
            if fingerprint(body)!=h or status not in ("RECEIVED","SIMULATED","REJECTED_STALE_EPOCH"):raise ValueError("Invalid stored receipt")
            if (cid,stored_epoch,sid,seq)!=(body["command_id"],body["epoch"],body["session_id"],body["client_seq"]):raise ValueError("Receipt index mismatch")
            if stored_epoch>epoch or (status!="SIMULATED" and (assigned is not None or event_seq is not None)):raise ValueError("Invalid pending receipt")
        if count>self.max_commands:raise ValueError("Existing command log exceeds configured cap")
        # Restart never resumes gameplay or queued input without a fresh grant.
        self.reason="RECOVERY_PAUSED" if tick or epoch else "NO_AUTHORITY"

    @staticmethod
    def _validate_body(body):
        fields={"command_id","session_id","actor","client_seq","epoch","authority_version","region_id","dx","dy"}
        if not isinstance(body,dict) or set(body)!=fields:raise ValueError("Invalid command schema")
        for k in ("command_id","session_id","actor"):identifier(body[k])
        integer(body["client_seq"],1,10**9);integer(body["epoch"],1,10**9);integer(body["authority_version"],1,10**9)
        if body["region_id"]!="lab-region":raise ValueError("Wrong region")
        integer(body["dx"],-1,1);integer(body["dy"],-1,1)
        if abs(body["dx"])+abs(body["dy"])>1:raise ValueError("Invalid fixture movement")

    def activate(self,envelope):
        with self.lock:
            body=checked(self.key,envelope)
            if set(body)!={"node_id","region_id","epoch","ttl"} or body["node_id"]!="A" or body["region_id"]!="lab-region":raise ValueError("Wrong authority grant")
            integer(body["epoch"],1,10**9)
            ttl=body["ttl"]
            if type(ttl) not in (int,float) or not math.isfinite(ttl) or not 0<ttl<=300:raise ValueError("Invalid lab lease")
            epoch,tick,state,head=self._meta()
            if body["epoch"]<=epoch:raise ValueError("Stale grant")
            if self.error:raise ValueError("Runtime fault requires inspection/restart")
            if tick>=self.max_ticks:raise ValueError("Log cap reached")
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute("UPDATE ra_commands SET status='REJECTED_STALE_EPOCH' WHERE status='RECEIVED'")
                self.db.execute("UPDATE ra_meta SET epoch=? WHERE id=1",(body["epoch"],));self.db.commit()
            except BaseException:self.db.rollback();raise
            self.sessions.clear();self.deadline=self.clock()+ttl;self.reason="RUNNING"
            return self.snapshot()

    def session(self,session_id,actor,key,authority_version=1):
        # Called only by trusted lab control pipe; never a public API.
        with self.lock:
            identifier(session_id);identifier(actor);integer(authority_version,1,10**9)
            if self._pause():raise ValueError("Region paused")
            if actor not in self._meta()[2]["positions"]:raise ValueError("Unknown actor")
            if not isinstance(key,bytes) or len(key)!=32:raise ValueError("Invalid lab session key")
            if session_id in self.sessions:raise ValueError("Session already provisioned")
            if len(self.sessions)>=32:raise ValueError("Session cap reached")
            self.sessions[session_id]=(actor,key,authority_version)
            return {"status":"READY"}

    def _pause(self):
        if self.reason=="RUNNING" and self.clock()>=self.deadline:self.reason="LEASE_EXPIRED"
        if self.reason=="RUNNING" and self._meta()[1]>=self.max_ticks:self.reason="LOG_LIMIT"
        return self.reason!="RUNNING"

    def submit(self,envelope):
        with self.lock:
            if not isinstance(envelope,dict) or not isinstance(envelope.get("body"),dict):raise ValueError("Invalid envelope")
            untrusted=envelope["body"];sid=untrusted.get("session_id")
            if not isinstance(sid,str) or sid not in self.sessions:raise ValueError("Unknown session")
            actor,key,version=self.sessions[sid];body=checked(key,envelope);self._validate_body(body)
            epoch,tick,state,head=self._meta()
            if body["actor"]!=actor or body["authority_version"]!=version or body["epoch"]!=epoch:raise ValueError("Command authority mismatch")
            h=fingerprint(body);old=self.db.execute("SELECT hash,status,assigned_tick,event_sequence FROM ra_commands WHERE id=?",(body["command_id"],)).fetchone()
            if old:
                if old[0]!=h:raise ValueError("Command ID payload conflict")
                return {"command_id":body["command_id"],"status":old[1],"tick_id":old[2],"event_sequence":old[3],"duplicate":True}
            if self._pause():raise ValueError("Region paused: "+self.reason)
            count=self.db.execute("SELECT COUNT(*) FROM ra_commands").fetchone()[0]
            if count>=self.max_commands:raise ValueError("Command log cap reached")
            pending=self.db.execute("SELECT COUNT(*) FROM ra_commands WHERE status='RECEIVED'").fetchone()[0]
            if pending>=self.max_pending:raise ValueError("Input queue full")
            last=self.db.execute("SELECT COALESCE(MAX(client_seq),0) FROM ra_commands WHERE epoch=? AND session_id=?",(epoch,sid)).fetchone()[0]
            if body["client_seq"]!=last+1:raise ValueError("Client sequence gap or duplicate")
            self.db.execute("INSERT INTO ra_commands VALUES(?,?,?,'RECEIVED',NULL,NULL,?,?,?,?)",(body["command_id"],h,canonical(body).decode(),epoch,sid,body["client_seq"],envelope["proof"]))
            return {"command_id":body["command_id"],"status":"RECEIVED","tick_id":None,"event_sequence":None,"duplicate":False}

    def _step(self):
        # Internal method used by the autonomous thread and deterministic unit tests only.
        with self.lock:
            if self._pause():return False
            epoch,tick,state,head=self._meta()
            if tick>=self.max_ticks:self.reason="LOG_LIMIT";return False
            rows=self.db.execute("SELECT id,body,proof FROM ra_commands WHERE status='RECEIVED' ORDER BY rowid LIMIT ?",(self.per_tick,)).fetchall()
            events=[];commands=[]
            for cid,raw,proof in rows:
                body=json.loads(raw);seq=state["seq"]+len(commands)+1
                commands.append({"seq":seq,"actor":body["actor"],"dx":body["dx"],"dy":body["dy"]})
                events.append({"command_id":cid,"body":body,"event_sequence":seq,"auth_proof":proof})
            next_state=simulate(state,commands) if commands else state
            record={"tick_id":tick+1,"region_id":"lab-region","region_epoch":epoch,"ruleset":RULESET,"previous_hash":head,"commands":events,"state_hash":fingerprint(next_state)}
            h=fingerprint(record)
            if self._pause():return False
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute("INSERT INTO ra_ticks VALUES(?,?,?)",(tick+1,canonical(record).decode(),h))
                for event in events:
                    self.db.execute("UPDATE ra_commands SET status='SIMULATED',assigned_tick=?,event_sequence=? WHERE id=? AND status='RECEIVED'",(tick+1,event["event_sequence"],event["command_id"]))
                self.db.execute("UPDATE ra_meta SET tick=?,state=?,head=? WHERE id=1",(tick+1,canonical(next_state).decode(),h))
                if self.clock()>=self.deadline:
                    self.db.rollback();self.reason="LEASE_EXPIRED";return False
                self.db.commit()
            except BaseException:self.db.rollback();raise
            return True

    def _loop(self):
        deadline=self.clock()+self.interval
        while not self.stop_event.is_set():
            if self.stop_event.wait(max(0,deadline-self.clock())):break
            started=self.clock()
            try:self._step()
            except Exception as exc:
                with self.lock:self.error=type(exc).__name__;self.reason="RUNTIME_ERROR"
            elapsed=self.clock()-started
            with self.lock:
                if elapsed>self.interval:self.overruns+=1
            deadline+=self.interval
            now=self.clock()
            if deadline<now:
                with self.lock:self.missed_deadlines+=int((now-deadline)/self.interval)+1
                # No unlimited catch-up burst, no skipped logical ticks.
                deadline=now+self.interval

    def start(self):
        with self.lock:
            if self.thread is not None:raise ValueError("Loop already started")
            self.thread=threading.Thread(target=self._loop,name="r1a-fixed-tick",daemon=False);self.thread.start()

    def snapshot(self):
        with self.lock:
            self._pause();epoch,tick,state,head=self._meta()
            pending=self.db.execute("SELECT COUNT(*) FROM ra_commands WHERE status='RECEIVED'").fetchone()[0]
            return {"epoch":epoch,"tick_id":tick,"state":state,"head_hash":head,"reason":self.reason,"pending":pending,"overruns":self.overruns,"missed_deadlines":self.missed_deadlines,"error":self.error,"verified_tick":0,"committed_tick":0,"local_simulated_only":True}

    def receipt(self,command_id):
        with self.lock:
            identifier(command_id)
            row=self.db.execute("SELECT status,assigned_tick,event_sequence FROM ra_commands WHERE id=?",(command_id,)).fetchone()
            return {"command_id":command_id,"status":row[0] if row else "UNKNOWN","tick_id":row[1] if row else None,"event_sequence":row[2] if row else None}

    def log(self,after=0,limit=64):
        with self.lock:
            integer(after,0,10**9);integer(limit,1,128)
            return [{"record":json.loads(raw),"hash":h} for raw,h in self.db.execute("SELECT body,hash FROM ra_ticks WHERE tick>? ORDER BY tick LIMIT ?",(after,limit))]

    def close(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(5)
            if self.thread.is_alive():raise RuntimeError("Tick loop did not stop; DB left open")
        with self.lock:
            self.db.close();self.owner.close()
