import hashlib

import pytest

from cognivault.adapters.legacy_sources import LegacySourceInput, SQLiteLegacySourceStore
from cognivault.obsidian_projection import render_projection
from cognivault.projection_collector import collect_projection
from test_projection_collector import _projection_gateway


def test_source_projection_is_complete_metadata_only_and_not_messages(tmp_path):
    gateway = _projection_gateway(tmp_path, source_count=1, analysis_count=1)
    store = SQLiteLegacySourceStore(gateway.history_backend.database_path)
    store.initialize()
    for number in range(23):
        content = '私人合成正文，不作为消息'.encode()
        item = LegacySourceInput('synthetic-legacy', f'item-{number}', 'legacy_markdown',
                                 content, hashlib.sha256(content).hexdigest(), number,
                                 source_created_at='2026-09-01T08:00:00+08:00')
        store.import_batch([item], import_batch_id='synthetic-batch')
    collected = collect_projection(gateway)
    assert len(collected.snapshot.legacy_sources) == 23
    assert len(collected.snapshot.history_items) == 1
    files = render_projection(collected.snapshot)
    assert '23' in files['Sources/LegacyChat/index.md']
    pages = [text for path, text in files.items() if path.startswith('Sources/LegacyChat/') and not path.endswith('index.md')]
    assert len(pages) == 23
    assert all('author: unknown' in page and 'role: unknown' in page for page in pages)
    assert all('私人合成正文' not in page for page in pages)
    assert files == render_projection(collect_projection(gateway).snapshot)


def test_source_watermark_excludes_later_import_and_rejects_bad_cursor(tmp_path):
    gateway = _projection_gateway(tmp_path, source_count=1, analysis_count=1)
    store = SQLiteLegacySourceStore(gateway.history_backend.database_path)
    store.initialize()
    def add(number):
        content = b'synthetic'
        store.import_batch([LegacySourceInput('synthetic', str(number), 'codex_jsonl', content,
                             hashlib.sha256(content).hexdigest(), number)], import_batch_id='batch')
    add(1)
    begin = gateway.projection_snapshot('legacy_sources', 'begin')
    add(2)
    page = gateway.projection_snapshot('legacy_sources', 'records', snapshot_token=begin['snapshot_token'])
    assert len(page['records']) == 1
    assert 'content' not in page['records'][0]
    with pytest.raises(Exception):
        gateway.projection_snapshot('legacy_sources', 'records', snapshot_token=begin['snapshot_token'], cursor=-1)
