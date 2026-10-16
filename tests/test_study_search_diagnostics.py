"""Content-free diagnostics over invented temporary files and fake QMD only."""
import json
from pathlib import Path
import subprocess

import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from cognivault.contracts import GatewayError
from cognivault.gateway import Gateway
from cognivault.transports.mcp_stdio import create_mcp_server
from test_study_qmd import FakeRunner, _backend, _study, _hit


def test_diagnostic_distinguishes_literal_and_decoded_without_content(tmp_path):
    config, _ = _study(tmp_path)
    for name in ['invented#part.md', 'invented%20note.md', 'invented note.md']:
        (config.study_root / name).write_text('INVENTED_PRIVATE_BODY', encoding='utf-8')
    records = [_hit('qmd://studyvault/'+name, title='INVENTED_PRIVATE_TITLE', snippet='INVENTED_PRIVATE_BODY')
               for name in ['invented#part.md', 'invented%20note.md', 'missing.md']]
    runner = FakeRunner(json.dumps(records))
    g = Gateway(config, _backend(config, runner))
    result = g.study_search_diagnostics('INVENTED_PRIVATE_QUERY', 3)
    assert result['collection'] == 'studyvault'
    assert result['stage'] == 'validated_output' and result['error_code'] is None
    assert len(result['hits']) == 3 and len(runner.calls) == 1
    a,b,c = result['hits']
    assert a['has_fragment_marker'] and a['strict_path_status'] == 'invalid'
    assert a['literal_path_status'] == 'within_file'
    assert b['has_percent_escape'] and b['literal_path_status'] == b['decoded_path_status'] == 'within_file'
    assert c['strict_path_status'] == 'missing'
    output=json.dumps(result)
    for secret in ['INVENTED_PRIVATE', 'invented#', 'invented%20', 'missing.md', str(tmp_path), 'qmd://']:
        assert secret not in output
    # Existing search still rejects the raw fragment. This tool does not repair paths.
    with pytest.raises(GatewayError, match='outside'):
        g.search_study('ordinary', 3)


@pytest.mark.parametrize('uri', ['qmd://private/outside.md', 'file:///outside.md',
    'qmd://studyvault/../outside.md', 'qmd://studyvault/%2e%2e/outside.md',
    'qmd://studyvault/C:/outside.md', 'qmd://studyvault/%5coutside.md',
    'qmd://studyvault/..%20/outside.md'])
def test_diagnostic_does_not_probe_disallowed_paths(tmp_path, monkeypatch, uri):
    config, _ = _study(tmp_path)
    original = Path.lstat
    def guarded(path, *args, **kwargs):
        assert path.is_relative_to(config.study_root)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', guarded)
    result=Gateway(config, _backend(config, FakeRunner(json.dumps([_hit(uri)])))).study_search_diagnostics('ordinary', 1)
    assert result['hits'][0]['strict_path_status'] in {'invalid', 'outside'}


def test_diagnostic_does_not_follow_links(tmp_path, monkeypatch):
    config, source = _study(tmp_path)
    original=Path.lstat
    def linked(path, *args, **kwargs):
        if path == source:
            from types import SimpleNamespace
            return SimpleNamespace(st_mode=0o120777, st_file_attributes=0)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', linked)
    result=Gateway(config, _backend(config, FakeRunner(json.dumps([_hit()])))).study_search_diagnostics('ordinary', 1)
    assert result['hits'][0]['strict_path_status'] == 'unknown'


@pytest.mark.parametrize('query,limit', [('',1), ('x'*501,1), ('x',True), ('x',0), ('x',4), ('x',1.5)])
def test_diagnostic_input_validation(tmp_path, query, limit):
    config,_=_study(tmp_path)
    runner=FakeRunner()
    with pytest.raises(GatewayError) as e:
        Gateway(config,_backend(config,runner)).study_search_diagnostics(query,limit)
    assert e.value.code=='INVALID_ARGUMENT' and runner.calls==[]


@pytest.mark.parametrize('runner,code', [(FakeRunner(error=subprocess.TimeoutExpired('INVENTED_SECRET',10)), 'BACKEND_TIMEOUT'),
    (FakeRunner(returncode=2), 'STUDY_UNAVAILABLE'), (FakeRunner('not json'), 'BACKEND_BAD_OUTPUT')])
def test_diagnostic_safe_runtime_errors(tmp_path, runner, code):
    config,_=_study(tmp_path)
    result=Gateway(config,_backend(config,runner)).study_search_diagnostics('ordinary',1)
    assert result['error_code']==code and result['hits']==[]
    assert 'INVENTED' not in json.dumps(result)


def test_native_diagnostic_permissions_shape_and_missing_backend(tmp_path):
    config,_=_study(tmp_path)
    g=Gateway(config,_backend(config,FakeRunner()))
    async def run():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            tools={t.name:t for t in (await client.list_tools()).tools}
            assert tools['study_search_diagnostics'].annotations.readOnlyHint is True
            result=(await client.call_tool('study_search_diagnostics',{'query':'ordinary','limit':1})).structuredContent
            assert result['ok'] and result['hits']==[]
            bad=(await client.call_tool('study_search_diagnostics',{'query':'ordinary','unexpected':'INVENTED_SECRET'})).structuredContent
            assert bad['error']['code']=='INVALID_ARGUMENT' and 'INVENTED_SECRET' not in json.dumps(bad)
    anyio.run(run)
    g.capabilities=frozenset({'write'})
    with pytest.raises(GatewayError) as e:g.study_search_diagnostics('ordinary',1)
    assert e.value.code=='PERMISSION_DENIED'
    g.capabilities=frozenset({'read'});g.study_backend=None
    with pytest.raises(GatewayError) as e:g.study_search_diagnostics('ordinary',1)
    assert e.value.code=='STUDY_UNAVAILABLE'


def test_unknown_runtime_error_code_and_long_uri_remain_content_free(tmp_path):
    config,_=_study(tmp_path)
    runner=FakeRunner(error=ValueError('INVENTED_SECRET_MESSAGE'))
    with pytest.raises(GatewayError) as raised:
        Gateway(config,_backend(config,runner)).study_search_diagnostics('ordinary',1)
    assert raised.value.code=='INTERNAL_ERROR'
    assert 'INVENTED_SECRET' not in str(raised.value)
    result=Gateway(config,_backend(config,FakeRunner(json.dumps([_hit('qmd://studyvault/'+'x'*5000)]))))\
        .study_search_diagnostics('ordinary',1)
    assert result['hits'][0]['literal_path_status']=='unknown'
    assert 'INVENTED_SECRET' not in json.dumps(result)


def test_diagnostic_write_only_host_does_not_discover_tool(tmp_path):
    config,_=_study(tmp_path)
    g=Gateway(config,_backend(config,FakeRunner()),capabilities=frozenset({'write'}))
    async def run():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            assert 'study_search_diagnostics' not in {t.name for t in (await client.list_tools()).tools}
            result=(await client.call_tool('study_search_diagnostics',{'query':'ordinary','limit':1})).structuredContent
            assert result['error']['code']=='PERMISSION_DENIED'
    anyio.run(run)


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Win32 path aliases')
def test_diagnostic_rejects_win32_alias_before_metadata_probe(tmp_path, monkeypatch):
    config,_=_study(tmp_path)
    def reject_probe(*args, **kwargs):
        pytest.fail('path metadata probe for a Win32 alias')
    monkeypatch.setattr(Path,'lstat',reject_probe)
    runner=FakeRunner(json.dumps([_hit('qmd://studyvault/.. /outside.md')]))
    result=Gateway(config,_backend(config,runner)).study_search_diagnostics('ordinary',1)
    assert result['hits'][0]['literal_path_status']=='invalid'
