"""Strict G JSON, typed digests, and bounded artifact resolution."""
from __future__ import annotations
import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from jsonschema import Draft202012Validator

VERSION = '1.0.0'
CANON = 'aurora.stage_g.canonical.ascii.v1'
CONTRACT_ID = 'urn:aurora:stage-g:contract:' + VERSION
MAX_BYTES = 1048576

class Rejected(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

def require(condition, code):
    if not condition:
        raise Rejected(code)

def _check(value, depth=0):
    require(depth <= 32, 'RESOURCE_LIMIT_EXCEEDED')
    if type(value) is bool:
        return
    if type(value) is int:
        require(0 <= value <= 9007199254740991, 'INTEGER_RANGE')
    elif type(value) is str:
        try:
            encoded = value.encode('utf-8')
        except UnicodeError as exc:
            raise Rejected('LONE_SURROGATE') from exc
        require(len(encoded) <= 262144, 'RESOURCE_LIMIT_EXCEEDED')
    elif type(value) is list:
        for item in value:
            _check(item, depth + 1)
    elif type(value) is dict:
        for key, item in value.items():
            require(type(key) is str and all(32 <= ord(c) <= 126 for c in key), 'NON_ASCII_KEY')
            _check(key, depth + 1)
            _check(item, depth + 1)
    else:
        raise Rejected('NON_G_JSON_VALUE')

def canonical(value):
    _check(value)
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')

def parse(raw: bytes, *, legacy=False):
    require(type(raw) is bytes and len(raw) <= MAX_BYTES, 'RESOURCE_LIMIT_EXCEEDED')
    require(not raw.startswith(b'\xef\xbb\xbf'), 'BOM_FORBIDDEN')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_KEY')
            result[key] = value
        return result
    def integer(value):
        if not legacy:
            require(re.fullmatch(r'0|[1-9][0-9]*', value), 'NEGATIVE_INTEGER')
        return int(value)
    def forbidden(_):
        raise Rejected('FLOAT_TOKEN')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_int=integer,
                           parse_float=forbidden, parse_constant=forbidden)
        if not legacy:
            _check(value)
        else:
            # Legacy semantics remain with their native validators; bound nesting first.
            def bounded(v, d=0):
                require(d <= 32, 'RESOURCE_LIMIT_EXCEEDED')
                if isinstance(v, dict):
                    for k, x in v.items():
                        k.encode('utf-8'); bounded(x, d + 1)
                elif isinstance(v, list):
                    for x in v: bounded(x, d + 1)
                elif isinstance(v, str):
                    require(len(v.encode('utf-8')) <= 262144, 'RESOURCE_LIMIT_EXCEEDED')
            bounded(value)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise Rejected('INVALID_JSON') from exc

def raw_digest(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()

def typed_digest(kind, tenant, body):
    return raw_digest(canonical(dict(domain='aurora.stage_g', contract_version=VERSION,
        canonicalization=CANON, kind=kind, tenant_id=tenant, body=body)))

@lru_cache(maxsize=1)
def schema():
    return json.loads((Path(__file__).parent / 'schemas/g-contract.schema.json').read_text(encoding="utf-8"))

def validate(value, kind=None):
    require(type(value) is dict, 'ARTIFACT_SCHEMA_INVALID')
    require(value.get('contract_version') == VERSION, 'UNSUPPORTED_CONTRACT')
    _check(value)
    require(len(canonical(value)) <= MAX_BYTES, 'RESOURCE_LIMIT_EXCEEDED')
    require(not next(Draft202012Validator(schema()).iter_errors(value), None), 'ARTIFACT_SCHEMA_INVALID')
    def calendar_values(v):
        if isinstance(v,dict):
            for key,item in v.items():
                if key in timestamp_fields():timestamp(item)
                else:calendar_values(item)
        elif isinstance(v,list):
            for item in v:calendar_values(item)
    calendar_values(value)
    if kind is not None:
        require(value['kind'] == kind, 'ARTIFACT_KIND_MISMATCH')
    require(typed_digest(value['kind'], value['tenant_id'], value['body']) == value['digest'], 'DIGEST_MISMATCH')
    return value

@lru_cache(maxsize=1)
def timestamp_fields():
    fields=set()
    def visit(v):
        if isinstance(v,dict):
            for name,definition in v.get('properties',{}).items():
                pattern=definition.get('pattern','')
                if pattern.startswith('^[0-9]{4}-[0-9]{2}-'):fields.add(name)
            for item in v.values():visit(item)
        elif isinstance(v,list):
            for item in v:visit(item)
    visit(schema());return frozenset(fields)

def artifact(kind, tenant, body, artifact_id):
    return validate(dict(contract_version=VERSION, canonicalization=CANON, tenant_id=tenant,
        artifact_id=artifact_id, kind=kind, body=body, digest=typed_digest(kind, tenant, body)))

def reference(value):
    return dict(contract_id=CONTRACT_ID, artifact_id=value['artifact_id'], digest=value['digest'], digest_kind='G_TYPED')

def timestamp(value):
    require(type(value) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z', value), 'TIMESTAMP_CALENDAR_INVALID')
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise Rejected('TIMESTAMP_CALENDAR_INVALID') from exc

def utc(value):
    require(value.tzinfo is not None, 'TIMESTAMP_CALENDAR_INVALID')
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

def decode64(value, limit=MAX_BYTES):
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise Rejected('BASE64_INVALID') from exc
    require(len(raw) <= limit and base64.b64encode(raw).decode() == value, 'BASE64_INVALID')
    return raw

class Artifacts:
    def __init__(self, values, tenant):
        require(len(values) <= 1000, 'RESOURCE_LIMIT_EXCEEDED')
        self.tenant = tenant
        self.items = {}
        total = 0
        for value in values:
            value = parse(canonical(value))
            validate(value)
            require(value['tenant_id'] == tenant, 'TENANT_MISMATCH')
            key = value['artifact_id']
            require(key not in self.items or self.items[key] == value, 'ARTIFACT_ID_CONFLICT')
            self.items[key] = value
            total += len(canonical(value))
        require(total <= 16777216, 'RESOURCE_LIMIT_EXCEEDED')

    def resolve(self, ref, kind=None):
        require(ref.get('contract_id') == CONTRACT_ID and ref.get('digest_kind') == 'G_TYPED', 'REFERENCE_CONTRACT_MISMATCH')
        value = self.items.get(ref.get('artifact_id'))
        require(value is not None, 'DEPENDENCY_MISSING')
        require(reference(value) == ref, 'REFERENCE_MISMATCH')
        if kind: require(value['kind'] == kind, 'ARTIFACT_KIND_MISMATCH')
        return value

    def signatures(self, value, role):
        return [a for a in self.items.values() if a['kind'] == 'signature'
                and a['body']['signed_ref'] == reference(value) and a['body']['role'] == role]
