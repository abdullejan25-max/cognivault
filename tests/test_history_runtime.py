"""Explicit local History configuration never discovers sources."""

from pathlib import Path

import pytest

from cognivault.adapters.history import SQLiteHistoryBackend
from cognivault.runtime import load_gateway_from_config


def _write_config(path: Path, study_root: Path, history: str) -> None:
    path.write_text(
        '[gateway]\nversion = "0.1.0"\n[study]\n'
        f'root = "{study_root.as_posix()}"\n'
        'qmd_collection = "studyvault"\nqmd_version = "2.8.3"\n'
        'qmd_executable = "missing-synthetic-qmd"\n'
        f'[history]\n{history}', encoding="utf-8",
    )


def test_runtime_uses_only_explicit_private_sqlite_path(tmp_path: Path) -> None:
    root = tmp_path / "study"
    root.mkdir()
    config = tmp_path / "local.toml"
    database = tmp_path / "private-history.db"
    _write_config(config, root, f'backend = "sqlite"\ndatabase = "{database.as_posix()}"\n')
    gateway = load_gateway_from_config(config)
    assert isinstance(gateway.history_backend, SQLiteHistoryBackend)
    assert not database.exists()
    assert gateway.health_report()["history"] == {"backend": "sqlite", "status": "unavailable"}


def test_optional_history_migration_inbox_is_explicit_and_backward_compatible(tmp_path: Path):
    root=tmp_path/"study"
    root.mkdir()
    config=tmp_path/"local.toml"
    database=tmp_path/"private-history.db"
    _write_config(config,root,f'backend="sqlite"\ndatabase="{database.as_posix()}"\n')
    assert load_gateway_from_config(config).config.history_migration_inbox is None
    with config.open("a",encoding="utf-8") as stream:
        stream.write(f'migration_inbox="{(tmp_path/"private-inbox").as_posix()}"\n')
    assert load_gateway_from_config(config).config.history_migration_inbox==tmp_path/"private-inbox"


@pytest.mark.parametrize("history", [
    'backend = "sqlite"\n',
    'backend = "sqlite"\ndatabase = "relative.db"\n',
    'backend = "other"\ndatabase = "C:/private.db"\n',
])
def test_runtime_rejects_implicit_or_invalid_history_storage(tmp_path: Path, history: str) -> None:
    root = tmp_path / "study"
    root.mkdir()
    config = tmp_path / "local.toml"
    _write_config(config, root, history)
    with pytest.raises(ValueError, match="Invalid local configuration"):
        load_gateway_from_config(config)
