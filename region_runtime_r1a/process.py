"""Private local JSON-pipe lab. No network listener or public control API."""
import multiprocessing as mp
import os
import secrets
from region_runtime.processes import receive,send
from .runtime import Runtime

def serve(conn,config):
    runtime=None
    try:
        runtime=Runtime(config["path"],config["authority_key"],**config.get("settings",{}));runtime.start()
        send(conn,{"ready":True,"pid":os.getpid()})
        allowed={"activate","submit","snapshot","receipt","log"}
        while True:
            request=receive(conn);mid=request.get("message_id")
            try:
                if request.get("protocol_version")!=1:raise ValueError("Wrong protocol")
                action=request["message_type"];args=request.get("args",[])
                if not isinstance(args,list):raise ValueError("Invalid arguments")
                if action=="stop":send(conn,{"message_id":mid,"ok":True,"result":{}});break
                if action=="session":
                    if len(args)!=4:raise ValueError("Invalid session provisioning")
                    result=runtime.session(args[0],args[1],bytes.fromhex(args[2]),args[3])
                elif action in allowed:result=getattr(runtime,action)(*args)
                else:raise ValueError("Action not allowed; tick is autonomous")
                send(conn,{"message_id":mid,"ok":True,"result":result})
            except Exception as exc:
                # Never echo payloads, session secrets or SQLite data.
                send(conn,{"message_id":mid,"ok":False,"error":type(exc).__name__})
    except (EOFError,BrokenPipeError,OSError):pass
    finally:
        if runtime is not None:runtime.close()
        conn.close()

class NodeProcess:
    def __init__(self,config):
        context=mp.get_context("spawn");self.conn,child=context.Pipe()
        self.process=context.Process(target=serve,args=(child,config),name="region-r1a-node-A")
        self.closed=False
        try:
            self.process.start();child.close()
            if not self.conn.poll(15):raise TimeoutError("Node startup timeout")
            ready=receive(self.conn)
            if not ready.get("ready"):raise ValueError("Node not ready")
            self.pid=ready["pid"]
        except BaseException:child.close();self.kill();raise
    def call(self,action,*args):
        if self.closed:raise ValueError("Node process closed")
        mid=secrets.token_hex(16)
        send(self.conn,{"protocol_version":1,"message_id":mid,"message_type":action,"args":list(args)})
        if not self.conn.poll(15):self.kill();raise TimeoutError("Outcome unknown; query receipt after recovery")
        result=receive(self.conn)
        if result.get("message_id")!=mid:raise ValueError("Reply mismatch")
        if not result.get("ok"):raise ValueError(result.get("error","Remote error"))
        return result["result"]
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
        except (ValueError,OSError,EOFError,TimeoutError):pass
        self.process.join(5);self.kill()
