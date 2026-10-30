"""Synthetic regressions for transient SQLite journals and path safety."""
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from cognivault.adapters import memory
from cognivault.contracts import GatewayError


REF = "history:" + "a" * 64


@pytest.mark.parametrize("when", ["before", "after"])
def test_journal_disappearance_during_validation_allows_memory_write(tmp_path, monkeypatch, when):
    store = memory.SQLiteMemoryStore(tmp_path / "memory.db")
    original = store.create("synthetic", "goal", "physics", [REF], "original")["memory"]
    journal = tmp_path / "memory.db-journal"
    journal.touch()
    stat_file = Path.stat
    check_reparse = memory._path_has_reparse_point
    armed = removed = False

    def check_then_arm(path):
        nonlocal armed
        result = check_reparse(path)
        # Inject at the leaf check, after ancestor/reparse validation finishes.
        if path in (journal, journal.parent):
            armed = True
        return result

    def remove_during_stat(path, *, follow_symlinks=True):
        nonlocal removed
        if path == journal and armed and not removed:
            removed = True
            if when == "before":
                journal.unlink()
                return stat_file(path, follow_symlinks=follow_symlinks)
            info = stat_file(path, follow_symlinks=follow_symlinks)
            journal.unlink()
            return info
        return stat_file(path, follow_symlinks=follow_symlinks)

    with monkeypatch.context() as patch:
        patch.setattr(memory, "_path_has_reparse_point", check_then_arm)
        patch.setattr(Path, "stat", remove_during_stat)
        saved = store.create("synthetic", "priority", "mechanics", [REF], "next")["memory"]
    assert removed, "the real synthetic journal must disappear during validation"
    assert saved["value"] == "mechanics"
    assert store.fetch(saved["memory_id"])["memory"]["value"] == "mechanics"
    assert store.fetch(original["memory_id"])["memory"]["value"] == "physics"


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_linked_memory_namespace_is_rejected_before_open(tmp_path, monkeypatch, suffix):
    store = memory.SQLiteMemoryStore(tmp_path / "memory.db")
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"synthetic outside target")
    os.link(sentinel, tmp_path / ("memory.db" + suffix))

    def forbidden_open(*args, **kwargs):
        raise AssertionError("unsafe namespace reached SQLite")

    monkeypatch.setattr(memory.sqlite3, "connect", forbidden_open)
    with pytest.raises(GatewayError) as error:
        store.create("synthetic", "goal", "physics", [REF], "unsafe")
    assert error.value.code == "STORAGE_UNAVAILABLE"
    assert sentinel.read_bytes() == b"synthetic outside target"
    if suffix:
        assert not store.database_path.exists()


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_nonfile_memory_namespace_is_rejected(tmp_path, suffix):
    store = memory.SQLiteMemoryStore(tmp_path / "memory.db")
    (tmp_path / ("memory.db" + suffix)).mkdir()
    with pytest.raises(GatewayError) as error:
        store.create("synthetic", "goal", "physics", [REF], "unsafe")
    assert error.value.code == "STORAGE_UNAVAILABLE"
    if suffix:
        assert not store.database_path.exists()


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
@pytest.mark.parametrize("kind", ["reparse", "symlink", "permission"])
def test_unsafe_leaf_metadata_is_rejected(tmp_path, monkeypatch, suffix, kind):
    store = memory.SQLiteMemoryStore(tmp_path / "memory.db")
    candidate = tmp_path / ("memory.db" + suffix)
    candidate.touch()
    stat_file = Path.stat

    def unsafe_metadata(path, *, follow_symlinks=True):
        if path == candidate:
            if kind == "permission":
                raise PermissionError("synthetic denied metadata")
            info = stat_file(path, follow_symlinks=follow_symlinks)
            return SimpleNamespace(st_mode=stat.S_IFLNK if kind == "symlink" else info.st_mode,
                                   st_nlink=info.st_nlink,
                                   st_file_attributes=0x400 if kind == "reparse" else 0)
        return stat_file(path, follow_symlinks=follow_symlinks)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", unsafe_metadata)
        with pytest.raises(GatewayError) as error:
            store.create("synthetic", "goal", "physics", [REF], "unsafe")
    assert error.value.code == "STORAGE_UNAVAILABLE"
    assert candidate.read_bytes() == b""
    if suffix:
        assert not store.database_path.exists()


@pytest.mark.parametrize("kind", ["reparse", "permission"])
def test_unsafe_parent_is_rejected_before_creating_memory(tmp_path, monkeypatch, kind):
    parent = tmp_path / "synthetic-parent"
    parent.mkdir()
    store = memory.SQLiteMemoryStore(parent / "memory.db")
    stat_file = Path.stat

    def unsafe_parent(path, *, follow_symlinks=True):
        if path == parent:
            if kind == "permission":
                raise PermissionError("synthetic denied parent")
            info = stat_file(path, follow_symlinks=follow_symlinks)
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return stat_file(path, follow_symlinks=follow_symlinks)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", unsafe_parent)
        with pytest.raises(GatewayError) as error:
            store.create("synthetic", "goal", "physics", [REF], "unsafe")
    assert error.value.code == "STORAGE_UNAVAILABLE"
    assert not list(parent.iterdir())
