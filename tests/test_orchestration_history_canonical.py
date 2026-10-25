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
