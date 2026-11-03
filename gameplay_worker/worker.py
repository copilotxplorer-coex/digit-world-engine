"""Worker receives sanitized snapshot only; no World imports or DB credentials."""
from coordination.worker import Worker
from .task import solve,KIND,PROTOCOL

class GameplayWorker(Worker):
    def run_once(self):
        self.call('register',{'protocol':PROTOCOL})
        job=self.call('claim',{})['job']
        if job is None:return {'worked':False,'reward_enabled':False}
        if job['kind']!=KIND:raise ValueError('Unsupported gameplay task')
        result=solve(job['payload']['snapshot'])
        self.call('heartbeat',{'job_id':job['id'],'lease_token':job['lease_token']})
        verified=self.call('submit',{'job_id':job['id'],'lease_token':job['lease_token'],'result':result})
        return {'worked':True,'job_id':job['id'],'verified':verified['accepted'],'match_count':len(result['matches']),'reward_enabled':False,'soul_digit_awarded':0}
