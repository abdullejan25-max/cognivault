"""Read-only scanners for explicitly supplied legacy roots."""

from contextlib import closing, contextmanager
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import BinaryIO, Iterable, Iterator

from ..adapters.documents import _opened_file_path, _path_has_reparse_point, _stream_signature
from .planner import SourceRecord


_FRONTMATTER = re.compile(rb"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
_FIELD = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*?)\s*$")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_LEGACY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,255}\Z")
_WRONG_ANSWER_WORDS = re.compile(r"wrong[ _-]*answers?", re.IGNORECASE)
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"})
_MAX_NATIVE_FACT_BYTES = 1_048_576
_MAX_SQLITE_SNAPSHOT_BYTES = 512 * 1024 * 1024
_READ_CHUNK_BYTES = 1_048_576
_FRONTMATTER_CAPTURE_BYTES = 32_770
_MAX_INVENTORY_FILES = 250_000
_MAX_INVENTORY_SOURCE_BYTES = 512 * 1024 * 1024 * 1024
_MAX_ASSET_ROWS = 250_000


class InventoryError(ValueError):
    """A source cannot be safely inventoried; messages are intentionally path-free."""


def _validate_root(root: Path) -> Path:
    root = Path(root)
    if not root.is_absolute() or not root.is_dir() or _path_has_reparse_point(root):
        raise InventoryError("Unsafe migration source")
    try:
        return root.resolve(strict=True)
    except OSError:
        raise InventoryError("Unsafe migration source") from None


def validate_legacy_project_root(root: Path) -> Path:
    """Validate the explicitly selected legacy project even when no DB is selected."""
    return _validate_root(root)


def _walk_files(root: Path) -> Iterator[tuple[Path, int, int]]:
    root = _validate_root(root)
    file_count = 0
    total_bytes = 0

    def onerror(_error: OSError) -> None:
        raise InventoryError("Unreadable migration source") from None

    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False,
                                                   onerror=onerror):
        current_path = Path(current)
        directories.sort(key=str.casefold)
        filenames.sort(key=str.casefold)
        for name in tuple(directories):
            candidate = current_path / name
            if _path_has_reparse_point(candidate):
                raise InventoryError("Unsafe migration source")
        for name in filenames:
            candidate = current_path / name
            if _path_has_reparse_point(candidate):
                raise InventoryError("Unsafe migration source")
            if not candidate.is_file():
                raise InventoryError("Invalid migration source entry")
            try:
                info = candidate.stat(follow_symlinks=False)
                size = info.st_size
            except OSError:
                raise InventoryError("Unreadable migration source") from None
            file_count += 1
            total_bytes += size
            if file_count > _MAX_INVENTORY_FILES \
                    or size < 0 or total_bytes > _MAX_INVENTORY_SOURCE_BYTES:
                raise InventoryError("Migration source inventory limit exceeded")
            yield candidate, size, info.st_mtime_ns


@contextmanager
def _open_verified_source(path: Path, source_root: Path) -> Iterator[BinaryIO]:
    """Open only the exact file resolved through a handle beneath its authorized root."""
    path = Path(path)
    root = _validate_root(source_root)
    if not path.is_absolute() or _path_has_reparse_point(path):
        raise InventoryError("Unsafe migration source")
    try:
        # Win32 may require an extended path for preserved snapshot members.
        # Keep logical paths unchanged for the handle-resolved containment check.
        physical_path = path
        if os.name == "nt" and not str(path).startswith("\\\\?\\"):
            value = str(path.absolute())
            physical_path = Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\")
                                 else "\\\\?\\" + value)
        stream = physical_path.open("rb")
        try:
            opened_path = _opened_file_path(stream)
            if opened_path is None:
                raise InventoryError("Unverifiable migration source")
            final_path = Path(os.path.normpath(str(opened_path)))
            expected_path = Path(os.path.normpath(os.path.abspath(path)))
            if os.path.normcase(os.path.normpath(str(final_path))) != \
                    os.path.normcase(os.path.normpath(str(expected_path))) \
                    or not final_path.is_relative_to(root):
                raise InventoryError("Unsafe migration source")
            yield stream
        finally:
            stream.close()
    except InventoryError:
        raise
    except OSError:
        raise InventoryError("Unreadable migration source") from None


def _fingerprint_file(path: Path, *, source_root: Path,
                      capture_limit: int = 0, expected_size: int | None = None,
                      max_bytes: int = _MAX_INVENTORY_SOURCE_BYTES) -> tuple[str, int, bytes]:
    """Hash a file through a verified handle, retaining only an optional small prefix."""
    if type(capture_limit) is not int or capture_limit < 0 \
            or type(max_bytes) is not int or max_bytes < 0 \
            or (expected_size is not None and
                (type(expected_size) is not int or expected_size < 0)):
        raise InventoryError("Invalid migration source")
    digest = hashlib.sha256()
    captured = bytearray()
    with _open_verified_source(path, source_root) as stream:
        before = _stream_signature(stream)
        size = before[2]
        if expected_size is not None and size != expected_size:
            raise InventoryError("Migration source changed during inventory")
        if size < 0 or size > max_bytes:
            raise InventoryError("Migration source inventory limit exceeded")
        total = 0
        while total < size:
            chunk = stream.read(min(_READ_CHUNK_BYTES, size - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise InventoryError("Migration source inventory limit exceeded")
            if total > size:
                raise InventoryError("Migration source changed during inventory")
            digest.update(chunk)
            if len(captured) < capture_limit:
                captured.extend(chunk[:capture_limit - len(captured)])
        if total != size or _stream_signature(stream) != before or _path_has_reparse_point(path):
            raise InventoryError("Migration source changed during inventory")
    return digest.hexdigest(), total, bytes(captured)


def _copy_verified_file(source: Path, target: Path, *, source_root: Path,
                        max_bytes: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    with _open_verified_source(source, source_root) as stream:
        before = _stream_signature(stream)
        size = before[2]
        if size < 0 or size > max_bytes:
            raise InventoryError("Legacy database snapshot limit exceeded")
        try:
            if _path_has_reparse_point(target):
                raise InventoryError("Unsafe private database snapshot")
            with target.open("xb") as output:
                copied = 0
                while True:
                    chunk = stream.read(_READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    next_size = copied + len(chunk)
                    if next_size > max_bytes:
                        raise InventoryError("Legacy database snapshot limit exceeded")
                    if next_size > size:
                        raise InventoryError("Legacy database changed during snapshot")
                    written = output.write(chunk)
                    if written != len(chunk):
                        raise InventoryError("Unable to create private database snapshot")
                    copied += written
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
        except InventoryError:
            raise
        except OSError:
            raise InventoryError("Unable to create private database snapshot") from None
        if copied != size or _stream_signature(stream) != before \
                or _path_has_reparse_point(source):
            raise InventoryError("Legacy database changed during snapshot")
    target_hash, target_size, _prefix = _fingerprint_file(
        target, source_root=target.parent, expected_size=size, max_bytes=size,
    )
    if target_hash != digest.hexdigest() or target_size != size:
        raise InventoryError("Unable to verify private database snapshot")
    return digest.hexdigest(), size


@contextmanager
def _private_sqlite_snapshot(database_path: Path, *, authorized_root: Path,
                             protected_paths: Iterable[Path] = ()) -> Iterator[Path]:
    """Copy a bounded SQLite database and WAL to private scratch before opening SQLite.

    SQLite read-only connections can still update a WAL shared-memory sidecar. Reading a
    private snapshot keeps every source database and sidecar byte-for-byte untouched.
    """
    root = _validate_root(authorized_root)
    database_path = Path(database_path)
    rollback_journal = Path(str(database_path) + "-journal")
    try:
        database_path.resolve(strict=True).relative_to(root)
    except (ValueError, OSError):
        raise InventoryError("Unsafe migration database") from None
    if _path_has_reparse_point(rollback_journal):
        raise InventoryError("Unsafe migration database")
    try:
        if rollback_journal.exists() and rollback_journal.stat().st_size > 0:
            raise InventoryError("Legacy database has a pending recovery journal")
    except OSError:
        raise InventoryError("Unreadable migration database") from None
    wal_path = Path(str(database_path) + "-wal")
    source_files = (database_path, wal_path)
    temporary_root = _validate_root(Path(tempfile.gettempdir()))
    repository_root = Path(__file__).resolve().parents[3]
    protected_locations = tuple(Path(path).resolve(strict=False) for path in protected_paths)
    all_protected = (root, repository_root, *protected_locations)
    if any(temporary_root.is_relative_to(protected) for protected in all_protected):
        raise InventoryError("Private database snapshot location overlaps protected data")
    with tempfile.TemporaryDirectory(prefix="study-migration-snapshot-", dir=temporary_root) as temporary:
        scratch_root = Path(temporary)
        scratch_root = _validate_root(scratch_root)
        if any(scratch_root.is_relative_to(protected) or protected.is_relative_to(scratch_root)
               for protected in all_protected):
            raise InventoryError("Private database snapshot location overlaps protected data")
        snapshot_db = scratch_root / "legacy.sqlite3"
        snapshot_wal = Path(str(snapshot_db) + "-wal")
        snapshot_paths = (snapshot_db, snapshot_wal)
        states: list[tuple[Path, Path, str | None, int]] = []
        remaining = _MAX_SQLITE_SNAPSHOT_BYTES
        for index, source in enumerate(source_files):
            if _path_has_reparse_point(source):
                raise InventoryError("Unsafe migration database")
            try:
                source.stat()
            except FileNotFoundError:
                states.append((source, snapshot_paths[index], None, 0))
                continue
            except OSError:
                raise InventoryError("Unreadable migration database") from None
            if not source.is_file():
                raise InventoryError("Invalid migration database entry")
            digest, size = _copy_verified_file(
                source, snapshot_paths[index], source_root=root, max_bytes=remaining,
            )
            remaining -= size
            states.append((source, snapshot_paths[index], digest, size))

        for source, _snapshot, expected_hash, expected_size in states:
            if _path_has_reparse_point(source):
                raise InventoryError("Legacy database changed during snapshot")
            try:
                source.stat()
            except FileNotFoundError:
                if expected_hash is not None:
                    raise InventoryError("Legacy database changed during snapshot") from None
                continue
            except OSError:
                raise InventoryError("Legacy database changed during snapshot") from None
            if expected_hash is None:
                raise InventoryError("Legacy database changed during snapshot")
            actual_hash, actual_size, _prefix = _fingerprint_file(
                source, source_root=root, expected_size=expected_size,
                max_bytes=expected_size,
            )
            if actual_hash != expected_hash or actual_size != expected_size:
                raise InventoryError("Legacy database changed during snapshot")
        if _path_has_reparse_point(rollback_journal):
            raise InventoryError("Legacy database changed during snapshot")
        try:
            if rollback_journal.exists() and rollback_journal.stat().st_size > 0:
                raise InventoryError("Legacy database has a pending recovery journal")
        except OSError:
            raise InventoryError("Legacy database changed during snapshot") from None
        yield snapshot_db


def _path_identity(relative_path: str) -> str:
    return "path-" + hashlib.sha256(relative_path.encode("utf-8")).hexdigest()


def _safe_legacy_id(value: str) -> str:
    if _SAFE_LEGACY_ID.fullmatch(value):
        return value
    return "legacy-id-" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _metadata_fingerprint(relative_path: str, size: int, mtime_ns: int) -> str:
    payload = f"{relative_path}\0{size}\0{mtime_ns}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _content_fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_legacy_wrong_answer_collection(name: str) -> bool:
    normalized = name.casefold()
    return bool(_WRONG_ANSWER_WORDS.search(name) or "错题" in normalized or "錯題" in normalized)


def scan_study_root(root: Path) -> tuple[SourceRecord, ...]:
    """Inventory the authoritative Study root without reading its file bodies."""
    root = _validate_root(root)
    records = []
    for path, expected_size, modified_ns in _walk_files(root):
        relative_path = path.relative_to(root).as_posix()
        parts = Path(relative_path).parts
        under_wrong_answers = bool(parts) and _is_legacy_wrong_answer_collection(parts[0])
        if not under_wrong_answers:
            category = "Documents" if path.suffix.casefold() == ".pdf" else "Study"
            source_type = ("study_file_pdf" if category == "Documents" else
                           "study_file_markdown" if path.suffix.casefold() == ".md" else
                           "study_file_authoritative")
            target_type = "study_source"
            fingerprint = _metadata_fingerprint(relative_path, expected_size, modified_ns)
            action = "reuse"
            reason = "same_authoritative_root"
        else:
            fingerprint, size, _prefix = _fingerprint_file(
                path, source_root=root, expected_size=expected_size,
                max_bytes=_MAX_INVENTORY_SOURCE_BYTES,
            )
            if path.suffix.casefold() == ".md":
                category = "Wrong Answers"
                source_type = "legacy_wrong_answer_note"
                target_type = "wrong_answer_source"
                reason = "legacy_record_unlinked"
            elif path.suffix.casefold() in _IMAGE_SUFFIXES:
                category = "Assets"
                source_type = "legacy_wrong_answer_image"
                target_type = "asset"
                reason = "unpaired_evidence"
            else:
                category = "Other / Unsupported"
                source_type = "unsupported_wrong_answer_file"
                target_type = "archive_record"
                reason = "unsupported_file_type"
            action = "archive" if category != "Other / Unsupported" else "skip"
        records.append(SourceRecord(
            category=category,
            legacy_system="studyvault_legacy",
            legacy_source_type=source_type,
            legacy_item_id=_path_identity(relative_path),
            source_fingerprint=fingerprint,
            target_type=target_type,
            target_logical_id=None,
            source_event_time=None,
            intended_action=action,
            validation_state=("unresolved" if under_wrong_answers else "valid"),
            requires_backend=None,
            reason_code=reason,
            source_size_bytes=size if under_wrong_answers else expected_size,
        ))
    return tuple(records)


def _parse_frontmatter(raw: bytes) -> tuple[dict[str, str], bool]:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    match = _FRONTMATTER.match(raw)
    if match is None or len(match.group(1)) > 32_768:
        return {}, False
    try:
        header = match.group(1).decode("utf-8")
    except UnicodeDecodeError:
        return {}, False
    fields: dict[str, str] = {}
    for line in header.splitlines():
        found = _FIELD.match(line)
        if found is None:
            continue
        key, value = found.groups()
        if key in {"type", "conversation_id", "created_at"}:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            fields[key] = value
    return fields, True


def _timestamp_or_none(value: str | None) -> str | None:
    if value is None or not value or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if parsed.tzinfo is not None else None


def _native_memory_record(row: sqlite3.Row, index: int) -> SourceRecord:
    content = row["content_value"]
    content_bytes = row["content_bytes"] if type(row["content_bytes"]) is int else 0
    too_large = content_bytes > _MAX_NATIVE_FACT_BYTES
    valid_content = type(content) is str and not too_large
    raw_content = content.encode("utf-8") if valid_content else b""
    raw_legacy_id = row["id"] if type(row["id"]) is str and row["id"] else f"row-{index}"
    legacy_id = _safe_legacy_id(raw_legacy_id)
    event_time = _timestamp_or_none(row["created_at"] if type(row["created_at"]) is str else None)
    if too_large:
        metadata = "\0".join((legacy_id, str(content_bytes),
                               row["kind"] if type(row["kind"]) is str else "unknown"))
        fingerprint = hashlib.sha256(b"legacy-fact-unread-content\0" +
                                     metadata.encode("utf-8")).hexdigest()
        reason_code = "legacy_fact_exceeds_limit"
    elif valid_content:
        fingerprint = _content_fingerprint(raw_content)
        reason_code = "annotation_contract_pending"
    else:
        metadata = "\0".join((legacy_id, str(content_bytes),
                               row["kind"] if type(row["kind"]) is str else "unknown"))
        fingerprint = hashlib.sha256(b"legacy-fact-invalid-content\0" +
                                     metadata.encode("utf-8")).hexdigest()
        reason_code = "invalid_legacy_content"
    return SourceRecord(
        category="Atomic Facts",
        legacy_system="workbuddy_native_memory",
        legacy_source_type="legacy_atomic_fact",
        legacy_item_id=legacy_id,
        source_fingerprint=fingerprint,
        target_type="history_annotation",
        target_logical_id=None,
        source_event_time=event_time,
        intended_action="import",
        validation_state="unresolved",
        requires_backend="history",
        reason_code=reason_code,
        source_size_bytes=content_bytes,
    )


def scan_personal_root(root: Path) -> tuple[SourceRecord, ...]:
    """Inventory the configured Personal History tree without emitting its content."""
    root = _validate_root(root)
    records = []
    for path, expected_size, modified_ns in _walk_files(root):
        relative_path = path.relative_to(root).as_posix()
        path_id = _path_identity(relative_path)
        if path.suffix.casefold() != ".md":
            records.append(SourceRecord(
                category="Other / Unsupported",
                legacy_system="basic_memory_legacy",
                legacy_source_type="unsupported_personal_file",
                legacy_item_id=path_id,
                source_fingerprint=_metadata_fingerprint(relative_path, expected_size, modified_ns),
                target_type="archive_record",
                target_logical_id=None,
                source_event_time=None,
                intended_action="skip",
                validation_state="unresolved",
                requires_backend=None,
                reason_code="unsupported_personal_file",
                source_size_bytes=expected_size,
            ))
            continue

        fingerprint, size, prefix = _fingerprint_file(
            path, source_root=root, capture_limit=_FRONTMATTER_CAPTURE_BYTES,
            expected_size=expected_size, max_bytes=_MAX_INVENTORY_SOURCE_BYTES,
        )
        fields, valid_frontmatter = _parse_frontmatter(prefix)
        record_type = fields.get("type")
        if record_type == "imported_chat":
            category = "Raw transcripts"
            source_type = "legacy_chat_markdown_archive"
            reason = "not_message_granular"
        elif record_type in {"imported_chat_index", "imported_chat_note"}:
            category = "Other / Unsupported"
            source_type = "legacy_chat_index_or_note"
            reason = "index_or_note_artifact"
        else:
            category = "Other / Unsupported"
            source_type = "legacy_personal_markdown"
            reason = "unsupported_personal_markdown"
        stable_id = fields.get("conversation_id") if record_type == "imported_chat" else None
        if stable_id:
            stable_id = _safe_legacy_id(stable_id)
        if not stable_id:
            stable_id = path_id
        event_time = _timestamp_or_none(fields.get("created_at"))
        valid_metadata = valid_frontmatter and record_type is not None
        records.append(SourceRecord(
            category=category,
            legacy_system="basic_memory_legacy",
            legacy_source_type=source_type,
            legacy_item_id=stable_id,
            source_fingerprint=fingerprint,
            target_type="history_archive" if category == "Raw transcripts" else "archive_record",
            target_logical_id=None,
            source_event_time=event_time,
            intended_action="archive" if category == "Raw transcripts" else "skip",
            validation_state="valid" if valid_metadata else "unresolved",
            requires_backend=None,
            reason_code=reason if valid_metadata else "malformed_frontmatter",
            source_size_bytes=size,
        ))
    return tuple(records)


def scan_native_memory_db(database_path: Path, *, project_root: Path,
                          protected_paths: Iterable[Path] = ()) -> tuple[SourceRecord, ...]:
    """Read only the known native-memory table within the explicitly selected project."""
    project_root = _validate_root(project_root)
    database_path = Path(database_path)
    if not database_path.is_absolute() or not database_path.is_file() \
            or _path_has_reparse_point(database_path):
        raise InventoryError("Unsafe legacy database")
    try:
        database_path.resolve(strict=True).relative_to(project_root)
    except (ValueError, OSError):
        raise InventoryError("Unsafe legacy database") from None

    records = []
    try:
        with _private_sqlite_snapshot(
                database_path, authorized_root=project_root,
                protected_paths=protected_paths) as snapshot:
            uri = snapshot.as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only=ON")
                columns = {row[1] for row in connection.execute("PRAGMA table_info(memories)")}
                required = {"id", "kind", "content", "created_at"}
                if not required <= columns:
                    raise InventoryError("Unsupported legacy memory schema")
                cursor = connection.execute(
                    "SELECT id, kind, "
                    "CASE WHEN length(CAST(content AS BLOB)) <= ? THEN content ELSE NULL END AS content_value, "
                    "created_at, length(CAST(content AS BLOB)) AS content_bytes "
                    "FROM memories ORDER BY id LIMIT 10001", (_MAX_NATIVE_FACT_BYTES,),
                )
                index = 0
                while (row := cursor.fetchone()) is not None:
                    index += 1
                    if index > 10_000:
                        raise InventoryError("Legacy database inventory limit exceeded")
                    records.append(_native_memory_record(row, index))
    except InventoryError:
        raise
    except sqlite3.Error:
        raise InventoryError("Unreadable legacy database") from None
    return tuple(records)


def read_existing_asset_hashes(database_path: Path, *,
                               protected_paths: Iterable[Path] = ()) -> set[tuple[str, str]]:
    """Read content hashes from the configured V2 Asset database without writes."""
    database_path = Path(database_path)
    if not database_path.is_absolute() or not database_path.is_file() \
            or _path_has_reparse_point(database_path):
        raise InventoryError("Invalid asset target")
    result = set()
    try:
        asset_root = _validate_root(database_path.parent)
        with _private_sqlite_snapshot(
                database_path, authorized_root=asset_root,
                protected_paths=protected_paths) as snapshot:
            uri = snapshot.as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only=ON")
                columns = {row[1] for row in connection.execute("PRAGMA table_info(assets)")}
                if not {"uri", "sha256"} <= columns:
                    raise InventoryError("Invalid asset target")
                cursor = connection.execute("SELECT uri, sha256 FROM assets")
                row_count = 0
                while (row := cursor.fetchone()) is not None:
                    row_count += 1
                    if row_count > _MAX_ASSET_ROWS:
                        raise InventoryError("Asset target row limit exceeded")
                    uri_value, digest = row["uri"], row["sha256"]
                    if type(digest) is not str or not _HASH.fullmatch(digest) \
                            or uri_value != "asset://sha256/" + digest:
                        raise InventoryError("Invalid asset target")
                    result.add(("asset", digest))
    except InventoryError:
        raise
    except sqlite3.Error:
        raise InventoryError("Invalid asset target") from None
    return result
