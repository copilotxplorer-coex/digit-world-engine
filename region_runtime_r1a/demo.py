"""Explicit opt-in R1-A autonomous Node loop demonstration."""
import argparse
import json
from pathlib import Path
import secrets
import tempfile
import time
from region_runtime.contract import signed
from .process import NodeProcess
from .replay import replay

def grant(key,epoch=1,ttl=30):
    return signed(key,{"node_id":"A","region_id":"lab-region","epoch":epoch,"ttl":ttl})

def command(key,seq=1,*,command_id=None,epoch=1,dx=1,dy=0,actor="p1",session_id="s1",authority_version=1):
    return signed(key,{"command_id":command_id or "cmd"+str(seq),"session_id":session_id,"actor":actor,"client_seq":seq,"epoch":epoch,"authority_version":authority_version,"region_id":"lab-region","dx":dx,"dy":dy})

def wait_for(node,predicate,timeout=10):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=node.call("snapshot")
        if value["error"]:raise AssertionError("Runtime fault")
        if predicate(value):return value
        time.sleep(.02)
    raise AssertionError("Node did not reach expected state")

def all_logs(node):
    records=[]
    while True:
        page=node.call("log",len(records),128)
        records.extend(page)
        if len(page)<128:return records
        if len(records)>10000:raise AssertionError("Unbounded log")

def run_demo():
    with tempfile.TemporaryDirectory(prefix="digit-r1a-") as directory:
        key=secrets.token_bytes(32);session_key=secrets.token_bytes(32)
        config={"path":str(Path(directory)/"r1a-lab.sqlite3"),"authority_key":key,"settings":{"hz":30,"max_ticks":600}}
        node=NodeProcess(config)
        try:
            node.call("activate",grant(key));node.call("session","s1","p1",session_key.hex(),1)
            start=node.call("snapshot")["tick_id"]
            # Driver sends NO messages while the child owns and advances its loop.
            time.sleep(.3)
            after=wait_for(node,lambda s:s["tick_id"]>start)
            if after["tick_id"]<=start:raise AssertionError("Driver was required to drive ticks")
            envelopes=[command(session_key,1),command(session_key,2),command(session_key,3,dx=0,dy=1)]
            for envelope in envelopes:node.call("submit",envelope)
            done=wait_for(node,lambda s:s["state"]["seq"]==3)
            if done["state"]["positions"]["p1"]!=[1,1]:raise AssertionError("Movement fixture mismatch")
            duplicate=node.call("submit",envelopes[0])
            if not duplicate["duplicate"] or duplicate["status"]!="SIMULATED":raise AssertionError("Duplicate not idempotent")
            before=node.call("snapshot");node.kill()
            node=NodeProcess(config);recovered=node.call("snapshot")
            if recovered["reason"]!="RECOVERY_PAUSED" or recovered["tick_id"]<before["tick_id"] or recovered["state"]!=before["state"]:raise AssertionError("Crash recovery mismatch")
            if node.call("receipt","cmd1")["status"]!="SIMULATED":raise AssertionError("Durable receipt lost")
            replayed=replay(all_logs(node))
            if replayed["state"]!=recovered["state"] or replayed["head_hash"]!=recovered["head_hash"]:raise AssertionError("Offline replay mismatch")
            paused_tick=recovered["tick_id"];time.sleep(.15)
            if node.call("snapshot")["tick_id"]!=paused_tick:raise AssertionError("Restart resumed without grant")
            node.call("activate",grant(key,epoch=2));node.call("session","s1","p1",session_key.hex(),1)
            old_rejected=False
            try:node.call("submit",envelopes[0])
            except ValueError:old_rejected=True
            if not old_rejected:raise AssertionError("Old epoch accepted")
            node.call("submit",command(session_key,1,command_id="epoch2cmd",epoch=2,dx=-1))
            final=wait_for(node,lambda s:s["state"]["seq"]==4)
            if final["state"]["positions"]["p1"]!=[0,1]:raise AssertionError("Resume state mismatch")
            return {"passed":True,"node_process_separate":True,"autonomous_ticks_without_driver_calls":after["tick_id"]-start,"simulated_commands":4,"duplicate_safe":True,"crash_recovered_local_log":True,"restart_paused":True,"offline_reference_replay_match":True,"old_epoch_rejected":True,"verified_tick":0,"committed_tick":0,"replication_connected":False,"trusted_verifier_pipeline_connected":False,"live_world_connected":False,"lab_hz":30,"scope":"R1-A synthetic fixture only; no production authority or anti-cheat claim"}
        finally:node.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--enable-lab",action="store_true")
    args=parser.parse_args()
    if not args.enable_lab:parser.error("Disabled by default; pass --enable-lab")
    print(json.dumps(run_demo(),indent=2));print("PASS: R1-A autonomous fixture loop and local logs; not verified gameplay or failover.")
if __name__=="__main__":main()
