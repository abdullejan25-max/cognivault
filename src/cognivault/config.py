"""Explicit configuration; constructing it performs no I/O."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    gateway_version: str
    study_root: Path | None = None
    collection: str = "studyvault"
    expected_qmd_version: str = "2.8.3"
    history_database: Path | None = None
    asset_root: Path | None = None
    asset_database: Path | None = None
    asset_ingest_root: Path | None = None
    history_migration_inbox: Path | None = None
    recovery_root: Path | None = None
    recovery_sidecar_root: Path | None = None
    recovery_sidecar_exclusions: tuple[str, ...] = ()
    gateway_config_file: Path | None = None
    qmd_snapshot_config: Path | None = None
    qmd_snapshot_index: Path | None = None
    recovery_include_study: bool = True
