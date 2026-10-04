"""Synthetic History records; no real conversations or source discovery."""

import hashlib
from pathlib import Path
import sqlite3

import pytest

from cognivault.adapters.history import SQLiteHistoryBackend
import cognivault.adapters.history as history_adapter
from cognivault.contracts import (BackendHealth, GatewayError, HistoryImportItem,
                                           HistoryItem)
from cognivault.gateway import Gateway
from cognivault.config import AppConfig


def _store(tmp_path: Path) -> SQLiteHistoryBackend:
    return SQLiteHistoryBackend(tmp_path / "private-history.db")


def _item(external_id: str, conversation: str, content: str, created_at: str,
          role: str = "user") -> HistoryImportItem:
    return HistoryImportItem(external_id, conversation, role, content, created_at)


def test_explicit_sources_and_import_preserve_provenance_and_stable_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert not (tmp_path / "private-history.db").exists()
    store.register_source("export-a", "Invented export")
    item = _item("entry-1", "chat-1", "  Alpha\n   beta  ", "2026-01-02T03:04:05Z")
    first = store.import_items("export-a", [item])
    second = store.import_items("export-a", [item])
    assert first == second
    assert len(first) == 1
    fetched = store.fetch(first[0])
    assert fetched is not None
    assert fetched.item_id == first[0]
    assert fetched.content == "Alpha beta"
    assert fetched.content_sha256 == hashlib.sha256(b"Alpha beta").hexdigest()
    assert fetched.source_id == "export-a"
    assert fetched.source_item_id == "entry-1"
    assert fetched.conversation_id == "chat-1"
    assert fetched.role == "user"
    assert fetched.created_at == "2026-01-02T03:04:05Z"
    assert fetched.imported_at.endswith("Z")
    assert fetched.imported_at != fetched.created_at
    assert fetched.import_batch_id.startswith("batch-")
    assert fetched.write_provenance["original_created_at"] == fetched.created_at
    assert fetched.write_provenance["imported_at"] == fetched.imported_at
    assert fetched.write_provenance["data_origin"] == "imported"
    assert fetched.write_provenance["legacy_status"] == "imported"
    assert store.list_sources()[0].item_count == 1


def test_history_import_persists_source_system_and_one_batch_identity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register_source("synthetic-export", "Synthetic export")
    ids = store.import_items("synthetic-export", [
        _item("item-a", "chat-a", "First synthetic item", "2026-01-01T00:00:00Z"),
        _item("item-b", "chat-a", "Second synthetic item", "2026-01-02T00:00:00Z"),
    ], source_system="legacy-chat-export", import_batch_id="batch-test-1")
    records = [store.fetch(item_id) for item_id in ids]
    assert {record.import_batch_id for record in records} == {"batch-test-1"}
    assert {record.source_system for record in records} == {"legacy-chat-export"}
    assert records[0].imported_at == records[1].imported_at


def test_projection_snapshot_watermark_paginates_stable_history_sources_and_items(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register_source("synthetic-a", "Synthetic A")
    first_ids = store.import_items("synthetic-a", [
        _item("item-a", "chat-a", "Alpha synthetic record", "2026-01-01T00:00:00Z"),
        _item("item-b", "chat-a", "Beta synthetic record", "2026-01-02T00:00:00Z"),
    ])
    store.register_source("synthetic-b", "Synthetic B")
    started = store.projection_snapshot("begin")
    store.register_source("synthetic-b", "Synthetic B")
    store.import_items("synthetic-b", [
        _item("item-c", "chat-b", "Later synthetic record", "2026-01-03T00:00:00Z"),
    ])

    source_page = store.projection_snapshot(
        "sources", snapshot_token=started["snapshot_token"], limit=1,
    )
    source_tail = store.projection_snapshot(
        "sources", snapshot_token=started["snapshot_token"],
        cursor=source_page["next_cursor"], limit=1,
    )
    item_page = store.projection_snapshot(
        "records", snapshot_token=started["snapshot_token"],
        source_id="synthetic-a", limit=1,
    )
    item_tail = store.projection_snapshot(
        "records", snapshot_token=started["snapshot_token"],
        source_id="synthetic-a", cursor=item_page["next_cursor"], limit=1,
    )

    assert started["total_sources"] == 2
    assert started["total_records"] == 2
    assert started["stored_payload_bytes"] == len("Alpha synthetic recordBeta synthetic record".encode())
    assert source_page["sources"] == [{"source_id": "synthetic-a", "label": "Synthetic A", "item_count": 2}]
    assert source_tail["sources"] == [{"source_id": "synthetic-b", "label": "Synthetic B", "item_count": 0}]
    assert item_page["items"][0].item_id == first_ids[0]
    assert item_page["has_more"] is True
    assert item_tail["items"][0].item_id == first_ids[1]
    assert item_tail["has_more"] is False


def test_projection_snapshot_rejects_bad_tokens_and_page_bounds(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(GatewayError) as invalid_action:
        store.projection_snapshot("all")
    assert invalid_action.value.code == "INVALID_ARGUMENT"
    with pytest.raises(GatewayError) as invalid_token:
        store.projection_snapshot("sources", snapshot_token="history-v1:0:0:extra")
    assert invalid_token.value.code == "INVALID_ARGUMENT"
    with pytest.raises(GatewayError) as unavailable:
        store.projection_snapshot("begin")
    assert unavailable.value.code == "HISTORY_UNAVAILABLE"
    assert not store.database_path.exists()
    store.register_source("synthetic", "Synthetic")
    started = store.projection_snapshot("begin")
    with pytest.raises(GatewayError) as invalid_cursor:
        store.projection_snapshot("sources", snapshot_token=started["snapshot_token"], cursor=-1)
    assert invalid_cursor.value.code == "INVALID_ARGUMENT"


def test_projection_snapshot_rejects_noncontinuation_cursor_and_enforces_input_budget(
    tmp_path: Path, monkeypatch,
) -> None:
    store = _store(tmp_path)
    store.register_source("synthetic", "Synthetic")
    store.import_items("synthetic", [
        _item("item-a", "chat-a", "Synthetic content", "2026-01-01T00:00:00Z"),
    ])
    started = store.projection_snapshot("begin")
    with pytest.raises(GatewayError) as invalid_cursor:
        store.projection_snapshot("records", snapshot_token=started["snapshot_token"],
                                  source_id="synthetic", cursor=99)
    assert invalid_cursor.value.code == "INVALID_ARGUMENT"

    monkeypatch.setattr(history_adapter, "_PROJECTION_MAX_BYTES", 1)
    with pytest.raises(GatewayError) as oversized:
        store.projection_snapshot("begin")
    assert oversized.value.code == "PAYLOAD_TOO_LARGE"


def test_gateway_projection_snapshot_uses_safe_history_dto_serialization(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register_source("synthetic", "Synthetic")
    store.import_items("synthetic", [
        _item("item-a", "chat-a", "C:\\private\\history.txt", "2026-01-01T00:00:00Z"),
    ])
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, history_backend=store,
                      capabilities=frozenset({"projection"}), qmd_discoverable=lambda: False)
    with sqlite3.connect(store.database_path) as connection:
        audit_count_before = connection.execute("SELECT COUNT(*) FROM operation_audit").fetchone()[0]

    started = gateway.projection_snapshot("history", "begin")
    source_page = gateway.projection_snapshot(
        "history", "sources", snapshot_token=started["snapshot_token"],
    )
    source_id = source_page["sources"][0]["source_id"]
    records = gateway.projection_snapshot(
        "history", "records", snapshot_token=started["snapshot_token"], source_id=source_id,
    )

    assert records["items"][0]["content"] == "[local path redacted]"
    assert "C:\\private" not in str(records)
    with sqlite3.connect(store.database_path) as connection:
        audit_count_after = connection.execute("SELECT COUNT(*) FROM operation_audit").fetchone()[0]
    assert audit_count_after == audit_count_before


def test_gateway_rejects_malformed_history_provenance_from_replaceable_backend(tmp_path: Path) -> None:
    item = HistoryItem("history:" + "a" * 64, "source", "item", "conversation", "user",
                       "2026-01-01T00:00:00Z", "b" * 64, "Synthetic content",
                       write_provenance={"recorded_at": "C:/private/history.db"})

    class Backend:
        def fetch(self, _item_id):
            return item

        def probe(self):
            return BackendHealth("sqlite", "ready")

    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, history_backend=Backend(),
                      capabilities=frozenset({"read"}), qmd_discoverable=lambda: False)
    with pytest.raises(GatewayError) as error:
        gateway.fetch_history_item(item.item_id)
    assert error.value.code == "BACKEND_BAD_OUTPUT"


def test_content_hash_dedupes_payload_without_collapsing_distinct_provenance(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register_source("export-a", "Invented export")
    ids = store.import_items("export-a", [
        _item("entry-1", "chat-1", "Same content", "2026-01-02T00:00:00Z"),
        _item("entry-2", "chat-2", "Same  content", "2026-01-03T00:00:00Z"),
    ])
    assert len(set(ids)) == 2
    assert store.fetch(ids[0]).content_sha256 == store.fetch(ids[1]).content_sha256
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM history_content").fetchone()[0] == 1


def test_history_audit_records_logical_ids_without_raw_content(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register_source("export-a", "Invented export")
    item_id = store.import_items("export-a", [
        _item("entry-1", "chat-1", "Synthetic private body", "2026-01-01T00:00:00Z")
    ])[0]
    with sqlite3.connect(store.database_path) as connection:
        events = connection.execute(
            "SELECT operation, resource_type, resource_id, outcome FROM operation_audit ORDER BY audit_id"
        ).fetchall()
    assert events == [
        ("register_history_source", "history_source", "export-a", "success"),
        ("import_history_item", "history_item", item_id, "success"),
    ]
    assert "Synthetic private body" not in repr(events)


def test_history_import_migrates_existing_database_before_audit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register_source("legacy-export", "Synthetic legacy export")
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("DROP TABLE operation_audit")
    ids = store.import_items("legacy-export", [
        _item("entry-1", "chat-1", "Synthetic post-upgrade item", "2026-01-01T00:00:00Z")
    ])
    assert store.fetch(ids[0]).content == "Synthetic post-upgrade item"
    with sqlite3.connect(store.database_path) as connection:
        audit = connection.execute(
            "SELECT resource_id FROM operation_audit WHERE operation='import_history_item'"
        ).fetchall()
    assert audit == [(ids[0],)]


def test_search_filters_paginates_and_handles_prefix_or_typo(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register_source("export-a", "Invented export A")
    store.register_source("export-b", "Invented export B")
    store.import_items("export-a", [
        _item("a1", "chat-a", "Quadratic formula example", "2026-01-01T00:00:00Z"),
        _item("a2", "chat-a", "Quadratic graph example", "2026-01-02T00:00:00Z"),
        _item("a3", "chat-b", "Quadratic roots example", "2026-01-03T00:00:00Z"),
    ])
    store.import_items("export-b", [
        _item("b1", "chat-a", "Quadratic equation example", "2026-01-04T00:00:00Z"),
    ])
    page = store.search("quadrat", source_id="export-a", conversation_id="chat-a", limit=1, offset=1)
    assert [hit.content for hit in page.items] == ["Quadratic formula example"]
    assert page.total == 2
    assert page.has_more is False
    typo = store.search("qudratic", limit=5)
    assert len(typo.items) == 4
    assert {hit.source_id for hit in typo.items} == {"export-a", "export-b"}


def test_import_rejects_malformed_or_conflicting_records_atomically(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register_source("export-a", "Invented export")
    with pytest.raises(GatewayError) as bad:
        store.import_items("export-a", [
            _item("valid", "chat-1", "Valid", "2026-01-01T00:00:00Z"),
            _item("bad", "chat-1", "Bad", "not-a-time"),
        ])
    assert bad.value.code == "INVALID_ARGUMENT"
    assert store.search("Valid").total == 0
    store.import_items("export-a", [_item("entry-1", "chat-1", "Original", "2026-01-01T00:00:00Z")])
    with pytest.raises(GatewayError) as conflict:
        store.import_items("export-a", [_item("entry-1", "chat-1", "Changed", "2026-01-01T00:00:00Z")])
    assert conflict.value.code == "CONFLICT"
    assert store.search("Original").total == 1
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM operation_audit WHERE operation='import_history_item'"
        ).fetchone()[0] == 1


def test_import_without_registered_source_does_not_create_storage(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(GatewayError) as unavailable:
        store.import_items("missing", [
            _item("entry-1", "chat-1", "Synthetic", "2026-01-01T00:00:00Z")
        ])
    assert unavailable.value.code == "HISTORY_UNAVAILABLE"
    assert not store.database_path.exists()


def test_history_probe_reports_unavailable_for_reparse_point_database(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    store.database_path.touch()
    original = history_adapter._path_has_reparse_point
    monkeypatch.setattr(history_adapter, "_path_has_reparse_point",
                        lambda path: Path(path) == store.database_path or original(Path(path)))
    assert store.probe().status == "unavailable"


def test_fractional_timestamp_change_conflicts_instead_of_collapsing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register_source("export-a", "Invented export")
    item_id = store.import_items("export-a", [
        _item("entry-1", "chat-1", "Synthetic", "2026-01-01T00:00:00.123456Z")
    ])[0]
    assert store.fetch(item_id).created_at == "2026-01-01T00:00:00.123456Z"
    with pytest.raises(GatewayError) as changed:
        store.import_items("export-a", [
            _item("entry-1", "chat-1", "Synthetic", "2026-01-01T00:00:00.123457Z")
        ])
    assert changed.value.code == "CONFLICT"
    with pytest.raises(GatewayError) as excessive_precision:
        store.import_items("export-a", [
            _item("entry-2", "chat-1", "Synthetic", "2026-01-01T00:00:00.1234567Z")
        ])
    assert excessive_precision.value.code == "INVALID_ARGUMENT"


def test_search_fails_safely_when_candidate_set_exceeds_internal_bound(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register_source("export-a", "Invented export")
    for start in range(0, 2050, 500):
        store.import_items("export-a", [
            _item(f"entry-{index}", "chat-1", "Synthetic quadratic example",
                  "2026-01-01T00:00:00Z")
            for index in range(start, min(start + 500, 2050))
        ])
    store.import_items("export-a", [
        _item("selective", "chat-2", "Unique telescope clue", "2026-01-02T00:00:00Z")
    ])
    assert store.search("telescope", limit=1).total == 1
    for query in ("quadratic", "qudratic"):
        with pytest.raises(GatewayError) as bounded:
            store.search(query, limit=1)
        assert bounded.value.code == "PAYLOAD_TOO_LARGE"


def test_unconfigured_gateway_fails_closed_and_bounds_inputs(tmp_path: Path) -> None:
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, qmd_discoverable=lambda: False)
    for operation in (lambda: gateway.list_history_sources(),
                      lambda: gateway.search_history("alpha"),
                      lambda: gateway.fetch_history_item("history:" + "0" * 64)):
        with pytest.raises(GatewayError) as unavailable:
            operation()
        assert unavailable.value.code == "HISTORY_UNAVAILABLE"

    store = _store(tmp_path)
    configured = Gateway(AppConfig("0.1.0", tmp_path), None, store, qmd_discoverable=lambda: False)
    for kwargs in ({"limit": 0}, {"limit": 21}, {"limit": True}, {"offset": -1},
                   {"offset": 1001}, {"source_id": "C:/secret"}):
        with pytest.raises(GatewayError) as invalid:
            configured.search_history("alpha", **kwargs)
        assert invalid.value.code == "INVALID_ARGUMENT"
