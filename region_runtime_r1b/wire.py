"""Bounded authenticated JSON over loopback TCP only; experimental lab transport."""
import json
import math
import socket
import struct
import time
from region_runtime.contract import canonical,checked,signed,identifier,integer
from region_runtime_r1a.runtime import RULESET
MAX_FRAME=262144
MAX_BATCH_BYTES=131072
MAX_BATCH_TICKS=16

def number(value,low,high):
    if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:raise ValueError("Invalid lab setting")
    return float(value)

def digest(value):
    if not isinstance(value,str) or len(value)!=64 or any(c not in "0123456789abcdef" for c in value):raise ValueError("Invalid digest")
    return value

def exact(value,fields):
    if not isinstance(value,dict) or set(value)!=set(fields.split()):raise ValueError("Invalid message schema")

def packet(key,kind,sender,recipient,epoch,session,boot,request_id,payload):
    return signed(key,{"protocol_version":1,"ruleset":RULESET,"region_id":"lab-region","region_epoch":epoch,"sender":sender,"recipient":recipient,"replica_session_id":session,"replica_boot":boot,"message_id":request_id,"message_type":kind,"payload":payload})

def unpack(key,envelope,*,sender,recipient,epoch=None,session=None,boot=None):
    body=checked(key,envelope)
    exact(body,"protocol_version ruleset region_id region_epoch sender recipient replica_session_id replica_boot message_id message_type payload")
    if type(body["protocol_version"]) is not int or body["protocol_version"]!=1 or body["ruleset"]!=RULESET:raise ValueError("INCOMPATIBLE")
    if body["region_id"]!="lab-region" or body["sender"]!=sender or body["recipient"]!=recipient:raise ValueError("Wrong routing")
    integer(body["region_epoch"],1,10**9)
    for k in ("replica_session_id","replica_boot","message_id","message_type"):identifier(body[k])
    if epoch is not None and body["region_epoch"]!=epoch:raise ValueError("Stale epoch")
    if session is not None and body["replica_session_id"]!=session:raise ValueError("Stale session")
    if boot is not None and body["replica_boot"]!=boot:raise ValueError("Stale incarnation")
    return body

class Channel:
    def __init__(self,sock,timeout=.6):
        self.sock=sock;self.timeout=number(timeout,.05,5);sock.settimeout(self.timeout)
        if sock.family==socket.AF_INET:sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
    def _read(self,n,deadline):
        parts=[];remaining=n
        while remaining:
            left=deadline-time.monotonic()
            if left<=0:raise TimeoutError("Frame deadline")
            self.sock.settimeout(left);part=self.sock.recv(remaining)
            if not part:raise EOFError("Connection closed")
            parts.append(part);remaining-=len(part)
        return b"".join(parts)
    def receive(self):
        deadline=time.monotonic()+self.timeout
        length=struct.unpack("!I",self._read(4,deadline))[0]
        if not 1<=length<=MAX_FRAME:raise ValueError("Frame limit")
        raw=self._read(length,deadline)
        def invalid(value):raise ValueError("Nonfinite JSON")
        def pairs(items):
            result={}
            for k,v in items:
                if k in result:raise ValueError("Duplicate JSON key")
                result[k]=v
            return result
        return json.loads(raw,parse_constant=invalid,object_pairs_hook=pairs)
    def send(self,value):
        raw=canonical(value)
        if not 1<=len(raw)<=MAX_FRAME:raise ValueError("Frame limit")
        self.sock.settimeout(self.timeout);self.sock.sendall(struct.pack("!I",len(raw))+raw)
    def close(self):
        try:self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:pass
        self.sock.close()

def listener():
    sock=socket.socket();sock.bind(("127.0.0.1",0));sock.listen(2);sock.settimeout(.1);return sock
