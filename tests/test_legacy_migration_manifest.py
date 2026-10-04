"""Synthetic migration journal records; never use personal data here."""

from pathlib import Path
import os

import pytest

import cognivault.migration.manifest as manifest_module
from cognivault.migration.manifest import (
    ManifestConflict,
    MigrationItem,
    MigrationJournal,
    stable_migration_key,
    summarize,
)
from cognivault.migration.planner import SourceRecord, plan_records


def _item(**overrides) -> MigrationItem:
    values = {
        "category": "History",
        "legacy_system": "synthetic-legacy",
        "legacy_source_type": "chat_message",
        "legacy_item_id": "message-1",
        "source_fingerprint": "a" * 64,
        "target_type": "history_item",
        "target_logical_id": None,
        "action": "import",
        "status": "planned",
        "source_event_time": "2024-01-02T03:04:05Z",
        "imported_at": None,
        "dedup_decision": "none",
        "validation_state": "valid",
        "reason_code": None,
        "error_code": None,
        "source_size_bytes": 12,
    }
    values.update(overrides)
    return MigrationItem(**values)


def test_stable_migration_key_uses_system_identity_and_content_hash() -> None:
    first = stable_migration_key("legacy-a", "item-7", "a" * 64)
    assert first == stable_migration_key("legacy-a", "item-7", "a" * 64)
    assert first != stable_migration_key("legacy-a", "item-8", "a" * 64)
    assert first != stable_migration_key("legacy-a", "item-7", "b" * 64)
    assert first.startswith("migration:")


def test_stable_migration_key_rejects_path_shaped_source_ids() -> None:
    for value in ("nested/item", r"nested\item", "private.md", ".."):
        with pytest.raises(ValueError, match="Invalid migration identity"):
            stable_migration_key("legacy-a", value, "a" * 64)


def test_journal_reopens_and_reruns_items_without_duplicate_rows(tmp_path: Path) -> None:
    path = tmp_path / "private-migration.sqlite3"
    first = MigrationJournal(path, run_id="migration-synthetic-1")
    item = _item()
    first.add_batch([item], batch_number=1)
    first.close()

    resumed = MigrationJournal(path, run_id="migration-synthetic-1")
    resumed.add_batch([item], batch_number=1)
    assert resumed.list_items() == (item,)
    assert resumed.checkpoints() == (1,)
    resumed.close()


def test_journal_write_connections_use_full_synchronous_wal_durability(tmp_path: Path) -> None:
    journal = MigrationJournal(tmp_path / "private-migration.sqlite3", run_id="migration-wal-test")
    with manifest_module.closing(journal._connect(write=True)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].casefold() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    journal.close()


def test_manifest_keeps_source_event_time_separate_from_import_time(tmp_path: Path) -> None:
    item = _item(status="committed", source_event_time="2020-02-03T04:05:06+02:00",
                 imported_at="2026-09-27T08:09:10Z")
    journal = MigrationJournal(tmp_path / "private-migration.sqlite3", run_id="migration-time-test")
    journal.add_batch([item], batch_number=1)
    restored = journal.list_items()[0]
    assert restored.source_event_time == "2020-02-03T04:05:06+02:00"
    assert restored.imported_at == "2026-09-27T08:09:10Z"


def test_changed_checkpoint_replay_rolls_back_the_whole_batch(tmp_path: Path) -> None:
    journal = MigrationJournal(tmp_path / "private-migration.sqlite3", run_id="migration-test-2")
    existing = _item()
    journal.add_batch([existing], batch_number=1)

    first_new = _item(legacy_item_id="message-2", source_fingerprint="b" * 64)
    journal.add_batch([first_new], batch_number=2)
    changed_replay = _item(legacy_item_id="message-3", source_fingerprint="c" * 64)
    with pytest.raises(ManifestConflict):
        journal.add_batch([changed_replay], batch_number=2)

    assert set(journal.list_items()) == {existing, first_new}
    assert journal.checkpoints() == (1, 2)
    journal.close()


def test_manifest_updates_planned_row_to_committed_without_a_duplicate(tmp_path: Path) -> None:
    journal = MigrationJournal(tmp_path / "private-migration.sqlite3", run_id="migration-transition-test")
    planned = _item()
    committed = _item(status="committed", target_logical_id="history-123",
                      imported_at="2026-09-27T08:09:10Z")
    journal.add_batch([planned], batch_number=1)
    journal.add_batch([committed], batch_number=1)
    assert journal.list_items() == (committed,)
    assert journal.checkpoints() == (1,)
    journal.close()


def test_journal_rejects_any_overlap_with_protected_source_root(tmp_path: Path) -> None:
    source_root = tmp_path / "legacy"
    source_root.mkdir()
    for candidate in (source_root / "manifest.sqlite3", source_root):
        with pytest.raises(ValueError, match="Invalid private migration journal"):
            MigrationJournal(candidate, run_id="migration-path-guard-test",
                             protected_paths=(source_root,))


def test_planner_conflicting_legacy_ids_are_durable_and_replayable(tmp_path: Path) -> None:
    source = SourceRecord(
        category="History", legacy_system="synthetic-legacy", legacy_source_type="chat_message",
        legacy_item_id="message-conflict", source_fingerprint="b" * 64,
        target_type="history_item", target_logical_id=None, source_event_time=None,
        intended_action="import", validation_state="valid", requires_backend="history",
        reason_code=None, source_size_bytes=10,
    )
    conflicting = SourceRecord(**{
        **source.__dict__, "source_fingerprint": "c" * 64,
    })
    planned = plan_records([source, conflicting], existing_targets=set(),
                           target_health={"history": "ready"})
    path = tmp_path / "private-migration.sqlite3"
    journal = MigrationJournal(path, run_id="migration-conflict-test")
    journal.add_batch(planned, batch_number=1)
    assert len(journal.list_items()) == 2
    assert all(item.error_code == "duplicate_legacy_id_conflict" for item in journal.list_items())
    journal.close()

    resumed = MigrationJournal(path, run_id="migration-conflict-test")
    resumed.add_batch(planned, batch_number=1)
    assert len(resumed.list_items()) == 2
    assert resumed.checkpoints() == (1,)
    resumed.close()


def test_same_id_and_content_with_distinct_plan_outcomes_are_journaled(tmp_path: Path) -> None:
    base = SourceRecord(
        category="Assets", legacy_system="synthetic-legacy", legacy_source_type="image",
        legacy_item_id="same-asset", source_fingerprint="d" * 64, target_type="asset",
        target_logical_id=None, source_event_time=None, intended_action="import",
        validation_state="valid", requires_backend="assets", reason_code=None,
        source_size_bytes=12,
    )
    planned = plan_records([base, base], existing_targets=set(), target_health={"assets": "ready"})
    path = tmp_path / "private-migration.sqlite3"
    journal = MigrationJournal(path, run_id="migration-duplicate-id-test")
    journal.add_batch(planned, batch_number=1)
    assert {(item.action, item.status) for item in journal.list_items()} == {
        ("skip", "skipped"), ("import", "planned"),
    }
    journal.close()


def test_manifest_rejects_absolute_paths_in_identifiers(tmp_path: Path) -> None:
    journal = MigrationJournal(tmp_path / "private-migration.sqlite3", run_id="migration-test-3")
    with pytest.raises(ValueError, match="Invalid migration item"):
        journal.add_batch([_item(legacy_item_id=r"C:\private\message.md")], batch_number=1)
    assert journal.list_items() == ()
    journal.close()


def test_manifest_rejects_unhashable_action_as_invalid_item(tmp_path: Path) -> None:
    journal = MigrationJournal(tmp_path / "private-migration.sqlite3", run_id="migration-invalid-action")
    with pytest.raises(ValueError, match="Invalid migration item"):
        journal.add_batch([_item(action=["import"])], batch_number=1)
    assert journal.list_items() == ()


def test_journal_rejects_reparse_point_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manifest_module, "_path_has_reparse_point", lambda _path: True)
    with pytest.raises(ValueError, match="Invalid private migration journal"):
        MigrationJournal(tmp_path / "private-migration.sqlite3", run_id="migration-reparse-test")


def test_journal_rejects_unsafe_wal_sidecar_before_open(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    journal_path = tmp_path / "private-migration.sqlite3"
    wal_path = Path(str(journal_path) + "-wal")
    original = manifest_module._path_has_reparse_point
    monkeypatch.setattr(
        manifest_module, "_path_has_reparse_point",
        lambda path: Path(path) == wal_path or original(path),
    )
    with pytest.raises(ValueError, match="Invalid private migration journal"):
        MigrationJournal(journal_path, run_id="migration-sidecar-guard")
    assert not journal_path.exists()


def test_journal_rejects_sidecar_overlap_with_protected_database(tmp_path: Path) -> None:
    journal_path = tmp_path / "private-migration.sqlite3"
    protected_wal = Path(str(journal_path) + "-wal")
    with pytest.raises(ValueError, match="Invalid private migration journal"):
        MigrationJournal(journal_path, run_id="migration-sidecar-overlap",
                         protected_paths=(protected_wal,))


def test_journal_rejects_hardlinked_database_and_sidecar(tmp_path: Path) -> None:
    protected_database = tmp_path / "legacy.sqlite3"
    protected_database.write_bytes(b"synthetic protected database")
    journal_path = tmp_path / "private" / "manifest.sqlite3"
    journal_path.parent.mkdir()
    try:
        os.link(protected_database, journal_path)
    except OSError:
        pytest.skip("host cannot create hard links")
    with pytest.raises(ValueError, match="Invalid private migration journal"):
        MigrationJournal(journal_path, run_id="migration-hardlink-guard",
                         protected_paths=(protected_database,))

    journal_path.unlink()
    wal_path = Path(str(journal_path) + "-wal")
    os.link(protected_database, wal_path)
    with pytest.raises(ValueError, match="Invalid private migration journal"):
        MigrationJournal(journal_path, run_id="migration-hardlink-sidecar-guard",
                         protected_paths=(protected_database,))


def test_summary_counts_actions_and_never_returns_item_identifiers() -> None:
    private_id = "synthetic-secret-item-id"
    items = (
        _item(action="reuse", status="planned", dedup_decision="same_authoritative_root"),
        _item(legacy_item_id=private_id, source_fingerprint="b" * 64,
              action="import", status="planned"),
        _item(legacy_item_id="message-3", source_fingerprint="c" * 64,
              action="skip", status="skipped", dedup_decision="content_hash"),
        _item(legacy_item_id="message-4", source_fingerprint="d" * 64,
              action="archive", status="skipped", reason_code="archive_only"),
        _item(legacy_item_id="message-5", source_fingerprint="e" * 64,
              action="import", status="unresolved", error_code="history_target_not_configured",
              validation_state="unresolved"),
        _item(legacy_item_id="message-6", source_fingerprint="f" * 64,
              action="skip", status="error", error_code="invalid_record",
              validation_state="invalid"),
        _item(legacy_item_id="message-7", source_fingerprint="7" * 64,
              action="import", status="committed"),
    )
    result = summarize(items)

    assert result["History"] == {
        "found": 7,
        "reuse": 1,
        "planned_import": 1,
        "committed": 1,
        "deduplicated": 1,
        "archive_only": 1,
        "skip": 0,
        "unresolved": 1,
        "errors": 1,
        "dedup_decisions": {"content_hash": 1},
        "reason_codes": {"archive_only": 1},
        "error_codes": {"history_target_not_configured": 1, "invalid_record": 1},
    }
    assert private_id not in repr(result)
    assert "source_fingerprint" not in repr(result)
    assert result["History"]["found"] == sum(
        result["History"][key]
        for key in ("reuse", "planned_import", "committed", "deduplicated",
                    "archive_only", "skip", "unresolved", "errors")
    )


def test_unresolved_item_keeps_orthogonal_content_dedup_decision() -> None:
    item = _item(action="archive", status="unresolved", dedup_decision="content_hash",
                 validation_state="unresolved", reason_code="unpaired_evidence",
                 error_code="source_relation_unresolved")
    counts = summarize((item,))["History"]
    assert counts["unresolved"] == 1
    assert counts["deduplicated"] == 0
    assert counts["dedup_decisions"] == {"content_hash": 1}
    assert sum(counts[key] for key in (
        "reuse", "planned_import", "committed", "deduplicated", "archive_only",
        "skip", "unresolved", "errors",
    )) == counts["found"]


def test_storage_estimate_counts_only_bytes_that_would_be_imported() -> None:
    from cognivault.migration.manifest import estimate_storage

    items = (
        _item(source_size_bytes=100, action="reuse"),
        _item(legacy_item_id="message-2", source_fingerprint="b" * 64,
              source_size_bytes=30, action="import", status="planned"),
        _item(legacy_item_id="message-3", source_fingerprint="c" * 64,
              source_size_bytes=50, action="skip", status="skipped",
              dedup_decision="content_hash"),
        _item(legacy_item_id="message-4", source_fingerprint="d" * 64,
              source_size_bytes=20, action="archive", status="skipped"),
        _item(legacy_item_id="message-5", source_fingerprint="e" * 64,
              source_size_bytes=10, action="import", status="unresolved",
              validation_state="unresolved", error_code="history_target_not_configured"),
    )
    assert estimate_storage(items) == {
        "source_bytes": 210,
        "estimated_import_bytes": 40,
        "ready_import_bytes": 30,
    }
