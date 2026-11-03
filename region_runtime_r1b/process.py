"""Spawn-safe local lab processes. Control JSON pipe is separate from replication TCP."""
import multiprocessing as mp
import os,secrets,socket,threading,time
from pathlib import Path
from region_runtime.processes import receive,send
from region_runtime.contract import signed,integer
from region_runtime_r1a.process import NodeProcess as BaseProcess
from .primary import Primary
from .replica import Replica,Conflict,Gap,Backpressure
from .wire import Channel,packet,unpack,exact,listener

class ReplicaService:
    def __init__(self,replica,sock,key,epoch=1):
        self.replica=replica;self.sock=sock;self.key=key;self.epoch=epoch;self.stop=threading.Event();self.thread=None;self.failpoint=None;self.network_hold=False;self.last_error=None
        self.replica.authorize_epoch(epoch)
    def _connection(self,channel):
        r=self.replica;key=self.key;node=r.node_id;token=None
        try:
            envelope=channel.receive()
            with r.lock:
                if self.stop.is_set():return
                first=unpack(key,envelope,sender="A",recipient=node,epoch=self.epoch)
                if first["message_type"]!="REPLICA_HELLO" or first["replica_boot"]!="unknown":raise ValueError("HELLO required")
                exact(first["payload"],"target_tick");session=first["replica_session_id"];epoch=first["region_epoch"]
                try:status=r.negotiate(session,epoch,first["payload"]["target_tick"]);token=r.connection_token()
                except Conflict:
                    r.conflict(0,"SOURCE_BEHIND",r.status()["stored_head_hash"],"unknown");status=r.status()
            channel.send(packet(key,"REPLICA_HELLO",node,"A",epoch,session,r.boot,first["message_id"],status))
            if token is None:return
            while not self.stop.is_set():
                envelope=channel.receive()
                while self.network_hold and not self.stop.wait(.02):pass
                with r.lock:
                    if self.stop.is_set():return
                    r.require_connection(token)
                    body=unpack(key,envelope,sender="A",recipient=node,epoch=self.epoch,session=session,boot=r.boot)
                    if epoch!=self.epoch:raise ValueError("Fenced epoch")
                    kind=body["message_type"];reply="APPLIED_ACK"
                    try:
                        if kind=="LOG_BATCH":
                            exact(body["payload"],"entries")
                            if self.failpoint=="before_store":os._exit(77)
                            r.ingest(body["payload"]["entries"],epoch)
                            if self.failpoint=="after_store":os._exit(78)
                            reply="STORED_ACK"
                        elif kind=="REPLICA_STATUS":exact(body["payload"],"")
                        else:raise ValueError("Unknown replication message")
                    except Gap:reply="GAP_REQUEST"
                    except Backpressure:reply="REPLICA_STATUS"
                    except Conflict:reply="REPLICA_CONFLICT"
                    r.last_contact=time.monotonic();status=r.status()
                    if status["reason"]=="DIVERGED":reply="REPLICA_CONFLICT"
                channel.send(packet(key,reply,node,"A",epoch,session,r.boot,body["message_id"],status))
        finally:
            if token is not None:r.disconnect(token)
    def _loop(self):
        self.sock.settimeout(.1)
        while not self.stop.is_set():
            channel=None
            try:
                conn,addr=self.sock.accept()
                if addr[0]!="127.0.0.1":conn.close();continue
                channel=Channel(conn);self._connection(channel)
            except socket.timeout:pass
            except (ValueError,EOFError,OSError,KeyError,TypeError,RecursionError) as exc:self.last_error=type(exc).__name__
            except Exception as exc:self.replica.fail(type(exc).__name__)
            finally:
                if channel:channel.close()
    def start(self):self.thread=threading.Thread(target=self._loop,name="r1b-receive");self.thread.start()
    def close(self):
        with self.replica.lock:self.stop.set();self.replica.disconnect()
        if self.thread:
            self.thread.join(5)
            if self.thread.is_alive():raise RuntimeError("Receiver did not stop")
        self.sock.close()

    def authorize_epoch(self,epoch):
        with self.replica.lock:self.replica.authorize_epoch(epoch);self.epoch=epoch

def serve(conn,config):
    runtime=None;service=None
    try:
        if config.get("enable_lab") is not True:raise ValueError("Lab disabled")
        node=config["node_id"]
        if node=="A":runtime=Primary(config["path"],config["authority_key"],config["peers"],**config.get("settings",{}))
        else:
            runtime=Replica(config["path"],node,**config.get("settings",{}));service=ReplicaService(runtime,config["listener"],config["peer_key"],config.get("epoch",1));service.start()
        runtime.start();send(conn,{"ready":True,"pid":os.getpid()})
        while True:
            request=receive(conn);mid=request.get("message_id")
            try:
                if request.get("protocol_version")!=1:raise ValueError("Wrong protocol")
                action=request["message_type"];args=request.get("args",[])
                if not isinstance(args,list):raise ValueError("Invalid args")
                if action=="stop":send(conn,{"message_id":mid,"ok":True,"result":{}});break
                if node=="A":
                    if action=="session":result=runtime.session(args[0],args[1],bytes.fromhex(args[2]),args[3])
                    elif action in ("activate","submit","snapshot","receipt","log"):result=getattr(runtime,action)(*args)
                    else:raise ValueError("Action not allowed")
                elif action=="snapshot":result=runtime.local_snapshot()
                elif action=="state":result=runtime.state()
                elif action=="hold_replay":
                    if len(args)!=1 or type(args[0]) is not bool:raise ValueError("Boolean required")
                    with runtime.lock:runtime.hold=args[0]
                    result=runtime.status()
                elif action=="hold_network":
                    if len(args)!=1 or type(args[0]) is not bool:raise ValueError("Boolean required")
                    service.network_hold=args[0];result=runtime.status()
                elif action=="failpoint":
                    if len(args)!=1 or args[0] not in (None,"before_store","after_store"):raise ValueError("Invalid failpoint")
                    service.failpoint=args[0];result={"armed":args[0]}
                elif action=="authorize_epoch":
                    ep=integer(args[0],1,10**9)
                    service.authorize_epoch(ep)
                    result={"epoch":ep}
                else:raise ValueError("Replica never accepts gameplay or authority")
                send(conn,{"message_id":mid,"ok":True,"result":result})
            except Exception as exc:send(conn,{"message_id":mid,"ok":False,"error":type(exc).__name__})
    except (EOFError,BrokenPipeError,OSError):pass
    except Exception as exc:
        try:send(conn,{"ready":False,"error":type(exc).__name__})
        except OSError:pass
    finally:
        if service:service.close()
        if runtime:runtime.close()
        conn.close()

class NodeProcess(BaseProcess):
    def __init__(self,config):
        context=mp.get_context("spawn");self.conn,child=context.Pipe();self.closed=False
        self.process=context.Process(target=serve,args=(child,config),name="region-r1b-"+config["node_id"])
        try:
            self.process.start();child.close()
            if not self.conn.poll(15):raise TimeoutError("Startup timeout")
            ready=receive(self.conn)
            if not ready.get("ready"):raise ValueError("Node startup failed: "+ready.get("error","unknown"))
            self.pid=ready["pid"]
        except BaseException:child.close();self.kill();raise

class Cluster:
    def __init__(self,directory,*,enable_lab=False,hz=20,max_lead=24,max_ticks=2000,replica_settings=None):
        if enable_lab is not True:raise ValueError("Lab disabled; explicit opt-in required")
        self.root=Path(directory)
        if not self.root.is_dir():raise ValueError("Existing dedicated lab directory required")
        self.nodes={};self.sockets={};self.configs={};self.key=secrets.token_bytes(32)
        try:
            peers={}
            for node in ("B","C"):
                sock=listener();self.sockets[node]=sock;key=secrets.token_bytes(32);peers[node]=(sock.getsockname()[1],key)
                config={"enable_lab":True,"node_id":node,"path":str(self.root/("r1b-"+node+"-lab.sqlite3")),"listener":sock,"peer_key":key,"settings":(replica_settings or {}).get(node,{})}
                self.configs[node]=config;self.nodes[node]=NodeProcess(config)
            config={"enable_lab":True,"node_id":"A","path":str(self.root/"r1a-lab.sqlite3"),"authority_key":self.key,"peers":peers,"settings":{"hz":hz,"max_lead":max_lead,"max_ticks":max_ticks}}
            self.configs["A"]=config;self.nodes["A"]=NodeProcess(config)
        except BaseException:self.close();raise
    def activate(self,epoch=1,ttl=60):return self.nodes["A"].call("activate",signed(self.key,{"node_id":"A","region_id":"lab-region","epoch":epoch,"ttl":ttl}))
    def restart(self,node):
        self.nodes[node].kill();self.nodes[node]=NodeProcess(self.configs[node]);return self.nodes[node]
    def close(self):
        for node in ("A","B","C"):
            if node in self.nodes:self.nodes[node].close()
        for sock in self.sockets.values():sock.close()
