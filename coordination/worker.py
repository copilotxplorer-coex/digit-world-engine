"""Bounded one-job worker; no shell execution, gameplay writes or reward claims."""
import json
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from .contract import solve,KIND,PROTOCOL,identifier

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class Worker:
    def __init__(self,url,node_id,credential):
        u=urlsplit(url)
        if u.scheme!='http' or u.hostname!='127.0.0.1' or u.username or u.password or u.path not in ('','/') or u.query or u.fragment or not u.port:raise ValueError('Loopback coordinator URL required')
        self.url=url.rstrip('/');self.node_id=identifier(node_id)
        if not isinstance(credential,str) or not 32<=len(credential)<=128:raise ValueError('Invalid credential')
        self.credential=credential
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def call(self,action,data):
        req=urllib.request.Request(self.url+'/v1/node/'+action,json.dumps(data,allow_nan=False).encode(),{'Content-Type':'application/json','Authorization':'Bearer '+self.credential,'X-Digit-Node':self.node_id})
        try:
            with self.opener.open(req,timeout=5) as response:
                raw=response.read(65537)
                if len(raw)>65536:raise ValueError('Coordinator response too large')
                result=json.loads(raw)
        except (OSError,ValueError):raise ValueError('Coordinator request failed; no automatic retry; lease may expire or result may already be committed') from None
        if not isinstance(result,dict) or result.get('ok') is not True:raise ValueError('Invalid coordinator response')
        return result['result']
    def run_once(self):
        self.call('register',{'protocol':PROTOCOL})
        claim=self.call('claim',{});job=claim['job']
        if job is None:return {'worked':False,'reward_enabled':False}
        if job['kind']!=KIND:raise ValueError('Unsupported task kind; no arbitrary code execution')
        result=solve(job['payload'])
        self.call('heartbeat',{'job_id':job['id'],'lease_token':job['lease_token']})
        verified=self.call('submit',{'job_id':job['id'],'lease_token':job['lease_token'],'digest':result})
        return {'worked':True,'job_id':job['id'],'verified':verified['accepted'],'reward_enabled':False,'soul_digit_awarded':0}
