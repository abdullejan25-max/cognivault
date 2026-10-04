"""Synthetic acquisition ledger integration tests; no production stores."""

import json
import sqlite3
from pathlib import Path

import pytest

from cognivault.migration.history_inventory import inspect_source
from cognivault.migration.history_ledger import HistoryMigrationLedger, HistoryLedgerError


def source(tmp_path, name="first.jsonl"):
    root = tmp_path / "input"
    root.mkdir(exist_ok=True)
    file = root / name
    file.write_bytes(b'{"content":"synthetic-private-text"}\n')
    return inspect_source(file, source_root=root, source_type="workbuddy", acquisition_method="legacy_local_archive")


def test_rerun_and_renamed_copy_preserve_all_acquisitions_without_new_source(tmp_path):
    path = tmp_path / "private/ledger.sqlite3"
    ledger = HistoryMigrationLedger(path)
    record = source(tmp_path)
    assert ledger.discover(record) == "discovered"
    assert ledger.discover(record) == "duplicate"
    assert ledger.discover(source(tmp_path, "renamed.jsonl")) == "duplicate"
    reopened = HistoryMigrationLedger(path)
    summary = reopened.public_summary()
    assert summary["unique_sources"] == 1
    assert summary["acquisition_locations"] == 2
    assert summary["event_counts"] == {"discovered": 1, "duplicate": 2}
    assert summary["current_state_counts"] == {"discovered": 1}
    assert reopened.acquisitions(record.fingerprint) == (str(tmp_path / "input/first.jsonl"), str(tmp_path / "input/renamed.jsonl"))


def test_imported_requires_gateway_evidence_and_preserves_event_history(tmp_path):
    ledger = HistoryMigrationLedger(tmp_path / "private/ledger.sqlite3")
    record = source(tmp_path)
    ledger.discover(record)
    with pytest.raises(HistoryLedgerError, match="gateway_evidence_required"):
        ledger.record_outcome(record.fingerprint, "imported")
    ledger.record_outcome(record.fingerprint, "parsed")
    ledger.record_outcome(record.fingerprint, "imported", evidence={"authority":"cognivault", "record_id":"source-synthetic", "receipt_digest":"a"*64})
    summary = ledger.public_summary()
    assert summary["event_counts"] == {"discovered": 1, "imported": 1, "parsed": 1}
    assert summary["current_state_counts"] == {"imported": 1}


def test_unregistered_source_or_private_error_text_is_rejected(tmp_path):
    ledger = HistoryMigrationLedger(tmp_path / "private/ledger.sqlite3")
    with pytest.raises(HistoryLedgerError, match="unknown_source"):
        ledger.record_outcome("a"*64, "parsed")
    record = source(tmp_path)
    ledger.discover(record)
    with pytest.raises(HistoryLedgerError, match="invalid_outcome"):
        ledger.record_outcome(record.fingerprint, "error", error_code=str(tmp_path))


def test_public_summary_and_repr_contain_no_private_values(tmp_path):
    ledger = HistoryMigrationLedger(tmp_path / "private/ledger.sqlite3")
    record = source(tmp_path)
    ledger.discover(record)
    public = json.dumps(ledger.public_summary()) + repr(ledger)
    for private in [str(tmp_path), record.fingerprint, "synthetic-private-text", "first.jsonl"]:
        assert private not in public
    assert ledger.public_summary()["by_source_type"] == {"workbuddy": 1}


def test_ledger_rejects_overlap_with_input_before_creation(tmp_path):
    source_root = tmp_path / "input"
    source_root.mkdir()
    with pytest.raises(HistoryLedgerError, match="unsafe_ledger"):
        HistoryMigrationLedger(source_root / "ledger.sqlite3", protected_paths=(source_root,))
    assert not (source_root / "ledger.sqlite3").exists()


def test_catalog_accounts_for_unavailable_categories_without_fabricated_sources(tmp_path):
    ledger = HistoryMigrationLedger(tmp_path / "private/ledger.sqlite3")
    ledger.record_catalog("chatgpt_export", source_type="chatgpt", state="waiting_for_export")
    ledger.record_catalog("production_sources", source_type="v1", state="gateway_unavailable")
    ledger.record_catalog("chatgpt_export", source_type="chatgpt", state="waiting_for_export")
    summary = HistoryMigrationLedger(tmp_path / "private/ledger.sqlite3").public_summary()
    assert summary["unique_sources"] == 0
    assert summary["catalog_state_counts"] == {"gateway_unavailable": 1, "waiting_for_export": 1}


def test_pending_export_is_recorded_without_blocking_or_inventing_a_source(tmp_path):
    path = tmp_path / "private/ledger.sqlite3"
    ledger = HistoryMigrationLedger(path)
    ledger.record_catalog("chatgpt_export", source_type="chatgpt", state="acquisition_pending")
    summary = HistoryMigrationLedger(path).public_summary()
    assert summary["catalog_state_counts"] == {"acquisition_pending": 1}
    assert summary["unique_sources"] == 0


def test_gateway_document_uri_is_a_valid_persistent_destination(tmp_path):
    ledger = HistoryMigrationLedger(tmp_path / "private/ledger.sqlite3")
    record = source(tmp_path)
    ledger.discover(record)
    ledger.record_outcome(record.fingerprint, "imported", evidence={
        "authority": "cognivault", "record_id": "document://sha256/" + "a" * 64,
        "receipt_digest": "b" * 64,
    })
    assert ledger.public_summary()["current_state_counts"] == {"imported": 1}
    with pytest.raises(HistoryLedgerError, match="gateway_evidence_required"):
        ledger.record_outcome(record.fingerprint, "reused", evidence={
            "authority": "cognivault", "record_id": "document://sha256/../../private",
            "receipt_digest": "b" * 64,
        })


@pytest.mark.parametrize("state", ["imported", "reused"])
def test_new_outcomes_require_canonical_authority_without_appending_legacy_event(tmp_path, state):
    path = tmp_path / "private/ledger.sqlite3"
    ledger = HistoryMigrationLedger(path)
    record = source(tmp_path)
    ledger.discover(record)
    evidence = {"authority": "study_system", "record_id": "source-synthetic", "receipt_digest": "a" * 64}
    with pytest.raises(HistoryLedgerError, match="gateway_evidence_required"):
        ledger.record_outcome(record.fingerprint, state, evidence=evidence)
    assert ledger.public_summary()["event_counts"] == {"discovered": 1}
    ledger.record_outcome(record.fingerprint, state, evidence={**evidence, "authority": "cognivault"})
    reopened = HistoryMigrationLedger(path)
    assert reopened.public_summary()["current_state_counts"] == {state: 1}
    with sqlite3.connect(path) as connection:
        stored = json.loads(connection.execute("SELECT evidence FROM events WHERE state=?", (state,)).fetchone()[0])
    assert stored == {"authority": "cognivault", "record_id": "source-synthetic", "receipt_digest": "a" * 64}


@pytest.mark.parametrize("state", ["imported", "reused"])
def test_existing_legacy_authority_is_readable_without_rewriting_payload_or_identity(tmp_path, state):
    # Only this disposable synthetic fixture uses SQL; never a production ledger.
    path = tmp_path / "private/ledger.sqlite3"
    ledger = HistoryMigrationLedger(path)
    record = source(tmp_path)
    ledger.discover(record)
    evidence = '{ "authority": "study_system", "record_id": "source-synthetic", "receipt_digest": "' + "a" * 64 + '" }'
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO events(fingerprint,state,recorded_at,evidence) VALUES (?,?,?,?)",
                           (record.fingerprint, state, "2026-09-30T00:00:00Z", evidence))
        connection.commit()
        before = list(connection.iterdump())
    reopened = HistoryMigrationLedger(path)
    assert reopened.public_summary()["current_state_counts"] == {state: 1}
    with sqlite3.connect(path) as connection:
        assert list(connection.iterdump()) == before
        assert connection.execute("SELECT evidence FROM events WHERE state=?", (state,)).fetchone()[0] == evidence


@pytest.mark.parametrize("evidence", [
    {"authority": "unknown", "record_id": "source-synthetic", "receipt_digest": "a" * 64},
    {"authority": "study_system", "record_id": "source-synthetic", "receipt_digest": "invalid"},
])
def test_stored_outcome_evidence_must_still_be_valid(tmp_path, evidence):
    path = tmp_path / "private/ledger.sqlite3"
    ledger = HistoryMigrationLedger(path)
    record = source(tmp_path)
    ledger.discover(record)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO events(fingerprint,state,recorded_at,evidence) VALUES (?,?,?,?)",
                           (record.fingerprint, "imported", "2026-09-30T00:00:00Z", json.dumps(evidence)))
    with pytest.raises(HistoryLedgerError, match="invalid_ledger"):
        HistoryMigrationLedger(path).public_summary()


def test_discover_rerun_does_not_inflate_exact_duplicate_copy_count(tmp_path):
    ledger = HistoryMigrationLedger(tmp_path / "private/ledger.sqlite3")
    record = source(tmp_path)
    for _ in range(3):
        ledger.discover(record)
    ledger.discover(source(tmp_path, "copy.jsonl"))
    assert ledger.public_summary()["exact_duplicate_copies"] == 1
    assert ledger.public_summary()["rerun_discoveries"] == 2


def test_unknown_schema_is_rejected_before_altering_existing_ledger(tmp_path):
    import sqlite3
    path = tmp_path / "private/ledger.sqlite3"
    path.parent.mkdir()
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE ledger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        c.execute("INSERT INTO ledger_meta VALUES ('schema_version','unsupported')")
    with pytest.raises(HistoryLedgerError, match="invalid_schema"):
        HistoryMigrationLedger(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("ledger_meta",)]


def test_ledger_rejects_other_git_checkout(tmp_path):
    checkout = tmp_path / "another-repository"
    checkout.mkdir()
    (checkout / ".git").mkdir()
    with pytest.raises(HistoryLedgerError, match="unsafe_ledger"):
        HistoryMigrationLedger(checkout / "private/ledger.sqlite3")
    assert not (checkout / "private").exists()


def test_private_custom_error_class_cannot_enter_public_summary(tmp_path):
    ledger = HistoryMigrationLedger(tmp_path / "private/ledger.sqlite3")
    with pytest.raises(HistoryLedgerError, match="invalid_outcome"):
        ledger.record_failure("codex_sessions", str(tmp_path / "private-name.jsonl"), "synthetic_private_title")


def test_failure_history_is_append_only_and_success_resolves_current_error(tmp_path):
    ledger = HistoryMigrationLedger(tmp_path / "private/ledger.sqlite3")
    location = str(tmp_path / "input/source.jsonl")
    ledger.record_failure("codex_sessions", location, "source_changed")
    ledger.record_failure("codex_sessions", location, "unreadable_input")
    assert ledger.public_summary()["acquisition_error_counts"] == {"unreadable_input": 1}
    assert ledger.public_summary()["historical_failure_counts"] == {"source_changed": 1, "unreadable_input": 1}
    ledger.resolve_failure("codex_sessions", location)
    ledger.resolve_failure("codex_sessions", location)
    summary = ledger.public_summary()
    assert summary["acquisition_error_counts"] == {}
    assert summary["historical_failure_counts"] == {"source_changed": 1, "unreadable_input": 1}
    assert summary["resolved_acquisition_errors"] == 1


def test_fake_versioned_ledger_with_unrelated_schema_is_refused(tmp_path):
    import sqlite3
    path = tmp_path / "private/ledger.sqlite3"
    path.parent.mkdir()
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE ledger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        c.execute("INSERT INTO ledger_meta VALUES ('schema_version','1')")
        c.execute("CREATE TABLE unrelated (body TEXT)")
    with pytest.raises(HistoryLedgerError, match="invalid_schema"):
        HistoryMigrationLedger(path)


def test_public_summary_refuses_private_label_in_tampered_record(tmp_path):
    import sqlite3
    path = tmp_path / "private/ledger.sqlite3"
    ledger = HistoryMigrationLedger(path)
    record = source(tmp_path)
    ledger.discover(record)
    with sqlite3.connect(path) as c:
        payload = json.loads(c.execute("SELECT payload FROM sources").fetchone()[0])
        payload["source_type"] = "synthetic_private_label"
        c.execute("UPDATE sources SET payload=?", (json.dumps(payload),))
    with pytest.raises(HistoryLedgerError, match="invalid_ledger"):
        ledger.public_summary()


def test_concurrent_v1_upgrade_seeds_existing_failure_only_once(tmp_path, monkeypatch):
    import sqlite3
    from contextlib import closing
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    path = tmp_path / "private/ledger.sqlite3"
    HistoryMigrationLedger(path)
    with closing(sqlite3.connect(path)) as c:
        c.execute("DROP TABLE failure_events")
        c.execute("UPDATE ledger_meta SET value='1'")
        c.execute("INSERT INTO acquisition_errors VALUES (?,?,?,?)", ("codex_sessions", str(tmp_path / "input.jsonl"), "2025-01-01T00:00:00Z", "source_changed"))
        c.commit()
    connect = HistoryMigrationLedger._connect
    gate = Barrier(2)

    class LockContender:
        # Both constructors contend for the real SQLite write lock together.
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, *args):
            if sql == "BEGIN IMMEDIATE":
                gate.wait(timeout=10)
            return self.connection.execute(sql, *args)

        def close(self):
            self.connection.close()

    monkeypatch.setattr(HistoryMigrationLedger, "_connect", lambda self: LockContender(connect(self)))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: HistoryMigrationLedger(path), range(2)))
    with closing(sqlite3.connect(path)) as c:
        assert c.execute("SELECT state,error_code FROM failure_events").fetchall() == [("failed", "source_changed")]
