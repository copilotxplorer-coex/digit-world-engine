"""Snapshot-based training combat and structured private/server telemetry."""
import hashlib
import json
import logging
import math

log=logging.getLogger('digit.combat')
ATTACK_RANGE=2
DAMAGE=10
ESCAPE_RANGE=12
ESCAPE_SECONDS=3
PROTECTION_SECONDS=5

class CombatMixin:
    def _telemetry(self, recipients, kind, data, combat_id=None):
        payload=dict(data)
        if combat_id is not None: payload['combat_id']=combat_id
        message=kind+': '+json.dumps(payload,ensure_ascii=True,sort_keys=True)
        for pid in dict.fromkeys(recipients):
            self.db.execute('INSERT INTO events(owner,message,created,kind,data) VALUES(?,?,?,?,?)',
                            (pid,message,self.clock(),kind,json.dumps(payload,sort_keys=True)))
        log.info('%s %s',kind,json.dumps(payload,sort_keys=True))

    def _weapon(self,pid):
        actor=self._actor(pid)
        w=self.db.execute('SELECT * FROM weapons WHERE id=? AND owner=? AND durability>0',(actor['equipped'],pid)).fetchone()
        if not w:
            w=self.db.execute('SELECT * FROM weapons WHERE owner=? AND durability>0 ORDER BY seq,id LIMIT 1',(pid,)).fetchone()
            selected=w['id'] if w else None
            self.db.execute('UPDATE actors SET equipped=? WHERE owner=?',(selected,pid))
            if actor['equipped']!=selected:
                self._telemetry([pid],'equipment_changed',{'player':pid,'previous':actor['equipped'],
                                'equipped':selected,'reason':'first_usable_in_bag' if selected else 'no_usable_equipment',
                                'durability':w['durability'] if w else None})
        return w

    def _finish(self,c,outcome,winner,loser,escaped=None):
        now=self.clock(); participants=[c['a'],c['b']]
        if outcome in ('escape','separation','logout','mutual_defeat','energy_depleted_both'):
            winner=loser=None
        detail={'outcome':outcome,'winner':winner,'loser':loser}
        if outcome in ('escape','separation'):
            escaped=escaped if escaped is not None else [pid for pid in participants if self._actor(pid)['flee']]
            detail={'outcome':outcome,'escaped_players':escaped,
                    'remaining_players':[pid for pid in participants if pid not in escaped]}
        self.db.execute('UPDATE combats SET done=1,outcome=?,winner=?,loser=?,detail=? WHERE id=?',
                        (outcome,winner,loser,json.dumps(detail),c['id']))
        for pid in participants:
            state='fireball' if self._vital(pid)['form']=='fireball' else 'idle'
            self.db.execute('UPDATE actors SET state=?,until_at=0,active_at=0,resolved=0,flee=0,retreat_reason=NULL,protect_until=? WHERE owner=?',
                            (state,now+PROTECTION_SECONDS,pid))
            self.db.execute('UPDATE vitals SET charge_ready=0 WHERE owner=?',(pid,))
        self._telemetry(participants,'combat_end',detail,c['id'])
        # Explicitly a training resolution; final PK/loot/Freeze decisions remain backlog.
        reset=participants if outcome=='mutual_defeat' else ([loser] if outcome=='defeat' and loser else [])
        for pid in reset:
            self.db.execute('UPDATE players SET x=0,y=0 WHERE id=?',(pid,))
            self.db.execute('UPDATE actors SET hp=100 WHERE owner=?',(pid,))
            self._event(pid,'Training defeat: returned to origin; no Soul Digit loss in this prototype')

    def _snapshot(self,pid):
        return {'player':dict(self._p(pid)),'actor':dict(self._actor(pid)),
                'weapon':dict(w) if (w:=self.db.execute('SELECT * FROM weapons WHERE id=? AND owner=? AND durability>0',
                    (self._actor(pid)['equipped'],pid)).fetchone()) else None,
                'vital':dict(self._vital(pid))}

    def _evaluate_hit(self,pid,target,attack,now,snapshots=None):
        src=snapshots[pid] if snapshots else self._snapshot(pid)
        dst=snapshots[target] if snapshots else self._snapshot(target)
        p,q=src['player'],dst['player']; defender=dst['actor']
        result={'attacker':pid,'target':target,'damage':0,'result':'hit','weapon':None,
                'durability_before':None,'durability_after':None}
        if dst['vital']['form']=='fireball': result['result']='immune_fireball'; return result
        if self._distance(p,q)>ATTACK_RANGE: result['result']='miss_range'; return result
        angle=math.atan2(q['y']-p['y'],q['x']-p['x'])
        delta=abs((angle-attack['facing']+math.pi)%(2*math.pi)-math.pi)
        if delta>math.pi/3: result['result']='miss_direction'; return result
        if defender['state']=='dodge' and defender['active_at']<=now<=defender['active_at']+.25:
            result['result']='dodge_success'; return result
        result['damage']=DAMAGE
        if defender['state']=='block' and now<defender['until_at']:
            incoming=math.atan2(p['y']-q['y'],p['x']-q['x'])
            delta=abs((incoming-defender['facing']+math.pi)%(2*math.pi)-math.pi)
            w=dst['weapon']
            if delta<=math.pi/3 and w:
                absorbed=min(w['durability'],DAMAGE)
                result.update(damage=DAMAGE-absorbed,weapon=w['id'],durability_before=w['durability'],
                              durability_after=max(0,w['durability']-DAMAGE),
                              result='block_success' if absorbed==DAMAGE else 'block_break')
        return result

    def _apply_hit(self,result,now,combat_id=None):
        pid,target=result['attacker'],result['target']; damage=result['damage']
        before=self._actor(target)['hp']
        if result['weapon']:
            self.db.execute('UPDATE weapons SET durability=? WHERE id=?',(result['durability_after'],result['weapon']))
        contact=result['result'] in ('hit','block_success','block_break')
        if contact:
            s=self.db.execute('SELECT * FROM sessions WHERE owner=?',(target,)).fetchone()
            self.db.execute('UPDATE sessions SET last_hit=?,logout_at=CASE WHEN logout_requested=1 THEN NULL ELSE logout_at END WHERE owner=?',(now,target))
            if s['logout_requested'] and s['logout_at'] is not None:
                self._telemetry([target],'logout_interrupted',{'player':target,'reason':'attack_contact'},combat_id)
        if damage: self.db.execute('UPDATE actors SET hp=MAX(0,hp-?) WHERE owner=?',(damage,target))
        result=dict(result,hp_before=before,hp_after=max(0,before-damage))
        self._telemetry([pid,target],'combat_hit',result,combat_id)
        if result['weapon'] and result['durability_after']==0:
            self._telemetry([target],'equipment_unusable',{'player':target,'weapon':result['weapon'],'reason':'durability_zero'},combat_id)

    def _hit(self,pid,target,attack,now):
        # Kept for direct regression tests. Runtime uses snapshot evaluation of both hits first.
        result=self._evaluate_hit(pid,target,attack,now)
        self._apply_hit(result,now)
        return result

    def _choose_action(self,pid,other,snapshots,now,dt):
        own=snapshots[pid]; opponent=snapshots[other]
        p,q=own['player'],opponent['player']; ac=own['actor']; w=own['weapon']
        if ac['state'] in ('attack','dodge','block') and now<ac['until_at']: return None
        angle=math.atan2(q['y']-p['y'],q['x']-p['x']); distance=self._distance(p,q)
        if ac['flee'] or not w:
            # A stable player-ID tie break at coincident positions, independent of initiator role.
            if distance<.01: angle=0 if pid<other else math.pi
            reason=ac['retreat_reason'] or ('no_usable_equipment' if not w else 'player_requested')
            return {'kind':'retreat','angle':angle,'dx':-math.cos(angle)*5*dt,
                    'dy':-math.sin(angle)*5*dt,'reason':reason}
        if distance>1.5:
            step=min(2*dt,max(0,distance-1.5))
            return {'kind':'approach','angle':angle,'dx':math.cos(angle)*step,'dy':math.sin(angle)*step}
        visible_attack=opponent['actor']['state']=='attack' and opponent['actor']['active_at']>now
        # A per-player deterministic cycle is independent of a/b ordering. This is not a balanced AI.
        offset=int(hashlib.sha256(pid.encode()).hexdigest()[:4],16)%4
        phase=(ac['decision_seq']+offset)%4
        if visible_attack or phase==0:
            if ac['stamina']>=20:
                return {'kind':'dodge','angle':angle,'dx':-math.sin(angle)*.6,'dy':math.cos(angle)*.6}
            return {'kind':'block','angle':angle}
        if phase==1: return {'kind':'block','angle':angle}
        return {'kind':'attack','angle':angle}

    def _apply_action(self,pid,decision,now,combat_id):
        if not decision: return
        kind=decision['kind']; angle=decision['angle']; ac=self._actor(pid)
        if kind in ('retreat','approach'):
            self.db.execute('UPDATE players SET x=x+?,y=y+? WHERE id=?',(decision['dx'],decision['dy'],pid))
            self.db.execute("UPDATE actors SET state='moving',facing=? WHERE owner=?",(angle,pid))
            if kind=='retreat':
                if not ac['retreat_reason']:
                    c=self._combat(pid)
                    recipients=[c['a'],c['b']] if c else [pid]
                    self._telemetry(recipients,'auto_retreat_started',{'player':pid,'reason':decision['reason']},combat_id)
                self.db.execute('UPDATE actors SET flee=1,retreat_reason=? WHERE owner=?',(decision['reason'],pid))
            return
        self.db.execute('UPDATE actors SET facing=?,decision_seq=decision_seq+1 WHERE owner=?',(angle,pid))
        if kind=='attack':
            self.db.execute("UPDATE actors SET state='attack',until_at=?,active_at=?,resolved=0 WHERE owner=?",(now+1.4,now+.6,pid))
            self._telemetry([pid],'attack_started',{'player':pid,'active_at':now+.6,'active_end':now+.8,'ends_at':now+1.4},combat_id)
        elif kind=='dodge':
            self.db.execute("UPDATE actors SET state='dodge',until_at=?,active_at=?,stamina=stamina-20 WHERE owner=?",(now+.6,now+.1,pid))
            self.db.execute('UPDATE players SET x=x+?,y=y+? WHERE id=?',(decision['dx'],decision['dy'],pid))
            self._telemetry([pid],'dodge_started',{'player':pid,'stamina_before':ac['stamina'],'stamina_after':ac['stamina']-20,'invulnerable_from':now+.1,'invulnerable_until':now+.35},combat_id)
        elif kind=='block':
            self.db.execute("UPDATE actors SET state='block',until_at=? WHERE owner=?",(now+.6,pid))
            self._telemetry([pid],'block_started',{'player':pid,'facing':angle},combat_id)

    def _tick_combats(self,now,dt):
        for c in self.db.execute('SELECT * FROM combats WHERE done=0').fetchall():
            ids=[c['a'],c['b']]
            if any(self._vital(pid)['form']=='fireball' for pid in ids): continue
            distance=self._distance(self._p(ids[0]),self._p(ids[1]))
            if distance>ESCAPE_RANGE:
                if c['escape_since'] is None:
                    self.db.execute('UPDATE combats SET escape_since=? WHERE id=?',(now,c['id']))
                    self._telemetry(ids,'escape_countdown_started',{'distance':round(distance,3),'required_seconds':ESCAPE_SECONDS},c['id'])
                elif now-c['escape_since']>=ESCAPE_SECONDS:
                    escaped=[pid for pid in ids if self._actor(pid)['flee']]
                    self._finish(c,'escape' if escaped else 'separation',None,None,escaped); continue
            elif c['escape_since'] is not None:
                self.db.execute('UPDATE combats SET escape_since=NULL WHERE id=?',(c['id'],))
                self._telemetry(ids,'escape_countdown_reset',{'reason':'returned_inside_range'},c['id'])
            snapshots={pid:self._snapshot(pid) for pid in ids}
            hits=[]
            for pid,other in ((ids[0],ids[1]),(ids[1],ids[0])):
                ac=snapshots[pid]['actor']
                if ac['state']=='attack' and not ac['resolved'] and now>=ac['active_at']:
                    self.db.execute('UPDATE actors SET resolved=1 WHERE owner=?',(pid,))
                    if now>ac['active_at']+.2+1e-9:
                        self._telemetry([pid],'attack_window_missed',{'player':pid,'reason':'tick_passed_active_window'},c['id'])
                    else: hits.append(self._evaluate_hit(pid,other,ac,now,snapshots))
            # Evaluate both against the same pre-hit snapshot; simultaneous lethal hits can draw.
            for hit in hits: self._apply_hit(hit,now,c['id'])
            dead=[pid for pid in ids if self._actor(pid)['hp']<=0]
            if len(dead)==2: self._finish(c,'mutual_defeat',None,None); continue
            if dead: self._finish(c,'defeat',ids[1] if dead[0]==ids[0] else ids[0],dead[0]); continue
            # Auto equip is only allowed after an action ends, never during a block/attack/dodge.
            for pid in ids:
                ac=self._actor(pid)
                if ac['state'] not in ('attack','dodge','block') or now>=ac['until_at']: self._weapon(pid)
            snapshots={pid:self._snapshot(pid) for pid in ids}
            decisions={pid:self._choose_action(pid,ids[1] if pid==ids[0] else ids[0],snapshots,now,dt) for pid in ids}
            for pid in ids: self._apply_action(pid,decisions[pid],now,c['id'])
