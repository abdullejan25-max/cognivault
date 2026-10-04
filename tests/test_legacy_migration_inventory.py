"""Synthetic source inventories; never use personal files as fixtures."""

import hashlib
import io
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

import cognivault.migration.inventory as inventory
from cognivault.migration.planner import plan_records


@pytest.mark.skipif(os.name != "nt", reason="Win32 extended path handling")
def test_verified_source_opens_long_windows_path_without_changing_logical_root(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    source = root / ("synthetic-" * 14) / ("member-" * 14) / "source.jsonl"
    extended = Path("\\\\?\\" + str(source))
    extended.parent.mkdir(parents=True)
    raw = b'{"type":"synthetic","payload":"original bytes"}\r\n'
    extended.write_bytes(raw)
    assert len(str(source)) > 260
    with inventory._open_verified_source(source, root) as stream:
        assert stream.read() == raw


def test_study_root_reuses_authority_and_archives_unpaired_wrong_answer_files(tmp_path: Path) -> None:
    study = tmp_path / "Study"
    (study / "algebra").mkdir(parents=True)
    (study / "wrong_answer" / "set-a").mkdir(parents=True)
    (study / "algebra" / "lesson.md").write_text("synthetic lesson", encoding="utf-8")
    (study / "algebra" / "book.pdf").write_bytes(b"synthetic pdf bytes")
    (study / "wrong_answer" / "set-a" / "note.md").write_text(
        "synthetic wrong-answer note", encoding="utf-8")
    (study / "wrong_answer" / "set-a" / "image.jpg").write_bytes(b"synthetic image bytes")

    records = inventory.scan_study_root(study)
    actions = {(item.category, item.legacy_source_type): item.intended_action for item in records}
    assert actions[("Study", "study_file_markdown")] == "reuse"
    assert actions[("Documents", "study_file_pdf")] == "reuse"
    assert actions[("Wrong Answers", "legacy_wrong_answer_note")] == "archive"
    assert actions[("Assets", "legacy_wrong_answer_image")] == "archive"
    assert all(item.validation_state == "unresolved" for item in records
               if item.category in {"Wrong Answers", "Assets"})
    assert all(str(study) not in repr(item) for item in records)


def test_chinese_wrong_answer_directory_is_classified_as_legacy_evidence(tmp_path: Path) -> None:
    study = tmp_path / "Study"
    evidence = study / "错题集"
    evidence.mkdir(parents=True)
    (evidence / "note.md").write_text("synthetic note", encoding="utf-8")
    (evidence / "photo.jpg").write_bytes(b"synthetic photo")

    records = inventory.scan_study_root(study)
    assert {item.category for item in records} == {"Wrong Answers", "Assets"}
    assert all(item.intended_action == "archive" for item in records)


def test_study_inventory_fingerprint_uses_metadata_without_opening_authoritative_bytes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    study = tmp_path / "Study"
    study.mkdir()
    (study / "lesson.md").write_bytes(b"synthetic authoritative content")

    def fail_read(_path: Path, **_kwargs) -> tuple[str, int, bytes]:
        raise AssertionError("Study reuse must not hash or rewrite authoritative content")

    monkeypatch.setattr(inventory, "_fingerprint_file", fail_read)
    record = inventory.scan_study_root(study)[0]
    assert record.intended_action == "reuse"
    assert len(record.source_fingerprint) == 64


def test_personal_chat_export_is_archived_without_parsing_or_rewriting_messages(tmp_path: Path) -> None:
    personal = tmp_path / "Personal"
    imports = personal / "imported-chats"
    imports.mkdir(parents=True)
    transcript = imports / "private-name.md"
    raw = ("---\ntype: imported_chat\nconversation_id: conversation-7\n"
           "created_at: 2024-06-05T10:11:12Z\n---\n## User\nsynthetic body\n").encode()
    transcript.write_bytes(raw)

    records = inventory.scan_personal_root(personal)
    assert len(records) == 1
    record = records[0]
    assert record.category == "Raw transcripts"
    assert record.legacy_item_id == "conversation-7"
    assert record.source_event_time == "2024-06-05T10:11:12Z"
    assert record.source_fingerprint == hashlib.sha256(raw).hexdigest()
    assert record.intended_action == "archive"
    assert b"synthetic body" not in repr(record).encode()
    assert str(personal) not in repr(record)


def test_personal_indexes_and_unsupported_markdown_are_explicitly_skipped(tmp_path: Path) -> None:
    personal = tmp_path / "Personal"
    personal.mkdir()
    (personal / "index.md").write_text(
        "---\ntype: imported_chat_index\n---\nsynthetic index", encoding="utf-8")
    (personal / "notes.md").write_text(
        "---\ntype: unknown_note\n---\nsynthetic note", encoding="utf-8")

    records = inventory.scan_personal_root(personal)
    assert len(records) == 2
    assert all(record.category == "Other / Unsupported" for record in records)
    assert all(record.intended_action == "skip" for record in records)


def test_personal_export_unknown_time_stays_unknown_and_malformed_frontmatter_isolated(
        tmp_path: Path) -> None:
    imports = tmp_path / "Personal" / "imported-chats"
    imports.mkdir(parents=True)
    (imports / "unknown-time.md").write_text(
        "---\ntype: imported_chat\nconversation_id: conversation-8\ncreated_at: unknown\n---\nbody",
        encoding="utf-8",
    )
    (imports / "malformed.md").write_bytes(b"---\ntype: imported_chat\n\xff")

    records = inventory.scan_personal_root(imports.parent)
    by_id = {record.legacy_item_id: record for record in records}
    assert by_id["conversation-8"].source_event_time is None
    assert by_id["conversation-8"].intended_action == "archive"
    malformed = next(record for record in records if record.validation_state == "unresolved")
    assert malformed.reason_code == "malformed_frontmatter"
    plan = plan_records([malformed], existing_targets=set(), target_health={})[0]
    assert plan.status == "unresolved"


def test_path_shaped_conversation_id_is_replaced_by_opaque_identity(tmp_path: Path) -> None:
    imports = tmp_path / "Personal" / "imported-chats"
    imports.mkdir(parents=True)
    (imports / "private-name.md").write_text(
        "---\ntype: imported_chat\nconversation_id: private.md\n---\nsynthetic body",
        encoding="utf-8",
    )
    record = inventory.scan_personal_root(imports.parent)[0]
    assert record.legacy_item_id != "private.md"
    assert record.legacy_item_id.startswith("legacy-id-")
    assert "private.md" not in repr(record)


def test_sqlite_snapshot_copy_enforces_byte_limit_during_streaming(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root = tmp_path / "legacy"
    source_root.mkdir()
    source = source_root / "memory.sqlite3"
    source.write_bytes(b"a")
    target = tmp_path / "scratch.sqlite3"

    @contextmanager
    def growing_source(_path: Path, _root: Path):
        yield io.BytesIO(b"ab")

    monkeypatch.setattr(inventory, "_open_verified_source", growing_source)
    monkeypatch.setattr(inventory, "_stream_signature", lambda _stream: (1, 2, 1))
    monkeypatch.setattr(inventory, "_path_has_reparse_point", lambda _path: False)
    with pytest.raises(inventory.InventoryError, match="snapshot limit exceeded"):
        inventory._copy_verified_file(source, target, source_root=source_root, max_bytes=1)
    assert target.stat().st_size <= 1


def test_fingerprint_checks_stat_size_and_never_reads_past_expected_size(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root = tmp_path / "legacy"
    source_root.mkdir()
    source = source_root / "changed.md"
    stream = io.BytesIO(b"ab")

    @contextmanager
    def opened_source(_path: Path, _root: Path):
        yield stream

    signatures = iter(((1, 2, 1), (1, 2, 2)))
    monkeypatch.setattr(inventory, "_open_verified_source", opened_source)
    monkeypatch.setattr(inventory, "_stream_signature", lambda _stream: next(signatures))
    monkeypatch.setattr(inventory, "_path_has_reparse_point", lambda _path: False)

    with pytest.raises(inventory.InventoryError, match="changed during inventory"):
        inventory._fingerprint_file(
            source, source_root=source_root, expected_size=1, max_bytes=1,
        )
    assert stream.tell() == 1


def test_fingerprint_refuses_file_growth_before_reading(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root = tmp_path / "legacy"
    source_root.mkdir()
    source = source_root / "changed.md"
    stream = io.BytesIO(b"ab")

    @contextmanager
    def opened_source(_path: Path, _root: Path):
        yield stream

    monkeypatch.setattr(inventory, "_open_verified_source", opened_source)
    monkeypatch.setattr(inventory, "_stream_signature", lambda _stream: (1, 2, 2))
    monkeypatch.setattr(inventory, "_path_has_reparse_point", lambda _path: False)
    with pytest.raises(inventory.InventoryError, match="changed during inventory"):
        inventory._fingerprint_file(
            source, source_root=source_root, expected_size=1, max_bytes=1,
        )
    assert stream.tell() == 0


def test_sqlite_snapshot_source_recheck_keeps_the_copied_size_limit(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root = tmp_path / "legacy"
    source_root.mkdir()
    database = source_root / "memory.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE memories(id TEXT)")
    original = inventory._fingerprint_file
    source_checks = []

    def record_source_check(path, **kwargs):
        if Path(path) == database:
            source_checks.append(kwargs.copy())
        return original(path, **kwargs)

    monkeypatch.setattr(inventory, "_fingerprint_file", record_source_check)
    with inventory._private_sqlite_snapshot(database, authorized_root=source_root) as snapshot:
        assert snapshot.is_file()
    assert source_checks
    assert source_checks[-1]["expected_size"] == database.stat().st_size
    assert source_checks[-1]["max_bytes"] == database.stat().st_size


def test_sqlite_snapshot_temp_root_cannot_overlap_another_protected_root(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root = tmp_path / "legacy"
    source_root.mkdir()
    database = source_root / "memory.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE memories(id TEXT)")
    other_protected_root = tmp_path / "other-source"
    temporary_root = other_protected_root / "temp"
    temporary_root.mkdir(parents=True)
    monkeypatch.setattr(inventory.tempfile, "gettempdir", lambda: str(temporary_root))

    with pytest.raises(inventory.InventoryError, match="overlaps protected data"):
        with inventory._private_sqlite_snapshot(
                database, authorized_root=source_root,
                protected_paths=(other_protected_root,)):
            pytest.fail("snapshot must not be created inside another protected root")
    assert list(temporary_root.iterdir()) == []


def test_native_memory_fact_is_typed_as_unresolved_annotation_and_read_only(
        tmp_path: Path) -> None:
    project = tmp_path / "legacy-project"
    database_dir = project / "data"
    database_dir.mkdir(parents=True)
    database = database_dir / "synthetic_memory.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE memories(id TEXT, kind TEXT, content TEXT, created_at TEXT)")
        connection.execute(
            "INSERT INTO memories VALUES (?, ?, ?, ?)",
            ("fact-1", "preference", "synthetic fact", "2024-01-02T03:04:05Z"),
        )
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    records = inventory.scan_native_memory_db(database, project_root=project)
    assert len(records) == 1
    assert records[0].category == "Atomic Facts"
    assert records[0].target_type == "history_annotation"
    assert records[0].intended_action == "import"
    assert records[0].validation_state == "unresolved"
    assert records[0].reason_code == "annotation_contract_pending"
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert "synthetic fact" not in repr(records[0])


def test_invalid_native_memory_row_remains_unresolved_without_invented_time(
        tmp_path: Path) -> None:
    project = tmp_path / "legacy-project"
    database_dir = project / "data"
    database_dir.mkdir(parents=True)
    database = database_dir / "synthetic_memory.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE memories(id TEXT, kind TEXT, content TEXT, created_at TEXT)")
        connection.execute("INSERT INTO memories VALUES (?, ?, ?, ?)",
                           (None, "preference", sqlite3.Binary(b"\xff"), "not-a-time"))

    record = inventory.scan_native_memory_db(database, project_root=project)[0]
    assert record.validation_state == "unresolved"
    assert record.legacy_item_id == "row-1"
    assert record.source_event_time is None
    assert record.source_size_bytes == 1
    assert "not-a-time" not in repr(record)


def test_asset_target_hashes_are_read_only_and_validate_schema(tmp_path: Path) -> None:
    database = tmp_path / "assets.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE assets(uri TEXT, sha256 TEXT, media_type TEXT, size INTEGER)")
        connection.execute("INSERT INTO assets VALUES (?, ?, ?, ?)",
                           ("asset://sha256/" + "a" * 64, "a" * 64, "image/jpeg", 12))
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    assert inventory.read_existing_asset_hashes(database) == {("asset", "a" * 64)}
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before

    invalid = tmp_path / "invalid.db"
    with sqlite3.connect(invalid) as connection:
        connection.execute("CREATE TABLE assets(uri TEXT)")
    with pytest.raises(inventory.InventoryError, match="Invalid asset target"):
        inventory.read_existing_asset_hashes(invalid)


def test_wal_native_database_and_sidecars_remain_unchanged_during_inventory(
        tmp_path: Path) -> None:
    project = tmp_path / "legacy-project"
    project.mkdir()
    database = project / "memory.sqlite3"
    writer = sqlite3.connect(database)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].casefold() == "wal"
    writer.execute("CREATE TABLE memories(id TEXT, kind TEXT, content TEXT, created_at TEXT)")
    writer.execute("INSERT INTO memories VALUES (?, ?, ?, ?)",
                   ("fact-1", "preference", "synthetic fact", "2024-01-02T03:04:05Z"))
    writer.commit()
    sidecars = (database, Path(str(database) + "-wal"), Path(str(database) + "-shm"))
    before = {path.name: (path.read_bytes(), path.stat().st_size, path.stat().st_mtime_ns)
              for path in sidecars if path.exists()}

    assert len(inventory.scan_native_memory_db(database, project_root=project)) == 1

    after = {path.name: (path.read_bytes(), path.stat().st_size, path.stat().st_mtime_ns)
             for path in sidecars if path.exists()}
    assert after == before

    assets = tmp_path / "assets.sqlite3"
    asset_writer = sqlite3.connect(assets)
    assert asset_writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].casefold() == "wal"
    asset_writer.execute("CREATE TABLE assets(uri TEXT, sha256 TEXT, media_type TEXT, size INTEGER)")
    asset_writer.execute("INSERT INTO assets VALUES (?, ?, ?, ?)",
                         ("asset://sha256/" + "a" * 64, "a" * 64, "image/jpeg", 12))
    asset_writer.commit()
    asset_sidecars = (assets, Path(str(assets) + "-wal"), Path(str(assets) + "-shm"))
    asset_before = {path.name: (path.read_bytes(), path.stat().st_size, path.stat().st_mtime_ns)
                    for path in asset_sidecars if path.exists()}
    assert inventory.read_existing_asset_hashes(assets) == {("asset", "a" * 64)}
    asset_after = {path.name: (path.read_bytes(), path.stat().st_size, path.stat().st_mtime_ns)
                   for path in asset_sidecars if path.exists()}

    writer.close()
    asset_writer.close()
    assert asset_after == asset_before


def test_inventory_rejects_reparse_source_root_without_scanning(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(inventory, "_path_has_reparse_point", lambda _path: True)
    with pytest.raises(inventory.InventoryError, match="Unsafe migration source"):
        inventory.scan_study_root(source)


def test_opened_handle_outside_source_root_is_rejected_even_if_precheck_misses(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    path = source / "record.md"
    path.write_text("synthetic inside content", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("synthetic outside content", encoding="utf-8")
    monkeypatch.setattr(inventory, "_path_has_reparse_point", lambda _path: False)
    monkeypatch.setattr(inventory, "_opened_file_path", lambda _stream: outside, raising=False)
    with pytest.raises(inventory.InventoryError, match="Unsafe migration source"):
        inventory.scan_personal_root(source)


def test_native_memory_large_row_is_unresolved_without_loading_its_body(tmp_path: Path) -> None:
    project = tmp_path / "legacy-project"
    project.mkdir()
    database = project / "memory.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE memories(id TEXT, kind TEXT, content TEXT, created_at TEXT)")
        connection.execute("INSERT INTO memories VALUES (?, ?, ?, ?)",
                           ("fact-large", "preference", "x" * (2 * 1024 * 1024), None))

    record = inventory.scan_native_memory_db(database, project_root=project)[0]
    assert record.validation_state == "unresolved"
    assert record.reason_code == "legacy_fact_exceeds_limit"
    assert record.source_size_bytes == 2 * 1024 * 1024
