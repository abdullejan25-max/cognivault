"""Restricted local migration orchestration through validated domain writes.

This module is not an Agent-facing tool. Explicit files, hashes, targets and
private recovery locations are required; it performs no source discovery.
"""

from contextlib import closing
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from uuid import uuid4

from ..adapters.documents import _path_has_reparse_point, _stream_signature
from ..adapters.legacy_sources import LegacySourceInput, SQLiteLegacySourceStore, validate_input
from ..provenance import ReportedIdentity
from .inventory import _open_verified_source, _private_sqlite_snapshot
from .manifest import (MigrationItem, MigrationJournal, _validate_item,
                       validate_cli_run_id, validate_private_journal_path)


MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_TARGET_BYTES = 480 * 1024 * 1024  # Reserve space below the verified DB/WAL snapshot ceiling.


@dataclass(frozen=True)
class FileSource:
    root: Path
    path: Path
    source_id: str
    source_item_id: str
    source_type: str
    source_hash: str
    byte_count: int
    source_order: int
    source_created_at: str | None = None
    source_refs: tuple[str, ...] = ()


def _overlap(left: Path, right: Path) -> bool:
    left, right = left.resolve(), right.resolve()
    return left.is_relative_to(right) or right.is_relative_to(left)


def _private_location(path: Path, protected=()) -> Path:
    return validate_private_journal_path(Path(path), protected_paths=tuple(protected))


def _load_source(source: FileSource) -> LegacySourceInput:
    if type(source) is not FileSource or type(source.byte_count) is not int \
            or not 0 < source.byte_count <= MAX_SOURCE_BYTES:
        raise ValueError("Invalid migration source")
    with _open_verified_source(source.path, source.root) as stream:
        before = _stream_signature(stream)
        if before[2] != source.byte_count or os.fstat(stream.fileno()).st_nlink != 1:
            raise ValueError("Migration source identity changed")
        content = stream.read(source.byte_count + 1)
        if len(content) != source.byte_count or _stream_signature(stream) != before:
            raise ValueError("Migration source changed")
    if hashlib.sha256(content).hexdigest() != source.source_hash:
        raise ValueError("Migration source hash changed")
    item = LegacySourceInput(source.source_id, source.source_item_id, source.source_type,
                             content, source.source_hash, source.source_order,
                             source.source_created_at, source.source_refs)
    validate_input(item)
    return item


def _table_counts(database: Path) -> dict:
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Migration snapshot integrity failed")
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '%_fts%'"
        )]
        result = {}
        for name in names:
            quoted = name.replace('"', '""')
            result[name] = connection.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[0]
        return result


def _hash_file(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def snapshot_and_verify_restore(database: Path, backup_root: Path, *, protected_paths=()) -> dict:
    """Persist a self-contained DB backup, then prove a private isolated restore."""
    database, backup_root = Path(database), Path(backup_root)
    if not database.is_absolute() or not database.is_file() or not backup_root.is_absolute():
        raise ValueError("Invalid migration snapshot target")
    if _overlap(database.parent, backup_root) or any(
        _overlap(backup_root, Path(path)) for path in protected_paths
    ):
        raise ValueError("Migration snapshot overlaps protected source")
    _private_location(database, (backup_root, *protected_paths))
    destination = backup_root / ("target-" + uuid4().hex + ".sqlite3")
    _private_location(destination, (database.parent, *protected_paths))
    backup_root.mkdir(parents=True, exist_ok=True)
    _private_location(destination, (database.parent, *protected_paths))
    # Never open the original DB/WAL to read; source sidecars remain untouched.
    with _private_sqlite_snapshot(database, authorized_root=database.parent,
                                  protected_paths=protected_paths) as scratch:
        with closing(sqlite3.connect(scratch.as_uri() + "?mode=ro", uri=True)) as source:
            if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Migration snapshot integrity failed")
            with destination.open("xb"):
                pass
            with closing(sqlite3.connect(destination)) as target:
                source.backup(target)
                target.commit()
    backup_hash, backup_size = _hash_file(destination)
    counts = _table_counts(destination)
    with tempfile.TemporaryDirectory(prefix="study-migration-restore-") as temporary:
        restored = Path(temporary) / "restored.sqlite3"
        if any(_overlap(Path(temporary), Path(path)) for path in (database.parent, backup_root, *protected_paths)):
            raise ValueError("Migration restore overlaps protected data")
        shutil.copyfile(destination, restored)
        if _hash_file(restored) != (backup_hash, backup_size) or _table_counts(restored) != counts:
            raise ValueError("Migration restore verification failed")
        from ..adapters.history import SQLiteHistoryBackend
        backend = SQLiteHistoryBackend(restored)
        if backend.probe().status != "ready":
            raise ValueError("Migration restore health failed")
        if "history_sources" in counts:
            backend.list_sources()
        if "legacy_source_records" in counts:
            SQLiteLegacySourceStore(restored).list_sources()
    receipt = {"private_snapshot": str(destination), "sha256": backup_hash,
               "byte_count": backup_size, "restore_verified": True, "table_counts": counts}
    receipt_path = destination.with_suffix(".receipt.json")
    with receipt_path.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    return receipt


def _record_id(source: FileSource) -> str:
    return "legacy-source:" + hashlib.sha256(
        (source.source_id + "\0" + source.source_item_id).encode("utf-8")
    ).hexdigest()


def _journal_item(source: FileSource) -> MigrationItem:
    return MigrationItem(
        category={"legacy_derived_fact": "Atomic Facts", "legacy_wrong_answer_document": "Wrong Answers"}.get(
            source.source_type, "Raw transcripts"), legacy_system=source.source_id,
        legacy_source_type=source.source_type, legacy_item_id=source.source_item_id,
        source_fingerprint=source.source_hash, target_type="legacy_source",
        target_logical_id=None, action="import", status="planned",
        source_event_time=source.source_created_at, validation_state="valid",
        reason_code="exact_source_document", source_size_bytes=source.byte_count,
    )


def _validate_committed_checkpoint(database: Path, journal_path: Path, run_id: str, *, protected_paths=()) -> None:
    """A restored target cannot silently reuse a journal from a later state."""
    if not journal_path.exists():
        return
    with _private_sqlite_snapshot(journal_path, authorized_root=journal_path.parent,
                                  protected_paths=(database.parent, *protected_paths)) as scratch:
        with closing(sqlite3.connect(scratch.as_uri() + "?mode=ro", uri=True)) as connection:
            rows = connection.execute(
                "SELECT target_logical_id, source_fingerprint, imported_at FROM migration_items "
                "WHERE run_id=? AND status='committed'", (run_id,),
            ).fetchall()
    store = SQLiteLegacySourceStore(database)
    for target_id, digest, imported_at in rows:
        if not target_id or not target_id.startswith("legacy-source-"):
            raise ValueError("Migration committed checkpoint conflicts with target")
        record_id = "legacy-source:" + target_id.removeprefix("legacy-source-")
        try:
            metadata = store.fetch(record_id, length=1)
        except Exception:
            raise ValueError("Migration committed checkpoint is absent from target") from None
        if metadata["source_hash"] != digest or metadata["imported_at"] != imported_at:
            raise ValueError("Migration committed checkpoint conflicts with target")


def apply_sources(*, database: Path, sources: list[FileSource], journal_path: Path,
                  backup_root: Path, run_id: str, batch_size: int = 8) -> dict:
    """Preflight every source, snapshot/restore, domain commit, read-back/checkpoint.

    Target and journal are separate transactions. A crash after domain commit is
    safe: deterministic IDs and strict domain conflicts permit replay, then the
    target's original imported_at is used to finish the journal checkpoint.
    """
    if not validate_cli_run_id(run_id) or type(sources) is not list or not 1 <= len(sources) <= 10_000 \
            or type(batch_size) is not int or not 1 <= batch_size <= 16:
        raise ValueError("Invalid migration source set")
    database, journal_path, backup_root = Path(database), Path(journal_path), Path(backup_root)
    roots = tuple(set(Path(source.root).resolve() for source in sources))
    if not database.is_absolute() or not database.is_file() or any(
        _overlap(database.parent, root) or _overlap(backup_root, root) for root in roots
    ):
        raise ValueError("Migration target overlaps source")
    _private_location(journal_path, (database.parent, backup_root, *roots))
    _private_location(database, (backup_root, *roots))
    keys = [(source.source_id, source.source_item_id) for source in sources]
    if len(set(keys)) != len(keys) or sum(source.byte_count for source in sources) > MAX_TOTAL_BYTES:
        raise ValueError("Invalid migration source set")
    for source in sources:
        _load_source(source)  # Validate the entire set before any target mutation.
        _validate_item(_journal_item(source))
    store = SQLiteLegacySourceStore(database, reported_identity=ReportedIdentity(
        reported_agent="Codex", reported_client="local-migration", run_id=run_id))
    new_bytes = 0
    for source in sources:
        try:
            store.fetch(_record_id(source), length=1)
        except Exception as error:
            if getattr(error, "code", None) != "RESOURCE_NOT_FOUND":
                raise
            new_bytes += source.byte_count + 8192
    target_bytes = sum(path.stat().st_size for path in (database, Path(str(database) + "-wal")) if path.exists())
    if target_bytes + new_bytes > MAX_TARGET_BYTES:
        raise ValueError("Migration target exceeds recoverable snapshot limit")
    set_payload = [(s.source_id, s.source_item_id, s.source_type, s.source_hash,
                    s.byte_count, s.source_order, s.source_created_at, s.source_refs) for s in sources]
    set_digest = hashlib.sha256(json.dumps(set_payload, separators=(",", ":")).encode()).hexdigest()
    binding_path = journal_path.parent / (run_id + "-source-set.json")
    _private_location(binding_path, (database.parent, backup_root, *roots))
    binding = {"source_set_digest": set_digest, "target_identity": hashlib.sha256(
        str(database.resolve()).encode()).hexdigest(), "batch_size": batch_size}
    if binding_path.exists() and json.loads(binding_path.read_text(encoding="utf-8")) != binding:
        raise ValueError("Migration source set conflicts with prior run")
    _validate_committed_checkpoint(database, journal_path, run_id, protected_paths=(backup_root, *roots))
    receipt = snapshot_and_verify_restore(database, backup_root, protected_paths=roots)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    if not binding_path.exists():
        with binding_path.open("x", encoding="utf-8") as stream:
            json.dump(binding, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
    journal = MigrationJournal(journal_path, run_id=run_id,
                               protected_paths=(database.parent, backup_root, *roots))
    _private_location(database, (backup_root, *roots))
    store.initialize()
    imported, deduplicated, verified, target_ids = 0, 0, 0, []
    try:
        previous = {(i.legacy_system, i.legacy_item_id): i for i in journal.list_items()}
        for start in range(0, len(sources), batch_size):
            batch = sources[start:start + batch_size]
            number = start // batch_size + 1
            planned = [previous.get((s.source_id, s.source_item_id), _journal_item(s)) for s in batch]
            journal.add_batch(planned, batch_number=number)
            existing_ids = set()
            for source in batch:
                try:
                    store.fetch(_record_id(source), length=1)
                    existing_ids.add(_record_id(source))
                except Exception as error:
                    if getattr(error, "code", None) != "RESOURCE_NOT_FOUND":
                        raise
            _private_location(database, (backup_root, *roots))
            ids = store.import_batch([_load_source(s) for s in batch], import_batch_id=run_id)
            committed = []
            for source, target_id in zip(batch, ids, strict=True):
                if target_id != _record_id(source):
                    raise ValueError("Migration target identity failed")
                metadata = store.verify_record(target_id, expected_sha256=source.source_hash,
                                                expected_bytes=source.byte_count)
                if metadata["source_type"] != source.source_type or metadata["source_order"] != source.source_order \
                        or metadata["source_created_at"] != source.source_created_at \
                        or metadata["write_provenance"]["data_origin"] != "legacy_import":
                    raise ValueError("Migration readback verification failed")
                item = _journal_item(source)
                # Manifest logical identifiers use an opaque path-free label.
                committed.append(replace(item, status="committed", imported_at=metadata["imported_at"],
                                         target_logical_id=target_id.replace(":", "-")))
                verified += 1
            journal.add_batch(committed, batch_number=number)
            deduplicated += len(existing_ids)
            imported += len(ids) - len(existing_ids)
            target_ids.extend(ids)
        for source in sources:
            _load_source(source)  # Sources must remain identical after the complete run.
        return {"found": len(sources), "imported": imported, "deduplicated": deduplicated,
                "readback_verified": verified, "source_set_digest": set_digest,
                "target_ids": target_ids, "snapshot": receipt, "run_id": run_id,
                "journal_committed": len(journal.list_items())}
    finally:
        journal.close()
