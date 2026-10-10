"""Private recovery uses only invented stores in pytest temporary directories."""
from contextlib import closing
from dataclasses import replace
import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
import pytest
import anyio
from mcp.shared.memory import create_connected_server_and_client_session
from cognivault.transports.mcp_stdio import create_mcp_server
from cognivault.config import AppConfig
from cognivault.contracts import GatewayError
from cognivault.gateway import Gateway
from cognivault.adapters.history import SQLiteHistoryBackend
from cognivault.adapters.history_sources import SourceEvidenceStore, SourceFileInput
from cognivault.adapters.documents import SQLiteDocumentStore, DocumentInput


def setup(tmp_path, *, documents=True):
    production=tmp_path/'production';production.mkdir()
    study=production/'study';study.mkdir();(study/'invented.txt').write_bytes(b'Invented Study evidence')
    objects=production/'objects';objects.mkdir()
    db=production/'history.sqlite3';SQLiteHistoryBackend(db).initialize()
    assets=production/'assets.sqlite3';store=SQLiteDocumentStore(objects,assets)
    if documents:
        store.ingest_documents([DocumentInput('Invented document','text/plain',b'Invented text')])
    else:
        with closing(store._connect(write=True)) as c:
            store._schema(c);c.commit()
    sidecar=tmp_path/'sidecar';sidecar.mkdir()
    inbox=sidecar/'source-inbox';inbox.mkdir()
    with closing(sqlite3.connect(sidecar/'ledger.sqlite3')) as c:
        c.execute('CREATE TABLE audit(id INTEGER PRIMARY KEY, disposition TEXT)')
        c.execute("INSERT INTO audit VALUES(1,'imported')");c.commit()
    (sidecar/'receipt.json').write_text('{"synthetic":true}')
    runtime=sidecar/'runtime';runtime.mkdir();(runtime/'pyvenv.cfg').write_text('synthetic runtime')
    recovery=tmp_path/'private-recovery';recovery.mkdir()
    cfg=AppConfig('0.6.0',study_root=study,history_database=db,asset_root=objects,
                  asset_database=assets,history_migration_inbox=inbox,recovery_root=recovery,
                  recovery_sidecar_root=sidecar,recovery_sidecar_exclusions=('runtime',))
    g=Gateway(cfg,None,SQLiteHistoryBackend(db),document_store=store,
              capabilities=frozenset({'read','ingest','admin'}))
    raw=b'{"type":"session_meta","payload":{"id":"invented-session"}}\n{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Invented message"}]}}\n'
    SourceEvidenceStore(db).import_bytes(SourceFileInput('codex','jsonl','raw_session',raw,hashlib.sha256(raw).hexdigest()))
    g.normalize_history_sources(g.history_normalization_snapshot()['source_set_sha256'])
    return g


def test_long_recovery_keeps_native_mcp_ping_responsive(tmp_path,monkeypatch):
    g=setup(tmp_path)
    started=threading.Event()

    def slow_snapshot(snapshot_key):
        started.set()
        time.sleep(1)
        return {'verified':True}

    monkeypatch.setattr(g,'create_recovery_snapshot',slow_snapshot)

    async def run():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            async with anyio.create_task_group() as group:
                async def create():
                    result=await client.call_tool('create_recovery_snapshot',{'snapshot_key':'invented-slow'})
                    assert result.structuredContent['verified']
                begin=time.monotonic()
                group.start_soon(create)
                while not started.is_set():await anyio.sleep(0.001)
                await client.send_ping()
                assert time.monotonic()-begin<0.5
    anyio.run(run)


def test_full_snapshot_restore_provenance_ledger_and_reuse(tmp_path):
    g=setup(tmp_path)
    before=g.canonical_history_summary()
    plan=g.recovery_snapshot_plan()
    assert plan['file_count']>=5 and plan['required_bytes']>0
    first=g.create_recovery_snapshot('invented-one')
    assert first['verified'] and not first['reused']
    assert first['canonical']['messages']==1 and first['sources']['source_records']==1
    rerun=g.create_recovery_snapshot('invented-one')
    assert rerun['reused'] and rerun['manifest_sha256']==first['manifest_sha256']
    restored=g.verify_recovery_snapshot('invented-one',restore=True)
    assert restored['verified'] and restored['isolated_restore_verified']
    assert restored['ledger_verified'] and restored['canonical']['messages']==1
    assert restored['canonical_sha256']==first['canonical_sha256']
    assert g.verify_recovery_snapshot('invented-one',restore=True)['isolated_restore_verified']
    assert g.canonical_history_summary()==before
    assert str(tmp_path) not in json.dumps(restored)


@pytest.mark.parametrize('key',['../escape','a/b','A','',True,'x'*65])
def test_invalid_key_never_creates_snapshot(tmp_path,key):
    g=setup(tmp_path)
    with pytest.raises(GatewayError):g.create_recovery_snapshot(key)
    assert not (g.config.recovery_root/'snapshots').exists()


def test_admin_and_configuration_are_required(tmp_path):
    g=setup(tmp_path);g.capabilities=frozenset({'read'})
    with pytest.raises(GatewayError) as error:g.recovery_snapshot_plan()
    assert error.value.code=='PERMISSION_DENIED'
    g.capabilities=frozenset({'read','admin'});g.config=replace(g.config,recovery_root=None)
    with pytest.raises(GatewayError):g.recovery_snapshot_plan()


@pytest.mark.parametrize('target',['study_root','asset_root','history_database','recovery_sidecar_root'])
def test_recovery_root_cannot_overlap_inputs(tmp_path,target):
    g=setup(tmp_path);g.config=replace(g.config,recovery_root=getattr(g.config,target))
    with pytest.raises(GatewayError):g.recovery_snapshot_plan()


def test_git_root_is_rejected(tmp_path):
    g=setup(tmp_path);(g.config.recovery_root/'.git').mkdir()
    with pytest.raises(GatewayError):g.recovery_snapshot_plan()


@pytest.mark.parametrize('corruption',['changed','missing','extra','manifest','database'])
def test_snapshot_corruption_cannot_pass_or_restore(tmp_path,corruption):
    g=setup(tmp_path);g.create_recovery_snapshot('invented-one')
    snapshot=g.config.recovery_root/'snapshots/invented-one'
    study=snapshot/'payload/study/invented.txt'
    if corruption=='changed':study.write_bytes(b'Corruption')
    elif corruption=='missing':study.unlink()
    elif corruption=='extra':(snapshot/'payload/extra.txt').write_bytes(b'Unexpected')
    elif corruption=='manifest':(snapshot/'manifest.json').write_text('{}')
    else:
        with closing(sqlite3.connect(snapshot/'payload/history.sqlite3')) as c:
            c.execute('DROP TRIGGER IF EXISTS p13_messages_no_update')
            c.execute('PRAGMA user_version=999');c.commit()
    with pytest.raises(GatewayError):g.verify_recovery_snapshot('invented-one',restore=True)
    assert not (g.config.recovery_root/'restores/invented-one').exists()


def test_populated_restore_target_is_never_overwritten(tmp_path):
    g=setup(tmp_path);g.create_recovery_snapshot('invented-one')
    target=g.config.recovery_root/'restores/invented-one';target.mkdir(parents=True)
    sentinel=target/'owner.txt';sentinel.write_bytes(b'Preserve owner data')
    with pytest.raises(GatewayError):g.verify_recovery_snapshot('invented-one',restore=True)
    assert sentinel.read_bytes()==b'Preserve owner data'


def test_existing_restore_is_reverified_not_trusted_by_receipt(tmp_path):
    g=setup(tmp_path);g.create_recovery_snapshot('invented-one');g.verify_recovery_snapshot('invented-one',restore=True)
    target=g.config.recovery_root/'restores/invented-one/payload/study/invented.txt'
    target.write_bytes(b'Corruption')
    with pytest.raises(GatewayError):g.verify_recovery_snapshot('invented-one',restore=True)


def test_committed_wal_and_hardlink_guard(tmp_path):
    g=setup(tmp_path)
    with closing(sqlite3.connect(g.config.recovery_sidecar_root/'ledger.sqlite3')) as ledger:
        ledger.execute('PRAGMA journal_mode=WAL');ledger.execute("INSERT INTO audit VALUES(2,'reused')");ledger.commit()
        out=g.create_recovery_snapshot('wal-one')
        assert out['verified']
    proof=g.verify_recovery_snapshot('wal-one',restore=True)
    assert proof['ledger_verified']
    with closing(sqlite3.connect(g.config.recovery_root/'restores/wal-one/payload/sidecar/ledger.sqlite3')) as restored:
        assert restored.execute('SELECT COUNT(*) FROM audit').fetchone()[0]==2
    try:os.link(g.config.study_root/'invented.txt',g.config.study_root/'alias.txt')
    except OSError:pytest.skip('Hardlinks unavailable')
    with pytest.raises(GatewayError):g.create_recovery_snapshot('alias-one')


def test_opt_in_mcp_discovery_contract_and_native_restore(tmp_path):
    g=setup(tmp_path)
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            names={t.name for t in (await client.list_tools()).tools}
            assert {'recovery_snapshot_plan','create_recovery_snapshot','verify_recovery_snapshot'}<=names
            created=(await client.call_tool('create_recovery_snapshot',{'snapshot_key':'mcp-one'})).structuredContent
            assert created['ok'] and created['verified']
            restored=(await client.call_tool('verify_recovery_snapshot',{'snapshot_key':'mcp-one','restore':True})).structuredContent
            assert restored['isolated_restore_verified']
            bad=(await client.call_tool('create_recovery_snapshot',{'snapshot_key':'mcp-two','absolute_path':str(tmp_path)})).structuredContent
            assert not bad['ok'] and str(tmp_path) not in json.dumps(bad)
    anyio.run(check)


def test_default_readonly_host_does_not_discover_recovery(tmp_path):
    g=setup(tmp_path);g.capabilities=frozenset({'read'})
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            assert not any('recovery' in t.name for t in (await client.list_tools()).tools)
            denied=(await client.call_tool('recovery_snapshot_plan',{})).structuredContent
            assert not denied['ok'] and denied['error']['code']=='PERMISSION_DENIED'
    anyio.run(check)


def test_runtime_explicit_recovery_config_and_bad_exclusions(tmp_path):
    from cognivault.runtime import load_gateway_from_config
    p=tmp_path/'gateway.toml'
    original='[gateway]\nversion="0.6.0"\n[study]\nroot='+json.dumps(str(tmp_path))+'\nqmd_collection="studyvault"\nqmd_version="2.8.3"\nqmd_executable="missing-synthetic"\n[history]\nbackend="not_configured"\n'
    config='[recovery]\nroot='+json.dumps(str(tmp_path/'recovery'))+'\nsidecar_root='+json.dumps(str(tmp_path/'sidecar'))+'\nexclude_sidecar_paths=["runtime"]\n'
    p.write_text(original+config)
    cfg=load_gateway_from_config(p).config
    assert cfg.recovery_root==tmp_path/'recovery' and cfg.recovery_sidecar_exclusions==('runtime',)
    assert cfg.gateway_config_file==p
    for bad in ['../escape','a\\b','/absolute','a:b']:
        p.write_text(original+config.replace('["runtime"]',json.dumps([bad])))
        with pytest.raises(ValueError):load_gateway_from_config(p)


def test_changed_file_and_capacity_fail_preserve_production(tmp_path,monkeypatch):
    from cognivault.recovery import service
    g=setup(tmp_path);original=service.copy_file
    def race(source,root,target):
        answer=original(source,root,target)
        if source.name=='invented.txt':source.write_bytes(b'Concurrent change')
        return answer
    monkeypatch.setattr(service,'copy_file',race)
    with pytest.raises(GatewayError):g.create_recovery_snapshot('race-one')
    assert not (g.config.recovery_root/'snapshots/race-one').exists()
    assert list((g.config.recovery_root/'snapshots').glob('race-one.partial-*'))
    monkeypatch.setattr(service,'copy_file',original)
    monkeypatch.setattr(service.shutil,'disk_usage',lambda _:type('Capacity',(),{'free':0})())
    with pytest.raises(GatewayError):g.create_recovery_snapshot('capacity-one')
    assert not (g.config.recovery_root/'snapshots/capacity-one').exists()


def test_sqlite_proof_streams_large_blob_evidence(tmp_path,monkeypatch):
    from cognivault.recovery.io import sqlite_proof
    path=tmp_path/'large.sqlite3'
    with closing(sqlite3.connect(path)) as c:
        c.execute('CREATE TABLE evidence(payload BLOB)');c.execute('INSERT INTO evidence VALUES(?)',(b'x'*(3*1024*1024),));c.commit()
    requested=[];real_connect=sqlite3.connect
    class Reader:
        def __init__(self,blob):self.blob=blob
        def __enter__(self):return self
        def __exit__(self,*_):self.blob.close()
        def read(self,n):requested.append(n);return self.blob.read(n)
        def __len__(self):return len(self.blob)
    class Connection(sqlite3.Connection):
        def blobopen(self,*a,**kw):return Reader(super().blobopen(*a,**kw))
    monkeypatch.setattr(sqlite3,'connect',lambda *a,**kw:real_connect(*a,**kw,factory=Connection))
    proof=sqlite_proof(path)
    assert proof['tables']['evidence']['rows']==1 and requested and max(requested)<=1024*1024


def test_sqlite_proof_handles_generated_and_without_rowid_blobs(tmp_path):
    from cognivault.recovery.io import sqlite_proof
    path=tmp_path/'variants.sqlite3'
    with closing(sqlite3.connect(path)) as c:
        c.execute('CREATE TABLE variants(k TEXT PRIMARY KEY, b BLOB) WITHOUT ROWID')
        c.executemany('INSERT INTO variants VALUES(?,?)',[('a',b''),('b',b'z'*2048)])
        c.execute('CREATE TABLE generated(v TEXT, b BLOB GENERATED ALWAYS AS (CAST(v AS BLOB)) VIRTUAL)')
        c.execute("INSERT INTO generated(v) VALUES('Invented generated bytes')");c.commit()
    first=sqlite_proof(path)
    assert first['tables']['variants']['rows']==2 and first['tables']['generated']['rows']==1
    assert sqlite_proof(path)==first


@pytest.mark.parametrize('tamper',['counts','components','removed_file'])
def test_self_consistent_manifest_cannot_fake_completeness(tmp_path,tamper):
    from cognivault.recovery.io import encoded
    g=setup(tmp_path);g.create_recovery_snapshot('fake-one')
    folder=g.config.recovery_root/'snapshots/fake-one';p=folder/'manifest.json';m=json.loads(p.read_bytes())
    if tamper=='counts':m['history_proof']['canonical']['messages']=0
    elif tamper=='components':m['components']['study']=False
    else:
        removed=next(i for i in m['files'] if i['relative']=='history.sqlite3');m['files'].remove(removed)
        (folder/'payload/history.sqlite3').unlink()
    raw=encoded(m);p.write_bytes(raw);(folder/'manifest.sha256').write_text(hashlib.sha256(raw).hexdigest())
    with pytest.raises(GatewayError):g.verify_recovery_snapshot('fake-one')


def test_same_size_change_with_restored_mtime_is_rejected(tmp_path,monkeypatch):
    from cognivault.recovery import service
    g=setup(tmp_path);original=service.copy_file
    def race(source,root,target):
        stamp=source.stat();answer=original(source,root,target)
        if source.name=='invented.txt':
            source.write_bytes(b'x'*stamp.st_size)
            os.utime(source,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        return answer
    monkeypatch.setattr(service,'copy_file',race)
    with pytest.raises(GatewayError):g.create_recovery_snapshot('mtime-one')


def test_capacity_includes_committed_wal_pages(tmp_path):
    g=setup(tmp_path)
    with closing(sqlite3.connect(g.config.recovery_sidecar_root/'ledger.sqlite3')) as c:
        c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA wal_autocheckpoint=0')
        c.execute('CREATE TABLE large(payload BLOB)');c.execute('INSERT INTO large VALUES(zeroblob(8388608))');c.commit()
        plan=g.recovery_snapshot_plan()
        assert plan['required_bytes']>=8388608


def test_checksum_hardlink_is_rejected(tmp_path):
    g=setup(tmp_path);g.create_recovery_snapshot('link-one')
    checksum=g.config.recovery_root/'snapshots/link-one/manifest.sha256'
    outside=tmp_path/'outside.txt';outside.write_bytes(checksum.read_bytes());checksum.unlink()
    try:os.link(outside,checksum)
    except OSError:pytest.skip('Hardlinks unavailable')
    with pytest.raises(GatewayError):g.verify_recovery_snapshot('link-one')


def test_oversized_manifest_never_publishes(tmp_path,monkeypatch):
    from cognivault.recovery import io
    g=setup(tmp_path);monkeypatch.setattr(io,'MAX_MANIFEST',10)
    with pytest.raises(GatewayError):g.create_recovery_snapshot('large-manifest')
    assert not (g.config.recovery_root/'snapshots/large-manifest').exists()


def test_restore_config_relocates_all_data_and_preserves_original(tmp_path):
    import tomllib
    g=setup(tmp_path)
    original=tmp_path/'gateway.toml'
    original.write_text('[study.qmd_runtime]\nnode_executable="synthetic-node"\ncli_entrypoint="synthetic-cli.js"\n')
    qmd_config=tmp_path/'qmd.yml';qmd_config.write_text('collections: {}')
    qmd_index=tmp_path/'qmd.sqlite3'
    with closing(sqlite3.connect(qmd_index)) as c:c.execute('CREATE TABLE synthetic(n INTEGER)')
    g.config=replace(g.config,gateway_config_file=original,qmd_snapshot_config=qmd_config,qmd_snapshot_index=qmd_index)
    g.create_recovery_snapshot('config-one')
    # Runtime files are invented here; test configuration preparation without launching them.
    from cognivault.recovery.service import prepare_restore_config
    target=g.config.recovery_root/'restores/config-one';target.mkdir(parents=True)
    (target/'payload').mkdir();(target/'payload/gateway-original.toml').write_bytes(original.read_bytes())
    cfg=prepare_restore_config(g,target)
    data=tomllib.loads(cfg.read_text())
    assert Path(data['study']['root']).is_relative_to(target)
    assert Path(data['history']['database']).is_relative_to(target)
    assert Path(data['assets']['database']).is_relative_to(target)
    relocated=json.loads((target/'qmd-restored.yml').read_text())
    assert Path(relocated['collections']['studyvault']['path']).is_relative_to(target)
    assert original.read_text().startswith('[study.qmd_runtime]')
    assert prepare_restore_config(g,target)==cfg


def test_restore_study_uses_relocated_gateway_and_response_contract(tmp_path,monkeypatch):
    from cognivault import runtime
    from cognivault.recovery import service
    g=setup(tmp_path);original=tmp_path/'original.toml'
    original.write_text('[study.qmd_runtime]\nnode_executable="synthetic-node"\ncli_entrypoint="synthetic-cli.js"\n')
    qmd_config=tmp_path/'qmd.yml';qmd_config.write_text('collections: {}')
    qmd_index=tmp_path/'qmd.sqlite3'
    with closing(sqlite3.connect(qmd_index)) as c:c.execute('CREATE TABLE synthetic(n INTEGER)')
    g.config=replace(g.config,gateway_config_file=original,qmd_snapshot_config=qmd_config,qmd_snapshot_index=qmd_index)
    g.create_recovery_snapshot('study-one')
    def isolated(config):
        result=service.restored_gateway(g,config.parent)
        class Study:
            name='synthetic_study'
            def search(self,query,limit):return []
        result.study_backend=Study()
        return result
    monkeypatch.setattr(runtime,'load_gateway_from_config',isolated)
    assert g.verify_recovery_snapshot('study-one',restore=True)['study_read_verified']


def test_catalog_write_failure_retains_stage_without_publishing(tmp_path,monkeypatch):
    from cognivault.recovery import service
    g=setup(tmp_path);original=service.write_json
    def broken(path,value):
        if path.parent.name=='catalog':raise OSError('Invented write failure')
        return original(path,value)
    monkeypatch.setattr(service,'write_json',broken)
    with pytest.raises(GatewayError):g.create_recovery_snapshot('catalog-one')
    assert not (g.config.recovery_root/'snapshots/catalog-one').exists()
    assert list((g.config.recovery_root/'snapshots').glob('catalog-one.partial-*'))


def test_legacy_qmd_launcher_cannot_claim_isolated_restore(tmp_path):
    from cognivault.recovery.service import prepare_restore_config
    g=setup(tmp_path);target=tmp_path/'isolated';(target/'payload').mkdir(parents=True)
    original=target/'payload/gateway-original.toml';original.write_text('[study]\nqmd_executable="synthetic-qmd"\n')
    g.config=replace(g.config,gateway_config_file=original)
    with pytest.raises(GatewayError):prepare_restore_config(g,target)


def test_restore_capacity_is_checked_before_copy(tmp_path,monkeypatch):
    from cognivault.recovery import service
    g=setup(tmp_path);g.create_recovery_snapshot('restore-capacity')
    monkeypatch.setattr(service.shutil,'disk_usage',lambda _:type('Capacity',(),{'free':0})())
    with pytest.raises(GatewayError):g.verify_recovery_snapshot('restore-capacity',restore=True)
    assert not (g.config.recovery_root/'restores/restore-capacity').exists()


@pytest.mark.skipif(os.name!='nt',reason='Windows extended path regression')
def test_normalization_receipt_in_deep_private_inbox(tmp_path):
    from cognivault.recovery.io import physical
    g=setup(tmp_path);deep=tmp_path/('invented-deep-inbox-'+'x'*90)
    physical(deep).mkdir(parents=True)
    g.config=replace(g.config,history_migration_inbox=deep)
    outcome=g.normalize_history_sources(g.history_normalization_snapshot()['source_set_sha256'])
    receipt=deep/outcome['receipt']['relative_path']
    assert len(str(receipt))>260
    assert hashlib.sha256(physical(receipt).read_bytes()).hexdigest()==outcome['receipt']['sha256']


def test_plan_accounts_for_interrupted_attempts_without_claiming_verified(tmp_path):
    g=setup(tmp_path)
    assert g.recovery_snapshot_plan()['recovery_state']=={'published_directories':0,'incomplete_attempts':0,'incomplete_bytes':0}
    g.create_recovery_snapshot('planned-one')
    stage=g.config.recovery_root/'snapshots/planned-two.partial-invented';stage.mkdir()
    (stage/'partial.bin').write_bytes(b'x'*256)
    assert g.recovery_snapshot_plan()['recovery_state']=={'published_directories':1,'incomplete_attempts':1,'incomplete_bytes':256}


def test_restore_requires_reserve_above_copy_size_before_creating_target(tmp_path,monkeypatch):
    from cognivault.recovery import service
    g=setup(tmp_path);snapshot=g.create_recovery_snapshot('reserved-space')
    monkeypatch.setattr(service.shutil,'disk_usage',lambda _:type('Capacity',(),{'free':snapshot['total_bytes']+128*1024*1024})())
    with pytest.raises(GatewayError):g.verify_recovery_snapshot('reserved-space',restore=True)
    assert not (g.config.recovery_root/'restores/reserved-space').exists()


def test_plan_reports_restore_budget_and_published_allocation_without_private_refs(tmp_path):
    g=setup(tmp_path);snapshot=g.create_recovery_snapshot('budget-one')
    plan=g.recovery_snapshot_plan();budget=plan['restore_capacity']
    assert budget['restore_reserve_bytes']>=4*1024**3
    assert budget['restore_temp_bytes']>=64*1024**2 and budget['restore_wal_bytes']>=64*1024**2
    assert budget['restore_required_bytes']==budget['restore_copy_bytes']+budget['restore_temp_bytes']+budget['restore_wal_bytes']+budget['restore_reserve_bytes']
    assert plan['published_snapshot_bytes']>=snapshot['total_bytes']
    assert str(tmp_path) not in json.dumps(plan)


def test_existing_restore_does_not_budget_the_payload_copy_twice(tmp_path,monkeypatch):
    from cognivault.recovery import service
    g=setup(tmp_path);g.create_recovery_snapshot('reuse-budget');g.verify_recovery_snapshot('reuse-budget',restore=True)
    monkeypatch.setattr(service.shutil,'disk_usage',lambda _:type('Capacity',(),{'free':4*1024**3+128*1024**2})())
    result=g.verify_recovery_snapshot('reuse-budget',restore=True)
    assert result['restore_capacity']['restore_copy_bytes']==0
    assert result['restore_capacity']['sufficient']


def test_isolated_domain_readback_uses_formal_api_and_preserves_analysis_versions(tmp_path,monkeypatch):
    g=setup(tmp_path);g.capabilities=frozenset({'read','ingest','write','admin'})
    doc=g.search_documents('Invented',limit=1)['results'][0]['document_uri']
    source=g.register_wrong_answer_source(doc,'Invented arithmetic question','Invented incorrect answer')['source']
    analysis={'error_type':'invented','knowledge_points':['invented arithmetic'],'reasoning':'Invented explanation',
              'correct_solution':'Invented correction','review_advice':'Invented practice'}
    g.save_wrong_answer_analysis(source['source_id'],analysis,[doc],[],'invented-analysis-one')
    g.update_wrong_answer_analysis(source['source_id'],{**analysis,'review_advice':'Invented second practice'},[doc],[],
                                   'invented-analysis-two',expected_version=1)
    g.create_recovery_snapshot('domain-one')
    seen={name:0 for name in ('fetch_document','fetch_asset','get_wrong_answer_bundle')}
    for name in seen:
        method=getattr(Gateway,name)
        def checked(self,*args,_method=method,_name=name,**kwargs):
            if self is not g:
                assert self.config.asset_database.is_relative_to(g.config.recovery_root/'restores/domain-one')
                seen[_name]+=1
            return _method(self,*args,**kwargs)
        monkeypatch.setattr(Gateway,name,checked)
    result=g.verify_recovery_snapshot('domain-one',restore=True);proof=result['domain_readback']
    assert proof['assets']['state']=='verified' and proof['assets']['readback_verified']
    assert proof['documents']['state']=='verified' and proof['documents']['readback_verified']
    wrong=proof['wrong_answers']
    assert wrong['source_records']==1 and wrong['analysis_records']==2
    assert wrong['source_readback_verified'] and wrong['analysis_version_readback_verified']
    assert wrong['analysis_versions_read_back']==1
    assert all(seen.values())
    private=json.dumps(result)
    assert str(tmp_path) not in private and source['source_id'] not in private and doc not in private
    assert 'Invented explanation' not in private
    assert g.get_wrong_answer_bundle(source['source_id'])['total']==2


def test_empty_domains_cannot_claim_object_or_analysis_version_readback(tmp_path):
    g=setup(tmp_path,documents=False);g.create_recovery_snapshot('empty-domains')
    proof=g.verify_recovery_snapshot('empty-domains',restore=True)['domain_readback']
    assert proof['assets']['state']=='empty' and proof['assets']['records']==0 and not proof['assets']['readback_verified']
    assert proof['documents']['state']=='empty' and proof['documents']['no_result_verified'] and not proof['documents']['readback_verified']
    wrong=proof['wrong_answers']
    assert wrong['state']=='empty' and wrong['source_records']==0 and wrong['analysis_records']==0
    assert wrong['no_result_verified'] and not wrong['analysis_version_readback_verified']


def test_source_only_wrong_answer_is_not_reported_as_analysis_version_verified(tmp_path):
    g=setup(tmp_path)
    doc=g.search_documents('Invented',limit=1)['results'][0]['document_uri']
    g.register_wrong_answer_source(doc,'Invented question','Invented answer')
    g.create_recovery_snapshot('source-only-domains')
    wrong=g.verify_recovery_snapshot('source-only-domains',restore=True)['domain_readback']['wrong_answers']
    assert wrong['state']=='source_only' and wrong['source_readback_verified']
    assert wrong['analysis_records']==0 and not wrong['analysis_version_readback_verified']


@pytest.fixture(autouse=True)
def synthetic_disk_capacity(monkeypatch):
    import shutil
    original = shutil.disk_usage
    def enough(path):
        usage = original(path)
        return type(usage)(usage.total + 64*1024**3, usage.used, usage.free + 64*1024**3)
    monkeypatch.setattr(shutil, "disk_usage", enough)
