"""Explicit internal image migration through the existing Asset domain store.

No discovery, arbitrary SQL writes, question inference or Agent-facing endpoint.
Source identities are retained even when two sources contain identical bytes.
"""

from contextlib import closing
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from ..adapters.documents import (AssetRecord, SQLiteDocumentStore, MAX_IMAGE_BYTES,
                                  _validate_asset, _stream_signature)
from ..provenance import utc_now
from .inventory import _open_verified_source, _private_sqlite_snapshot
from .manifest import MigrationItem, MigrationJournal, _validate_item, validate_cli_run_id
from .real_apply import FileSource, MAX_TOTAL_BYTES, _overlap, _private_location, snapshot_and_verify_restore


def _source_ref(source: FileSource) -> str:
    return "migration-source://" + source.source_id + "/" + source.source_item_id


def _load_image(source: FileSource) -> tuple[bytes, str]:
    if type(source) is not FileSource or type(source.byte_count) is not int \
            or not 0 < source.byte_count <= MAX_IMAGE_BYTES \
            or source.source_type != "legacy_wrong_answer_image" \
            or type(source.source_order) is not int or source.source_order < 0 \
            or not source.root.is_absolute() or not source.path.is_absolute() \
            or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,255}", source.source_id) is None \
            or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,255}", source.source_item_id) is None:
        raise ValueError("Invalid legacy image source")
    media_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(
        source.path.suffix.casefold())
    if media_type is None:
        raise ValueError("Unsupported legacy image source")
    with _open_verified_source(source.path, source.root) as stream:
        before = _stream_signature(stream)
        if before[2] != source.byte_count or os.fstat(stream.fileno()).st_nlink != 1:
            raise ValueError("Legacy image source identity changed")
        data = stream.read(source.byte_count + 1)
        if len(data) != source.byte_count or _stream_signature(stream) != before:
            raise ValueError("Legacy image source changed")
    digest, _uri = _validate_asset(data, media_type)
    if digest != source.source_hash:
        raise ValueError("Legacy image source hash changed")
    _validate_item(_journal_item(source))
    return data, media_type


def _journal_item(source: FileSource) -> MigrationItem:
    return MigrationItem(
        category="Assets", legacy_system=source.source_id,
        legacy_source_type=source.source_type, legacy_item_id=source.source_item_id,
        source_fingerprint=source.source_hash, target_type="asset", target_logical_id=None,
        action="import", status="planned", source_event_time=source.source_created_at,
        validation_state="valid", reason_code="legacy_image_bytes_only",
        dedup_decision="content_hash", source_size_bytes=source.byte_count,
    )


def _asset_snapshot(store: SQLiteDocumentStore, backup_root: Path, roots: tuple[Path, ...]) -> dict:
    """Snapshot DB and every registered original blob, then restore both in isolation."""
    store._ready()
    receipt = snapshot_and_verify_restore(store.database_path, backup_root,
                                          protected_paths=(store.asset_root, *roots))
    backup_db = Path(receipt["private_snapshot"])
    backup_assets = backup_db.with_suffix(".assets")
    _private_location(backup_assets, (store.database_path.parent, store.asset_root, *roots))
    backup_assets.mkdir()
    with closing(sqlite3.connect(backup_db.as_uri() + "?mode=ro", uri=True)) as connection:
        uris = [row[0] for row in connection.execute("SELECT uri FROM assets ORDER BY uri LIMIT 10001")]
    if len(uris) > 10_000:
        raise ValueError("Legacy asset snapshot limit exceeded")
    # All metadata comes from the stable DB backup. Live blobs are immutable,
    # and the domain reader checks bytes, sizes, hashes and file identity.
    live_blobs = SQLiteDocumentStore(store.asset_root, backup_db)
    entries = []
    for uri in uris:
        record = live_blobs.fetch_asset_record(uri)
        data = live_blobs._verified_asset_snapshot(record)
        folder = backup_assets / record.sha256[:2]
        folder.mkdir(exist_ok=True)
        with (folder / record.sha256).open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        entries.append({"asset_uri": uri, "sha256": record.sha256,
                        "byte_count": record.size, "media_type": record.media_type})
    with tempfile.TemporaryDirectory(prefix="study-asset-restore-") as temporary:
        isolated = Path(temporary)
        if any(_overlap(isolated, p) for p in (store.asset_root, store.database_path.parent,
                                              backup_root, *roots)):
            raise ValueError("Legacy asset restore overlaps protected data")
        restored_db = isolated / "documents.db"
        restored_root = isolated / "assets"
        shutil.copyfile(backup_db, restored_db)
        shutil.copytree(backup_assets, restored_root)
        restored = SQLiteDocumentStore(restored_root, restored_db)
        for entry in entries:
            record = restored.fetch_asset_record(entry["asset_uri"])
            data = restored._verified_asset_snapshot(record)
            if record.sha256 != entry["sha256"] or record.media_type != entry["media_type"] \
                    or len(data) != entry["byte_count"] \
                    or restored.get_write_provenance("asset", record.uri) != live_blobs.get_write_provenance(
                        "asset", record.uri):
                raise ValueError("Legacy asset restore verification failed")
    receipt.update(private_asset_snapshot=str(backup_assets), asset_restore_verified=len(entries),
                   asset_blobs=entries, private_receipt=str(backup_db.with_suffix(".receipt.json")))
    # The shared DB receipt already exists; append the verified blob receipt.
    with Path(receipt["private_receipt"]).open("w", encoding="utf-8") as stream:
        json.dump(receipt, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    return receipt


def _validate_committed_checkpoints(store: SQLiteDocumentStore, journal_path: Path,
                                    run_id: str, protected: tuple[Path, ...]) -> None:
    """A restored target cannot reuse committed checkpoints from a later state."""
    if not journal_path.exists():
        return
    with _private_sqlite_snapshot(journal_path, authorized_root=journal_path.parent,
                                  protected_paths=protected) as scratch:
        with closing(sqlite3.connect(scratch.as_uri() + "?mode=ro", uri=True)) as connection:
            rows = connection.execute(
                "SELECT target_logical_id,source_fingerprint,source_size_bytes,imported_at,reason_code "
                "FROM migration_items WHERE run_id=? AND status='committed'", (run_id,),
            ).fetchall()
    for target_id, digest, size, imported_at, reason in rows:
        if target_id != "asset-sha256-" + digest:
            raise ValueError("Legacy asset committed checkpoint identity conflicts with target")
        try:
            record = store.fetch_asset_record("asset://sha256/" + digest)
            if record is None or record.sha256 != digest or record.size != size:
                raise ValueError("Legacy asset committed checkpoint is absent from target")
            store._verified_asset_snapshot(record)
            provenance = store.get_write_provenance("asset", record.uri)
            if reason != "native_content_reuse_checkpoint_time" and (
                not provenance or provenance["data_origin"] != "legacy_import"
                or provenance["actor_type"] != "importer" or provenance["imported_at"] != imported_at
            ):
                raise ValueError("Legacy asset committed checkpoint import time conflicts with target")
        except Exception:
            raise ValueError("Legacy asset committed checkpoint conflicts with target") from None


def apply_assets(*, database: Path, asset_root: Path, sources: list[FileSource],
                 journal_path: Path, backup_root: Path, run_id: str, batch_size: int = 1) -> dict:
    """Validate all files, prove recovery, domain import, verify bytes and checkpoint.

    The atomic unit is exactly one original image and its domain transaction.
    Its checkpoint follows byte readback. A run can stop between images; prior
    committed images survive and are reused on retry, without claiming whole-run
    atomicity. Target restore requires a new run rather than a stale checkpoint.
    """
    if not validate_cli_run_id(run_id) or type(sources) is not list \
            or not 1 <= len(sources) <= 10_000 or type(batch_size) is not int \
            or batch_size != 1 or any(type(s) is not FileSource for s in sources):
        raise ValueError("Invalid legacy image source set")
    database, asset_root = Path(database), Path(asset_root)
    journal_path, backup_root = Path(journal_path), Path(backup_root)
    roots = tuple(set(s.root.resolve() for s in sources))
    if not database.is_absolute() or not database.is_file() or not asset_root.is_absolute() \
            or not asset_root.is_dir() or any(_overlap(p, root)
                                            for p in (database.parent, asset_root, backup_root)
                                            for root in roots):
        raise ValueError("Legacy asset target overlaps source")
    protected = (database.parent, asset_root, backup_root, *roots)
    _private_location(journal_path, protected)
    identities = [(s.source_id, s.source_item_id) for s in sources]
    if len(set(identities)) != len(sources) or any(type(s.byte_count) is not int for s in sources) \
            or sum(s.byte_count for s in sources) > MAX_TOTAL_BYTES:
        raise ValueError("Invalid legacy image source set")
    store = SQLiteDocumentStore(asset_root, database)
    store._ready()
    for source in sources:
        _data, media = _load_image(source)
        # Even an unregistered prospective content blob must not alias protected
        # data, or conflict with the validated bytes, before the first commit.
        blob = asset_root / source.source_hash[:2] / source.source_hash
        if blob.exists():
            store._verified_asset_snapshot(AssetRecord("asset://sha256/" + source.source_hash,
                                                       media, source.byte_count, source.source_hash))
    payload = [(s.source_id, s.source_item_id, s.source_type, s.source_hash, s.byte_count,
                s.source_order, s.source_created_at) for s in sources]
    digest = hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
    binding_path = journal_path.parent / (run_id + "-asset-source-set.json")
    _private_location(binding_path, protected)
    binding = {"source_set_digest": digest, "batch_size": batch_size,
               "database_identity": hashlib.sha256(str(database.resolve()).encode()).hexdigest(),
               "asset_root_identity": hashlib.sha256(str(asset_root.resolve()).encode()).hexdigest()}
    if binding_path.exists() and json.loads(binding_path.read_text("utf-8")) != binding:
        raise ValueError("Legacy asset source set conflicts with prior run")
    _validate_committed_checkpoints(store, journal_path, run_id, protected)
    receipt = _asset_snapshot(store, backup_root, roots)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    if not binding_path.exists():
        with binding_path.open("x", encoding="utf-8") as stream:
            json.dump(binding, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
    journal = MigrationJournal(journal_path, run_id=run_id, protected_paths=protected)
    imported = deduplicated = verified = 0
    target_ids, refs = [], []
    try:
        previous = {(i.legacy_system, i.legacy_item_id): i for i in journal.list_items()}
        for start in range(0, len(sources), batch_size):
            batch = sources[start:start + batch_size]
            number = start // batch_size + 1
            planned = []
            for source in batch:
                item = previous.get((source.source_id, source.source_item_id))
                if item is None:
                    item = _journal_item(source)
                    uri = "asset://sha256/" + source.source_hash
                    if store.fetch_asset_record(uri) is not None:
                        metadata = store.get_write_provenance("asset", uri)
                        if not metadata or metadata["data_origin"] != "legacy_import":
                            item = replace(item, reason_code="native_content_reuse_checkpoint_time")
                planned.append(item)
            journal.add_batch(planned, batch_number=number)
            committed = []
            for source in batch:
                data, media = _load_image(source)
                uri = "asset://sha256/" + source.source_hash
                existing = store.fetch_asset_record(uri)
                old_provenance = store.get_write_provenance("asset", uri) if existing else None
                record = store.register_legacy_asset(data, media, import_batch_id=run_id,
                                                     source_ref=_source_ref(source))
                actual = store.fetch_asset_record(record.uri)
                readback = store._verified_asset_snapshot(actual)
                provenance = store.get_write_provenance("asset", record.uri)
                if record.uri != uri or actual.size != source.byte_count \
                        or actual.sha256 != source.source_hash or readback != data \
                        or (existing is not None and provenance != old_provenance) \
                        or (existing is None and (provenance["data_origin"] != "legacy_import"
                            or provenance["actor_type"] != "importer"
                            or provenance["source_refs"] != [_source_ref(source)]
                            or provenance["import_batch_id"] != run_id
                            or not provenance["imported_at"])):
                    raise ValueError("Legacy asset readback verification failed")
                prior = previous.get((source.source_id, source.source_item_id))
                if provenance and provenance["data_origin"] == "legacy_import":
                    imported_at = provenance["imported_at"]
                else:
                    # For native content reuse this is checkpoint acquisition
                    # time, never an inferred source creation/import event.
                    imported_at = prior.imported_at if prior and prior.imported_at else utc_now()
                committed.append(replace(planned[0], status="committed", imported_at=imported_at,
                                         target_logical_id="asset-sha256-" + source.source_hash))
                imported += existing is None
                deduplicated += existing is not None
                verified += 1
                target_ids.append(uri)
                refs.append({"source_id": source.source_id, "source_item_id": source.source_item_id,
                             "source_ref": _source_ref(source), "asset_uri": uri})
            journal.add_batch(committed, batch_number=number)
        for source in sources:
            _load_image(source)
        return {"found": len(sources), "imported": imported, "deduplicated": deduplicated,
                "readback_verified": verified, "journal_committed": len(journal.list_items()),
                "target_ids": target_ids, "asset_refs": refs, "source_set_digest": digest,
                "snapshot": receipt, "run_id": run_id}
    finally:
        journal.close()
