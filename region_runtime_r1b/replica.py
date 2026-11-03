"""Durable independent replica; receiving is separate from reference replay."""
import json,secrets,sqlite3,threading,time
from pathlib import Path
from region_runtime.contract import canonical,fingerprint,fixture,integer,identifier
from region_runtime.verifier import reference
from region_runtime_r1a.runtime import Runtime,RULESET,ZERO
from .storage import Owner,check_tables
from .frontiers import replica_frontiers
from .wire import exact,digest,number,MAX_BATCH_BYTES,MAX_BATCH_TICKS
class Conflict(ValueError):pass
class Gap(ValueError):pass
class Backpressure(ValueError):pass

def validate_entry(e):
    exact(e,"record hash");r=e["record"];exact(r,"tick_id region_id region_epoch ruleset previous_hash commands state_hash")
    integer(r["tick_id"],1,10000);integer(r["region_epoch"],1,10**9)
    if r["region_id"]!="lab-region" or r["ruleset"]!=RULESET:raise ValueError("INCOMPATIBLE")
    for h in (e["hash"],r["previous_hash"],r["state_hash"]):digest(h)
    if fingerprint(r)!=e["hash"]:raise ValueError("Invalid log hash")
    if not isinstance(r["commands"],list) or len(r["commands"])>128:raise ValueError("Command cap")
    for c in r["commands"]:
        exact(c,"command_id body event_sequence auth_proof");Runtime._validate_body(c["body"]);digest(c["auth_proof"]);integer(c["event_sequence"],1,10**12)
        if c["command_id"]!=c["body"]["command_id"] or c["body"]["epoch"]!=r["region_epoch"]:raise ValueError("Command binding")
    return r

def calculate(state,r):
    commands=[{"seq":c["event_sequence"],"actor":c["body"]["actor"],"dx":c["body"]["dx"],"dy":c["body"]["dy"]} for c in r["commands"]]
    return reference(state,commands) if commands else state

class Replica:
    def __init__(self,path,node_id,*,max_pending=64,max_ticks=2000,max_bytes=8388608,max_commands=4096,replay_delay=0):
        if node_id not in ("B","C"):raise ValueError("Replica must be B or C")
        for v,lo,hi in ((max_pending,1,4096),(max_ticks,1,10000),(max_bytes,1024,67108864),(max_commands,1,10000)):integer(v,lo,hi)
        self.delay=number(replay_delay,0,2);self.node_id=node_id;self.max_pending=max_pending;self.max_ticks=max_ticks;self.max_bytes=max_bytes;self.max_commands=max_commands
        self.owner=Owner(path,"r1b-"+node_id+"-lab.sqlite3");self.lock=threading.RLock();self.stop=threading.Event();self.thread=None
        self.boot=secrets.token_hex(16);self.session=None;self.target=0;self.connected=False;self.last_contact=0;self.error=None;self.reason="DISCONNECTED";self._hold=False;self._replay_generation=0;self._connection_generation=0;self._authorized_epoch=None
        try:
            existing=Path(path).exists()
            check_tables(path,{"rb_meta","rb_ticks","rb_commands"})
            self.db=sqlite3.connect(path,check_same_thread=False,isolation_level=None,timeout=.2);self.db.execute("PRAGMA synchronous=FULL")
            if not existing:self.db.executescript("CREATE TABLE IF NOT EXISTS rb_meta(id INTEGER PRIMARY KEY CHECK(id=1),epoch INTEGER,stored INTEGER,applied INTEGER,state TEXT,head TEXT,evidence TEXT);CREATE TABLE IF NOT EXISTS rb_ticks(tick INTEGER PRIMARY KEY,body TEXT,hash TEXT);CREATE TABLE IF NOT EXISTS rb_commands(id TEXT PRIMARY KEY,tick INTEGER,epoch INTEGER,session TEXT,seq INTEGER,UNIQUE(epoch,session,seq));")
            if not existing:self.db.execute("INSERT INTO rb_meta VALUES(1,0,0,0,?,?,NULL)",(canonical(fixture()).decode(),ZERO))
            self._recover()
        except BaseException:
            if hasattr(self,"db"):self.db.close()
            self.owner.close();raise
    def _meta(self):
        ep,s,a,raw,h,ev=self.db.execute("SELECT epoch,stored,applied,state,head,evidence FROM rb_meta WHERE id=1").fetchone()
        return ep,s,a,json.loads(raw),h,json.loads(ev) if ev else None
    def _recover(self):
        ep,s,a,state,head,ev=self._meta();integer(ep,0,10**9);integer(s,0,self.max_ticks);integer(a,0,s)
        expected=fixture();previous=ZERO;last_ep=0;seen={};total=0
        rows=self.db.execute("SELECT tick,body,hash FROM rb_ticks ORDER BY tick").fetchall()
        if len(rows)!=s:raise ValueError("Stored frontier mismatch")
        for i,(tick,raw,h) in enumerate(rows,1):
            r=validate_entry({"record":json.loads(raw),"hash":h});total+=len(raw.encode())
            if tick!=i or r["tick_id"]!=i or r["previous_hash"]!=previous or not last_ep<=r["region_epoch"]<=ep:raise ValueError("Broken chain")
            last_ep=r["region_epoch"];previous=h
            for c in r["commands"]:
                b=c["body"]
                if c["command_id"] in seen:raise ValueError("Duplicate command")
                seen[c["command_id"]]=(tick,b["epoch"],b["session_id"],b["client_seq"])
            if i<=a:
                expected=calculate(expected,r)
                if fingerprint(expected)!=r["state_hash"]:raise ValueError("Applied replay mismatch")
        actual={cid:(tick,ep,sid,seq) for cid,tick,ep,sid,seq in self.db.execute("SELECT id,tick,epoch,session,seq FROM rb_commands")}
        if previous!=head or expected!=state or actual!=seen or total>self.max_bytes or len(seen)>self.max_commands or s-a>self.max_pending:raise ValueError("Local state mismatch or cap")
        self.reason="DIVERGED" if ev else "DISCONNECTED"
    def conflict(self,tick,kind,expected,observed):
        with self.lock:
            self._replay_generation+=1
            ev={"tick_id":tick,"kind":kind,"expected":expected,"observed":observed}
            self.db.execute("UPDATE rb_meta SET evidence=COALESCE(evidence,?) WHERE id=1",(canonical(ev).decode(),))
            self.reason="DIVERGED"
    def negotiate(self,session,epoch,target):
        identifier(session);integer(epoch,1,10**9);integer(target,0,10000)
        with self.lock:
            ep,s,a,state,h,ev=self._meta()
            if self.stop.is_set() or self.error:raise ValueError("Replica unavailable")
            if epoch<ep or (self._authorized_epoch is not None and epoch!=self._authorized_epoch):raise ValueError("Stale or unauthorized epoch")
            if s>target:raise Conflict("Replica ahead of source")
            self._replay_generation+=1;self._connection_generation+=1
            self.db.execute("UPDATE rb_meta SET epoch=? WHERE id=1",(epoch,))
            self.session=session;self.target=target;self.connected=True;self.last_contact=time.monotonic()
            if not ev:self.reason="CATCHING_UP"
            return self.status()
    def disconnect(self,token=None):
        with self.lock:
            if token is not None and token!=self._connection_token_locked():return False
            self._replay_generation+=1;self._connection_generation+=1
            self.connected=False;self.session=None
            if self.reason not in ("DIVERGED","NEEDS_BASELINE"):self.reason="DISCONNECTED"
            return True
    def ingest(self,entries,epoch):
        if not isinstance(entries,list) or not 1<=len(entries)<=MAX_BATCH_TICKS or len(canonical(entries))>MAX_BATCH_BYTES:raise ValueError("Batch cap")
        records=[validate_entry(e) for e in entries]
        if any(b["tick_id"]!=a["tick_id"]+1 for a,b in zip(records,records[1:])):raise Gap("Noncontiguous batch")
        with self.lock:
            ep,s,a,state,head,ev=self._meta()
            if epoch!=ep or not self.connected:raise ValueError("No current session")
            if ev:raise Conflict("Already diverged")
            if records[0]["tick_id"]>s+1:raise Gap("Missing prefix")
            new=[];previous=head;last_ep=0
            if s:last_ep=json.loads(self.db.execute("SELECT body FROM rb_ticks WHERE tick=?",(s,)).fetchone()[0])["region_epoch"]
            for e,r in zip(entries,records):
                tick=r["tick_id"]
                if tick<=s:
                    row=self.db.execute("SELECT body,hash FROM rb_ticks WHERE tick=?",(tick,)).fetchone()
                    if row!=(canonical(r).decode(),e["hash"]):
                        self.conflict(tick,"LOG_CONFLICT",row[1] if row else ZERO,e["hash"]);raise Conflict("Conflicting tick")
                else:
                    if r["previous_hash"]!=previous or not last_ep<=r["region_epoch"]<=ep:raise ValueError("Chain or epoch mismatch")
                    previous=e["hash"];last_ep=r["region_epoch"];new.append(e)
            if s+len(new)-a>self.max_pending or s+len(new)>self.max_ticks:raise Backpressure("Tick cap")
            size=self.db.execute("SELECT COALESCE(SUM(LENGTH(CAST(body AS BLOB))),0) FROM rb_ticks").fetchone()[0]
            count=self.db.execute("SELECT COUNT(*) FROM rb_commands").fetchone()[0]
            if size+sum(len(canonical(e["record"])) for e in new)>self.max_bytes or count+sum(len(e["record"]["commands"]) for e in new)>self.max_commands:raise Backpressure("Byte or command cap")
            self.db.execute("BEGIN IMMEDIATE")
            try:
                for e in new:
                    r=e["record"];self.db.execute("INSERT INTO rb_ticks VALUES(?,?,?)",(r["tick_id"],canonical(r).decode(),e["hash"]))
                    for c in r["commands"]:
                        b=c["body"];self.db.execute("INSERT INTO rb_commands VALUES(?,?,?,?,?)",(c["command_id"],r["tick_id"],b["epoch"],b["session_id"],b["client_seq"]))
                self.db.execute("UPDATE rb_meta SET stored=?,head=? WHERE id=1",(s+len(new),previous));self.db.commit()
            except sqlite3.IntegrityError:
                self.db.rollback();self.conflict(records[0]["tick_id"],"COMMAND_CONFLICT",ZERO,fingerprint(entries));raise Conflict("Command identity conflict")
            except BaseException:self.db.rollback();raise
            self.last_contact=time.monotonic();return self.status()
    def _apply_one(self):
        with self.lock:
            self._expire_locked()
            ep,s,a,state,h,ev=self._meta()
            if ev or self.error or self.reason=="NEEDS_BASELINE" or self.stop.is_set() or self.hold or not self.connected or a>=s:return False
            row=self.db.execute("SELECT body,hash FROM rb_ticks WHERE tick=?",(a+1,)).fetchone()
            if row is None:self.fail("MissingReplayRecord");return False
            r=json.loads(row[0]);state_raw=self.db.execute("SELECT state FROM rb_meta WHERE id=1").fetchone()[0];state_hash=fingerprint(state)
            token=(self._connection_token_locked(),self._replay_generation,ep,a,state_hash,row)
        failure=None;actual=None;result_raw=None
        try:
            result=calculate(state,r);actual=fingerprint(result);result_raw=canonical(result).decode()
        except Exception as exc:failure=exc
        with self.lock:
            self._expire_locked()
            now_ep,stored,applied,current_state,head,evidence=self._meta()
            current_row=self.db.execute("SELECT body,hash FROM rb_ticks WHERE tick=?",(a+1,)).fetchone()
            current=(self._connection_token_locked(),self._replay_generation,now_ep,applied,fingerprint(current_state),current_row)
            if current!=token or evidence or self.error or self.reason=="NEEDS_BASELINE" or self.stop.is_set() or self.hold or not self.connected or stored<a+1:return False
            if failure is not None:
                if isinstance(failure,ValueError):self.conflict(a+1,"REPLAY_INVALID",r["state_hash"],state_hash)
                else:self.fail(type(failure).__name__)
                return False
            if actual!=r["state_hash"]:self.conflict(a+1,"STATE_MISMATCH",r["state_hash"],actual);return False
            cursor=self.db.execute("UPDATE rb_meta SET applied=?,state=? WHERE id=1 AND epoch=? AND applied=? AND state=? AND evidence IS NULL",(a+1,result_raw,ep,a,state_raw))
            return cursor.rowcount==1
    def _loop(self):
        while not self.stop.wait(.005):
            if self.stop.wait(self.delay):break
            try:self._apply_one()
            except Exception as exc:self.fail(type(exc).__name__);break
            with self.lock:
                if self.error:break
    def start(self):
        if self.thread:raise ValueError("Already started")
        self.thread=threading.Thread(target=self._loop,name="r1b-replay-"+self.node_id);self.thread.start()
    def status(self):
        with self.lock:
            self._expire_locked()
            ep,s,a,state,h,ev=self._meta()
            if self.connected and not ev and not self.error:self.reason="FOLLOWING" if a>=self.target else "CATCHING_UP"
            return {"node_id":self.node_id,"stored_tick":s,"stored_head_hash":h,"applied_tick":a,"applied_state_hash":fingerprint(state),"matched_tick":a,"target_tick":self.target,"reason":self.reason,"pending":s-a,"evidence":ev,"error":self.error,"verified_tick":0,"committed_tick":0,"boot":self.boot}
    def state(self):
        with self.lock:return self._meta()[3]
    def close(self):
        with self.lock:self.stop.set();self.disconnect()
        if self.thread:
            self.thread.join(5)
            if self.thread.is_alive():raise RuntimeError("Replay did not stop")
        with self.lock:self.db.close();self.owner.close()

    def _connection_token_locked(self):
        return (self.boot,self.session,self._meta()[0],self._connection_generation)

    def connection_token(self):
        with self.lock:return self._connection_token_locked()

    def _expire_locked(self):
        if self.connected and time.monotonic()-self.last_contact>2:self.disconnect()

    def require_connection(self,token):
        with self.lock:
            self._expire_locked()
            if token!=self._connection_token_locked() or not self.connected or self.stop.is_set() or self.error:raise ValueError("Fenced replication connection")

    def authorize_epoch(self,epoch):
        integer(epoch,1,10**9)
        with self.lock:
            if epoch<self._meta()[0] or (self._authorized_epoch is not None and epoch<=self._authorized_epoch):raise ValueError("Stale authorization")
            self._authorized_epoch=epoch;self.disconnect()

    def fail(self,error):
        with self.lock:self.error=error;self.reason="NEEDS_BASELINE";self.disconnect()

    @property
    def hold(self):return self._hold

    @hold.setter
    def hold(self,value):
        if type(value) is not bool:raise ValueError("Boolean required")
        with self.lock:
            if value!=self._hold:self._replay_generation+=1;self._hold=value

    def local_snapshot(self):
        """Control-plane view only; status() remains the exact wire ACK schema."""
        with self.lock:
            result=self.status()
            result.update(frontiers=replica_frontiers(result),
                verified_checkpoint=None,failover_eligible=False)
            return result

