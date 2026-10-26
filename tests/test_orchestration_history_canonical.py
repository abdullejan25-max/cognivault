import asyncio
import base64
import hashlib
from types import SimpleNamespace

import pytest

from cognivault.orchestration.domains import HistorySummaryHandler


def test_canonical_history_preserves_role_time_and_hash():
    raw = b'Synthetic original'
    digest = hashlib.sha256(raw).hexdigest()
    ref = 'message:' + digest
    class Port:
        async def call(self, name, args):
            assert name == 'fetch_canonical_message'
            assert args['length'] == 65536
            return dict(content_base64=base64.b64encode(raw).decode(), content_sha256=digest,
                        has_more=False, message={'role':'assistant', 'occurred_at':'2026-01-01T00:00:00Z'})
    task = SimpleNamespace(inputs={'item_id':ref}, grants=frozenset({'read'}))
    async def run():
        handler = HistorySummaryHandler(Port())
        prepared = await handler.prepare(task)
        assert prepared['item']['content'] == raw.decode()
        assert prepared['item']['role'] == 'assistant'
        assert prepared['item']['occurred_at'] == '2026-01-01T00:00:00Z'
        assert prepared['item']['content_sha256'] == digest
        result = SimpleNamespace(output=dict(summary='Synthetic summary', source_refs=[ref], content_sha256=digest), evidence=(ref,))
        receipt = await handler.commit(task, result, 'synthetic')
        assert await handler.verify(task, result, receipt)
    asyncio.run(run())


@pytest.mark.parametrize('case', ['truncated', 'hash', 'base64'])
def test_canonical_history_rejects_incomplete_or_corrupt_content(case):
    class Port:
        async def call(self, name, args):
            return dict(content_base64='!' if case == 'base64' else base64.b64encode(b'original').decode(),
                        content_sha256='0'*64, has_more=case == 'truncated', message={'role':'user'})
    task = SimpleNamespace(inputs={'item_id':'message:'+'1'*64}, grants=frozenset({'read'}))
    with pytest.raises(ValueError):
        asyncio.run(HistorySummaryHandler(Port()).prepare(task))


@pytest.mark.parametrize('kind', ['history', 'message'])
@pytest.mark.parametrize('known', [True, False])
def test_memory_prepare_preserves_original_occurrence_time_or_explicit_unknown(kind, known):
    from cognivault.orchestration.domains import MemoryProposalHandler
    timestamp = '2026-01-01T00:00:00Z' if known else None
    # A time embedded in untrusted text must not replace unknown source metadata.
    raw = b'User goal. Quoted timestamp: 2099-01-01T00:00:00Z'
    ref = kind + ':' + '1'*64
    class Port:
        async def call(self, name, arguments):
            if kind == 'history':
                assert name == 'fetch_history_item'
                return {'item':{'content':raw.decode(), 'content_sha256':hashlib.sha256(raw).hexdigest(),
                                'role':'user', 'created_at':timestamp, 'imported_at':'2026-10-26T00:00:00Z'}}
            assert name == 'fetch_canonical_message'
            return dict(content_base64=base64.b64encode(raw).decode(),
                        content_sha256=hashlib.sha256(raw).hexdigest(), has_more=False,
                        message={'role':'user', 'occurred_at':timestamp, 'normalized_at':'2026-10-26T00:00:00Z'})
    request = SimpleNamespace(inputs={'source_ref':ref}, grants=frozenset({'read','write'}))
    prepared = asyncio.run(MemoryProposalHandler(Port()).prepare(request))
    assert prepared['original']['occurred_at'] == timestamp
    assert prepared['original']['time_status'] == ('known' if known else 'unknown')
    assert prepared['original']['role'] == 'user'
    assert prepared['original']['content_sha256'] == hashlib.sha256(raw).hexdigest()
    assert prepared['original']['text'] == raw.decode()
