"""Deterministic bounded proximity query, independent of game DB and credentials."""
import hashlib
import json
import math
from coordination.contract import identifier

KIND='nearby_snapshot_v1'
PROTOCOL=2
MAX_ENTITIES=64
MAX_AGE=5.0

def canonical(data):return json.dumps(data,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def fingerprint(data):return hashlib.sha256(canonical(data)).hexdigest()
def number(value,limit):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or abs(value)>limit:raise ValueError('Invalid finite coordinate/time')
    return value

def validate(snapshot):
    if not isinstance(snapshot,dict) or set(snapshot)!={'world_id','observer','radius','entities'}:raise ValueError('Invalid snapshot fields')
    identifier(snapshot['world_id'])
    observer=snapshot['observer']
    if not isinstance(observer,dict) or set(observer)!={'id','x','y'}:raise ValueError('Invalid observer')
    identifier(observer['id']);number(observer['x'],1e6);number(observer['y'],1e6)
    number(snapshot['radius'],30)
    if snapshot['radius']<=0:raise ValueError('Radius must be >0 and <=30')
    entities=snapshot['entities']
    if not isinstance(entities,list) or len(entities)>MAX_ENTITIES:raise ValueError('Snapshot exceeds 64 entities; no silent truncation')
    seen=set()
    for entity in entities:
        if not isinstance(entity,dict) or set(entity)!={'id','kind','x','y'}:raise ValueError('Invalid entity fields')
        identifier(entity['id'])
        if entity['kind'] not in ('player','object','drop'):raise ValueError('Invalid entity kind')
        number(entity['x'],1e6);number(entity['y'],1e6)
        key=(entity['kind'],entity['id'])
        if key in seen:raise ValueError('Duplicate entity')
        seen.add(key)
    return snapshot

def solve(snapshot):
    validate(snapshot);o=snapshot['observer'];r2=snapshot['radius']**2;matches=[]
    for entity in snapshot['entities']:
        if entity['kind']=='player' and entity['id']==o['id']:continue
        distance2=(entity['x']-o['x'])**2+(entity['y']-o['y'])**2
        if distance2<=r2:matches.append({'id':entity['id'],'kind':entity['kind'],'distance2':distance2})
    matches.sort(key=lambda row:(row['distance2'],row['kind'],row['id']))
    return {'snapshot_hash':fingerprint(snapshot),'matches':matches}

def validate_result(result):
    if not isinstance(result,dict) or set(result)!={'snapshot_hash','matches'}:raise ValueError('Invalid result fields')
    if not isinstance(result['snapshot_hash'],str) or len(result['snapshot_hash'])!=64 or any(c not in '0123456789abcdef' for c in result['snapshot_hash']):raise ValueError('Invalid snapshot hash')
    if not isinstance(result['matches'],list) or len(result['matches'])>MAX_ENTITIES:raise ValueError('Invalid match count')
    for match in result['matches']:
        if not isinstance(match,dict) or set(match)!={'id','kind','distance2'}:raise ValueError('Invalid match')
        identifier(match['id'])
        if match['kind'] not in ('player','object','drop'):raise ValueError('Invalid match kind')
        number(match['distance2'],900)
        if match['distance2']<0:raise ValueError('Negative distance')
    return result
