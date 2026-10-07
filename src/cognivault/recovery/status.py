"""Finite, metadata-only observation; never proof of payload or writer ownership."""
import hashlib
import json
import os
import re
from .io import MAX_FILES, MAX_MANIFEST, encoded, fail, relative, safe, signature
from .service import key, operation, roots, validate_manifest
from ..contracts import GatewayError
from ..migration.inventory import _open_verified_source

MAX_OBSERVATION_ENTRIES = 10_000
DIGEST = re.compile(r'[0-9a-f]{64}')


def digest(value):
    return type(value) is str and DIGEST.fullmatch(value) is not None


def identity(path, *, directory=False):
    safe(path,directory=directory,private=True)
    try:return signature(path)
    except FileNotFoundError:return None


def metadata(path, limit):
    before=identity(path)
    if before is None:return None
    if before[2]>limit:fail('PAYLOAD_TOO_LARGE')
    with _open_verified_source(path,path.parent) as stream:
        info=os.fstat(stream.fileno())
        if info.st_nlink!=1:fail('OUTSIDE_ALLOWLIST')
        data=stream.read(limit+1)
        after=os.fstat(stream.fileno())
    if len(data)>limit:fail('PAYLOAD_TOO_LARGE')
    current=identity(path)
    opened=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
    closed=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
    return before,data,before!=opened or opened!=closed or closed!=current


def observe(root, snapshot_key):
    parent=root/'snapshots';catalog=root/'catalog';folder=parent/snapshot_key
    dirs=tuple(identity(p,directory=True) for p in (root,parent,catalog,folder))
    count=0;complete=True;unexplained=False
    if dirs[1] is not None:
        with os.scandir(parent) as entries:
            for index,entry in enumerate(entries):
                if index>=MAX_OBSERVATION_ENTRIES:complete=False;break
                if entry.name.startswith(snapshot_key+'.partial-'):
                    safe(parent/entry.name,directory=True,private=True)
                    if re.fullmatch(re.escape(snapshot_key)+r'\.partial-[0-9a-f]{32}',entry.name):count+=1
                    else:unexplained=True
    blobs=(metadata(folder/'manifest.json',MAX_MANIFEST),metadata(folder/'manifest.sha256',64),
           metadata(catalog/(snapshot_key+'.json'),MAX_MANIFEST))
    return dirs,blobs,count,complete,unexplained


def parse(data):
    def unique(pairs):
        result={}
        for name,value in pairs:
            if name in result:raise ValueError('duplicate metadata key')
            result[name]=value
        return result
    def invalid(_):raise ValueError('nonfinite metadata')
    return json.loads(data,object_pairs_hook=unique,parse_constant=invalid)


def manifest_shape(value, snapshot_key):
    if type(value) is not dict or set(value)!={'schema_version','snapshot_key','recorded_at','files','history_proof','excluded_sidecar_paths','components'}:return False
    if type(value['schema_version']) is not int or value['schema_version']!=1 or value['snapshot_key']!=snapshot_key:return False
    if type(value['recorded_at']) is not str or not value['recorded_at'] or type(value['history_proof']) is not dict:return False
    components=value['components']
    if type(components) is not dict or set(components)!={'study','objects','sidecar'} or any(type(v) is not bool for v in components.values()):return False
    exclusions=value['excluded_sidecar_paths']
    if type(exclusions) is not list or any(type(v) is not str or not v.startswith('sidecar/') for v in exclusions):return False
    for name in exclusions:relative(name)
    if len({v.casefold() for v in exclusions})!=len(exclusions):return False
    items=value['files'];seen=set()
    if type(items) is not list:return False
    if len(items)>MAX_FILES:fail('PAYLOAD_TOO_LARGE')
    for item in items:
        if type(item) is not dict or not {'relative','sha256','bytes','database','original_location'}<=set(item) or set(item)-{'relative','sha256','bytes','database','original_location','sqlite_proof'}:return False
        name=relative(item['relative']).casefold()
        if name in seen:return False
        seen.add(name)
        if type(item['bytes']) is not int or item['bytes']<0 or not digest(item['sha256']) or type(item['database']) is not bool or type(item['original_location']) is not str:return False
        if item['database']:
            proof=item.get('sqlite_proof')
            if type(proof) is not dict or set(proof)!={'schema_sha256','tables','user_version'} or not digest(proof['schema_sha256']) or type(proof['user_version']) is not int or type(proof['tables']) is not dict:return False
            for table,row in proof['tables'].items():
                if type(table) is not str or type(row) is not dict or set(row)!={'rows','sha256'} or type(row['rows']) is not int or row['rows']<0 or not digest(row['sha256']):return False
        elif 'sqlite_proof' in item:return False
    return any(item['relative']=='history.sqlite3' and item['database'] for item in items)


@operation
def status(g, snapshot_key, expected_manifest_sha256=None):
    root=roots(g);snapshot_key=key(snapshot_key)
    if expected_manifest_sha256 is not None and not digest(expected_manifest_sha256):fail('INVALID_ARGUMENT')
    out={'kind':'recovery_snapshot','snapshot_ref':'recovery://snapshot/'+snapshot_key,
         'state':'not_observed','metadata_consistent':False,'artifact_integrity':'not_verified',
         'verification_required':False,'expected_manifest_match':'not_requested' if expected_manifest_sha256 is None else 'unavailable',
         'operation_binding':'unavailable','owner':{'state':'unknown'},
         'resume':{'supported':False,'reason':'NO_FENCED_RESUME_HANDLER'},
         'partial_observation':{'count':0,'complete':False},'reason_codes':[]}
    try:
        first=observe(root,snapshot_key);second=observe(root,snapshot_key)
    except FileNotFoundError:
        out.update(state='observation_changed',verification_required=True,reason_codes=['CONCURRENT_CHANGE'])
        return out
    dirs,blobs,count,complete,unexplained=second
    out['partial_observation']={'count':count,'complete':complete}
    out['verification_required']=dirs[3] is not None or any(v is not None for v in blobs)
    if first!=second or any(v is not None and v[2] for v in blobs):
        out.update(state='observation_changed',reason_codes=['CONCURRENT_CHANGE']);return out
    reasons=out['reason_codes']
    if count:reasons.append('PARTIAL_OBSERVED')
    if unexplained:reasons.append('UNEXPLAINED_ARTIFACT')
    if not complete:reasons.append('OBSERVATION_INCOMPLETE')
    if dirs[3] is None:
        if blobs[2] is not None:reasons.append('CATALOG_WITHOUT_PUBLISHED_ARTIFACT')
        if reasons:out['state']='needs_action'
        return out
    out['state']='needs_action'
    if any(v is None for v in blobs):
        reasons.append('CATALOG_MISSING' if blobs[2] is None else 'METADATA_MISSING');return out
    try:
        manifest=parse(blobs[0][1]);catalog=parse(blobs[2][1])
        if not manifest_shape(manifest,snapshot_key) or type(catalog) is not dict or set(catalog)!={'manifest_sha256'} or not digest(catalog['manifest_sha256']):
            reasons.append('METADATA_INVALID');return out
        value=hashlib.sha256(encoded(manifest)).hexdigest()
    except (ValueError,UnicodeError,RecursionError,GatewayError) as error:
        if isinstance(error,GatewayError) and error.code!='INVALID_ARGUMENT':raise
        reasons.append('METADATA_INVALID');return out
    out['manifest_sha256']=value
    if expected_manifest_sha256 is not None:
        out['expected_manifest_match']='matches' if expected_manifest_sha256==value else 'mismatch'
        if expected_manifest_sha256!=value:reasons.append('EXPECTED_MANIFEST_MISMATCH')
    if blobs[1][1]!=value.encode('ascii') or catalog['manifest_sha256']!=value:
        reasons.append('METADATA_MISMATCH');return out
    try:validate_manifest(g,manifest)
    except GatewayError:
        reasons.append('CONFIGURATION_MISMATCH');return out
    out['metadata_consistent']=True
    if 'EXPECTED_MANIFEST_MISMATCH' not in reasons:out['state']='published_unverified'
    return out
