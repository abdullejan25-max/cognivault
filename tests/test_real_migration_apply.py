"""Synthetic sources exercise snapshots, retry and failure recovery."""

import hashlib
import os
from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

from cognivault.contracts import GatewayError
from cognivault.adapters.history import SQLiteHistoryBackend
from cognivault.migration.real_apply import (
    FileSource, apply_sources, snapshot_and_verify_restore,
)


def make_target(tmp_path):
    target = tmp_path / "target" / "history.db"
    target.parent.mkdir()
    SQLiteHistoryBackend(target).register_source("synthetic", "Synthetic")
    return target


def source(tmp_path, content=b"# Original\r\n\nUnicode: \xe6\x95\xb0\xe5\xad\xa6\n"):
    root = tmp_path / "sources"
    root.mkdir(exist_ok=True)
    path = root / "one.md"
    path.write_bytes(content)
    return FileSource(root, path, "v1-legacy", "synthetic-one", "legacy_markdown",
                      hashlib.sha256(content).hexdigest(), len(content), 0)


def test_snapshot_restore_preserves_source_and_reopens(tmp_path):
    target = make_target(tmp_path)
    before = target.read_bytes()
    receipt = snapshot_and_verify_restore(target, tmp_path / "backups")
    assert target.read_bytes() == before
    assert receipt["restore_verified"] is True
    backup = Path(receipt["private_snapshot"])
    assert backup.is_file() and backup != target
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert receipt["sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest()


def test_apply_readback_and_idempotent_rerun(tmp_path):
    target = make_target(tmp_path)
    item = source(tmp_path)
    journal = tmp_path / "journal" / "apply.db"
    kwargs = dict(database=target, sources=[item], journal_path=journal,
                  backup_root=tmp_path / "backups", run_id="migration-" + "a" * 32)
    first = apply_sources(**kwargs)
    second = apply_sources(**kwargs)
    assert first["imported"] == 1 and first["deduplicated"] == 0
    assert second["imported"] == 0 and second["deduplicated"] == 1
    assert first["target_ids"] == second["target_ids"]
    assert first["source_set_digest"] == second["source_set_digest"]
    assert second["readback_verified"] == 1
    with sqlite3.connect(journal) as connection:
        assert connection.execute("SELECT COUNT(*) FROM migration_items WHERE status='committed'").fetchone()[0] == 1


def test_changed_source_fails_before_any_snapshot_or_write(tmp_path):
    target = make_target(tmp_path)
    item = source(tmp_path)
    item.path.write_bytes(b"changed")
    before = target.read_bytes()
    with pytest.raises(ValueError, match="source"):
        apply_sources(database=target, sources=[item], journal_path=tmp_path / "journal" / "apply.db",
                      backup_root=tmp_path / "backups", run_id="migration-" + "a" * 32)
    assert target.read_bytes() == before
    assert not (tmp_path / "backups").exists()


def test_target_or_backup_overlapping_source_rejected(tmp_path):
    target = make_target(tmp_path)
    item = source(tmp_path)
    with pytest.raises(ValueError):
        apply_sources(database=target, sources=[item], journal_path=tmp_path / "journal" / "apply.db",
                      backup_root=item.root / "backups", run_id="migration-" + "a" * 32)


def test_journal_failure_after_commit_can_retry(tmp_path, monkeypatch):
    from cognivault.migration.manifest import MigrationJournal
    target = make_target(tmp_path)
    item = source(tmp_path)
    kwargs = dict(database=target, sources=[item], journal_path=tmp_path / "journal" / "apply.db",
                  backup_root=tmp_path / "backups", run_id="migration-" + "a" * 32)
    original = MigrationJournal.add_batch
    def fail_after_write(self, items, *, batch_number):
        values = tuple(items)
        if values[0].status == "committed":
            raise RuntimeError("synthetic checkpoint failure")
        return original(self, values, batch_number=batch_number)
    monkeypatch.setattr(MigrationJournal, "add_batch", fail_after_write)
    with pytest.raises(RuntimeError):
        apply_sources(**kwargs)
    monkeypatch.setattr(MigrationJournal, "add_batch", original)
    result = apply_sources(**kwargs)
    assert result["imported"] == 0 and result["deduplicated"] == 1
    assert result["readback_verified"] == 1


def test_restored_target_with_committed_journal_requires_new_run(tmp_path):
    import shutil
    target = make_target(tmp_path)
    item = source(tmp_path)
    kwargs = dict(database=target, sources=[item], journal_path=tmp_path / "journal" / "apply.db",
                  backup_root=tmp_path / "backups", run_id="migration-" + "a" * 32)
    receipt = apply_sources(**kwargs)
    shutil.copyfile(receipt["snapshot"]["private_snapshot"], target)
    before = target.read_bytes()
    with pytest.raises(ValueError, match="checkpoint"):
        apply_sources(**kwargs)
    assert target.read_bytes() == before


def test_target_hardlink_fails_before_snapshot_or_business_write(tmp_path):
    target = make_target(tmp_path)
    alias = tmp_path / "protected-original.db"
    try:
        os.link(target, alias)
    except OSError:
        pytest.skip("hard links are unavailable")
    before = alias.read_bytes()
    with pytest.raises(ValueError):
        apply_sources(database=target, sources=[source(tmp_path)],
                      journal_path=tmp_path / "journal" / "apply.db",
                      backup_root=tmp_path / "backups", run_id="migration-" + "a" * 32)
    assert alias.read_bytes() == before
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("suffix", ("-wal", "-shm", "-journal"))
def test_target_sidecar_hardlink_fails_before_snapshot_or_write(tmp_path, suffix):
    target = make_target(tmp_path)
    sidecar = Path(str(target) + suffix)
    sidecar.write_bytes(b"")
    alias = tmp_path / "protected-sidecar"
    try:
        os.link(sidecar, alias)
    except OSError:
        pytest.skip("hard links are unavailable")
    before = target.read_bytes()
    with pytest.raises(ValueError):
        apply_sources(database=target, sources=[source(tmp_path)],
                      journal_path=tmp_path / "journal" / "apply.db",
                      backup_root=tmp_path / "backups", run_id="migration-" + "a" * 32)
    assert target.read_bytes() == before and alias.read_bytes() == b""
    assert not (tmp_path / "backups").exists()


def test_manifest_invalid_source_identity_fails_before_snapshot_or_write(tmp_path):
    target = make_target(tmp_path)
    item = replace(source(tmp_path), source_item_id="private/path.md")
    before = target.read_bytes()
    with pytest.raises((ValueError, GatewayError)):
        apply_sources(database=target, sources=[item],
                      journal_path=tmp_path / "journal" / "apply.db",
                      backup_root=tmp_path / "backups", run_id="migration-" + "a" * 32)
    assert target.read_bytes() == before
    assert not (tmp_path / "backups").exists()


def test_snapshot_retains_committed_wal_and_leaves_source_sidecars_unchanged(tmp_path):
    target = make_target(tmp_path)
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE wal_only(value TEXT)")
        connection.execute("INSERT INTO wal_only VALUES ('committed in wal')")
        connection.commit()
        originals = {Path(str(target) + suffix): Path(str(target) + suffix).read_bytes()
                     for suffix in ("", "-wal", "-shm")}
        receipt = snapshot_and_verify_restore(target, tmp_path / "backups")
        assert all(path.read_bytes() == content for path, content in originals.items())
        with sqlite3.connect(receipt["private_snapshot"]) as backup:
            assert backup.execute("SELECT value FROM wal_only").fetchone()[0] == "committed in wal"


def test_projected_target_limit_rejects_before_snapshot_or_write(tmp_path, monkeypatch):
    from cognivault.migration import real_apply
    target = make_target(tmp_path)
    item = source(tmp_path)
    before = target.read_bytes()
    monkeypatch.setattr(real_apply, "MAX_TARGET_BYTES", len(before) + item.byte_count + 8191)
    with pytest.raises(ValueError, match="recoverable snapshot limit"):
        apply_sources(database=target, sources=[item],
                      journal_path=tmp_path / "journal" / "apply.db",
                      backup_root=tmp_path / "backups", run_id="migration-" + "a" * 32)
    assert target.read_bytes() == before
    assert not (tmp_path / "backups").exists()


def test_existing_source_rerun_does_not_reserve_payload_twice(tmp_path, monkeypatch):
    from cognivault.migration import real_apply
    target = make_target(tmp_path)
    kwargs = dict(database=target, sources=[source(tmp_path)],
                  journal_path=tmp_path / "journal" / "apply.db",
                  backup_root=tmp_path / "backups", run_id="migration-" + "a" * 32)
    apply_sources(**kwargs)
    monkeypatch.setattr(real_apply, "MAX_TARGET_BYTES", target.stat().st_size)
    result = apply_sources(**kwargs)
    assert result["imported"] == 0 and result["deduplicated"] == 1
    assert result["readback_verified"] == 1


@pytest.mark.parametrize("source_type,category", (
    ("legacy_derived_fact", "Atomic Facts"),
    ("legacy_wrong_answer_document", "Wrong Answers"),
))
def test_source_kind_remains_distinct_in_committed_journal(tmp_path, source_type, category):
    target = make_target(tmp_path)
    item = replace(source(tmp_path), source_type=source_type)
    journal = tmp_path / "journal" / "apply.db"
    result = apply_sources(database=target, sources=[item], journal_path=journal,
                           backup_root=tmp_path / "backups", run_id="migration-" + "a" * 32)
    assert result["imported"] == 1
    with sqlite3.connect(journal) as connection:
        assert connection.execute("SELECT category,legacy_source_type,status FROM migration_items").fetchone() \
            == (category, source_type, "committed")


def test_corrupted_existing_blob_prevents_new_batch_members_committing(tmp_path):
    from cognivault.contracts import GatewayError
    target = make_target(tmp_path)
    original = source(tmp_path)
    journal = tmp_path / "journal" / "apply.db"
    apply_sources(database=target, sources=[original], journal_path=journal,
                  backup_root=tmp_path / "backups", run_id="migration-" + "a" * 32)
    with sqlite3.connect(target) as connection:
        rowid = connection.execute("SELECT rowid FROM legacy_source_records").fetchone()[0]
        with connection.blobopen("legacy_source_records", "content", rowid) as blob:
            blob.write(b"!")
    new_path = original.root / "two.md"
    new_path.write_bytes(original.path.read_bytes())
    new = replace(original, path=new_path, source_item_id="synthetic-two", source_order=1)
    with pytest.raises(GatewayError) as raised:
        apply_sources(database=target, sources=[new, original], journal_path=journal,
                      backup_root=tmp_path / "backups", run_id="migration-" + "b" * 32)
    assert raised.value.code == "CONFLICT"
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT COUNT(*) FROM legacy_source_records").fetchone()[0] == 1


def test_restored_target_cannot_reuse_later_committed_journal(tmp_path):
    target = make_target(tmp_path)
    kwargs = dict(database=target, sources=[source(tmp_path)],
                  journal_path=tmp_path / "journal" / "apply.db",
                  backup_root=tmp_path / "backups", run_id="migration-" + "a" * 32)
    result = apply_sources(**kwargs)
    target.write_bytes(Path(result["snapshot"]["private_snapshot"]).read_bytes())
    restored_bytes = target.read_bytes()
    backup_files = set((tmp_path / "backups").iterdir())
    with pytest.raises(ValueError, match="checkpoint"):
        apply_sources(**kwargs)
    assert target.read_bytes() == restored_bytes
    assert set((tmp_path / "backups").iterdir()) == backup_files
    resumed = apply_sources(**{**kwargs, "run_id": "migration-" + "b" * 32})
    assert resumed["imported"] == 1 and resumed["readback_verified"] == 1
