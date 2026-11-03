"""Loopback direct-peer chat federation, not a shared simulation or DB replica."""
from collections import deque
import hashlib
import hmac
import json
import math
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.request
from urllib.parse import urlsplit


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def node_name(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}',value):
        raise ValueError('Invalid node_id')
    return value


class Federation:
    def __init__(self,path=None):
        self.node_id='standalone';self.secret=None;self.peers={}
        self.lock=threading.RLock();self.nonces={};self.events=deque(maxlen=500)
        self.sequence=0;self.event_ids=deque(maxlen=1000);self.last_chat={}
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if path is None:return
        data=json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data,dict) or set(data)!={'node_id','shared_secret','peers'}:
            raise ValueError('Federation config requires node_id/shared_secret/peers')
        self.node_id=node_name(data['node_id'])
        secret=data['shared_secret']
        if not isinstance(secret,str) or len(secret)<32 or secret.startswith('REPLACE_'):
            raise ValueError('Use a random federation shared_secret of at least 32 characters')
        self.secret=secret.encode()
        if not isinstance(data['peers'],list) or len(data['peers'])>8:raise ValueError('At most 8 peers allowed')
        for peer in data['peers']:
            if not isinstance(peer,dict) or set(peer)!={'node_id','url'}:raise ValueError('Invalid peer')
            name=node_name(peer['node_id']);url=peer['url']
            if not isinstance(url,str):raise ValueError('Invalid peer URL')
            u=urlsplit(url)
            if u.scheme!='http' or u.hostname!='127.0.0.1' or u.username or u.password or u.path not in ('','/') or u.query or u.fragment or not u.port or not 1024<=u.port<=65535:
                raise ValueError('Federation v0.3.5 accepts loopback HTTP URLs only')
            if name==self.node_id or name in self.peers:raise ValueError('Duplicate/self peer')
            self.peers[name]=url.rstrip('/')

    @property
    def enabled(self):return self.secret is not None

    def summary(self):
        return {'enabled':self.enabled,'node_id':self.node_id,'peer_count':len(self.peers),
                'scope':'direct-peer-chat-only','persistent_events':False}

    def signed(self,path,payload):
        if not self.enabled:raise ValueError('Federation is not configured')
        raw=canonical({'sender':self.node_id,'timestamp':time.time(),'nonce':secrets.token_hex(16),'payload':payload})
        sig=hmac.new(self.secret,path.encode()+b'\n'+raw,hashlib.sha256).hexdigest()
        return raw,sig

    def authenticate(self,path,raw,signature):
        if not self.enabled or not isinstance(signature,str):raise ValueError('Peer authentication failed')
        expected=hmac.new(self.secret,path.encode()+b'\n'+raw,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,signature):raise ValueError('Peer authentication failed')
        envelope=json.loads(raw)
        if not isinstance(envelope,dict) or set(envelope)!={'sender','timestamp','nonce','payload'}:raise ValueError('Invalid envelope')
        sender=envelope['sender'];stamp=envelope['timestamp'];nonce=envelope['nonce']
        if not isinstance(sender,str) or sender not in self.peers:raise ValueError('Unknown peer')
        if isinstance(stamp,bool) or not isinstance(stamp,(float,int)) or not math.isfinite(stamp) or abs(time.time()-stamp)>30:raise ValueError('Peer request expired')
        if not isinstance(nonce,str) or not re.fullmatch('[a-f0-9]{32}',nonce):raise ValueError('Invalid nonce')
        with self.lock:
            now=time.monotonic();self.nonces={k:v for k,v in self.nonces.items() if now-v<65}
            key=(sender,nonce)
            if key in self.nonces:raise ValueError('Peer replay rejected')
            if len(self.nonces)>=4096:raise ValueError('Peer request limit')
            self.nonces[key]=now
        return sender,envelope['payload']

    def post(self,name,path,payload):
        raw,sig=self.signed(path,payload)
        req=urllib.request.Request(self.peers[name]+path,raw,{'Content-Type':'application/json','X-Digit-Signature':sig})
        with self.opener.open(req,timeout=.75) as response:
            data=json.loads(response.read(65536))
        if not isinstance(data,dict) or data.get('node_id')!=name or not data.get('ok'):raise ValueError('Unexpected peer response')
        return data

    def status(self):
        peers=[]
        for name in self.peers:
            try:
                result=self.post(name,'/federation/ping',{})
                peers.append({'node_id':name,'online':True,'version':result.get('version')})
            except (OSError,ValueError):peers.append({'node_id':name,'online':False})
        return dict(self.summary(),peers=peers)

    def accept(self,sender,event):
        if not isinstance(event,dict) or set(event)!={'id','origin','sender_id','sender_name','text'}:raise ValueError('Invalid world event')
        if event['origin']!=sender:raise ValueError('Event origin mismatch')
        for key,limit in (('id',96),('sender_id',64),('sender_name',32),('text',280)):
            value=event[key]
            if not isinstance(value,str) or not 1<=len(value)<=limit or any(ord(c)<32 for c in value):raise ValueError('Invalid event field')
        with self.lock:
            if event['id'] in self.event_ids:return False
            self.event_ids.append(event['id']);self.sequence+=1
            self.events.append(dict(event,sequence=self.sequence))
        return True

    def broadcast(self,player,text):
        if not self.enabled:raise ValueError('Federation is not configured')
        if not isinstance(text,str):raise ValueError('Text required')
        event={'id':self.node_id+'-'+secrets.token_hex(16),'origin':self.node_id,
               'sender_id':player['id'],'sender_name':player['name'],'text':text.strip()}
        with self.lock:
            now=time.monotonic();pid=player['id']
            self.last_chat={k:v for k,v in self.last_chat.items() if now-v<60}
            if now-self.last_chat.get(pid,-100)<2:raise ValueError('World chat cooldown: 2 seconds')
            self.accept(self.node_id,event);self.last_chat[pid]=now
        delivered=[];failed=[]
        for name in self.peers:
            try:self.post(name,'/federation/event',event);delivered.append(name)
            except (OSError,ValueError):failed.append(name)
        return {'event_id':event['id'],'delivered':delivered,'failed':failed,'retry_queue':False}

    def after(self,cursor=0):
        if isinstance(cursor,bool) or not isinstance(cursor,int) or cursor<0:raise ValueError('Invalid event cursor')
        with self.lock:
            oldest=self.events[0]['sequence'] if self.events else self.sequence+1
            return {'cursor':self.sequence,'gap':cursor<oldest-1 or cursor>self.sequence,
                    'events':[e.copy() for e in self.events if e['sequence']>cursor]}
