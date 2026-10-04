"""Deterministic dry-run classification; this module never writes a target."""

from dataclasses import dataclass
import re
from typing import Iterable, Mapping

from .manifest import MigrationItem


_HEX_256 = re.compile(r"[0-9a-f]{64}\Z")
_ACTIONS = frozenset({"reuse", "import", "archive", "skip"})
_VALIDATION_STATES = frozenset({"valid", "invalid", "unresolved"})


@dataclass(frozen=True)
class SourceRecord:
    """Path-free facts needed to decide a migration action."""

    category: str
    legacy_system: str
    legacy_source_type: str
    legacy_item_id: str | None
    source_fingerprint: str
    target_type: str
    target_logical_id: str | None
    source_event_time: str | None
    intended_action: str
    validation_state: str
    requires_backend: str | None
    reason_code: str | None
    source_size_bytes: int | None = None


def plan_records(records: Iterable[SourceRecord], *,
                 existing_targets: set[tuple[str, str]],
                 target_health: Mapping[str, str]) -> tuple[MigrationItem, ...]:
    """Classify source records without mutating sources, targets, or input sets."""
    source_records = tuple(records)
    if type(existing_targets) is not set or any(
            type(pair) is not tuple or len(pair) != 2 for pair in existing_targets):
        raise ValueError("Invalid existing target fingerprints")
    if not isinstance(target_health, Mapping):
        raise ValueError("Invalid migration target status")

    fingerprints_by_identity: dict[tuple[str, str], set[str]] = {}
    for record in source_records:
        _validate_source_record(record)
        if record.legacy_item_id is not None:
            identity = (record.legacy_system, record.legacy_item_id)
            fingerprints_by_identity.setdefault(identity, set()).add(record.source_fingerprint)
    conflicts = {identity for identity, hashes in fingerprints_by_identity.items() if len(hashes) > 1}

    result: list[MigrationItem] = []
    seen_identities: set[tuple[str, str]] = set()
    seen_asset_hashes: set[tuple[str, str]] = set()
    for record in source_records:
        identity = ((record.legacy_system, record.legacy_item_id)
                    if record.legacy_item_id is not None else None)

        if identity in conflicts:
            result.append(_make_item(
                record, action=record.intended_action, status="unresolved",
                validation_state="unresolved", error_code="duplicate_legacy_id_conflict",
            ))
            continue
        if identity is not None and identity in seen_identities:
            result.append(_make_item(
                record, action="skip", status="skipped", dedup_decision="legacy_identity",
                reason_code="duplicate_legacy_identity",
            ))
            continue
        if identity is not None:
            seen_identities.add(identity)

        if record.validation_state == "invalid":
            result.append(_make_item(
                record, action="skip", status="error", error_code="invalid_record",
            ))
            continue

        target_fingerprint = (record.target_type, record.source_fingerprint)
        matches_existing = target_fingerprint in existing_targets
        matches_legacy = record.target_type == "asset" and target_fingerprint in seen_asset_hashes
        if record.target_type == "asset":
            seen_asset_hashes.add(target_fingerprint)

        if record.validation_state == "unresolved":
            health = (target_health.get(record.requires_backend, "not_configured")
                      if record.requires_backend is not None else "ready")
            if record.reason_code in {"legacy_record_unlinked", "unpaired_evidence"}:
                error_code = "source_relation_unresolved"
            elif health == "not_configured" and record.requires_backend == "history":
                error_code = "history_target_not_configured"
            elif health != "ready":
                error_code = "target_unavailable" if health == "unavailable" else "target_not_configured"
            elif record.reason_code == "annotation_contract_pending":
                error_code = "source_schema_not_supported"
            else:
                error_code = "incomplete_source_metadata"
            result.append(_make_item(
                record, action=record.intended_action, status="unresolved",
                dedup_decision="content_hash" if matches_existing or matches_legacy else "none",
                validation_state="unresolved", error_code=error_code,
            ))
            continue

        if matches_existing:
            result.append(_make_item(
                record, action="skip", status="skipped", dedup_decision="content_hash",
                reason_code="existing_target_match",
            ))
            continue
        if matches_legacy:
            result.append(_make_item(
                record, action="skip", status="skipped", dedup_decision="content_hash",
                reason_code="duplicate_legacy_content",
            ))
            continue

        if record.intended_action == "reuse":
            result.append(_make_item(
                record, action="reuse", status="planned", reason_code="same_authoritative_root",
            ))
            continue
        if record.intended_action == "archive":
            result.append(_make_item(
                record, action="archive", status="skipped",
                reason_code=record.reason_code or "archive_only",
            ))
            continue
        if record.intended_action == "skip":
            result.append(_make_item(
                record, action="skip", status="skipped",
                reason_code=record.reason_code or "excluded_by_policy",
            ))
            continue

        if record.requires_backend is not None:
            health = target_health.get(record.requires_backend, "not_configured")
            if health != "ready":
                error_code = ("history_target_not_configured"
                              if record.requires_backend == "history" and health == "not_configured"
                              else "target_not_configured" if health == "not_configured"
                              else "target_unavailable")
                result.append(_make_item(
                    record, action="import", status="unresolved",
                    validation_state="unresolved", error_code=error_code,
                ))
                continue
        result.append(_make_item(record, action="import", status="planned"))
    return tuple(result)


def _validate_source_record(record: SourceRecord) -> None:
    if type(record) is not SourceRecord \
            or type(record.intended_action) is not str or record.intended_action not in _ACTIONS \
            or type(record.validation_state) is not str or record.validation_state not in _VALIDATION_STATES \
            or type(record.source_fingerprint) is not str \
            or not _HEX_256.fullmatch(record.source_fingerprint):
        raise ValueError("Invalid migration source record")
    # Validate all manifest fields before the planner returns a record that a
    # journal could later accept or reject differently.
    MigrationItem(
        category=record.category,
        legacy_system=record.legacy_system,
        legacy_source_type=record.legacy_source_type,
        legacy_item_id=record.legacy_item_id,
        source_fingerprint=record.source_fingerprint,
        target_type=record.target_type,
        target_logical_id=record.target_logical_id,
        action=record.intended_action,
        status="planned",
        source_event_time=record.source_event_time,
        validation_state=record.validation_state if record.validation_state != "unresolved" else "unresolved",
        reason_code=record.reason_code,
        source_size_bytes=record.source_size_bytes,
    )


def _make_item(record: SourceRecord, *, action: str, status: str,
               dedup_decision: str = "none", validation_state: str | None = None,
               reason_code: str | None = None, error_code: str | None = None) -> MigrationItem:
    return MigrationItem(
        category=record.category,
        legacy_system=record.legacy_system,
        legacy_source_type=record.legacy_source_type,
        legacy_item_id=record.legacy_item_id,
        source_fingerprint=record.source_fingerprint,
        target_type=record.target_type,
        target_logical_id=record.target_logical_id,
        action=action,
        status=status,
        source_event_time=record.source_event_time,
        imported_at=None,
        dedup_decision=dedup_decision,
        validation_state=validation_state or record.validation_state,
        reason_code=reason_code if reason_code is not None else record.reason_code,
        error_code=error_code,
        source_size_bytes=record.source_size_bytes,
    )
