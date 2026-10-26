"""Orchestration acceptance against isolated synthetic Gateways only."""
import asyncio
from types import SimpleNamespace

import pytest

from cognivault.contracts import GatewayError
from test_evidence_bundle import scenario
from test_wrong_answers import analysis


def task(kind, **inputs):
    return SimpleNamespace(kind=kind, inputs=inputs, grants=frozenset({'read', 'write'}), task_id='synthetic-task')


def result(output, evidence=()):
    return SimpleNamespace(output=output, evidence=evidence, confidence=.9, adapter_id='synthetic-agent')


def test_domain_ports_are_explicitly_available():
    from cognivault.orchestration import domains
    assert callable(domains.InProcessGatewayPort)
    assert callable(domains.MCPGatewayPort)


def test_retrieval_keeps_reported_notes_separate_and_verifies_them(tmp_path):
    from cognivault.orchestration.domains import InProcessGatewayPort, ThreeDomainRetrievalHandler
    g, *_ = scenario(tmp_path)
    t = task('evidence.retrieve', queries={'history':'engineering'})
    async def check():
        h = ThreeDomainRetrievalHandler(InProcessGatewayPort(g))
        prepared = await h.prepare(t)
        r = result({'selected_refs':[row['citation'] for row in prepared['evidence']],
                    'notes':['Historical source; this selection does not establish current truth.']})
        h.validate(t, r, prepared)
        receipt = await h.commit(t, r, 'synthetic-readonly-notes')
        assert receipt['persisted'] is False and receipt['notes'] == r.output['notes']
        assert await h.verify(t, r, receipt)
        receipt['notes'] = ['tampered note']
        assert not await h.verify(t, r, receipt)
        for notes in ['not a list', [True], ['x'*1001], ['note']*21]:
            with pytest.raises(ValueError):
                h.validate(t, result({'selected_refs':[], 'notes':notes}), prepared)
    asyncio.run(check())


def test_memory_historical_scope_is_caller_bound_and_cannot_be_upgraded(tmp_path):
    from cognivault.orchestration.domains import InProcessGatewayPort, MemoryProposalHandler
    from cognivault.orchestration.acceptance import _GuidedHandler
    g, ref, *_ = scenario(tmp_path)
    t = task('memory.propose', source_ref=ref,
             proposal_scope={'subject':'invented-user', 'classification':'historical_statement'})
    output = dict(quote='engineering is my goal', subject='invented-user', predicate='reported_goal',
                  value='engineering', classification='historical_statement', basis='Original historical quote; pending review.')
    async def check():
        handler = MemoryProposalHandler(InProcessGatewayPort(g))
        prepared = await handler.prepare(t)
        assert prepared['proposal_scope'] == t.inputs['proposal_scope']
        guided = await _GuidedHandler(handler, 'memory.propose').prepare(t)
        assert guided['expected_output_shape']['subject'] == 'invented-user'
        assert guided['expected_output_shape']['classification'] == 'historical_statement'
        for field, bad in [('subject','different-user'), ('classification','fact_update')]:
            with pytest.raises(ValueError):
                handler.validate(t, result({**output, field:bad}), prepared)
        good = result(output, [ref])
        handler.validate(t, good, prepared)
        saved = await handler.commit(t, good, 'scoped-synthetic-memory')
        assert saved['candidate']['state'] == 'pending'
        assert await handler.verify(t, good, saved)
    asyncio.run(check())


@pytest.mark.parametrize('scope', [None, {}, {'subject':'x','classification':'fact_update'},
    {'subject':'','classification':'historical_statement'},
    {'subject':'x','classification':'historical_statement','admin':True}])
def test_memory_rejects_invalid_historical_scope_before_any_gateway_call(scope):
    from cognivault.orchestration.domains import MemoryProposalHandler
    class NoCalls:
        async def call(self, *_):
            pytest.fail('Invalid scope must fail before data access')
    t = task('memory.propose', source_ref='history:'+'a'*64, proposal_scope=scope)
    async def check():
        with pytest.raises(ValueError):
            await MemoryProposalHandler(NoCalls()).prepare(t)
    asyncio.run(check())


def test_study_roundtrip_and_idempotent_version(tmp_path):
    from cognivault.orchestration.domains import InProcessGatewayPort, StudyAnalysisHandler
    g, _, doc, wrong, _ = scenario(tmp_path)
    t = task('study.analyze', source_id=wrong['source_id'])
    r = result(dict(analysis=analysis(), source_refs=[doc['document_uri']], study_relations=[]))
    async def check():
        h = StudyAnalysisHandler(InProcessGatewayPort(g))
        prepared = await h.prepare(t)
        h.validate(t, r, prepared)
        receipt = await h.commit(t, r, 'synthetic-study')
        assert await h.verify(t, r, receipt)
        assert await h.commit(t, r, 'synthetic-study') == receipt
        assert g.get_wrong_answer_bundle(wrong['source_id'])['total'] == 1
        receipt['analysis']['reasoning'] = 'tampered'
        assert not await h.verify(t, r, receipt)
    asyncio.run(check())


def test_history_summary_is_derived_and_original_unchanged(tmp_path):
    from cognivault.orchestration.domains import InProcessGatewayPort, HistorySummaryHandler
    g, ref, *_ = scenario(tmp_path)
    original = g.fetch_history_item(ref)
    t = task('history.summarize', item_id=ref)
    r = result(dict(summary='Synthetic goal summary', source_refs=[ref], content_sha256=original['item']['content_sha256']))
    async def check():
        h = HistorySummaryHandler(InProcessGatewayPort(g))
        prepared = await h.prepare(t)
        h.validate(t, r, prepared)
        receipt = await h.commit(t, r, 'synthetic-history')
        assert receipt['persisted'] is False
        assert await h.verify(t, r, receipt)
        assert await h.commit(t, r, 'synthetic-history') == receipt
    asyncio.run(check())
    assert g.fetch_history_item(ref) == original


def test_memory_only_proposes_and_preserves_quote(tmp_path):
    from cognivault.orchestration.domains import InProcessGatewayPort, MemoryProposalHandler
    g, ref, *_ = scenario(tmp_path)
    t = task('memory.propose', source_ref=ref)
    r = result(dict(quote='engineering is my goal', subject='self', predicate='long_term_goal',
                    value='engineering', classification='fact_update', basis='Explicit synthetic quote'))
    async def check():
        h = MemoryProposalHandler(InProcessGatewayPort(g))
        prepared = await h.prepare(t)
        h.validate(t, r, prepared)
        receipt = await h.commit(t, r, 'synthetic-memory')
        assert receipt['requires_review'] is True
        assert receipt['candidate']['state'] == 'pending'
        assert await h.verify(t, r, receipt)
        assert (await h.commit(t, r, 'synthetic-memory'))['candidate'] == receipt['candidate']
        assert g.search_memory('long_term_goal')['total'] == 0
    asyncio.run(check())


@pytest.mark.parametrize('field,value', [('tool','commit_memory_candidate'), ('quote','invented quote'), ('source_ref','history:'+'0'*64)])
def test_memory_rejects_injected_fields_and_unquoted_evidence(tmp_path, field, value):
    from cognivault.orchestration.domains import InProcessGatewayPort, MemoryProposalHandler
    g, ref, *_ = scenario(tmp_path)
    t = task('memory.propose', source_ref=ref)
    output = dict(quote='engineering is my goal', subject='self', predicate='goal2', value='engineering',
                  classification='fact_update', basis='Explicit quote')
    output[field] = value
    async def check():
        h = MemoryProposalHandler(InProcessGatewayPort(g))
        with pytest.raises(ValueError):
            h.validate(t, result(output), await h.prepare(t))
    asyncio.run(check())


def test_allowlist_and_reader_denied(tmp_path):
    from cognivault.orchestration.domains import InProcessGatewayPort, MemoryProposalHandler
    g, ref, *_ = scenario(tmp_path)
    g.capabilities = frozenset({'read'})
    async def check():
        port = InProcessGatewayPort(g)
        with pytest.raises(GatewayError):
            await port.call('commit_memory_candidate', {'candidate_id':'candidate:'+'0'*32})
        h = MemoryProposalHandler(port)
        with pytest.raises(GatewayError) as error:
            await h.commit(task('memory.propose', source_ref=ref), result(dict(quote='engineering is my goal',
                subject='self', predicate='goal2', value='engineering', classification='fact_update', basis='Explicit')), 'reader')
        assert error.value.code == 'PERMISSION_DENIED'
    asyncio.run(check())


def test_history_ingest_preserves_raw_source_and_replays(tmp_path):
    import base64
    from test_source_gateway import setup, manifest
    from cognivault.orchestration.domains import InProcessGatewayPort, HistoryIngestHandler
    g, inbox = setup(tmp_path)
    raw = b'{"synthetic":"no guessed roles"}\n'
    digest = manifest(inbox, raw=raw)
    t = task('history.ingest', relative_manifest='manifest.json', expected_manifest_sha256=digest)
    t.grants |= {'ingest'}
    r = result({'manifest_sha256': digest, 'approved': True})
    async def check():
        h = HistoryIngestHandler(InProcessGatewayPort(g))
        h.validate(t, r, await h.prepare(t))
        first = await h.commit(t, r, 'ingest')
        assert await h.verify(t, r, first)
        second = await h.commit(t, r, 'ingest')
        assert await h.verify(t, r, second)
        assert first['results'][0]['source_id'] == second['results'][0]['source_id']
        fetched = g.fetch_history_source(first['results'][0]['source_id'])
        assert base64.b64decode(fetched['content_base64']) == raw
        assert first['canonical_messages_created'] == 0
    asyncio.run(check())


def test_retrieval_preserves_gateway_status_and_blocks_forged_evidence(tmp_path):
    from cognivault.orchestration.domains import InProcessGatewayPort, ThreeDomainRetrievalHandler
    g, *_ = scenario(tmp_path)
    t = task('evidence.retrieve', queries={'history':'missing-synthetic-query'})
    async def check():
        h = ThreeDomainRetrievalHandler(InProcessGatewayPort(g))
        prepared = await h.prepare(t)
        assert prepared['status'] == 'NO_EVIDENCE'
        r = result({'selected_refs': []})
        h.validate(t, r, prepared)
        receipt = await h.commit(t, r, 'retrieve')
        assert receipt['status'] == 'NO_EVIDENCE'
        assert await h.verify(t, r, receipt)
        with pytest.raises(ValueError):
            h.validate(t, result({'selected_refs':['history:'+'0'*64]}), prepared)
    asyncio.run(check())



def test_connected_mcp_port_roundtrip_and_permission_error(tmp_path):
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session
    from cognivault.transports.mcp_stdio import create_mcp_server
    from cognivault.orchestration.domains import MCPGatewayPort
    g, ref, *_ = scenario(tmp_path)
    g.capabilities = frozenset({'read'})
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as session:
            port = MCPGatewayPort(session)
            assert (await port.call('fetch_history_item', {'item_id':ref}))['item']['item_id'] == ref
            with pytest.raises(GatewayError) as error:
                await port.call('propose_memory_candidate', dict(source_ref=ref, quote='engineering is my goal',
                    subject='self', predicate='goal', value='engineering', classification='fact_update',
                    confidence=.9, basis='Synthetic quote'))
            assert error.value.code == 'PERMISSION_DENIED'
    anyio.run(check)


def test_every_allowed_operation_has_identical_sdk_and_real_mcp_result_shape(tmp_path, monkeypatch):
    """Real MCP encoding/dispatch; invented SDK replies, no business writes.

    This is a transport contract test, not Desktop Host acceptance evidence.
    """
    import anyio
    from copy import deepcopy
    from mcp.shared.memory import create_connected_server_and_client_session
    from cognivault.adapters.history import SQLiteHistoryBackend
    from cognivault.adapters.memory import SQLiteMemoryStore
    from cognivault.config import AppConfig
    from cognivault.gateway import Gateway
    from cognivault.transports.mcp_stdio import create_mcp_server
    from cognivault.orchestration.domains import (_ALLOWED, _MCP_RESULT_SHAPES,
                                                 InProcessGatewayPort, MCPGatewayPort)
    history_id, message_id = 'history:'+'0'*64, 'message:'+'1'*64
    source_id, asset_uri = 'source-file:'+'2'*64, 'asset://sha256/'+'3'*64
    document_uri, wrong_id = 'document://sha256/'+'4'*64, 'wrong-answer://sha256/'+'5'*64
    candidate_id = 'candidate:'+'6'*32
    cases = {
        'get_wrong_answer_bundle': ({'source_id':wrong_id}, {'source':{'source_id':wrong_id}, 'analyses':[], 'total':0, 'has_more':False}),
        'save_wrong_answer_analysis': ({'source_id':wrong_id, 'analysis':analysis(), 'source_refs':[document_uri], 'study_relations':[], 'idempotency_key':'synthetic-transport'}, {'analysis':{'source_id':wrong_id, 'version':1}}),
        'fetch_history_item': ({'item_id':history_id}, {'item':{'item_id':history_id}}),
        'fetch_canonical_message': ({'message_id':message_id}, {'message':{'message_id':message_id}, 'content_base64':'eA==', 'content_bytes':1, 'content_sha256':'7'*64, 'offset':0, 'has_more':False}),
        'search_memory': ({'query':'synthetic'}, {'memories':[], 'total':0}),
        'propose_memory_candidate': ({'source_ref':message_id, 'quote':'synthetic', 'subject':'self', 'predicate':'goal', 'value':'physics', 'classification':'historical_statement', 'confidence':.9, 'basis':'synthetic'}, {'candidate':{'candidate_id':candidate_id}, 'reused':False}),
        'fetch_memory_candidate': ({'candidate_id':candidate_id}, {'candidate':{'candidate_id':candidate_id}, 'competing_memories':[], 'content_trust':'untrusted_data', 'currency_verified':False, 'snapshot_scope':'synthetic', 'review_instruction':'synthetic'}),
        'retrieve_evidence': ({'queries':{'history':'synthetic'}}, {'status':'NO_EVIDENCE', 'evidence':[], 'domains':{}}),
        'ingest_history_sources': ({'relative_manifest':'manifest.json', 'expected_manifest_sha256':'8'*64}, {'results':[], 'errors':0, 'has_more':False, 'manifest_sha256':'8'*64, 'canonical_messages_created':0}),
        'fetch_history_source': ({'source_id':source_id}, {'source':{'source_id':source_id}, 'offset':0, 'content_base64':'eA==', 'has_more':False}),
        'verify_history_source': ({'source_id':source_id}, {'verified':True, 'sha256':'9'*64, 'byte_count':1}),
        'fetch_document_page': ({'document_uri':document_uri, 'page_number':1}, {'page':{'document_uri':document_uri, 'text':'synthetic'}}),
        'fetch_asset': ({'asset_uri':asset_uri}, {'asset_uri':asset_uri, 'media_type':'image/png', 'size':1, 'offset':0, 'content_base64':'eA==', 'has_more':False}),
    }
    assert set(cases) == _ALLOWED
    assert set(_MCP_RESULT_SHAPES) == _ALLOWED
    gateway = Gateway(AppConfig('0.9.0', history_migration_inbox=tmp_path), None,
                      SQLiteHistoryBackend(tmp_path/'unused-history.db'),
                      memory_store=SQLiteMemoryStore(tmp_path/'unused-memory.db'),
                      capabilities=frozenset({'read','write','ingest'}))
    for name, (_, value) in cases.items():
        monkeypatch.setattr(gateway, name, lambda *args, _value=value, **kwargs: deepcopy(_value))
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as session:
            sdk, mcp = InProcessGatewayPort(gateway), MCPGatewayPort(session)
            for name, (arguments, expected) in cases.items():
                direct = await sdk.call(name, arguments)
                wire = await mcp.call(name, arguments)
                assert wire == direct == expected, name
    anyio.run(check)


def test_study_prepare_over_real_mcp_matches_gateway_sdk(tmp_path):
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session
    from cognivault.transports.mcp_stdio import create_mcp_server
    from cognivault.orchestration.domains import InProcessGatewayPort, MCPGatewayPort, StudyAnalysisHandler
    gateway, _, _, source, _ = scenario(tmp_path)
    request = task('study.analyze', source_id=source['source_id'], include_source=True)
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as session:
            actual = await StudyAnalysisHandler(MCPGatewayPort(session)).prepare(request)
            expected = await StudyAnalysisHandler(InProcessGatewayPort(gateway)).prepare(request)
            assert actual == expected
            assert actual['bundle']['source']['source_id'] == source['source_id']
            assert actual['material']['page']['text']
    anyio.run(check)


def test_memory_search_real_gateway_and_mcp_preserve_sdk_shape(tmp_path):
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session
    from cognivault.transports.mcp_stdio import create_mcp_server
    from cognivault.orchestration.domains import MCPGatewayPort
    gateway, *_ = scenario(tmp_path)
    expected = gateway.search_memory('goal')
    assert set(expected) == {'memories', 'total'}
    assert expected['total'] == 1
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as session:
            assert await MCPGatewayPort(session).call('search_memory', {'query':'goal'}) == expected
    anyio.run(check)


def test_history_ingest_reused_real_gateway_and_mcp_preserve_sdk_shape(tmp_path, monkeypatch):
    import anyio
    from copy import deepcopy
    from mcp.shared.memory import create_connected_server_and_client_session
    from cognivault.transports.mcp_stdio import create_mcp_server
    from cognivault.orchestration.domains import MCPGatewayPort
    from test_source_gateway import setup, manifest
    gateway, inbox = setup(tmp_path)
    digest = manifest(inbox)
    first = gateway.ingest_history_sources('manifest.json', digest)
    assert type(first['errors']) is int and first['errors'] == 0
    original, actual_sdk_replies = gateway.ingest_history_sources, []
    def capture_actual_reply(*args, **kwargs):
        reply = original(*args, **kwargs)
        actual_sdk_replies.append(deepcopy(reply))
        return reply
    monkeypatch.setattr(gateway, 'ingest_history_sources', capture_actual_reply)
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as session:
            reply = await MCPGatewayPort(session).call('ingest_history_sources', {
                'relative_manifest':'manifest.json', 'expected_manifest_sha256':digest})
            assert len(actual_sdk_replies) == 1
            assert reply == actual_sdk_replies[0]
            assert reply['reused'] == 2 and reply['imported'] == 0
            assert type(reply['errors']) is int and reply['errors'] == 0
    anyio.run(check)


def test_mcp_port_rejects_empty_success_for_every_allowed_operation():
    from cognivault.orchestration.domains import _ALLOWED, MCPGatewayPort
    class Session:
        async def call_tool(self, *args):
            return SimpleNamespace(structuredContent={'ok':True}, isError=False)
    async def check():
        for name in _ALLOWED:
            with pytest.raises(GatewayError) as error:
                await MCPGatewayPort(Session()).call(name, {})
            assert error.value.code == 'BACKEND_BAD_OUTPUT'
    asyncio.run(check())


@pytest.mark.parametrize('payload', [
    {'ok':True}, {'ok':True, 'bundle':None}, {'ok':True, 'bundle':[]},
    {'ok':True, 'bundle':{'source':{}, 'analyses':[], 'total':0, 'has_more':False}, 'unexpected':{}},
    {'ok':True, 'bundle':{'source':{}, 'analyses':[], 'total':False, 'has_more':False}},
    {'ok':True, 'bundle':{'source':{}, 'analyses':[], 'total':0, 'has_more':0}},
])
def test_mcp_port_rejects_missing_or_malformed_named_bundle(payload):
    from cognivault.orchestration.domains import MCPGatewayPort
    class Session:
        async def call_tool(self, *args):
            return SimpleNamespace(structuredContent=payload, isError=False)
    with pytest.raises(GatewayError) as error:
        asyncio.run(MCPGatewayPort(Session()).call('get_wrong_answer_bundle', {'source_id':'synthetic'}))
    assert error.value.code == 'BACKEND_BAD_OUTPUT'


@pytest.mark.parametrize('malformed', [None, {}, {'ok':True}, {'ok':False,'error':{'code':[]}}])
def test_mcp_port_rejects_malformed_payload(malformed):
    from cognivault.orchestration.domains import MCPGatewayPort
    class ExistingSession:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(structuredContent=malformed, isError=True)
    async def check():
        with pytest.raises(GatewayError):
            await MCPGatewayPort(ExistingSession()).call('fetch_history_item', {'item_id':'history:'+'0'*64})
    asyncio.run(check())


def test_memory_conflict_stays_pending_with_gateway_relations(tmp_path):
    from cognivault.orchestration.domains import InProcessGatewayPort, MemoryProposalHandler
    g, ref, *_ = scenario(tmp_path)
    r = result(dict(quote='engineering is my goal', subject='self', predicate='goal', value='engineering',
                    classification='fact_update', basis='Synthetic original quoted'), evidence=(ref,))
    async def check():
        h = MemoryProposalHandler(InProcessGatewayPort(g))
        receipt = await h.commit(task('memory.propose', source_ref=ref), r, 'conflict')
        assert receipt['requires_review']
        assert receipt['candidate']['state'] == 'pending'
        assert receipt['candidate']['relations'][0]['kind'] == 'possible_conflict'
        assert await h.verify(task('memory.propose', source_ref=ref), r, receipt)
        assert g.search_memory('goal')['memories'][0]['value'] == 'engineering with physics'
    asyncio.run(check())


def test_study_rejects_model_chosen_refs_and_evidence(tmp_path):
    from cognivault.orchestration.domains import InProcessGatewayPort, StudyAnalysisHandler
    g, ref, doc, wrong, *_ = scenario(tmp_path)
    t = task('study.analyze', source_id=wrong['source_id'])
    async def check():
        h = StudyAnalysisHandler(InProcessGatewayPort(g))
        prepared = await h.prepare(t)
        with pytest.raises(ValueError):
            h.validate(t, result(dict(analysis=analysis(), source_refs=[ref], study_relations=[])), prepared)
        with pytest.raises(ValueError):
            h.validate(t, result(dict(analysis=analysis(), source_refs=[doc['document_uri']], study_relations=[]),
                                  evidence=('unprepared-ref',)), prepared)
    asyncio.run(check())


def test_retrieval_unavailability_is_preserved():
    from cognivault.orchestration.domains import InProcessGatewayPort, ThreeDomainRetrievalHandler
    from cognivault.gateway import Gateway
    from cognivault.config import AppConfig
    g = Gateway(AppConfig('0.8.0'), None, capabilities=frozenset({'read'}))
    async def check():
        h = ThreeDomainRetrievalHandler(InProcessGatewayPort(g))
        t = task('evidence.retrieve', queries={'history':'goal', 'memory':'goal'})
        receipt = await h.commit(t, result({'selected_refs':[]}), 'unavailable')
        assert receipt['status'] == 'UNAVAILABLE' and receipt['partial'] is True
        assert await h.verify(t, result({'selected_refs':[]}), receipt)
    asyncio.run(check())


def test_study_writes_reported_adapter_provenance(tmp_path):
    from cognivault.orchestration.domains import InProcessGatewayPort, StudyAnalysisHandler
    g, _, doc, wrong, _ = scenario(tmp_path)
    t = task('study.analyze', source_id=wrong['source_id'])
    t.task_id = 'task-synthetic'
    r = result(dict(analysis=analysis(), source_refs=[doc['document_uri']], study_relations=[]))
    r.adapter_id = 'agent-synthetic'
    async def check():
        receipt = await StudyAnalysisHandler(InProcessGatewayPort(g)).commit(t, r, 'provenance')
        p = receipt['analysis']['write_provenance']
        assert p['reported_agent'] == 'agent-synthetic'
        assert p['reported_client'] == 'cognivault-orchestrator'
        assert p['run_id'] == 'task-synthetic'
    asyncio.run(check())



@pytest.mark.parametrize('kind', ['study.analyze', 'history.summarize', 'memory.propose', 'history.ingest', 'evidence.retrieve'])
def test_engine_domain_integration(tmp_path, kind):
    from cognivault.orchestration.adapters import Registry, SyntheticAdapter
    from cognivault.orchestration.engine import Engine
    from cognivault.orchestration.journal import FileJournal
    from cognivault.orchestration.models import Task, Result, Descriptor
    from cognivault.orchestration.domains import (InProcessGatewayPort, StudyAnalysisHandler,
        HistorySummaryHandler, MemoryProposalHandler, HistoryIngestHandler, ThreeDomainRetrievalHandler)
    if kind == 'history.ingest':
        from test_source_gateway import setup, manifest
        g, inbox = setup(tmp_path)
        inputs = dict(relative_manifest='manifest.json', expected_manifest_sha256=manifest(inbox))
    else:
        g, ref, doc, wrong, _ = scenario(tmp_path)
        inputs = {'study.analyze':dict(source_id=wrong['source_id']),
                  'history.summarize':dict(item_id=ref), 'memory.propose':dict(source_ref=ref),
                  'evidence.retrieve':dict(queries={'history':'nonexistent-synthetic-query'})}[kind]
    handlers = {'study.analyze':StudyAnalysisHandler, 'history.summarize':HistorySummaryHandler,
                'memory.propose':MemoryProposalHandler, 'history.ingest':HistoryIngestHandler,
                'evidence.retrieve':ThreeDomainRetrievalHandler}
    handler = handlers[kind](InProcessGatewayPort(g))
    def callback(t, prepared):
        if kind == 'study.analyze':
            output = dict(analysis=analysis(), source_refs=[doc['document_uri']], study_relations=[])
            evidence = (wrong['source_id'],)
        elif kind == 'history.summarize':
            output = dict(summary='Synthetic summary', source_refs=[ref], content_sha256=prepared['item']['content_sha256'])
            evidence = (ref,)
        elif kind == 'memory.propose':
            output = dict(quote='engineering is my goal', subject='self', predicate='goal2', value='engineering',
                          classification='fact_update', basis='Explicit original')
            evidence = (ref,)
        elif kind == 'history.ingest':
            output = dict(manifest_sha256=inputs['expected_manifest_sha256'], approved=True)
            evidence = ()
        else:
            output, evidence = {'selected_refs':[]}, ()
        return Result(t.task_id, t.correlation_id, 'synthetic', '1', output, .9, evidence)
    registry = Registry()
    registry.register(SyntheticAdapter(Descriptor('synthetic', '1', frozenset({kind}), frozenset({'structured'}), 'synthetic'), callback))
    engine = Engine(registry, FileJournal(tmp_path/'journal'), {kind:handler})
    t = Task('domain-integration', 'correlation', kind, inputs, frozenset({'read','write','ingest'}))
    async def check():
        receipt = await engine.run(t, 'synthetic')
        assert receipt.state == ('WAITING_FOR_REVIEW' if kind == 'memory.propose' else 'COMPLETED'), receipt
        assert receipt.verified
        replay = await engine.run(t, 'synthetic')
        assert replay.state == receipt.state
    asyncio.run(check())


def test_memory_invalid_classification_is_value_error(tmp_path):
    from cognivault.orchestration.domains import InProcessGatewayPort, MemoryProposalHandler
    g, ref, *_ = scenario(tmp_path)
    async def check():
        h = MemoryProposalHandler(InProcessGatewayPort(g))
        t = task('memory.propose', source_ref=ref)
        prepared = await h.prepare(t)
        with pytest.raises(ValueError, match='INVALID_DOMAIN_RESULT'):
            h.validate(t, result(dict(quote='engineering is my goal', subject='self', predicate='goal', value='engineering',
                                    classification=['fact_update'], basis='original')), prepared)
    asyncio.run(check())


def test_study_readback_recovers_older_version_outside_first_page(tmp_path):
    from cognivault.orchestration.domains import InProcessGatewayPort, StudyAnalysisHandler
    g, _, doc, wrong, _ = scenario(tmp_path)
    t = task('study.analyze', source_id=wrong['source_id'])
    r = result(dict(analysis=analysis(), source_refs=[doc['document_uri']], study_relations=[]))
    async def check():
        h = StudyAnalysisHandler(InProcessGatewayPort(g))
        first = await h.commit(t, r, 'old-version')
        for index in range(1, 21):
            g.save_wrong_answer_analysis(wrong['source_id'], analysis(), [doc['document_uri']], [],
                                         f'newer-{index}', expected_version=index)
        assert await h.verify(t, r, first)
    asyncio.run(check())


def test_study_rejects_more_knowledge_points_than_gateway_allows(tmp_path):
    from cognivault.orchestration.domains import InProcessGatewayPort, StudyAnalysisHandler
    g, _, doc, wrong, _ = scenario(tmp_path)
    t = task('study.analyze', source_id=wrong['source_id'])
    output = dict(analysis={**analysis(), 'knowledge_points':[f'point-{i}' for i in range(21)]},
                  source_refs=[doc['document_uri']], study_relations=[])
    async def check():
        h = StudyAnalysisHandler(InProcessGatewayPort(g))
        with pytest.raises(ValueError):
            h.validate(t, result(output), await h.prepare(t))
    asyncio.run(check())


def test_study_include_source_returns_gateway_page_text(tmp_path):
    from cognivault.orchestration.domains import InProcessGatewayPort, StudyAnalysisHandler
    g, _, doc, wrong, _ = scenario(tmp_path)
    async def check():
        h = StudyAnalysisHandler(InProcessGatewayPort(g))
        prepared = await h.prepare(task('study.analyze', source_id=wrong['source_id'], include_source=True))
        assert prepared['material']['kind'] == 'document_page'
        assert prepared['material']['page'] == g.fetch_document_page(doc['document_uri'], 1)['page']
        assert 'content_base64' not in prepared['material']
        assert 'material' not in await h.prepare(task('study.analyze', source_id=wrong['source_id']))
    asyncio.run(check())


def test_study_include_image_uses_bounded_gateway_chunks_and_original_hash(tmp_path):
    import base64
    import hashlib
    from cognivault.orchestration.domains import InProcessGatewayPort, StudyAnalysisHandler
    g, *_ = scenario(tmp_path)
    raw = b'\x89PNG\r\n\x1a\n' + b'synthetic'*10000
    asset = g.register_asset('image/png', base64.b64encode(raw).decode())['asset']
    source = g.register_wrong_answer_source(asset['asset_uri'], 'Synthetic image exercise', 'Synthetic wrong')['source']
    t = task('study.analyze', source_id=source['source_id'], include_source=True)
    t.required_capabilities = frozenset({'image', 'structured'})
    class RecordingPort(InProcessGatewayPort):
        def __init__(self, gateway):
            super().__init__(gateway)
            self.lengths = []
        async def call(self, name, arguments):
            if name == 'fetch_asset':
                self.lengths.append(arguments['length'])
            return await super().call(name, arguments)
    async def check():
        port = RecordingPort(g)
        prepared = await StudyAnalysisHandler(port).prepare(t)
        material = prepared['material']
        assert material['kind'] == 'image'
        assert material['asset_uri'] == asset['asset_uri']
        assert material['sha256'] == hashlib.sha256(raw).hexdigest()
        assert material['media_type'] == 'image/png'
        assert base64.b64decode(material['content_base64']) == raw
        assert len(port.lengths) == 2 and max(port.lengths) <= 65536
    asyncio.run(check())


@pytest.mark.parametrize('case', ['oversized', 'media_type', 'capability'])
def test_study_image_rejects_oversize_unsupported_type_or_missing_capability(tmp_path, case):
    import base64
    from cognivault.orchestration.domains import InProcessGatewayPort, StudyAnalysisHandler
    g, *_ = scenario(tmp_path)
    raw = b'\x89PNG\r\n\x1a\n' + b'x'*(1048576 if case == 'oversized' else 16)
    media_type = 'application/octet-stream' if case == 'media_type' else 'image/png'
    asset = g.register_asset(media_type, base64.b64encode(raw).decode())['asset']
    source = g.register_wrong_answer_source(asset['asset_uri'], 'Synthetic exercise', 'Synthetic wrong')['source']
    t = task('study.analyze', source_id=source['source_id'], include_source=True)
    if case != 'capability':
        t.required_capabilities = frozenset({'image','structured'})
    async def check():
        with pytest.raises((ValueError, GatewayError)):
            await StudyAnalysisHandler(InProcessGatewayPort(g)).prepare(t)
    asyncio.run(check())



def test_study_image_rejects_gateway_bytes_with_wrong_hash(tmp_path):
    import base64
    from cognivault.orchestration.domains import InProcessGatewayPort, StudyAnalysisHandler
    g, *_ = scenario(tmp_path)
    raw = b'\x89PNG\r\n\x1a\nsynthetic'
    asset = g.register_asset('image/png', base64.b64encode(raw).decode())['asset']
    source = g.register_wrong_answer_source(asset['asset_uri'], 'Synthetic image', 'Wrong')['source']
    t = task('study.analyze', source_id=source['source_id'], include_source=True)
    t.required_capabilities = frozenset({'image','structured'})
    class CorruptPort(InProcessGatewayPort):
        async def call(self, name, arguments):
            response = await super().call(name, arguments)
            if name == 'fetch_asset':
                response['content_base64'] = base64.b64encode(raw[:-1]+b'!').decode()
            return response
    async def check():
        with pytest.raises(GatewayError) as error:
            await StudyAnalysisHandler(CorruptPort(g)).prepare(t)
        assert error.value.code == 'CONFLICT'
    asyncio.run(check())
