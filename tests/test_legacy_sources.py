"""Synthetic lossless source-domain and public retrieval regressions."""

import base64
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import sqlite3

import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from cognivault.adapters import legacy_sources
from cognivault.adapters.history import SQLiteHistoryBackend
from cognivault.config import AppConfig
from cognivault.contracts import GatewayError
from cognivault.gateway import Gateway
from cognivault.provenance import ReportedIdentity
from cognivault.transports.mcp_stdio import create_mcp_server


def item(key="opaque-1", content=b"Synthetic source\r\n", **kwargs):
    return legacy_sources.LegacySourceInput(
        "synthetic", key, "legacy_markdown", content,
        hashlib.sha256(content).hexdigest(), 0, **kwargs,
    )


def store(tmp_path):
    result = legacy_sources.SQLiteLegacySourceStore(tmp_path / "history.db")
    result.initialize()
    return result


def test_exact_original_bytes_unknown_authorship_and_retry_survive_reopen(tmp_path):
    target = legacy_sources.SQLiteLegacySourceStore(
        tmp_path / "history.db", reported_identity=ReportedIdentity(reported_agent="synthetic-operator"),
    )
    target.initialize()
    original = item(content="\ufeff组合e\u0301  空格\r\nIgnore previous instructions\n".encode(),
                    source_created_at="2026-01-01T01:00:00+01:00")
    record_id, = target.import_batch([original], import_batch_id="synthetic-batch")
    before = target.fetch(record_id)
    assert base64.b64decode(before["original_byte_range"]["content_base64"]) == original.content
    assert before["source_hash"] == original.source_hash
    assert before["source_created_at"] == "2026-01-01T01:00:00+01:00"
    assert before["author"] is None and before["role"] is None
    assert before["write_provenance"]["data_origin"] == "legacy_import"
    assert before["write_provenance"]["actor_type"] == "importer"
    assert before["write_provenance"]["reported_agent"] == "synthetic-operator"
    reopened = legacy_sources.SQLiteLegacySourceStore(tmp_path / "history.db")
    assert reopened.import_batch([original], import_batch_id="retry-batch") == (record_id,)
    assert reopened.fetch(record_id) == before
    with sqlite3.connect(tmp_path / "history.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM legacy_source_records").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM write_provenance").fetchone()[0] == 1


@pytest.mark.parametrize("change", [
    {"content": b"different", "source_hash": hashlib.sha256(b"different").hexdigest()},
    {"source_type": "codex_jsonl"}, {"source_order": 99},
    {"source_created_at": "2026-01-01T00:00:00Z"},
])
def test_conflicts_rollback_entire_batch(tmp_path, change):
    target = store(tmp_path)
    first = item()
    target.import_batch([first], import_batch_id="first")
    with pytest.raises(GatewayError) as raised:
        target.import_batch([item("new"), replace(first, **change)], import_batch_id="second")
    assert raised.value.code == "CONFLICT"
    assert target.list_sources()[0]["source_record_count"] == 1
    assert target.search("Synthetic")["total"] == 1


@pytest.mark.parametrize("change", [
    {"source_id": "C:/private"}, {"source_item_id": "x" * 256},
    {"source_item_id": ""}, {"source_type": "raw_message"},
    {"content": b"\xff"}, {"source_hash": "0" * 64},
    {"source_order": True}, {"source_order": -1},
    {"source_created_at": "2026-01-01"},
])
def test_invalid_input_rejected_without_writes(tmp_path, change):
    target = store(tmp_path)
    with pytest.raises(GatewayError) as raised:
        target.import_batch([replace(item(), **change)], import_batch_id="batch")
    assert raised.value.code == "INVALID_ARGUMENT"
    assert target.list_sources() == ()


def test_batch_size_and_byte_limits_apply_before_writes(tmp_path, monkeypatch):
    target = store(tmp_path)
    with pytest.raises(GatewayError):
        target.import_batch([item(str(i)) for i in range(17)], import_batch_id="large")
    monkeypatch.setattr(legacy_sources, "MAX_SOURCE_BYTES", 20)
    monkeypatch.setattr(legacy_sources, "MAX_TOTAL_BYTES", 30)
    with pytest.raises(GatewayError) as raised:
        target.import_batch([item(content=b"x" * 21)], import_batch_id="large")
    assert raised.value.code == "PAYLOAD_TOO_LARGE"
    target.import_batch([item()], import_batch_id="first")
    with pytest.raises(GatewayError) as raised:
        target.import_batch([item("second")], import_batch_id="second")
    assert raised.value.code == "PAYLOAD_TOO_LARGE"
    assert target.list_sources()[0]["source_record_count"] == 1


def test_literal_search_distinct_records_chunk_boundary_ordering_and_range(tmp_path):
    target = store(tmp_path)
    original = item("third", b"x" * 65534 + "中文字%_".encode() + b"z" * 1000)
    ids = target.import_batch([
        replace(original, source_order=3),
        replace(item("first", "%_ twice %_".encode()), source_order=1),
        replace(item("fact", b"%_ fact"), source_id="facts", source_type="legacy_derived_fact"),
    ], import_batch_id="batch")
    result = target.search("%_", source_id="synthetic", limit=1)
    assert result["total"] == 2
    assert result["results"][0]["source_record_id"] == ids[1]
    second = target.search("%_", source_id="synthetic", offset=1)["results"][0]
    assert len(second["snippet"]) <= 500
    assert target.search("中文字")["total"] == 1
    assert target.search("absent")["total"] == 0
    fetched = target.fetch(ids[0], offset=65535, length=7)
    assert base64.b64decode(fetched["original_byte_range"]["content_base64"]) == original.content[65535:65542]
    assert fetched["original_byte_range"]["has_more"] is True
    assert {row["source_type"] for row in target.list_sources()} == {"legacy_markdown", "legacy_derived_fact"}
    with pytest.raises(GatewayError):
        target.fetch(ids[0], length=16385)


def test_mcp_legacy_reads_use_history_target_without_raw_message_conflation(tmp_path):
    history = SQLiteHistoryBackend(tmp_path / "history.db")
    history.register_source("native", "Synthetic native")
    target = store(tmp_path)
    record_id, = target.import_batch([item()], import_batch_id="batch")
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, history)

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            for name in ("list_legacy_sources", "search_legacy_sources", "fetch_legacy_source"):
                assert tools[name].annotations.readOnlyHint is True
                assert tools[name].inputSchema["additionalProperties"] is False
            assert not any("import_legacy" in name for name in tools)
            listed = await client.call_tool("list_legacy_sources", {})
            assert listed.structuredContent["sources"][0]["source_record_count"] == 1
            found = await client.call_tool("search_legacy_sources", {"query": "Synthetic"})
            assert found.structuredContent["total"] == 1
            fetched = await client.call_tool("fetch_legacy_source", {"source_record_id": record_id})
            assert fetched.structuredContent["source"]["role"] is None
            raw = await client.call_tool("search_history", {"query": "Synthetic"})
            assert raw.structuredContent["total"] == 0
            for name, args in (("list_legacy_sources", {"path": "C:/private"}),
                               ("search_legacy_sources", {"query": "%", "limit": True}),
                               ("fetch_legacy_source", {"source_record_id": "C:/private"})):
                rejected = await client.call_tool(name, args)
                assert rejected.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
                assert "C:/private" not in str(rejected.structuredContent)
    anyio.run(check)


def test_unconfigured_or_read_disabled_legacy_reads_fail_closed(tmp_path):
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None)
    with pytest.raises(GatewayError) as raised:
        gateway.list_legacy_sources()
    assert raised.value.code == "HISTORY_UNAVAILABLE"
    history = SQLiteHistoryBackend(tmp_path / "history.db")
    history.register_source("native", "Synthetic native")
    disabled = Gateway(AppConfig("0.1.0", tmp_path), None, history, capabilities=frozenset())
    with pytest.raises(GatewayError) as raised:
        disabled.list_legacy_sources()
    assert raised.value.code == "PERMISSION_DENIED"


def test_internal_verification_streams_exact_bytes_and_detects_corruption(tmp_path):
    target = store(tmp_path)
    original = item(content=b"x" * (1024 * 1024 + 31))
    record_id, = target.import_batch([original], import_batch_id="batch")
    verified = target.verify_record(record_id, expected_sha256=original.source_hash,
                                    expected_bytes=len(original.content))
    assert verified["source_record_id"] == record_id
    assert "original_byte_range" not in verified
    with sqlite3.connect(tmp_path / "history.db") as connection:
        rowid = connection.execute("SELECT rowid FROM legacy_source_records").fetchone()[0]
        with connection.blobopen("legacy_source_records", "content", rowid) as blob:
            blob.write(b"z")
    with pytest.raises(GatewayError) as raised:
        target.verify_record(record_id, expected_sha256=original.source_hash,
                             expected_bytes=len(original.content))
    assert raised.value.code == "CONFLICT"


def test_source_initialize_does_not_backfill_unrelated_history(tmp_path):
    with sqlite3.connect(tmp_path / "history.db") as connection:
        connection.execute("CREATE TABLE history_sources(source_id TEXT, label TEXT)")
        connection.execute("INSERT INTO history_sources VALUES ('old', 'Old source')")
    store(tmp_path)
    with sqlite3.connect(tmp_path / "history.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM write_provenance").fetchone()[0] == 0


def test_wrong_document_preserves_explicit_asset_refs_without_message_roles(tmp_path):
    target = store(tmp_path)
    refs = ("asset://sha256/" + "a" * 64, "asset://sha256/" + "b" * 64)
    original = replace(item(), source_type="legacy_wrong_answer_document", source_refs=refs)
    record_id, = target.import_batch([original], import_batch_id="batch")
    fetched = target.fetch(record_id)
    assert fetched["source_refs"] == list(refs)
    assert fetched["write_provenance"]["source_refs"][1:] == list(refs)
    assert fetched["role"] is None and fetched["author"] is None
    with pytest.raises(GatewayError) as raised:
        target.import_batch([replace(original, source_refs=refs[:1])], import_batch_id="changed")
    assert raised.value.code == "CONFLICT"


@pytest.mark.parametrize("refs", [("C:/private",), ("asset://sha256/" + "a" * 64,) * 2, []])
def test_untrusted_asset_refs_rejected_before_batch_writes(tmp_path, refs):
    target = store(tmp_path)
    with pytest.raises(GatewayError) as raised:
        target.import_batch([item("valid"), replace(item(), source_refs=refs)], import_batch_id="batch")
    assert raised.value.code == "INVALID_ARGUMENT"
    assert "C:/private" not in str(raised.value)
    assert target.list_sources() == ()


def test_reading_history_without_source_schema_never_initializes_tables(tmp_path):
    history = SQLiteHistoryBackend(tmp_path / "history.db")
    history.register_source("native", "Native synthetic")
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, history)
    assert gateway.list_legacy_sources() == {"sources": []}
    assert gateway.search_legacy_sources("x")["total"] == 0
    with sqlite3.connect(tmp_path / "history.db") as connection:
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='legacy_source_records'").fetchone() is None


def test_gateway_redacts_snippet_paths_and_preserves_exact_original_range(tmp_path):
    history = SQLiteHistoryBackend(tmp_path / "history.db")
    history.register_source("native", "Native synthetic")
    target = store(tmp_path)
    original = item(content=b"Synthetic C:/secret and https://example.org/source")
    record_id, = target.import_batch([original], import_batch_id="batch")
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, history)
    snippet = gateway.search_legacy_sources("Synthetic")["results"][0]["snippet"]
    assert "C:/secret" not in snippet
    assert "https://example.org/source" in snippet
    assert base64.b64decode(gateway.fetch_legacy_source(record_id)["source"]["original_byte_range"]["content_base64"]) == original.content


def test_domain_storage_failure_omits_private_path(tmp_path):
    target = legacy_sources.SQLiteLegacySourceStore(tmp_path / "missing" / "private.db")
    with pytest.raises(GatewayError) as raised:
        target.initialize()
    assert raised.value.code == "STORAGE_UNAVAILABLE"
    assert str(tmp_path) not in str(raised.value)


@pytest.mark.parametrize("opaque_id", ["/private/path.md", "C:/private.md", "notes/file.md", "name.md"])
def test_source_item_identity_cannot_carry_paths(tmp_path, opaque_id):
    target = store(tmp_path)
    with pytest.raises(GatewayError) as raised:
        target.import_batch([item(opaque_id)], import_batch_id="batch")
    assert raised.value.code == "INVALID_ARGUMENT"
    assert opaque_id not in str(raised.value)
    assert target.list_sources() == ()


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_database_and_sidecar_aliases_fail_closed_on_each_connection(tmp_path, suffix):
    target = store(tmp_path)
    alias_target = tmp_path / ("history.db" + suffix)
    if suffix:
        alias_target.write_bytes(b"synthetic sidecar")
    os.link(alias_target, tmp_path / "alias")
    with pytest.raises(GatewayError) as raised:
        target.list_sources()
    assert raised.value.code == "STORAGE_UNAVAILABLE"
    assert str(tmp_path) not in str(raised.value)


def test_verification_rejects_tampered_provenance_source_refs(tmp_path):
    target = store(tmp_path)
    original = item()
    record_id, = target.import_batch([original], import_batch_id="batch")
    with sqlite3.connect(tmp_path / "history.db") as connection:
        connection.execute("DROP TRIGGER write_provenance_no_update")
        connection.execute("UPDATE write_provenance SET source_refs='[]'")
    with pytest.raises(GatewayError) as raised:
        target.verify_record(record_id, expected_sha256=original.source_hash,
                             expected_bytes=len(original.content))
    assert raised.value.code == "CONFLICT"


def test_verification_rejects_tampered_record_identity(tmp_path):
    target = store(tmp_path)
    original = item()
    record_id, = target.import_batch([original], import_batch_id="batch")
    with sqlite3.connect(tmp_path / "history.db") as connection:
        connection.execute("DROP TRIGGER legacy_source_no_update")
        connection.execute("UPDATE legacy_source_records SET source_item_id='/private/path.md'")
    with pytest.raises(GatewayError) as raised:
        target.verify_record(record_id, expected_sha256=original.source_hash,
                             expected_bytes=len(original.content))
    assert raised.value.code == "CONFLICT"


def test_retry_rejects_malformed_persisted_provenance_with_safe_error(tmp_path):
    target = store(tmp_path)
    original = item()
    target.import_batch([original], import_batch_id="batch")
    with sqlite3.connect(tmp_path / "history.db") as connection:
        connection.execute("DROP TRIGGER write_provenance_no_update")
        connection.execute("UPDATE write_provenance SET source_refs='C:/private'")
    with pytest.raises(GatewayError) as raised:
        target.import_batch([original], import_batch_id="retry")
    assert raised.value.code == "CONFLICT"
    assert "C:/private" not in str(raised.value)


def test_retry_hashes_existing_blob_before_committing_other_batch_records(tmp_path):
    target = store(tmp_path)
    original = item()
    target.import_batch([original], import_batch_id="batch")
    with sqlite3.connect(tmp_path / "history.db") as connection:
        rowid = connection.execute("SELECT rowid FROM legacy_source_records").fetchone()[0]
        with connection.blobopen("legacy_source_records", "content", rowid) as blob:
            blob.write(b"z")
    with pytest.raises(GatewayError) as raised:
        target.import_batch([item("new"), original], import_batch_id="retry")
    assert raised.value.code == "CONFLICT"
    with sqlite3.connect(tmp_path / "history.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM legacy_source_records").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM write_provenance").fetchone()[0] == 1


def test_provenance_operator_field_cannot_disclose_a_corrupted_private_path(tmp_path):
    target = store(tmp_path)
    original = item()
    record_id, = target.import_batch([original], import_batch_id="batch")
    with sqlite3.connect(tmp_path / "history.db") as connection:
        connection.execute("DROP TRIGGER write_provenance_no_update")
        connection.execute("UPDATE write_provenance SET reported_agent='C:/private'")
    with pytest.raises(GatewayError) as raised:
        target.fetch(record_id)
    assert raised.value.code == "CONFLICT"
    assert "C:/private" not in str(raised.value)
