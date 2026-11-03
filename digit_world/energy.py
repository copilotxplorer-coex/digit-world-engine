"""Persistent Digit Energy, online-only drain and explicit/automatic charging."""
import json
import logging
import math

log = logging.getLogger('digit.energy')

class EnergyMixin:
    def _ensure_energy(self, pid):
        self.db.execute('INSERT OR IGNORE INTO vitals(owner,energy,updated_at) VALUES(?,?,?)',
                        (pid,self.config.energy_max,self.clock()))

    def _vital(self,pid):
        return self.db.execute('SELECT * FROM vitals WHERE owner=?',(pid,)).fetchone()

    def _energy_status(self,pid):
        v=self._vital(pid)
        return {'digit_energy':round(v['energy'],4),'energy_max':self.config.energy_max,
                'form':v['form'],'charge_ready_at':v['charge_ready'],
                'autocharge':{'enabled':bool(v['auto_enabled']),'threshold':v['auto_threshold'],
                              'amount':v['auto_amount'],'reserve':v['auto_reserve']}}

    def _charge(self, donor, target, amount, now, automatic=False):
        if type(amount) is not int or not 1<=amount<=100000:
            raise ValueError('Charge amount must be an integer from 1 to 100000')
        dv=self._vital(donor); tv=self._vital(target)
        if dv['charge_ready']>now: raise ValueError('Charging is on cooldown')
        if self._actor(donor)['state'] in ('stun','stagger'):
            raise ValueError('Cannot charge while stunned or staggered')
        if tv['energy']>=self.config.energy_max-1e-9: raise ValueError('Target energy is full')
        p=self._p(donor)
        needed=max(1,math.ceil((self.config.energy_max-tv['energy'])/self.config.energy_per_soul-1e-12))
        spent=min(amount,needed)
        if p['soul']<spent: raise ValueError('Insufficient Soul Digit')
        if automatic and p['soul']-spent<dv['auto_reserve']:
            raise ValueError('Automatic charge would breach reserve')
        after=min(self.config.energy_max,tv['energy']+spent*self.config.energy_per_soul)
        self.db.execute('UPDATE players SET soul=soul-? WHERE id=?',(spent,donor))
        self.db.execute("UPDATE vitals SET energy=?,form='normal',updated_at=? WHERE owner=?",(after,now,target))
        self.db.execute('UPDATE vitals SET charge_ready=? WHERE owner=?',(now+self.config.charge_cooldown_seconds,donor))
        if tv['form']=='fireball':
            self.db.execute("UPDATE actors SET state='idle' WHERE owner=?",(target,))
        self._telemetry([donor] if donor==target else [donor,target], 'energy_charged',
                        {'donor':donor,'target':target,'spent_soul':spent,
                         'energy_before':round(tv['energy'],4),'energy_after':round(after,4),
                         'restored_body':tv['form']=='fireball','automatic':automatic})
        return {'spent_soul':spent,'target':target,'digit_energy':round(after,4),'form':'normal',
                'charge_ready_at':now+self.config.charge_cooldown_seconds}

    def _energy_command(self,p,action,a):
        pid=p['id']; now=self.clock()
        if action=='charge': return self._charge(pid,pid,a.get('amount',1),now)
        if action=='aid':
            target=self._p(str(a['player_id']))
            if not target or target['id']==pid: raise ValueError('Use charge for yourself')
            target_id=target['id']
            ts=self.db.execute('SELECT * FROM sessions WHERE owner=?',(target_id,)).fetchone()
            if not ts['online']: raise ValueError('Target is offline')
            if self._vital(pid)['form']=='fireball': raise ValueError('Fireball can charge itself but cannot aid others')
            if self._combat(pid) or self._combat(target_id):
                raise ValueError('Third-party energy assistance is blocked during Combat')
            self._near(p,target)
            return self._charge(pid,target_id,a.get('amount',1),now)
        if action=='autocharge':
            if not a: return self._energy_status(pid)['autocharge']
            enabled=a.get('enabled')
            if type(enabled) is not bool: raise ValueError('enabled must be true or false')
            if not enabled:
                self.db.execute('UPDATE vitals SET auto_enabled=0 WHERE owner=?',(pid,))
                return self._energy_status(pid)['autocharge']
            threshold=a.get('threshold'); amount=a.get('amount',1); reserve=a.get('reserve',0)
            if isinstance(threshold,bool) or not isinstance(threshold,(int,float)) or not math.isfinite(threshold) or not 0<threshold<self.config.energy_max:
                raise ValueError('Threshold must be between 0 and energy_max')
            if type(amount) is not int or not 1<=amount<=100000 or type(reserve) is not int or reserve<0:
                raise ValueError('Amount must be positive and reserve nonnegative integers')
            self.db.execute('UPDATE vitals SET auto_enabled=1,auto_threshold=?,auto_amount=?,auto_reserve=? WHERE owner=?',
                            (threshold,amount,reserve,pid))
            return self._energy_status(pid)['autocharge']
        raise ValueError('Unknown energy action')

    def _tick_energy(self,now):
        # Use elapsed online time, not the movement dt cap. A due logout stops drain at its deadline.
        rows=self.db.execute('SELECT v.*,s.logout_requested,s.logout_at FROM vitals v JOIN sessions s ON s.owner=v.owner WHERE s.online=1').fetchall()
        depleted=[]
        for v in rows:
            pid=v['owner']; until=now
            if v['logout_requested'] and v['logout_at'] is not None:
                until=min(until,v['logout_at'])
            elapsed=max(0,until-v['updated_at'])
            if v['form']=='normal' and v['auto_enabled'] and self._combat(pid) and not v['logout_requested']:
                predicted=v['energy']-elapsed*self.config.energy_drain_per_second
                if predicted<=v['auto_threshold'] and v['charge_ready']<=now:
                    try: self._charge(pid,pid,v['auto_amount'],now,automatic=True)
                    except ValueError: pass
                    else: v=self._vital(pid)
            after=max(0,min(self.config.energy_max,v['energy']-elapsed*self.config.energy_drain_per_second))
            self.db.execute('UPDATE vitals SET energy=?,updated_at=? WHERE owner=?',(after,until,pid))
            if after<=0 and v['form']!='fireball':
                self.db.execute("UPDATE vitals SET form='fireball' WHERE owner=?",(pid,))
                self._cancel_craft(pid)
                self.db.execute("UPDATE actors SET state='fireball',until_at=0,resolved=1,flee=0 WHERE owner=?",(pid,))
                self._telemetry([pid],'energy_depleted',{'player':pid,'form':'fireball'})
                depleted.append(pid)
        # Both can deplete in the same tick: decide only after every participant is updated.
        handled=set()
        for pid in depleted:
            c=self._combat(pid)
            if not c or c['id'] in handled: continue
            handled.add(c['id'])
            other=c['b'] if c['a']==pid else c['a']
            if self._vital(other)['form']=='fireball':
                self._finish(c,'energy_depleted_both',None,None)
            else: self._finish(c,'energy_depleted',other,pid)
