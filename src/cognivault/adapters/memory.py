"""Append-only durable assertions; the Gateway resolves evidence and permissions."""

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
    if type(value) is not str or not 1 <= len(value.strip()) <= maximum or len(value) > maximum \
            or any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        raise _invalid()
    return value.strip()


def _references(value: object) -> list[str]:
    if type(value) is not list or not 1 <= len(value) <= 16 \
            or any(type(ref) is not str or EVIDENCE_ID.fullmatch(ref) is None for ref in value) \
            or len(value) != len(set(value)):
        raise _invalid()
    return sorted(value)


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
        if not self.database_path.is_absolute() or ".." in self.database_path.parts \
                or any(":" in part for part in self.database_path.parts[1:]):
            raise ValueError("Memory database path must be absolute")

    def _connect(self, *, write: bool = False) -> sqlite3.Connection:
        self._validate_path()
        path = self.database_path
        try:
            if write:
                con = sqlite3.connect(path, timeout=5)
            else:
                con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            return con
        except (sqlite3.Error, OSError):
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
            CREATE TABLE IF NOT EXISTS memory_assessments(
                memory_id TEXT NOT NULL, version INTEGER NOT NULL,
                epistemic_status TEXT NOT NULL CHECK(epistemic_status IN ('unverified','verified','inference')),
                verification_note TEXT, PRIMARY KEY(memory_id, version),
                FOREIGN KEY(memory_id, version) REFERENCES memory_versions(memory_id, version)
            );
            CREATE TRIGGER IF NOT EXISTS memory_assessments_no_update
                BEFORE UPDATE ON memory_assessments BEGIN SELECT RAISE(ABORT,'immutable assessment'); END;
            CREATE TRIGGER IF NOT EXISTS memory_assessments_no_delete
                BEFORE DELETE ON memory_assessments BEGIN SELECT RAISE(ABORT,'immutable assessment'); END;
            CREATE INDEX IF NOT EXISTS memory_fact_slot ON memory_facts(subject, predicate);
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
        # Read old PR 10 databases without migration or modifying schema.
        assessment = None
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='memory_assessments'").fetchone():
            assessment = con.execute("SELECT epistemic_status,verification_note FROM memory_assessments "
                                     "WHERE memory_id=? AND version=?", (memory_id, result["version"])).fetchone()
        result["epistemic_status"] = assessment[0] if assessment else "unverified"
        result["verification_note"] = assessment[1] if assessment else None
        result["verification_trust"] = "reported" if result["epistemic_status"] == "verified" else "unavailable"
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
               identity: ReportedIdentity | None = None, *,
               epistemic_status: str = "unverified", verification_note: str | None = None) -> dict:
        self._assessment(epistemic_status, verification_note)
        subject = _text(subject, 200)
        predicate = _text(predicate, 120)
        value = _text(value, 4000)
        refs = _references(source_refs)
        if type(idempotency_key) is not str or REQUEST_KEY.fullmatch(idempotency_key) is None:
            raise _invalid()
        payload = {"op": "create", "subject": subject, "predicate": predicate, "value": value, "source_refs": refs}
        if epistemic_status != "unverified" or verification_note is not None:
            payload.update(epistemic_status=epistemic_status, verification_note=verification_note)
        digest = _digest(payload)
        try:
            with closing(self._connect(write=True)) as con:
                self._schema(con)
                con.commit()  # Schema metadata must finish before the atomic fact transaction.
                with con:
                    con.execute("BEGIN IMMEDIATE")
                    existing = self._check_request(con, idempotency_key, digest)
                    if existing is not None:
                        return existing
                    rows = con.execute("SELECT f.memory_id FROM memory_facts f JOIN memory_versions v USING(memory_id) "
                                       "WHERE f.subject=? AND f.predicate=? AND v.state='active' AND v.version="
                                       "(SELECT MAX(version) FROM memory_versions WHERE memory_id=f.memory_id)",
                                       (subject, predicate)).fetchall()
                    if rows:
                        previous = self._record(con, rows[0]["memory_id"])
                        if len(rows) != 1 or not self._same(previous, value, refs, "active", epistemic_status, verification_note):
                            raise GatewayError("CONFLICT", "Memory assertion conflicts; revise the existing fact")
                        con.execute("INSERT INTO memory_requests VALUES (?,?,?,?)",
                                    (idempotency_key, digest, previous["memory_id"], previous["version"]))
                        return {"memory": previous, "reused": True}
                    mid = "memory:" + uuid4().hex
                    now = utc_now()
                    con.execute("INSERT INTO memory_facts VALUES (?,?,?,?)",
                                (mid, subject, predicate, now))
                    con.execute("INSERT INTO memory_versions VALUES (?,?,?,?,?,?)",
                                (mid, 1, value, "active", json.dumps(refs), now))
                    con.execute("INSERT INTO memory_assessments VALUES (?,?,?,?)", (mid, 1, epistemic_status, verification_note))
                    insert_provenance(con, record_type="memory", record_id=mid, version=1,
                                      data_origin="agent_generated", actor_type="external_client",
                                      source_refs=refs, identity=identity)
                    con.execute("INSERT INTO memory_requests VALUES (?,?,?,?)",
                                (idempotency_key, digest, mid, 1))
                    self._audit(con, "create_memory", mid)
                    return {"memory": self._record(con, mid, 1), "reused": False}
        except (sqlite3.Error, OSError):
            raise GatewayError("STORAGE_UNAVAILABLE", "Memory storage is unavailable") from None

    def revise(self, memory_id: str, value: str, source_refs: list[str],
               expected_version: int, idempotency_key: str, *,
               retire: bool = False, identity: ReportedIdentity | None = None,
               epistemic_status: str = "unverified", verification_note: str | None = None) -> dict:
        self._assessment(epistemic_status, verification_note)
        if type(memory_id) is not str or MEMORY_ID.fullmatch(memory_id) is None \
                or type(expected_version) is not int or not 1 <= expected_version <= 1000000 \
                or type(retire) is not bool \
                or type(idempotency_key) is not str or REQUEST_KEY.fullmatch(idempotency_key) is None:
            raise _invalid()
        value = _text(value, 4000)  # For retirement, value is the reason, not a new active fact.
        refs = _references(source_refs)
        state = "retired" if retire else "active"
        payload = {"op": "revise", "memory_id": memory_id, "value": value, "source_refs": refs, "expected_version": expected_version, "state": state}
        if epistemic_status != "unverified" or verification_note is not None:
            payload.update(epistemic_status=epistemic_status, verification_note=verification_note)
        digest = _digest(payload)
        try:
            with closing(self._connect(write=True)) as con:
                self._schema(con)
                con.commit()  # Schema metadata must finish before the atomic fact transaction.
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
                    if self._same(previous, value, refs, state, epistemic_status, verification_note):
                        con.execute("INSERT INTO memory_requests VALUES (?,?,?,?)", (idempotency_key, digest, memory_id, expected_version))
                        return {"memory": previous, "reused": True}
                    version = expected_version + 1
                    con.execute("INSERT INTO memory_versions VALUES (?,?,?,?,?,?)",
                                (memory_id, version, value, state, json.dumps(refs), utc_now()))
                    con.execute("INSERT INTO memory_assessments VALUES (?,?,?,?)", (memory_id, version, epistemic_status, verification_note))
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
        except (sqlite3.Error, OSError):
            raise GatewayError("STORAGE_UNAVAILABLE", "Memory storage is unavailable") from None

    def fetch(self, memory_id: str, version: int | None = None) -> dict:
        if type(memory_id) is not str or MEMORY_ID.fullmatch(memory_id) is None:
            raise _invalid()
        if version is not None and (type(version) is not int or not 1 <= version <= 1000000):
            raise _invalid()
        self._validate_path()
        if not self.database_path.exists():
            raise GatewayError("RESOURCE_NOT_FOUND", "Memory was not found")
        try:
            with closing(self._connect()) as con:
                record = self._record(con, memory_id, version)
                if record is None:
                    raise GatewayError("RESOURCE_NOT_FOUND", "Memory was not found")
                return {"memory": record}
        except (sqlite3.Error, OSError):
            raise GatewayError("STORAGE_UNAVAILABLE", "Memory storage is unavailable") from None

    def search(self, query: str, limit: int = 5, offset: int = 0) -> dict:
        query = _text(query, 500)
        if type(limit) is not int or not 1 <= limit <= 20 \
                or type(offset) is not int or not 0 <= offset <= 1000:
            raise _invalid()
        self._validate_path()
        if not self.database_path.exists():
            return {"memories": [], "total": 0}
        try:
            with closing(self._connect()) as con:
                where = ("FROM memory_facts f JOIN memory_versions v USING(memory_id) "
                         "WHERE v.version=(SELECT MAX(version) FROM memory_versions "
                         "WHERE memory_id=f.memory_id) AND v.state='active' "
                         "AND (instr(lower(f.subject),lower(?))>0 OR "
                         "instr(lower(f.predicate),lower(?))>0 OR "
                         "instr(lower(v.value),lower(?))>0 OR instr(v.source_refs,?)>0)")
                params = (query, query, query, query)
                total = con.execute("SELECT COUNT(*) " + where, params).fetchone()[0]
                rows = con.execute("SELECT f.memory_id " + where +
                                   " ORDER BY v.recorded_at DESC, f.memory_id LIMIT ? OFFSET ?",
                                   (*params, limit, offset)).fetchall()
                return {"memories": [self._record(con, r["memory_id"]) for r in rows],
                        "total": total}
        except (sqlite3.Error, OSError):
            raise GatewayError("STORAGE_UNAVAILABLE", "Memory storage is unavailable") from None

    @staticmethod
    def _assessment(status, note):
        if type(status) is not str or status not in {"unverified", "verified", "inference"}:
            raise _invalid()
        if note is not None:
            _text(note, 1000)
        if status == "verified" and note is None:
            raise _invalid()

    @staticmethod
    def _same(record, value, refs, state, status, note):
        return (record["value"], sorted(record["source_refs"]), record["state"], record["epistemic_status"], record["verification_note"]) == (value, refs, state, status, note)

    def versions(self, memory_id: str, limit: int = 5, offset: int = 0) -> dict:
        if type(memory_id) is not str or MEMORY_ID.fullmatch(memory_id) is None or type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or not 0 <= offset <= 1000:
            raise _invalid()
        self._validate_path()
        if not self.database_path.exists():
            raise GatewayError("RESOURCE_NOT_FOUND", "Memory was not found")
        try:
            with closing(self._connect()) as con:
                total = con.execute("SELECT COUNT(*) FROM memory_versions WHERE memory_id=?", (memory_id,)).fetchone()[0]
                if not total:
                    raise GatewayError("RESOURCE_NOT_FOUND", "Memory was not found")
                rows = con.execute("SELECT version FROM memory_versions WHERE memory_id=? ORDER BY version DESC LIMIT ? OFFSET ?", (memory_id, limit, offset)).fetchall()
                return {"versions": [self._record(con, memory_id, row[0]) for row in rows], "total": total, "has_more": offset + len(rows) < total}
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Memory storage is unavailable") from None

    def _validate_path(self):
        path = self.database_path
        try:
            for candidate in (path, *(Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))):
                if _path_has_reparse_point(candidate) or (candidate.exists() and (not candidate.is_file() or candidate.stat().st_nlink > 1)):
                    raise GatewayError("STORAGE_UNAVAILABLE", "Memory storage is unavailable")
            if not path.parent.is_dir():
                raise GatewayError("STORAGE_UNAVAILABLE", "Memory storage is unavailable")
        except OSError:
            raise GatewayError("STORAGE_UNAVAILABLE", "Memory storage is unavailable") from None
