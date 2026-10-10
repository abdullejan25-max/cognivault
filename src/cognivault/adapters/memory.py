"""Append-only, opt-in private durable facts. Evidence references are not yet verified."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from ..contracts import GatewayError
from ..provenance import (ReportedIdentity, ensure_provenance_schema,
                          get_provenance, insert_provenance, utc_now)
from .documents import _path_has_reparse_point


MEMORY_ID = re.compile(r"memory:[0-9a-f]{32}\Z")
EVIDENCE_ID = re.compile(
    r"(?:history:[0-9a-f]{64}|message:[0-9a-f]{64}|"
    r"(?:document|wrong-answer)://sha256/[0-9a-f]{64})\Z"
)
REQUEST_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")


def _invalid() -> GatewayError:
    return GatewayError("INVALID_ARGUMENT", "Invalid Memory request")


def _text(value: object, maximum: int) -> str:
    if type(value) is not str or not 1 <= len(value.strip()) <= maximum \
            or any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        raise _invalid()
    return value.strip()


def _references(value: object) -> list[str]:
    if type(value) is not list or not 1 <= len(value) <= 16 \
            or any(type(ref) is not str or EVIDENCE_ID.fullmatch(ref) is None for ref in value) \
            or len(value) != len(set(value)):
        raise _invalid()
    return list(value)


def _digest(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SQLiteMemoryStore:
    """No automatic extraction. Only explicit writes with versioned source claims.

    Syntactically valid source references are stored as unverified claims; callers
    must not present them as independently validated evidence until a later
    cross-domain resolver verifies them.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        if not self.database_path.is_absolute():
            raise ValueError("Memory database path must be absolute")

    def _connect(self, *, write: bool = False) -> sqlite3.Connection:
        path = self.database_path
        if _path_has_reparse_point(path) or _path_has_reparse_point(path.parent) \
                or not path.parent.is_dir():
            raise GatewayError("STORAGE_UNAVAILABLE", "Memory storage is unavailable")
        try:
            if write:
                con = sqlite3.connect(path, timeout=5)
            else:
                con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            return con
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Memory storage is unavailable") from None

    @staticmethod
    def _schema(con: sqlite3.Connection) -> None:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS memory_facts(
                memory_id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memory_versions(
                memory_id TEXT NOT NULL REFERENCES memory_facts(memory_id),
                version INTEGER NOT NULL CHECK(version > 0),
                value TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('active', 'retired')),
                source_refs TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                PRIMARY KEY(memory_id, version)
            );
            CREATE TABLE IF NOT EXISTS memory_requests(
                idempotency_key TEXT PRIMARY KEY,
                request_sha256 TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operation_audit(
                audit_id INTEGER PRIMARY KEY,
                operation TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                outcome TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS memory_facts_no_update
                BEFORE UPDATE ON memory_facts BEGIN SELECT RAISE(ABORT,'immutable memory identity'); END;
            CREATE TRIGGER IF NOT EXISTS memory_facts_no_delete
                BEFORE DELETE ON memory_facts BEGIN SELECT RAISE(ABORT,'immutable memory identity'); END;
            CREATE TRIGGER IF NOT EXISTS memory_versions_no_update
                BEFORE UPDATE ON memory_versions BEGIN SELECT RAISE(ABORT,'immutable memory evidence'); END;
            CREATE TRIGGER IF NOT EXISTS memory_versions_no_delete
                BEFORE DELETE ON memory_versions BEGIN SELECT RAISE(ABORT,'immutable memory evidence'); END;
        """)
        ensure_provenance_schema(con, "memory", backfill=False)

    @staticmethod
    def _record(con: sqlite3.Connection, memory_id: str, version: int | None = None) -> dict | None:
        query = (
            "SELECT f.memory_id, f.subject, f.predicate, f.created_at, "
            "v.version, v.value, v.state, v.source_refs, v.recorded_at "
            "FROM memory_facts f JOIN memory_versions v USING(memory_id) "
            "WHERE f.memory_id=? "
        )
        if version is None:
            query += "AND v.version=(SELECT MAX(version) FROM memory_versions WHERE memory_id=f.memory_id)"
            args = (memory_id,)
        else:
            query += "AND v.version=?"
            args = (memory_id, version)
        row = con.execute(query, args).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["source_refs"] = json.loads(result["source_refs"])
        result["evidence_verified"] = False
        result["write_provenance"] = get_provenance(con, "memory", memory_id, result["version"])
        return result

    @staticmethod
    def _audit(con: sqlite3.Connection, operation: str, memory_id: str) -> None:
        con.execute("INSERT INTO operation_audit(operation,resource_type,resource_id,occurred_at,outcome) "
                    "VALUES (?,'memory',?,?,'success')", (operation, memory_id, utc_now()))

    @staticmethod
    def _check_request(con: sqlite3.Connection, key: str, digest: str) -> dict | None:
        row = con.execute("SELECT request_sha256,memory_id,version FROM memory_requests "
                          "WHERE idempotency_key=?", (key,)).fetchone()
        if row is None:
            return None
        if row["request_sha256"] != digest:
            raise GatewayError("CONFLICT", "Memory request key conflicts")
        result = SQLiteMemoryStore._record(con, row["memory_id"], row["version"])
        if result is None:
            raise GatewayError("STORAGE_UNAVAILABLE", "Memory record is unavailable")
        return {"memory": result, "reused": True}

    def create(self, subject: str, predicate: str, value: str,
               source_refs: list[str], idempotency_key: str,
               identity: ReportedIdentity | None = None) -> dict:
        subject = _text(subject, 200)
        predicate = _text(predicate, 120)
        value = _text(value, 4000)
        refs = _references(source_refs)
        if type(idempotency_key) is not str or REQUEST_KEY.fullmatch(idempotency_key) is None:
            raise _invalid()
        digest = _digest({"op": "create", "subject": subject, "predicate": predicate,
                          "value": value, "source_refs": refs})
        try:
            with closing(self._connect(write=True)) as con:
                self._schema(con)
                with con:
                    con.execute("BEGIN IMMEDIATE")
                    existing = self._check_request(con, idempotency_key, digest)
                    if existing is not None:
                        return existing
                    mid = "memory:" + uuid4().hex
                    now = utc_now()
                    con.execute("INSERT INTO memory_facts VALUES (?,?,?,?)",
                                (mid, subject, predicate, now))
                    con.execute("INSERT INTO memory_versions VALUES (?,?,?,?,?,?)",
                                (mid, 1, value, "active", json.dumps(refs), now))
                    insert_provenance(con, record_type="memory", record_id=mid, version=1,
                                      data_origin="agent_generated", actor_type="external_client",
                                      source_refs=refs, identity=identity)
                    con.execute("INSERT INTO memory_requests VALUES (?,?,?,?)",
                                (idempotency_key, digest, mid, 1))
                    self._audit(con, "create_memory", mid)
                    return {"memory": self._record(con, mid, 1), "reused": False}
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Memory storage is unavailable") from None

    def revise(self, memory_id: str, value: str, source_refs: list[str],
               expected_version: int, idempotency_key: str, *,
               retire: bool = False, identity: ReportedIdentity | None = None) -> dict:
        if type(memory_id) is not str or MEMORY_ID.fullmatch(memory_id) is None \
                or type(expected_version) is not int or not 1 <= expected_version <= 1000000 \
                or type(retire) is not bool \
                or type(idempotency_key) is not str or REQUEST_KEY.fullmatch(idempotency_key) is None:
            raise _invalid()
        value = _text(value, 4000)  # For retirement, value is the reason, not a new active fact.
        refs = _references(source_refs)
        state = "retired" if retire else "active"
        digest = _digest({"op": "revise", "memory_id": memory_id, "value": value,
                          "source_refs": refs, "expected_version": expected_version, "state": state})
        try:
            with closing(self._connect(write=True)) as con:
                self._schema(con)
                with con:
                    con.execute("BEGIN IMMEDIATE")
                    existing = self._check_request(con, idempotency_key, digest)
                    if existing is not None:
                        return existing
                    previous = self._record(con, memory_id)
                    if previous is None:
                        raise GatewayError("RESOURCE_NOT_FOUND", "Memory was not found")
                    if previous["version"] != expected_version or previous["state"] == "retired":
                        raise GatewayError("CONFLICT", "Memory version conflicts")
                    version = expected_version + 1
                    con.execute("INSERT INTO memory_versions VALUES (?,?,?,?,?,?)",
                                (memory_id, version, value, state, json.dumps(refs), utc_now()))
                    provenance = previous["write_provenance"]
                    insert_provenance(con, record_type="memory", record_id=memory_id,
                                      version=version, data_origin="agent_generated",
                                      actor_type="external_client", source_refs=refs,
                                      identity=identity,
                                      supersedes_provenance_id=(provenance or {}).get("provenance_id"))
                    con.execute("INSERT INTO memory_requests VALUES (?,?,?,?)",
                                (idempotency_key, digest, memory_id, version))
                    self._audit(con, "retire_memory" if retire else "revise_memory", memory_id)
                    return {"memory": self._record(con, memory_id, version), "reused": False}
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Memory storage is unavailable") from None

    def fetch(self, memory_id: str) -> dict:
        if type(memory_id) is not str or MEMORY_ID.fullmatch(memory_id) is None:
            raise _invalid()
        if not self.database_path.exists():
            raise GatewayError("RESOURCE_NOT_FOUND", "Memory was not found")
        try:
            with closing(self._connect()) as con:
                record = self._record(con, memory_id)
                if record is None:
                    raise GatewayError("RESOURCE_NOT_FOUND", "Memory was not found")
                return {"memory": record}
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Memory storage is unavailable") from None

    def search(self, query: str, limit: int = 5, offset: int = 0) -> dict:
        query = _text(query, 500)
        if type(limit) is not int or not 1 <= limit <= 20 \
                or type(offset) is not int or not 0 <= offset <= 1000:
            raise _invalid()
        if not self.database_path.exists():
            return {"memories": [], "total": 0}
        try:
            with closing(self._connect()) as con:
                where = ("FROM memory_facts f JOIN memory_versions v USING(memory_id) "
                         "WHERE v.version=(SELECT MAX(version) FROM memory_versions "
                         "WHERE memory_id=f.memory_id) AND v.state='active' "
                         "AND (instr(lower(f.subject),lower(?))>0 OR "
                         "instr(lower(f.predicate),lower(?))>0 OR "
                         "instr(lower(v.value),lower(?))>0)")
                params = (query, query, query)
                total = con.execute("SELECT COUNT(*) " + where, params).fetchone()[0]
                rows = con.execute("SELECT f.memory_id " + where +
                                   " ORDER BY v.recorded_at DESC, f.memory_id LIMIT ? OFFSET ?",
                                   (*params, limit, offset)).fetchall()
                return {"memories": [self._record(con, r["memory_id"]) for r in rows],
                        "total": total}
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Memory storage is unavailable") from None
