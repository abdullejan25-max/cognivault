"""Offline inventory CLI tests against explicit synthetic source descriptors."""

import json

from cognivault.migration.history_ledger import HistoryMigrationLedger


def test_acquisition_pending_descriptor_is_non_error_without_input_scan(tmp_path):
    from cognivault.migration.history_acquire import acquire
    path = tmp_path / "private/manifest.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"schema_version": 1, "protected_paths": [], "sources": [
        {"scope": "chatgpt_export", "source_type": "chatgpt", "state": "acquisition_pending"},
    ]}), encoding="utf-8")
    result = acquire(path, tmp_path / "private/ledger.sqlite3")
    assert result["run_errors"] == {}
    assert result["unique_sources"] == 0
    assert result["catalog_state_counts"] == {"acquisition_pending": 1}


def test_private_descriptor_runs_inventory_twice_without_importing(tmp_path, capsys):
    from cognivault.migration.history_acquire import main
    root = tmp_path / "input"
    root.mkdir()
    (root / "source.jsonl").write_text('{"role":"user","content":"synthetic"}\n', encoding="utf-8")
    manifest = tmp_path / "private/manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"schema_version": 1, "protected_paths": [], "sources": [
        {"scope": "codex_sessions", "source_type": "codex", "root": str(root),
         "suffixes": [".jsonl"], "acquisition_method": "bounded_local_inventory"},
        {"scope": "chatgpt_export", "source_type": "chatgpt", "state": "waiting_for_export"},
    ]}), encoding="utf-8")
    ledger_path = tmp_path / "private/ledger.sqlite3"
    args = ["--manifest", str(manifest), "--ledger", str(ledger_path)]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["unique_sources"] == 1
    assert first["current_state_counts"] == {"parsed": 1}
    assert first["run_state"] == "INVENTORY_ONLY"
    assert main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["unique_sources"] == 1
    assert second["rerun_discoveries"] == 1
    assert str(tmp_path) not in json.dumps(second)
    assert "synthetic" not in json.dumps(second)
    assert HistoryMigrationLedger(ledger_path).public_summary()["gateway_current_state"] == "unverified"


def test_command_reports_selected_input_error_without_printing_location(tmp_path, capsys):
    from cognivault.migration.history_acquire import main
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "protected_paths": [], "sources": [
        {"scope": "codex_sessions", "source_type": "codex", "root": str(tmp_path / "missing"),
         "suffixes": [".jsonl"], "acquisition_method": "bounded_local_inventory"},
    ]}), encoding="utf-8")
    assert main(["--manifest", str(manifest), "--ledger", str(tmp_path / "private/ledger.sqlite3")]) == 1
    output = capsys.readouterr()
    assert str(tmp_path) not in output.out + output.err
    summary = json.loads(output.out)
    assert summary["run_errors"] == {"unsafe_input": 1}
    assert summary["catalog_state_counts"] == {"review_required": 1}


def test_rerun_recovers_discovered_source_missing_its_disposition(tmp_path):
    from cognivault.migration.history_acquire import acquire
    from cognivault.migration.history_inventory import inspect_source
    root = tmp_path / "input"
    root.mkdir()
    file = root / "source.jsonl"
    file.write_bytes(b"{}\n")
    manifest = tmp_path / "private/manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"schema_version":1,"protected_paths":[],"sources":[
        {"scope":"codex_sessions","source_type":"codex","root":str(root),
         "suffixes":[".jsonl"],"acquisition_method":"bounded_local_inventory"}
    ]}), encoding="utf-8")
    ledger_path = tmp_path / "private/ledger.sqlite3"
    record = inspect_source(file, source_root=root, source_type="codex", acquisition_method="bounded_local_inventory")
    ledger = HistoryMigrationLedger(ledger_path)
    ledger.discover(record)  # A previous process stopped before recording parsed.
    assert acquire(manifest, ledger_path)["current_state_counts"] == {"parsed": 1}
