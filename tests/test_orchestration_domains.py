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
