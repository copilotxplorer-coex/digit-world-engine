"""Autonomous A/B/C fixture replication, not verified gameplay or failover."""
import argparse,json,tempfile,time,secrets
from pathlib import Path
from region_runtime.contract import signed,fingerprint
from .process import Cluster

def wait_for(fn,timeout=12):
    deadline=time.monotonic()+timeout;last=None
    while time.monotonic()<deadline:
        last=fn()
        if last:return last
        time.sleep(.03)
    raise AssertionError("Lab condition timed out")

def exercise(directory):
    c=Cluster(directory,enable_lab=True,hz=20,max_lead=12,max_ticks=240)
    try:
        c.activate();a=c.nodes["A"];b=c.nodes["B"];cc=c.nodes["C"]
        key=secrets.token_bytes(32);a.call("session","player-session","p1",key.hex(),1)
        command={"command_id":"move-1","session_id":"player-session","actor":"p1","client_seq":1,"epoch":1,"authority_version":1,"region_id":"lab-region","dx":1,"dy":0}
        a.call("submit",signed(key,command))
        wait_for(lambda:a.call("receipt","move-1")["status"]=="SIMULATED")
        wait_for(lambda:all(a.call("snapshot")["replicas"][n].get("matched_tick",0)>=3 for n in ("B","C")))
        pids={n:c.nodes[n].pid for n in c.nodes}
        if len(set(pids.values()))!=3:raise AssertionError("Not independent processes")
        b.call("hold_replay",True);before=a.call("snapshot")["tick_id"]
        wait_for(lambda:a.call("snapshot")["tick_id"]>=before+15)
        slow=b.call("snapshot");fast=cc.call("snapshot")
        if not slow["stored_tick"]>slow["applied_tick"] or fast["applied_tick"]<=slow["applied_tick"]:raise AssertionError("Receive/replay not independent")
        c.nodes["B"].kill();target=a.call("snapshot")["tick_id"];b=c.restart("B")
        wait_for(lambda:b.call("snapshot")["applied_tick"]>=target)
        c.nodes["B"].kill();c.nodes["C"].kill()
        wait_for(lambda:a.call("snapshot")["reason"]=="REPLICA_LAG")
        paused=a.call("snapshot");time.sleep(.2)
        if a.call("snapshot")["tick_id"]!=paused["tick_id"]:raise AssertionError("Unbounded lead")
        b=c.restart("B");cc=c.restart("C");target=paused["tick_id"]
        wait_for(lambda:all(c.nodes[n].call("snapshot")["applied_tick"]>=target for n in ("B","C")))
        wait_for(lambda:a.call("snapshot")["tick_id"]>target)
        wait_for(lambda:a.call("snapshot")["reason"]=="LOG_LIMIT",timeout=30);final_tick=0
        import sqlite3
        db=sqlite3.connect(Path(directory)/"r1a-lab.sqlite3")
        try:final_tick,state=db.execute("SELECT tick,state FROM ra_meta WHERE id=1").fetchone()
        finally:db.close()
        wait_for(lambda:all(c.nodes[n].call("snapshot")["applied_tick"]==final_tick for n in ("B","C")))
        for n in ("B","C"):
            if fingerprint(c.nodes[n].call("state"))!=fingerprint(json.loads(state)):raise AssertionError("Final state mismatch")
        return {"pids":pids,"final_tick":final_tick,"slow_replica":slow,"paused_tick":target,"verified_tick":0,"committed_tick":0,"production_failover":False}
    finally:c.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--enable-lab",action="store_true");args=p.parse_args()
    if not args.enable_lab:p.error("Lab disabled; use --enable-lab")
    with tempfile.TemporaryDirectory(prefix="digit-r1b-lab-") as directory:
        result=exercise(directory);print(json.dumps(result,indent=2))
    print("PASS: R1-B autonomous A/B/C replication, restart catch-up and bounded pause; not verified gameplay or failover.")
if __name__=="__main__":main()
