from pathlib import Path

import pytest

from cognivault.adapters.documents import SQLiteDocumentStore
from cognivault.runtime import load_gateway_from_config


def _config(path: Path, root: Path, assets: str) -> None:
    path.write_text('[gateway]\nversion = "0.1.0"\n[study]\n'
                    f'root = "{root.as_posix()}"\n'
                    'qmd_collection = "studyvault"\nqmd_version = "2.8.3"\n'
                    'qmd_executable = "synthetic-missing-qmd"\n'
                    '[history]\nbackend = "not_configured"\n' + assets, encoding="utf-8")


def test_explicit_asset_configuration_has_no_startup_write(tmp_path: Path) -> None:
    study = tmp_path / "study"
    study.mkdir()
    assets = tmp_path / "assets"
    assets.mkdir()
    database = tmp_path / "private.db"
    config = tmp_path / "config.toml"
    _config(config, study, f'[assets]\nbackend = "sqlite"\nroot = "{assets.as_posix()}"\n'
                            f'database = "{database.as_posix()}"\n')
    gateway = load_gateway_from_config(config)
    assert isinstance(gateway.document_store, SQLiteDocumentStore)
    assert not database.exists()


def test_asset_ingest_root_is_explicit_and_optional(tmp_path: Path) -> None:
    study = tmp_path / "study"
    study.mkdir()
    assets = tmp_path / "assets"
    assets.mkdir()
    sources = tmp_path / "sources"
    sources.mkdir()
    config = tmp_path / "config.toml"
    _config(config, study, f'[assets]\nbackend = "sqlite"\nroot = "{assets.as_posix()}"\n'
                            f'database = "{(tmp_path / "private.db").as_posix()}"\n'
                            f'ingest_root = "{sources.as_posix()}"\n')
    gateway = load_gateway_from_config(config)
    assert gateway.config.asset_ingest_root == sources


@pytest.mark.parametrize("assets", [
    '[assets]\nbackend = "sqlite"\nroot = "relative"\ndatabase = "C:/private.db"\n',
    '[assets]\nbackend = "sqlite"\nroot = "C:/private"\n',
    '[assets]\nbackend = "other"\n',
])
def test_asset_config_rejects_implicit_paths(tmp_path: Path, assets: str) -> None:
    study = tmp_path / "study"
    study.mkdir()
    config = tmp_path / "config.toml"
    _config(config, study, assets)
    with pytest.raises(ValueError, match="Invalid local configuration"):
        load_gateway_from_config(config)
