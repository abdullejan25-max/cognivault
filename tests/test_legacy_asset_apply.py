"""Synthetic proof for bounded image migration; no private data fixtures."""

import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from cognivault.adapters.documents import SQLiteDocumentStore
from cognivault.contracts import GatewayError
from cognivault.migration.real_apply import FileSource


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6"
    "S1sAAAAASUVORK5CYII="
)
RUN = "migration-" + "a" * 32


def _store(tmp_path):
    directory = tmp_path / "target"
    directory.mkdir()
    root = directory / "assets"
    root.mkdir()
    store = SQLiteDocumentStore(root, directory / "documents.db")
    # Establish a real initialized domain target, as required by apply.
    store.register_asset(PNG + b"existing-0", "image/png")
    return store


def _source(tmp_path, index, content):
    root = tmp_path / "sources"
    root.mkdir(exist_ok=True)
    path = root / f"image-{index}.png"
    path.write_bytes(content)
    return FileSource(root, path, "legacy-test", f"image-{index}",
                      "legacy_wrong_answer_image", hashlib.sha256(content).hexdigest(),
                      len(content), index)


def _apply(store, tmp_path, sources):
    from cognivault.migration import legacy_assets
    return legacy_assets.apply_assets(
        database=store.database_path, asset_root=store.asset_root, sources=sources,
        journal_path=tmp_path / "journal" / "journal.sqlite3",
        backup_root=tmp_path / "backups", run_id=RUN,
    )


def test_legacy_asset_preserves_native_provenance_and_marks_only_new_assets(tmp_path):
    store = _store(tmp_path)
    assert hasattr(store, "register_legacy_asset")
    original = store.register_asset(PNG + b"existing-0", "image/png")
    before = store.get_write_provenance("asset", original.uri)
    result = store.register_legacy_asset(
        PNG + b"existing-0", "image/png", import_batch_id=RUN,
        source_ref="migration-source://legacy-test/image-0",
    )
    assert result == original
    assert store.get_write_provenance("asset", original.uri) == before
    added = store.register_legacy_asset(
        PNG + b"new", "image/png", import_batch_id=RUN,
        source_ref="migration-source://legacy-test/image-1",
    )
    provenance = store.get_write_provenance("asset", added.uri)
    assert provenance["data_origin"] == "legacy_import"
    assert provenance["actor_type"] == "importer"
    assert provenance["source_refs"] == ["migration-source://legacy-test/image-1"]
    assert provenance["import_batch_id"] == RUN
    assert provenance["imported_at"].endswith("Z")
    assert provenance["original_created_at"] is None
    assert provenance["reported_agent"] is None


@pytest.mark.parametrize("changed", [PNG + b"changed", PNG + b"c"])
def test_changed_source_is_rejected_before_any_target_or_backup_write(tmp_path, changed):
    store = _store(tmp_path)
    sources = [_source(tmp_path, 0, PNG + b"a"), _source(tmp_path, 1, PNG + b"b")]
    sources[-1].path.write_bytes(changed)
    before = store.database_path.read_bytes()
    blobs = {p.relative_to(store.asset_root): p.read_bytes()
             for p in store.asset_root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="source"):
        _apply(store, tmp_path, sources)
    assert store.database_path.read_bytes() == before
    assert {p.relative_to(store.asset_root): p.read_bytes()
            for p in store.asset_root.rglob("*") if p.is_file()} == blobs
    assert not (tmp_path / "backups").exists()


def test_apply_restores_all_four_original_blobs_dedupes_and_replays(tmp_path):
    store = _store(tmp_path)
    old = [store.register_asset(PNG + f"existing-{i}".encode(), "image/png")
           for i in range(4)]
    provenance = {r.uri: store.get_write_provenance("asset", r.uri) for r in old}
    sources = [_source(tmp_path, i, PNG + f"existing-{i}".encode()) for i in range(4)]
    sources += [_source(tmp_path, i, PNG + f"new-{i}".encode()) for i in (4, 5)]
    result = _apply(store, tmp_path, sources)
    assert (result["found"], result["imported"], result["deduplicated"],
            result["readback_verified"], result["journal_committed"]) == (6, 2, 4, 6, 6)
    receipt = result["snapshot"]
    assert receipt["restore_verified"] is True
    assert receipt["asset_restore_verified"] == 4
    backup_store = SQLiteDocumentStore(Path(receipt["private_asset_snapshot"]),
                                      Path(receipt["private_snapshot"]))
    for record in old:
        assert backup_store.fetch_asset(record.uri) == store.fetch_asset(record.uri)
        assert backup_store.get_write_provenance("asset", record.uri) == provenance[record.uri]
    assert len(result["asset_refs"]) == 6
    assert all(set(ref) == {"source_id", "source_item_id", "source_ref", "asset_uri"}
               for ref in result["asset_refs"])
    assert all(store.get_write_provenance("asset", r.uri) == provenance[r.uri] for r in old)
    replay = _apply(store, tmp_path, sources)
    assert (replay["imported"], replay["deduplicated"], replay["journal_committed"]) == (0, 6, 6)
    assert replay["target_ids"] == result["target_ids"]
    assert json.loads(Path(receipt["private_receipt"]).read_text())["asset_restore_verified"] == 4
    # Recovery relies on the durable backup, even if originals become unavailable.
    for index, record in enumerate(old):
        (store.asset_root / record.sha256[:2] / record.sha256).unlink()
        assert backup_store.fetch_asset(record.uri) == PNG + f"existing-{index}".encode()


def test_corrupt_existing_blob_aborts_before_import(tmp_path):
    store = _store(tmp_path)
    old = store.register_asset(PNG + b"existing-0", "image/png")
    blob = store.asset_root / old.sha256[:2] / old.sha256
    blob.write_bytes(b"corrupt")
    source = _source(tmp_path, 0, PNG + b"new")
    with pytest.raises(GatewayError):
        _apply(store, tmp_path, [source])
    assert store.fetch_asset_record("asset://sha256/" + source.source_hash) is None


def test_same_bytes_dedupe_content_without_implying_question_role(tmp_path):
    store = _store(tmp_path)
    sources = [_source(tmp_path, i, PNG + b"shared") for i in range(2)]
    result = _apply(store, tmp_path, sources)
    assert (result["imported"], result["deduplicated"], result["journal_committed"]) == (1, 1, 2)
    assert result["asset_refs"][0]["asset_uri"] == result["asset_refs"][1]["asset_uri"]
    assert result["asset_refs"][0]["source_item_id"] != result["asset_refs"][1]["source_item_id"]
    assert "question" not in json.dumps(result["asset_refs"])


def test_legacy_registration_rejects_paths_and_non_image_media_without_writes(tmp_path):
    store = _store(tmp_path)
    assert hasattr(store, "register_legacy_asset")
    before = store.database_path.read_bytes()
    for media, ref in [("text/plain", "migration-source://legacy-test/image-1"),
                       ("image/png", "C:/private/image.png")]:
        with pytest.raises(GatewayError):
            store.register_legacy_asset(PNG, media, import_batch_id=RUN, source_ref=ref)
    assert store.database_path.read_bytes() == before


def test_same_run_rejects_changed_explicit_source_set_without_target_mutation(tmp_path):
    store = _store(tmp_path)
    source = _source(tmp_path, 0, PNG + b"new")
    _apply(store, tmp_path, [source])
    before = store.database_path.read_bytes()
    snapshots = set((tmp_path / "backups").iterdir())
    with pytest.raises(ValueError, match="source set conflicts"):
        _apply(store, tmp_path, [replace(source, source_order=1)])
    assert store.database_path.read_bytes() == before
    assert set((tmp_path / "backups").iterdir()) == snapshots


def test_oversized_image_descriptor_rejected_before_target_write(tmp_path):
    store = _store(tmp_path)
    source = replace(_source(tmp_path, 0, PNG), byte_count=32 * 1024 * 1024 + 1)
    before = store.database_path.read_bytes()
    with pytest.raises(ValueError, match="image source"):
        _apply(store, tmp_path, [source])
    assert store.database_path.read_bytes() == before
    assert not (tmp_path / "backups").exists()


def test_hard_linked_image_rejected_before_target_write(tmp_path):
    import os
    store = _store(tmp_path)
    source = _source(tmp_path, 0, PNG)
    try:
        os.link(source.path, source.path.with_name("alias.png"))
    except OSError:
        pytest.skip("Host does not permit synthetic hard links")
    before = store.database_path.read_bytes()
    with pytest.raises(ValueError):
        _apply(store, tmp_path, [source])
    assert store.database_path.read_bytes() == before


def test_legacy_registration_rejects_invalid_media_value_with_gateway_error(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(GatewayError) as error:
        store.register_legacy_asset(PNG, [], import_batch_id=RUN,
                                    source_ref="migration-source://legacy-test/image-1")
    assert error.value.code == "INVALID_ARGUMENT"


def test_failed_second_image_preserves_first_checkpoint_and_replays_safely(tmp_path, monkeypatch):
    store = _store(tmp_path)
    sources = [_source(tmp_path, i, PNG + f"new-{i}".encode()) for i in range(2)]
    insert = SQLiteDocumentStore._insert_asset

    def fail_second(self, connection, data, media_type, created):
        if data == sources[1].path.read_bytes():
            raise GatewayError("CONFLICT", "Synthetic second image failure")
        return insert(self, connection, data, media_type, created)

    monkeypatch.setattr(SQLiteDocumentStore, "_insert_asset", fail_second)
    with pytest.raises(GatewayError):
        _apply(store, tmp_path, sources)
    assert store.fetch_asset_record("asset://sha256/" + sources[0].source_hash) is not None
    assert store.fetch_asset_record("asset://sha256/" + sources[1].source_hash) is None
    assert not (store.asset_root / sources[1].source_hash[:2] / sources[1].source_hash).exists()
    with sqlite3.connect(tmp_path / "journal" / "journal.sqlite3") as connection:
        assert connection.execute("SELECT legacy_item_id,status FROM migration_items ORDER BY legacy_item_id").fetchall() \
            == [("image-0", "committed"), ("image-1", "planned")]
    monkeypatch.setattr(SQLiteDocumentStore, "_insert_asset", insert)
    result = _apply(store, tmp_path, sources)
    assert (result["imported"], result["deduplicated"], result["journal_committed"]) == (1, 1, 2)


def test_restored_asset_target_rejects_later_committed_journal_before_writes(tmp_path):
    store = _store(tmp_path)
    source = _source(tmp_path, 0, PNG + b"new")
    result = _apply(store, tmp_path, [source])
    store.database_path.write_bytes(Path(result["snapshot"]["private_snapshot"]).read_bytes())
    blob = store.asset_root / source.source_hash[:2] / source.source_hash
    blob.unlink()
    before = store.database_path.read_bytes()
    snapshots = set((tmp_path / "backups").iterdir())
    with pytest.raises(ValueError, match="checkpoint"):
        _apply(store, tmp_path, [source])
    assert store.database_path.read_bytes() == before
    assert not blob.exists()
    assert set((tmp_path / "backups").iterdir()) == snapshots


def test_hardlinked_old_blob_rejected_before_new_import(tmp_path):
    import os
    store = _store(tmp_path)
    old = store.register_asset(PNG + b"existing-0", "image/png")
    blob = store.asset_root / old.sha256[:2] / old.sha256
    try:
        os.link(blob, tmp_path / "protected-original.png")
    except OSError:
        pytest.skip("Host does not permit synthetic hard links")
    source = _source(tmp_path, 0, PNG + b"new")
    with pytest.raises((GatewayError, ValueError)):
        _apply(store, tmp_path, [source])
    assert store.fetch_asset_record("asset://sha256/" + source.source_hash) is None


def test_domain_commit_before_journal_failure_keeps_original_imported_time_on_retry(tmp_path, monkeypatch):
    from cognivault.migration.manifest import MigrationJournal
    store = _store(tmp_path)
    source = _source(tmp_path, 0, PNG + b"new")
    checkpoint = MigrationJournal.add_batch

    def fail_committed(self, items, *, batch_number):
        values = tuple(items)
        if values[0].status == "committed":
            raise RuntimeError("Synthetic journal commit failure")
        return checkpoint(self, values, batch_number=batch_number)

    monkeypatch.setattr(MigrationJournal, "add_batch", fail_committed)
    with pytest.raises(RuntimeError):
        _apply(store, tmp_path, [source])
    uri = "asset://sha256/" + source.source_hash
    before = store.get_write_provenance("asset", uri)
    assert before["imported_at"]
    monkeypatch.setattr(MigrationJournal, "add_batch", checkpoint)
    result = _apply(store, tmp_path, [source])
    assert (result["imported"], result["deduplicated"], result["journal_committed"]) == (0, 1, 1)
    assert store.get_write_provenance("asset", uri) == before
    with sqlite3.connect(tmp_path / "journal" / "journal.sqlite3") as connection:
        assert connection.execute("SELECT imported_at FROM migration_items WHERE status='committed'").fetchone()[0] \
            == before["imported_at"]


@pytest.mark.parametrize("suffix", ("", "-wal", "-shm", "-journal"))
def test_legacy_domain_rejects_hardlinked_database_or_sidecar_before_write(tmp_path, suffix):
    import os
    store = _store(tmp_path)
    path = Path(str(store.database_path) + suffix)
    if suffix:
        path.write_bytes(b"")
    try:
        os.link(path, tmp_path / "protected-file")
    except OSError:
        pytest.skip("Host does not permit synthetic hard links")
    before = store.database_path.read_bytes()
    with pytest.raises(GatewayError):
        store.register_legacy_asset(PNG + b"new", "image/png", import_batch_id=RUN,
                                    source_ref="migration-source://legacy-test/image-0")
    assert store.database_path.read_bytes() == before


def test_asset_apply_rejects_multi_image_atomic_unit_before_writes(tmp_path):
    from cognivault.migration.legacy_assets import apply_assets
    store = _store(tmp_path)
    before = store.database_path.read_bytes()
    with pytest.raises(ValueError):
        apply_assets(database=store.database_path, asset_root=store.asset_root,
                     sources=[_source(tmp_path, 0, PNG + b"new")],
                     journal_path=tmp_path / "journal" / "journal.sqlite3",
                     backup_root=tmp_path / "backups", run_id=RUN, batch_size=8)
    assert store.database_path.read_bytes() == before
    assert not (tmp_path / "backups").exists()


def test_domain_registration_rejects_hardlinked_prospective_blob(tmp_path):
    import os
    store = _store(tmp_path)
    data = PNG + b"new"
    digest = hashlib.sha256(data).hexdigest()
    original = tmp_path / "protected-image.png"
    original.write_bytes(data)
    blob = store.asset_root / digest[:2] / digest
    blob.parent.mkdir(exist_ok=True)
    try:
        os.link(original, blob)
    except OSError:
        pytest.skip("Host does not permit synthetic hard links")
    before = store.database_path.read_bytes()
    with pytest.raises(GatewayError):
        store.register_legacy_asset(data, "image/png", import_batch_id=RUN,
                                    source_ref="migration-source://legacy-test/image-0")
    assert store.database_path.read_bytes() == before
    assert original.read_bytes() == data


def test_corrupt_asset_metadata_is_rejected_before_any_blob_file_open(tmp_path, monkeypatch):
    from cognivault.adapters.documents import AssetRecord
    store = _store(tmp_path)
    (tmp_path / "protected.png").write_bytes(PNG)
    opened = []
    original_open = Path.open

    def observe_open(path, *args, **kwargs):
        opened.append(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", observe_open)
    with pytest.raises(GatewayError):
        store._verified_asset_snapshot(AssetRecord("asset://sha256/" + "0" * 64,
                                                  "image/png", len(PNG), "../../protected.png"))
    assert opened == []


def test_native_asset_reuse_journal_time_is_explicitly_checkpoint_acquisition(tmp_path):
    store = _store(tmp_path)
    source = _source(tmp_path, 0, PNG + b"existing-0")
    _apply(store, tmp_path, [source])
    with sqlite3.connect(tmp_path / "journal" / "journal.sqlite3") as connection:
        reason, recorded = connection.execute(
            "SELECT reason_code, imported_at FROM migration_items WHERE status='committed'"
        ).fetchone()
    assert reason == "native_content_reuse_checkpoint_time"
    assert recorded.endswith("Z")
    assert store.get_write_provenance("asset", "asset://sha256/" + source.source_hash)["imported_at"] is None
