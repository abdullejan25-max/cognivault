"""Synthetic legacy migration plans; never use personal records here."""

from cognivault.migration.planner import SourceRecord, plan_records


def _record(**overrides) -> SourceRecord:
    values = {
        "category": "History",
        "legacy_system": "synthetic-legacy",
        "legacy_source_type": "chat_message",
        "legacy_item_id": "message-1",
        "source_fingerprint": "a" * 64,
        "target_type": "history_item",
        "target_logical_id": None,
        "source_event_time": "2024-01-02T03:04:05Z",
        "intended_action": "import",
        "validation_state": "valid",
        "requires_backend": "history",
        "reason_code": None,
        "source_size_bytes": 12,
    }
    values.update(overrides)
    return SourceRecord(**values)


def test_dry_run_does_not_mutate_existing_target_fingerprints() -> None:
    existing = {("history_item", "z" * 64)}
    before = existing.copy()
    planned = plan_records([_record()], existing_targets=existing,
                           target_health={"history": "ready"})
    assert existing == before
    assert planned[0].action == "import"
    assert planned[0].status == "planned"


def test_unconfigured_history_target_blocks_planned_import() -> None:
    planned = plan_records([_record()], existing_targets=set(),
                           target_health={"history": "not_configured"})
    item = planned[0]
    assert item.action == "import"
    assert item.status == "unresolved"
    assert item.error_code == "history_target_not_configured"
    assert item.validation_state == "unresolved"


def test_same_content_at_different_legacy_ids_deduplicates_to_existing_target() -> None:
    existing = {("asset", "a" * 64)}
    planned = plan_records([
        _record(category="Assets", legacy_source_type="image", legacy_item_id="path-a",
                source_fingerprint="a" * 64, target_type="asset", requires_backend="assets"),
        _record(category="Assets", legacy_source_type="image", legacy_item_id="path-b",
                source_fingerprint="a" * 64, target_type="asset", requires_backend="assets"),
    ], existing_targets=existing, target_health={"assets": "ready"})
    assert [item.action for item in planned] == ["skip", "skip"]
    assert {item.dedup_decision for item in planned} == {"content_hash"}
    assert all(item.reason_code == "existing_target_match" for item in planned)


def test_duplicate_legacy_blob_paths_share_one_planned_asset_import() -> None:
    planned = plan_records([
        _record(category="Assets", legacy_source_type="image", legacy_item_id="path-a",
                source_fingerprint="a" * 64, target_type="asset", requires_backend="assets"),
        _record(category="Assets", legacy_source_type="image", legacy_item_id="path-b",
                source_fingerprint="a" * 64, target_type="asset", requires_backend="assets"),
    ], existing_targets=set(), target_health={"assets": "ready"})
    assert [item.action for item in planned] == ["import", "skip"]
    assert planned[1].dedup_decision == "content_hash"
    assert planned[1].reason_code == "duplicate_legacy_content"


def test_archived_asset_already_present_in_target_is_reported_as_deduplicated() -> None:
    planned = plan_records([
        _record(category="Assets", legacy_source_type="legacy_wrong_answer_image",
                source_fingerprint="a" * 64, target_type="asset", intended_action="archive",
                requires_backend=None, reason_code="unpaired_evidence"),
    ], existing_targets={("asset", "a" * 64)}, target_health={})[0]
    assert planned.action == "skip"
    assert planned.status == "skipped"
    assert planned.dedup_decision == "content_hash"
    assert planned.reason_code == "existing_target_match"


def test_conflicting_duplicate_legacy_id_is_unresolved_without_overwrite() -> None:
    planned = plan_records([
        _record(legacy_item_id="message-9", source_fingerprint="b" * 64),
        _record(legacy_item_id="message-9", source_fingerprint="c" * 64),
    ], existing_targets=set(), target_health={"history": "ready"})
    assert len(planned) == 2
    assert all(item.status == "unresolved" for item in planned)
    assert all(item.error_code == "duplicate_legacy_id_conflict" for item in planned)


def test_study_reuse_and_unknown_time_are_preserved_without_guessing() -> None:
    record = _record(category="Study", legacy_source_type="study_file",
                     target_type="study_source", source_event_time=None,
                     intended_action="reuse", requires_backend=None)
    item = plan_records([record], existing_targets=set(), target_health={})[0]
    assert item.action == "reuse"
    assert item.status == "planned"
    assert item.source_event_time is None
    assert item.reason_code == "same_authoritative_root"


def test_malformed_record_is_isolated_as_an_error() -> None:
    item = plan_records([_record(validation_state="invalid")], existing_targets=set(),
                        target_health={"history": "ready"})[0]
    assert item.action == "skip"
    assert item.status == "error"
    assert item.error_code == "invalid_record"
    assert item.validation_state == "invalid"


def test_pending_annotation_reports_both_target_and_schema_gates() -> None:
    source = _record(target_type="history_annotation", validation_state="unresolved",
                     reason_code="annotation_contract_pending")
    blocked_by_config = plan_records([source], existing_targets=set(),
                                     target_health={"history": "not_configured"})[0]
    assert blocked_by_config.error_code == "history_target_not_configured"
    assert blocked_by_config.reason_code == "annotation_contract_pending"

    blocked_by_schema = plan_records([source], existing_targets=set(),
                                     target_health={"history": "ready"})[0]
    assert blocked_by_schema.error_code == "source_schema_not_supported"


def test_unresolved_wrong_answer_relation_is_preserved_as_unresolved() -> None:
    source = _record(category="Wrong Answers", legacy_source_type="legacy_wrong_answer_note",
                     target_type="wrong_answer_source", intended_action="archive",
                     validation_state="unresolved", reason_code="legacy_record_unlinked",
                     requires_backend=None)
    item = plan_records([source], existing_targets=set(), target_health={})[0]
    assert item.action == "archive"
    assert item.status == "unresolved"
    assert item.error_code == "source_relation_unresolved"


def test_existing_asset_bytes_do_not_hide_unresolved_wrong_answer_relation() -> None:
    source = _record(category="Assets", legacy_source_type="legacy_wrong_answer_image",
                     target_type="asset", intended_action="archive",
                     validation_state="unresolved", reason_code="unpaired_evidence",
                     requires_backend=None)
    item = plan_records([source], existing_targets={("asset", "a" * 64)},
                        target_health={})[0]
    assert item.action == "archive"
    assert item.status == "unresolved"
    assert item.dedup_decision == "content_hash"
    assert item.error_code == "source_relation_unresolved"
    assert item.reason_code == "unpaired_evidence"
