"""Partial search contract using invented files and a fake QMD runner."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cognivault.contracts import GatewayError
from cognivault.gateway import Gateway
from test_study_qmd import FakeRunner, _backend, _study, _hit


@pytest.mark.parametrize('uris,count,missing', [
    (['qmd://studyvault/数学/synthetic.md','qmd://studyvault/missing.md'],1,1),
    (['qmd://studyvault/missing.md','qmd://studyvault/missing-folder/note.md'],0,2),
    (['qmd://studyvault/数学/synthetic.md'],1,0),
])
def test_definite_missing_returns_explicit_partial_without_backfill(tmp_path,uris,count,missing):
    config,_=_study(tmp_path)
    runner=FakeRunner(json.dumps([_hit(uri) for uri in uris]))
    result=Gateway(config,_backend(config,runner)).search_study('ordinary',len(uris))
    assert len(result['results'])==count
    assert result['partial'] is bool(missing)
    assert result['unavailable_source_count']==missing
    assert result['truncated'] is False and len(runner.calls)==1
    assert 'missing.md' not in json.dumps(result)


@pytest.mark.parametrize('uri', ['qmd://private/missing.md','qmd://studyvault/../missing.md',
 'qmd://studyvault/%2e%2e/missing.md','qmd://studyvault/missing.md#part',
 'qmd://studyvault/missing.md?query=1','qmd://studyvault/%5cmissing.md'])
def test_invalid_or_outside_missing_is_not_skipped(tmp_path,uri):
    config,_=_study(tmp_path)
    runner=FakeRunner(json.dumps([_hit(),_hit(uri)]))
    with pytest.raises(GatewayError) as e:Gateway(config,_backend(config,runner)).search_study('ordinary',2)
    assert e.value.code=='OUTSIDE_ALLOWLIST'


@pytest.mark.parametrize('failure',['permission','unknown','link'])
def test_missing_after_uncertain_component_fails_closed(tmp_path,monkeypatch,failure):
    config,_=_study(tmp_path)
    folder=config.study_root/'uncertain';folder.mkdir()
    original=Path.lstat
    def uncertain(path,*args,**kwargs):
        if path==folder:
            if failure=='permission':raise PermissionError('INVENTED_PRIVATE')
            if failure=='unknown':raise OSError('INVENTED_PRIVATE')
            return SimpleNamespace(st_mode=0o120777,st_file_attributes=0)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'lstat',uncertain)
    runner=FakeRunner(json.dumps([_hit('qmd://studyvault/uncertain/missing.md')]))
    with pytest.raises(GatewayError) as e:Gateway(config,_backend(config,runner)).search_study('ordinary',1)
    assert e.value.code=='OUTSIDE_ALLOWLIST' and 'INVENTED_PRIVATE' not in str(e.value)


def test_missing_beneath_reparse_root_is_not_skipped(tmp_path,monkeypatch):
    config,_=_study(tmp_path)
    original=Path.lstat
    def reparse_root(path,*args,**kwargs):
        if path==config.study_root:
            return SimpleNamespace(st_mode=0o040777,st_file_attributes=0x400)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'lstat',reparse_root)
    runner=FakeRunner(json.dumps([_hit('qmd://studyvault/missing.md')]))
    with pytest.raises(GatewayError) as e:Gateway(config,_backend(config,runner)).search_study('ordinary',1)
    assert e.value.code=='OUTSIDE_ALLOWLIST'


def test_missing_does_not_hide_invalid_metadata(tmp_path):
    config,_=_study(tmp_path)
    runner=FakeRunner(json.dumps([_hit('qmd://studyvault/missing.md',title=None)]))
    with pytest.raises(GatewayError) as e:Gateway(config,_backend(config,runner)).search_study('ordinary',1)
    assert e.value.code=='BACKEND_BAD_OUTPUT'


def test_native_partial_response_and_read_gate(tmp_path):
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session
    from cognivault.transports.mcp_stdio import create_mcp_server
    config,_=_study(tmp_path)
    runner=FakeRunner(json.dumps([_hit('qmd://studyvault/missing.md')]))
    g=Gateway(config,_backend(config,runner))
    async def run():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            result=(await client.call_tool('search_study',{'query':'ordinary','limit':1})).structuredContent
            assert result['ok'] and result['partial'] and result['unavailable_source_count']==1
            assert result['results']==[] and result['truncated'] is False
    anyio.run(run)
    g.capabilities=frozenset({'write'})
    with pytest.raises(GatewayError) as e:g.search_study('ordinary',1)
    assert e.value.code=='PERMISSION_DENIED' and len(runner.calls)==1


@pytest.mark.parametrize('valid',[False,True])
def test_retrieve_evidence_preserves_partial_and_never_alias_backfills(tmp_path,monkeypatch,valid):
    config,_=_study(tmp_path)
    records=[_hit('qmd://studyvault/missing.md')]
    if valid:records.insert(0,_hit())
    runner=FakeRunner(json.dumps(records))
    g=Gateway(config,_backend(config,runner))
    monkeypatch.setattr(g,'search_documents',lambda *a,**kw:{'results':[]})
    monkeypatch.setattr(g,'search_wrong_answers',lambda *a,**kw:{'results':[]})
    result=g.retrieve_evidence({'study':'物理'},limit=2)
    assert result['status']=='PARTIAL'
    domain=result['domains']['study']
    assert domain['status']=='partial' and domain['returned']==int(valid)
    assert domain['partial_backends']==[{'backend':'qmd','unavailable_source_count':1}]
    assert domain['errors']==[] and len(runner.calls)==1
    assert 'missing.md' not in json.dumps(result)


def test_focused_partial_empty_stops_aliases():
    from cognivault.learning_retrieval import focused
    calls=[]
    def search(query):
        calls.append(query)
        return {'results':[],'partial':True,'unavailable_source_count':1}
    result=focused('物理',search,'results')
    assert result['partial'] and len(calls)==1


def test_focused_partial_from_first_alias_does_not_try_second():
    from cognivault.learning_retrieval import focused
    calls=[]
    def search(query):
        calls.append(query)
        return {'results':[],'partial':len(calls)>1,'unavailable_source_count':int(len(calls)>1)}
    result=focused('近期学习',search,'results')
    assert result['partial'] and len(calls)==2
