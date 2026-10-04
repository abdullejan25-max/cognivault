"""Shared append-only metadata for persisted records; identities are reports, not authentication."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import sqlite3
from uuid import uuid4

from .contracts import GatewayError


_ORIGINS = {"source", "deterministic_derived", "agent_generated", "imported", "legacy_import", "system_generated"}
_ACTORS = {"external_client", "importer", "system", "unknown"}
_LEGACY = {"native", "imported", "pre_provenance"}
_IDENTITY_FIELDS = {"reported_agent", "reported_client", "run_id"}
_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|[/\\]|\.\.)")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _safe_report(value: object, maximum: int) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not 1 <= len(value.strip()) <= maximum \
            or any(ord(char) < 32 for char in value) or _PATH.search(value):
        raise GatewayError("INVALID_ARGUMENT", "Invalid reported provenance identity")
    return value.strip()


@dataclass(frozen=True)
class ReportedIdentity:
    reported_agent: str | None = None
    reported_client: str | None = None
    run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reported_agent", _safe_report(self.reported_agent, 120))
        object.__setattr__(self, "reported_client", _safe_report(self.reported_client, 120))
        object.__setattr__(self, "run_id", _safe_report(self.run_id, 120))

    @classmethod
    def from_value(cls, value: object = None) -> "ReportedIdentity":
        if value is None:
            return cls()
        if type(value) is not dict or set(value) - _IDENTITY_FIELDS:
            raise GatewayError("INVALID_ARGUMENT", "Invalid reported provenance identity")
        return cls(_safe_report(value.get("reported_agent"), 120),
                   _safe_report(value.get("reported_client"), 120),
                   _safe_report(value.get("run_id"), 120))


def _has_table(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _legacy_insert(connection: sqlite3.Connection, record_type: str, record_id: str,
                   data_origin: str, recorded_at: str | None = None, source_refs: list[str] | None = None,
                   version: int = 1, supersedes: str | None = None,
                   original_created_at: str | None = None, source_system: str | None = None,
                   imported_at: str | None = None, import_batch_id: str | None = None) -> str:
    prior = connection.execute(
        "SELECT provenance_id FROM write_provenance WHERE record_type=? AND record_id=? AND version=?",
        (record_type, record_id, version),
    ).fetchone()
    if prior:
        return prior[0]
    provenance_id = "provenance:" + uuid4().hex
    connection.execute(
        "INSERT INTO write_provenance(provenance_id, record_type, record_id, version, data_origin, "
        "actor_type, identity_trust, reported_agent, reported_client, run_id, source_refs, recorded_at, "
        "supersedes_provenance_id, original_created_at, imported_at, source_system, import_batch_id, legacy_status) "
        "VALUES (?, ?, ?, ?, ?, 'unknown', 'unavailable', NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, 'pre_provenance')",
        (provenance_id, record_type, record_id, version, data_origin,
         json.dumps(source_refs or [], ensure_ascii=False, separators=(",", ":")),
         recorded_at or utc_now(), supersedes, original_created_at, imported_at, source_system, import_batch_id),
    )
    return provenance_id


def ensure_provenance_schema(connection: sqlite3.Connection, scope: str = "generic", *,
                             backfill: bool = True) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS schema_migrations(
            scope TEXT NOT NULL, version INTEGER NOT NULL, applied_at TEXT NOT NULL,
            PRIMARY KEY(scope, version));
        CREATE TABLE IF NOT EXISTS write_provenance(
            provenance_id TEXT PRIMARY KEY, record_type TEXT NOT NULL, record_id TEXT NOT NULL,
            version INTEGER NOT NULL CHECK(version > 0), data_origin TEXT NOT NULL,
            actor_type TEXT NOT NULL, identity_trust TEXT NOT NULL,
            reported_agent TEXT, reported_client TEXT, run_id TEXT, source_refs TEXT NOT NULL,
            recorded_at TEXT NOT NULL, supersedes_provenance_id TEXT REFERENCES write_provenance(provenance_id),
            original_created_at TEXT, imported_at TEXT, source_system TEXT, import_batch_id TEXT,
            legacy_status TEXT NOT NULL, UNIQUE(record_type, record_id, version));
        CREATE INDEX IF NOT EXISTS write_provenance_record
            ON write_provenance(record_type, record_id, version DESC);
        CREATE TRIGGER IF NOT EXISTS write_provenance_no_update BEFORE UPDATE ON write_provenance
            BEGIN SELECT RAISE(ABORT, 'immutable provenance'); END;
        CREATE TRIGGER IF NOT EXISTS write_provenance_no_delete BEFORE DELETE ON write_provenance
            BEGIN SELECT RAISE(ABORT, 'immutable provenance'); END;
    """)
    applied = connection.execute("SELECT 1 FROM schema_migrations WHERE scope=? AND version=1", (scope,)).fetchone()
    if applied:
        return

    if not backfill:
        connection.execute("INSERT OR IGNORE INTO schema_migrations(scope, version, applied_at) VALUES (?, 1, ?)",
                           (scope, utc_now()))
        return

    if _has_table(connection, "assets"):
        for row in connection.execute("SELECT uri FROM assets"):
            _legacy_insert(connection, "asset", row[0], "source")
    if _has_table(connection, "documents"):
        columns = _columns(connection, "documents")
        query = "SELECT uri, asset_uri" + (", created_at" if "created_at" in columns else "") + " FROM documents"
        for row in connection.execute(query):
            _legacy_insert(connection, "document", row[0], "deterministic_derived",
                           source_refs=[row[1]], recorded_at=row[2] if len(row) > 2 else None)
    if _has_table(connection, "pages"):
        for row in connection.execute("SELECT document_uri, page_number FROM pages"):
            _legacy_insert(connection, "document_page", f"{row[0]}#page={row[1]}",
                           "deterministic_derived", source_refs=[row[0]])
    if _has_table(connection, "wrong_sources"):
        for row in connection.execute("SELECT source_id, source_uri, created_at FROM wrong_sources"):
            _legacy_insert(connection, "wrong_answer_source", row[0], "source",
                           recorded_at=row[2], source_refs=[row[1]])
    if _has_table(connection, "wrong_analyses"):
        previous_by_source: dict[str, str] = {}
        for row in connection.execute("SELECT source_id, analysis_id, source_refs, created_at, version FROM wrong_analyses ORDER BY source_id, version"):
            prior_id = previous_by_source.get(row[0])
            prior_prov = get_provenance(connection, "wrong_answer_analysis", prior_id, row[4] - 1) if prior_id else None
            prov_id = _legacy_insert(connection, "wrong_answer_analysis", row[1], "agent_generated",
                                     recorded_at=row[3], source_refs=json.loads(row[2]), version=row[4],
                                     supersedes=prior_prov["provenance_id"] if prior_prov else None)
            previous_by_source[row[0]] = row[1]
    if _has_table(connection, "history_sources"):
        for row in connection.execute("SELECT source_id FROM history_sources"):
            _legacy_insert(connection, "history_source", row[0], "imported")
    if _has_table(connection, "history_items"):
        cols = _columns(connection, "history_items")
        select = "SELECT item_id, source_id, created_at" + (", imported_at, source_system, import_batch_id" if {"imported_at", "source_system", "import_batch_id"} <= cols else "") + " FROM history_items"
        for row in connection.execute(select):
            _legacy_insert(connection, "history_item", row[0], "imported", original_created_at=row[2],
                           imported_at=row[3] if len(row) > 3 else None,
                           source_system=row[4] if len(row) > 4 else None,
                           import_batch_id=row[5] if len(row) > 5 else None,
                           source_refs=[f"history-source://{row[1]}"])
    connection.execute("INSERT OR IGNORE INTO schema_migrations(scope, version, applied_at) VALUES (?, 1, ?)",
                       (scope, utc_now()))


def insert_provenance(connection: sqlite3.Connection, *, record_type: str, record_id: str,
                      version: int, data_origin: str, actor_type: str,
                      source_refs: list[str] | tuple[str, ...] = (),
                      identity: ReportedIdentity | None = None,
                      supersedes_provenance_id: str | None = None,
                      original_created_at: str | None = None, imported_at: str | None = None,
                      source_system: str | None = None, import_batch_id: str | None = None,
                      legacy_status: str = "native") -> str:
    if data_origin not in _ORIGINS or actor_type not in _ACTORS or legacy_status not in _LEGACY \
            or type(version) is not int or version < 1 or type(record_id) is not str or not record_id \
            or type(record_type) is not str or not record_type:
        raise GatewayError("INVALID_ARGUMENT", "Invalid provenance record")
    if type(source_refs) not in (list, tuple) or any(type(ref) is not str or not ref for ref in source_refs):
        raise GatewayError("INVALID_ARGUMENT", "Invalid provenance source references")
    identity = identity or ReportedIdentity()
    trust = "reported" if any((identity.reported_agent, identity.reported_client, identity.run_id)) else "unavailable"
    provenance_id = "provenance:" + uuid4().hex
    connection.execute(
        "INSERT INTO write_provenance(provenance_id, record_type, record_id, version, data_origin, "
        "actor_type, identity_trust, reported_agent, reported_client, run_id, source_refs, recorded_at, "
        "supersedes_provenance_id, original_created_at, imported_at, source_system, import_batch_id, legacy_status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (provenance_id, record_type, record_id, version, data_origin, actor_type, trust,
         identity.reported_agent, identity.reported_client, identity.run_id,
         json.dumps(list(source_refs), ensure_ascii=False, separators=(",", ":")), utc_now(),
         supersedes_provenance_id, original_created_at, imported_at, source_system, import_batch_id,
         legacy_status),
    )
    return provenance_id


def get_provenance(connection: sqlite3.Connection, record_type: str, record_id: str,
                   version: int | None = None) -> dict | None:
    if not _has_table(connection, "write_provenance"):
        return None
    query = "SELECT * FROM write_provenance WHERE record_type=? AND record_id=?"
    args: tuple = (record_type, record_id)
    if version is not None:
        query += " AND version=?"
        args += (version,)
    else:
        query += " ORDER BY version DESC LIMIT 1"
    row = connection.execute(query, args).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["source_refs"] = json.loads(result["source_refs"])
    return result
