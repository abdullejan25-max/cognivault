"""Privacy-safe migration manifests and a resumable private SQLite journal.

The journal stores identifiers and fingerprints only. Source text, source paths,
and filenames are intentionally outside this interface.
"""

from dataclasses import dataclass
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
import stat
from typing import Iterable

from ..adapters.documents import _path_has_reparse_point

CATEGORIES = (
    "Study",
    "History",
    "Atomic Facts",
    "Raw transcripts",
    "Assets",
    "Documents",
    "Wrong Answers",
    "Other / Unsupported",
)
_ACTIONS = frozenset({"reuse", "import", "archive", "skip"})
_STATUSES = frozenset({"planned", "committed", "skipped", "unresolved", "error"})
_VALIDATION_STATES = frozenset({"not_checked", "valid", "invalid", "unresolved"})
_HEX_256 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}\Z")
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,255}\Z")
_CLI_RUN_ID = re.compile(r"migration-[0-9a-f]{32}\Z")
_ABSOLUTE_PATH = re.compile(
    r"^(?:[A-Za-z]:[\\/]|\\\\|/|file:/+)",
    re.IGNORECASE,
)


class ManifestConflict(ValueError):
    """A stable legacy identity was observed with incompatible migration data."""


@dataclass(frozen=True)
class MigrationItem:
    category: str
    legacy_system: str
    legacy_source_type: str
    legacy_item_id: str | None
    source_fingerprint: str
    target_type: str
    target_logical_id: str | None
    action: str
    status: str
    source_event_time: str | None = None
    imported_at: str | None = None
    dedup_decision: str = "none"
    validation_state: str = "not_checked"
    reason_code: str | None = None
    error_code: str | None = None
    source_size_bytes: int | None = None


def stable_migration_key(legacy_system: str, legacy_id: str | None,
                         content_hash: str) -> str:
    """Return an opaque deterministic identity for one legacy version."""
    if type(legacy_system) is not str or not _SAFE_LABEL.fullmatch(legacy_system) \
            or (legacy_id is not None and not _valid_identifier(legacy_id)) \
            or type(content_hash) is not str or not _HEX_256.fullmatch(content_hash):
        raise ValueError("Invalid migration identity")
    payload = "\0".join((legacy_system, legacy_id or "", content_hash)).encode("utf-8")
    return "migration:" + hashlib.sha256(payload).hexdigest()


def _valid_identifier(value: str) -> bool:
    return type(value) is str and _SAFE_IDENTIFIER.fullmatch(value) is not None \
        and not _ABSOLUTE_PATH.match(value)


def validate_cli_run_id(value: str) -> bool:
    """Only expose opaque random run tokens that are safe to print and resume."""
    return type(value) is str and _CLI_RUN_ID.fullmatch(value) is not None


def _canonical_path(path: Path) -> Path:
    try:
        return Path(path).resolve(strict=False)
    except OSError:
        raise ValueError("Invalid private migration journal") from None


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _existing_file_identity(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError("Invalid private migration journal") from None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Invalid private migration journal")
    if info.st_nlink > 1:
        raise ValueError("Invalid private migration journal")
    return info.st_dev, info.st_ino


def validate_private_journal_path(path: Path, *,
                                  protected_paths: Iterable[Path] = ()) -> Path:
    """Reject journals in the repository or any configured source/target tree."""
    candidate = Path(path)
    if not candidate.is_absolute() or _path_has_reparse_point(candidate):
        raise ValueError("Invalid private migration journal")
    resolved = _canonical_path(candidate)
    journal_paths = (resolved, *(Path(str(resolved) + suffix)
                                 for suffix in ("-wal", "-shm", "-journal")))
    if any(_path_has_reparse_point(journal_path) for journal_path in journal_paths):
        raise ValueError("Invalid private migration journal")
    journal_identities = {
        identity for journal_path in journal_paths
        if (identity := _existing_file_identity(journal_path)) is not None
    }
    repository_root = _canonical_path(Path(__file__).resolve().parents[3])
    if any(_paths_overlap(journal_path, repository_root) for journal_path in journal_paths):
        raise ValueError("Invalid private migration journal")
    for protected_path in protected_paths:
        if protected_path is None:
            continue
        protected = Path(protected_path)
        if not protected.is_absolute() or _path_has_reparse_point(protected):
            raise ValueError("Invalid private migration journal")
        canonical_protected = _canonical_path(protected)
        if any(_paths_overlap(journal_path, canonical_protected)
               for journal_path in journal_paths):
            raise ValueError("Invalid private migration journal")
        if protected.is_file():
            try:
                info = protected.stat(follow_symlinks=False)
            except OSError:
                raise ValueError("Invalid private migration journal") from None
            if (info.st_dev, info.st_ino) in journal_identities:
                raise ValueError("Invalid private migration journal")
    return resolved


def _valid_timestamp(value: str | None) -> bool:
    if value is None:
        return True
    if type(value) is not str or not value or len(value) > 40:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _validate_item(item: MigrationItem) -> None:
    if type(item) is not MigrationItem \
            or type(item.category) is not str or item.category not in CATEGORIES \
            or type(item.legacy_system) is not str or not _SAFE_LABEL.fullmatch(item.legacy_system) \
            or type(item.legacy_source_type) is not str or not _SAFE_LABEL.fullmatch(item.legacy_source_type) \
            or (item.legacy_item_id is not None and not _valid_identifier(item.legacy_item_id)) \
            or type(item.source_fingerprint) is not str or not _HEX_256.fullmatch(item.source_fingerprint) \
            or type(item.target_type) is not str or not _SAFE_LABEL.fullmatch(item.target_type) \
            or (item.target_logical_id is not None and not _valid_identifier(item.target_logical_id)) \
            or type(item.action) is not str or item.action not in _ACTIONS \
            or type(item.status) is not str or item.status not in _STATUSES \
            or type(item.validation_state) is not str or item.validation_state not in _VALIDATION_STATES \
            or not _valid_timestamp(item.source_event_time) or not _valid_timestamp(item.imported_at) \
            or type(item.dedup_decision) is not str or not _SAFE_CODE.fullmatch(item.dedup_decision) \
            or (item.reason_code is not None and
                (type(item.reason_code) is not str or not _SAFE_CODE.fullmatch(item.reason_code))) \
            or (item.error_code is not None and
                (type(item.error_code) is not str or not _SAFE_CODE.fullmatch(item.error_code))) \
            or (item.source_size_bytes is not None and
                (type(item.source_size_bytes) is not int or item.source_size_bytes < 0)):
        raise ValueError("Invalid migration item")


def _legacy_identity(item: MigrationItem) -> str:
    stable_key = stable_migration_key(item.legacy_system, item.legacy_item_id,
                                      item.source_fingerprint)
    plan_identity = (item.category, item.legacy_source_type, item.target_type, item.action)
    payload = repr(plan_identity).encode("utf-8")
    return hashlib.sha256(stable_key.encode("ascii") + b"\0" + payload).hexdigest()


class MigrationJournal:
    """A local SQLite manifest journal with atomic, resumable batch checkpoints."""

    def __init__(self, path: Path, *, run_id: str,
                 protected_paths: Iterable[Path] = ()) -> None:
        self.protected_paths = tuple(protected_paths)
        self.path = validate_private_journal_path(
            path, protected_paths=self.protected_paths,
        )
        if not self.path.parent.is_dir() \
                or type(run_id) is not str or not _SAFE_RUN_ID.fullmatch(run_id):
            raise ValueError("Invalid private migration journal")
        self.run_id = run_id
        with closing(self._connect(write=True)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            validate_private_journal_path(
                self.path, protected_paths=self.protected_paths,
            )
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS migration_runs(
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS migration_items(
                    run_id TEXT NOT NULL REFERENCES migration_runs(run_id),
                    migration_key TEXT NOT NULL,
                    legacy_identity TEXT NOT NULL,
                    category TEXT NOT NULL,
                    legacy_system TEXT NOT NULL,
                    legacy_source_type TEXT NOT NULL,
                    legacy_item_id TEXT,
                    source_fingerprint TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_logical_id TEXT,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_event_time TEXT,
                    imported_at TEXT,
                    dedup_decision TEXT NOT NULL,
                    validation_state TEXT NOT NULL,
                    reason_code TEXT,
                    error_code TEXT,
                    source_size_bytes INTEGER CHECK(source_size_bytes IS NULL OR source_size_bytes >= 0),
                    PRIMARY KEY(run_id, migration_key),
                    UNIQUE(run_id, legacy_identity)
                );
                CREATE TABLE IF NOT EXISTS migration_checkpoints(
                    run_id TEXT NOT NULL REFERENCES migration_runs(run_id),
                    batch_number INTEGER NOT NULL CHECK(batch_number > 0),
                    item_count INTEGER NOT NULL CHECK(item_count > 0),
                    batch_fingerprint TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, batch_number)
                );
            """)

    def _connect(self, *, write: bool = False) -> sqlite3.Connection:
        validate_private_journal_path(self.path, protected_paths=self.protected_paths)
        if write:
            connection = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        else:
            connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True,
                                         isolation_level=None, timeout=10)
        try:
            validate_private_journal_path(self.path, protected_paths=self.protected_paths)
        except Exception:
            connection.close()
            raise
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if write:
            connection.execute("PRAGMA synchronous=FULL")
        return connection

    def add_batch(self, items: Iterable[MigrationItem], *, batch_number: int) -> None:
        batch = tuple(items)
        if type(batch_number) is not int or batch_number < 1 or not 1 <= len(batch) <= 500:
            raise ValueError("Invalid migration batch")
        for item in batch:
            _validate_item(item)
        keys = ["migration:" + _legacy_identity(item) for item in batch]
        batch_fingerprint = hashlib.sha256("\n".join(sorted(keys)).encode("ascii")).hexdigest()
        connection = self._connect(write=True)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO migration_runs(run_id, created_at) VALUES (?, ?)",
                (self.run_id, _now_utc()),
            )
            checkpoint = connection.execute(
                "SELECT item_count, batch_fingerprint FROM migration_checkpoints "
                "WHERE run_id=? AND batch_number=?", (self.run_id, batch_number),
            ).fetchone()
            if checkpoint is not None and (checkpoint["item_count"] != len(batch)
                                           or checkpoint["batch_fingerprint"] != batch_fingerprint):
                raise ManifestConflict("Migration checkpoint conflicts with replay")
            for item, migration_key in zip(batch, keys, strict=True):
                identity = _legacy_identity(item)
                existing = connection.execute(
                    "SELECT * FROM migration_items WHERE run_id=? AND migration_key=?",
                    (self.run_id, migration_key),
                ).fetchone()
                values = _item_values(item)
                if existing is not None:
                    if _stored_values(existing) != values:
                        if not _valid_state_transition(existing, item):
                            raise ManifestConflict("Migration item key has conflicting manifest fields")
                        connection.execute(
                            "UPDATE migration_items SET category=?, legacy_system=?, legacy_source_type=?, "
                            "legacy_item_id=?, source_fingerprint=?, target_type=?, target_logical_id=?, "
                            "action=?, status=?, source_event_time=?, imported_at=?, dedup_decision=?, "
                            "validation_state=?, reason_code=?, error_code=?, source_size_bytes=? "
                            "WHERE run_id=? AND migration_key=?",
                            (*values, self.run_id, migration_key),
                        )
                    continue
                connection.execute(
                    "INSERT INTO migration_items(run_id, migration_key, legacy_identity, category, "
                    "legacy_system, legacy_source_type, legacy_item_id, source_fingerprint, target_type, "
                    "target_logical_id, action, status, source_event_time, imported_at, dedup_decision, "
                    "validation_state, reason_code, error_code, source_size_bytes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (self.run_id, migration_key, identity, *values),
                )
            connection.execute(
                "INSERT OR IGNORE INTO migration_checkpoints(run_id, batch_number, item_count, "
                "batch_fingerprint, recorded_at) VALUES (?, ?, ?, ?, ?)",
                (self.run_id, batch_number, len(batch), batch_fingerprint, _now_utc()),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def list_items(self) -> tuple[MigrationItem, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT category, legacy_system, legacy_source_type, legacy_item_id, "
                "source_fingerprint, target_type, target_logical_id, action, status, "
                "source_event_time, imported_at, dedup_decision, validation_state, reason_code, error_code, "
                "source_size_bytes "
                "FROM migration_items WHERE run_id=? ORDER BY migration_key", (self.run_id,),
            ).fetchall()
        return tuple(MigrationItem(*row) for row in rows)

    def checkpoints(self) -> tuple[int, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT batch_number FROM migration_checkpoints WHERE run_id=? ORDER BY batch_number",
                (self.run_id,),
            ).fetchall()
        return tuple(row[0] for row in rows)

    def close(self) -> None:
        """Compatibility no-op; connections are scoped to each journal operation."""


def _item_values(item: MigrationItem) -> tuple:
    return (
        item.category, item.legacy_system, item.legacy_source_type, item.legacy_item_id,
        item.source_fingerprint, item.target_type, item.target_logical_id, item.action,
        item.status, item.source_event_time, item.imported_at, item.dedup_decision,
        item.validation_state, item.reason_code, item.error_code,
        item.source_size_bytes,
    )


def _stored_values(row: sqlite3.Row) -> tuple:
    names = (
        "category", "legacy_system", "legacy_source_type", "legacy_item_id",
        "source_fingerprint", "target_type", "target_logical_id", "action", "status",
        "source_event_time", "imported_at", "dedup_decision", "validation_state",
        "reason_code", "error_code", "source_size_bytes",
    )
    return tuple(row[name] for name in names)


def _valid_state_transition(existing: sqlite3.Row, item: MigrationItem) -> bool:
    """Allow only the planned-to-committed update for a successfully applied item."""
    if existing["status"] != "planned" or item.status != "committed" \
            or existing["imported_at"] is not None or item.imported_at is None:
        return False
    old = _stored_values(existing)
    new = _item_values(item)
    mutable = {"target_logical_id", "status", "imported_at"}
    names = (
        "category", "legacy_system", "legacy_source_type", "legacy_item_id",
        "source_fingerprint", "target_type", "target_logical_id", "action", "status",
        "source_event_time", "imported_at", "dedup_decision", "validation_state",
        "reason_code", "error_code", "source_size_bytes",
    )
    for name, old_value, new_value in zip(names, old, new, strict=True):
        if name not in mutable and old_value != new_value:
            return False
    old_target = existing["target_logical_id"]
    new_target = item.target_logical_id
    return old_target == new_target or (old_target is None and new_target is not None)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def summarize(items: Iterable[MigrationItem]) -> dict[str, dict[str, int | dict[str, int]]]:
    """Return fixed-category counts without IDs, fingerprints, paths, or content."""
    result = {category: {
        "found": 0,
        "reuse": 0,
        "planned_import": 0,
        "committed": 0,
        "deduplicated": 0,
        "archive_only": 0,
        "skip": 0,
        "unresolved": 0,
        "errors": 0,
        "dedup_decisions": {},
        "reason_codes": {},
        "error_codes": {},
    } for category in CATEGORIES}
    for item in items:
        _validate_item(item)
        counts = result[item.category]
        counts["found"] += 1
        if item.status == "planned":
            if item.action == "reuse":
                counts["reuse"] += 1
            elif item.action == "import":
                counts["planned_import"] += 1
            else:
                raise ValueError("Invalid planned migration outcome")
        elif item.status == "committed":
            counts["committed"] += 1
        elif item.status == "skipped":
            if item.dedup_decision != "none":
                counts["deduplicated"] += 1
            elif item.action == "archive":
                counts["archive_only"] += 1
            else:
                counts["skip"] += 1
        elif item.status == "unresolved":
            counts["unresolved"] += 1
        elif item.status == "error":
            counts["errors"] += 1

        if item.dedup_decision != "none" and item.action != "reuse":
            _increment(counts["dedup_decisions"], item.dedup_decision)
        if item.reason_code is not None:
            _increment(counts["reason_codes"], item.reason_code)
        if item.error_code is not None:
            _increment(counts["error_codes"], item.error_code)
    return result


def _increment(counts: dict[str, int], code: str) -> None:
    counts[code] = counts.get(code, 0) + 1


def estimate_storage(items: Iterable[MigrationItem]) -> dict[str, int]:
    """Estimate source volume, candidate payload volume, and ready import volume."""
    total = 0
    estimated = 0
    ready = 0
    for item in items:
        _validate_item(item)
        size = item.source_size_bytes or 0
        total += size
        if item.action == "import" and item.status in {"planned", "unresolved"}:
            estimated += size
            if item.status == "planned":
                ready += size
    return {"source_bytes": total, "estimated_import_bytes": estimated,
            "ready_import_bytes": ready}
