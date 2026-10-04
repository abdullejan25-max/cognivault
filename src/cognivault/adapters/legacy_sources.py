"""Restricted immutable UTF-8 source archives, distinct from raw History messages.

The local migration service supplies explicit bytes. No discovery, path inputs,
message inference, normalization, or Agent-facing import API exists here.
"""

import base64
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat

from .documents import _path_has_reparse_point
from .history import valid_logical_id, _normalize_timestamp
from ..contracts import GatewayError
from ..provenance import ReportedIdentity, ensure_provenance_schema, get_provenance, insert_provenance, utc_now


MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_FETCH_BYTES = 16384
_SEARCH_CHUNK = 65536
_TYPES = frozenset({"legacy_markdown", "codex_jsonl", "legacy_derived_fact", "legacy_wrong_answer_document"})
_RECORD_ID = re.compile(r"legacy-source:[0-9a-f]{64}\Z")
_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,254}\Z")


@dataclass(frozen=True)
class LegacySourceInput:
    source_id: str
    source_item_id: str
    source_type: str
    content: bytes
    source_hash: str
    source_order: int
    source_created_at: str | None = None
    source_refs: tuple[str, ...] = ()


def validate_input(item: LegacySourceInput) -> None:
    """Validate one complete source before snapshots or any business writes."""
    if type(item) is not LegacySourceInput or not valid_logical_id(item.source_id) \
            or type(item.source_item_id) is not str or _OPAQUE_ID.fullmatch(item.source_item_id) is None \
            or type(item.source_type) is not str or item.source_type not in _TYPES \
            or type(item.content) is not bytes \
            or type(item.source_order) is not int or not 0 <= item.source_order <= 9223372036854775807 \
            or type(item.source_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", item.source_hash):
        raise GatewayError("INVALID_ARGUMENT", "Invalid legacy source")
    if len(item.content) > MAX_SOURCE_BYTES:
        raise GatewayError("PAYLOAD_TOO_LARGE", "Legacy source exceeds limits")
    if hashlib.sha256(item.content).hexdigest() != item.source_hash:
        raise GatewayError("INVALID_ARGUMENT", "Invalid legacy source hash")
    # Validate incrementally to avoid an additional full decoded copy of a large source.
    import codecs
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        for start in range(0, len(item.content), _SEARCH_CHUNK):
            decoder.decode(item.content[start:start + _SEARCH_CHUNK])
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        raise GatewayError("INVALID_ARGUMENT", "Legacy source must be UTF-8") from None
    if item.source_created_at is not None:
        _normalize_timestamp(item.source_created_at)  # Validate only; retain the proven original value.
    if type(item.source_refs) is not tuple or len(item.source_refs) > 70 \
            or any(type(ref) is not str or not re.fullmatch(r"asset://sha256/[0-9a-f]{64}", ref)
                   for ref in item.source_refs) or len(set(item.source_refs)) != len(item.source_refs):
        raise GatewayError("INVALID_ARGUMENT", "Invalid legacy source references")


def source_record_id(item: LegacySourceInput) -> str:
    return "legacy-source:" + hashlib.sha256(
        (item.source_id + "\x00" + item.source_item_id).encode("utf-8")
    ).hexdigest()


class SQLiteLegacySourceStore:
    def __init__(self, database_path: Path, *, reported_identity: ReportedIdentity | None = None):
        self.database_path = Path(database_path)
        if not self.database_path.is_absolute():
            raise ValueError("Legacy source database path must be absolute")
        if reported_identity is not None and type(reported_identity) is not ReportedIdentity:
            raise ValueError("Invalid reported importer identity")
        self.reported_identity = reported_identity

    def _connect(self, *, create=False, write=False):
        if _path_has_reparse_point(self.database_path) \
                or _path_has_reparse_point(self.database_path.parent) \
                or not self.database_path.parent.is_dir():
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
        try:
            for suffix in ("", "-wal", "-shm", "-journal"):
                member = Path(str(self.database_path) + suffix)
                if _path_has_reparse_point(member):
                    raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
                if member.exists():
                    info = member.stat(follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
        except OSError:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None
        if not create and not self.database_path.is_file():
            raise GatewayError("HISTORY_UNAVAILABLE", "History is unavailable")
        try:
            connection = sqlite3.connect(self.database_path if create else
                self.database_path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=not create)
            connection.row_factory = sqlite3.Row
            return connection
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None

    @staticmethod
    def _exists(connection):
        return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                  "AND name='legacy_source_records'").fetchone() is not None

    def initialize(self) -> None:
        try:
            with closing(self._connect(create=True)) as connection, connection:
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS legacy_source_records(
                        source_record_id TEXT PRIMARY KEY,
                        source_id TEXT NOT NULL, source_item_id TEXT NOT NULL,
                        source_type TEXT NOT NULL, content BLOB NOT NULL,
                        source_hash TEXT NOT NULL, byte_count INTEGER NOT NULL,
                        source_order INTEGER NOT NULL, source_created_at TEXT,
                        imported_at TEXT NOT NULL, import_batch_id TEXT NOT NULL,
                        source_refs TEXT NOT NULL,
                        UNIQUE(source_id, source_item_id));
                    CREATE INDEX IF NOT EXISTS legacy_source_order
                        ON legacy_source_records(source_id, source_order, source_item_id);
                    CREATE TRIGGER IF NOT EXISTS legacy_source_no_update
                        BEFORE UPDATE ON legacy_source_records
                        BEGIN SELECT RAISE(ABORT, 'immutable legacy source'); END;
                    CREATE TRIGGER IF NOT EXISTS legacy_source_no_delete
                        BEFORE DELETE ON legacy_source_records
                        BEGIN SELECT RAISE(ABORT, 'immutable legacy source'); END;
                """)
                ensure_provenance_schema(connection, "legacy_sources", backfill=False)
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None

    def import_batch(self, items: list[LegacySourceInput], *, import_batch_id: str) -> tuple[str, ...]:
        if type(items) is not list or not 1 <= len(items) <= 16 or not valid_logical_id(import_batch_id):
            raise GatewayError("INVALID_ARGUMENT", "Invalid legacy source batch")
        for item in items:
            validate_input(item)
        ids = tuple(source_record_id(item) for item in items)
        unique = {}
        for record_id, item in zip(ids, items):
            if record_id in unique and unique[record_id] != item:
                raise GatewayError("CONFLICT", "Legacy source conflicts with existing data")
            unique[record_id] = item
        try:
            with closing(self._connect(write=True)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                if not self._exists(connection):
                    raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
                new = []
                for record_id, item in unique.items():
                    existing = connection.execute(
                        "SELECT source_type, source_hash, byte_count, source_order, source_created_at, source_refs "
                        "FROM legacy_source_records WHERE source_record_id=?", (record_id,),
                    ).fetchone()
                    expected = (item.source_type, item.source_hash, len(item.content),
                                item.source_order, item.source_created_at,
                                json.dumps(list(item.source_refs), separators=(",", ":")))
                    if existing is not None:
                        if tuple(existing) != expected:
                            raise GatewayError("CONFLICT", "Legacy source conflicts with existing data")
                        persisted = connection.execute(
                            self._SELECT + " WHERE source_record_id=?", (record_id,)).fetchone()
                        self._metadata(connection, persisted)
                        self._verify_blob(connection, persisted, item.source_hash, len(item.content))
                    else:
                        new.append((record_id, item))
                stored = connection.execute("SELECT COALESCE(SUM(byte_count),0) FROM legacy_source_records").fetchone()[0]
                if stored + sum(len(item.content) for _, item in new) > MAX_TOTAL_BYTES:
                    raise GatewayError("PAYLOAD_TOO_LARGE", "Legacy sources exceed limits")
                imported_at = utc_now()
                for record_id, item in new:
                    connection.execute("INSERT INTO legacy_source_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (record_id, item.source_id, item.source_item_id, item.source_type, item.content,
                         item.source_hash, len(item.content), item.source_order, item.source_created_at,
                         imported_at, import_batch_id, json.dumps(list(item.source_refs), separators=(",", ":"))))
                    insert_provenance(connection, record_type="legacy_source", record_id=record_id,
                        version=1, data_origin="legacy_import", actor_type="importer",
                        source_refs=[f"legacy-source://{item.source_id}/{record_id.split(':')[1]}", *item.source_refs],
                        identity=self.reported_identity, original_created_at=item.source_created_at,
                        imported_at=imported_at, source_system=item.source_type,
                        import_batch_id=import_batch_id, legacy_status="imported")
            return ids
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None

    def list_sources(self) -> tuple[dict, ...]:
        try:
            with closing(self._connect()) as connection:
                if not self._exists(connection):
                    return ()
                rows = connection.execute("SELECT source_id, source_type, COUNT(*) AS source_record_count, "
                    "SUM(byte_count) AS total_bytes FROM legacy_source_records GROUP BY source_id, source_type "
                    "ORDER BY source_id, source_type LIMIT 1001").fetchall()
                if len(rows) > 1000:
                    raise GatewayError("PAYLOAD_TOO_LARGE", "Legacy source listing exceeds limits")
                if any(not valid_logical_id(row["source_id"]) or row["source_type"] not in _TYPES for row in rows):
                    raise GatewayError("CONFLICT", "Legacy source integrity verification failed")
                return tuple(dict(row) for row in rows)
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None

    @staticmethod
    def _metadata(connection, row):
        result = dict(row)
        result.pop("blob_rowid", None)
        if not valid_logical_id(row["source_id"]) or type(row["source_item_id"]) is not str \
                or _OPAQUE_ID.fullmatch(row["source_item_id"]) is None \
                or row["source_type"] not in _TYPES \
                or type(row["source_order"]) is not int or not 0 <= row["source_order"] <= 9223372036854775807 \
                or type(row["byte_count"]) is not int or not 0 <= row["byte_count"] <= MAX_SOURCE_BYTES \
                or type(row["source_hash"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", row["source_hash"]) \
                or not valid_logical_id(row["import_batch_id"]):
            raise GatewayError("CONFLICT", "Legacy source integrity verification failed")
        expected_id = "legacy-source:" + hashlib.sha256(
            (row["source_id"] + "\x00" + row["source_item_id"]).encode("utf-8")).hexdigest()
        if row["source_record_id"] != expected_id:
            raise GatewayError("CONFLICT", "Legacy source integrity verification failed")
        try:
            _normalize_timestamp(row["imported_at"])
            if row["source_created_at"] is not None:
                _normalize_timestamp(row["source_created_at"])
        except GatewayError:
            raise GatewayError("CONFLICT", "Legacy source integrity verification failed") from None
        try:
            refs = json.loads(result["source_refs"])
            valid_refs = type(refs) is list and len(refs) <= 70 and all(
                type(ref) is str and re.fullmatch(r"asset://sha256/[0-9a-f]{64}", ref) for ref in refs)
            if not valid_refs or len(set(refs)) != len(refs):
                raise ValueError
        except (TypeError, ValueError):
            raise GatewayError("CONFLICT", "Legacy source integrity verification failed") from None
        result["source_refs"] = refs
        try:
            provenance = get_provenance(connection, "legacy_source", row["source_record_id"], 1)
        except (TypeError, ValueError):
            raise GatewayError("CONFLICT", "Legacy source integrity verification failed") from None
        expected_refs = [f"legacy-source://{row['source_id']}/{row['source_record_id'].split(':')[1]}", *refs]
        if provenance is None or provenance["data_origin"] != "legacy_import" \
                or provenance["actor_type"] != "importer" or provenance["source_refs"] != expected_refs \
                or provenance["imported_at"] != row["imported_at"] \
                or provenance["original_created_at"] != row["source_created_at"] \
                or provenance["source_system"] != row["source_type"] \
                or provenance["import_batch_id"] != row["import_batch_id"] \
                or provenance["legacy_status"] != "imported":
            raise GatewayError("CONFLICT", "Legacy source integrity verification failed")
        try:
            identity = ReportedIdentity.from_value({key: provenance[key] for key in
                ("reported_agent", "reported_client", "run_id")})
            _normalize_timestamp(provenance["recorded_at"])
        except GatewayError:
            raise GatewayError("CONFLICT", "Legacy source integrity verification failed") from None
        trust = "reported" if any((identity.reported_agent, identity.reported_client, identity.run_id)) else "unavailable"
        if provenance["identity_trust"] != trust or provenance["supersedes_provenance_id"] is not None \
                or type(provenance["provenance_id"]) is not str \
                or not re.fullmatch(r"provenance:[0-9a-f]{32}", provenance["provenance_id"]):
            raise GatewayError("CONFLICT", "Legacy source integrity verification failed")
        result.update(author=None, role=None, raw_hash=row["source_hash"],
            data_origin="legacy_import", record_kind="legacy_source",
            write_provenance=provenance)
        return result

    _SELECT = ("SELECT rowid AS blob_rowid, source_record_id, source_id, source_item_id, source_type, "
               "source_hash, byte_count, source_order, source_created_at, imported_at, import_batch_id, source_refs "
               "FROM legacy_source_records")

    def projection_snapshot(self, operation: str, *, snapshot_token: str | None = None,
                            cursor: int = 0, limit: int = 20) -> dict:
        """Bounded metadata-only enumeration at an append-only source watermark."""
        if operation not in {"begin", "records"} or type(cursor) is not int or cursor < 0 \
                or type(limit) is not int or not 1 <= limit <= 20:
            raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN")
                exists = self._exists(connection)
                maximum = connection.execute("SELECT COALESCE(MAX(rowid),0) FROM legacy_source_records").fetchone()[0] if exists else 0
                if operation == "begin":
                    if snapshot_token is not None or cursor:
                        raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")
                    count = connection.execute("SELECT COUNT(*) FROM legacy_source_records").fetchone()[0] if exists else 0
                    if count > 10000:
                        raise GatewayError("PAYLOAD_TOO_LARGE", "Source projection exceeds limits")
                    return {"snapshot_token": f"legacy-v1:{maximum}:0", "total_sources": count,
                            "total_records": count, "stored_payload_bytes": 0}
                if type(snapshot_token) is not str or not re.fullmatch(r"legacy-v1:(?:0|[1-9][0-9]{0,18}):0", snapshot_token):
                    raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")
                watermark = int(snapshot_token.split(':')[1])
                if watermark > maximum or cursor > watermark:
                    raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot cursor")
                rows = connection.execute(self._SELECT + " WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?",
                                          (cursor, watermark, limit + 1)).fetchall() if exists else []
                page = rows[:limit]
                total = connection.execute("SELECT COUNT(*) FROM legacy_source_records WHERE rowid<=?", (watermark,)).fetchone()[0] if exists else 0
                return {"snapshot_token": snapshot_token, "records": [self._metadata(connection, row) for row in page],
                        "total_records": total, "has_more": len(rows) > limit,
                        "next_cursor": page[-1]["blob_rowid"] if len(rows) > limit else None}
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None

    def fetch(self, source_record_id: str, offset: int = 0, length: int = MAX_FETCH_BYTES) -> dict:
        if type(source_record_id) is not str or _RECORD_ID.fullmatch(source_record_id) is None \
                or type(offset) is not int or not 0 <= offset <= MAX_SOURCE_BYTES \
                or type(length) is not int or not 1 <= length <= MAX_FETCH_BYTES:
            raise GatewayError("INVALID_ARGUMENT", "Invalid legacy source range")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN")
                row = connection.execute(self._SELECT + " WHERE source_record_id=?", (source_record_id,)).fetchone() \
                    if self._exists(connection) else None
                if row is None:
                    raise GatewayError("RESOURCE_NOT_FOUND", "Legacy source was not found")
                if offset > row["byte_count"]:
                    raise GatewayError("INVALID_ARGUMENT", "Invalid legacy source range")
                with connection.blobopen("legacy_source_records", "content", row["blob_rowid"], readonly=True) as blob:
                    blob.seek(offset)
                    content = blob.read(length)
                return {**self._metadata(connection, row), "original_byte_range": {
                    "offset": offset, "length": len(content),
                    "content_base64": base64.b64encode(content).decode("ascii"),
                    "has_more": offset + len(content) < row["byte_count"]}}
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None

    def verify_record(self, source_record_id: str, *, expected_sha256: str, expected_bytes: int) -> dict:
        """Internal read-back gate; hash original BLOB in bounded chunks without returning content."""
        if type(source_record_id) is not str or _RECORD_ID.fullmatch(source_record_id) is None \
                or type(expected_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) \
                or type(expected_bytes) is not int or not 0 <= expected_bytes <= MAX_SOURCE_BYTES:
            raise GatewayError("INVALID_ARGUMENT", "Invalid legacy source verification")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN")
                row = connection.execute(self._SELECT + " WHERE source_record_id=?", (source_record_id,)).fetchone() \
                    if self._exists(connection) else None
                if row is None:
                    raise GatewayError("RESOURCE_NOT_FOUND", "Legacy source was not found")
                self._verify_blob(connection, row, expected_sha256, expected_bytes)
                return self._metadata(connection, row)
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None

    @staticmethod
    def _verify_blob(connection, row, expected_sha256, expected_bytes):
        digest = hashlib.sha256()
        count = 0
        with connection.blobopen("legacy_source_records", "content", row["blob_rowid"], readonly=True) as blob:
            while chunk := blob.read(1024 * 1024):
                digest.update(chunk)
                count += len(chunk)
        if count != expected_bytes or count != row["byte_count"] \
                or digest.hexdigest() != expected_sha256 or expected_sha256 != row["source_hash"]:
            raise GatewayError("CONFLICT", "Legacy source integrity verification failed")

    def search(self, query: str, source_id: str | None = None, limit: int = 5, offset: int = 0) -> dict:
        if type(query) is not str or not 1 <= len(query.strip()) <= 500 or len(query) > 500 or "\x00" in query \
                or (source_id is not None and not valid_logical_id(source_id)) \
                or type(limit) is not int or not 1 <= limit <= 20 \
                or type(offset) is not int or not 0 <= offset <= 1000:
            raise GatewayError("INVALID_ARGUMENT", "Invalid legacy source search")
        needle = query.encode("utf-8")
        result = {"total": 0, "limit": limit, "offset": offset, "results": []}
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN")
                if not self._exists(connection):
                    return result
                rows = connection.execute(self._SELECT + (" WHERE source_id=?" if source_id else "") +
                    " ORDER BY source_id, source_order, source_item_id", (source_id,) if source_id else ())
                for row in rows:
                    with connection.blobopen("legacy_source_records", "content", row["blob_rowid"], readonly=True) as blob:
                        tail = b""
                        while chunk := blob.read(_SEARCH_CHUNK):
                            window = tail + chunk
                            found = window.find(needle)
                            if found >= 0:
                                if offset <= result["total"] < offset + limit:
                                    snippet = window[max(0, found - 240):found + len(needle) + 2000].decode("utf-8", errors="ignore")[:500]
                                    result["results"].append({**self._metadata(connection, row), "snippet": snippet})
                                result["total"] += 1
                                break
                            tail = window[-max(len(needle) - 1, 240):]
            return result
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None
