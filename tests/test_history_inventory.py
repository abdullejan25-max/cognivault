"""P13 acquisition probes use hand-authored synthetic bytes only."""

import hashlib
from pathlib import Path

import pytest

from cognivault.migration import history_inventory as inventory


def inspect(root, name="input.jsonl", raw=b'{"role":"user","timestamp":"2025-01-01T00:00:00Z","content":"synthetic"}\n', **kwargs):
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_bytes(raw)
    return inventory.inspect_source(path, source_root=root, source_type="codex",
                                    acquisition_method="bounded_local_inventory", **kwargs)


def test_identity_uses_bytes_not_path_and_source_is_unchanged(tmp_path):
    raw = b'{"role":"user","content":"synthetic"}\r\n'
    a = inspect(tmp_path, "first.jsonl", raw)
    b = inspect(tmp_path, "renamed.jsonl", raw)
    c = inspect(tmp_path, "changed.jsonl", raw + b'{}\n')
    assert a.content_digest == b.content_digest == hashlib.sha256(raw).hexdigest()
    assert a.fingerprint == b.fingerprint
    assert c.fingerprint != a.fingerprint
    assert (tmp_path / "first.jsonl").read_bytes() == raw
    assert str(tmp_path) not in repr(a)
    assert "synthetic" not in repr(a)


def test_probe_missing_time_does_not_use_file_mtime(tmp_path):
    result = inspect(tmp_path, raw=b'{"role":"user","content":"synthetic"}\n')
    assert result.record_estimate == 1
    assert result.earliest_at is None and result.latest_at is None
    assert result.timestamp_quality == "unknown"
    assert result.role_quality == "explicit"
    assert result.boundary_quality == "unknown"


def test_probe_counts_malformed_and_unknown_roles_without_dropping_raw(tmp_path):
    result = inspect(tmp_path, raw=b'{"role":"guess-me","timestamp":"bad"}\nNOT JSON\n{"role":"assistant"}\n')
    assert result.record_estimate == 2
    assert result.malformed_records == 1
    assert result.role_quality == "partial"
    assert result.timestamp_quality == "unknown"
    assert result.import_readiness == "source_only_review"


def test_probe_explicit_session_boundary_is_only_source_metadata(tmp_path):
    result = inspect(tmp_path, raw=b'{"type":"session_meta","payload":{"id":"synthetic-session"}}\n{"role":"user","timestamp":"2025-01-01T00:00:00Z"}\n')
    assert result.boundary_quality == "explicit"
    assert result.record_estimate == 2
    assert result.earliest_at == "2025-01-01T00:00:00Z"
    assert result.timestamp_quality == "partial"
    assert result.message_estimate is None


def test_probe_hermes_export_counts_only_explicit_messages(tmp_path):
    result = inspect(tmp_path, name="sessions.jsonl", raw=b'{"session_id":"synthetic","messages":[{"role":"user","timestamp":1735689600,"content":"synthetic"},{"role":"assistant","content":"synthetic"}]}\n')
    assert result.message_estimate == 2
    assert result.boundary_quality == "explicit"
    assert result.timestamp_quality == "partial"


def test_unknown_format_is_retained_not_parsed(tmp_path):
    result = inspect(tmp_path, name="source.bin", raw=b"unrecognized synthetic source")
    assert result.source_format == "unknown"
    assert result.parser_availability == "unsupported"
    assert result.import_readiness == "source_only_review"
    assert result.record_estimate is None


@pytest.mark.parametrize("name", ["credentials.json", "auth.json", "tokens.jsonl", "passwords.txt"])
def test_credential_inputs_are_rejected_before_read(tmp_path, name):
    with pytest.raises(inventory.HistoryInventoryError, match="excluded_input"):
        inspect(tmp_path, name=name)


def test_explicit_limits_prevent_unbounded_input(tmp_path):
    with pytest.raises(inventory.HistoryInventoryError, match="file_limit"):
        inspect(tmp_path, limits=inventory.InventoryLimits(max_file_bytes=4))


def test_inventory_counts_every_selected_file_and_excludes_credentials(tmp_path):
    for name in ["a.jsonl", "b.jsonl", "credentials.jsonl", "unselected.txt"]:
        (tmp_path / name).write_bytes(b"{}\n")
    files = inventory.inventory_files(tmp_path, suffixes=(".jsonl",))
    assert [p.name for p in files] == ["a.jsonl", "b.jsonl"]
    with pytest.raises(inventory.HistoryInventoryError, match="entry_limit"):
        inventory.inventory_files(tmp_path, suffixes=(".jsonl",), limits=inventory.InventoryLimits(max_entries=1))


def test_hardlink_input_is_rejected(tmp_path):
    import os
    source = tmp_path / "source.jsonl"
    source.write_bytes(b"{}\n")
    os.link(source, tmp_path / "copy.jsonl")
    with pytest.raises(inventory.HistoryInventoryError, match="unsafe_input"):
        inventory.inspect_source(source, source_root=tmp_path, source_type="codex", acquisition_method="bounded_local_inventory")


def test_protected_target_is_refused_before_input_open(tmp_path):
    target = tmp_path / "private-target"
    target.mkdir()
    (target / "input.jsonl").write_bytes(b"{}\n")
    with pytest.raises(inventory.HistoryInventoryError, match="protected_target"):
        inventory.inspect_source(target / "input.jsonl", source_root=target, source_type="codex",
                                 acquisition_method="bounded_local_inventory", protected_paths=(target,))


def test_inventory_prunes_credential_directory_without_descending(tmp_path):
    # The max_entries would fail if the credentials tree were traversed.
    (tmp_path / "credentials").mkdir()
    (tmp_path / "credentials/private.jsonl").write_bytes(b"{}\n")
    (tmp_path / "allowed.jsonl").write_bytes(b"{}\n")
    result = inventory.inventory_files(tmp_path, suffixes=(".jsonl",), limits=inventory.InventoryLimits(max_entries=1))
    assert [p.name for p in result] == ["allowed.jsonl"]


def test_invalid_record_cannot_inject_private_labels_into_public_ledger(tmp_path):
    from dataclasses import replace
    record = inspect(tmp_path)
    with pytest.raises(inventory.HistoryInventoryError, match="invalid_record"):
        replace(record, source_type="synthetic-private-title")


def test_duplicate_json_keys_are_marked_malformed_not_silently_overwritten(tmp_path):
    result = inspect(tmp_path, raw=b'{"role":"user","role":"assistant"}\n')
    assert result.malformed_records == 1
    assert result.record_estimate == 0
    assert result.import_readiness == "source_only_review"


def test_truncated_probe_does_not_claim_complete_record_count(tmp_path):
    result = inspect(tmp_path, raw=b'{}\n{}\n', limits=inventory.InventoryLimits(max_probe_bytes=4))
    assert result.probe_complete is False
    assert result.record_estimate is None
    assert result.malformed_records == 0


def test_conflicting_codex_session_boundaries_are_ambiguous(tmp_path):
    raw = b'{"type":"session_meta","payload":{"id":"synthetic-a"}}\n{"type":"session_meta","payload":{"id":"synthetic-b"}}\n'
    result = inspect(tmp_path, raw=raw)
    assert result.boundary_quality == "ambiguous"
    assert result.import_readiness == "source_only_review"


def test_runtime_metadata_is_distinct_from_conversation_evidence(tmp_path):
    result = inspect(tmp_path, raw=b'{"cwd":"synthetic-workspace"}\n', evidence_kind="session_metadata")
    assert result.evidence_kind == "session_metadata"
    assert result.message_estimate is None
    assert result.boundary_quality == "unknown"


@pytest.mark.parametrize("raw", [b'[{"role":"user"},{"role":"assistant"}]', b'[{},5]'])
def test_json_record_limit_is_not_swallowed_as_parse_error(tmp_path, raw):
    with pytest.raises(inventory.HistoryInventoryError, match="probe_limit"):
        inspect(tmp_path, name="export.json", raw=raw, limits=inventory.InventoryLimits(max_records=1))


def test_nested_message_probe_has_one_global_record_limit(tmp_path):
    raw = b'[{"messages":[{},{}]},{"messages":[{},{}]}]'
    with pytest.raises(inventory.HistoryInventoryError, match="probe_limit"):
        inspect(tmp_path, name="export.json", raw=raw, limits=inventory.InventoryLimits(max_records=2))


def test_valid_same_second_fractional_time_range_is_not_rejected(tmp_path):
    raw = b'{"timestamp":"2025-01-01T00:00:00Z"}\n{"timestamp":"2025-01-01T00:00:00.100Z"}\n'
    result = inspect(tmp_path, raw=raw)
    assert result.earliest_at == "2025-01-01T00:00:00Z"
    assert result.latest_at == "2025-01-01T00:00:00.100000Z"
