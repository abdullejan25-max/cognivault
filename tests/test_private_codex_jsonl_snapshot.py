import hashlib
import json
from pathlib import Path
import os

import pytest

from cognivault.migration.codex_snapshot import (
    CodexSnapshotError,
    CodexSnapshotLimits,
    PrivateCodexJSONLSnapshotStore,
)


def _source(tmp_path: Path, files: dict[str, bytes] | None = None) -> Path:
    root = tmp_path / "codex-sessions"
    root.mkdir(parents=True, exist_ok=True)
    for relative, payload in (files if files is not None else {"one.jsonl": b"synthetic\n"}).items():
        path = root / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return root


def _store(tmp_path: Path, **limits: int) -> PrivateCodexJSONLSnapshotStore:
    return PrivateCodexJSONLSnapshotStore(
        root=tmp_path / "private" / "codex-jsonl",
        limits=CodexSnapshotLimits(**limits),
    )


def test_snapshot_preserves_nested_jsonl_bytes_and_redacts_manifest(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    contents = {
        "2026/09/first.jsonl": b'{"type":"user","text":"synthetic sentinel one"}\n',
        "2026/09/27/second.jsonl": b'{"type":"assistant","text":"synthetic sentinel two"}\n',
    }
    source = _source(tmp_path, contents)

    store = _store(tmp_path)
    first = store.snapshot_jsonl_tree(source)
    second = store.snapshot_jsonl_tree(source)

    assert first.file_count == 2
    assert first.byte_count == sum(map(len, contents.values()))
    assert first.snapshot_sha256 == second.snapshot_sha256
    assert second.duplicate is True
    manifest_bytes = (first.stored_path / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert manifest["source_system"] == "codex"
    assert manifest["file_count"] == 2
    assert manifest["total_bytes"] == first.byte_count
    assert manifest["snapshot_sha256"] == first.snapshot_sha256
    assert str(source).encode() not in manifest_bytes
    assert b"synthetic sentinel" not in manifest_bytes
    for entry in manifest["files"]:
        original = contents[entry["relative_path"]]
        archived = (first.stored_path / "files" / Path(entry["relative_path"])).read_bytes()
        assert archived == original
        assert entry["byte_count"] == len(original)
        assert entry["sha256"] == hashlib.sha256(original).hexdigest()


def test_snapshot_verifies_long_destination_paths(tmp_path: Path) -> None:
    relative = "nested-directory/" + ("long-name-" * 8) + ".jsonl"
    source = _source(tmp_path, {relative: b"long path synthetic bytes"})
    store = _store(tmp_path)

    result = store.snapshot_jsonl_tree(source)
    manifest = store._verify_snapshot(result.stored_path, expected_digest=result.snapshot_sha256)

    assert manifest["file_count"] == 1
    assert manifest["total_bytes"] == len(b"long path synthetic bytes")


@pytest.mark.parametrize(
    ("files", "limits", "code"),
    [
        ({"a.jsonl": b"a", "b.jsonl": b"b"}, {"max_files": 1}, "too_many_files"),
        ({"a.jsonl": b"123"}, {"max_file_bytes": 2}, "file_too_large"),
        ({"a.jsonl": b"12", "b.jsonl": b"34"}, {"max_total_bytes": 3}, "total_too_large"),
        ({"a/b/c.jsonl": b"x"}, {"max_depth": 1}, "too_deep"),
        ({"a.jsonl": b"1", "b.jsonl": b"2"}, {"max_entries": 1}, "too_many_entries"),
        ({}, {}, "no_candidates"),
    ],
)
def test_snapshot_enforces_resource_bounds_and_fixed_errors(
    tmp_path: Path, files: dict[str, bytes], limits: dict[str, int], code: str,
) -> None:
    source = _source(tmp_path, files)
    store = _store(tmp_path, **limits)
    with pytest.raises(CodexSnapshotError) as error:
        store.snapshot_jsonl_tree(source)
    if code:
        assert error.value.code == code
    assert str(tmp_path) not in str(error.value)
    assert not list(store.root.glob(".staging-*"))


def test_snapshot_rejects_hardlinked_jsonl(tmp_path: Path) -> None:
    source = _source(tmp_path)
    try:
        os.link(source / "one.jsonl", source / "linked.jsonl")
    except (OSError, NotImplementedError):
        pytest.skip("host cannot create hard links")
    store = _store(tmp_path)
    with pytest.raises(CodexSnapshotError) as error:
        store.snapshot_jsonl_tree(source)
    assert error.value.code == "unsafe_path"


def test_snapshot_rejects_relative_source_root(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(CodexSnapshotError) as error:
        store.snapshot_jsonl_tree(Path("relative-source"))
    assert error.value.code == "unsafe_path"
    assert str(tmp_path) not in str(error.value)


def test_snapshot_rejects_symlinked_candidate(tmp_path: Path) -> None:
    source = _source(tmp_path)
    linked = source / "linked.jsonl"
    try:
        linked.symlink_to(source / "one.jsonl")
    except (OSError, NotImplementedError):
        pytest.skip("host cannot create symlinks")
    store = _store(tmp_path)
    with pytest.raises(CodexSnapshotError) as error:
        store.snapshot_jsonl_tree(source)
    assert error.value.code == "unsafe_path"


@pytest.mark.skipif(os.name == "nt", reason="FIFO entries are POSIX-only")
def test_snapshot_rejects_non_regular_tree_entry(tmp_path: Path) -> None:
    import stat

    source = _source(tmp_path)
    fifo = source / "pipe"
    try:
        os.mkfifo(fifo)
    except (AttributeError, NotImplementedError, OSError):
        pytest.skip("host cannot create FIFOs")
    assert stat.S_ISFIFO(fifo.stat().st_mode)
    store = _store(tmp_path)
    with pytest.raises(CodexSnapshotError) as error:
        store.snapshot_jsonl_tree(source)
    assert error.value.code == "unsafe_path"


def test_snapshot_detects_source_tree_change_and_cleans_incomplete_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    store = _store(tmp_path)
    original = store._copy_and_verify

    def mutate_after_copy(candidate, files_root):
        copied = original(candidate, files_root)
        (source / "added.jsonl").write_bytes(b"changed after enumeration")
        return copied

    monkeypatch.setattr(store, "_copy_and_verify", mutate_after_copy)
    with pytest.raises(CodexSnapshotError) as error:
        store.snapshot_jsonl_tree(source)
    assert error.value.code == "source_changed"
    assert not list(store.root.glob(".staging-*"))
    assert not [path for path in store.root.iterdir() if path.is_dir()]


def test_duplicate_capture_rejects_corrupt_existing_snapshot(tmp_path: Path) -> None:
    source = _source(tmp_path)
    store = _store(tmp_path)
    first = store.snapshot_jsonl_tree(source)
    archived = first.stored_path / "files" / "one.jsonl"
    archived.write_bytes(b"tampered")
    with pytest.raises(CodexSnapshotError) as error:
        store.snapshot_jsonl_tree(source)
    assert error.value.code == "integrity_mismatch"
    assert first.stored_path.exists()


def test_retry_promotes_completed_staging_snapshot(tmp_path: Path) -> None:
    source = _source(tmp_path)
    store = _store(tmp_path)
    result = store.snapshot_jsonl_tree(source)
    staging = store.root / ".staging-recovery"
    result.stored_path.rename(staging)
    retried = store.snapshot_jsonl_tree(source)
    assert retried.duplicate is True
    assert retried.snapshot_sha256 == result.snapshot_sha256
    assert retried.stored_path.is_dir()
    assert not staging.exists()


def test_retry_preserves_incomplete_staging_and_fails_closed(tmp_path: Path) -> None:
    source = _source(tmp_path)
    store = _store(tmp_path)
    staging = store.root / ".staging-incomplete"
    staging.mkdir()
    marker = staging / "partial"
    marker.write_bytes(b"preserve for recovery")
    with pytest.raises(CodexSnapshotError) as error:
        store.snapshot_jsonl_tree(source)
    assert error.value.code == "recovery_required"
    assert marker.read_bytes() == b"preserve for recovery"
