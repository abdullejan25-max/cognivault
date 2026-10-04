from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

import cognivault.migration.codex_jsonl_inspector as inspector_module
from cognivault.migration.codex_jsonl_inspector import (
    CodexJSONLInspectionError,
    inspect_codex_snapshot,
)
from cognivault.migration.codex_snapshot import PrivateCodexJSONLSnapshotStore


def _line(record: object) -> bytes:
    return json.dumps(record, separators=(",", ":")).encode() + b"\n"


def _snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, bytes]):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    source = tmp_path / "source"
    source.mkdir()
    for relative, payload in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    store = PrivateCodexJSONLSnapshotStore(root=tmp_path / "private" / "snapshot")
    result = store.snapshot_jsonl_tree(source)
    return store, result


def test_inspector_returns_only_aggregates_and_detects_semantic_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "private synthetic sentinel"
    image = "data:image/png;base64," + base64.b64encode(b"synthetic image").decode()
    first = [
        {"type": "session_meta", "payload": {"id": "session-one"}},
        {"type": "session_meta", "payload": {"id": "session-two"}},
        {"type": "response_item", "timestamp": "2026-01-01T00:00:00Z", "payload": {
            "type": "message", "id": "message-one", "role": "user",
            "content": [{"type": "input_text", "text": text}, {"type": "input_image", "image_url": image}],
        }},
        {"type": "response_item", "timestamp": "2026-01-02T00:00:00Z", "payload": {
            "type": "message", "id": "message-one", "role": "user",
            "content": [{"type": "input_text", "text": text}, {"type": "input_image", "image_url": image}],
        }},
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {"type": "response_item", "timestamp": "invalid", "payload": {
            "type": "message", "id": "developer-one", "role": "developer",
            "content": [{"type": "input_text", "text": "private developer sentinel"}],
        }},
    ]
    second = [
        {"type": "session_meta", "payload": {"id": "session-one"}},
        {"type": "response_item", "timestamp": "2026-01-03T00:00:00Z", "payload": {
            "type": "message", "id": "message-two", "role": "assistant",
            "content": [{"type": "output_text", "text": "another synthetic private sentinel"}],
        }},
    ]
    store, snapshot = _snapshot(tmp_path, monkeypatch, {
        "one.jsonl": b"".join(_line(row) for row in first) + b"not-json\n[]\n",
        "nested/two.jsonl": b"".join(_line(row) for row in second),
    })

    result = inspect_codex_snapshot(store, snapshot.stored_path, expected_digest=snapshot.snapshot_sha256)
    rendered = repr(result)

    assert result.file_count == 2
    assert result.line_count == 10
    assert result.valid_json_record_count == 9
    assert result.malformed_json_line_count == 1
    assert result.non_object_record_count == 1
    assert result.session_metadata_record_count == 3
    assert result.session_metadata_id_count == 3
    assert result.unique_session_metadata_id_count == 2
    assert result.session_ids_repeated_across_files == 1
    assert result.files_with_multiple_session_ids == 1
    assert result.message_record_count == 4
    assert result.message_id_count == 3
    assert result.duplicate_message_id_groups == 1
    assert result.duplicate_message_id_occurrences == 1
    assert result.duplicate_groups_with_content_conflicts == 0
    assert result.duplicate_groups_with_timestamp_conflicts == 1
    assert result.duplicate_groups_crossing_files == 0
    assert result.parseable_message_timestamp_count == 3
    assert result.utc_message_timestamp_count == 3
    assert result.inline_image_payload_count == 2
    assert result.valid_inline_image_payload_count == 2
    assert result.unique_inline_image_count == 1
    assert result.unique_inline_image_bytes == len(b"synthetic image")
    for private_value in (text, "private developer sentinel", "session-one", "message-one",
                          "one.jsonl", str(tmp_path), snapshot.snapshot_sha256):
        assert private_value not in rendered


def test_inspector_counts_conflicting_duplicate_content_and_invalid_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = {"type": "response_item", "timestamp": "2026-01-01T00:00:00Z", "payload": {
        "type": "message", "id": "duplicate", "role": "assistant",
        "content": [{"type": "output_text", "text": "first"}],
    }}
    second = {"type": "response_item", "timestamp": "2026-01-01T00:00:00Z", "payload": {
        "type": "message", "id": "duplicate", "role": "assistant",
        "content": [{"type": "input_image", "image_url": "data:image/png;base64,not-base64!"}],
    }}
    store, snapshot = _snapshot(tmp_path, monkeypatch, {
        "one.jsonl": _line(first) + _line(second),
    })

    result = inspect_codex_snapshot(store, snapshot.stored_path, expected_digest=snapshot.snapshot_sha256)

    assert result.duplicate_groups_with_content_conflicts == 1
    assert result.duplicate_groups_with_timestamp_conflicts == 0
    assert result.invalid_inline_image_payload_count == 1


def test_inspector_counts_nested_session_meta_wrapper_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = {"type": "session_meta", "payload": {
        "meta": {"id": "nested-session-id"}, "git": {"branch": "synthetic"},
    }}
    store, snapshot = _snapshot(tmp_path, monkeypatch, {"one.jsonl": _line(wrapper)})

    result = inspect_codex_snapshot(store, snapshot.stored_path, expected_digest=snapshot.snapshot_sha256)

    assert result.session_metadata_record_count == 1
    assert result.session_metadata_id_count == 1
    assert result.unique_session_metadata_id_count == 1


def test_inspector_returns_only_allowlisted_field_presence_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        {"type": "session_meta", "timestamp": "private timestamp", "payload": {
            "id": "private outer id", "meta": {
                "session_id": "private nested id", "parent_thread_id": "private parent",
                "private_custom_key": "private nested value",
            },
        }},
        {"type": "response_item", "ordinal": 9, "payload": {
            "type": "message", "id": "private message id", "role": "user",
            "private_custom_key": "private payload value",
            "content": [{"type": "input_image", "image_url": "private image URL",
                         "private_block_key": "private block value"}],
        }},
    ]
    store, snapshot = _snapshot(tmp_path, monkeypatch, {
        "one.jsonl": b"".join(_line(record) for record in records),
    })

    result = inspect_codex_snapshot(store, snapshot.stored_path, expected_digest=snapshot.snapshot_sha256)
    field_counts = result.field_presence_counts
    rendered = repr(field_counts)

    assert ("record", "session_meta", "timestamp", 1) in field_counts
    assert ("payload", "session_meta", "id", 1) in field_counts
    assert ("payload.meta", "session_meta", "session_id", 1) in field_counts
    assert ("payload.meta", "session_meta", "parent_thread_id", 1) in field_counts
    assert ("record", "response_item", "ordinal", 1) in field_counts
    assert ("content_block", "response_item", "image_url", 1) in field_counts
    for private_value in (
        "private timestamp", "private outer id", "private nested id", "private parent",
        "private message id", "private image URL", "private_custom_key", "private_block_key",
    ):
        assert private_value not in rendered


def test_inspector_reports_session_id_and_ordinal_relations_as_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        {"type": "session_meta", "ordinal": 1, "payload": {"id": "private-one", "session_id": "private-one"}},
        {"type": "session_meta", "ordinal": 2, "payload": {"id": "private-two", "session_id": "private-three"}},
        {"type": "event_msg", "ordinal": 2, "payload": {"type": "task_started"}},
        {"type": "response_item", "ordinal": 1, "payload": {"type": "reasoning"}},
        {"type": "turn_context", "ordinal": 3, "payload": {"model": "private-model"}},
    ]
    store, snapshot = _snapshot(tmp_path, monkeypatch, {
        "one.jsonl": b"".join(_line(record) for record in records),
    })

    result = inspect_codex_snapshot(store, snapshot.stored_path, expected_digest=snapshot.snapshot_sha256)

    assert result.session_id_pair_count == 2
    assert result.session_id_pair_match_count == 1
    assert result.session_id_pair_nonmatching_count == 1
    assert result.parseable_record_ordinal_count == 5
    assert result.adjacent_equal_record_ordinal_count == 1
    assert result.record_ordinal_regression_count == 1
    assert "private-one" not in repr(result)
    assert "private-model" not in repr(result)


def test_inspector_counts_duplicate_json_key_lines_without_exposing_keys_or_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = (
        b'{"private_top_key":"one","private_top_key":"two"}\n'
        b'{"type":"response_item","payload":{"type":"message","role":"user",'
        b'"private_nested_key":"three","private_nested_key":"four","content":[]}}\n'
    )
    store, snapshot = _snapshot(tmp_path, monkeypatch, {"one.jsonl": raw})

    result = inspect_codex_snapshot(store, snapshot.stored_path, expected_digest=snapshot.snapshot_sha256)

    assert result.duplicate_json_key_line_count == 2
    assert result.valid_json_record_count == 0
    assert result.malformed_json_line_count == 0
    rendered = repr(result)
    for private_value in (
        "private_top_key", "private_nested_key", "one", "two", "three", "four",
    ):
        assert private_value not in rendered


def test_inspector_rejects_conflicting_outer_and_nested_session_meta_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = {"type": "session_meta", "payload": {
        "id": "outer-session-id", "meta": {"id": "nested-session-id"},
    }}
    store, snapshot = _snapshot(tmp_path, monkeypatch, {"one.jsonl": _line(wrapper)})

    with pytest.raises(CodexJSONLInspectionError) as error:
        inspect_codex_snapshot(store, snapshot.stored_path, expected_digest=snapshot.snapshot_sha256)

    assert error.value.code == "session_metadata_id_conflict"


def test_inspector_rejects_snapshot_tampering_without_path_bearing_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, snapshot = _snapshot(tmp_path, monkeypatch, {"one.jsonl": _line({"type": "session_meta"})})
    (snapshot.stored_path / "files" / "one.jsonl").write_bytes(b"tampered")

    with pytest.raises(CodexJSONLInspectionError) as error:
        inspect_codex_snapshot(store, snapshot.stored_path, expected_digest=snapshot.snapshot_sha256)

    assert error.value.code == "snapshot_invalid"
    assert str(tmp_path) not in str(error.value)


def test_inspector_enforces_record_and_line_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, snapshot = _snapshot(tmp_path, monkeypatch, {"one.jsonl": b"{}\n{}\n"})
    monkeypatch.setattr(inspector_module, "_MAX_RECORDS", 1)
    with pytest.raises(CodexJSONLInspectionError) as error:
        inspect_codex_snapshot(store, snapshot.stored_path, expected_digest=snapshot.snapshot_sha256)
    assert error.value.code == "record_limit_exceeded"

    monkeypatch.setattr(inspector_module, "_MAX_RECORDS", 10)
    monkeypatch.setattr(inspector_module, "_MAX_LINE_BYTES", 2)
    result = inspect_codex_snapshot(store, snapshot.stored_path, expected_digest=snapshot.snapshot_sha256)
    assert result.oversized_line_count == 2


def test_inspector_enforces_message_id_and_image_memory_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = lambda identifier, image: {"type": "response_item", "payload": {
        "type": "message", "id": identifier, "role": "user",
        "content": [{"type": "input_image", "image_url": "data:image/png;base64," +
                    base64.b64encode(image).decode()}],
    }}
    store, snapshot = _snapshot(tmp_path, monkeypatch, {
        "one.jsonl": _line(message("first", b"one")) + _line(message("second", b"two")),
    })

    monkeypatch.setattr(inspector_module, "_MAX_TRACKED_MESSAGE_IDS", 1)
    with pytest.raises(CodexJSONLInspectionError) as error:
        inspect_codex_snapshot(store, snapshot.stored_path, expected_digest=snapshot.snapshot_sha256)
    assert error.value.code == "message_id_limit_exceeded"

    monkeypatch.setattr(inspector_module, "_MAX_TRACKED_MESSAGE_IDS", 10)
    monkeypatch.setattr(inspector_module, "_MAX_INLINE_IMAGE_DECODED_BYTES", 5)
    with pytest.raises(CodexJSONLInspectionError) as error:
        inspect_codex_snapshot(store, snapshot.stored_path, expected_digest=snapshot.snapshot_sha256)
    assert error.value.code == "image_byte_limit_exceeded"
