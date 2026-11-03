"""Local communication and bounded public world inspection."""
import json
import logging
import math
import unicodedata

log=logging.getLogger('digit.social')
VIEW_RADIUS=30
CHAT_COOLDOWN=2
MAX_CHAT=200

def clean_text(value,limit=MAX_CHAT):
    if not isinstance(value,str): raise ValueError('Text must be a string')
    value=value.strip()
    if not value or len(value)>limit: raise ValueError('Text must contain 1..'+str(limit)+' characters')
    if any(unicodedata.category(c).startswith('C') or c in '\u2028\u2029' for c in value):
        raise ValueError('Control and formatting characters are not allowed')
    return value

class SocialMixin:
    def _public_entity(self,p,row,kind):
        distance=self._distance(p,row)
        result={k:row[k] for k in (('id','name','x','y') if kind=='player' else row.keys())}
        result['entity_type']=kind
        result['distance']=round(distance,3)
        result['direction_degrees']=round(math.degrees(math.atan2(row['y']-p['y'],row['x']-p['x']))%360,2)
        if kind=='player':result['form']=self._vital(row['id'])['form']
        if kind=='object':
            result['usable']=row['progress']>=4
            result['interaction_in_range']=distance<=3
        if kind=='drop':result['interaction_in_range']=distance<=3
        return result

    def _nearby(self,p):
        result={'origin':{'x':p['x'],'y':p['y']},'radius':VIEW_RADIUS}
        players=self.db.execute('SELECT p.* FROM players p JOIN sessions s ON s.owner=p.id WHERE s.online=1').fetchall()
        for key,rows,kind in [('players',players,'player'),('objects',self.db.execute('SELECT * FROM objects').fetchall(),'object'),('drops',self.db.execute('SELECT * FROM drops').fetchall(),'drop')]:
            result[key]=sorted([self._public_entity(p,r,kind) for r in rows if self._distance(p,r)<=VIEW_RADIUS],key=lambda e:(e['distance'],e['id']))
        return result

    def _social_command(self,p,action,a):
        pid=p['id']; now=self.clock()
        session=self.db.execute('SELECT * FROM sessions WHERE owner=?',(pid,)).fetchone()
        if not session['online']:raise ValueError('Login required')
        if action=='nearby':return self._nearby(p)
        if action=='inspect':
            entity_id=a.get('entity_id')
            if not isinstance(entity_id,str) or len(entity_id)>128:raise ValueError('Invalid entity ID')
            nearby=self._nearby(p)
            for key in ('players','objects','drops'):
                for entity in nearby[key]:
                    if entity['id']==entity_id:return entity
            raise ValueError('Entity is not visible within 30 units')
        if action=='say':
            if session['logout_requested']:raise ValueError('Logout pending; use resume first')
            text=clean_text(a.get('text'))
            key='chat_after:'+pid
            row=self.db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
            if row and now<float(row['value']):raise ValueError('Local chat cooldown: 2 seconds')
            recipients=[r['id'] for r in self.db.execute('SELECT p.id,p.x,p.y FROM players p JOIN sessions s ON s.owner=p.id WHERE s.online=1') if self._distance(p,r)<=VIEW_RADIUS]
            payload={'sender_id':pid,'sender_name':p['name'],'text':text,'radius':VIEW_RADIUS}
            # Never send chat content to server logs. Persist only in recipient-private events.
            for recipient in recipients:
                self.db.execute('INSERT INTO events(owner,message,created,kind,data) VALUES(?,?,?,?,?)',
                                (recipient,'Local chat message',now,'chat_local',json.dumps(payload,ensure_ascii=True)))
            self.db.execute('INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)',(key,str(now+CHAT_COOLDOWN)))
            log.info('chat_delivered sender=%s recipient_count=%s characters=%s',pid,len(recipients),len(text))
            return {'sent':True,'recipient_count':len(recipients),'radius':VIEW_RADIUS,'cooldown_seconds':CHAT_COOLDOWN}
        raise ValueError('Unknown social command')
