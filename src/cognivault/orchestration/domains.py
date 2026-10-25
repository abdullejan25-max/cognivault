"""Fixed, bounded domain operations; model output never chooses Gateway tools.

Ports consume an existing Gateway or connected MCP session. Neither port starts
clients, edits host configuration, or reads authoritative storage directly.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Protocol

from ..contracts import ERROR_CODES, GatewayError


class GatewayPort(Protocol):
    async def call(self, name: str, arguments: dict) -> dict: ...


_ALLOWED = frozenset({
    'get_wrong_answer_bundle', 'save_wrong_answer_analysis', 'fetch_history_item',
    'fetch_canonical_message', 'search_memory', 'propose_memory_candidate',
    'fetch_memory_candidate', 'retrieve_evidence', 'ingest_history_sources',
    'fetch_history_source', 'verify_history_source', 'fetch_document_page', 'fetch_asset',
})


def _allowed(name):
    if name not in _ALLOWED:
        raise GatewayError('PERMISSION_DENIED', 'Operation is not allowed')


class InProcessGatewayPort:
    """Explicit SDK port for isolated synthetic acceptance, not Host evidence."""
    def __init__(self, gateway):
        self.gateway = gateway

    async def call(self, name: str, arguments: dict) -> dict:
        _allowed(name)
        return deepcopy(getattr(self.gateway, name)(**deepcopy(arguments)))


class MCPGatewayPort:
    """Uses only the supplied, already connected Host session."""
    def __init__(self, session):
        self.session = session

    async def call(self, name: str, arguments: dict) -> dict:
        _allowed(name)
        response = await self.session.call_tool(name, deepcopy(arguments))
        payload = getattr(response, 'structuredContent', None)
        if type(payload) is not dict or type(payload.get('ok')) is not bool:
            raise GatewayError('BACKEND_BAD_OUTPUT', 'Invalid Gateway response')
        if payload['ok'] is False:
            error = payload.get('error', {})
            code = error.get('code') if type(error) is dict else None
            raise GatewayError(code if type(code) is str and code in ERROR_CODES else 'INTERNAL_ERROR', 'Gateway operation failed')
        if getattr(response, 'isError', False):
            raise GatewayError('BACKEND_BAD_OUTPUT', 'Invalid Gateway response')
        return deepcopy({k: v for k, v in payload.items() if k != 'ok'})


def _invalid():
    raise ValueError('INVALID_DOMAIN_RESULT')


def _text(value, limit):
    if type(value) is not str or not value.strip() or len(value) > limit:
        _invalid()


def _shape(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        _invalid()


def _evidence(result, refs):
    if not set(result.evidence) <= set(refs):
        _invalid()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


class _Handler:
    required_grants = frozenset({'read'})

    def __init__(self, port: GatewayPort):
        self.port = port

    def _grants(self, task):
        if not self.required_grants <= task.grants:
            raise GatewayError('PERMISSION_DENIED', 'Task grant is insufficient')


class StudyAnalysisHandler(_Handler):
    required_grants = frozenset({'read', 'write'})

    async def prepare(self, task):
        self._grants(task)
        bundle = await self.port.call('get_wrong_answer_bundle', {'source_id': task.inputs['source_id']})
        prepared = {'bundle': bundle, 'source_refs': task.inputs.get('source_refs', [bundle['source']['source_uri']]),
                    'study_relations': task.inputs.get('study_relations', [])}
        include_source = task.inputs.get('include_source', False)
        if type(include_source) is not bool:
            _invalid()
        if include_source:
            source = bundle['source']
            uri = source['source_uri']
            if re.fullmatch(r'document://sha256/[0-9a-f]{64}', uri):
                page = await self.port.call('fetch_document_page', {
                    'document_uri': uri, 'page_number': source.get('page_number') or 1})
                prepared['material'] = {'kind': 'document_page', 'page': page['page']}
            elif re.fullmatch(r'asset://sha256/[0-9a-f]{64}', uri):
                if 'image' not in getattr(task, 'required_capabilities', frozenset()):
                    raise GatewayError('PERMISSION_DENIED', 'Image capability is required')
                prepared['material'] = await self._image_material(uri)
            else:
                _invalid()
        return prepared

    async def _image_material(self, uri):
        maximum, chunk_size = 1_048_576, 65_536
        content = bytearray()
        size, media_type = None, None
        for _ in range(maximum // chunk_size):
            chunk = await self.port.call('fetch_asset', {
                'asset_uri': uri, 'offset': len(content), 'length': chunk_size})
            if chunk.get('media_type') not in {'image/png', 'image/jpeg', 'image/webp'}:
                raise GatewayError('UNSUPPORTED_MEDIA_TYPE', 'Source image type is unsupported')
            if type(chunk.get('size')) is not int or not 1 <= chunk['size'] <= maximum:
                raise GatewayError('PAYLOAD_TOO_LARGE', 'Source image exceeds bounded review size')
            if size is None:
                size, media_type = chunk['size'], chunk['media_type']
            if (chunk['size'] != size or chunk['media_type'] != media_type
                    or chunk.get('asset_uri') != uri or chunk.get('offset') != len(content)
                    or type(chunk.get('has_more')) is not bool):
                raise GatewayError('BACKEND_BAD_OUTPUT', 'Invalid source image response')
            try:
                piece = base64.b64decode(chunk['content_base64'], validate=True)
            except (ValueError, TypeError, KeyError):
                raise GatewayError('BACKEND_BAD_OUTPUT', 'Invalid source image response') from None
            if not piece or len(piece) > chunk_size or len(content) + len(piece) > size:
                raise GatewayError('BACKEND_BAD_OUTPUT', 'Invalid source image response')
            content.extend(piece)
            if chunk['has_more'] != (len(content) < size):
                raise GatewayError('BACKEND_BAD_OUTPUT', 'Invalid source image response')
            if not chunk['has_more']:
                digest = hashlib.sha256(content).hexdigest()
                if len(content) != size or digest != uri.rsplit('/', 1)[-1]:
                    raise GatewayError('CONFLICT', 'Source image integrity verification failed')
                return {'kind': 'image', 'asset_uri': uri, 'sha256': digest, 'size': size,
                        'media_type': media_type, 'content_base64': base64.b64encode(content).decode('ascii')}
        raise GatewayError('PAYLOAD_TOO_LARGE', 'Source image exceeds bounded review size')

    def validate(self, task, result, prepared):
        output = result.output
        _shape(output, {'analysis', 'source_refs', 'study_relations'})
        _shape(output['analysis'], {'error_type', 'knowledge_points', 'reasoning', 'correct_solution', 'review_advice'})
        for field in ('reasoning', 'correct_solution', 'review_advice'):
            _text(output['analysis'][field], 10000)
        _text(output['analysis']['error_type'], 200)
        points = output['analysis']['knowledge_points']
        if type(points) is not list or not 1 <= len(points) <= 20:
            _invalid()
        for point in points:
            _text(point, 200)
        if output['source_refs'] != prepared['source_refs'] or output['study_relations'] != prepared['study_relations']:
            _invalid()
        _evidence(result, [task.inputs['source_id'], *prepared['source_refs'], *prepared['study_relations']])

    async def commit(self, task, result, key):
        self._grants(task)
        prepared = await self.prepare(task)
        self.validate(task, result, prepared)
        return await self.port.call('save_wrong_answer_analysis', {
            'source_id': task.inputs['source_id'], **result.output,
            'idempotency_key': key, 'expected_version': task.inputs.get('expected_version', 0),
            'provenance': {'reported_agent': result.adapter_id, 'reported_client': 'cognivault-orchestrator', 'run_id': task.task_id}})

    async def verify(self, task, result, receipt):
        from ..gateway import Gateway
        # Compare the formal Gateway's public text projection, not raw model
        # whitespace/control characters that the Gateway deliberately normalizes.
        # This pure formatter does not access any storage; readback stays on port.
        expected = {key: ([Gateway._history_text(point.strip(), 200) for point in value]
                         if key == 'knowledge_points' else
                         Gateway._history_text(value.strip(), 200 if key == 'error_type' else 10000))
                    for key, value in result.output['analysis'].items()}
        saved = receipt.get('analysis', {})
        bundle = await self.port.call('get_wrong_answer_bundle', {'source_id': task.inputs['source_id']})
        target_version = task.inputs.get('expected_version', 0) + 1
        if bundle['has_more'] and not any(r.get('version') == target_version for r in bundle['analyses']):
            offset = max(0, bundle['total'] - target_version)
            if offset > 1000:
                return False  # Current formal Gateway cannot address older offsets.
            bundle = await self.port.call('get_wrong_answer_bundle', {
                'source_id': task.inputs['source_id'], 'offset': offset, 'limit': 20})
        return any(record == saved and all(record.get(k) == v for k, v in expected.items())
                   and record.get('source_refs') == result.output['source_refs']
                   and record.get('study_relations') == result.output['study_relations']
                   and record.get('version') == target_version
                   for record in bundle['analyses'])


class HistorySummaryHandler(_Handler):
    async def prepare(self, task):
        self._grants(task)
        ref = task.inputs['item_id']
        if not re.fullmatch(r'message:[0-9a-f]{64}', ref):
            return await self.port.call('fetch_history_item', {'item_id': ref})
        response = await self.port.call('fetch_canonical_message', {
            'message_id': ref, 'length': 65536, 'evidence_limit': 1})
        if response.get('has_more') is not False:
            _invalid()
        encoded = response.get('content_base64')
        if type(encoded) is not str or len(encoded) > 87384:
            _invalid()
        try:
            raw = base64.b64decode(encoded, validate=True)
            content = raw.decode('utf-8')
        except (KeyError, ValueError, TypeError, UnicodeError):
            _invalid()
        if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != response.get('content_sha256'):
            _invalid()
        return {'item': {**deepcopy(response['message']), 'item_id': ref,
                         'content': content, 'content_sha256': response['content_sha256']}}

    def validate(self, task, result, prepared):
        _shape(result.output, {'summary', 'source_refs', 'content_sha256'})
        if result.output['content_sha256'] != prepared['item']['content_sha256']:
            _invalid()
        _text(result.output['summary'], 10000)
        if result.output['source_refs'] != [task.inputs['item_id']]:
            _invalid()
        _evidence(result, [task.inputs['item_id']])

    async def commit(self, task, result, key):
        prepared = await self.prepare(task)
        self.validate(task, result, prepared)
        return {'persisted': False, 'derived': deepcopy(result.output),
                'source_sha256': _digest(prepared), 'idempotency_key': key}

    async def verify(self, task, result, receipt):
        original = await self.prepare(task)
        return (receipt.get('persisted') is False and receipt.get('derived') == result.output
                and receipt.get('source_sha256') == _digest(original))


class MemoryProposalHandler(_Handler):
    required_grants = frozenset({'read', 'write'})

    async def prepare(self, task):
        self._grants(task)
        ref = task.inputs['source_ref']
        if re.fullmatch(r'history:[0-9a-f]{64}', ref):
            item = (await self.port.call('fetch_history_item', {'item_id': ref}))['item']
            original = {'text': item['content'], 'content_sha256': item['content_sha256'], 'role': item['role']}
        elif re.fullmatch(r'message:[0-9a-f]{64}', ref):
            response = await self.port.call('fetch_canonical_message', {'message_id': ref, 'length': 65536, 'evidence_limit': 1})
            if response['has_more']:
                _invalid()
            original = {'text': base64.b64decode(response['content_base64'], validate=True).decode(),
                        'content_sha256': response['content_sha256'], 'role': response['message']['role']}
        else:
            _invalid()
        return {'source_ref': ref, 'original': original}

    def validate(self, task, result, prepared):
        output = result.output
        _shape(output, {'quote', 'subject', 'predicate', 'value', 'classification', 'basis'})
        for field, limit in {'quote':2000, 'subject':200, 'predicate':120, 'value':4000, 'basis':1000}.items():
            _text(output[field], limit)
        if type(output['classification']) is not str or output['classification'] not in {'fact_update', 'preference_change', 'historical_statement', 'inference'}:
            _invalid()
        if output['quote'] not in prepared['original']['text']:
            _invalid()
        if prepared['original']['role'] != 'user' and output['classification'] != 'inference':
            _invalid()
        if type(result.confidence) not in (int, float) or not math.isfinite(result.confidence) or not 0 <= result.confidence <= 1:
            _invalid()
        _evidence(result, [prepared['source_ref']])

    async def commit(self, task, result, key):
        prepared = await self.prepare(task)
        self.validate(task, result, prepared)
        receipt = await self.port.call('propose_memory_candidate', {
            'source_ref': task.inputs['source_ref'], **result.output, 'confidence': result.confidence})
        return {**receipt, 'requires_review': True}

    async def verify(self, task, result, receipt):
        saved = receipt.get('candidate', {})
        fresh = (await self.port.call('fetch_memory_candidate', {'candidate_id': saved['candidate_id']}))['candidate']
        original = await self.prepare(task)
        return (saved == fresh and fresh['state'] == 'pending' and fresh['source_ref'] == task.inputs['source_ref']
                and fresh['content_sha256'] == original['original']['content_sha256']
                and fresh['confidence'] == result.confidence
                and all(fresh.get(k) == v for k, v in result.output.items()))


class HistoryIngestHandler(_Handler):
    allow_empty_evidence = True
    """Lossless source ingestion only; normalization is a separate operation."""
    required_grants = frozenset({'read', 'ingest'})

    async def prepare(self, task):
        self._grants(task)
        fields = {'relative_manifest', 'expected_manifest_sha256', 'cursor', 'limit'}
        if set(task.inputs) - fields:
            _invalid()
        args = {'relative_manifest': task.inputs['relative_manifest'],
                'expected_manifest_sha256': task.inputs['expected_manifest_sha256'],
                'cursor': task.inputs.get('cursor', 0), 'limit': task.inputs.get('limit', 128)}
        path = args['relative_manifest']
        if (type(path) is not str or not 1 <= len(path) <= 500 or ':' in path or '\\' in path
                or any(part in {'', '.', '..'} for part in path.split('/'))
                or any(ord(c) < 32 for c in path)):
            _invalid()
        if type(args['expected_manifest_sha256']) is not str or not re.fullmatch(r'[0-9a-f]{64}', args['expected_manifest_sha256']):
            _invalid()
        if type(args['cursor']) is not int or args['cursor'] < 0 or type(args['limit']) is not int or not 1 <= args['limit'] <= 128:
            _invalid()
        # This is caller-reported metadata. Only the Gateway verifies inbox bytes.
        return {'arguments': args, 'manifest_verified': False}

    def validate(self, task, result, prepared):
        _shape(result.output, {'manifest_sha256', 'approved'})
        if (result.output['manifest_sha256'] != prepared['arguments']['expected_manifest_sha256']
                or result.output['approved'] is not True or result.evidence):
            _invalid()

    async def commit(self, task, result, key):
        prepared = await self.prepare(task)
        self.validate(task, result, prepared)
        receipt = await self.port.call('ingest_history_sources', {**prepared['arguments'],
            'provenance': {'reported_agent': result.adapter_id, 'reported_client': 'cognivault-orchestrator', 'run_id': task.task_id}})
        return {**receipt, 'partial': bool(receipt['errors'] or receipt['has_more'])}

    async def verify(self, task, result, receipt):
        if receipt.get('manifest_sha256') != task.inputs['expected_manifest_sha256']:
            return False
        if receipt.get('canonical_messages_created') != 0:
            return False
        for record in receipt['results']:
            if record['disposition'] == 'error':
                continue
            proof = await self.port.call('verify_history_source', {'source_id': record['source_id']})
            if (proof.get('verified') is not True or proof.get('sha256') != record['sha256']
                    or proof.get('byte_count') != record['byte_count']):
                return False
            fetched = await self.port.call('fetch_history_source', {'source_id': record['source_id'], 'length': 1})
            if fetched['source']['sha256'] != record['sha256']:
                return False
        return True


class ThreeDomainRetrievalHandler(_Handler):
    allow_empty_evidence = True
    async def prepare(self, task):
        self._grants(task)
        return await self.port.call('retrieve_evidence', {'queries': task.inputs['queries'], 'limit': task.inputs.get('limit', 3)})

    def validate(self, task, result, prepared):
        _shape(result.output, {'selected_refs'})
        refs = result.output['selected_refs']
        available = {item['citation'] for item in prepared['evidence']}
        if type(refs) is not list or not all(type(ref) is str for ref in refs) or len(refs) != len(set(refs)) or not set(refs) <= available:
            _invalid()
        _evidence(result, available)

    async def commit(self, task, result, key):
        prepared = await self.prepare(task)
        self.validate(task, result, prepared)
        return {**prepared, 'persisted': False, 'selected_refs': deepcopy(result.output['selected_refs']),
                'partial': prepared['status'] in {'PARTIAL', 'UNAVAILABLE'}}

    async def verify(self, task, result, receipt):
        fresh = await self.prepare(task)
        return (receipt.get('persisted') is False and receipt.get('selected_refs') == result.output['selected_refs']
                and all(receipt.get(key) == value for key, value in fresh.items()))
