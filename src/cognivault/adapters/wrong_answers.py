"""Immutable evidence and append-only Agent analysis in the private document DB."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3

from ..contracts import GatewayError
from ..provenance import (ReportedIdentity, ensure_provenance_schema, get_provenance,
                          insert_provenance)
from .documents import SQLiteDocumentStore, _audit


SOURCE_ID = re.compile(r"wrong-answer://sha256/[0-9a-f]{64}\Z")
ASSET_URI = re.compile(r"asset://sha256/[0-9a-f]{64}\Z")
DOCUMENT_URI = re.compile(r"document://sha256/[0-9a-f]{64}\Z")
ANALYSIS_FIELDS = frozenset({"error_type", "knowledge_points", "reasoning",
                             "correct_solution", "review_advice"})
_PROJECTION_MAX_RECORDS = 10_000
_PROJECTION_MAX_BYTES = 32 * 1024 * 1024
_PROJECTION_PAGE_SIZE = 20


def _invalid() -> GatewayError:
    return GatewayError("INVALID_ARGUMENT", "Invalid wrong-answer input")


def _text(value: object, maximum: int) -> str:
    if type(value) is not str or len(value) > maximum or not 1 <= len(value.strip()) <= maximum \
            or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise _invalid()
    return value.strip()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class SQLiteWrongAnswerStore:
    def __init__(self, documents: SQLiteDocumentStore) -> None:
        self.documents = documents

    @staticmethod
    def _has_table(con: sqlite3.Connection, name: str) -> bool:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
                           (name,)).fetchone() is not None

    @staticmethod
    def _schema(con: sqlite3.Connection) -> None:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS wrong_sources(
                source_id TEXT PRIMARY KEY, source_uri TEXT NOT NULL, page_number INTEGER,
                question_text TEXT NOT NULL, student_answer TEXT NOT NULL,
                text_origin TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS wrong_analyses(
                analysis_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, version INTEGER NOT NULL,
                body TEXT NOT NULL, source_refs TEXT NOT NULL, study_relations TEXT NOT NULL,
                generated_by_agent INTEGER NOT NULL, provenance TEXT NOT NULL,
                created_at TEXT NOT NULL, UNIQUE(source_id, version));
            CREATE TABLE IF NOT EXISTS wrong_analysis_requests(
                idempotency_key TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL,
                analysis_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operation_audit(
                audit_id INTEGER PRIMARY KEY, operation TEXT NOT NULL, resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL, occurred_at TEXT NOT NULL, outcome TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS wrong_sources_no_update BEFORE UPDATE ON wrong_sources
                BEGIN SELECT RAISE(ABORT, 'immutable source'); END;
            CREATE TRIGGER IF NOT EXISTS wrong_sources_no_delete BEFORE DELETE ON wrong_sources
                BEGIN SELECT RAISE(ABORT, 'immutable source'); END;
            CREATE TRIGGER IF NOT EXISTS wrong_analyses_no_update BEFORE UPDATE ON wrong_analyses
                BEGIN SELECT RAISE(ABORT, 'immutable analysis'); END;
            CREATE TRIGGER IF NOT EXISTS wrong_analyses_no_delete BEFORE DELETE ON wrong_analyses
                BEGIN SELECT RAISE(ABORT, 'immutable analysis'); END;
        """)
        ensure_provenance_schema(con, "wrong_answers")

    @staticmethod
    def _source(row: sqlite3.Row) -> dict:
        return {key: row[key] for key in ("source_id", "source_uri", "page_number",
                                        "question_text", "student_answer", "text_origin", "created_at")}

    @staticmethod
    def _analysis(row: sqlite3.Row, provenance: dict | None = None,
                  supersedes_analysis_id: str | None = None) -> dict:
        return {"analysis_id": row["analysis_id"], "source_id": row["source_id"],
                "version": row["version"], **json.loads(row["body"]),
                "source_refs": json.loads(row["source_refs"]),
                "study_relations": json.loads(row["study_relations"]),
                "generated_by_agent": bool(row["generated_by_agent"]),
                "provenance": row["provenance"], "created_at": row["created_at"],
                "supersedes_analysis_id": supersedes_analysis_id,
                "write_provenance": provenance}

    def _source_origin(self, source_uri: str, page_number: int | None) -> str:
        if type(source_uri) is not str:
            raise _invalid()
        if ASSET_URI.fullmatch(source_uri):
            if page_number is not None:
                raise _invalid()
            if self.documents.fetch_asset_record(source_uri) is None:
                raise GatewayError("RESOURCE_NOT_FOUND", "Source was not found")
            self.documents.fetch_asset(source_uri, 0, 1)
            return "asset_only"
        if DOCUMENT_URI.fullmatch(source_uri):
            doc = self.documents.fetch_document(source_uri)
            if doc is None:
                raise GatewayError("RESOURCE_NOT_FOUND", "Source was not found")
            self.documents.fetch_asset(doc.asset_uri, 0, 1)
            if page_number is None:
                return doc.text_origin
            if type(page_number) is not int or not 1 <= page_number <= 64:
                raise _invalid()
            page = self.documents.fetch_page(source_uri, page_number)
            if page is None:
                raise GatewayError("RESOURCE_NOT_FOUND", "Source page was not found")
            return page.text_origin
        raise _invalid()

    def register_source(self, source_uri: str, question_text: str, student_answer: str,
                        page_number: int | None = None,
                        identity: ReportedIdentity | None = None) -> dict:
        identity = identity or ReportedIdentity()
        question_text = _text(question_text, 10_000)
        student_answer = _text(student_answer, 10_000)
        origin = self._source_origin(source_uri, page_number)
        source_identity = _canonical([source_uri, page_number, question_text, student_answer, origin])
        source_id = "wrong-answer://sha256/" + hashlib.sha256(source_identity.encode()).hexdigest()
        legacy_identity = _canonical([source_uri, page_number])
        legacy_source_id = "wrong-answer://sha256/" + hashlib.sha256(legacy_identity.encode()).hexdigest()
        with closing(self.documents._connect(write=True)) as con:
            try:
                self._schema(con)
                with con:
                    row = con.execute("SELECT * FROM wrong_sources WHERE source_id = ?", (source_id,)).fetchone()
                    if row is None and legacy_source_id != source_id:
                        legacy = con.execute("SELECT * FROM wrong_sources WHERE source_id = ?",
                                             (legacy_source_id,)).fetchone()
                        if legacy is not None and legacy["question_text"] == question_text \
                                and legacy["student_answer"] == student_answer \
                                and legacy["text_origin"] == origin:
                            result = self._source(legacy)
                            result["write_provenance"] = get_provenance(
                                con, "wrong_answer_source", legacy["source_id"]
                            )
                            return result
                    if row:
                        if row["question_text"] != question_text or row["student_answer"] != student_answer \
                                or row["text_origin"] != origin:
                            raise GatewayError("CONFLICT", "Source conflicts with existing data")
                        result = self._source(row)
                        result["write_provenance"] = get_provenance(con, "wrong_answer_source", source_id)
                        return result
                    created_at = _timestamp()
                    con.execute("INSERT INTO wrong_sources VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (source_id, source_uri, page_number, question_text,
                                 student_answer, origin, created_at))
                    provenance_id = insert_provenance(
                        con, record_type="wrong_answer_source", record_id=source_id, version=1,
                        data_origin="source", actor_type="external_client", source_refs=[source_uri],
                        identity=identity,
                    )
                    _audit(con, "register_wrong_answer_source", "wrong_answer", source_id)
                    result = {"source_id": source_id, "source_uri": source_uri,
                            "page_number": page_number, "question_text": question_text,
                            "student_answer": student_answer, "text_origin": origin,
                            "created_at": created_at}
                    result["write_provenance"] = get_provenance(con, "wrong_answer_source", source_id)
                    return result
            except sqlite3.Error:
                raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None

    def _existing_refs(self, refs: object, source_uri: str) -> list[str]:
        if type(refs) is not list or not 1 <= len(refs) <= 16 or len(set(map(str, refs))) != len(refs):
            raise _invalid()
        for ref in refs:
            if type(ref) is not str:
                raise _invalid()
            if ASSET_URI.fullmatch(ref):
                exists = self.documents.fetch_asset_record(ref) is not None
                if exists:
                    self.documents.fetch_asset(ref, 0, 1)
            elif DOCUMENT_URI.fullmatch(ref):
                document = self.documents.fetch_document(ref)
                exists = document is not None
                if document is not None:
                    self.documents.fetch_asset(document.asset_uri, 0, 1)
            else:
                raise _invalid()
            if not exists:
                raise GatewayError("RESOURCE_NOT_FOUND", "Source reference was not found")
        if source_uri not in refs:
            raise _invalid()
        return refs

    @staticmethod
    def _validated_analysis(body: object) -> dict:
        if type(body) is not dict or set(body) != ANALYSIS_FIELDS:
            raise _invalid()
        normalized = {}
        for key in ANALYSIS_FIELDS - {"knowledge_points"}:
            normalized[key] = _text(body[key], 10_000)
        points = body["knowledge_points"]
        if type(points) is not list or not 1 <= len(points) <= 20:
            raise _invalid()
        normalized["knowledge_points"] = [_text(point, 200) for point in points]
        return normalized

    def lookup_analysis(self, source_id: str, analysis: dict, source_refs: list[str],
                        study_relations: list[str], idempotency_key: str,
                        expected_version: int, identity: ReportedIdentity | None = None) -> dict | None:
        """Replay a completed request without depending on later source availability."""
        if type(source_id) is not str or not SOURCE_ID.fullmatch(source_id) \
                or type(idempotency_key) is not str \
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", idempotency_key) \
                or type(expected_version) is not int or expected_version < 0:
            raise _invalid()
        body = self._validated_analysis(analysis)
        if type(source_refs) is not list or not 1 <= len(source_refs) <= 16 \
                or any(type(ref) is not str or not (ASSET_URI.fullmatch(ref) or DOCUMENT_URI.fullmatch(ref))
                       for ref in source_refs) or len(set(source_refs)) != len(source_refs):
            raise _invalid()
        if type(study_relations) is not list or len(study_relations) > 16 \
                or any(type(item) is not str for item in study_relations) \
                or len(set(study_relations)) != len(study_relations):
            raise _invalid()
        identity = identity or ReportedIdentity()
        payload = _canonical([source_id, body, source_refs, study_relations, expected_version,
                              identity.reported_agent, identity.reported_client, identity.run_id])
        digest = hashlib.sha256(payload.encode()).hexdigest()
        legacy_payload = _canonical([source_id, body, source_refs, study_relations, expected_version])
        legacy_digest = hashlib.sha256(legacy_payload.encode()).hexdigest()
        with closing(self.documents._connect()) as con:
            if not self._has_table(con, "wrong_analysis_requests"):
                return None
            prior = con.execute("SELECT * FROM wrong_analysis_requests WHERE idempotency_key = ?",
                                (idempotency_key,)).fetchone()
            if prior is None:
                return None
            legacy_replay = not any((identity.reported_agent, identity.reported_client, identity.run_id)) \
                and prior["payload_sha256"] == legacy_digest
            if prior["payload_sha256"] != digest and not legacy_replay:
                raise GatewayError("CONFLICT", "Idempotency key conflicts with existing data")
            row = con.execute("SELECT * FROM wrong_analyses WHERE analysis_id = ?",
                              (prior["analysis_id"],)).fetchone()
            if row is None:
                raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
            previous = con.execute("SELECT analysis_id FROM wrong_analyses WHERE source_id=? AND version=?",
                                    (row["source_id"], row["version"] - 1)).fetchone()
            return self._analysis(row, get_provenance(con, "wrong_answer_analysis", row["analysis_id"], row["version"]),
                                  previous[0] if previous else None)

    def write_analysis(self, source_id: str, analysis: dict, source_refs: list[str],
                       study_relations: list[str], idempotency_key: str,
                       expected_version: int, identity: ReportedIdentity | None = None) -> dict:
        if type(source_id) is not str or not SOURCE_ID.fullmatch(source_id) \
                or type(idempotency_key) is not str \
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", idempotency_key) \
                or type(expected_version) is not int or expected_version < 0:
            raise _invalid()
        body = self._validated_analysis(analysis)
        identity = identity or ReportedIdentity()
        if type(study_relations) is not list or len(study_relations) > 16 \
                or any(type(item) is not str for item in study_relations) \
                or len(set(study_relations)) != len(study_relations):
            raise _invalid()
        with closing(self.documents._connect(write=True)) as con:
            try:
                self._schema(con)
                con.commit()
                con.execute("BEGIN IMMEDIATE")
                source = con.execute("SELECT * FROM wrong_sources WHERE source_id = ?", (source_id,)).fetchone()
                if source is None:
                    raise GatewayError("RESOURCE_NOT_FOUND", "Wrong-answer source was not found")
                if type(source_refs) is not list or not 1 <= len(source_refs) <= 16 \
                        or any(type(ref) is not str or not (ASSET_URI.fullmatch(ref) or DOCUMENT_URI.fullmatch(ref))
                               for ref in source_refs) or len(set(source_refs)) != len(source_refs):
                    raise _invalid()
                payload = _canonical([source_id, body, source_refs, study_relations, expected_version,
                                      identity.reported_agent, identity.reported_client, identity.run_id])
                digest = hashlib.sha256(payload.encode()).hexdigest()
                legacy_digest = hashlib.sha256(_canonical(
                    [source_id, body, source_refs, study_relations, expected_version]
                ).encode()).hexdigest()
                prior = con.execute("SELECT * FROM wrong_analysis_requests WHERE idempotency_key = ?",
                                    (idempotency_key,)).fetchone()
                if prior:
                    legacy_replay = not any((identity.reported_agent, identity.reported_client, identity.run_id)) \
                        and prior["payload_sha256"] == legacy_digest
                    if prior["payload_sha256"] != digest and not legacy_replay:
                        raise GatewayError("CONFLICT", "Idempotency key conflicts with existing data")
                    row = con.execute("SELECT * FROM wrong_analyses WHERE analysis_id = ?",
                                      (prior["analysis_id"],)).fetchone()
                    con.commit()
                    previous = con.execute("SELECT analysis_id FROM wrong_analyses WHERE source_id=? AND version=?",
                                            (row["source_id"], row["version"] - 1)).fetchone()
                    return self._analysis(row, get_provenance(con, "wrong_answer_analysis", row["analysis_id"], row["version"]),
                                          previous[0] if previous else None)
                refs = self._existing_refs(source_refs, source["source_uri"])
                version = con.execute("SELECT COALESCE(MAX(version), 0) FROM wrong_analyses WHERE source_id = ?",
                                      (source_id,)).fetchone()[0]
                if version != expected_version:
                    raise GatewayError("CONFLICT", "Analysis version conflicts with existing data")
                analysis_id = "wrong-analysis://sha256/" + hashlib.sha256(
                    f"{source_id}\x00{version + 1}\x00{digest}".encode()).hexdigest()
                now = _timestamp()
                previous = con.execute("SELECT analysis_id FROM wrong_analyses WHERE source_id=? AND version=?",
                                       (source_id, version)).fetchone() if version else None
                con.execute("INSERT INTO wrong_analyses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (analysis_id, source_id, version + 1, _canonical(body),
                             _canonical(refs), _canonical(study_relations), 1,
                             "agent_supplied", now))
                con.execute("INSERT INTO wrong_analysis_requests VALUES (?, ?, ?)",
                            (idempotency_key, digest, analysis_id))
                previous_provenance = get_provenance(
                    con, "wrong_answer_analysis", previous[0], version
                ) if previous else None
                insert_provenance(
                    con, record_type="wrong_answer_analysis", record_id=analysis_id,
                    version=version + 1, data_origin="agent_generated", actor_type="external_client",
                    source_refs=refs, identity=identity,
                    supersedes_provenance_id=(previous_provenance["provenance_id"]
                                              if previous_provenance else None),
                )
                _audit(con, "save_wrong_answer_analysis", "wrong_answer_analysis", analysis_id)
                con.commit()
                row = con.execute("SELECT * FROM wrong_analyses WHERE analysis_id = ?", (analysis_id,)).fetchone()
                return self._analysis(row, get_provenance(con, "wrong_answer_analysis", analysis_id,
                                                          version + 1),
                                      previous[0] if previous else None)
            except sqlite3.Error:
                con.rollback()
                raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None
            except Exception:
                con.rollback()
                raise

    def bundle(self, source_id: str, limit: int = 20, offset: int = 0) -> tuple[dict, list[dict], int]:
        if type(source_id) is not str or not SOURCE_ID.fullmatch(source_id) \
                or type(limit) is not int or not 1 <= limit <= 20 \
                or type(offset) is not int or not 0 <= offset <= 1000:
            raise _invalid()
        with closing(self.documents._connect()) as con:
            if not self._has_table(con, "wrong_sources"):
                raise GatewayError("RESOURCE_NOT_FOUND", "Wrong-answer source was not found")
            source = con.execute("SELECT * FROM wrong_sources WHERE source_id = ?", (source_id,)).fetchone()
            if source is None:
                raise GatewayError("RESOURCE_NOT_FOUND", "Wrong-answer source was not found")
            total = con.execute("SELECT COUNT(*) FROM wrong_analyses WHERE source_id = ?", (source_id,)).fetchone()[0]
            rows = con.execute("SELECT * FROM wrong_analyses WHERE source_id = ? ORDER BY version DESC LIMIT ? OFFSET ?",
                               (source_id, limit, offset)).fetchall()
            analyses = []
            for row in rows:
                previous = con.execute("SELECT analysis_id FROM wrong_analyses WHERE source_id=? AND version=?",
                                       (source_id, row["version"] - 1)).fetchone()
                analyses.append(self._analysis(
                    row, get_provenance(con, "wrong_answer_analysis", row["analysis_id"], row["version"]),
                    previous[0] if previous else None,
                ))
            source_result = self._source(source)
            source_result["write_provenance"] = get_provenance(con, "wrong_answer_source", source_id)
            return source_result, analyses, total

    def projection_snapshot(self, operation: str, *, snapshot_token: str | None = None,
                            cursor: int = 0, source_id: str | None = None,
                            limit: int = _PROJECTION_PAGE_SIZE) -> dict:
        """Enumerate a bounded, append-only Wrong Answer watermark for projection only."""
        if type(operation) is not str or operation not in {"begin", "sources", "records"} \
                or type(cursor) is not int or cursor < 0 \
                or type(limit) is not int or not 1 <= limit <= _PROJECTION_PAGE_SIZE:
            raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")
        if operation == "begin":
            if snapshot_token is not None or cursor != 0 or source_id is not None:
                raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")
        elif type(snapshot_token) is not str:
            raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")
        if operation == "records" and (type(source_id) is not str or not SOURCE_ID.fullmatch(source_id)):
            raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")
        if operation != "records" and source_id is not None:
            raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")

        token_match = re.fullmatch(r"wrong-v1:(0|[1-9][0-9]{0,18}):(0|[1-9][0-9]{0,18})",
                                   snapshot_token or "")
        if operation != "begin" and token_match is None:
            raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")
        try:
            with closing(self.documents._connect()) as con:
                con.execute("BEGIN")
                if not self._has_table(con, "wrong_sources"):
                    return {"snapshot_token": "wrong-v1:0:0", "total_sources": 0,
                            "total_records": 0, "stored_payload_bytes": 0} \
                        if operation == "begin" else self._empty_page(
                                operation, snapshot_token, cursor)
                if not self._has_table(con, "wrong_analyses"):
                    raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
                if operation == "begin":
                    source_watermark = con.execute(
                        "SELECT COALESCE(MAX(rowid), 0) FROM wrong_sources"
                    ).fetchone()[0]
                    analysis_watermark = con.execute(
                        "SELECT COALESCE(MAX(rowid), 0) FROM wrong_analyses"
                    ).fetchone()[0]
                    total_sources = con.execute(
                        "SELECT COUNT(*) FROM wrong_sources WHERE rowid <= ?", (source_watermark,)
                    ).fetchone()[0]
                    total_analyses, analysis_bytes = con.execute(
                        "SELECT COUNT(*), COALESCE(SUM(length(CAST(body AS BLOB)) + "
                        "length(CAST(source_refs AS BLOB)) + length(CAST(study_relations AS BLOB))), 0) "
                        "FROM wrong_analyses WHERE rowid <= ?", (analysis_watermark,),
                    ).fetchone()
                    source_bytes = con.execute(
                        "SELECT COALESCE(SUM(length(CAST(question_text AS BLOB)) + "
                        "length(CAST(student_answer AS BLOB))), 0) FROM wrong_sources WHERE rowid <= ?",
                        (source_watermark,),
                    ).fetchone()[0]
                    if total_sources > _PROJECTION_MAX_RECORDS \
                            or total_analyses > _PROJECTION_MAX_RECORDS \
                            or source_bytes + analysis_bytes > _PROJECTION_MAX_BYTES:
                        raise GatewayError("PAYLOAD_TOO_LARGE", "Wrong Answer projection snapshot exceeds limits")
                    return {"snapshot_token": f"wrong-v1:{source_watermark}:{analysis_watermark}",
                            "total_sources": total_sources, "total_records": total_analyses,
                            "stored_payload_bytes": source_bytes + analysis_bytes}

                source_watermark = int(token_match.group(1))
                analysis_watermark = int(token_match.group(2))
                if operation == "sources":
                    if cursor > source_watermark:
                        raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot cursor")
                    if cursor and con.execute(
                        "SELECT 1 FROM wrong_sources WHERE rowid=? AND rowid <= ?",
                        (cursor, source_watermark),
                    ).fetchone() is None:
                        raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot cursor")
                    rows = con.execute(
                        "SELECT s.rowid AS projection_rowid, s.* FROM wrong_sources s "
                        "WHERE s.rowid > ? AND s.rowid <= ? ORDER BY s.rowid LIMIT ?",
                        (cursor, source_watermark, limit + 1),
                    ).fetchall()
                    page = rows[:limit]
                    sources = []
                    for row in page:
                        source = self._source(row)
                        source["write_provenance"] = get_provenance(con, "wrong_answer_source", source["source_id"])
                        source["analysis_count"] = con.execute(
                            "SELECT COUNT(*) FROM wrong_analyses WHERE source_id=? AND rowid <= ?",
                            (source["source_id"], analysis_watermark),
                        ).fetchone()[0]
                        sources.append(source)
                    next_cursor = page[-1]["projection_rowid"] if len(rows) > limit else None
                    return {"snapshot_token": snapshot_token, "sources": sources,
                            "next_cursor": next_cursor, "has_more": len(rows) > limit}

                source_exists = con.execute(
                    "SELECT 1 FROM wrong_sources WHERE source_id=? AND rowid <= ?",
                    (source_id, source_watermark),
                ).fetchone()
                if source_exists is None:
                    raise GatewayError("RESOURCE_NOT_FOUND", "Wrong-answer source was not found")
                max_version = con.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM wrong_analyses "
                    "WHERE source_id=? AND rowid <= ?", (source_id, analysis_watermark),
                ).fetchone()[0]
                if cursor > max_version:
                    raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot cursor")
                if cursor and con.execute(
                    "SELECT 1 FROM wrong_analyses WHERE source_id=? AND version=? AND rowid <= ?",
                    (source_id, cursor, analysis_watermark),
                ).fetchone() is None:
                    raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot cursor")
                total_for_source = con.execute(
                    "SELECT COUNT(*) FROM wrong_analyses WHERE source_id=? AND rowid <= ?",
                    (source_id, analysis_watermark),
                ).fetchone()[0]
                rows = con.execute(
                    "SELECT * FROM wrong_analyses WHERE source_id=? AND rowid <= ? AND version > ? "
                    "ORDER BY version LIMIT ?",
                    (source_id, analysis_watermark, cursor, limit + 1),
                ).fetchall()
                page = rows[:limit]
                analyses = []
                for row in page:
                    previous = con.execute(
                        "SELECT analysis_id FROM wrong_analyses WHERE source_id=? AND version=? "
                        "AND rowid <= ?", (source_id, row["version"] - 1, analysis_watermark),
                    ).fetchone()
                    analyses.append(self._analysis(
                        row, get_provenance(con, "wrong_answer_analysis", row["analysis_id"], row["version"]),
                        previous[0] if previous else None,
                    ))
                next_cursor = page[-1]["version"] if len(rows) > limit else None
                return {"snapshot_token": snapshot_token, "source_id": source_id,
                        "total_records": total_for_source, "analyses": analyses,
                        "next_cursor": next_cursor, "has_more": len(rows) > limit}
        except GatewayError:
            raise
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None

    @staticmethod
    def _empty_page(operation: str, snapshot_token: str | None, cursor: int) -> dict:
        if not re.fullmatch(r"wrong-v1:0:0", snapshot_token or ""):
            raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot token")
        if cursor != 0:
            raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot cursor")
        if operation == "sources":
            return {"snapshot_token": snapshot_token, "sources": [], "next_cursor": None,
                    "has_more": False}
        raise GatewayError("RESOURCE_NOT_FOUND", "Wrong-answer source was not found")

    def search(self, query: str, limit: int = 5, offset: int = 0) -> tuple[list[dict], int]:
        query = _text(query, 500).casefold()
        if type(limit) is not int or not 1 <= limit <= 20 \
                or type(offset) is not int or not 0 <= offset <= 1000:
            raise _invalid()
        self.documents._ready()
        if not self.documents.database_path.exists():
            return [], 0
        if not self.documents.database_path.is_file():
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
        with closing(self.documents._connect()) as con:
            if not self._has_table(con, "wrong_sources"):
                return [], 0
            rows = con.execute("SELECT * FROM wrong_sources ORDER BY created_at DESC, source_id LIMIT 2049").fetchall()
            if len(rows) > 2048:
                raise GatewayError("PAYLOAD_TOO_LARGE", "Too many wrong-answer candidates")
            matches = []
            for row in rows:
                latest = con.execute("SELECT body FROM wrong_analyses WHERE source_id = ? ORDER BY version DESC LIMIT 1",
                                     (row["source_id"],)).fetchone()
                haystack = " ".join((row["question_text"], row["student_answer"],
                                     latest["body"] if latest else "")).casefold()
                if all(word in haystack for word in query.split()):
                    matches.append(self._source(row))
            return matches[offset:offset + limit], len(matches)
