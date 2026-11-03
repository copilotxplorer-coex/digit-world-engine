
'use strict';
const $=id=>document.getElementById(id), canvas=$('world'), ctx=canvas.getContext('2d');
let token='', online=false, me=null, world=null, cursor=0, busy=false, heartbeatAt=0, lastMove=0;
function log(text){const line=document.createElement('div');line.textContent=text;$('events').appendChild(line);while($('events').children.length>150)$('events').firstChild.remove();$('events').scrollTop=$('events').scrollHeight;}
async function api(action,args={},credential=token){const response=await fetch('/v1/command',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+credential},body:JSON.stringify({action,args,request_id:crypto.randomUUID()})});const data=await response.json();if(!response.ok||!data.ok){if(['Invalid token','Session expired; use Login Key'].includes(data.error)&&credential===token){online=false;me=null;world=null;ctx.clearRect(0,0,760,520);$('status').textContent='Session หมดอายุหรือย้าย Client กรุณา Login';$('entities').replaceChildren();$('connection').textContent='สิทธิ์เดิมใช้ไม่ได้ / ย้ายไป Client อื่นแล้ว';}throw new Error(data.error||'Request failed');}return data.result;}
async function run(action,args={}){try{const result=await api(action,args);log(action+': '+JSON.stringify(result));return result;}catch(e){log('Error: '+e.message);return null;}}
$('logout').onclick=()=>run('logout');
async function refresh(){if(!online||receiving)return;me=await api('me');if(!me.online){online=false;me=null;world=null;ctx.clearRect(0,0,760,520);$('status').textContent='Session หมดอายุหรือย้าย Client กรุณา Login';$('entities').replaceChildren();$('connection').textContent='ออฟไลน์';return;}world=await api('nearby');const events=await api('events',{after:cursor});for(const e of events.events){cursor=e.seq;log(e.kind==='chat_local'?'[local] '+e.data.sender_name+': '+e.data.text:e.message);}if(Date.now()-heartbeatAt>5000){await api('heartbeat');heartbeatAt=Date.now();}render();}
function render(){if(!me||!world)return;$('status').textContent=`${me.name}\nID ${me.id}\nตำแหน่ง ${me.x.toFixed(2)}, ${me.y.toFixed(2)}\nHP ${me.hp} · Stamina ${me.stamina}\nEnergy ${me.digit_energy.toFixed(2)} · ${me.form}\nSoul ${me.soul} · น้ำหนัก ${me.weight}/${me.capacity}\n${me.state} · Combat ${me.combat?'กำลังต่อสู้':'ไม่มี'}\nกระเป๋า ${JSON.stringify(me.bag)}`;
const selected=$('entities').value;$('entities').replaceChildren();for(const key of ['players','objects','drops'])for(const e of world[key]){if(e.id===me.id)continue;const option=document.createElement('option');option.value=e.id;option.dataset.type=e.entity_type;option.textContent=(e.name||e.kind||e.item)+' · '+e.distance+' · '+e.id;$('entities').appendChild(option);}if([...$('entities').options].some(o=>o.value===selected))$('entities').value=selected;
ctx.clearRect(0,0,760,520);ctx.strokeStyle='#263a50';const scale=8,cx=380,cy=260;for(let i=-5;i<=5;i++){ctx.beginPath();ctx.moveTo(cx+i*80,0);ctx.lineTo(cx+i*80,520);ctx.stroke();ctx.beginPath();ctx.moveTo(0,cy+i*80);ctx.lineTo(760,cy+i*80);ctx.stroke();}ctx.strokeStyle='#55718b';ctx.beginPath();ctx.arc(cx,cy,30*scale,0,Math.PI*2);ctx.stroke();
for(const key of ['objects','drops','players'])for(const e of world[key]){const x=cx+(e.x-me.x)*scale,y=cy-(e.y-me.y)*scale;ctx.fillStyle=key==='players'?(e.form==='fireball'?'#ff9d45':e.id===me.id?'#58caff':'#68e2b3'):key==='objects'?'#b5a0e7':'#ffd45a';if(key==='players'){ctx.beginPath();ctx.arc(x,y,6,0,Math.PI*2);ctx.fill();}else ctx.fillRect(x-5,y-5,10,10);ctx.font='12px sans-serif';ctx.fillText(e.name||e.kind||e.item,x+9,y-7);}}
async function move(dx,dy){if(!online||Date.now()-lastMove<250)return;lastMove=Date.now();await run('move',{dx,dy});}
for(const b of document.querySelectorAll('[data-move]'))b.onclick=()=>move(...b.dataset.move.split(',').map(Number));
canvas.addEventListener('keydown',e=>{const map={ArrowUp:[0,1],w:[0,1],ArrowDown:[0,-1],s:[0,-1],ArrowLeft:[-1,0],a:[-1,0],ArrowRight:[1,0],d:[1,0]};if(map[e.key]){e.preventDefault();move(...map[e.key]);}});
$('send').onclick=async()=>{if(await run('say',{text:$('chat').value}))$('chat').value='';};$('chat').onkeydown=e=>{if(e.key==='Enter')$('send').click();};
function selected(type){const o=$('entities').selectedOptions[0];if(!o||type&&o.dataset.type!==type){log('เลือกเป้าหมายประเภท '+(type||'ที่ต้องการ')+' ก่อน');return null;}return o.value;}
for(const [id,action,key,type] of [['inspect','inspect','entity_id',null],['lock','lock','player_id','player'],['build','build','object_id','object'],['dismantle','dismantle','object_id','object'],['pickup','pickup','drop_id','drop']])$(id).onclick=()=>{const target=selected(type);if(target)run(action,{[key]:target});};
for(const [id,action,args] of [['brick','craft',{item:'brick'}],['blade','craft',{item:'training_blade'}],['place','place',{kind:'wall'}],['charge','charge',{amount:1}],['flee','flee',{}],['surrender','surrender',{}]])$(id).onclick=()=>run(action,args);
$('demo').onclick=async()=>{if(!crypto.subtle){log('ต้องใช้ localhost เพื่อคำนวณงานตัวอย่าง');return;}$('demo').disabled=true;try{const job=await api('node_job');let value=new TextEncoder().encode(job.challenge);for(let i=0;i<job.iterations;i++)value=new Uint8Array(await crypto.subtle.digest('SHA-256',value));const result=Array.from(value,b=>b.toString(16).padStart(2,'0')).join('');await run('node_submit',{job_id:job.job_id,result});}catch(e){log('Demo: '+e.message);}finally{$('demo').disabled=false;}};

let receiving=false;
async function destinationReady(){if(online)throw Error('ออกเกมและรอออฟไลน์ก่อน Login อีกบัญชี');}
async function adoptAccount(result){
 token=result.token;online=!!result.online;cursor=result.event_cursor;heartbeatAt=0;me=null;world=null;
 $('connection').textContent='ออนไลน์ด้วย Login Key';log('Login สำเร็จ');
}
setInterval(async()=>{if(busy||!online)return;busy=true;try{await refresh();}catch(e){log('Connection: '+e.message);}finally{busy=false;}},600);
// Login Key is memory-only. Never pass key responses through run()/log().
let pendingLoginKey='',pendingKeyToken='',keyWorking=false;
const keyMessage=$('keyMessage');
function keyWarn(){if(!$('keyLossAck').checked)throw Error('กรุณายอมรับว่า KEY หายจะไม่สามารถกู้คืนบัญชีได้');}
async function keyTask(fn){if(keyWorking)return;keyWorking=true;try{await fn();}catch(e){keyMessage.textContent=e.message;}finally{keyWorking=false;}}
function showLoginKey(result){pendingLoginKey=result.login_key;$('keyOutput').value=pendingLoginKey;$('keyReveal').hidden=false;keyMessage.textContent='บันทึก Key แล้วกดเปิดใช้งาน ห้ามส่ง Key ให้ผู้อื่น';}
$('keyLogin').onclick=()=>keyTask(async()=>{
 await destinationReady();const key=$('keyInput').value.trim();$('keyInput').value='';
 const result=await api('key_login',{key},'');await adoptAccount(result);await refresh();keyMessage.textContent='Login ด้วย Key สำเร็จ';
});
$('keyNew').onclick=()=>keyTask(async()=>{
 keyWarn();await destinationReady();const result=await api('key_register',{name:$('keyName').value.trim(),ack_key_loss:true},'');
 pendingKeyToken=result.token;showLoginKey(result);
});
$('keyCopy').onclick=()=>keyTask(async()=>{if(!pendingLoginKey)throw Error('ไม่มี Key ที่รอบันทึก');try{await navigator.clipboard.writeText(pendingLoginKey);keyMessage.textContent='คัดลอกแล้ว เก็บไว้ในที่ปลอดภัย';}catch(e){$('keyOutput').select();keyMessage.textContent='เลือก Key แล้ว กรุณากด Ctrl+C หรือดาวน์โหลดไฟล์';}});
$('keyDownload').onclick=()=>{if(!pendingLoginKey)return;const blob=new Blob(['Digit World Login Key\n'+pendingLoginKey+'\nKEY หายจะไม่สามารถกู้คืนบัญชีได้ ห้ามส่งให้ผู้อื่น\n'],{type:'text/plain;charset=utf-8'});const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='digit-world-login-key.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);keyMessage.textContent='โปรดตรวจว่าไฟล์ดาวน์โหลดและเปิดอ่านได้ก่อนยืนยัน';};
$('keySaved').onclick=()=>keyTask(async()=>{
 if(!pendingLoginKey)throw Error('ไม่มี Key ที่รอยืนยัน');
 const result=await api('key_confirm',{key:pendingLoginKey,ack_saved:true},pendingKeyToken);await adoptAccount(result);
 pendingLoginKey='';pendingKeyToken='';$('keyOutput').value='';$('keyReveal').hidden=true;await refresh();
 keyMessage.textContent='เปิดใช้ Key แล้ว หาก KEY หายจะไม่สามารถกู้คืนได้';
});
window.addEventListener('beforeunload',e=>{if(pendingLoginKey){e.preventDefault();e.returnValue='';}});
