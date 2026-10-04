"""Private Phase A ledger, separate from the authoritative Gateway stores."""

from __future__ import annotations

from collections import Counter
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

from .conversation_registry import _restrict_private_permissions
from .history_inventory import ERROR_CODES, SOURCE_TYPES, SourceInventoryRecord
from .manifest import validate_private_journal_path

_STATES = frozenset({"discovered", "parsed", "imported", "reused", "skipped", "duplicate", "malformed", "unsupported", "ambiguous", "error"})
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_ID = re.compile(r"(?:[A-Za-z0-9][A-Za-z0-9:_.-]{0,255}|(?:asset|document)://sha256/[0-9a-f]{64})\Z")
_CATALOG_STATES = frozenset({"available", "waiting_for_export", "acquisition_pending", "gateway_unavailable", "not_found", "retained_legacy", "review_required"})
_SCHEMA = {
    "ledger_meta": "key TEXT PRIMARY KEY, value TEXT NOT NULL",
    "sources": "fingerprint TEXT PRIMARY KEY, payload TEXT NOT NULL",
    "acquisitions": "fingerprint TEXT NOT NULL REFERENCES sources, location TEXT NOT NULL, acquired_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (fingerprint, location)",
    "catalog": "scope TEXT PRIMARY KEY, source_type TEXT NOT NULL, state TEXT NOT NULL, private_location TEXT, recorded_at TEXT NOT NULL",
    "acquisition_errors": "scope TEXT NOT NULL, location TEXT NOT NULL, recorded_at TEXT NOT NULL, error_code TEXT NOT NULL, PRIMARY KEY(scope,location)",
    "events": "event_id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL REFERENCES sources, state TEXT NOT NULL, recorded_at TEXT NOT NULL, evidence TEXT, error_code TEXT",
    "failure_events": "failure_id INTEGER PRIMARY KEY, scope TEXT NOT NULL, location TEXT NOT NULL, state TEXT NOT NULL, error_code TEXT, recorded_at TEXT NOT NULL",
}


def _validate_schema(connection):
    rows = connection.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()
    if not rows:
        return None
    try:
        meta = connection.execute("SELECT key,value FROM ledger_meta").fetchall()
        if meta not in ([("schema_version", "1")], [("schema_version", "2")]):
            raise HistoryLedgerError("invalid_schema")
        version = meta[0][1]
        expected = {k:v for k,v in _SCHEMA.items() if version == "2" or k != "failure_events"}
        if len(rows) != len(expected):
            raise HistoryLedgerError("invalid_schema")
        def normalize(sql):
            return re.sub(r"\s+", "", sql).casefold().replace("ifnotexists", "")
        for kind, name, sql in rows:
            if kind != "table" or name not in expected or normalize(sql) != normalize(f"CREATE TABLE {name} ({expected[name]})"):
                raise HistoryLedgerError("invalid_schema")
        return version
    except sqlite3.Error:
        raise HistoryLedgerError("invalid_schema") from None


class HistoryLedgerError(ValueError):
    """Fixed error classes only; no private exception chaining."""


def _valid_gateway_evidence(evidence, *, historical: bool = False) -> bool:
    # study_system is deprecated provenance from pre-rename events, never a new write.
    authorities = {"cognivault", "study_system"} if historical else {"cognivault"}
    return type(evidence) is dict and set(evidence) == {"authority", "record_id", "receipt_digest"} \
        and type(evidence.get("authority")) is str and evidence["authority"] in authorities \
        and type(evidence.get("record_id")) is str and bool(_ID.fullmatch(evidence["record_id"])) \
        and type(evidence.get("receipt_digest")) is str and bool(_HASH.fullmatch(evidence["receipt_digest"]))


class HistoryMigrationLedger:
    """Durable acquisitions plus append-only outcome events; not proof of V2 state."""

    def __init__(self, path: Path, *, protected_paths: tuple[Path, ...] = ()):
        self._protected = protected_paths
        try:
            self._path = validate_private_journal_path(path, protected_paths=protected_paths)
            if any((parent / ".git").exists() for parent in self._path.parents):
                raise HistoryLedgerError("unsafe_ledger")
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _restrict_private_permissions(self._path.parent, directory=True)
            with closing(self._connect()) as c:
                c.execute("BEGIN IMMEDIATE")
                version = _validate_schema(c)
                for name, definition in _SCHEMA.items():
                    c.execute(f"CREATE TABLE IF NOT EXISTS {name} ({definition})")
                if version == "1":
                    c.execute("INSERT INTO failure_events(scope,location,state,error_code,recorded_at) "
                              "SELECT scope,location,'failed',error_code,recorded_at FROM acquisition_errors")
                c.execute("INSERT INTO ledger_meta VALUES ('schema_version','2') ON CONFLICT(key) DO UPDATE SET value='2'")
                c.execute("COMMIT")
            _restrict_private_permissions(self._path, directory=False)
        except (OSError, ValueError, sqlite3.Error) as error:
            if isinstance(error, HistoryLedgerError):
                raise
            raise HistoryLedgerError("unsafe_ledger") from None

    def _connect(self):
        validate_private_journal_path(self._path, protected_paths=self._protected)
        if self._path.exists() and self._path.stat().st_size > 512 * 1024 * 1024:
            raise HistoryLedgerError("ledger_limit")
        c = sqlite3.connect(self._path, timeout=5, isolation_level=None)
        c.execute("PRAGMA trusted_schema=OFF")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA synchronous=FULL")
        c.execute("PRAGMA journal_mode=DELETE")
        return c

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def discover(self, record: SourceInventoryRecord, *, initial_state: str | None = None) -> str:
        if type(record) is not SourceInventoryRecord:
            raise HistoryLedgerError("invalid_record")
        if initial_state not in {None, "parsed", "malformed", "unsupported", "ambiguous"}:
            raise HistoryLedgerError("invalid_outcome")
        payload = json.dumps(asdict(record), sort_keys=True)
        try:
            with closing(self._connect()) as c:
                c.execute("BEGIN IMMEDIATE")
                existing = c.execute("SELECT payload FROM sources WHERE fingerprint=?", (record.fingerprint,)).fetchone()
                state = "duplicate" if existing else "discovered"
                if existing:
                    original = json.loads(existing[0])
                    if original["content_digest"] != record.content_digest or original["source_type"] != record.source_type:
                        raise HistoryLedgerError("identity_conflict")
                else:
                    c.execute("INSERT INTO sources VALUES (?, ?)", (record.fingerprint, payload))
                now = self._now()
                c.execute("INSERT OR IGNORE INTO acquisitions VALUES (?, ?, ?, ?)", (record.fingerprint, record.private_location, now, payload))
                c.execute("INSERT INTO events(fingerprint,state,recorded_at) VALUES (?,?,?)", (record.fingerprint, state, now))
                if initial_state is not None:
                    disposition = c.execute("SELECT state FROM events WHERE fingerprint=? AND state!='duplicate' "
                                            "ORDER BY event_id DESC LIMIT 1", (record.fingerprint,)).fetchone()
                    if disposition == ("discovered",):
                        c.execute("INSERT INTO events(fingerprint,state,recorded_at) VALUES (?,?,?)",
                                  (record.fingerprint, initial_state, now))
                c.execute("COMMIT")
                return state
        except (sqlite3.Error, OSError, ValueError) as error:
            if isinstance(error, HistoryLedgerError):
                raise
            raise HistoryLedgerError("ledger_unavailable") from None

    def record_outcome(self, fingerprint: str, state: str, *, evidence: dict | None = None,
                       error_code: str | None = None):
        if type(fingerprint) is not str or not _HASH.fullmatch(fingerprint) or state not in _STATES \
                or (error_code is not None and (type(error_code) is not str or error_code not in ERROR_CODES)):
            raise HistoryLedgerError("invalid_outcome")
        if state in {"imported", "reused"}:
            if not _valid_gateway_evidence(evidence):
                raise HistoryLedgerError("gateway_evidence_required")
        elif evidence is not None:
            raise HistoryLedgerError("invalid_outcome")
        try:
            with closing(self._connect()) as c:
                c.execute("BEGIN IMMEDIATE")
                if c.execute("SELECT 1 FROM sources WHERE fingerprint=?", (fingerprint,)).fetchone() is None:
                    raise HistoryLedgerError("unknown_source")
                c.execute("INSERT INTO events(fingerprint,state,recorded_at,evidence,error_code) VALUES (?,?,?,?,?)",
                          (fingerprint, state, self._now(), json.dumps(evidence) if evidence else None, error_code))
                c.execute("COMMIT")
        except (sqlite3.Error, OSError, ValueError) as error:
            if isinstance(error, HistoryLedgerError):
                raise
            raise HistoryLedgerError("ledger_unavailable") from None

    def acquisitions(self, fingerprint: str) -> tuple[str, ...]:
        """Private audit only. Never include these locators in public outputs."""
        with closing(self._connect()) as c:
            return tuple(r[0] for r in c.execute("SELECT location FROM acquisitions WHERE fingerprint=? ORDER BY location", (fingerprint,)))

    def record_catalog(self, scope: str, *, source_type: str, state: str,
                       private_location: str | None = None):
        """Account for unavailable source categories without inventing file records."""
        if type(scope) is not str or not _CODE.fullmatch(scope) or source_type not in SOURCE_TYPES \
                or state not in _CATALOG_STATES or (private_location is not None and
                (type(private_location) is not str or not Path(private_location).is_absolute() or
                 len(private_location) > 4096 or any(ord(c) < 32 for c in private_location))):
            raise HistoryLedgerError("invalid_catalog")
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO catalog VALUES (?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET "
                      "source_type=excluded.source_type,state=excluded.state,"
                      "private_location=excluded.private_location,recorded_at=excluded.recorded_at",
                      (scope, source_type, state, private_location, self._now()))
            c.execute("COMMIT")

    def record_failure(self, scope: str, private_location: str, error_code: str):
        """Preserve every unreadable/changed input locator even without a digest."""
        if type(scope) is not str or not _CODE.fullmatch(scope) or type(error_code) is not str \
                or error_code not in ERROR_CODES or type(private_location) is not str \
                or not Path(private_location).is_absolute() or len(private_location) > 4096 \
                or any(ord(c) < 32 for c in private_location):
            raise HistoryLedgerError("invalid_outcome")
        with closing(self._connect()) as c:
            c.execute("INSERT INTO failure_events(scope,location,state,error_code,recorded_at) VALUES (?,?,'failed',?,?)",
                      (scope, private_location, error_code, self._now()))

    def resolve_failure(self, scope: str, private_location: str):
        """Append a recovery event; repeated success does not erase error history."""
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            latest = c.execute("SELECT state FROM failure_events WHERE scope=? AND location=? ORDER BY failure_id DESC LIMIT 1",
                               (scope, private_location)).fetchone()
            if latest == ("failed",):
                c.execute("INSERT INTO failure_events(scope,location,state,recorded_at) VALUES (?,?,'resolved',?)",
                          (scope, private_location, self._now()))
            c.execute("COMMIT")

    def public_summary(self) -> dict:
        with closing(self._connect()) as c:
            _validate_schema(c)
            try:
                payloads = [asdict(SourceInventoryRecord(**json.loads(r[0]))) for r in c.execute("SELECT payload FROM sources")]
                for state, evidence in c.execute("SELECT state,evidence FROM events"):
                    if state not in _STATES:
                        raise ValueError
                    if state in {"imported", "reused"}:
                        if evidence is None or not _valid_gateway_evidence(json.loads(evidence), historical=True):
                            raise ValueError
                    elif evidence is not None:
                        raise ValueError
                for state, source_type in c.execute("SELECT state,source_type FROM catalog"):
                    if state not in _CATALOG_STATES or source_type not in SOURCE_TYPES:
                        raise ValueError
                for state, code in c.execute("SELECT DISTINCT state,error_code FROM failure_events"):
                    if state not in {"failed", "resolved"} or (state == "failed" and code not in ERROR_CODES) or (state == "resolved" and code is not None):
                        raise ValueError
            except (ValueError, TypeError):
                raise HistoryLedgerError("invalid_ledger") from None
            states = dict(c.execute("""SELECT state, COUNT(*) FROM events WHERE event_id IN
                (SELECT MAX(event_id) FROM events WHERE state!='duplicate' GROUP BY fingerprint)
                GROUP BY state ORDER BY state"""))
            locations = c.execute("SELECT COUNT(*) FROM acquisitions").fetchone()[0]
            duplicates = c.execute("SELECT COUNT(*) FROM events WHERE state='duplicate'").fetchone()[0]
            return {
                "unique_sources": len(payloads),
                "acquisition_locations": locations,
                "exact_duplicate_copies": locations - len(payloads),
                "rerun_discoveries": duplicates - (locations - len(payloads)),
                "catalog_state_counts": dict(c.execute("SELECT state,COUNT(*) FROM catalog GROUP BY state ORDER BY state")),
                "acquisition_error_counts": dict(c.execute("SELECT error_code,COUNT(*) FROM failure_events WHERE state='failed' AND failure_id IN "
                    "(SELECT MAX(failure_id) FROM failure_events GROUP BY scope,location) GROUP BY error_code ORDER BY error_code")),
                "historical_failure_counts": dict(c.execute("SELECT error_code,COUNT(*) FROM failure_events WHERE state='failed' GROUP BY error_code ORDER BY error_code")),
                "resolved_acquisition_errors": c.execute("SELECT COUNT(*) FROM failure_events WHERE state='resolved'").fetchone()[0],
                "event_counts": dict(c.execute("SELECT state, COUNT(*) FROM events GROUP BY state ORDER BY state")),
                "current_state_counts": states,
                "by_source_type": dict(sorted(Counter(p["source_type"] for p in payloads).items())),
                "by_format": dict(sorted(Counter(p["source_format"] for p in payloads).items())),
                "by_evidence_kind": dict(sorted(Counter(p["evidence_kind"] for p in payloads).items())),
                "source_bytes": sum(p["size_bytes"] for p in payloads),
                "malformed_records": sum(p["malformed_records"] for p in payloads),
                "gateway_current_state": "unverified",
            }
