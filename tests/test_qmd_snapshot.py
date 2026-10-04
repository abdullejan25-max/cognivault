"""Consistent, read-only snapshots of QMD's derived SQLite index."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from cognivault.adapters.qmd_snapshot import (
    QmdSnapshotSources,
    create_disposable_qmd_runtime,
    create_sqlite_snapshot,
)


def test_snapshot_includes_committed_wal_data_without_changing_source(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    destination = tmp_path / "runtime" / "index.sqlite"
    destination.parent.mkdir()

    writer = sqlite3.connect(source)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE documents (value TEXT NOT NULL)")
    writer.execute("INSERT INTO documents VALUES ('before-wal')")
    writer.commit()
    writer.execute("INSERT INTO documents VALUES ('committed-in-wal')")
    writer.commit()

    source_stat_before = source.stat()
    create_sqlite_snapshot(source, destination)
    source_stat_after = source.stat()

    with sqlite3.connect(destination) as snapshot:
        values = snapshot.execute("SELECT value FROM documents ORDER BY rowid").fetchall()
    writer.close()

    assert values == [("before-wal",), ("committed-in-wal",)]
    assert source_stat_after.st_mtime_ns == source_stat_before.st_mtime_ns
    assert source_stat_after.st_size == source_stat_before.st_size


def test_disposable_runtime_copies_only_derived_qmd_state(tmp_path: Path) -> None:
    source_config = tmp_path / "protected-config.yml"
    source_config.write_text("collections: {}\n", encoding="utf-8")
    source_index = tmp_path / "protected-index.sqlite"
    with sqlite3.connect(source_index) as database:
        database.execute("CREATE TABLE entries (value TEXT NOT NULL)")
        database.execute("INSERT INTO entries VALUES ('derived-source')")
    package = tmp_path / "qmd-package"
    cli = package / "dist" / "cli" / "qmd.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// synthetic cli", encoding="utf-8")
    (package / "package.json").write_text(
        '{"name":"@tobilu/qmd","version":"2.8.3"}', encoding="utf-8"
    )
    node = tmp_path / "node.exe"
    node.write_bytes(b"synthetic node")
    source_config_before = source_config.read_bytes()
    source_index_before = source_index.read_bytes()

    sources = QmdSnapshotSources(
        node_executable=node,
        cli_entrypoint=cli,
        source_config=source_config,
        source_index=source_index,
    )
    with create_disposable_qmd_runtime(sources) as runtime:
        assert runtime.runtime_root.is_dir()
        assert runtime.config_dir.joinpath("index.yml").read_bytes() == source_config_before
        snapshot = sqlite3.connect(runtime.index_path)
        try:
            assert snapshot.execute("SELECT value FROM entries").fetchall() == [("derived-source",)]
        finally:
            snapshot.close()
        assert runtime.runtime_root != tmp_path

    assert source_config.read_bytes() == source_config_before
    assert source_index.read_bytes() == source_index_before
