"""Explicit, manifest-bounded writer for generated Obsidian projection files."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import tempfile


_MANIFEST = ".projection-manifest.json"
_SCHEMA_VERSION = 1
_MAX_FILES = 10_013
_MAX_BYTES = 64 * 1024 * 1024
_WINDOWS_RESERVED = re.compile(r"(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z")


class ProjectionWriteError(ValueError):
    """Fixed-message error that does not expose file paths or content."""

    def __init__(self, *, recovery_required: bool = False, cleanup_required: bool = False) -> None:
        self.recovery_required = recovery_required
        self.cleanup_required = cleanup_required
        super().__init__("Invalid projection output")


def _checked_relative_path(value: object) -> str:
    if type(value) is not str or not value or value == _MANIFEST or "\\" in value \
            or value.startswith("/") or re.match(r"[A-Za-z]:", value):
        raise ProjectionWriteError
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ProjectionWriteError from None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ProjectionWriteError
    parts = value.split("/")
    if any(part in {"", ".", ".."} or part.endswith((" ", "."))
           or any(char in '<>:"|?*' for char in part)
           or _WINDOWS_RESERVED.fullmatch(part)
           for part in parts):
        raise ProjectionWriteError
    if PurePosixPath(value).as_posix() != value:
        raise ProjectionWriteError
    return value


def _manifest_files(content: bytes) -> tuple[str, ...]:
    if len(content) > 4 * 1024 * 1024:
        raise ProjectionWriteError
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProjectionWriteError from None
    if type(value) is not dict or set(value) != {"files", "schema_version"} \
            or type(value.get("schema_version")) is not int \
            or value["schema_version"] != _SCHEMA_VERSION \
            or type(value.get("files")) is not list or len(value["files"]) > _MAX_FILES:
        raise ProjectionWriteError
    paths = tuple(_checked_relative_path(path) for path in value["files"])
    if paths != tuple(sorted(set(paths))):
        raise ProjectionWriteError
    return paths


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _reject_symlink_components(path: Path, stop: Path) -> None:
    current = path
    while current != stop:
        if _is_reparse_point(current):
            raise ProjectionWriteError
        parent = current.parent
        if parent == current:
            raise ProjectionWriteError
        current = parent
    if _is_reparse_point(stop):
        raise ProjectionWriteError


def _replace_file(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _restore_backup(backup: Path, destination: Path) -> None:
    staged_restore = destination.parent / f".{destination.name}.{secrets.token_hex(16)}.restore"
    os.link(backup, staged_restore)
    try:
        os.replace(staged_restore, destination)
    finally:
        staged_restore.unlink(missing_ok=True)


def _cleanup_backup_files(backup_root: Path, backups: list[Path]) -> bool:
    recovery_file = backup_root / "recovery.json"
    try:
        record = json.loads(recovery_file.read_bytes())
        record["action"] = "cleanup_only"
        _atomic_write(recovery_file, json.dumps(record, sort_keys=True).encode("utf-8"))
        for backup in backups:
            backup.unlink(missing_ok=True)
        recovery_file.unlink()
        backup_root.rmdir()
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _repository_root() -> Path:
    for start in (Path.cwd().resolve(), Path(__file__).resolve().parent):
        for candidate in (start, *start.parents):
            if (candidate / ".git").exists():
                return candidate
    return Path(__file__).resolve().parents[2]


def _set_private_directory_mode(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, 0o700)


def _atomic_write(destination: Path, content: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix=f".{destination.name}.",
            suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _replace_file(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _ensure_safe_parent(
    root: Path, relative: str, *, create: bool = False, created_dirs: list[Path] | None = None,
) -> Path:
    target = root.joinpath(*relative.split("/"))
    current = root
    for part in relative.split("/")[:-1]:
        current = current / part
        if current.exists():
            if _is_reparse_point(current) or not current.is_dir():
                raise ProjectionWriteError
        elif create:
            current.mkdir()
            if created_dirs is not None:
                created_dirs.append(current)
    if _is_reparse_point(target) or (target.exists() and not target.is_file()):
        raise ProjectionWriteError
    return target


def _write_projection(files: Mapping[str, str], projection_dir: Path) -> None:
    """Write one rendered projection into an explicit dedicated directory.

    The manifest is the sole ownership record. Unknown files are preserved;
    only stale files listed by the previous valid manifest can be removed.
    """
    if not isinstance(files, Mapping) or not isinstance(projection_dir, Path):
        raise ProjectionWriteError
    if len(files) > _MAX_FILES:
        raise ProjectionWriteError

    encoded: dict[str, bytes] = {}
    total_bytes = 0
    for raw_path, content in files.items():
        path = _checked_relative_path(raw_path)
        if type(content) is not str:
            raise ProjectionWriteError
        try:
            data = content.encode("utf-8")
        except UnicodeEncodeError:
            raise ProjectionWriteError from None
        total_bytes += len(data)
        if total_bytes > _MAX_BYTES:
            raise ProjectionWriteError
        encoded[path] = data
    if len(encoded) != len(files):
        raise ProjectionWriteError

    root = projection_dir.absolute()
    if root == Path(root.anchor) or root.name.casefold() != "v2projection":
        raise ProjectionWriteError
    parent = root.parent
    if not parent.is_dir():
        raise ProjectionWriteError
    _reject_symlink_components(root, Path(root.anchor))
    repository = _repository_root().resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if (resolved_root == repository or resolved_root.is_relative_to(repository)
            or repository.is_relative_to(resolved_root)):
        raise ProjectionWriteError
    root_created = False
    if root.exists():
        if _is_reparse_point(root) or not root.is_dir():
            raise ProjectionWriteError
    else:
        root.mkdir()
        root_created = True

    manifest_path = root / _MANIFEST
    if _is_reparse_point(manifest_path) or (manifest_path.exists() and not manifest_path.is_file()):
        raise ProjectionWriteError
    old_files: tuple[str, ...]
    if manifest_path.exists():
        try:
            old_files = _manifest_files(manifest_path.read_bytes())
        except OSError:
            raise ProjectionWriteError from None
    else:
        try:
            if next(root.iterdir(), None) is not None:
                raise ProjectionWriteError
        except OSError:
            raise ProjectionWriteError from None
        old_files = ()

    old_set = set(old_files)
    for relative in old_files:
        target = _ensure_safe_parent(root, relative)
        if target.exists() and not target.is_file():
            raise ProjectionWriteError
    for relative in encoded:
        target = _ensure_safe_parent(root, relative)
        if target.exists() and relative not in old_set:
            raise ProjectionWriteError

    manifest_bytes = (json.dumps(
        {"files": sorted(encoded), "schema_version": _SCHEMA_VERSION},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ) + "\n").encode("utf-8")
    if len(manifest_bytes) > 4 * 1024 * 1024:
        raise ProjectionWriteError

    created_paths: list[Path] = []
    created_dirs: list[Path] = []
    backup_root: Path | None = None
    try:
        backup_root = Path(tempfile.mkdtemp(dir=parent, prefix=".v2projection-rollback-"))
        _set_private_directory_mode(backup_root)
    except OSError:
        cleanup_required = False
        if backup_root is not None and backup_root.exists():
            try:
                shutil.rmtree(backup_root)
            except OSError:
                cleanup_required = True
        if root_created:
            try:
                root.rmdir()
            except OSError:
                cleanup_required = True
        raise ProjectionWriteError(cleanup_required=cleanup_required) from None

    try:
        backups: dict[str, Path] = {}
        for index, relative in enumerate(old_files):
            target = root.joinpath(*relative.split("/"))
            if target.exists():
                backup = backup_root / f"owned-{index}"
                os.link(target, backup)
                backups[relative] = backup
        manifest_backup: Path | None = None
        if manifest_path.exists():
            manifest_backup = backup_root / "manifest"
            os.link(manifest_path, manifest_backup)
        recovery_record = {
            "action": "restore_previous",
            "files": {backup.name: relative for relative, backup in backups.items()},
            "manifest": manifest_backup.name if manifest_backup is not None else None,
            "schema_version": 1,
        }
        recovery_file = backup_root / "recovery.json"
        recovery_bytes = json.dumps(recovery_record, sort_keys=True).encode("utf-8")
        with recovery_file.open("xb") as stream:
            if os.name != "nt":
                os.chmod(recovery_file, 0o600)
            stream.write(recovery_bytes)
            stream.flush()
            os.fsync(stream.fileno())

        try:
            for relative in sorted(encoded):
                target = _ensure_safe_parent(root, relative, create=True, created_dirs=created_dirs)
                if relative not in old_set or not target.exists():
                    created_paths.append(target)
                _atomic_write(target, encoded[relative])
            for relative in sorted(old_set - set(encoded)):
                target = root.joinpath(*relative.split("/"))
                if _is_reparse_point(target):
                    raise ProjectionWriteError
                if target.exists():
                    if not target.is_file():
                        raise ProjectionWriteError
                    target.unlink()
            _atomic_write(manifest_path, manifest_bytes)
        except (OSError, ProjectionWriteError):
            recovery_required = False
            for path in reversed(created_paths):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    recovery_required = True
            for relative, backup in backups.items():
                if backup.exists():
                    target = root.joinpath(*relative.split("/"))
                    try:
                        _restore_backup(backup, target)
                    except OSError:
                        recovery_required = True
            if manifest_backup is not None and manifest_backup.exists():
                try:
                    _restore_backup(manifest_backup, manifest_path)
                except OSError:
                    recovery_required = True
            elif manifest_path.exists():
                try:
                    manifest_path.unlink()
                except OSError:
                    recovery_required = True
            for directory in reversed(created_dirs):
                try:
                    directory.rmdir()
                except OSError:
                    recovery_required = True
            if root_created and not recovery_required:
                try:
                    root.rmdir()
                except OSError:
                    recovery_required = True
            if not recovery_required:
                cleanup_ok = _cleanup_backup_files(
                    backup_root, [*backups.values(), *([manifest_backup] if manifest_backup else [])],
                )
                backup_root = None if cleanup_ok else backup_root
                raise ProjectionWriteError(cleanup_required=not cleanup_ok) from None
            raise ProjectionWriteError(recovery_required=True) from None
    except (OSError, ProjectionWriteError) as error:
        if isinstance(error, ProjectionWriteError) \
                and (error.recovery_required or error.cleanup_required):
            raise
        cleanup_required = False
        if backup_root is not None and backup_root.exists():
            try:
                shutil.rmtree(backup_root)
            except OSError:
                cleanup_required = True
        if root_created:
            try:
                root.rmdir()
            except OSError:
                cleanup_required = True
        raise ProjectionWriteError(cleanup_required=cleanup_required) from None
    else:
        if not _cleanup_backup_files(
                backup_root, [*backups.values(), *([manifest_backup] if manifest_backup else [])]):
            raise ProjectionWriteError(cleanup_required=True) from None


def write_projection(files: Mapping[str, str], projection_dir: Path) -> None:
    """Write one rendered projection into an explicit ``V2Projection`` directory."""
    try:
        _write_projection(files, projection_dir)
    except OSError:
        raise ProjectionWriteError from None
