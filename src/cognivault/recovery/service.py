"""Full snapshots and isolated restore, exclusively executed by the Gateway."""
from contextlib import closing, contextmanager
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import re
import shutil
import sqlite3
import json
import tomllib
from uuid import uuid4
from ..contracts import GatewayError
from ..migration.inventory import InventoryError
from ..provenance import utc_now
from .io import (copy_file,digest_file,encoded,fail,files,read_json,relative,reserve,
                 safe,signature,sqlite_copy,sqlite_proof,sqlite_size,restore_capacity,write_json)


def operation(method):
    def run(*args,**kwargs):
        try: return method(*args,**kwargs)
        except GatewayError: raise
        except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,InventoryError):
            fail('STORAGE_UNAVAILABLE')
    return run


def key(value):
    if type(value) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',value): fail('INVALID_ARGUMENT')
    return value


def roots(g):
    g._require_capability('admin')
    cfg=g.config
    if cfg.recovery_root is None: fail('STORAGE_UNAVAILABLE')
    root=safe(cfg.recovery_root,directory=True,private=True)
    protected=[cfg.study_root,cfg.history_database,cfg.asset_root,cfg.asset_database,
               cfg.recovery_sidecar_root,cfg.history_migration_inbox,cfg.qmd_snapshot_config,cfg.qmd_snapshot_index]
    for p in protected:
        if p is not None:
            other=Path(p).resolve()
            if root.is_relative_to(other) or other.is_relative_to(root): fail('OUTSIDE_ALLOWLIST')
    if len(root.parts)<3: fail('OUTSIDE_ALLOWLIST')
    if cfg.recovery_sidecar_root:
        safe(cfg.recovery_sidecar_root,directory=True,private=True)
        for p in (cfg.study_root,cfg.history_database,cfg.asset_root,cfg.asset_database):
            if p is not None:
                side=cfg.recovery_sidecar_root.resolve();other=Path(p).resolve()
                if side.is_relative_to(other) or other.is_relative_to(side): fail('OUTSIDE_ALLOWLIST')
    return root


def inventory(g):
    cfg=g.config;entries=[];excluded=[]
    databases=[('history.sqlite3',cfg.history_database),('assets.sqlite3',cfg.asset_database),
               ('qmd-index.sqlite3',cfg.qmd_snapshot_index if cfg.recovery_include_study else None)]
    if cfg.recovery_sidecar_root: databases.append(('sidecar/ledger.sqlite3',cfg.recovery_sidecar_root/'ledger.sqlite3'))
    for rel,path in databases:
        if path is None: continue
        path=safe(path)
        if not path.is_file(): fail('STORAGE_UNAVAILABLE')
        for suffix in ('-wal','-shm','-journal'):
            aux=Path(str(path)+suffix)
            if aux.exists(): safe(aux)
        entries.append({'relative':rel,'source':path,'root':path.parent,'database':True,'signature':signature(path)})
    database_paths={e['source'] for e in entries}
    aux={Path(str(p)+suffix) for p in database_paths for suffix in ('-wal','-shm','-journal')}
    for component,path in (('study',cfg.study_root if cfg.recovery_include_study else None),('objects',cfg.asset_root),('sidecar',cfg.recovery_sidecar_root)):
        if path is None: continue
        exclusions=cfg.recovery_sidecar_exclusions if component=='sidecar' else ()
        for rel,source,sig in files(path,excluded=exclusions):
            if source in database_paths or source in aux: continue
            entries.append({'relative':component+'/'+rel,'source':source,'root':path,'database':False,'signature':sig})
        excluded.extend(component+'/'+relative(v) for v in exclusions)
    for rel,path in (('gateway-original.toml',cfg.gateway_config_file),('qmd-config.yml',cfg.qmd_snapshot_config if cfg.recovery_include_study else None)):
        if path:
            path=safe(path)
            if not path.is_file(): fail('STORAGE_UNAVAILABLE')
            entries.append({'relative':rel,'source':path,'root':path.parent,'database':False,'signature':signature(path)})
    entries.sort(key=lambda e:e['relative'])
    if len({e['relative'].casefold() for e in entries})!=len(entries): fail()
    return entries,excluded


def inventory_identity(entries):
    return [(e['relative'],str(e['source']),e['signature']) for e in entries]


@operation
def plan(g):
    root=roots(g);entries,exclusions=inventory(g)
    ancestor=root
    while not ancestor.exists(): ancestor=ancestor.parent
    sizes=[(e,sqlite_size(e['source']) if e['database'] else e['signature'][2]) for e in entries]
    required=sum(size for _,size in sizes)
    components={}
    for entry,size in sizes:
        category=entry['relative'].split('/')[0]
        components[category]=components.get(category,0)+size
    state={'published_directories':0,'incomplete_attempts':0,'incomplete_bytes':0}
    published_bytes=0
    snapshots=safe(root/'snapshots',directory=True,private=True)
    if snapshots.exists():
        attempts=list(snapshots.iterdir())
        if len(attempts)>10000: fail('PAYLOAD_TOO_LARGE')
        for attempt in attempts:
            safe(attempt,directory=True,private=True)
            if '.partial-' in attempt.name:
                key(attempt.name.split('.partial-',1)[0]);state['incomplete_attempts']+=1
                state['incomplete_bytes']+=sum(sig[2] for _,_,sig in files(attempt))
            else:
                key(attempt.name);state['published_directories']+=1
                published_bytes+=sum(sig[2] for _,_,sig in files(attempt))
    return {'file_count':len(entries),'required_bytes':required,'free_bytes':shutil.disk_usage(ancestor).free,
            'component_bytes':components,'recovery_state':state,
            'published_snapshot_bytes':published_bytes,
            'restore_capacity':{**restore_capacity(root,required,components.get('qmd-index.sqlite3',0)),
                                'estimate_basis':'configured_inputs'},
            'excluded_sidecar_paths':len(exclusions),'configured_study':g.config.study_root is not None and g.config.recovery_include_study,
            'configured_assets':g.config.asset_root is not None,'configured_ledger':g.config.recovery_sidecar_root is not None}


def history_proof(g):
    canonical=g.canonical_history_summary();proof=g.verify_canonical_history()
    if not proof['verified']: fail()
    sources=g.history_source_summary();verified=[];offset=0
    while True:
        page=g.search_history_sources(offset=offset,limit=100)
        for source in page['sources']:
            p=g.verify_history_source(source['source_id'])
            if not p['verified']: fail()
            verified.append([p['source_id'],p['sha256'],p['byte_count'],p['provenance']])
        if not page['has_more']: break
        offset+=len(page['sources'])
    if len(verified)!=sources['source_records']: fail()
    return {'canonical':canonical,'sources':sources,'canonical_sha256':proof['canonical_sha256'],
            'source_proof_sha256':hashlib.sha256(encoded(sorted(verified))).hexdigest()}


def public(manifest,digest,*,reused=False):
    return {'verified':True,'reused':reused,'snapshot_ref':'recovery://snapshot/'+manifest['snapshot_key'],
            'scope':'full_configured_domains' if manifest['components']['study'] else 'mutable_domains_supplement',
            'manifest_sha256':digest,'file_count':len(manifest['files']),
            'total_bytes':sum(e['bytes'] for e in manifest['files']),**manifest['history_proof']}


def check_payload(folder,manifest):
    if type(manifest) is not dict or manifest.get('schema_version')!=1 or type(manifest.get('files')) is not list: fail()
    expected={}
    for item in manifest['files']:
        if type(item) is not dict or not {'relative','sha256','bytes','database'}<=set(item): fail()
        rel=relative(item['relative'])
        if rel.casefold() in {r.casefold() for r in expected} or type(item['bytes']) is not int \
                or item['bytes']<0 or type(item['database']) is not bool \
                or type(item['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}',item['sha256']): fail()
        expected[rel]=item
    actual={rel for rel,_,_ in files(folder/'payload')}
    if actual!=set(expected): fail()
    for rel,item in expected.items():
        path=folder/'payload'/rel
        if digest_file(path,folder/'payload')!=(item['sha256'],item['bytes']): fail()
        if item['database'] and sqlite_proof(path)!=item.get('sqlite_proof'): fail()


def snapshot(g,snapshot_key):
    root=roots(g);snapshot_key=key(snapshot_key);folder=safe(root/'snapshots'/snapshot_key,directory=True,private=True)
    manifest=read_json(folder/'manifest.json')
    digest=hashlib.sha256(encoded(manifest)).hexdigest()
    checksum=safe(folder/'manifest.sha256')
    if checksum.stat().st_size!=64: fail()
    saved=checksum.read_text(encoding='ascii')
    if saved!=digest or manifest.get('snapshot_key')!=snapshot_key: fail()
    if read_json(root/'catalog'/ (snapshot_key+'.json'))!={'manifest_sha256':digest}: fail()
    validate_manifest(g,manifest)
    check_payload(folder,manifest)
    if history_proof(restored_gateway(g,folder))!=manifest['history_proof']: fail()
    return folder,manifest,digest


def validate_manifest(g,manifest):
    if type(manifest) is not dict or set(manifest)!={'schema_version','snapshot_key','recorded_at','files','history_proof','excluded_sidecar_paths','components'}: fail()
    if type(manifest['schema_version']) is not int or manifest['schema_version']!=1: fail()
    cfg=g.config
    components={'study':cfg.study_root is not None and cfg.recovery_include_study,'objects':cfg.asset_root is not None,'sidecar':cfg.recovery_sidecar_root is not None}
    if type(manifest['components']) is not dict or set(manifest['components'])!=set(components) \
            or any(type(v) is not bool for v in manifest['components'].values()) or manifest['components']!=components: fail()
    if manifest['excluded_sidecar_paths']!=['sidecar/'+relative(v) for v in cfg.recovery_sidecar_exclusions]: fail()
    required={'history.sqlite3':True}
    for rel,p,db in [('assets.sqlite3',cfg.asset_database,True),('qmd-index.sqlite3',cfg.qmd_snapshot_index if cfg.recovery_include_study else None,True),
                     ('qmd-config.yml',cfg.qmd_snapshot_config if cfg.recovery_include_study else None,False),('gateway-original.toml',cfg.gateway_config_file,False),
                     ('sidecar/ledger.sqlite3',cfg.recovery_sidecar_root,True)]:
        if p is not None: required[rel]=db
    if type(manifest['files']) is not list: fail()
    for rel,db in required.items():
        matches=[v for v in manifest['files'] if type(v) is dict and v.get('relative')==rel]
        if len(matches)!=1 or matches[0].get('database') is not db: fail()


@operation
def create(g,snapshot_key):
    root=roots(g);snapshot_key=key(snapshot_key)
    final=safe(root/'snapshots'/snapshot_key,directory=True,private=True)
    if final.exists():
        _,manifest,digest=snapshot(g,snapshot_key);return public(manifest,digest,reused=True)
    details=plan(g)
    if details['free_bytes']<details['required_bytes']*2+64*1024*1024: fail('STORAGE_UNAVAILABLE')
    entries,excluded=inventory(g)
    root.mkdir(parents=True,exist_ok=True);parent=root/'snapshots';parent.mkdir(exist_ok=True);safe(parent,directory=True,private=True)
    stage=parent/(snapshot_key+'.partial-'+uuid4().hex);stage.mkdir();(stage/'payload').mkdir()
    for component,p in [('study',g.config.study_root if g.config.recovery_include_study else None),('objects',g.config.asset_root),('sidecar',g.config.recovery_sidecar_root)]:
        if p is not None:(stage/'payload'/component).mkdir()
    saved=[]
    with reserve([e['source'] for e in entries if e['database']]):
        entries,excluded=inventory(g);before=inventory_identity(entries)
        details=plan(g)
        if details['free_bytes']<details['required_bytes']*2+64*1024*1024: fail('STORAGE_UNAVAILABLE')
        hproof=history_proof(g)
        for entry in entries:
            source=entry['source'];target=stage/'payload'/entry['relative']
            if entry['database']:
                origin=sqlite_proof(source);sqlite_copy(source,target)
                if sqlite_proof(target)!=origin: fail()
                digest,size=digest_file(target,stage/'payload')
            else:
                digest,size=copy_file(source,entry['root'],target);origin=None
            saved.append({'relative':entry['relative'],'original_location':str(source),'sha256':digest,
                          'bytes':size,'database':entry['database'],**({'sqlite_proof':origin} if origin else {})})
        if before!=inventory_identity(inventory(g)[0]): fail()
        # Rehash source files: Windows mtime can be restored after a same-size write.
        for entry,item in zip(entries,saved):
            if not entry['database'] and digest_file(entry['source'],entry['root'])!=(item['sha256'],item['bytes']): fail()
        if hproof!=history_proof(g): fail()
    manifest={'schema_version':1,'snapshot_key':snapshot_key,'recorded_at':utc_now(),'files':saved,
              'history_proof':hproof,'excluded_sidecar_paths':excluded,
              'components':{'study':g.config.study_root is not None and g.config.recovery_include_study,'objects':g.config.asset_root is not None,
                            'sidecar':g.config.recovery_sidecar_root is not None}}
    digest=write_json(stage/'manifest.json',manifest)
    with (stage/'manifest.sha256').open('x',encoding='ascii') as stream:stream.write(digest);stream.flush();os.fsync(stream.fileno())
    check_payload(stage,manifest)
    validate_manifest(g,manifest)
    if history_proof(restored_gateway(g,stage))!=hproof: fail()
    catalog=safe(root/'catalog',directory=True,private=True);catalog.mkdir(exist_ok=True)
    write_json(catalog/(snapshot_key+'.json'),{'manifest_sha256':digest})
    os.rename(stage,final)
    return public(manifest,digest)


def restored_gateway(g,target):
    from ..adapters.history import SQLiteHistoryBackend
    from ..adapters.documents import SQLiteDocumentStore
    from ..gateway import Gateway
    payload=target/'payload'
    cfg=replace(g.config,history_database=payload/'history.sqlite3',asset_database=payload/'assets.sqlite3',
                asset_root=payload/'objects',study_root=payload/'study' if g.config.recovery_include_study else None,history_migration_inbox=None,
                recovery_root=None,recovery_sidecar_root=None,gateway_config_file=None,
                qmd_snapshot_config=payload/'qmd-config.yml',qmd_snapshot_index=payload/'qmd-index.sqlite3')
    store=SQLiteDocumentStore(cfg.asset_root,cfg.asset_database) if cfg.asset_database.is_file() else None
    return Gateway(cfg,None,SQLiteHistoryBackend(cfg.history_database),document_store=store,capabilities=frozenset({'read'}))


def prepare_restore_config(g,target):
    """Persist private, relocatable data bindings; software dependencies remain explicit."""
    payload=target/'payload';cfg=g.config
    raw=tomllib.loads(safe(payload/'gateway-original.toml').read_text(encoding='utf-8'))
    runtime=raw.get('study',{}).get('qmd_runtime')
    # The legacy executable mode uses ambient QMD state and cannot prove isolation.
    if runtime is None or cfg.qmd_snapshot_index is None or cfg.qmd_snapshot_config is None: fail('STUDY_UNAVAILABLE')
    data='[gateway]\nversion='+json.dumps(cfg.gateway_version)+'\n[study]\nroot='+json.dumps(str(payload/'study')) \
        +'\nqmd_collection="studyvault"\nqmd_version="2.8.3"\n'
    if runtime is not None:
        relocated={'collections':{'studyvault':{'path':str(payload/'study'),'pattern':'**/*'}}}
        yaml=target/'qmd-restored.yml'
        if yaml.exists():
            if read_json(yaml)!=relocated: fail()
        else:write_json(yaml,relocated)
        data+='[study.qmd_runtime]\n'
        for field,value in {'node_executable':runtime['node_executable'],'cli_entrypoint':runtime['cli_entrypoint'],
                            'config':str(yaml),'index':str(payload/'qmd-index.sqlite3')}.items():
            data+=field+'='+json.dumps(value)+'\n'
    data+='[history]\nbackend="sqlite"\ndatabase='+json.dumps(str(payload/'history.sqlite3'))+'\n'
    if cfg.asset_database is not None:
        data+='[assets]\nbackend="sqlite"\nroot='+json.dumps(str(payload/'objects'))+'\ndatabase='+json.dumps(str(payload/'assets.sqlite3'))+'\n'
    data+='[permissions]\ncapabilities=["read"]\n'
    path=safe(target/'gateway-restored.toml')
    if path.exists():
        if path.read_text(encoding='utf-8')!=data: fail()
    else:
        with path.open('x',encoding='utf-8',newline='\n') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    return path


def domain_readback(isolated,manifest):
    """Locate bounded IDs inside the restored adapter; prove reads through Gateway APIs."""
    item=next((v for v in manifest['files'] if v['relative']=='assets.sqlite3'),None)
    tables=item['sqlite_proof']['tables'] if item else {}
    count=lambda name:tables.get(name,{}).get('rows',0)
    asset_count=count('assets');document_count=count('documents')
    source_count=count('wrong_sources');analysis_count=count('wrong_analyses')
    default='empty' if item else 'not_configured'
    proof={'assets':{'state':default,'records':asset_count,'readback_verified':False},
           'documents':{'state':default,'records':document_count,'readback_verified':False,'no_result_verified':False},
           'wrong_answers':{'state':default,'source_records':source_count,'analysis_records':analysis_count,
                            'source_readback_verified':False,'analysis_version_readback_verified':False,
                            'analysis_versions_read_back':0,'no_result_verified':False}}
    if item is None:return proof
    document_id=asset_id=source=None
    # No content/title is inspected here. Counts already have whole-table logical
    # proof; these read-only LIMIT 1 lookups merely choose API readback candidates.
    with closing(isolated._documents()._connect()) as c:
        c.execute('BEGIN')
        if document_count:document_id=c.execute('SELECT uri FROM documents ORDER BY uri LIMIT 1').fetchone()[0]
        if asset_count:asset_id=c.execute('SELECT uri FROM assets ORDER BY uri LIMIT 1').fetchone()[0]
        if source_count:
            source=c.execute('SELECT s.source_id,(SELECT MAX(version) FROM wrong_analyses a WHERE a.source_id=s.source_id) AS latest '
                             'FROM wrong_sources s ORDER BY latest DESC,s.source_id LIMIT 1').fetchone()
    query='P13_RECOVERY_NO_RESULT_'+uuid4().hex
    if document_id:
        document=isolated.fetch_document(document_id)['document']
        if document['document_uri']!=document_id:fail()
        asset_id=document['asset_uri']
        proof['documents'].update(state='verified',readback_verified=True)
    else:
        result=isolated.search_documents(query,limit=1)
        if result['total']!=0 or result['results']:fail()
        proof['documents']['no_result_verified']=True
    if asset_id:
        asset=isolated.fetch_asset(asset_id,length=1)
        if asset['asset_uri']!=asset_id or asset['size']<1 or not asset['content_base64']:fail()
        proof['assets'].update(state='verified',readback_verified=True)
    wrong=proof['wrong_answers']
    if source:
        bundle=isolated.get_wrong_answer_bundle(source[0],limit=1)
        if bundle['source']['source_id']!=source[0]:fail()
        wrong.update(state='source_only',source_readback_verified=True)
        if source[1] is not None:
            if bundle['total']<1 or len(bundle['analyses'])!=1 or bundle['analyses'][0]['version']!=source[1]:fail()
            wrong.update(state='verified',analysis_version_readback_verified=True,analysis_versions_read_back=1)
        elif bundle['total']!=0 or bundle['analyses']:fail()
    else:
        result=isolated.search_wrong_answers(query,limit=1)
        if result['total']!=0 or result['results']:fail()
        wrong['no_result_verified']=True
    return proof


def restore_snapshot(g,folder,manifest,digest):
    root=roots(g);target=safe(root/'restores'/manifest['snapshot_key'],directory=True,private=True)
    capacity=restore_capacity(root,sum(item['bytes'] for item in manifest['files']),
                              next((v['bytes'] for v in manifest['files'] if v['relative']=='qmd-index.sqlite3'),0),
                              reuse=target.exists())
    if not capacity['sufficient']:fail('STORAGE_UNAVAILABLE')
    if not target.exists():
        target.parent.mkdir(parents=True,exist_ok=True);safe(target.parent,directory=True,private=True)
        target.mkdir();(target/'payload').mkdir()
        for component,included in manifest['components'].items():
            if included:(target/'payload'/component).mkdir()
        for item in manifest['files']:copy_file(folder/'payload'/item['relative'],folder/'payload',target/'payload'/item['relative'])
        write_json(target/'restore-origin.json',{'manifest_sha256':digest})
    elif not (target/'restore-origin.json').is_file() or read_json(target/'restore-origin.json')!={'manifest_sha256':digest}: fail()
    check_payload(target,manifest)
    isolated=restored_gateway(g,target);study_verified=False
    if g.config.gateway_config_file is not None and manifest['components']['study']:
        from ..runtime import load_gateway_from_config
        isolated=load_gateway_from_config(prepare_restore_config(g,target))
        result=isolated.search_study('P13_RECOVERY_NO_RESULT_20261002_37c6',limit=1)
        study_verified=not result['results']
        if not study_verified: fail()
    proof=history_proof(isolated)
    if proof!=manifest['history_proof']: fail()
    domains=domain_readback(isolated,manifest)
    ledger=next((i for i in manifest['files'] if i['relative']=='sidecar/ledger.sqlite3'),None)
    if ledger and sqlite_proof(target/'payload/sidecar/ledger.sqlite3')!=ledger['sqlite_proof']: fail()
    receipt={'verified':True,'manifest_sha256':digest,'history_proof':proof,'ledger_verified':ledger is not None,
             'study_read_verified':study_verified,'domain_readback':domains}
    if not (target/'restore-proof.json').exists(): write_json(target/'restore-proof.json',receipt)
    return {'isolated_restore_verified':True,'ledger_verified':ledger is not None,'study_read_verified':study_verified,
            'study_restore_state':'verified' if study_verified else 'excluded_by_configuration' if not manifest['components']['study'] else 'not_verified',
            'restore_capacity':capacity,'domain_readback':domains,'restore_ref':'recovery://restore/'+manifest['snapshot_key']}


@operation
def verify(g,snapshot_key,*,restore=False):
    if type(restore) is not bool: fail('INVALID_ARGUMENT')
    folder,manifest,digest=snapshot(g,snapshot_key)
    result=public(manifest,digest,reused=True)
    if restore: result.update(restore_snapshot(g,folder,manifest,digest))
    return result
