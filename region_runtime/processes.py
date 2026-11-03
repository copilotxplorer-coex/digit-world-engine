"""Private local JSON pipes for R0 tests only. Not a public network service.
The parent is a trusted fault-injection router. It provisions ephemeral role keys.
No pickle messages, sockets, production credentials or shared World objects.
"""
import json
import multiprocessing as mp
import os
import secrets
from .contract import canonical

LIMIT = 1024 * 1024

def receive(conn):
    def invalid(value):raise ValueError("Non-finite JSON")
    return json.loads(conn.recv_bytes(LIMIT), parse_constant=invalid)

def send(conn, value):
    raw=canonical(value)
    if len(raw)>LIMIT:raise ValueError("Message too large")
    conn.send_bytes(raw)

def service(conn, role, config):
    target=None
    try:
        if role=="central":
            from .store import RegionStore
            target=RegionStore(config["path"],config["node_keys"],config["verifier_key"])
            allowed={"snapshot","grant","prepare","result","verify","commit","cancel","status"}
        elif role=="node":
            from .node import RegionNode
            target=RegionNode(config["node_id"],config["key"])
            allowed={"sync","grant","run"}
        elif role=="verifier":
            from .verifier import verify
            allowed={"verify"}
        else:raise ValueError("Unknown process role")
        send(conn,{"ready":True,"pid":os.getpid(),"role":role})
        while True:
            request=receive(conn)
            message_id=request.get("message_id")
            try:
                if request.get("protocol_version")!=1:raise ValueError("Wrong protocol")
                action=request["message_type"]
                if action=="stop":
                    send(conn,{"message_id":message_id,"ok":True,"result":{}});break
                if action not in allowed:raise ValueError("Action not allowed for role")
                args=request.get("args",[]);kwargs=request.get("kwargs",{})
                if not isinstance(args,list) or not isinstance(kwargs,dict):raise ValueError("Invalid arguments")
                if role=="verifier":result=verify(*args,key=config["key"],**kwargs)
                else:result=getattr(target,action)(*args,**kwargs)
                send(conn,{"message_id":message_id,"ok":True,"result":result})
            except Exception as exc:
                # No payload or key values are echoed.
                send(conn,{"message_id":message_id,"ok":False,"error":type(exc).__name__+": "+str(exc)})
    except (EOFError,BrokenPipeError,OSError):
        pass
    finally:
        if role=="central" and target is not None:target.close()
        conn.close()

class Peer:
    def __init__(self, role, config):
        context=mp.get_context("spawn")
        self.conn,child=context.Pipe(duplex=True)
        self.process=context.Process(target=service,args=(child,role,config),name="rr-"+role)
        self.closed=False
        try:
            self.process.start();child.close()
            if not self.conn.poll(15):raise TimeoutError("Process startup timed out")
            ready=receive(self.conn)
            if ready.get("ready") is not True:raise ValueError("Process not ready")
            self.pid=ready["pid"]
        except BaseException:
            child.close();self.kill();raise

    def call(self, action, *args, **kwargs):
        if self.closed:raise ValueError("Peer closed")
        message_id=secrets.token_hex(16)
        send(self.conn,{"protocol_version":1,"message_id":message_id,"message_type":action,"args":list(args),"kwargs":kwargs})
        if not self.conn.poll(15):
            # A late reply cannot be mistaken for the next operation.
            self.kill();raise TimeoutError("RPC outcome unknown; query durable operation after reconnect")
        reply=receive(self.conn)
        if reply.get("message_id")!=message_id:raise ValueError("Reply correlation mismatch")
        if not reply.get("ok"):raise ValueError(reply.get("error","Remote failure"))
        return reply["result"]

    def kill(self):
        if self.closed:return
        self.closed=True
        if self.process.pid is not None:
            if self.process.is_alive():self.process.terminate()
            self.process.join(5)
            if self.process.is_alive():self.process.kill();self.process.join(5)
        self.conn.close()

    def close(self):
        if self.closed:return
        try:self.call("stop")
        except (ValueError,EOFError,OSError,TimeoutError):pass
        self.process.join(5)
        self.kill()

class Cluster:
    def __init__(self, directory):
        from pathlib import Path
        self.node_keys={n:secrets.token_bytes(32) for n in "ABC"}
        self.verifier_key=secrets.token_bytes(32)
        self.central_config={"path":str(Path(directory)/"region-r0-lab.sqlite3"),"node_keys":self.node_keys,"verifier_key":self.verifier_key}
        self.nodes={};self.central=None;self.verifier=None
        try:
            self.central=Peer("central",self.central_config)
            for n in "ABC":self.nodes[n]=Peer("node",{"node_id":n,"key":self.node_keys[n]})
            self.verifier=Peer("verifier",{"key":self.verifier_key})
        except BaseException:self.close();raise

    def sync(self):
        snapshot=self.central.call("snapshot")
        return {n:p.call("sync",snapshot) for n,p in self.nodes.items() if not p.closed}

    def grant(self, leader):
        ready=self.sync()
        grants=self.central.call("grant",leader,ready[leader],ttl=60)
        for n,p in self.nodes.items():
            if not p.closed:p.call("grant",grants[n])
        return grants

    def batch(self, operation_id, commands, participants=None):
        proposal=self.central.call("prepare",operation_id,commands)["proposal"]
        for n in participants or [n for n,p in self.nodes.items() if not p.closed]:
            result=self.nodes[n].call("run",proposal)
            self.central.call("result",n,result)
        proof=self.verifier.call("verify",proposal)
        self.central.call("verify",operation_id,proof)
        result=self.central.call("commit",operation_id)
        self.sync()
        return result

    def restart_central(self):
        self.central.kill()
        self.central=Peer("central",self.central_config)

    def close(self):
        for peer in [*self.nodes.values(),self.verifier,self.central]:
            if peer is not None:peer.close()
