"""Compose the local Gateway only from an explicitly supplied TOML file."""

from pathlib import Path
from dataclasses import replace
import re
import shutil
import tomllib

from .adapters.history import NotConfiguredHistoryBackend, SQLiteHistoryBackend
from .adapters.documents import SQLiteDocumentStore
from .adapters.qmd_snapshot import QmdSnapshotSources, create_disposable_qmd_runtime
from .adapters.study_qmd import QmdStudyBackend
from .config import AppConfig
from .gateway import Gateway


def load_gateway_from_config(config_file: Path) -> Gateway:
    """Read local configuration without creating any runtime state or running QMD."""
    with Path(config_file).open("rb") as stream:
        raw = tomllib.load(stream)
    try:
        version = raw["gateway"]["version"]
        study = raw["study"]
        history_config = raw["history"]
        history = history_config["backend"]
    except (KeyError, TypeError):
        raise ValueError("Invalid local configuration") from None
    if (type(version) is not str or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None
            or type(study) is not dict or history not in {"not_configured", "sqlite"}):
        raise ValueError("Invalid local configuration")
    if study == {"backend": "not_configured"}:
        root, collection, qmd_version = None, "studyvault", "2.8.3"
    else:
        root = study.get("root")
        collection = study.get("qmd_collection")
        qmd_version = study.get("qmd_version")
        if (study.get("backend") not in (None, "qmd")
                or any(type(value) is not str or not value for value in (root, collection, qmd_version))
                or not Path(root).is_absolute() or collection != "studyvault" or qmd_version != "2.8.3"):
            raise ValueError("Invalid local configuration")

    if history == "sqlite":
        database = history_config.get("database")
        migration_inbox = history_config.get("migration_inbox")
        if set(history_config) - {"backend", "database", "migration_inbox"} \
                or not {"backend", "database"} <= set(history_config) \
                or type(database) is not str or not database or not Path(database).is_absolute():
            raise ValueError("Invalid local configuration")
        if migration_inbox is not None and (type(migration_inbox) is not str or not migration_inbox
                                            or not Path(migration_inbox).is_absolute()):
            raise ValueError("Invalid local configuration")
        history_backend = SQLiteHistoryBackend(Path(database))
        history_database = Path(database)
    else:
        if set(history_config) != {"backend"}:
            raise ValueError("Invalid local configuration")
        history_backend = NotConfiguredHistoryBackend()
        history_database = None
        migration_inbox = None

    assets_config = raw.get("assets", {"backend": "not_configured"})
    if type(assets_config) is not dict:
        raise ValueError("Invalid local configuration")
    if assets_config.get("backend") == "sqlite":
        asset_root = assets_config.get("root")
        asset_database = assets_config.get("database")
        ingest_root = assets_config.get("ingest_root")
        if set(assets_config) - {"backend", "root", "database", "ingest_root"} \
                or not {"backend", "root", "database"} <= set(assets_config) \
                or type(asset_root) is not str or type(asset_database) is not str \
                or not asset_root or not asset_database \
                or not Path(asset_root).is_absolute() or not Path(asset_database).is_absolute():
            raise ValueError("Invalid local configuration")
        if ingest_root is not None and (type(ingest_root) is not str or not ingest_root
                                        or not Path(ingest_root).is_absolute()):
            raise ValueError("Invalid local configuration")
        document_store = SQLiteDocumentStore(Path(asset_root), Path(asset_database))
    elif assets_config == {"backend": "not_configured"}:
        asset_root = None
        asset_database = None
        ingest_root = None
        document_store = None
    else:
        raise ValueError("Invalid local configuration")

    permissions = raw.get("permissions", {"capabilities": ["read"]})
    if type(permissions) is not dict or set(permissions) != {"capabilities"}:
        raise ValueError("Invalid local configuration")
    configured_capabilities = permissions["capabilities"]
    if type(configured_capabilities) is not list or any(type(item) is not str for item in configured_capabilities) \
            or len(set(configured_capabilities)) != len(configured_capabilities) \
            or not set(configured_capabilities) <= {"read", "write", "ingest", "projection", "admin"}:
        raise ValueError("Invalid local configuration")

    config = AppConfig(version, Path(root) if root else None, collection, qmd_version, history_database,
                       Path(asset_root) if asset_root else None,
                       Path(asset_database) if asset_database else None,
                       Path(ingest_root) if ingest_root else None,
                       Path(migration_inbox) if migration_inbox else None)
    recovery = raw.get("recovery")
    if recovery is not None:
        if type(recovery) is not dict or "root" not in recovery \
                or set(recovery)-{"root","sidecar_root","exclude_sidecar_paths","include_study"}:
            raise ValueError("Invalid local configuration")
        paths=[recovery.get(k) for k in ("root","sidecar_root")]
        exclusions=recovery.get("exclude_sidecar_paths",[])
        include_study=recovery.get("include_study",True)
        if type(include_study) is not bool:
            raise ValueError("Invalid local configuration")
        if any(p is not None and (type(p) is not str or not p or not Path(p).is_absolute()) for p in paths) \
                or paths[0] is None or type(exclusions) is not list \
                or any(type(p) is not str or not p or len(p)>500 or "\\" in p or ":" in p
                       or any(ord(c)<32 for c in p) or any(c in {"",".",".."} for c in p.split("/")) for p in exclusions):
            raise ValueError("Invalid local configuration")
        config=replace(config,recovery_root=Path(paths[0]),recovery_sidecar_root=Path(paths[1]) if paths[1] else None,
                       recovery_sidecar_exclusions=tuple(exclusions),gateway_config_file=Path(config_file).resolve(),
                       recovery_include_study=include_study)
    runtime_raw = study.get("qmd_runtime")
    if root is None:
        study_backend = None
        qmd_discoverable = lambda: False
    elif runtime_raw is None:
        executable = study.get("qmd_executable")
        if type(executable) is not str or not executable:
            raise ValueError("Invalid local configuration")
        study_backend = QmdStudyBackend(
            config, qmd_executable=executable, approved_qmd_version=qmd_version
        )
        qmd_discoverable = lambda: shutil.which(executable) is not None
    else:
        if not isinstance(runtime_raw, dict):
            raise ValueError("Invalid local configuration")
        try:
            sources = QmdSnapshotSources(
                node_executable=Path(runtime_raw["node_executable"]),
                cli_entrypoint=Path(runtime_raw["cli_entrypoint"]),
                source_config=Path(runtime_raw["config"]),
                source_index=Path(runtime_raw["index"]),
            )
        except (KeyError, TypeError):
            raise ValueError("Invalid local configuration") from None
        if any(not path.is_absolute() for path in (
            sources.node_executable, sources.cli_entrypoint, sources.source_config, sources.source_index,
        )):
            raise ValueError("Invalid local configuration")
        config=replace(config,qmd_snapshot_config=sources.source_config,qmd_snapshot_index=sources.source_index)
        study_backend = QmdStudyBackend(
            config,
            runtime_provider=lambda: create_disposable_qmd_runtime(sources),
        )
        qmd_discoverable = lambda: (
            sources.node_executable.is_file() and sources.cli_entrypoint.is_file()
        )
    return Gateway(
        config,
        study_backend,
        history_backend,
        qmd_discoverable=qmd_discoverable,
        document_store=document_store,
        capabilities=frozenset(configured_capabilities),
    )
