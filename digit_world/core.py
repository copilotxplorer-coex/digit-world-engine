"""Trusted local development world. Server owns all state transitions."""
import hashlib
import json
import logging
import math
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from .config import WorldConfig
from .accounts import AccountMixin, ACTIONS
from .login_keys import authenticated
from .key_database import require_ready
from .energy import EnergyMixin
from .combat import CombatMixin
from .social import SocialMixin, clean_text

log = logging.getLogger('digit.world')
SCHEMA = 4
RECIPES = {
    'brick': {'cost': 2, 'seconds': 2, 'weight': 1},
    'training_blade': {'cost': 4, 'seconds': 2, 'weight': 3},
}
CAPACITY = 20
TICK = .1
HEARTBEAT_TIMEOUT = 30
LOGOUT_DELAY = 5
PEACE_DELAY = 5
LOCK_RANGE = 10
INTERACT_RANGE = 3
ATTACK_RANGE = 2
DAMAGE = 10
WEAPON_DURABILITY = 60
ESCAPE_RANGE = 12
ESCAPE_SECONDS = 3
PROTECTION_SECONDS = 5

def compute(job):
    value = job['challenge'].encode()
    for _ in range(job['iterations']):
        value = hashlib.sha256(value).digest()
    return value.hex()

class World(EnergyMixin, CombatMixin, SocialMixin, AccountMixin):
    def __init__(self, path='world.sqlite3', clock=time.time, config=None):
        require_ready(path)
        self.clock = clock
        self.config = config or WorldConfig()
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if version > SCHEMA:
            self.db.close()
            raise RuntimeError('Database is newer than this server; refusing downgrade')
        self.backup_path = None
        if tables and version < SCHEMA:
            if not {'players','objects','drops','jobs','receipts'}.issubset(tables):
                self.db.close()
                raise RuntimeError('Unrecognized legacy database; refusing migration')
            if path != ':memory:':
                backup = str(path) + '.pre-v033.' + time.strftime('%Y%m%d-%H%M%S') + '.' + secrets.token_hex(3) + '.bak'
                dest = sqlite3.connect(backup)
                try: self.db.backup(dest)
                finally: dest.close()
                self.backup_path = backup
                log.info('database_backup path=%s schema=%s', backup, version)
        self.db.execute('PRAGMA journal_mode=WAL')
        try:
            self.db.execute('BEGIN IMMEDIATE')
            statements = [
                "CREATE TABLE IF NOT EXISTS players(id TEXT PRIMARY KEY,name TEXT UNIQUE,token TEXT UNIQUE,x REAL DEFAULT 0,y REAL DEFAULT 0,soul INTEGER DEFAULT 0,bag TEXT DEFAULT '{}')",
                "CREATE TABLE IF NOT EXISTS objects(id TEXT PRIMARY KEY,kind TEXT,x REAL,y REAL,progress INTEGER DEFAULT 0)",
                "CREATE TABLE IF NOT EXISTS drops(id TEXT PRIMARY KEY,item TEXT,qty INTEGER,x REAL,y REAL)",
                "CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,owner TEXT,kind TEXT,payload TEXT,ready REAL,done INTEGER DEFAULT 0)",
                "CREATE TABLE IF NOT EXISTS receipts(owner TEXT,key TEXT,response TEXT,PRIMARY KEY(owner,key))",
                "CREATE TABLE IF NOT EXISTS sessions(owner TEXT PRIMARY KEY,online INTEGER DEFAULT 0,last_seen REAL DEFAULT 0,logout_at REAL,logout_requested INTEGER DEFAULT 0,last_hit REAL DEFAULT 0)",
                "CREATE TABLE IF NOT EXISTS actors(owner TEXT PRIMARY KEY,hp REAL DEFAULT 100,stamina REAL DEFAULT 100,facing REAL DEFAULT 0,state TEXT DEFAULT 'idle',until_at REAL DEFAULT 0,active_at REAL DEFAULT 0,resolved INTEGER DEFAULT 0,equipped TEXT,flee INTEGER DEFAULT 0,protect_until REAL DEFAULT 0,move_after REAL DEFAULT 0)",
                "CREATE TABLE IF NOT EXISTS weapons(id TEXT PRIMARY KEY,owner TEXT,kind TEXT,durability INTEGER,seq INTEGER)",
                "CREATE TABLE IF NOT EXISTS combats(id TEXT PRIMARY KEY,a TEXT,b TEXT,started REAL,escape_since REAL,done INTEGER DEFAULT 0,outcome TEXT,winner TEXT,loser TEXT)",
                "CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,owner TEXT,message TEXT,created REAL)",
                "CREATE TABLE IF NOT EXISTS receipt_v2(owner TEXT,key TEXT,fingerprint TEXT,response TEXT,PRIMARY KEY(owner,key))",
                "CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT)",
            ]
            for sql in statements: self.db.execute(sql)
            self.db.execute("CREATE TABLE IF NOT EXISTS vitals(owner TEXT PRIMARY KEY,energy REAL,form TEXT DEFAULT 'normal',updated_at REAL,charge_ready REAL DEFAULT 0,auto_enabled INTEGER DEFAULT 0,auto_threshold REAL DEFAULT 25,auto_amount INTEGER DEFAULT 1,auto_reserve INTEGER DEFAULT 0)")
            for table, additions in {
                'actors': [('retreat_reason','TEXT'),('decision_seq','INTEGER DEFAULT 0')],
                'events': [('kind',"TEXT DEFAULT 'message'"),('data',"TEXT DEFAULT '{}'" )],
                'combats': [('detail',"TEXT DEFAULT '{}'" )],
            }.items():
                existing={r[1] for r in self.db.execute('PRAGMA table_info('+table+')')}
                for name, definition in additions:
                    if name not in existing: self.db.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+definition)
            for p in self.db.execute('SELECT id FROM players').fetchall(): self._ensure_actor(p['id'])
            self.db.execute("DELETE FROM metadata WHERE key LIKE 'handoff:%'")
            self.db.execute('UPDATE sessions SET online=0,logout_at=NULL,logout_requested=0')
            self.db.execute("UPDATE jobs SET done=1 WHERE kind='craft' AND done=0")
            self.db.execute("UPDATE actors SET state=CASE WHEN owner IN (SELECT owner FROM vitals WHERE form='fireball') THEN 'fireball' ELSE 'idle' END,until_at=0,active_at=0,resolved=0,flee=0,retreat_reason=NULL")
            # No offline combat simulation after a server restart.
            self.db.execute("UPDATE combats SET done=1,outcome='server_restart' WHERE done=0")
            self._account_tables()
            self.db.execute('PRAGMA user_version=4')
            self.db.commit()
        except Exception:
            self.db.rollback(); self.db.close(); raise
        self.last_tick = self.clock()
        log.info('world_ready schema=4 migrated_from=%s', version)

    def _ensure_actor(self, pid):
        self.db.execute('INSERT OR IGNORE INTO sessions(owner) VALUES(?)', (pid,))
        self.db.execute('INSERT OR IGNORE INTO actors(owner) VALUES(?)', (pid,))
        self._ensure_energy(pid)

    def close(self):
        with self.lock: self.db.close()

    def _event(self, pid, message):
        self.db.execute('INSERT INTO events(owner,message,created) VALUES(?,?,?)', (pid,message,self.clock()))

    def _p(self, pid): return self.db.execute('SELECT * FROM players WHERE id=?',(pid,)).fetchone()
    def _actor(self, pid): return self.db.execute('SELECT * FROM actors WHERE owner=?',(pid,)).fetchone()
    def _combat(self, pid):
        return self.db.execute('SELECT * FROM combats WHERE done=0 AND (a=? OR b=?)',(pid,pid)).fetchone()
    def _weight(self, bag): return sum(RECIPES.get(k, {'weight':1})['weight'] * v for k,v in bag.items())
    def _bag(self, pid, bag): self.db.execute('UPDATE players SET bag=? WHERE id=?',(json.dumps(bag),pid))
    def _distance(self, a, b): return math.hypot(a['x']-b['x'],a['y']-b['y'])
    def _near(self, p, obj):
        if not obj or self._distance(p,obj)>INTERACT_RANGE: raise ValueError('Missing object or outside interaction range')
    def _cancel_craft(self, pid):
        n=self.db.execute("UPDATE jobs SET done=1 WHERE owner=? AND kind='craft' AND done=0",(pid,)).rowcount
        if n: self._event(pid,'Craft cancelled; no cost charged')
    def execute(self, token, action, args=None, request_id=None):
        if not isinstance(action,str) or (args is not None and not isinstance(args,dict)): raise ValueError('Invalid command')
        args=args or {}
        if request_id is not None and (not isinstance(request_id,str) or not 1<=len(request_id)<=128): raise ValueError('Invalid request ID')
        with self.lock:
            try:
                self._auth_limit(action,args,token)
                self.db.execute('BEGIN IMMEDIATE')
                if action in ACTIONS:
                    self._tick_energy(self.clock())
                    result=self._account_action(token,action,args)
                elif action in ('register','login','account_login','account_register','account_claim','account_status','password_forgot','password_reset','password_change','email_set','email_verify','key_prepare','handoff','handoff_claim','handoff_cancel'):
                    raise ValueError('Legacy authentication removed; use Login Key')
                else:
                    p=authenticated(self,token)
                    fingerprint=hashlib.sha256(json.dumps([action,args],sort_keys=True,allow_nan=False).encode()).hexdigest()
                    if request_id:
                        old=self.db.execute('SELECT * FROM receipt_v2 WHERE owner=? AND key=?',(p['id'],request_id)).fetchone()
                        if old:
                            if old['fingerprint']!=fingerprint: raise ValueError('Request ID reused with different payload')
                            self.db.commit(); return json.loads(old['response'])
                    self._tick_energy(self.clock())
                    p=self._p(p['id'])
                    result=self._act(p,action,args)
                    if request_id and action not in ('heartbeat','events','look','me','jobs','nearby','inspect','handoff','handoff_cancel'):
                        self.db.execute('INSERT INTO receipt_v2 VALUES(?,?,?,?)',(p['id'],request_id,fingerprint,json.dumps(result)))
                self.db.commit(); return result
            except sqlite3.IntegrityError:
                self.db.rollback(); raise ValueError('Name or request already exists') from None
            except Exception:
                self.db.rollback(); raise

    def _act(self, p, action, a):
        pid=p['id']; now=self.clock(); actor=self._actor(pid)
        if action=='events':
            cursor=int(a.get('after',0))
            rows=self.db.execute('SELECT seq,message,created,kind,data FROM events WHERE owner=? AND seq>? ORDER BY seq LIMIT 100',(pid,cursor)).fetchall()
            return {'events':[dict(r, data=json.loads(r['data'] or '{}')) for r in rows]}
        if action=='me':
            bag=json.loads(p['bag']); s=self.db.execute('SELECT * FROM sessions WHERE owner=?',(pid,)).fetchone()
            return {k:p[k] for k in ('id','name','x','y','soul')} | {'bag':bag,'weight':self._weight(bag),'capacity':CAPACITY,'online':bool(s['online']),'logout_at':s['logout_at'],'hp':actor['hp'],'stamina':round(actor['stamina'],2),'state':actor['state'],'equipped':actor['equipped'],'weapons':[dict(r) for r in self.db.execute('SELECT id,kind,durability,seq FROM weapons WHERE owner=? ORDER BY seq,id',(pid,))],'combat':dict(self._combat(pid)) if self._combat(pid) else None,**self._energy_status(pid),'last_combat':self._last_combat(pid)}
        if action in ('say','nearby','inspect'):return self._social_command(p,action,a)
        if action=='look':
            players=[{k:r[k] for k in ('id','name','x','y')} | {'form':self._vital(r['id'])['form']} for r in self.db.execute('SELECT p.* FROM players p JOIN sessions s ON p.id=s.owner WHERE s.online=1') if self._distance(p,r)<=30]
            return {'players':players,'objects':[dict(r) for r in self.db.execute('SELECT * FROM objects') if self._distance(p,r)<=30],'drops':[dict(r) for r in self.db.execute('SELECT * FROM drops') if self._distance(p,r)<=30]}
        s=self.db.execute('SELECT * FROM sessions WHERE owner=?',(pid,)).fetchone()
        if not s['online']: raise ValueError('Login required')
        if action=='heartbeat':
            self.db.execute('UPDATE sessions SET last_seen=? WHERE owner=?',(now,pid)); return {'online':True}
        if action=='logout':
            self._cancel_craft(pid)
            self.db.execute('UPDATE sessions SET logout_requested=1,logout_at=? WHERE owner=?',(now+LOGOUT_DELAY,pid))
            self._event(pid,'Logout countdown started (5 seconds)')
            return {'logout_at':now+LOGOUT_DELAY}
        if action=='resume':
            self.db.execute('UPDATE sessions SET logout_requested=0,logout_at=NULL,last_seen=? WHERE owner=?',(now,pid)); return {'logout_cancelled':True}
        if s['logout_requested']: raise ValueError('Logout pending; use resume to cancel')
        combat=self._combat(pid)
        if action in ('charge','aid','autocharge'): return self._energy_command(p,action,a)
        if self._vital(pid)['form']=='fireball' and action not in ('jobs','claim','cancel','node_job','node_submit'):
            raise ValueError('Fireball cannot move, fight, work or pick up items; use charge or receive aid')
        if action=='flee':
            if not combat: raise ValueError('Not in combat')
            self.db.execute("UPDATE actors SET flee=1,retreat_reason='player_requested' WHERE owner=?",(pid,))
            self._telemetry([pid],'retreat_requested',{'player':pid,'reason':'player_requested'},combat['id'])
            return {'flee':True}
        if action=='surrender':
            if not combat: raise ValueError('Not in combat')
            other=combat['b'] if combat['a']==pid else combat['a']
            self._finish(combat,'surrender',other,pid); return {'outcome':'surrender'}
        if action=='lock':
            target=self._p(str(a['player_id']))
            if not target or target['id']==pid: raise ValueError('Invalid target')
            if self._vital(target['id'])['form']=='fireball': raise ValueError('Fireball cannot be targeted')
            ts=self.db.execute('SELECT * FROM sessions WHERE owner=?',(target['id'],)).fetchone()
            if not ts['online'] or ts['logout_requested']: raise ValueError('Target is offline or logging out')
            if combat or self._combat(target['id']): raise ValueError('One participant is already in combat')
            if self._actor(target['id'])['protect_until']>now: raise ValueError('Target has temporary protection')
            if self._distance(p,target)>LOCK_RANGE: raise ValueError('Outside lock range')
            cid=secrets.token_hex(8)
            self.db.execute('INSERT INTO combats(id,a,b,started) VALUES(?,?,?,?)',(cid,pid,target['id'],now))
            for who in (pid,target['id']):
                self._cancel_craft(who)
                self.db.execute("UPDATE actors SET state='idle',until_at=0,flee=0,retreat_reason=NULL,decision_seq=0,protect_until=0 WHERE owner=?",(who,))
            self._telemetry([pid,target['id']],'combat_start',{'initiator':pid,'defender':target['id'],'mode':'auto'},cid)
            log.info('combat_start combat=%s initiator=%s defender=%s',cid,pid,target['id'])
            return {'combat_id':cid,'initiator':pid,'defender':target['id']}
        if action=='equip':
            if actor['state']!='idle': raise ValueError('Wait for current action to finish')
            w=self.db.execute('SELECT * FROM weapons WHERE id=? AND owner=? AND durability>0',(a['weapon_id'],pid)).fetchone()
            if not w: raise ValueError('Usable carried weapon required')
            self.db.execute('UPDATE actors SET equipped=? WHERE owner=?',(w['id'],pid))
            self._telemetry([pid],'equipment_changed',{'player':pid,'previous':actor['equipped'],'equipped':w['id'],'reason':'player_requested','durability':w['durability']})
            return {'equipped':w['id']}
        if action=='move':
            if combat: raise ValueError('CLI combat movement is automatic; use flee or surrender')
            if now<actor['move_after']: raise ValueError('Movement cooldown (0.2 seconds)')
            dx,dy=float(a['dx']),float(a['dy'])
            if not math.isfinite(dx) or not math.isfinite(dy) or math.hypot(dx,dy)>5: raise ValueError('Finite move <=5 units required')
            self.db.execute('UPDATE players SET x=x+?,y=y+? WHERE id=?',(dx,dy,pid))
            self.db.execute("UPDATE actors SET move_after=?,state='moving',until_at=? WHERE owner=?",(now+.2,now+.2,pid))
            return {'x':p['x']+dx,'y':p['y']+dy}
        if action=='jobs':
            return {'jobs':[dict(r) for r in self.db.execute('SELECT id,kind,payload,ready FROM jobs WHERE owner=? AND done=0',(pid,))]}
        if action=='claim':
            j=self.db.execute("SELECT * FROM jobs WHERE id=? AND owner=? AND kind='craft'",(a['job_id'],pid)).fetchone()
            if not j: raise ValueError('Craft not found')
            return {'automatic':True,'completed_or_cancelled':bool(j['done']),'message':'No claim needed; server completes crafting automatically'}
        if action=='cancel':
            n=self.db.execute("UPDATE jobs SET done=1 WHERE id=? AND owner=? AND kind='craft' AND done=0",(a['job_id'],pid)).rowcount
            if not n: raise ValueError('Active craft not found')
            return {'cancelled':a['job_id']}
        if action in ('craft','place','build','dismantle','node_job','node_submit') and combat:
            raise ValueError('Work is disabled during Combat')
        bag=json.loads(p['bag'])
        if action=='node_job':
            if self.db.execute("SELECT 1 FROM jobs WHERE owner=? AND kind='compute' AND done=0",(pid,)).fetchone(): raise ValueError('Finish outstanding compute job first')
            jid=secrets.token_hex(8); payload={'challenge':secrets.token_hex(24),'iterations':10000}
            self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,0)',(jid,pid,'compute',json.dumps(payload),now)); return {'job_id':jid,**payload}
        if action=='node_submit':
            j=self.db.execute("SELECT * FROM jobs WHERE id=? AND owner=? AND kind='compute' AND done=0",(a['job_id'],pid)).fetchone()
            if not j: raise ValueError('Job missing or already claimed')
            if not secrets.compare_digest(compute(json.loads(j['payload'])),str(a['result'])): raise ValueError('Invalid compute result')
            self.db.execute('UPDATE jobs SET done=1 WHERE id=?',(j['id'],))
            self.db.execute('UPDATE players SET soul=soul+10 WHERE id=?',(pid,))
            log.info('demo_reward player=%s job=%s amount=10',pid,j['id']); return {'reward':10,'job_id':j['id']}
        if action=='craft':
            item=str(a.get('item','brick'))
            if item not in RECIPES: raise ValueError('Unknown recipe')
            if self.db.execute("SELECT 1 FROM jobs WHERE owner=? AND kind='craft' AND done=0",(pid,)).fetchone(): raise ValueError('One active craft allowed')
            recipe=RECIPES[item]
            if p['soul']<recipe['cost']: raise ValueError('Insufficient Soul Digit')
            jid=secrets.token_hex(8)
            self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,0)',(jid,pid,'craft',json.dumps({'item':item}),now+recipe['seconds']))
            log.info('craft_start player=%s job=%s item=%s',pid,jid,item)
            return {'job_id':jid,'seconds':recipe['seconds'],'automatic':True}
        if action=='place':
            if a.get('kind','wall')!='wall': raise ValueError('Only wall supported')
            if self.db.execute('SELECT COUNT(*) FROM objects').fetchone()[0]>=1000: raise ValueError('Prototype object limit')
            oid=secrets.token_hex(8); self.db.execute("INSERT INTO objects VALUES(?,'wall',?,?,0)",(oid,p['x'],p['y']))
            return {'object_id':oid,'required_bricks':4}
        if action in ('build','dismantle'):
            o=self.db.execute('SELECT * FROM objects WHERE id=?',(a['object_id'],)).fetchone(); self._near(p,o)
            marker='work:'+pid; last=self.db.execute('SELECT ready FROM jobs WHERE id=?',(marker,)).fetchone()
            if last and now<last['ready']: raise ValueError('Work step cooldown')
            if action=='build':
                if o['progress']>=4: raise ValueError('Already complete')
                if bag.get('brick',0)<1: raise ValueError('One brick required')
                bag['brick']-=1; self._bag(pid,bag); progress=o['progress']+1
            else:
                if o['progress']<=0: raise ValueError('No material to dismantle')
                progress=o['progress']-1
                if o['progress']%2==0: self.db.execute('INSERT INTO drops VALUES(?,?,?,?,?)',(secrets.token_hex(8),'brick',1,o['x'],o['y']))
            self.db.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,'work','{}',?,1)",(marker,pid,now+1))
            if action=='dismantle' and progress==0: self.db.execute('DELETE FROM objects WHERE id=?',(o['id'],))
            else: self.db.execute('UPDATE objects SET progress=? WHERE id=?',(progress,o['id']))
            return {'progress':progress,'usable':progress==4}
        if action in ('drop','pickup'):
            if combat: raise ValueError('Inventory ground transfers disabled during prototype Combat')
            if action=='drop':
                item=str(a['item']); qty=a.get('qty',1)
                if type(qty) is not int or qty<=0 or bag.get(item,0)<qty: raise ValueError('Invalid quantity')
                if item=='training_blade': raise ValueError('Weapon ground transfers are not implemented yet')
                bag[item]-=qty; self._bag(pid,bag); did=secrets.token_hex(8)
                self.db.execute('INSERT INTO drops VALUES(?,?,?,?,?)',(did,item,qty,p['x'],p['y'])); return {'drop_id':did}
            d=self.db.execute('SELECT * FROM drops WHERE id=?',(a['drop_id'],)).fetchone(); self._near(p,d)
            if self._weight(bag)+RECIPES.get(d['item'],{'weight':1})['weight']*d['qty']>CAPACITY: raise ValueError('Weight limit exceeded')
            bag[d['item']]=bag.get(d['item'],0)+d['qty']; self._bag(pid,bag)
            if d['item']=='training_blade': self._add_weapon(pid)
            self.db.execute('DELETE FROM drops WHERE id=?',(d['id'],)); return {'item':d['item'],'qty':d['qty']}
        raise ValueError('Unknown action')

    def _last_combat(self,pid):
        c=self.db.execute('SELECT id,outcome,detail FROM combats WHERE done=1 AND (a=? OR b=?) ORDER BY started DESC,rowid DESC LIMIT 1',(pid,pid)).fetchone()
        if not c: return None
        detail=json.loads(c['detail'] or '{}')
        if not detail: detail={'outcome':c['outcome'],'legacy_record':True,'details_unavailable':True}
        return dict(c,detail=detail)

    def _add_weapon(self, pid):
        seq=self.db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM weapons WHERE owner=?',(pid,)).fetchone()[0]
        wid=secrets.token_hex(8)
        self.db.execute('INSERT INTO weapons VALUES(?,?,?,?,?)',(wid,pid,'training_blade',WEAPON_DURABILITY,seq))
        self._weapon(pid)

    def tick(self):
        with self.lock:
            now=self.clock(); dt=max(0,min(now-self.last_tick,.25)); self.last_tick=now
            try:
                self.db.execute('BEGIN IMMEDIATE')
                self._tick_energy(now)
                # Session loss starts a server-owned countdown, not instant disappearance.
                for s in self.db.execute('SELECT * FROM sessions WHERE online=1').fetchall():
                    pid=s['owner']
                    if now-s['last_seen']>HEARTBEAT_TIMEOUT and not s['logout_requested']:
                        self._cancel_craft(pid)
                        self.db.execute('UPDATE sessions SET logout_requested=1,logout_at=? WHERE owner=?',(now+LOGOUT_DELAY,pid))
                        self._event(pid,'Heartbeat lost; logout countdown started')
                        log.info('heartbeat_timeout player=%s',pid)
                    elif s['logout_requested'] and s['logout_at'] is None and now-s['last_hit']>=PEACE_DELAY:
                        self.db.execute('UPDATE sessions SET logout_at=? WHERE owner=?',(now+LOGOUT_DELAY,pid))
                        self._event(pid,'Peace interval reached; logout countdown restarted')
                    elif s['logout_requested'] and s['logout_at'] is not None and now>=s['logout_at']:
                        c=self._combat(pid)
                        if c: self._finish(c,'logout',c['b'] if c['a']==pid else c['a'],pid)
                        self.db.execute('UPDATE sessions SET online=0,logout_at=NULL WHERE owner=?',(pid,))
                        self._cancel_craft(pid)
                        log.info('player_offline player=%s',pid)
                # Due individual jobs finish once in the same transaction as charging and delivery.
                for j in self.db.execute("SELECT * FROM jobs WHERE kind='craft' AND done=0 AND ready<=?",(now,)).fetchall():
                    pid=j['owner']; p=self._p(pid); s=self.db.execute('SELECT * FROM sessions WHERE owner=?',(pid,)).fetchone()
                    if not s['online'] or s['logout_requested'] or self._combat(pid):
                        self._cancel_craft(pid); continue
                    item=json.loads(j['payload'])['item']; recipe=RECIPES.get(item)
                    if not recipe or p['soul']<recipe['cost']:
                        self.db.execute('UPDATE jobs SET done=1 WHERE id=?',(j['id'],)); self._event(pid,'Craft cancelled: insufficient cost'); continue
                    bag=json.loads(p['bag']); destination='inventory'
                    self.db.execute('UPDATE players SET soul=soul-? WHERE id=?',(recipe['cost'],pid))
                    if self._weight(bag)+recipe['weight']>CAPACITY:
                        destination='ground'
                        self.db.execute('INSERT INTO drops VALUES(?,?,?,?,?)',(secrets.token_hex(8),item,1,p['x'],p['y']))
                    else:
                        bag[item]=bag.get(item,0)+1; self._bag(pid,bag)
                        if item=='training_blade': self._add_weapon(pid)
                    self.db.execute('UPDATE jobs SET done=1 WHERE id=?',(j['id'],))
                    self._event(pid,'Craft completed: '+item+' -> '+destination)
                    log.info('craft_complete player=%s job=%s item=%s destination=%s',pid,j['id'],item,destination)
                self.db.execute("UPDATE actors SET state='idle' WHERE state='moving' AND until_at<=? AND owner NOT IN (SELECT a FROM combats WHERE done=0 UNION SELECT b FROM combats WHERE done=0)",(now,))
                self._tick_combats(now,dt)
                # Idle and stationary blocking regenerate; attacking/moving/dodging do not.
                self.db.execute("UPDATE actors SET stamina=MIN(100,stamina+?) WHERE state IN ('idle','block') AND owner IN (SELECT owner FROM sessions WHERE online=1)",(10*dt,))
                self.db.commit()
            except Exception:
                self.db.rollback(); raise

