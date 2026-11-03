"""Key-only CLI. Secrets are prompted without echo; no legacy login or profiles."""
import argparse
import getpass
import json
import shlex
import threading
import urllib.request
import urllib.error
from .core import compute

HELP='''key_login | key_new | key_status | me | nearby | look | events
move DX DY | say MESSAGE | inspect ID | lock PLAYER_ID | flee | surrender
place | build ID | dismantle ID | craft ITEM | jobs | cancel ID
pickup ID | drop ITEM [QTY] | equip ID | charge [AMOUNT] | aid ID [AMOUNT]
node [COUNT] | logout | resume | help | quit
Login Key is entered at a hidden prompt, never as a command argument.'''

def format_event(event):
    kind=event.get('kind','message'); d=event.get('data',{})
    if kind=='chat_local':return '[local] '+d['sender_name']+' ('+d['sender_id']+'): '+d['text']
    if kind=='combat_start': return 'Combat started: '+str(d.get('combat_id',''))+'; initiator='+d['initiator']+'; defender='+d['defender']+'; mode=auto'
    if kind=='combat_end':
        if d['outcome'] in ('escape','separation'):
            return 'Combat ended: '+d['outcome']+'; escaped_players='+','.join(d['escaped_players'])+'; remaining_players='+','.join(d['remaining_players'])
        return 'Combat ended: '+d['outcome']+'; winner='+str(d.get('winner'))+'; loser='+str(d.get('loser'))
    if kind=='combat_hit':
        extra=''
        if d.get('weapon'): extra='; weapon='+d['weapon']+' durability='+str(d['durability_before'])+'->'+str(d['durability_after'])
        return ('Combat '+str(d.get('combat_id',''))+' '+d['result']+': '+d['attacker']+' -> '+d['target']+
                '; damage='+str(d['damage'])+'; HP='+str(d['hp_before'])+'->'+str(d['hp_after'])+extra)
    if kind=='equipment_changed':
        return 'Equipment changed: '+str(d['previous'])+' -> '+str(d['equipped'])+'; reason='+d['reason']
    if kind=='auto_retreat_started': return 'Auto retreat: '+d['player']+'; reason='+d['reason']
    if kind=='energy_depleted': return 'Digit Energy depleted: fireball; movement and combat disabled'
    if kind=='energy_charged':
        return 'Energy charged: target='+d['target']+'; Soul Digit spent='+str(d['spent_soul'])+'; energy='+str(d['energy_before'])+'->'+str(d['energy_after'])+'; automatic='+str(d['automatic'])
    return event['message']

def format_nearby(result):
    origin=result['origin']
    lines=['Nearby (radius '+str(result['radius'])+'); position=('+str(round(origin['x'],3))+', '+str(round(origin['y'],3))+')',
           'Direction: 0=+X, 90=+Y, 180=-X, 270=-Y; no line-of-sight filtering yet.']
    for group in ('players','objects','drops'):
        lines.append(group+':')
        if not result[group]:lines.append('  (none within view radius)')
        for e in result[group]:
            label=e.get('name',e.get('kind',e.get('item','')))
            extra=(' form='+e['form']) if 'form' in e else ''
            if 'progress' in e:extra+=' progress='+str(e['progress'])+'/4 usable='+str(e['usable'])
            if 'qty' in e:extra+=' qty='+str(e['qty'])
            lines.append('  '+e['id']+' '+label+' distance='+str(e['distance'])+' direction='+str(e['direction_degrees'])+extra)
    return '\n'.join(lines)

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--url',default='http://127.0.0.1:8765');a=ap.parse_args()
    token='';online=False;stop=threading.Event()
    def call(action,args=None,credential=None):
        req=urllib.request.Request(a.url.rstrip('/')+'/v1/command',json.dumps({'action':action,'args':args or {}}).encode(),{'Content-Type':'application/json','Authorization':'Bearer '+(token if credential is None else credential)})
        try:response=urllib.request.urlopen(req,timeout=5)
        except urllib.error.HTTPError as exc:response=exc
        with response:
            data=json.load(response)
            if not data.get('ok'):raise ValueError(data.get('error','Request failed'))
            return data['result']
    def heartbeat():
        nonlocal online
        while not stop.wait(5):
            if online:
                try:call('heartbeat')
                except Exception:online=False
    thread=threading.Thread(target=heartbeat,daemon=True);thread.start();print(HELP)
    try:
        while True:
            try:
                parts=shlex.split(input('digit> '))
                if not parts:continue
                cmd,*v=parts
                if cmd=='quit':break
                if cmd=='help':print(HELP);continue
                if cmd in ('key_login','key_new'):
                    if v:raise ValueError('Do not put Login Key in command arguments')
                    if online:
                        state=call('me')
                        if state['online']:raise ValueError('Logout and wait offline before changing account')
                        online=False
                    if cmd=='key_login':
                        result=call('key_login',{'key':getpass.getpass('Login Key (hidden): ').strip()},'')
                    else:
                        print('หาก KEY หาย จะไม่สามารถกู้คืนบัญชีได้ ห้ามส่ง Key ให้ผู้อื่น')
                        name=input('Character name: ')
                        if input('Accept Key loss warning? Type YES: ')!='YES':continue
                        pending=call('key_register',{'name':name,'ack_key_loss':True},'')
                        print('ONE-TIME LOGIN KEY (do not record/share console): '+pending['login_key'])
                        if input('Save Key now. Type SAVED to activate: ')!='SAVED':continue
                        result=call('key_confirm',{'key':pending['login_key'],'ack_saved':True},pending['token'])
                        pending.clear()
                    token=result['token'];online=True;print('Login successful; session credentials not displayed');continue
                if not token:raise ValueError('Use key_login or key_new first')
                if cmd=='move':args={'dx':float(v[0]),'dy':float(v[1])}
                elif cmd=='say':args={'text':' '.join(v)}
                elif cmd=='inspect':args={'entity_id':v[0]}
                elif cmd=='lock':args={'player_id':v[0]}
                elif cmd in ('build','dismantle'):args={'object_id':v[0]}
                elif cmd=='craft':args={'item':v[0]}
                elif cmd=='cancel':args={'job_id':v[0]}
                elif cmd=='pickup':args={'drop_id':v[0]}
                elif cmd=='drop':args={'item':v[0],'qty':int(v[1]) if len(v)>1 else 1}
                elif cmd=='equip':args={'weapon_id':v[0]}
                elif cmd=='charge':args={'amount':int(v[0]) if v else 1}
                elif cmd=='aid':args={'player_id':v[0],'amount':int(v[1]) if len(v)>1 else 1}
                elif cmd=='place':args={'kind':'wall'}
                elif cmd=='node':
                    for _ in range(min(10,max(1,int(v[0]) if v else 1))):
                        job=call('node_job');print(json.dumps(call('node_submit',{'job_id':job['job_id'],'result':compute(job)})))
                    continue
                elif cmd in ('key_status','me','nearby','look','events','jobs','flee','surrender','logout','resume'):args={}
                else:raise ValueError('Unknown command; use help')
                result=call(cmd,args)
                if cmd=='events':
                    for event in result['events']:print(format_event(event))
                elif cmd=='nearby':print(format_nearby(result))
                else:print(json.dumps(result,ensure_ascii=False,indent=2))
                if cmd=='me':online=result['online']
            except (EOFError,KeyboardInterrupt):print();break
            except Exception as exc:print('Error: '+str(exc))
    finally:
        stop.set();thread.join(timeout=6)
        if online:
            try:call('logout')
            except Exception:pass
if __name__=='__main__':main()
