"""Opt-in proximity queue using the existing dedicated coordinator DB schema."""
import hmac
import math
import json
import secrets
from coordination.store import Coordinator,LeaseError
from .task import validate,solve,fingerprint,canonical,validate_result,number,KIND,PROTOCOL,MAX_AGE

class GameplayCoordinator(Coordinator):
    def register(self,node_id,credential,protocol):
        if type(protocol) is not int or protocol!=PROTOCOL:raise ValueError('Gameplay worker protocol2 required')
        result=super().register(node_id,credential,1);result['protocol']=PROTOCOL
        return result

    def enqueue(self,payload):raise ValueError('Use enqueue_snapshot for gameplay queue')

    def enqueue_snapshot(self,snapshot):
        validate(snapshot);job_id=secrets.token_hex(16)
        with self.transaction():
            if self.db.execute("SELECT COUNT(*) FROM coord_jobs WHERE state IN ('queued','leased')").fetchone()[0]>=128:raise ValueError('Gameplay queue full')
            payload={'snapshot':snapshot,'issued_at':self.now()}
            self.db.execute('INSERT INTO coord_jobs(id,kind,payload,state) VALUES(?,?,?,?)',(job_id,KIND,canonical(payload).decode(),'queued'))
        return job_id

    def claim(self,node_id,credential):
        # Prevent a mixed M1 queue from accidentally reaching a gameplay worker.
        with self.lock:
            if self.db.execute('SELECT 1 FROM coord_jobs WHERE kind!=? LIMIT 1',(KIND,)).fetchone():raise ValueError('Use a separate gameplay coordinator DB, not an M1 queue')
            with self.transaction():
                self.auth(node_id,credential)
                now=self.now()
                for row in self.db.execute("SELECT id,payload FROM coord_jobs WHERE state IN ('queued','leased')").fetchall():
                    age=now-json.loads(row['payload'])['issued_at']
                    if not 0<=age<=MAX_AGE:self.db.execute("UPDATE coord_jobs SET state='failed',owner=NULL,lease_hash=NULL,expires=NULL WHERE id=?",(row['id'],))
            return super().claim(node_id,credential)

    def submit(self,*args,**kwargs):raise ValueError('Gameplay queue requires structured submit_result')

    def submit_result(self,node_id,credential,job_id,lease_token,result):
        from coordination.contract import identifier
        identifier(job_id);validate_result(result)
        if not isinstance(lease_token,str) or not 32<=len(lease_token)<=128:raise LeaseError('Invalid lease')
        digest=fingerprint(result)
        with self.transaction():
            self.auth(node_id,credential);now=self.now()
            old=self.db.execute('SELECT * FROM coord_results WHERE job_id=?',(job_id,)).fetchone()
            if old:
                if old['node_id']==node_id and hmac.compare_digest(old['lease_hash'],self.hashed(lease_token)) and hmac.compare_digest(old['digest'],digest):
                    return {'accepted':True,'duplicate':True,'reward_enabled':False,'soul_digit_awarded':0}
                raise LeaseError('Conflicting result receipt')
            row=self.db.execute('SELECT * FROM coord_jobs WHERE id=?',(job_id,)).fetchone()
            self.check_lease(row,node_id,lease_token,now)
            if row['kind']!=KIND:raise ValueError('Wrong task kind')
            payload=json.loads(row['payload']);age=now-payload['issued_at']
            if not 0<=age<=MAX_AGE:raise ValueError('Snapshot too old for verification')
            expected=reference(payload['snapshot'])
            if not hmac.compare_digest(fingerprint(expected),digest):raise ValueError('Gameplay result verification failed')
            self.db.execute('INSERT INTO coord_results VALUES(?,?,?,?,?)',(job_id,node_id,self.hashed(lease_token),digest,now))
            self.db.execute("UPDATE coord_jobs SET state='verified' WHERE id=?",(job_id,))
            self.db.execute('UPDATE coord_nodes SET last_seen=? WHERE id=?',(now,node_id))
        return {'accepted':True,'duplicate':False,'reward_enabled':False,'soul_digit_awarded':0}

    def consume(self,job_id,current_snapshot):
        """Trusted Central-only read. Revalidates freshness and full snapshot identity."""
        validate(current_snapshot)
        with self.lock:
            row=self.db.execute("SELECT j.payload,r.digest FROM coord_jobs j JOIN coord_results r ON r.job_id=j.id WHERE j.id=? AND j.state='verified' AND j.kind=?",(job_id,KIND)).fetchone()
            if row is None:raise ValueError('No verified gameplay result')
            payload=json.loads(row['payload']);age=self.now()-payload['issued_at']
            if not 0<=age<=MAX_AGE:raise ValueError('Snapshot result expired')
            if fingerprint(payload['snapshot'])!=fingerprint(current_snapshot):raise ValueError('World snapshot changed; discard stale result')
            result=reference(payload['snapshot'])
            if fingerprint(result)!=row['digest']:raise ValueError('Stored receipt mismatch')
            return result

    def stats(self):
        result=super().stats();result.update(protocol=PROTOCOL,task_scope='read-only-proximity-snapshot',gameplay_mutations=False)
        return result


def reference(snapshot):
    """Central independent membership check, including omitted/extra entities."""
    validate(snapshot);observer=snapshot['observer'];matches=[]
    for entity in snapshot['entities']:
        if entity['kind']=='player' and entity['id']==observer['id']:continue
        dx=entity['x']-observer['x'];dy=entity['y']-observer['y']
        if dx*dx+dy*dy<=snapshot['radius']*snapshot['radius']:
            matches.append({'id':entity['id'],'kind':entity['kind'],'distance2':dx*dx+dy*dy})
    matches.sort(key=lambda row:(row['distance2'],row['kind'],row['id']))
    return {'snapshot_hash':fingerprint(snapshot),'matches':matches}
