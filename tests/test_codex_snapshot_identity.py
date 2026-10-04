"""Synthetic registration identity checks; never read real private snapshots."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from cognivault.migration.codex_snapshot import (
    CodexSnapshotError, PrivateCodexJSONLSnapshotStore, _canonical_json,
)
from cognivault.migration.conversation_registry import ConversationSourceRecord


def _fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    source = tmp_path / "sessions"
    source.mkdir()
    (source / "synthetic.jsonl").write_bytes(b'{"type":"synthetic"}\n')
    store = PrivateCodexJSONLSnapshotStore(tmp_path / "private" / "snapshots")
    captured = store.snapshot_jsonl_tree(source)
    record = ConversationSourceRecord(
        source_id="source-" + "a" * 32, source_system="codex", source_type="local_history",
        acquisition_method="bounded_local_inventory", discovered=True, accessible=True,
        export_status="available", import_status="blocked", raw_format="jsonl",
        stable_identity=None, conversation_count=None, message_count=None,
        earliest_known_time=None, latest_known_time=None, source_hash=captured.snapshot_sha256,
        manifest_hash=hashlib.sha256((captured.stored_path / "manifest.json").read_bytes()).hexdigest(),
        imported_count=0, deduplicated_count=0, unresolved_count=1,
        coverage_notes=("normalization_not_started",), private_locator=f"path:{source}",
    )
    return store, captured, record


def _verify(*args, **kwargs):
    from cognivault.migration.codex_identity import verify_registered_codex_snapshot
    return verify_registered_codex_snapshot(*args, **kwargs)


def test_distinct_snapshot_and_manifest_hashes_identify_registered_capture(tmp_path, monkeypatch):
    store, captured, record = _fixture(tmp_path, monkeypatch)
    before = {p: p.stat().st_mtime_ns for p in captured.stored_path.rglob("*")}
    assert record.source_hash != record.manifest_hash
    selected = _verify(record, snapshot_root=store.root)
    assert selected.stored_path == captured.stored_path
    assert selected.snapshot_sha256 == record.source_hash
    assert selected.file_count == 1
    assert selected.byte_count == captured.byte_count
    assert selected.duplicate is True
    assert record.private_locator == f"path:{tmp_path / 'sessions'}"
    assert before == {p: p.stat().st_mtime_ns for p in captured.stored_path.rglob("*")}


@pytest.mark.parametrize("overrides", [
    {"source_system": "legacy"}, {"source_type": "legacy_archive"},
    {"raw_format": "markdown"}, {"source_hash": None}, {"manifest_hash": None},
    {"private_locator": None}, {"private_locator": "official:google_takeout"},
])
def test_ineligible_registration_is_rejected(tmp_path, monkeypatch, overrides):
    store, _, record = _fixture(tmp_path, monkeypatch)
    with pytest.raises(CodexSnapshotError) as error:
        _verify(replace(record, **overrides), snapshot_root=store.root)
    assert error.value.code == "invalid_registration"
    assert str(tmp_path) not in str(error.value)


def test_captured_at_change_cannot_bypass_registered_manifest_identity(tmp_path, monkeypatch):
    store, captured, record = _fixture(tmp_path, monkeypatch)
    p = captured.stored_path / "manifest.json"
    manifest = json.loads(p.read_bytes())
    manifest["captured_at"] = "2026-01-01T00:00:00Z"
    p.write_bytes(_canonical_json(manifest))
    with pytest.raises(CodexSnapshotError) as error:
        _verify(record, snapshot_root=store.root)
    assert error.value.code == "manifest_identity_mismatch"


def test_wrong_registered_source_digest_does_not_select_other_snapshot(tmp_path, monkeypatch):
    store, _, record = _fixture(tmp_path, monkeypatch)
    with pytest.raises(CodexSnapshotError):
        _verify(replace(record, source_hash="0" * 64), snapshot_root=store.root)


def test_changed_member_bytes_are_rejected(tmp_path, monkeypatch):
    store, captured, record = _fixture(tmp_path, monkeypatch)
    (captured.stored_path / "files" / "synthetic.jsonl").write_bytes(b"tampered\n")
    with pytest.raises(CodexSnapshotError):
        _verify(record, snapshot_root=store.root)


@pytest.mark.parametrize("mode", ["relative", "traversal", "protected", "acquisition_overlap"])
def test_unsafe_snapshot_roots_are_rejected(tmp_path, monkeypatch, mode):
    store, _, record = _fixture(tmp_path, monkeypatch)
    root = store.root
    protected = ()
    if mode == "relative":
        root = Path("private/snapshots")
    elif mode == "traversal":
        root = store.root / ".." / "snapshots"
    elif mode == "protected":
        protected = (store.root,)
    else:
        record = replace(record, private_locator=f"path:{store.root}")
    with pytest.raises(CodexSnapshotError) as error:
        _verify(record, snapshot_root=root, protected_paths=protected)
    assert error.value.code == "unsafe_path"
    assert str(tmp_path) not in str(error.value)


def test_snapshot_symlink_is_rejected(tmp_path, monkeypatch):
    store, captured, record = _fixture(tmp_path, monkeypatch)
    alternate = tmp_path / "linked-snapshots"
    alternate.mkdir()
    try:
        (alternate / record.source_hash).symlink_to(captured.stored_path, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks unavailable on this host")
    with pytest.raises(CodexSnapshotError) as error:
        _verify(record, snapshot_root=alternate)
    assert error.value.code == "unsafe_path"


def test_manifest_member_traversal_fails_even_with_registered_manifest_hash(tmp_path, monkeypatch):
    store, captured, record = _fixture(tmp_path, monkeypatch)
    manifest_path = captured.stored_path / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"][0]["relative_path"] = "..\\escaped.jsonl"
    payload = _canonical_json(manifest)
    manifest_path.write_bytes(payload)
    record = replace(record, manifest_hash=hashlib.sha256(payload).hexdigest())
    with pytest.raises(CodexSnapshotError) as error:
        _verify(record, snapshot_root=store.root)
    assert error.value.code == "unsafe_path"
