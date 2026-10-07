"""Finite recovery observations over authored synthetic Gateway fixtures."""
from dataclasses import replace
import hashlib
import json
import os
import threading
import pytest
import anyio
from mcp.shared.memory import create_connected_server_and_client_session
from cognivault.transports.mcp_stdio import create_mcp_server
from cognivault.transports.recovery_tools import call_recovery_tool
from cognivault.contracts import GatewayError
from cognivault.recovery import service
from cognivault.recovery.io import encoded
from test_recovery import setup


def tree(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def observe(g, **kwargs):
    out=g.recovery_snapshot_status('example', **kwargs)
    assert out['artifact_integrity']=='not_verified'
    assert out['owner']=={'state':'unknown'}
    assert out['operation_binding']=='unavailable'
    assert out['resume']=={'supported':False,'reason':'NO_FENCED_RESUME_HANDLER'}
    assert str(g.config.recovery_root.parent) not in json.dumps(out)
    return out


def published(tmp_path):
    g=setup(tmp_path);g.create_recovery_snapshot('example');return g


def rewrite(g, change):
    folder=g.config.recovery_root/'snapshots/example'
    m=json.loads((folder/'manifest.json').read_bytes());change(m)
    data=encoded(m);digest=hashlib.sha256(data).hexdigest()
    (folder/'manifest.json').write_bytes(data)
    (folder/'manifest.sha256').write_text(digest)
    (g.config.recovery_root/'catalog/example.json').write_bytes(encoded({'manifest_sha256':digest}))


def test_absent_no_directory_creation(tmp_path):
    g=setup(tmp_path);g.config=replace(g.config,recovery_root=tmp_path/'absent')
    out=observe(g)
    assert out['state']=='not_observed' and not out['verification_required']
    assert not g.config.recovery_root.exists()


def test_response_loss_reopen_and_duplicate(tmp_path,monkeypatch):
    g=setup(tmp_path);original=service.public
    def lost(*a,**k):raise OSError('PRIVATE response lost')
    with monkeypatch.context() as m:
        m.setattr(service,'public',lost)
        with pytest.raises(GatewayError):g.create_recovery_snapshot('example')
    g=replace_gateway(g)
    before=tree(g.config.recovery_root);out=observe(g)
    assert out['state']=='published_unverified' and out['metadata_consistent']
    assert tree(g.config.recovery_root)==before
    assert g.create_recovery_snapshot('example')['reused']


def replace_gateway(g):
    from cognivault.gateway import Gateway
    return Gateway(g.config,None,g.history_backend,document_store=g.document_store,capabilities=g.capabilities)


def test_catalog_fsync_then_rename_failure_and_retry_preserves(tmp_path,monkeypatch):
    g=setup(tmp_path)
    with monkeypatch.context() as m:
        m.setattr(service.os,'rename',lambda *a: (_ for _ in ()).throw(OSError('PRIVATE')))
        with pytest.raises(GatewayError):g.create_recovery_snapshot('example')
    assert (g.config.recovery_root/'catalog/example.json').exists()
    before=tree(g.config.recovery_root)
    out=observe(g);assert out['state']=='needs_action'
    assert out['partial_observation']['count']==1
    assert tree(g.config.recovery_root)==before
    with pytest.raises(GatewayError):g.create_recovery_snapshot('example')
    assert len(list((g.config.recovery_root/'snapshots').glob('example.partial-*')))==2
    assert all(tree(g.config.recovery_root)[k]==v for k,v in before.items())


def test_interrupted_catalog_write_leaves_partial(tmp_path,monkeypatch):
    g=setup(tmp_path);original=service.write_json
    def interrupted(path,value):
        if path.parent.name=='catalog':
            with path.open('xb') as stream:
                stream.write(b'{');stream.flush();os.fsync(stream.fileno())
            raise OSError('private interrupted write')
        return original(path,value)
    with monkeypatch.context() as m:
        m.setattr(service,'write_json',interrupted)
        with pytest.raises(GatewayError):g.create_recovery_snapshot('example')
    before=tree(g.config.recovery_root);out=observe(g)
    assert out['state']=='needs_action' and out['partial_observation']['count']==1
    assert tree(g.config.recovery_root)==before


@pytest.mark.parametrize('point',['copy','publish'])
def test_active_worker_owner_unknown(tmp_path,monkeypatch,point):
    g=setup(tmp_path);started=threading.Event();release=threading.Event();errors=[]
    attr='copy_file' if point=='copy' else 'rename';owner=service if point=='copy' else service.os
    original=getattr(owner,attr)
    def paused(*a,**k):
        started.set();assert release.wait(20);return original(*a,**k)
    monkeypatch.setattr(owner,attr,paused)
    def run():
        try:g.create_recovery_snapshot('example')
        except BaseException as e:errors.append(e)
    worker=threading.Thread(target=run);worker.start()
    try:
        assert started.wait(20)
        before=tree(g.config.recovery_root);out=observe(g)
        assert out['state']=='needs_action' and tree(g.config.recovery_root)==before
    finally:release.set();worker.join(20)
    assert not worker.is_alive() and not errors
    assert observe(g)['state']=='published_unverified'


def test_payload_corruption_and_no_payload_sqlite_calls(tmp_path,monkeypatch):
    g=published(tmp_path);(g.config.recovery_root/'snapshots/example/payload/study/invented.txt').write_bytes(b'bad')
    before=tree(g.config.recovery_root)
    with monkeypatch.context() as m:
        for name in ('plan','snapshot','check_payload','history_proof','restored_gateway','sqlite_proof','inventory'):
            m.setattr(service,name,lambda *a,**k: pytest.fail('status touched payload/SQLite'))
        m.setattr(service.sqlite3,'connect',lambda *a,**k:pytest.fail('SQLite opened'))
        assert observe(g)['metadata_consistent']
    assert tree(g.config.recovery_root)==before
    with pytest.raises(GatewayError):g.verify_recovery_snapshot('example',restore=False)


@pytest.mark.parametrize('change',[
    lambda m:m.update(schema_version=True),lambda m:m['files'][0].update(bytes=True),
    lambda m:m['files'][0].update(relative='../private'),lambda m:m['files'].append(m['files'][0]),
    lambda m:m['files'][0].update(sha256='bad'),lambda m:m['files'][0].update(database=1),
    lambda m:m['files'][0].update(sqlite_proof={}),lambda m:m.update(recorded_at=1),
    lambda m:m.update(files=[]),
])
def test_malformed_metadata_not_verified(tmp_path,change):
    g=published(tmp_path);rewrite(g,change);out=observe(g)
    assert out['state']=='needs_action' and not out['metadata_consistent']


@pytest.mark.parametrize('file,data',[('catalog',b'{'),('manifest',b'{"x":1,"x":2}'),('checksum',b'0'*64)])
def test_broken_metadata(tmp_path,file,data):
    g=published(tmp_path)
    path={'catalog':g.config.recovery_root/'catalog/example.json','manifest':g.config.recovery_root/'snapshots/example/manifest.json',
          'checksum':g.config.recovery_root/'snapshots/example/manifest.sha256'}[file]
    path.write_bytes(data);assert observe(g)['state']=='needs_action'


def test_digest_and_configuration_shape(tmp_path):
    g=published(tmp_path);out=observe(g)
    assert observe(g,expected_manifest_sha256=out['manifest_sha256'])['expected_manifest_match']=='matches'
    assert observe(g,expected_manifest_sha256='0'*64)['state']=='needs_action'
    g.config=replace(g.config,study_root=tmp_path/'different-same-shape')
    assert observe(g)['metadata_consistent']
    g.config=replace(g.config,recovery_include_study=False)
    assert 'CONFIGURATION_MISMATCH' in observe(g)['reason_codes']


def test_partial_bounds_and_unexplained(tmp_path,monkeypatch):
    from cognivault.recovery import status
    g=setup(tmp_path);parent=g.config.recovery_root/'snapshots';parent.mkdir()
    for i in range(3):(parent/('example.partial-'+f'{i:032x}')).mkdir()
    assert observe(g)['partial_observation']=={'count':3,'complete':True}
    monkeypatch.setattr(status,'MAX_OBSERVATION_ENTRIES',1)
    assert not observe(g)['partial_observation']['complete']
    monkeypatch.setattr(status,'MAX_OBSERVATION_ENTRIES',10000)
    (parent/'example.partial-unknown').mkdir()
    assert 'UNEXPLAINED_ARTIFACT' in observe(g)['reason_codes']


def test_observation_changed(tmp_path,monkeypatch):
    from cognivault.recovery import status
    g=published(tmp_path);original=status.observe;calls=0
    def changed(*a):
        nonlocal calls
        result=original(*a);calls+=1
        if calls==1:(g.config.recovery_root/'catalog/example.json').write_bytes(b'{}')
        return result
    monkeypatch.setattr(status,'observe',changed)
    assert observe(g)['state']=='observation_changed'


@pytest.mark.parametrize('digest',['A'*64,'0'*63,True,1])
def test_invalid_digest(tmp_path,digest):
    with pytest.raises(GatewayError) as e:setup(tmp_path).recovery_snapshot_status('example',expected_manifest_sha256=digest)
    assert e.value.code=='INVALID_ARGUMENT'


def test_permissions_paths_and_io(tmp_path,monkeypatch):
    from cognivault.recovery import status
    g=published(tmp_path);g.capabilities=frozenset({'read'})
    with pytest.raises(GatewayError) as e:observe(g)
    assert e.value.code=='PERMISSION_DENIED'
    g.capabilities=frozenset({'read','admin'})
    with monkeypatch.context() as m:
        m.setattr(status,'_open_verified_source',lambda *a: (_ for _ in ()).throw(PermissionError('PRIVATE/path')))
        with pytest.raises(GatewayError) as e:observe(g)
        assert e.value.code=='STORAGE_UNAVAILABLE' and 'PRIVATE' not in str(e.value)
    path=g.config.recovery_root/'snapshots/example/manifest.sha256';os.link(path,path.with_name('linked'))
    with pytest.raises(GatewayError) as e:observe(g)
    assert e.value.code=='OUTSIDE_ALLOWLIST'


def test_bounded_metadata_and_git_root(tmp_path,monkeypatch):
    from cognivault.recovery import status
    g=published(tmp_path)
    with monkeypatch.context() as m:
        m.setattr(status,'MAX_MANIFEST',8)
        with pytest.raises(GatewayError) as e:observe(g)
        assert e.value.code=='PAYLOAD_TOO_LARGE'
    (g.config.recovery_root/'.git').mkdir()
    with pytest.raises(GatewayError) as e:observe(g)
    assert e.value.code=='OUTSIDE_ALLOWLIST'


def test_final_requires_directory_and_strict_key(tmp_path):
    g=setup(tmp_path);parent=g.config.recovery_root/'snapshots';parent.mkdir()
    (parent/'example').write_bytes(b'not a directory')
    with pytest.raises(GatewayError) as e:observe(g)
    assert e.value.code=='OUTSIDE_ALLOWLIST'
    for value in ('../private','UPPER',True,None,'x'*65):
        with pytest.raises(GatewayError) as e:g.recovery_snapshot_status(value)
        assert e.value.code=='INVALID_ARGUMENT'


def test_native_mcp_discovery_arguments_and_admin(tmp_path):
    g=setup(tmp_path)
    async def run():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            tool=next(t for t in (await client.list_tools()).tools if t.name=='recovery_snapshot_status')
            assert tool.annotations.readOnlyHint and tool.inputSchema['required']==['snapshot_key']
            result=await client.call_tool(tool.name,{'snapshot_key':'example'})
            assert result.structuredContent['state']=='not_observed'
    anyio.run(run)
    for args in ({'snapshot_key':None},{'snapshot_key':'example','extra':1},{'snapshot_key':'../secret'}, {'snapshot_key':'example','expected_manifest_sha256':None}):
        with pytest.raises(GatewayError):call_recovery_tool(g,'recovery_snapshot_status',args)
    g.capabilities=frozenset({'read'})
    with pytest.raises(GatewayError) as e:call_recovery_tool(g,'recovery_snapshot_status',{'snapshot_key':'example'})
    assert e.value.code=='PERMISSION_DENIED'
    async def denied():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            assert all(t.name!='recovery_snapshot_status' for t in (await client.list_tools()).tools)
            result=await client.call_tool('recovery_snapshot_status',{'snapshot_key':'example'})
            assert result.isError
            assert 'PERMISSION_DENIED' in str(result.content)
    anyio.run(denied)
