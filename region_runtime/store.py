"""Durable Central control-plane lab. No live World DB access or tick simulation."""
import copy
import json
import math
import sqlite3
import time
from .contract import RULESET, canonical, checked, commands_valid, fingerprint, fixture, identifier, signed, state_valid

class RegionStore:
    def __init__(self, path, node_keys, verifier_key, clock=time.monotonic):
        self.node_keys = dict(node_keys)
        if set(self.node_keys) != {"A", "B", "C"}:
            raise ValueError("R0 requires exactly A/B/C")
        self.verifier_key, self.clock = verifier_key, clock
        self.leader, self.deadline = None, 0
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS rr_meta(id INTEGER PRIMARY KEY CHECK(id=1), epoch INTEGER NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL, hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rr_ops(id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, proposal TEXT NOT NULL, status TEXT NOT NULL, results TEXT NOT NULL, verified TEXT);
        CREATE TABLE IF NOT EXISTS rr_checkpoints(version INTEGER PRIMARY KEY, state TEXT NOT NULL, hash TEXT NOT NULL, operation_id TEXT UNIQUE);
        """)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM rr_meta WHERE id=1").fetchone()
            if row is None:
                state = fixture(); raw = canonical(state).decode(); h = fingerprint(state)
                self.db.execute("INSERT INTO rr_meta VALUES(1,0,0,?,?)", (raw,h))
                self.db.execute("INSERT INTO rr_checkpoints VALUES(0,?,?,NULL)", (raw,h))
            else:
                state_valid(json.loads(row[3]))
                if fingerprint(json.loads(row[3])) != row[4]:
                    raise ValueError("Stored state corrupt")
                cp = self.db.execute("SELECT state,hash FROM rr_checkpoints WHERE version=?", (row[2],)).fetchone()
                if cp != (row[3],row[4]):
                    raise ValueError("Certified checkpoint missing or corrupt")
            # Restart invalidates all old grants; a fresh readiness proof is required.
            self.db.execute("UPDATE rr_meta SET epoch=epoch+1 WHERE id=1")
            self.db.commit()
        except BaseException:
            self.db.rollback();self.db.close();raise

    def close(self):
        self.db.close()

    def snapshot(self):
        epoch, version, raw, h = self.db.execute("SELECT epoch,version,state,hash FROM rr_meta WHERE id=1").fetchone()
        return {"epoch":epoch,"version":version,"state":json.loads(raw),"state_hash":h,"leader":self.leader,"active":self.leader is not None and self.clock()<self.deadline}

    def active(self):
        s = self.snapshot()
        if not s["active"]:
            raise ValueError("Region paused: no valid authority")
        return s

    def grant(self, leader, ready, ttl=30):
        identifier(leader)
        if leader not in self.node_keys:
            raise ValueError("Unknown node")
        if type(ttl) not in (int,float) or not math.isfinite(ttl) or not 0 < ttl <= 3600:
            raise ValueError("Invalid lab lease duration")
        s = self.snapshot(); proof = checked(self.node_keys[leader], ready)
        if proof != {"node_id":leader,"version":s["version"],"state_hash":s["state_hash"]}:
            raise ValueError("Candidate not caught up")
        self.db.execute("UPDATE rr_meta SET epoch=epoch+1 WHERE id=1")
        self.leader, self.deadline = leader, self.clock()+ttl
        epoch = self.snapshot()["epoch"]
        return {node:signed(key,{"node_id":node,"epoch":epoch,"ttl":ttl}) for node,key in self.node_keys.items()}

    def status(self, operation_id):
        identifier(operation_id)
        row = self.db.execute("SELECT status,proposal,verified FROM rr_ops WHERE id=?", (operation_id,)).fetchone()
        if row is None:
            return {"status":"UNKNOWN","operation_id":operation_id}
        return {"status":row[0],"operation_id":operation_id,"proposal":json.loads(row[1]),"verified":json.loads(row[2]) if row[2] else None}

    def prepare(self, operation_id, commands):
        identifier(operation_id)
        h = fingerprint(commands)
        old = self.db.execute("SELECT input_hash FROM rr_ops WHERE id=?", (operation_id,)).fetchone()
        if old:
            if old[0] != h:
                raise ValueError("Operation payload conflict")
            return self.status(operation_id)
        s = self.active();commands_valid(commands,s["state"])
        if self.db.execute("SELECT 1 FROM rr_ops WHERE status IN ('PENDING','VERIFIED','CONFLICT')").fetchone():
            raise ValueError("R0 pending limit reached; resolve old operation first")
        proposal={"operation_id":operation_id,"epoch":s["epoch"],"base_version":s["version"],"base_hash":s["state_hash"],"input_hash":h,"ruleset":RULESET,"base":s["state"],"commands":copy.deepcopy(commands)}
        self.db.execute("INSERT INTO rr_ops VALUES(?,?,?,'PENDING','{}',NULL)",(operation_id,h,canonical(proposal).decode()))
        return self.status(operation_id)

    def pending(self, operation_id):
        status=self.status(operation_id)
        if status["status"] not in ("PENDING","VERIFIED"):
            raise ValueError("Operation not pending")
        s=self.active();p=status["proposal"]
        if (p["epoch"],p["base_version"],p["base_hash"]) != (s["epoch"],s["version"],s["state_hash"]):
            raise ValueError("Stale epoch or base version")
        return status,s

    def result(self, node_id, envelope):
        if node_id not in self.node_keys:
            raise ValueError("Unknown node")
        body=checked(self.node_keys[node_id],envelope)
        operation_id=body["operation_id"]
        existing=self.db.execute("SELECT status,results FROM rr_ops WHERE id=?",(operation_id,)).fetchone()
        if existing:
            receipts=json.loads(existing[1])
            if node_id in receipts:
                if receipts[node_id]!=envelope:raise ValueError("Conflicting replica receipt")
                return {"status":existing[0],"operation_id":operation_id,"duplicate":True}
            if existing[0]!="PENDING":raise ValueError("Result set already sealed")
        status,s=self.pending(operation_id);p=status["proposal"]
        if body["node_id"] != node_id:
            raise ValueError("Node identity mismatch")
        for k in ("epoch","base_version","base_hash","input_hash","ruleset"):
            if body[k]!=p[k]:raise ValueError("Result binding mismatch")
        state_valid(body["state"])
        if fingerprint(body["state"])!=body["state_hash"]:
            raise ValueError("Result hash mismatch")
        row=self.db.execute("SELECT results FROM rr_ops WHERE id=?",(p["operation_id"],)).fetchone()
        results=json.loads(row[0])
        if node_id in results and results[node_id]!=envelope:
            raise ValueError("Conflicting replica receipt")
        results[node_id]=envelope
        self.db.execute("UPDATE rr_ops SET results=? WHERE id=?",(canonical(results).decode(),p["operation_id"]))
        return {"status":"RECEIVED","operation_id":p["operation_id"]}

    def verify(self, operation_id, envelope):
        old=self.status(operation_id)
        if old.get("verified") is not None:
            if old["verified"]!=envelope:raise ValueError("Conflicting verifier receipt")
            return old
        status,s=self.pending(operation_id);p=status["proposal"]
        body=checked(self.verifier_key,envelope)
        for k in ("operation_id","epoch","base_version","base_hash","input_hash","ruleset"):
            if body[k]!=p[k]:raise ValueError("Verifier evidence binding mismatch")
        state_valid(body["state"])
        if fingerprint(body["state"])!=body["state_hash"]:
            raise ValueError("Verifier hash mismatch")
        results=json.loads(self.db.execute("SELECT results FROM rr_ops WHERE id=?",(operation_id,)).fetchone()[0])
        if s["leader"] not in results or len(results)<2:
            raise ValueError("Need leader and at least one independent replica receipt")
        outcome="VERIFIED" if all(r["body"]["state_hash"]==body["state_hash"] for r in results.values()) else "CONFLICT"
        self.db.execute("UPDATE rr_ops SET status=?,verified=? WHERE id=?",(outcome,canonical(envelope).decode(),operation_id))
        return self.status(operation_id)

    def commit(self, operation_id):
        old=self.status(operation_id)
        if old["status"] in ("COMMITTED","CANCELLED"):
            return old
        self.db.execute("BEGIN IMMEDIATE")
        try:
            status,s=self.pending(operation_id)
            if status["status"]!="VERIFIED":raise ValueError("Not verified")
            body=checked(self.verifier_key,status["verified"]);p=status["proposal"]
            for k in ("operation_id","epoch","base_version","base_hash","input_hash","ruleset"):
                if body[k]!=p[k]:raise ValueError("Commit evidence mismatch")
            if fingerprint(body["state"])!=body["state_hash"]:raise ValueError("Commit hash mismatch")
            results=json.loads(self.db.execute("SELECT results FROM rr_ops WHERE id=?",(operation_id,)).fetchone()[0])
            if s["leader"] not in results or len(results)<2:raise ValueError("Replica evidence missing")
            for node_id,receipt in results.items():
                result=checked(self.node_keys[node_id],receipt)
                if result["state_hash"]!=body["state_hash"]:raise ValueError("Replica evidence conflict")
            version=s["version"]+1;raw=canonical(body["state"]).decode()
            self.db.execute("UPDATE rr_meta SET version=?,state=?,hash=? WHERE id=1 AND version=? AND epoch=?",(version,raw,body["state_hash"],s["version"],s["epoch"]))
            self.db.execute("INSERT INTO rr_checkpoints VALUES(?,?,?,?)",(version,raw,body["state_hash"],operation_id))
            self.db.execute("UPDATE rr_ops SET status='COMMITTED' WHERE id=?",(operation_id,))
            self.db.commit()
        except BaseException:
            self.db.rollback();raise
        return self.status(operation_id)

    def cancel(self, operation_id):
        old=self.status(operation_id)
        if old["status"] in ("COMMITTED","CANCELLED"):return old
        if old["status"]=="UNKNOWN":raise ValueError("Unknown operation")
        self.db.execute("UPDATE rr_ops SET status='CANCELLED' WHERE id=? AND status!='COMMITTED'",(operation_id,))
        return self.status(operation_id)
