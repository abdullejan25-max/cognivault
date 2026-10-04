"""Safe creation of disposable copies of QMD's derived SQLite index."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Iterator

from .study_qmd import QmdRuntime


@dataclass(frozen=True)
class QmdSnapshotSources:
    """Verified local inputs used to create a one-search QMD runtime."""

    node_executable: Path
    cli_entrypoint: Path
    source_config: Path
    source_index: Path


def create_sqlite_snapshot(source: Path, destination: Path) -> None:
    """Copy a consistent SQLite snapshot without opening the source for writes.

    SQLite's backup API includes committed WAL content, unlike copying only the
    main database file.  The destination must be a new file in a pre-created,
    disposable runtime directory.
    """

    source_path = source.resolve(strict=True)
    destination_path = destination.resolve(strict=False)
    if not source_path.is_file():
        raise ValueError("source must be a SQLite file")
    if not destination_path.parent.is_dir():
        raise ValueError("destination parent must exist")
    if destination_path.exists():
        raise ValueError("destination must not already exist")
    if source_path == destination_path:
        raise ValueError("source and destination must differ")

    source_uri = f"{source_path.as_uri()}?mode=ro"
    try:
        source_connection = sqlite3.connect(source_uri, uri=True)
        try:
            destination_connection = sqlite3.connect(destination_path)
            try:
                source_connection.backup(destination_connection)
            finally:
                destination_connection.close()
        finally:
            source_connection.close()
    except sqlite3.Error:
        destination_path.unlink(missing_ok=True)
        raise


@contextmanager
def create_disposable_qmd_runtime(sources: QmdSnapshotSources) -> Iterator[QmdRuntime]:
    """Create and clean up all mutable QMD state for exactly one search."""

    source_config = sources.source_config.resolve(strict=True)
    source_index = sources.source_index.resolve(strict=True)
    if not source_config.is_file() or not source_index.is_file():
        raise ValueError("QMD snapshot sources must be files")

    with tempfile.TemporaryDirectory(prefix="cognivault-qmd-") as directory:
        root = Path(directory)
        config_dir = root / "config"
        index_dir = root / "index"
        cache_dir = root / "cache"
        home_dir = root / "home"
        userprofile_dir = root / "userprofile"
        cwd = root / "work"
        for path in (config_dir, index_dir, cache_dir, home_dir, userprofile_dir, cwd):
            path.mkdir()
        shutil.copy2(source_config, config_dir / "index.yml")
        index_path = index_dir / "index.sqlite"
        create_sqlite_snapshot(source_index, index_path)
        yield QmdRuntime(
            runtime_root=root,
            node_executable=sources.node_executable,
            cli_entrypoint=sources.cli_entrypoint,
            package_json=sources.cli_entrypoint.resolve(strict=True).parents[2] / "package.json",
            cwd=cwd,
            index_path=index_path,
            config_dir=config_dir,
            cache_dir=cache_dir,
            home_dir=home_dir,
            userprofile_dir=userprofile_dir,
        )
