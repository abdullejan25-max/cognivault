"""Opt-in real-data QMD smoke that preserves every protected original."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil

import pytest

from cognivault.adapters.qmd_snapshot import create_sqlite_snapshot
from cognivault.adapters.study_qmd import QmdRuntime, QmdStudyBackend
from cognivault.config import AppConfig


def _manifest(root: Path) -> dict[str, tuple[str, int, int]]:
    return {
        path.relative_to(root).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _file_manifest(paths: list[Path]) -> dict[str, tuple[str, int, int]]:
    return {
        path.name: (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in paths
        if path.is_file()
    }


def _assert_source_index_is_unchanged_except_sqlite_shm_mtime(
    before: dict[str, tuple[str, int, int]], after: dict[str, tuple[str, int, int]]
) -> None:
    """SQLite WAL readers may refresh only the derived SHM file's mtime."""

    assert set(after) == set(before)
    for name, (before_hash, before_size, before_mtime) in before.items():
        after_hash, after_size, after_mtime = after[name]
        assert (after_hash, after_size) == (before_hash, before_size)
        if name != "index.sqlite-shm":
            assert after_mtime == before_mtime


def _required_environment() -> dict[str, Path | str]:
    names = {
        "node": "QMD_REAL_SMOKE_NODE_EXE",
        "cli": "QMD_REAL_SMOKE_CLI_ENTRYPOINT",
        "config": "QMD_REAL_SMOKE_CONFIG",
        "index": "QMD_REAL_SMOKE_INDEX",
        "study": "QMD_REAL_SMOKE_STUDY_ROOT",
        "query": "QMD_REAL_SMOKE_QUERY",
    }
    values = {key: os.environ.get(name) for key, name in names.items()}
    if not all(values.values()):
        pytest.skip("set QMD_REAL_SMOKE_* variables to run against protected local data")
    return {
        "node": Path(values["node"]),
        "cli": Path(values["cli"]),
        "config": Path(values["config"]),
        "index": Path(values["index"]),
        "study": Path(values["study"]),
        "query": values["query"],
    }


def test_real_qmd_search_uses_only_disposable_derived_state(tmp_path: Path) -> None:
    values = _required_environment()
    node = values["node"]
    cli = values["cli"]
    source_config = values["config"]
    source_index = values["index"]
    study_root = values["study"]
    query = values["query"]
    assert isinstance(node, Path)
    assert isinstance(cli, Path)
    assert isinstance(source_config, Path)
    assert isinstance(source_index, Path)
    assert isinstance(study_root, Path)
    assert isinstance(query, str)
    assert node.is_file() and cli.is_file() and source_config.is_file() and source_index.is_file()
    assert study_root.is_dir()

    protected_study_before = _manifest(study_root)
    protected_config_before = _file_manifest([source_config])
    protected_index_before = _file_manifest([source_index, Path(f"{source_index}-wal"), Path(f"{source_index}-shm")])

    runtime_root = tmp_path / "disposable-qmd-runtime"
    config_dir = runtime_root / "config"
    index_dir = runtime_root / "index"
    for directory in (config_dir, index_dir, runtime_root / "cache", runtime_root / "home", runtime_root / "userprofile", runtime_root / "work"):
        directory.mkdir(parents=True)
    runtime_config = config_dir / "index.yml"
    runtime_index = index_dir / "index.sqlite"
    shutil.copy2(source_config, runtime_config)
    create_sqlite_snapshot(source_index, runtime_index)

    runtime = QmdRuntime(
        runtime_root=runtime_root,
        node_executable=node,
        cli_entrypoint=cli,
        package_json=cli.parents[2] / "package.json",
        cwd=runtime_root / "work",
        index_path=runtime_index,
        config_dir=config_dir,
        cache_dir=runtime_root / "cache",
        home_dir=runtime_root / "home",
        userprofile_dir=runtime_root / "userprofile",
    )
    backend = QmdStudyBackend(
        AppConfig(gateway_version="0.1.0", study_root=study_root),
        runtime=runtime,
        timeout_seconds=60,
    )
    try:
        results = backend.search(query, 3)
    finally:
        protected_study_after = _manifest(study_root)
        protected_config_after = _file_manifest([source_config])
        protected_index_after = _file_manifest([source_index, Path(f"{source_index}-wal"), Path(f"{source_index}-shm")])

    runtime_files = _manifest(runtime_root)

    assert isinstance(results, list)
    assert protected_study_after == protected_study_before
    assert protected_config_after == protected_config_before
    _assert_source_index_is_unchanged_except_sqlite_shm_mtime(
        protected_index_before, protected_index_after
    )
    assert "index/index.sqlite" in runtime_files
    assert "config/index.yml" in runtime_files
