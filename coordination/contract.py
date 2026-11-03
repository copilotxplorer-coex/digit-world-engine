"""Bounded synthetic validation task. Not useful game simulation or proof of work."""
import hashlib
import re

PROTOCOL=1
KIND='validation_hash_v1'

def identifier(value):
    if not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9_-]{1,48}',value):
        raise ValueError('Invalid identifier')
    return value

def validate_payload(payload):
    if not isinstance(payload,dict) or set(payload)!={'challenge','rounds'}:
        raise ValueError('Invalid task payload')
    if not isinstance(payload['challenge'],str) or not re.fullmatch('[a-f0-9]{32}',payload['challenge']):
        raise ValueError('Invalid challenge')
    if type(payload['rounds']) is not int or not 1<=payload['rounds']<=1000:
        raise ValueError('Rounds must be 1..1000')
    return payload

def solve(payload):
    validate_payload(payload)
    value=bytes.fromhex(payload['challenge'])
    for _ in range(payload['rounds']):value=hashlib.sha256(value).digest()
    return value.hex()
