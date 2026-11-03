"""R1-A subclass: independent per-peer senders never hold the tick lock during I/O."""
import json,secrets,socket,sqlite3,threading,time
from pathlib import Path
from region_runtime.contract import canonical,fingerprint,fixture,integer
from region_runtime_r1a.runtime import Runtime,ZERO
from .wire import Channel,packet,unpack,number,exact,digest,MAX_BATCH_BYTES,MAX_BATCH_TICKS
from .replica import Conflict,validate_entry
from .frontiers import primary_frontiers,require_unverified

class Source:
    def __init__(self,path):
        self.db=sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True,isolation_level=None,timeout=.2)
        self.db.execute("PRAGMA query_only=ON")
    def meta(self):return self.db.execute("SELECT epoch,tick FROM ra_meta WHERE id=1").fetchone()
    def at(self,tick):
        if tick==0:return ZERO,fingerprint(fixture())
        row=self.db.execute("SELECT body,hash FROM ra_ticks WHERE tick=?",(tick,)).fetchone()
        if not row:raise Conflict("NEEDS_BASELINE: missing source tick")
        r=json.loads(row[0]);validate_entry({"record":r,"hash":row[1]});return row[1],r["state_hash"]
    def batch(self,after):
        result=[]
        for raw,h in self.db.execute("SELECT body,hash FROM ra_ticks WHERE tick>? ORDER BY tick LIMIT ?",(after,MAX_BATCH_TICKS)):
            entry={"record":json.loads(raw),"hash":h};validate_entry(entry)
            if entry["record"]["tick_id"]!=after+len(result)+1:raise Conflict("Missing source log")
            if len(canonical(result+[entry]))>MAX_BATCH_BYTES:break
            result.append(entry)
        if not result and self.meta()[1]>after:raise Conflict("NEEDS_BASELINE: missing source suffix")
        return result
    def close(self):self.db.close()

def validate_ack(source,status,*,node,boot,sent_limit,target,previous=None):
    exact(status,"node_id stored_tick stored_head_hash applied_tick applied_state_hash matched_tick target_tick reason pending evidence error verified_tick committed_tick boot")
    if status["node_id"]!=node or status["boot"]!=boot:raise ValueError("ACK identity")
    s=integer(status["stored_tick"],0,sent_limit);a=integer(status["applied_tick"],0,s)
    integer(status["matched_tick"],a,a);integer(status["target_tick"],target,target);integer(status["pending"],s-a,s-a)
    integer(status["verified_tick"],0,0);integer(status["committed_tick"],0,0)
    digest(status["stored_head_hash"]);digest(status["applied_state_hash"])
    if status["reason"] not in ("DISCONNECTED","CATCHING_UP","FOLLOWING","DIVERGED","NEEDS_BASELINE"):raise ValueError("Invalid replica state")
    if status["reason"]=="FOLLOWING" and a<target:raise ValueError("Premature FOLLOWING")
    if previous and (s<previous["stored_tick"] or a<previous["applied_tick"]):raise ValueError("Regressing ACK")
    if status["stored_head_hash"]!=source.at(s)[0] or status["applied_state_hash"]!=source.at(a)[1]:raise Conflict("Replica common-point hash mismatch")
    if status["evidence"] is not None or status["reason"]=="DIVERGED":raise Conflict("Replica divergence")
    return status

class Primary(Runtime):
    def __init__(self,path,key,peers,*,max_lead=24,**settings):
        integer(max_lead,1,1000)
        if set(peers)!={"B","C"}:raise ValueError("Exactly B/C required")
        for node,(port,secret) in peers.items():
            integer(port,1,65535)
            if not isinstance(secret,bytes) or len(secret)!=32:raise ValueError("Invalid peer key")
        self.peer_lock=threading.Lock();self.peers=peers;self.progress={n:{"reason":"DISCONNECTED","matched_tick":0} for n in peers}
        self.max_lead=max_lead;self.replication_frontier=0;self.replication_fault=None;self.sender_stop=threading.Event();self.senders=[];self.path=Path(path)
        for parent in (self.path,*self.path.parents):
            if parent.is_symlink() or bool(getattr(parent,"is_junction",lambda:False)()):raise ValueError("Linked lab path refused")
        super().__init__(path,key,**settings)
    def _pause(self):
        if self.reason=="REPLICA_LAG":self.reason="RUNNING"
        super()._pause()
        with self.peer_lock:
            if self.replication_fault:self.reason="REPLICA_CONFLICT"
            elif self.reason=="RUNNING" and self._meta()[1]-self.replication_frontier>=self.max_lead:self.reason="REPLICA_LAG"
        return self.reason!="RUNNING"
    def _update(self,node,status,epoch):
        require_unverified(status["verified_tick"],status["committed_tick"])
        with self.peer_lock:
            self.progress[node]={**status,"epoch":epoch}
            if status["reason"] in ("FOLLOWING","CATCHING_UP"):
                self.replication_frontier=max(self.replication_frontier,status["matched_tick"])
    def _fault(self,node,exc):
        with self.peer_lock:
            self.replication_fault=self.replication_fault or {"node_id":node,"reason":str(exc)[:180]}
            self.progress[node]={**self.progress[node],"reason":"DIVERGED"}
    def _sender(self,node):
        port,key=self.peers[node];source=Source(self.path)
        try:
            while not self.sender_stop.is_set():
                channel=None
                try:
                    epoch,target=source.meta()
                    if not epoch:self.sender_stop.wait(.05);continue
                    session=secrets.token_hex(16);request=secrets.token_hex(16)
                    channel=Channel(socket.create_connection(("127.0.0.1",port),timeout=.6))
                    channel.send(packet(key,"REPLICA_HELLO","A",node,epoch,session,"unknown",request,{"target_tick":target}))
                    body=unpack(key,channel.receive(),sender=node,recipient="A",epoch=epoch,session=session)
                    if body["message_id"]!=request or body["message_type"]!="REPLICA_HELLO":raise ValueError("Unexpected HELLO")
                    boot=body["replica_boot"];status=body["payload"]
                    if status.get("reason")=="NEEDS_BASELINE":raise Conflict("Replica NEEDS_BASELINE")
                    # Resume claims are checked against the retained common prefix, not trusted blindly.
                    status=validate_ack(source,status,node=node,boot=boot,sent_limit=target,target=target)
                    self._update(node,status,epoch);sent=status["stored_tick"]
                    while not self.sender_stop.wait(.025):
                        if source.meta()[0]!=epoch:break
                        entries=source.batch(status["stored_tick"])[:1]
                        if entries:sent=max(sent,entries[-1]["record"]["tick_id"])
                        request=secrets.token_hex(16);kind="LOG_BATCH" if entries else "REPLICA_STATUS"
                        channel.send(packet(key,kind,"A",node,epoch,session,boot,request,{"entries":entries} if entries else {}))
                        reply=unpack(key,channel.receive(),sender=node,recipient="A",epoch=epoch,session=session,boot=boot)
                        if reply["message_id"]!=request or reply["message_type"] not in ("STORED_ACK","APPLIED_ACK","GAP_REQUEST","REPLICA_STATUS","REPLICA_CONFLICT"):raise ValueError("Unsolicited ACK")
                        next_status=validate_ack(source,reply["payload"],node=node,boot=boot,sent_limit=sent,target=target,previous=status)
                        if reply["message_type"]=="REPLICA_CONFLICT":raise Conflict("Replica reported conflict")
                        if reply["message_type"]=="GAP_REQUEST" and next_status["stored_tick"]!=status["stored_tick"]:raise ValueError("Invalid gap frontier")
                        status=next_status;self._update(node,status,epoch)
                except Conflict as exc:
                    self._fault(node,exc);return
                except (OSError,EOFError,ValueError,sqlite3.Error,KeyError,TypeError,RecursionError) as exc:
                    with self.peer_lock:self.progress[node]={**self.progress[node],"reason":"DISCONNECTED","error":type(exc).__name__}
                finally:
                    if channel:channel.close()
                self.sender_stop.wait(.1)
        except Exception as exc:self._fault(node,exc)
        finally:source.close()
    def start(self):
        super().start()
        for node in self.peers:
            t=threading.Thread(target=self._sender,args=(node,),name="r1b-send-"+node);self.senders.append(t);t.start()
    @property
    def safe_frontier(self):
        """Deprecated replication-only alias. Never a Verified Checkpoint."""
        return self.replication_frontier
    def snapshot(self):
        # Copy peer observations first, then sample the monotonically advancing
        # local log. No peer lock is held while calling the base runtime.
        with self.peer_lock:
            progress=json.loads(json.dumps(self.progress))
            frontier=self.replication_frontier
            fault=json.loads(json.dumps(self.replication_fault))
        result=super().snapshot()
        frontiers=primary_frontiers(result["tick_id"],frontier)
        result.update(replicas=progress,replication_frontier=frontier,
            safe_frontier=frontier,safe_frontier_deprecated=True,
            safe_frontier_meaning="REPLICATION_ONLY_NOT_VERIFIED_CHECKPOINT",
            max_lead=self.max_lead,replication_fault=fault,
            frontiers=frontiers,verified_checkpoint=None,
            verified_tick=0,committed_tick=0,failover_eligible=False)
        return result
    def close(self):
        self.stop_event.set()
        self.sender_stop.set()
        for t in self.senders:
            t.join(5)
            if t.is_alive():raise RuntimeError("Sender did not stop")
        super().close()
