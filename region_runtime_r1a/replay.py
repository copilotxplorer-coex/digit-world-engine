"""Offline reference replay, not a live Verifier or a commit certificate."""
from region_runtime.contract import fingerprint, fixture
from region_runtime.verifier import reference
from .runtime import RULESET, ZERO, Runtime

def replay(records):
    if not isinstance(records,list) or len(records)>10000:raise ValueError("Invalid log size")
    state=fixture();previous=ZERO;seen=set();epoch=0
    for tick,entry in enumerate(records,1):
        if set(entry)!={"record","hash"}:raise ValueError("Invalid log envelope")
        r=entry["record"]
        if set(r)!={"tick_id","region_id","region_epoch","ruleset","previous_hash","commands","state_hash"}:raise ValueError("Invalid record")
        if r["tick_id"]!=tick or r["previous_hash"]!=previous or fingerprint(r)!=entry["hash"]:raise ValueError("Log chain mismatch")
        if r["ruleset"]!=RULESET or r["region_id"]!="lab-region" or type(r["region_epoch"]) is not int or not epoch<=r["region_epoch"]:raise ValueError("Log authority mismatch")
        epoch=r["region_epoch"];commands=[]
        if not isinstance(r["commands"],list) or len(r["commands"])>128:raise ValueError("Invalid commands")
        for item in r["commands"]:
            if set(item)!={"command_id","body","event_sequence","auth_proof"}:raise ValueError("Invalid command log")
            b=item["body"];Runtime._validate_body(b)
            proof=item["auth_proof"]
            if not isinstance(proof,str) or len(proof)!=64 or any(c not in "0123456789abcdef" for c in proof):raise ValueError("Invalid proof encoding")
            if item["command_id"]!=b["command_id"] or b["epoch"]!=epoch or b["command_id"] in seen:raise ValueError("Duplicate or wrong-epoch command")
            seen.add(b["command_id"])
            commands.append({"seq":item["event_sequence"],"actor":b["actor"],"dx":b["dx"],"dy":b["dy"]})
        if commands:state=reference(state,commands)
        if fingerprint(state)!=r["state_hash"]:raise ValueError("Replay state mismatch")
        previous=entry["hash"]
    return {"tick_id":len(records),"state":state,"head_hash":previous,"commands":len(seen)}
