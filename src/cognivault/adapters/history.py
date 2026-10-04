"""Explicit, deterministic private History storage; no source discovery."""

from datetime import datetime, timezone
from difflib import SequenceMatcher
from contextlib import closing
import hashlib
from pathlib import Path
import re
import sqlite3
import unicodedata
from uuid import uuid4

from ..contracts import (
    BackendHealth, GatewayError, HistoryImportItem, HistoryItem, HistoryPage, HistorySource,
)
from .documents import _path_has_reparse_point
from ..provenance import ensure_provenance_schema, get_provenance, insert_provenance, utc_now


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_ITEM_ID = re.compile(r"history:[0-9a-f]{64}\Z")
_ROLES = frozenset({"user", "assistant", "system", "tool"})
_SEARCH_CANDIDATE_LIMIT = 2048
_PROJECTION_MAX_RECORDS = 10_000
_PROJECTION_MAX_BYTES = 32 * 1024 * 1024
_PROJECTION_PAGE_SIZE = 20


def valid_logical_id(value: str) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def _normalize_content(value: str) -> str:
    if type(value) is not str or not value or len(value) > 100_000 or "\x00" in value:
        raise GatewayError("INVALID_ARGUMENT", "Invalid History item")
    normalized = unicodedata.normalize("NFC", value)
    normalized = " ".join(normalized.split())
    if not normalized:
        raise GatewayError("INVALID_ARGUMENT", "Invalid History item")
    return normalized


def _normalize_timestamp(value: str) -> str:
    if type(value) is not str or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})",
        value,
    ) is None:
        raise GatewayError("INVALID_ARGUMENT", "Invalid History item")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise GatewayError("INVALID_ARGUMENT", "Invalid History item") from None
    if parsed.tzinfo is None:
        raise GatewayError("INVALID_ARGUMENT", "Invalid History item")
    utc = parsed.astimezone(timezone.utc)
    precision = "microseconds" if utc.microsecond else "seconds"
    return utc.isoformat(timespec=precision).replace("+00:00", "Z")


def _write_audit(connection: sqlite3.Connection, operation: str,
                 resource_type: str, resource_id: str) -> None:
    occurred_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    connection.execute(
        "INSERT INTO operation_audit(operation, resource_type, resource_id, occurred_at, outcome) "
        "VALUES (?, ?, ?, ?, 'success')", (operation, resource_type, resource_id, occurred_at),
    )


class SQLiteHistoryBackend:
    """Stores only explicitly registered sources and explicitly supplied items."""

    def __init__(self, database_path: Path) -> None:
        path = Path(database_path)
        if not path.is_absolute():
            raise ValueError("History database path must be absolute")
        self.database_path = path

    def probe(self) -> BackendHealth:
        ready = self.database_path.is_file() \
            and not _path_has_reparse_point(self.database_path) \
            and not _path_has_reparse_point(self.database_path.parent)
        return BackendHealth("sqlite", "ready" if ready else "unavailable")

    def _connect(self, *, create: bool = False, write: bool = False) -> sqlite3.Connection:
        if _path_has_reparse_point(self.database_path) or _path_has_reparse_point(self.database_path.parent):
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
        if not self.database_path.is_file() and not create:
            raise GatewayError("HISTORY_UNAVAILABLE", "History is unavailable")
        if not self.database_path.parent.is_dir():
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
        try:
            if create:
                connection = sqlite3.connect(self.database_path)
            else:
                mode = "rw" if write else "ro"
                connection = sqlite3.connect(self.database_path.as_uri() + f"?mode={mode}", uri=True)
            connection.row_factory = sqlite3.Row
            return connection
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS history_sources (
                source_id TEXT PRIMARY KEY, label TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history_content (
                sha256 TEXT PRIMARY KEY, content TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history_items (
                item_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES history_sources(source_id),
                source_item_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL,
                content_sha256 TEXT NOT NULL REFERENCES history_content(sha256),
                imported_at TEXT,
                source_system TEXT,
                import_batch_id TEXT,
                UNIQUE(source_id, source_item_id)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(item_id UNINDEXED, content);
            CREATE TABLE IF NOT EXISTS operation_audit(
                audit_id INTEGER PRIMARY KEY, operation TEXT NOT NULL, resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL, occurred_at TEXT NOT NULL, outcome TEXT NOT NULL);
        """)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(history_items)")}
        for name in ("imported_at", "source_system", "import_batch_id"):
            if name not in columns:
                connection.execute(f"ALTER TABLE history_items ADD COLUMN {name} TEXT")
        ensure_provenance_schema(connection, "history")

    def register_source(self, source_id: str, label: str) -> None:
        if not valid_logical_id(source_id) or type(label) is not str or not 1 <= len(label) <= 120 \
                or any(ord(char) < 32 or char in "/\\" for char in label):
            raise GatewayError("INVALID_ARGUMENT", "Invalid History source")
        connection = self._connect(create=True)
        try:
            self._create_schema(connection)
            with connection:
                existing = connection.execute(
                    "SELECT label FROM history_sources WHERE source_id = ?", (source_id,)
                ).fetchone()
                if existing is not None and existing["label"] != label:
                    raise GatewayError("CONFLICT", "History source conflicts with existing data")
                connection.execute(
                    "INSERT OR IGNORE INTO history_sources(source_id, label) VALUES (?, ?)",
                    (source_id, label),
                )
                if existing is None:
                    insert_provenance(connection, record_type="history_source", record_id=source_id,
                                      version=1, data_origin="imported", actor_type="importer",
                                      legacy_status="imported")
                    _write_audit(connection, "register_history_source", "history_source", source_id)
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None
        finally:
            connection.close()

    def initialize(self) -> None:
        """Explicit local setup of an empty store, without inventing a source."""
        connection = self._connect(create=True)
        try:
            self._create_schema(connection)
            connection.commit()
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None
        finally:
            connection.close()

    def import_items(self, source_id: str, items: list[HistoryImportItem], *,
                     source_system: str | None = None,
                     import_batch_id: str | None = None) -> tuple[str, ...]:
        if not valid_logical_id(source_id) or type(items) is not list or not 1 <= len(items) <= 500:
            raise GatewayError("INVALID_ARGUMENT", "Invalid History import")
        if source_system is not None and (type(source_system) is not str or
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}", source_system)):
            raise GatewayError("INVALID_ARGUMENT", "Invalid History import source")
        if import_batch_id is not None and not valid_logical_id(import_batch_id):
            raise GatewayError("INVALID_ARGUMENT", "Invalid History import batch")
        import_batch_id = import_batch_id or "batch-" + uuid4().hex
        imported_at = utc_now()
        prepared = []
        for item in items:
            if type(item) is not HistoryImportItem or not valid_logical_id(item.source_item_id) \
                    or not valid_logical_id(item.conversation_id) or item.role not in _ROLES:
                raise GatewayError("INVALID_ARGUMENT", "Invalid History item")
            content = _normalize_content(item.content)
            created_at = _normalize_timestamp(item.created_at)
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            item_id = "history:" + hashlib.sha256(
                (source_id + "\x00" + item.source_item_id).encode("utf-8")
            ).hexdigest()
            prepared.append((item_id, source_id, item.source_item_id, item.conversation_id,
                             item.role, created_at, digest, content))
        connection = self._connect(write=True)
        try:
            if connection.execute(
                "SELECT 1 FROM history_sources WHERE source_id = ?", (source_id,)
            ).fetchone() is None:
                raise GatewayError("RESOURCE_NOT_FOUND", "History source was not found")
            self._create_schema(connection)
            with connection:
                for row in prepared:
                    item_id, _, _, conversation_id, role, created_at, digest, content = row
                    existing = connection.execute(
                        "SELECT conversation_id, role, created_at, content_sha256 "
                        "FROM history_items WHERE item_id = ?", (item_id,)
                    ).fetchone()
                    if existing is not None:
                        if tuple(existing) != (conversation_id, role, created_at, digest):
                            raise GatewayError("CONFLICT", "History item conflicts with existing data")
                        continue
                    connection.execute(
                        "INSERT OR IGNORE INTO history_content(sha256, content) VALUES (?, ?)",
                        (digest, content),
                    )
                    connection.execute(
                        "INSERT INTO history_items(item_id, source_id, source_item_id, conversation_id, role, "
                        "created_at, content_sha256, imported_at, source_system, import_batch_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (*row[:7], imported_at, source_system, import_batch_id),
                    )
                    connection.execute(
                        "INSERT INTO history_fts(item_id, content) VALUES (?, ?)",
                        (item_id, content),
                    )
                    insert_provenance(
                        connection, record_type="history_item", record_id=item_id, version=1,
                        data_origin="imported", actor_type="importer",
                        source_refs=[f"history-source://{source_id}"], original_created_at=created_at,
                        imported_at=imported_at, source_system=source_system,
                        import_batch_id=import_batch_id, legacy_status="imported",
                    )
                    _write_audit(connection, "import_history_item", "history_item", item_id)
            return tuple(row[0] for row in prepared)
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None
        finally:
            connection.close()

    def list_sources(self) -> tuple[HistorySource, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT s.source_id, s.label, COUNT(i.item_id) AS item_count "
                "FROM history_sources s LEFT JOIN history_items i USING(source_id) "
                "GROUP BY s.source_id ORDER BY s.source_id"
            ).fetchall()
            return tuple(HistorySource(*row) for row in rows)

    def projection_snapshot(self, operation: str, *, snapshot_token: str | None = None,
                            cursor: int = 0, source_id: str | None = None,
                            limit: int = _PROJECTION_PAGE_SIZE) -> dict:
        """Enumerate a bounded, append-only History watermark for projection only."""
        if type(operation) is not str or operation not in {"begin", "sources", "records"} \
                or type(cursor) is not int or cursor < 0 \
                or type(limit) is not int or not 1 <= limit <= _PROJECTION_PAGE_SIZE:
            raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")
        if operation == "begin":
            if snapshot_token is not None or cursor != 0 or source_id is not None:
                raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")
        elif type(snapshot_token) is not str:
            raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")
        if operation == "records" and not valid_logical_id(source_id):
            raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")
        if operation != "records" and source_id is not None:
            raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")

        token_match = re.fullmatch(r"history-v1:(0|[1-9][0-9]{0,18}):(0|[1-9][0-9]{0,18})",
                                   snapshot_token or "")
        if operation != "begin" and token_match is None:
            raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN")
                if operation == "begin":
                    source_watermark = connection.execute(
                        "SELECT COALESCE(MAX(rowid), 0) FROM history_sources"
                    ).fetchone()[0]
                    item_watermark = connection.execute(
                        "SELECT COALESCE(MAX(rowid), 0) FROM history_items"
                    ).fetchone()[0]
                    total_sources = connection.execute(
                        "SELECT COUNT(*) FROM history_sources WHERE rowid <= ?", (source_watermark,)
                    ).fetchone()[0]
                    total_records, input_bytes = connection.execute(
                        "SELECT COUNT(*), COALESCE(SUM(length(CAST(c.content AS BLOB))), 0) "
                        "FROM history_items i JOIN history_content c ON c.sha256=i.content_sha256 "
                        "WHERE i.rowid <= ?", (item_watermark,),
                    ).fetchone()
                    if total_sources + total_records > _PROJECTION_MAX_RECORDS \
                            or input_bytes > _PROJECTION_MAX_BYTES:
                        raise GatewayError("PAYLOAD_TOO_LARGE", "History projection snapshot exceeds limits")
                    return {
                        "snapshot_token": f"history-v1:{source_watermark}:{item_watermark}",
                        "total_sources": total_sources,
                        "total_records": total_records,
                        "stored_payload_bytes": input_bytes,
                    }

                source_watermark = int(token_match.group(1))
                item_watermark = int(token_match.group(2))
                if operation == "sources":
                    if cursor > source_watermark:
                        raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot cursor")
                    if cursor and connection.execute(
                        "SELECT 1 FROM history_sources WHERE rowid=? AND rowid <= ?", (cursor, source_watermark)
                    ).fetchone() is None:
                        raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot cursor")
                    rows = connection.execute(
                        "SELECT s.rowid AS projection_rowid, s.source_id, s.label "
                        "FROM history_sources s WHERE s.rowid > ? AND s.rowid <= ? "
                        "ORDER BY s.rowid LIMIT ?",
                        (cursor, source_watermark, limit + 1),
                    ).fetchall()
                    page = rows[:limit]
                    sources = []
                    for row in page:
                        count = connection.execute(
                            "SELECT COUNT(*) FROM history_items WHERE source_id=? AND rowid <= ?",
                            (row["source_id"], item_watermark),
                        ).fetchone()[0]
                        sources.append({"source_id": row["source_id"], "label": row["label"],
                                        "item_count": count})
                    next_cursor = page[-1]["projection_rowid"] if len(rows) > limit else None
                    return {"snapshot_token": snapshot_token, "sources": sources,
                            "next_cursor": next_cursor, "has_more": len(rows) > limit}

                if cursor > item_watermark:
                    raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot cursor")
                if connection.execute(
                    "SELECT 1 FROM history_sources WHERE source_id=? AND rowid <= ?",
                    (source_id, source_watermark),
                ).fetchone() is None:
                    raise GatewayError("RESOURCE_NOT_FOUND", "History source was not found")
                if cursor and connection.execute(
                    "SELECT 1 FROM history_items WHERE source_id=? AND rowid=? AND rowid <= ?",
                    (source_id, cursor, item_watermark),
                ).fetchone() is None:
                    raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot cursor")
                total_for_source = connection.execute(
                    "SELECT COUNT(*) FROM history_items WHERE source_id=? AND rowid <= ?",
                    (source_id, item_watermark),
                ).fetchone()[0]
                rows = connection.execute(
                    "SELECT i.rowid AS projection_rowid, i.*, c.content FROM history_items i "
                    "JOIN history_content c ON c.sha256=i.content_sha256 "
                    "WHERE i.source_id=? AND i.rowid > ? AND i.rowid <= ? "
                    "ORDER BY i.rowid LIMIT ?",
                    (source_id, cursor, item_watermark, limit + 1),
                ).fetchall()
                page = rows[:limit]
                next_cursor = page[-1]["projection_rowid"] if len(rows) > limit else None
                return {"snapshot_token": snapshot_token, "source_id": source_id,
                        "total_records": total_for_source,
                        "items": [self._item(row, connection) for row in page],
                        "next_cursor": next_cursor, "has_more": len(rows) > limit}
        except GatewayError:
            raise
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None

    @staticmethod
    def _item(row: sqlite3.Row, connection: sqlite3.Connection) -> HistoryItem:
        keys = set(row.keys())
        return HistoryItem(*(row[name] for name in (
            "item_id", "source_id", "source_item_id", "conversation_id", "role",
            "created_at", "content_sha256", "content",
        )), row["imported_at"] if "imported_at" in keys else None,
            row["source_system"] if "source_system" in keys else None,
            row["import_batch_id"] if "import_batch_id" in keys else None,
            get_provenance(connection, "history_item", row["item_id"]))

    def fetch(self, item_id: str) -> HistoryItem | None:
        if type(item_id) is not str or _ITEM_ID.fullmatch(item_id) is None:
            raise GatewayError("INVALID_ARGUMENT", "Invalid History item ID")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT i.*, c.content FROM history_items i "
                "JOIN history_content c ON c.sha256 = i.content_sha256 WHERE i.item_id = ?",
                (item_id,),
            ).fetchone()
            return self._item(row, connection) if row is not None else None

    def search(self, query: str, *, source_id: str | None = None,
               conversation_id: str | None = None, limit: int = 5,
               offset: int = 0) -> HistoryPage:
        if type(query) is not str or not 1 <= len(query.strip()) <= 500 or "\x00" in query \
                or type(limit) is not int or not 1 <= limit <= 20 \
                or type(offset) is not int or not 0 <= offset <= 1000 \
                or (source_id is not None and not valid_logical_id(source_id)) \
                or (conversation_id is not None and not valid_logical_id(conversation_id)):
            raise GatewayError("INVALID_ARGUMENT", "Invalid History search")
        words = re.findall(r"\w+", query.casefold())
        if not words:
            raise GatewayError("INVALID_ARGUMENT", "Invalid History search")
        filters = []
        params = []
        if source_id is not None:
            filters.append("i.source_id = ?")
            params.append(source_id)
        if conversation_id is not None:
            filters.append("i.conversation_id = ?")
            params.append(conversation_id)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with closing(self._connect()) as connection:
            fts_query = " AND ".join('"' + word.replace('"', '') + '"*' for word in words)
            fts_where = " WHERE history_fts MATCH ?"
            if filters:
                fts_where += " AND " + " AND ".join(filters)
            matches = connection.execute(
                "SELECT i.*, c.content FROM history_fts "
                "JOIN history_items i ON i.item_id = history_fts.item_id "
                "JOIN history_content c ON c.sha256 = i.content_sha256" + fts_where +
                " ORDER BY i.created_at DESC, i.item_id ASC LIMIT ?",
                [fts_query, *params, _SEARCH_CANDIDATE_LIMIT + 1],
            ).fetchall()
            if len(matches) > _SEARCH_CANDIDATE_LIMIT:
                raise GatewayError("PAYLOAD_TOO_LARGE", "History search has too many candidates")
            if not matches:
                rows = connection.execute(
                    "SELECT i.*, c.content FROM history_items i "
                    "JOIN history_content c ON c.sha256 = i.content_sha256" + where +
                    " ORDER BY i.created_at DESC, i.item_id ASC LIMIT ?",
                    [*params, _SEARCH_CANDIDATE_LIMIT + 1],
                ).fetchall()
                if len(rows) > _SEARCH_CANDIDATE_LIMIT:
                    raise GatewayError("PAYLOAD_TOO_LARGE", "History search has too many candidates")
                matches = [row for row in rows if all(
                    any(SequenceMatcher(None, word, token).ratio() >= 0.78
                        for token in re.findall(r"\w+", row["content"].casefold()))
                    for word in words
                )]
            total = len(matches)
            return HistoryPage(tuple(self._item(row, connection) for row in matches[offset:offset + limit]),
                               total, offset + limit < total)


class NotConfiguredHistoryBackend:
    def probe(self) -> BackendHealth:
        return BackendHealth(backend="not_configured", status="not_configured")
