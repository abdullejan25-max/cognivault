"""Offline, bounded acquisition metadata. Never reads a configured V2 store.

Callers select explicit legacy inputs and protect all authoritative target roots.
Probes describe evidence quality; they do not construct canonical messages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re

from .inventory import InventoryError, _fingerprint_file, _validate_root
from ..adapters.documents import _path_has_reparse_point

SOURCE_TYPES = frozenset({"chatgpt", "gemini", "workbuddy", "hermes", "codex", "v1", "basic_memory", "other_agent"})
ACQUISITION_METHODS = frozenset({"official_export", "bounded_local_inventory", "legacy_local_archive"})
EVIDENCE_KINDS = frozenset({"conversation_export", "session_metadata", "runtime_session_dump", "legacy_archive", "raw_session", "unknown"})
_ROLES = frozenset({"user", "assistant", "system", "developer", "tool"})
_EXCLUDED_NAMES = re.compile(r"(?:credential|password|token|secret|(?:^|[._-])auth(?:[._-]|$))", re.I)
_EXCLUDED_DIRECTORIES = frozenset({"credentials", "security", "passwords", "browser", "browsers", "cookies"})
_FORMATS = {".json": "json", ".jsonl": "jsonl", ".md": "markdown", ".html": "html", ".zip": "zip", ".sqlite": "sqlite", ".sqlite3": "sqlite", ".db": "sqlite", ".txt": "plain_text"}
PROBE_VERSION = "p13-inventory-1"
ERROR_CODES = frozenset({"invalid_limits", "invalid_record", "entry_limit", "file_limit", "depth_limit",
                         "probe_limit", "unsafe_input", "unreadable_input", "protected_target",
                         "excluded_input", "invalid_descriptor", "invalid_manifest", "source_changed"})


class HistoryInventoryError(ValueError):
    """Path-free error classes suitable for sanitized checkpoints."""

    def __init__(self, code: str):
        if type(code) is not str or code not in ERROR_CODES:
            raise ValueError("invalid_error_code")
        super().__init__(code)


@dataclass(frozen=True)
class InventoryLimits:
    max_entries: int = 25_000
    max_file_bytes: int = 512 * 1024 * 1024
    max_total_bytes: int = 8 * 1024 * 1024 * 1024
    max_probe_bytes: int = 64 * 1024 * 1024
    max_line_bytes: int = 16 * 1024 * 1024
    max_records: int = 250_000

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in vars(self).values()):
            raise HistoryInventoryError("invalid_limits")


@dataclass(frozen=True)
class SourceInventoryRecord:
    source_type: str
    acquisition_method: str
    source_format: str
    private_location: str = field(repr=False)
    content_digest: str = field(repr=False)
    fingerprint: str = field(repr=False)
    size_bytes: int
    record_estimate: int | None
    message_estimate: int | None
    earliest_at: str | None = field(repr=False)
    latest_at: str | None = field(repr=False)
    parser_availability: str
    provenance_quality: str
    timestamp_quality: str
    role_quality: str
    boundary_quality: str
    import_readiness: str
    malformed_records: int
    probe_complete: bool
    evidence_kind: str = "unknown"
    importer_version: str = PROBE_VERSION

    def __post_init__(self):
        digest = re.compile(r"[0-9a-f]{64}\Z")
        qualities = {"unknown", "explicit", "partial", "ambiguous"}
        counts = (self.size_bytes, self.malformed_records)
        optional_counts = (self.record_estimate, self.message_estimate)
        timestamps = (self.earliest_at, self.latest_at)
        if self.source_type not in SOURCE_TYPES or self.acquisition_method not in ACQUISITION_METHODS \
                or self.evidence_kind not in EVIDENCE_KINDS \
                or self.source_format not in {*_FORMATS.values(), "unknown"} \
                or type(self.content_digest) is not str or not digest.fullmatch(self.content_digest) \
                or type(self.fingerprint) is not str or not digest.fullmatch(self.fingerprint) \
                or self.fingerprint != hashlib.sha256((self.source_type + "\0" + self.content_digest).encode()).hexdigest() \
                or any(type(c) is not int or c < 0 for c in counts) \
                or any(c is not None and (type(c) is not int or c < 0) for c in optional_counts) \
                or self.timestamp_quality not in qualities or self.role_quality not in qualities \
                or self.boundary_quality not in qualities or self.provenance_quality != "acquired" \
                or self.parser_availability not in {"structural_probe", "unsupported"} \
                or self.import_readiness not in {"source_only_ready", "source_only_review"} \
                or type(self.probe_complete) is not bool or self.importer_version != PROBE_VERSION \
                or type(self.private_location) is not str or not Path(self.private_location).is_absolute() \
                or len(self.private_location) > 4096 or any(ord(c) < 32 for c in self.private_location) \
                or any(t is not None and _timestamp(t, epoch_seconds=False) is None for t in timestamps):
            raise HistoryInventoryError("invalid_record")
        if self.earliest_at and self.latest_at and \
                _timestamp(self.earliest_at, epoch_seconds=False) > _timestamp(self.latest_at, epoch_seconds=False):
            raise HistoryInventoryError("invalid_record")


def _excluded(path: Path) -> bool:
    return bool(_EXCLUDED_NAMES.search(path.name)) or any(
        part.casefold() in _EXCLUDED_DIRECTORIES for part in path.parts)


def _protect(path: Path, protected_paths: tuple[Path, ...]) -> None:
    candidate = path.resolve()
    for protected in protected_paths:
        root = Path(protected).resolve()
        if candidate == root or candidate.is_relative_to(root):
            raise HistoryInventoryError("protected_target")


def inventory_files(root: Path, *, suffixes: tuple[str, ...],
                    limits: InventoryLimits = InventoryLimits(),
                    protected_paths: tuple[Path, ...] = ()) -> tuple[Path, ...]:
    """Metadata walk of one explicit history root; rejects unsafe aliases."""
    try:
        root = _validate_root(root)
        _protect(root, protected_paths)
        files = []
        total_bytes = 0
        entries = 0
        def failed_walk(_error):
            raise HistoryInventoryError("unreadable_input")
        for current, directories, names in os.walk(root, followlinks=False, onerror=failed_walk):
            current = Path(current)
            if len(current.relative_to(root).parts) > 32:
                raise HistoryInventoryError("depth_limit")
            directories[:] = sorted(d for d in directories if not _excluded(current / d))
            entries += len(directories)
            if entries > limits.max_entries:
                raise HistoryInventoryError("entry_limit")
            for directory in directories:
                child = current / directory
                _protect(child, protected_paths)
                if _path_has_reparse_point(child):
                    raise HistoryInventoryError("unsafe_input")
            for name in sorted(names):
                path = current / name
                entries += 1
                if entries > limits.max_entries:
                    raise HistoryInventoryError("entry_limit")
                if _excluded(path) or path.suffix.casefold() not in suffixes:
                    continue
                _protect(path, protected_paths)
                if _path_has_reparse_point(path) or not path.is_file():
                    raise HistoryInventoryError("unsafe_input")
                info = path.stat(follow_symlinks=False)
                total_bytes += info.st_size
                if info.st_size > limits.max_file_bytes or total_bytes > limits.max_total_bytes:
                    raise HistoryInventoryError("file_limit")
                if info.st_nlink > 1:
                    raise HistoryInventoryError("unsafe_input")
                files.append(path)
        return tuple(sorted(files, key=lambda p: str(p).casefold()))
    except (InventoryError, OSError):
        raise HistoryInventoryError("unsafe_input") from None


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_key")
        value[key] = item
    return value


def _timestamp(value, *, epoch_seconds: bool) -> datetime | None:
    try:
        if type(value) in {int, float} and epoch_seconds and math.isfinite(value):
            return datetime.fromtimestamp(value, timezone.utc)
        if type(value) is not str or len(value) > 64:
            return None
        time = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return time.astimezone(timezone.utc) if time.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _quality(known: int, total: int) -> str:
    if known == 0:
        return "unknown"
    return "explicit" if known == total else "partial"


def _probe(raw: bytes, fmt: str, source_type: str, complete: bool, limits: InventoryLimits) -> dict:
    records = []
    malformed = 0
    parser = "structural_probe" if fmt in {"json", "jsonl"} else "unsupported"
    if fmt == "jsonl":
        lines = raw.splitlines()
        if not complete and lines:
            lines.pop()  # A truncated record is not a malformed original record.
        for line in lines:
            if not line.strip():
                continue
            if len(records) + malformed >= limits.max_records or len(line) > limits.max_line_bytes:
                raise HistoryInventoryError("probe_limit")
            try:
                value = json.loads(line, object_pairs_hook=_object)
                if type(value) is not dict:
                    raise ValueError
                records.append(value)
            except (ValueError, UnicodeError, RecursionError):
                malformed += 1
    elif fmt == "json" and complete:
        try:
            value = json.loads(raw, object_pairs_hook=_object)
            records = value if type(value) is list else [value]
            if len(records) > limits.max_records:
                raise HistoryInventoryError("probe_limit")
            malformed = sum(type(r) is not dict for r in records)
            records = [r for r in records if type(r) is dict]
        except HistoryInventoryError:
            raise
        except (ValueError, UnicodeError, RecursionError):
            malformed = 1
    timestamps = []
    known_roles = 0
    total = 0
    explicit_boundaries = 0
    codex_boundaries = set()
    message_estimate = 0
    has_messages = False
    for record in records:
        payload = record.get("payload")
        if record.get("type") == "session_meta" and type(payload) is dict and type(payload.get("id")) is str:
            explicit_boundaries += 1
            codex_boundaries.add(payload["id"])
        if type(record.get("session_id")) is str:
            explicit_boundaries += 1
        messages = record.get("messages")
        if type(messages) is list:
            if len(messages) > limits.max_records:
                raise HistoryInventoryError("probe_limit")
            has_messages = True
            message_estimate += len(messages)
            items = messages
        else:
            items = [record]
        for item in items:
            total += 1
            if total > limits.max_records:
                raise HistoryInventoryError("probe_limit")
            if type(item) is not dict:
                continue
            role = item.get("role")
            if type(role) is str and role in _ROLES:
                known_roles += 1
            time = _timestamp(item.get("timestamp"), epoch_seconds=source_type in {"hermes", "codex"})
            if time is not None:
                timestamps.append(time)
    def stamp(time):
        return time.isoformat().replace("+00:00", "Z")
    conflict = source_type == "codex" and len(codex_boundaries) > 1
    return {
        "record_estimate": len(records) if complete and parser != "unsupported" else None,
        "message_estimate": message_estimate if has_messages and complete else None,
        "earliest_at": stamp(min(timestamps)) if timestamps else None,
        "latest_at": stamp(max(timestamps)) if timestamps else None,
        "parser_availability": parser,
        "timestamp_quality": _quality(len(timestamps), total),
        "role_quality": _quality(known_roles, total),
        "boundary_quality": "ambiguous" if conflict else "explicit" if explicit_boundaries else "unknown",
        "malformed_records": malformed,
        "import_readiness": "source_only_ready" if parser != "unsupported" and complete and not malformed and not conflict else "source_only_review",
    }


def inspect_source(path: Path, *, source_root: Path, source_type: str,
                   acquisition_method: str, limits: InventoryLimits = InventoryLimits(),
                   protected_paths: tuple[Path, ...] = (), evidence_kind: str = "unknown") -> SourceInventoryRecord:
    """Hash exact input bytes and probe structure, without deriving messages."""
    path = Path(path)
    if source_type not in SOURCE_TYPES or acquisition_method not in ACQUISITION_METHODS or evidence_kind not in EVIDENCE_KINDS:
        raise HistoryInventoryError("invalid_descriptor")
    if _excluded(path):
        raise HistoryInventoryError("excluded_input")
    try:
        _protect(path, protected_paths)
        if path.stat(follow_symlinks=False).st_nlink > 1:
            raise HistoryInventoryError("unsafe_input")
        if path.stat().st_size > limits.max_file_bytes:
            raise HistoryInventoryError("file_limit")
        fmt = _FORMATS.get(path.suffix.casefold(), "unknown")
        capture = limits.max_probe_bytes if fmt in {"json", "jsonl"} else 0
        digest, size, raw = _fingerprint_file(path, source_root=source_root,
                                             capture_limit=capture, max_bytes=limits.max_file_bytes)
        complete = size <= limits.max_probe_bytes if capture else False
        details = _probe(raw, fmt, source_type, complete, limits)
        fingerprint = hashlib.sha256((source_type + "\0" + digest).encode()).hexdigest()
        return SourceInventoryRecord(source_type=source_type, acquisition_method=acquisition_method,
                                     source_format=fmt, private_location=str(path.resolve()),
                                     content_digest=digest, fingerprint=fingerprint, size_bytes=size,
                                     provenance_quality="acquired", probe_complete=complete,
                                     evidence_kind=evidence_kind, **details)
    except InventoryError as error:
        if str(error) == "Migration source changed during inventory":
            raise HistoryInventoryError("source_changed") from None
        raise HistoryInventoryError("unsafe_input") from None
    except OSError:
        raise HistoryInventoryError("unsafe_input") from None
