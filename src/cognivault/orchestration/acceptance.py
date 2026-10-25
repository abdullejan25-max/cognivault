"""Reproducible invented-data SDK acceptance; never Desktop Host evidence.

Default: synthetic callback. --live: explicit external HTTPS model invocation
using only invented material and an environment credential. No private config.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import tempfile

from ..adapters.documents import SQLiteDocumentStore
from ..adapters.history import SQLiteHistoryBackend
from ..adapters.memory import SQLiteMemoryStore
from ..config import AppConfig
from ..gateway import Gateway
from .adapters import Registry, SyntheticAdapter
from .domains import (HistorySummaryHandler, InProcessGatewayPort,
                      MemoryProposalHandler, StudyAnalysisHandler,
                      ThreeDomainRetrievalHandler)
from .engine import Engine
from .executors import HttpsChatAdapter
from .journal import FileJournal
from .models import AdapterError, Descriptor, Result, fingerprint


KINDS = ('study.analyze', 'history.summarize', 'memory.propose', 'evidence.retrieve')
SHAPES = {
    'study.analyze': {'analysis': {'error_type':'string', 'knowledge_points':['string'],
        'reasoning':'string', 'correct_solution':'string', 'review_advice':'string'},
        'source_refs':['exact prepared source_refs'], 'study_relations':['exact prepared study_relations']},
    'history.summarize': {'summary':'string', 'source_refs':['exact item_id'],
                          'content_sha256':'exact prepared item content_sha256'},
    'memory.propose': {'quote':'exact substring of original text', 'subject':'string',
        'predicate':'string', 'value':'string',
        'classification':'fact_update|preference_change|historical_statement|inference', 'basis':'string'},
    'evidence.retrieve': {'selected_refs':['citation from prepared evidence']},
}
GUIDANCE = {
    'study.analyze': 'Analyze the source exercise and learner answer as a self-contained mathematical exercise using the stated forces and mass. This source is a registered text/plain document: OCR and PDF text-layer checks are not applicable, and no experimental verification or real physical setup is requested. Copy prepared source_refs and study_relations exactly. Cite source_id or source_refs as evidence.',
    'history.summarize': 'Summarize only the single original item, preserving its role and historical context. Copy source_refs and content_sha256 constants from expected_output_shape exactly, without recomputing a hash. The evidence array must contain only the same single item_id, not its lineage source IDs.',
    'memory.propose': 'Propose one atomic candidate about the learning goal reported by this source, grounded in an exact quote of original text. Do not claim independent verification or infer a change from an unavailable earlier state. Choose classification according to supplied role and historical context. Cite only source_ref as evidence. This remains pending human review.',
    'evidence.retrieve': 'The bounded task is keyword retrieval: select citations of physics-related records in history, memory and study (query physics in each domain). Do not synthesize a present goal or judge independent corroboration. Select only available citations in selected_refs and evidence. Retain source status as given; an unverified or historical record is still retrievable as such. Preserve uncertainty if sources needed for this selection are missing.',
}
EPISTEMIC_GUIDANCE = (
    'Confidence measures fidelity of this bounded operation to the supplied evidence, '
    'not independent confirmation of the invented person or real-world truth. '
    'List in uncertainties actual missing conditions, conflicts or ambiguities that '
    'affect this requested operation. Preserve every such uncertainty. '
    'The uncertainties array is not a general provenance or caveat list: if no '
    'unresolved ambiguity affects the requested output, use an empty array. '
    'Describe known provenance limits, historical status or pending review within '
    'the relevant summary, analysis or basis fields. These known statuses alone '
    'do not prevent faithfully summarizing, proposing or selecting supplied evidence. '
    'Do not invent evidence, infer missing facts or choose confidence to satisfy a threshold.'
)


class _GuidedHandler:
    """Trusted task policy is separate from untrusted source material."""
    def __init__(self, handler, kind):
        self.handler, self.kind = handler, kind
        self.required_grants = handler.required_grants
        self.allow_empty_evidence = getattr(handler, 'allow_empty_evidence', False)

    async def prepare(self, task):
        prepared = await self.handler.prepare(task)
        if self.kind == 'study.analyze':
            # Only exercise evidence is sent externally. Historical writer identity
            # and PDF/OCR bookkeeping do not establish the mathematical answer.
            source = prepared['bundle']['source']
            prepared['bundle'] = {'source': {key: value for key, value in source.items()
                if key in {'source_id', 'source_uri', 'page_number', 'question_text',
                           'student_answer', 'text_origin'}}}
            page = prepared.get('material', {}).get('page')
            if page is not None:
                prepared['material']['page'] = {key: value for key, value in page.items()
                    if key in {'document_uri', 'source_asset_uri', 'page_number', 'text',
                               'text_origin', 'truncated'}}
        shape = SHAPES[self.kind]
        if self.kind == 'history.summarize':
            item = prepared['item']
            prepared['item'] = {key: value for key, value in item.items()
                if key in {'item_id', 'role', 'occurred_at', 'created_at', 'content', 'content_sha256'}}
            shape = {'summary':'string', 'source_refs':[task.inputs['item_id']],
                     'content_sha256':item['content_sha256']}
        return {**prepared,
                'expected_output_shape': shape,
                'task_guidance': GUIDANCE[self.kind] + ' ' + EPISTEMIC_GUIDANCE}

    def validate(self, *args):
        return self.handler.validate(*args)

    async def commit(self, *args):
        return await self.handler.commit(*args)

    async def verify(self, *args):
        return await self.handler.verify(*args)


def _fixture(root: Path):
    """New temporary schema only; every business mutation uses Gateway."""
    assets, inbox = root / 'objects', root / 'inbox'
    assets.mkdir(); inbox.mkdir()
    history = SQLiteHistoryBackend(root / 'history.db')
    history.initialize()
    cfg = AppConfig('0.9.0', history_database=history.database_path,
                    history_migration_inbox=inbox, asset_database=root / 'documents.db',
                    asset_root=assets, memory_database=root / 'memory.db')
    gateway = Gateway(cfg, None, history,
        document_store=SQLiteDocumentStore(assets, cfg.asset_database),
        memory_store=SQLiteMemoryStore(cfg.memory_database),
        capabilities=frozenset({'read', 'write', 'ingest', 'admin'}))
    original = 'My synthetic physics study goal is engineering.'
    rows = [{'type':'session_meta', 'payload':{'id':'synthetic-acceptance'}},
        {'type':'response_item', 'timestamp':'2026-01-01T00:00:00Z',
         'payload':{'type':'message', 'role':'user',
                    'content':[{'type':'input_text', 'text':original}]}}]
    raw = ('\n'.join(json.dumps(row) for row in rows)+'\n').encode()
    (inbox / 'original.jsonl').write_bytes(raw)
    manifest = json.dumps({'schema_version':1, 'entries':[{
        'source_system':'codex', 'source_format':'jsonl', 'record_kind':'raw_session',
        'sha256':hashlib.sha256(raw).hexdigest(), 'byte_count':len(raw),
        'relative_path':'original.jsonl'}]}).encode()
    (inbox / 'manifest.json').write_bytes(manifest)
    ingestion = gateway.ingest_history_sources('manifest.json', hashlib.sha256(manifest).hexdigest())
    snapshot = gateway.history_normalization_snapshot()
    normalization = gateway.normalize_history_sources(snapshot['source_set_sha256'])
    ref = gateway.search_canonical_messages('synthetic physics')['results'][0]['message_id']
    document = gateway.ingest_documents([{
        'title':'Synthetic physics', 'media_type':'text/plain',
        'content_base64':base64.b64encode(b'Newton law: F=ma. In this idealized exercise, a 2 kg block slides right along a horizontal surface in an inertial frame. A 10 N applied force acts right and 2 N kinetic friction acts left. Vertical forces cancel; no other horizontal forces act. Find horizontal acceleration. Acceleration is (10-2)/2=4 m/s^2 right.').decode()}])['documents'][0]
    wrong = gateway.register_wrong_answer_source(document['document_uri'],
        'Synthetic physics: a 2 kg block slides right on a horizontal surface in an inertial frame. Applied force is 10 N right; kinetic friction is 2 N left. Vertical forces cancel and no other horizontal forces act. Find horizontal acceleration.',
        'I answered 5 m/s^2 by forgetting friction.', 1)['source']
    initialization = gateway.initialize_memory()
    memory = gateway.create_memory('synthetic-self', 'physics-goal',
        'engineering', [ref], 'synthetic-seed-goal')
    return gateway, ref, wrong['source_id'], {
        'history_ingest_sha256':fingerprint(ingestion),
        'history_normalization_sha256':fingerprint(normalization),
        'memory_initialization_sha256':fingerprint(initialization),
        'memory_seed_sha256':fingerprint(memory)}


def _synthetic(task, prepared):
    """Fixed invented callback; explicitly synthetic, never live evidence."""
    if task.kind == 'study.analyze':
        output = {'analysis':{'error_type':'omitted friction', 'knowledge_points':['Newton second law'],
            'reasoning':'Subtract friction from applied force before dividing by mass.',
            'correct_solution':'(10-2)/2 = 4 m/s^2', 'review_advice':'Draw every force first.'},
            'source_refs':prepared['source_refs'], 'study_relations':prepared['study_relations']}
        evidence = (task.inputs['source_id'],)
    elif task.kind == 'history.summarize':
        output = {'summary':'The invented user reports an engineering goal for physics study.',
            'source_refs':[task.inputs['item_id']], 'content_sha256':prepared['item']['content_sha256']}
        evidence = (task.inputs['item_id'],)
    elif task.kind == 'memory.propose':
        output = {'quote':prepared['original']['text'], 'subject':'synthetic-self',
            'predicate':'physics-learning-goal', 'value':'engineering',
            'classification':'fact_update', 'basis':'Explicit invented user statement; pending review.'}
        evidence = (task.inputs['source_ref'],)
    else:
        refs = [item['citation'] for item in prepared['evidence']]
        output, evidence = {'selected_refs':refs}, tuple(refs)
    return Result(task.task_id, task.correlation_id, 'synthetic-acceptance', '1', output, .95, evidence)


async def run_acceptance(*, live=False, model=None, executor=None,
                         installation_root=None, credential_path=None):
    from .models import Task
    executor = executor or ('https' if live else 'synthetic')
    live = executor != 'synthetic'
    model = model or ('deepseek-flash' if executor == 'harness' else 'deepseek-chat')
    base = {'gateway_scope':'isolated-sdk-gateway', 'desktop_host_evidence':False,
            'source_scope':'invented-temporary-data',
            'execution_scope':'external-model' if live else 'synthetic'}
    if executor == 'harness':
        if installation_root is None or credential_path is None:
            return {**base, 'status':'BLOCKED', 'code':'EXECUTOR_BLOCKED',
                    'model_invocation_verified':False, 'network_attempted':False, 'domains':[]}, 2
        from .harness_no_tools import HarnessNoToolsAdapter
        adapter = HarnessNoToolsAdapter(installation_root=Path(installation_root),
            credential_path=Path(credential_path), model=model)
    elif executor == 'https':
        adapter = HttpsChatAdapter(model=model)
    else:
        adapter = SyntheticAdapter(Descriptor('synthetic-acceptance', '1',
            frozenset(KINDS), frozenset({'text','structured'}), 'synthetic'), _synthetic)
    if live:
        try:
            ready = await adapter.healthy()
        except AdapterError as error:
            return {**base, 'status':'BLOCKED', 'code':error.code,
                    'model_invocation_verified':False, 'network_attempted':False, 'domains':[]}, 2
        if not ready:
            return {**base, 'status':'BLOCKED', 'code':'AUTH_REQUIRED',
                    'model_invocation_verified':False, 'network_attempted':False, 'domains':[]}, 2
    with tempfile.TemporaryDirectory(prefix='cognivault-synthetic-acceptance-') as directory:
        root = Path(directory)
        gateway, ref, source_id, fixture_proof = _fixture(root)
        port = InProcessGatewayPort(gateway)
        handlers = dict(zip(KINDS, (StudyAnalysisHandler(port), HistorySummaryHandler(port),
                                   MemoryProposalHandler(port), ThreeDomainRetrievalHandler(port))))
        registry = Registry(); registry.register(adapter)
        journal = FileJournal(root / 'journal')
        engine = Engine(registry, journal, {kind:_GuidedHandler(handler, kind) for kind, handler in handlers.items()})
        inputs = {'study.analyze':{'source_id':source_id, 'include_source':True},
                  'history.summarize':{'item_id':ref}, 'memory.propose':{'source_ref':ref},
                  'evidence.retrieve':{'queries':{'history':'physics', 'study':'physics', 'memory':'physics'}}}
        domains = []
        for index, kind in enumerate(KINDS):
            task = Task(f'acceptance-{index}', f'acceptance-correlation-{index}', kind, inputs[kind],
                        frozenset({'read','write'}), external_allowed=live, timeout_s=60, max_attempts=1)
            receipt = await engine.run(task, adapter.descriptor.agent_id)
            record = journal.load(task.task_id)
            domains.append({'kind':kind, 'task_id':task.task_id, 'state':receipt.state,
                'code':receipt.error_code, 'verified':receipt.verified,
                'execution_scope':receipt.execution_scope, 'artifacts':list(receipt.artifacts),
                'input_refs':[value for key, value in inputs[kind].items()
                              if key in {'source_id', 'item_id', 'source_ref'}],
                'result_sha256':record.get('result_hash'),
                'gateway_receipt_sha256':record.get('gateway_receipt_hash'),
                'execution_evidence':receipt.execution_evidence, 'review':receipt.review})
        passed = all(row['verified'] and row['state'] == ('WAITING_FOR_REVIEW' if row['kind'] == 'memory.propose' else 'COMPLETED') for row in domains)
        blocked = any(row['state'] == 'BLOCKED' for row in domains)
        invoked = live and all(row['execution_evidence'] and row['execution_evidence'].get('execution_scope') == 'external-model' for row in domains)
        passed = passed and (not live or invoked)
        return {**base, 'status':'PASS' if passed else 'BLOCKED' if blocked else 'FAIL',
                'model_invocation_verified':bool(invoked), 'domains':domains,
                'fixture_receipts':fixture_proof}, 0 if passed else 2 if blocked else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Send only invented fixture text to the external model')
    parser.add_argument('--executor', choices=('synthetic', 'https', 'harness'),
                        help='Explicit executor; --live is shorthand for https')
    parser.add_argument('--model')
    parser.add_argument('--installation-root', type=Path, help='Trusted local zero-tool harness installation; harness only')
    parser.add_argument('--credential-path', type=Path, help='Existing provider credential file consumed by harness; harness only')
    args = parser.parse_args(argv)
    if args.live and args.executor not in (None, 'https'):
        parser.error('--live selects the https executor')
    if (args.installation_root is not None or args.credential_path is not None) and args.executor != 'harness':
        parser.error('Explicit harness locations require --executor harness')
    try:
        report, code = asyncio.run(run_acceptance(live=args.live, model=args.model,
            executor=args.executor, installation_root=args.installation_root, credential_path=args.credential_path))
    except AdapterError as error:
        report, code = {'status':'BLOCKED' if error.code in AdapterError.BLOCKING else 'FAIL',
            'code':error.code, 'model_invocation_verified':False, 'desktop_host_evidence':False}, 2 if error.code in AdapterError.BLOCKING else 1
    except Exception:
        # No exceptions, file paths, raw material or credentials enter public output.
        report, code = {'status':'FAIL', 'code':'ACCEPTANCE_ERROR', 'desktop_host_evidence':False}, 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
