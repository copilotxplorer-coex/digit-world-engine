"""Actual World read integration; disabled by default; original mutation paths intact."""
import math
from digit_world.core import World
from .bridge import capture

class ReadWorld(World):
    def __init__(self,*args,enable_read_worker=False,**kwargs):
        if type(enable_read_worker) is not bool:raise ValueError('Feature flag must be boolean')
        self.read_service=None;self.read_worker_enabled=enable_read_worker
        super().__init__(*args,**kwargs)
        if enable_read_worker:
            from .read_service import ReadService
            try:self.read_service=ReadService()
            except Exception:self.read_service=None  # Central remains available.

    def close(self):
        try:
            if self.read_service:self.read_service.close()
        finally:super().close()

    def _candidates(self,p):
        if not self.read_worker_enabled or self.read_service is None:return None
        try:
            # Called only after World.execute has authenticated and locked the World.
            snapshot=capture(self,p['id'])
            ids=self.read_service.candidates(snapshot)
            if ids is None:return None
            # Preserve legacy hypot boundary semantics, even for floating-point edges.
            o=snapshot['observer']
            expected={(e['kind'],e['id']) for e in snapshot['entities'] if not (e['kind']=='player' and e['id']==o['id']) and math.hypot(e['x']-o['x'],e['y']-o['y'])<=30}
            return ids if ids==expected else None
        except Exception:return None

    def _act(self,p,action,a):
        if action=='look':
            ids=self._candidates(p)
            if ids is not None:
                players=[]
                for r in self.db.execute('SELECT p.* FROM players p JOIN sessions s ON p.id=s.owner WHERE s.online=1'):
                    if (r['id']==p['id'] or ('player',r['id']) in ids) and self._distance(p,r)<=30:
                        players.append({k:r[k] for k in ('id','name','x','y')} | {'form':self._vital(r['id'])['form']})
                return {'players':players,
                        'objects':[dict(r) for r in self.db.execute('SELECT * FROM objects') if ('object',r['id']) in ids and self._distance(p,r)<=30],
                        'drops':[dict(r) for r in self.db.execute('SELECT * FROM drops') if ('drop',r['id']) in ids and self._distance(p,r)<=30]}
        return super()._act(p,action,a)

    def _social_command(self,p,action,a):
        if action=='nearby':
            session=self.db.execute('SELECT * FROM sessions WHERE owner=?',(p['id'],)).fetchone()
            if not session['online']:raise ValueError('Login required')
            ids=self._candidates(p)
            if ids is not None:
                result={'origin':{'x':p['x'],'y':p['y']},'radius':30}
                players=self.db.execute('SELECT p.* FROM players p JOIN sessions s ON s.owner=p.id WHERE s.online=1').fetchall()
                for key,rows,kind in [('players',players,'player'),('objects',self.db.execute('SELECT * FROM objects').fetchall(),'object'),('drops',self.db.execute('SELECT * FROM drops').fetchall(),'drop')]:
                    # Existing nearby includes self: preserve that behavior.
                    result[key]=sorted([self._public_entity(p,r,kind) for r in rows if ((kind=='player' and r['id']==p['id']) or (kind,r['id']) in ids) and self._distance(p,r)<=30],key=lambda e:(e['distance'],e['id']))
                return result
        # inspect, say and all authorization/mutations keep the original implementation.
        return super()._social_command(p,action,a)
