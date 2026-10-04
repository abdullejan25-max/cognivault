"""Private, metadata-only registry for AI and Agent conversation sources.

This registry records source coverage and export/import state. It deliberately
does not store message text, credentials, or inferred message-level metadata.
"""

from __future__ import annotations

from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from .manifest import validate_private_journal_path


_SOURCE_ID = re.compile(r"source-[0-9a-f]{32}\Z")
_OPAQUE_ID = re.compile(r"opaque-[0-9a-f]{32}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SYSTEMS = frozenset({"chatgpt", "gemini", "codex", "workbuddy", "hermes", "obsidian", "legacy"})
_SOURCE_TYPES = frozenset({
    "official_export", "local_history", "agent_history", "legacy_archive", "app_installation",
})
_ACQUISITION_METHODS = frozenset({
    "official_export", "bounded_local_inventory", "repo_documentation", "gateway_read_only",
})
_EXPORT_STATUSES = frozenset({
    "not_started", "waiting_for_user", "requested", "processing", "available", "unavailable",
    "not_applicable",
})
_IMPORT_STATUSES = frozenset({
    "not_started", "blocked", "archive_only", "normalized", "imported", "partial", "not_applicable",
})
_RAW_FORMATS = frozenset({"json", "jsonl", "html", "markdown", "sqlite", "zip", "plain_text", "unknown", "none"})
_COVERAGE_NOTES = frozenset({
    "official_export_pending",
    "official_verification_required",
    "history_target_unconfigured",
    "source_identity_unverified",
    "role_or_boundary_unverified",
    "attachment_relation_unverified",
    "normalization_not_started",
    "agent_storage_unverified",
    "raw_archive_only",
    "app_installed_data_unverified",
    "legacy_annotation_not_raw_history",
})
_OFFICIAL_LOCATORS = frozenset({"openai_privacy_portal", "google_takeout"})
_MAX_RECORDS = 2_048
_MAX_RECORD_BYTES = 8_192
_MAX_DATABASE_BYTES = 32 * 1024 * 1024
_MAX_COUNT = 1_000_000_000


@dataclass(frozen=True)
class ConversationSourceRecord:
    """Validated source metadata; private identifiers and locators stay local."""

    source_id: str = field(repr=False)
    source_system: str
    source_type: str
    acquisition_method: str
    discovered: bool
    accessible: bool
    export_status: str
    import_status: str
    raw_format: str | None
    stable_identity: str | None = field(repr=False)
    conversation_count: int | None
    message_count: int | None
    earliest_known_time: str | None
    latest_known_time: str | None
    source_hash: str | None = field(repr=False)
    manifest_hash: str | None = field(repr=False)
    imported_count: int
    deduplicated_count: int
    unresolved_count: int
    coverage_notes: tuple[str, ...]
    private_locator: str | None = field(repr=False)

    def __post_init__(self) -> None:
        _validate_record(self)


def _is_count(value: object, *, optional: bool = False) -> bool:
    return (optional and value is None) or (type(value) is int and 0 <= value <= _MAX_COUNT)


def _is_utc_timestamp(value: str | None) -> bool:
    if value is None:
        return True
    if type(value) is not str or not value or len(value) > 40 or any(ord(ch) < 32 for ch in value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _valid_private_locator(value: str | None) -> bool:
    if value is None:
        return True
    if type(value) is not str or not value or len(value) > 2_048 \
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return False
    if value.startswith("official:"):
        return value.removeprefix("official:") in _OFFICIAL_LOCATORS
    if value.startswith("path:"):
        raw_path = value.removeprefix("path:")
        return bool(raw_path) and Path(raw_path).is_absolute()
    return False


def _restrict_private_permissions(path: Path, *, directory: bool) -> None:
    """Make POSIX registry storage owner-only; Windows inherits the user-state ACL."""
    if os.name == "nt":
        return
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    mode = 0o700 if directory else 0o600
    try:
        info = path.stat()
        if not expected(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError
        os.chmod(path, mode)
        if path.stat().st_mode & 0o077:
            raise ValueError
    except OSError:
        raise ValueError("Invalid private conversation registry") from None


def _require_private_permissions(path: Path, *, directory: bool) -> None:
    """Reject an existing POSIX registry directory unless it is already private."""
    if os.name == "nt":
        return
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    try:
        info = path.stat()
        if not expected(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError
    except OSError:
        raise ValueError("Invalid private conversation registry") from None


def _validate_windows_state_root(path: Path) -> None:
    """On Windows, confine storage to the configured per-user state directory."""
    if os.name != "nt":
        return
    roots = (os.environ.get("LOCALAPPDATA"), os.environ.get("XDG_STATE_HOME"))
    for root in roots:
        if not root or not Path(root).is_absolute():
            continue
        try:
            if path.is_relative_to(Path(root).resolve(strict=False)):
                return
        except OSError:
            continue
    raise ValueError("Invalid private conversation registry")


def _validate_record(record: ConversationSourceRecord) -> None:
    valid = (
        type(record) is ConversationSourceRecord
        and type(record.source_id) is str and _SOURCE_ID.fullmatch(record.source_id) is not None
        and type(record.source_system) is str and record.source_system in _SYSTEMS
        and type(record.source_type) is str and record.source_type in _SOURCE_TYPES
        and type(record.acquisition_method) is str and record.acquisition_method in _ACQUISITION_METHODS
        and type(record.discovered) is bool and type(record.accessible) is bool
        and type(record.export_status) is str and record.export_status in _EXPORT_STATUSES
        and type(record.import_status) is str and record.import_status in _IMPORT_STATUSES
        and (record.raw_format is None or
             (type(record.raw_format) is str and record.raw_format in _RAW_FORMATS))
        and (record.stable_identity is None or
             (type(record.stable_identity) is str and _OPAQUE_ID.fullmatch(record.stable_identity) is not None))
        and _is_count(record.conversation_count, optional=True)
        and _is_count(record.message_count, optional=True)
        and _is_utc_timestamp(record.earliest_known_time)
        and _is_utc_timestamp(record.latest_known_time)
        and (record.earliest_known_time is None or record.latest_known_time is None or
             datetime.fromisoformat(record.earliest_known_time.replace("Z", "+00:00")) <=
             datetime.fromisoformat(record.latest_known_time.replace("Z", "+00:00")))
        and (record.source_hash is None or
             (type(record.source_hash) is str and _SHA256.fullmatch(record.source_hash) is not None))
        and (record.manifest_hash is None or
             (type(record.manifest_hash) is str and _SHA256.fullmatch(record.manifest_hash) is not None))
        and _is_count(record.imported_count)
        and _is_count(record.deduplicated_count)
        and _is_count(record.unresolved_count)
        and type(record.coverage_notes) is tuple
        and len(record.coverage_notes) <= len(_COVERAGE_NOTES)
        and all(type(note) is str and note in _COVERAGE_NOTES for note in record.coverage_notes)
        and len(set(record.coverage_notes)) == len(record.coverage_notes)
        and _valid_private_locator(record.private_locator)
    )
    if not valid:
        raise ValueError("Invalid conversation source record")


def _record_payload(record: ConversationSourceRecord) -> str:
    payload = {}
    for item in fields(record):
        value = getattr(record, item.name)
        payload[item.name] = list(value) if item.name == "coverage_notes" else value
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_RECORD_BYTES:
        raise ValueError("Invalid conversation source record")
    return encoded


def _record_from_payload(payload: str) -> ConversationSourceRecord:
    if type(payload) is not str or len(payload.encode("utf-8")) > _MAX_RECORD_BYTES:
        raise RuntimeError("Private conversation source registry is invalid")
    try:
        values = json.loads(payload)
        if type(values) is not dict:
            raise ValueError
        if "coverage_notes" in values:
            values["coverage_notes"] = tuple(values["coverage_notes"])
        return ConversationSourceRecord(**values)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("Private conversation source registry is invalid") from None


def default_conversation_registry_path() -> Path:
    """Return a private registry path under the configured per-user state root."""
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    if not root or not Path(root).is_absolute():
        raise RuntimeError("Private local registry location is not configured")
    return (Path(root) / "ChatGPTStudySystemV2" / "migration" /
            "conversation-source-registry" / "sources.sqlite3")


class ConversationSourceRegistry:
    """SQLite-backed private source registry with transactional upserts."""

    def __init__(self, path: Path) -> None:
        try:
            candidate = validate_private_journal_path(Path(path))
            _validate_windows_state_root(candidate)
            if os.name == "nt":
                candidate.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            else:
                candidate.parent.parent.mkdir(parents=True, exist_ok=True)
                try:
                    candidate.parent.mkdir(mode=0o700)
                except FileExistsError:
                    _require_private_permissions(candidate.parent, directory=True)
                else:
                    _restrict_private_permissions(candidate.parent, directory=True)
            self.path = validate_private_journal_path(candidate)
            if not self.path.parent.is_dir():
                raise ValueError
            if self.path.exists():
                _restrict_private_permissions(self.path, directory=False)
            self._initialize()
            _restrict_private_permissions(self.path, directory=False)
        except (OSError, ValueError, sqlite3.Error):
            raise ValueError("Invalid private conversation registry") from None

    def _connect(self) -> sqlite3.Connection:
        validate_private_journal_path(self.path)
        connection: sqlite3.Connection | None = None
        try:
            if self.path.exists() and self.path.stat().st_size > _MAX_DATABASE_BYTES:
                raise ValueError
            connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute(f"PRAGMA max_page_count={_MAX_DATABASE_BYTES // 4096}")
            validate_private_journal_path(self.path)
            return connection
        except (OSError, sqlite3.Error, ValueError):
            if connection is not None:
                connection.close()
            raise RuntimeError("Private conversation source registry is unavailable") from None

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS registry_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS source_records (source_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
                version = connection.execute(
                    "SELECT value FROM registry_meta WHERE key='schema_version'"
                ).fetchone()
                if version is None:
                    connection.execute(
                        "INSERT INTO registry_meta(key, value) VALUES ('schema_version', '1')"
                    )
                elif version[0] != "1":
                    raise sqlite3.DatabaseError
                connection.execute("COMMIT")
            except sqlite3.Error:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise RuntimeError("Private conversation source registry is invalid") from None

    def list_sources(self) -> tuple[ConversationSourceRecord, ...]:
        with closing(self._connect()) as connection:
            try:
                rows = connection.execute(
                    "SELECT payload FROM source_records ORDER BY source_id LIMIT ?",
                    (_MAX_RECORDS + 1,),
                ).fetchall()
                if len(rows) > _MAX_RECORDS:
                    raise RuntimeError
                return tuple(_record_from_payload(row[0]) for row in rows)
            except (sqlite3.Error, RuntimeError):
                raise RuntimeError("Private conversation source registry is invalid") from None

    def upsert(self, record: ConversationSourceRecord) -> None:
        _validate_record(record)
        payload = _record_payload(record)
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                count = connection.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]
                exists = connection.execute(
                    "SELECT 1 FROM source_records WHERE source_id=?", (record.source_id,),
                ).fetchone() is not None
                if count >= _MAX_RECORDS and not exists:
                    raise RuntimeError
                connection.execute(
                    "INSERT INTO source_records(source_id, payload) VALUES (?, ?) "
                    "ON CONFLICT(source_id) DO UPDATE SET payload=excluded.payload",
                    (record.source_id, payload),
                )
                connection.execute("COMMIT")
            except (sqlite3.Error, RuntimeError):
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise RuntimeError("Private conversation source registry could not be updated") from None

    def public_summary(self) -> dict[str, object]:
        """Return aggregates; per-source totals may overlap across sources."""
        records = self.list_sources()
        export_counts = Counter(record.export_status for record in records)
        import_counts = Counter(record.import_status for record in records)
        known_conversations = [record.conversation_count for record in records
                               if record.conversation_count is not None]
        known_messages = [record.message_count for record in records
                          if record.message_count is not None]
        return {
            "source_count": len(records),
            "discovered_count": sum(record.discovered for record in records),
            "accessible_count": sum(record.accessible for record in records),
            "export_status_counts": {
                status: export_counts[status] for status in sorted(_EXPORT_STATUSES)
                if export_counts[status]
            },
            "import_status_counts": {
                status: import_counts[status] for status in sorted(_IMPORT_STATUSES)
                if import_counts[status]
            },
            "source_reported_conversation_count_sum": sum(known_conversations),
            "conversation_count_known_sources": len(known_conversations),
            "source_reported_message_count_sum": sum(known_messages),
            "message_count_known_sources": len(known_messages),
            "source_reported_imported_item_count_sum": sum(record.imported_count for record in records),
            "source_reported_deduplicated_item_count_sum": sum(record.deduplicated_count for record in records),
            "source_reported_unresolved_item_count_sum": sum(record.unresolved_count for record in records),
        }
