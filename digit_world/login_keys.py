"""Key-only accounts: generated once, hash-only persistence, no recovery."""
import hashlib
import hmac
import re
import secrets
WARNING='หาก Login Key สูญหาย จะไม่สามารถกู้คืนบัญชีได้ ผู้ที่มี Key สามารถเข้าถึงบัญชีได้ ห้ามส่ง Key ให้ผู้อื่น'
PUBLIC={'key_login','key_register'}
ACTIONS=PUBLIC|{'key_confirm','key_status'}

def digest(key):
    if not isinstance(key,str) or not re.fullmatch(r'DWK1_[A-Za-z0-9_-]{43}',key):raise ValueError('Invalid Login Key')
    return hashlib.sha256(key.encode('ascii')).hexdigest()

def tables(w):
    w.db.execute('CREATE TABLE IF NOT EXISTS login_keys(owner TEXT PRIMARY KEY,digest TEXT UNIQUE NOT NULL)')
    w.db.execute('CREATE TABLE IF NOT EXISTS pending_login_keys(owner TEXT PRIMARY KEY,digest TEXT UNIQUE NOT NULL,expires REAL NOT NULL,session_digest TEXT NOT NULL)')

def converted(w,pid):return w.db.execute('SELECT 1 FROM login_keys WHERE owner=?',(pid,)).fetchone() is not None

def authenticated(w,token,allow_pending=False):
    if not isinstance(token,str) or len(token)>256:raise ValueError('Invalid token')
    p=w.db.execute('SELECT * FROM players WHERE token=?',(token,)).fetchone()
    if not p:raise ValueError('Invalid token')
    session=w.db.execute('SELECT expires FROM control_sessions WHERE owner=?',(p['id'],)).fetchone()
    if not session or session[0]<=w.clock():raise ValueError('Session expired; use Login Key')
    if not allow_pending and not converted(w,p['id']):raise ValueError('Save and confirm Login Key before playing')
    return p

def action(w,token,name,a):
    if name=='key_login':
        if set(a)!={'key'}:raise ValueError('Key only; unsupported fields')
        d=digest(a.get('key'));row=w.db.execute('SELECT owner FROM login_keys WHERE digest=?',(d,)).fetchone()
        if not row:raise ValueError('Invalid Login Key')
        w._control_allowed(row['owner']);return w._rotate_control(row['owner'])
    if name=='key_register':
        if set(a)!={'name','ack_key_loss'} or a.get('ack_key_loss') is not True:raise ValueError(WARNING)
        from .social import clean_text
        raw=a.get('name')
        if not isinstance(raw,str) or not 1<=len(raw.strip())<=32 or any(ord(c)<32 for c in raw):raise ValueError('Name must contain 1..32 printable characters')
        display=clean_text(raw,32);pid=secrets.token_hex(8);key='DWK1_'+secrets.token_urlsafe(32)
        w.db.execute('INSERT INTO players(id,name,token) VALUES(?,?,?)',(pid,display,secrets.token_urlsafe(32)));w._ensure_actor(pid)
        result=w._rotate_control(pid,online=False)
        w.db.execute('INSERT INTO pending_login_keys VALUES(?,?,?,?)',(pid,digest(key),w.clock()+600,hashlib.sha256(result['token'].encode()).hexdigest()))
        return dict(result,login_key=key,warning=WARNING,expires_seconds=600,active=False)
    p=authenticated(w,token,allow_pending=name=='key_confirm');pid=p['id']
    if name=='key_status':
        if a:raise ValueError('Unsupported fields')
        return {'key_enabled':True,'warning':WARNING,'recovery_available':False}
    if name=='key_confirm':
        if set(a)!={'key','ack_saved'} or a.get('ack_saved') is not True:raise ValueError('Save Login Key and acknowledge first')
        w._control_allowed(pid);d=digest(a.get('key'))
        current=w.db.execute('SELECT digest FROM login_keys WHERE owner=?',(pid,)).fetchone()
        if current:
            if not hmac.compare_digest(current[0],d):raise ValueError('Invalid Login Key')
            return dict(w._rotate_control(pid),key_enabled=True,warning=WARNING)
        pending=w.db.execute('SELECT * FROM pending_login_keys WHERE owner=?',(pid,)).fetchone()
        if not pending or pending['expires']<=w.clock() or not hmac.compare_digest(pending['digest'],d) or not hmac.compare_digest(pending['session_digest'],hashlib.sha256(token.encode()).hexdigest()):raise ValueError('Invalid or expired pending Key')
        w.db.execute('INSERT INTO login_keys VALUES(?,?)',(pid,d))
        w.db.execute('DELETE FROM pending_login_keys WHERE owner=?',(pid,))
        return dict(w._rotate_control(pid),key_enabled=True,warning=WARNING)
    raise ValueError('Unknown Key action')
