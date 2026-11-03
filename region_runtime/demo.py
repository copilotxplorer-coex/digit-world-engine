"""Explicit opt-in, five-child-process Region Runtime R0 fault demo."""
import argparse
import json
import os
import tempfile
from .processes import Cluster

def command(seq, dx=1, dy=0):
    return {"seq":seq,"actor":"p1","dx":dx,"dy":dy}

def run_demo():
    with tempfile.TemporaryDirectory(prefix="digit-region-r0-") as directory:
        cluster=Cluster(directory)
        try:
            pids=[cluster.central.pid,cluster.verifier.pid]+[p.pid for p in cluster.nodes.values()]
            if len(set(pids+[os.getpid()]))!=6:raise AssertionError("Processes not isolated")
            cluster.grant("A")
            first=cluster.batch("move1",[command(1)])
            if first["status"]!="COMMITTED":raise AssertionError("Initial commit failed")
            if cluster.central.call("commit","move1")["status"]!="COMMITTED":raise AssertionError("Duplicate commit failed")
            if cluster.central.call("snapshot")["version"]!=1:raise AssertionError("Duplicate commit changed version")
            proposal=cluster.central.call("prepare","interrupted",[command(2,0,1)])["proposal"]
            old_result=cluster.nodes["A"].call("run",proposal)
            cluster.nodes["A"].kill()
            # Explicit test-orchestrated takeover, NOT an automatic detector.
            cluster.grant("B")
            stale_rejected=False
            try:cluster.central.call("result","A",old_result)
            except ValueError:stale_rejected=True
            if not stale_rejected:raise AssertionError("Old epoch accepted")
            cluster.central.call("cancel","interrupted")
            cluster.batch("move2",[command(2,0,1)],participants=["B","C"])
            before=cluster.central.call("snapshot")
            cluster.restart_central()
            recovered=cluster.central.call("snapshot")
            if recovered["state_hash"]!=before["state_hash"] or recovered["version"]!=2 or recovered["active"]:
                raise AssertionError("Restart recovery/fencing failed")
            if cluster.central.call("status","move2")["status"]!="COMMITTED":raise AssertionError("Receipt lost")
            cluster.grant("B")
            cluster.batch("move3",[command(3,-1,0)],participants=["B","C"])
            final=cluster.central.call("snapshot")
            if final["state"]["positions"]["p1"]!=[0,1] or final["version"]!=3:raise AssertionError("Final state mismatch")
            return {"passed":True,"initial_child_processes":5,"roles":["Central","A","B","C","TrustedVerifier"],"committed_batches":3,"duplicate_commit_safe":True,"old_epoch_rejected":True,"primary_process_terminated":True,"central_process_restarted":True,"durable_receipt_recovered":True,"final_position":[0,1],"takeover":"test-orchestrated","automatic_failure_detector":False,"live_world_connected":False,"fixture_rules_only":True,"production_security_claim":False,"performance_claim":False,"transport":"private local JSON pipes; test-only"}
        finally:cluster.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-lab",action="store_true",help="Explicitly run temporary isolated R0 fixture")
    args=parser.parse_args()
    if not args.enable_lab:
        parser.error("Disabled by default; pass --enable-lab. No live World integration.")
    print(json.dumps(run_demo(),indent=2))
    print("PASS: Region Runtime R0 fixture; not production region failover or real gameplay integration.")
if __name__=="__main__":main()
