"""Private, byte-preserving snapshots of local Codex JSONL candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import stat

from .manifest import validate_private_journal_path


_MAX_FILES = 10_000
_MAX_ENTRIES = 20_000
_MAX_FILE_BYTES = 512 * 1024 * 1024
_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_MAX_DEPTH = 16
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass(frozen=True)
class CodexSnapshotLimits:
    max_files: int = _MAX_FILES
    max_entries: int = _MAX_ENTRIES
    max_file_bytes: int = _MAX_FILE_BYTES
    max_total_bytes: int = _MAX_TOTAL_BYTES
    max_depth: int = _MAX_DEPTH

    def __post_init__(self) -> None:
        limits = (
            (self.max_files, _MAX_FILES),
            (self.max_entries, _MAX_ENTRIES),
            (self.max_file_bytes, _MAX_FILE_BYTES),
            (self.max_total_bytes, _MAX_TOTAL_BYTES),
            (self.max_depth, _MAX_DEPTH),
        )
        if any(type(value) is not int or value < 1 or value > maximum
               for value, maximum in limits):
            raise ValueError("Invalid Codex snapshot limits")


@dataclass(frozen=True)
class CodexSnapshotResult:
    source_system: str
    snapshot_sha256: str
    file_count: int
    byte_count: int
    duplicate: bool
    stored_path: Path = field(repr=False)


class CodexSnapshotError(RuntimeError):
    """Snapshot failure with a fixed, path-free code and message."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Codex snapshot failed ({code})")


@dataclass(frozen=True)
class _Candidate:
    relative_path: str
    path: Path = field(repr=False)
    signature: tuple[int, int, int, int, int]


def default_codex_snapshot_root() -> Path:
    state_root = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    if not state_root or not Path(state_root).is_absolute():
        raise CodexSnapshotError("storage_unavailable")
    return Path(state_root) / "ChatGPTStudySystemV2" / "migration" / "raw-local-snapshots" / "codex-jsonl"


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE)


def _signature(info: os.stat_result) -> tuple[int, int, int, int, int]:
    # Match the ZIP snapshot's stable identity fields. Windows may report
    # slightly different ctime_ns values between path stat and handle fstat.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)


def _os_path(path: Path) -> str | Path:
    """Use Win32 extended paths for snapshot entries beyond MAX_PATH."""
    if os.name != "nt":
        return path
    value = str(path.absolute())
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _path_stat(path: Path) -> os.stat_result:
    return os.stat(_os_path(path), follow_symlinks=False)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":")).encode("ascii")


def _validate_private_directory(path: Path) -> Path:
    """Apply the existing file-path boundary helper to a private directory."""
    marker = path / f".codex-snapshot-boundary-{secrets.token_hex(8)}"
    return validate_private_journal_path(marker).parent


def _digest_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with open(_os_path(path), "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


class PrivateCodexJSONLSnapshotStore:
    """Copy only JSONL files from one explicit root into per-user private storage."""

    def __init__(self, root: Path | None = None, limits: CodexSnapshotLimits | None = None):
        self.limits = limits or CodexSnapshotLimits()
        candidate = Path(root) if root is not None else default_codex_snapshot_root()
        try:
            if not candidate.is_absolute():
                raise ValueError
            resolved = _validate_private_directory(candidate)
            state_roots = [
                Path(value).resolve(strict=False)
                for key in ("LOCALAPPDATA", "XDG_STATE_HOME")
                if (value := os.environ.get(key)) and Path(value).is_absolute()
            ]
            if not any(resolved.is_relative_to(state_root) for state_root in state_roots):
                raise ValueError
            resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
            resolved = _validate_private_directory(resolved)
            info = resolved.stat(follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
                raise ValueError
            if os.name != "nt":
                resolved.chmod(0o700)
                if resolved.stat().st_mode & 0o077:
                    raise ValueError
            self.root = resolved
        except (OSError, ValueError):
            raise CodexSnapshotError("unsafe_path") from None

    def snapshot_jsonl_tree(self, source_root: Path) -> CodexSnapshotResult:
        root = self._validate_source_root(Path(source_root))
        if root == self.root or root.is_relative_to(self.root) or self.root.is_relative_to(root):
            raise CodexSnapshotError("unsafe_path")

        self._recover_complete_staging()
        candidates = self._scan_source(root)
        staging = self.root / f".staging-{secrets.token_hex(16)}"
        try:
            staging.mkdir(mode=0o700)
            if os.name != "nt":
                staging.chmod(0o700)
            files_root = staging / "files"
            files_root.mkdir(mode=0o700)
            manifest_entries = []
            total_bytes = 0
            for candidate in candidates:
                size, sha256 = self._copy_and_verify(candidate, files_root)
                total_bytes += size
                manifest_entries.append({
                    "relative_path": candidate.relative_path,
                    "byte_count": size,
                    "sha256": sha256,
                })

            if self._scan_source(root) != candidates:
                raise CodexSnapshotError("source_changed")

            stable_fields = {
                "schema_version": 1,
                "source_system": "codex",
                "file_count": len(manifest_entries),
                "total_bytes": total_bytes,
                "files": manifest_entries,
            }
            snapshot_sha256 = hashlib.sha256(_canonical_json(stable_fields)).hexdigest()
            manifest = {
                **stable_fields,
                "snapshot_sha256": snapshot_sha256,
                "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            self._write_manifest(staging, manifest)
            manifest_published = True
            target = self.root / snapshot_sha256
            if target.exists():
                self._verify_snapshot(target, expected_digest=snapshot_sha256)
                shutil.rmtree(staging)
                return CodexSnapshotResult("codex", snapshot_sha256, len(candidates), total_bytes,
                                           True, target)
            try:
                os.rename(staging, target)
            except OSError:
                if not target.exists():
                    raise
                self._verify_snapshot(target, expected_digest=snapshot_sha256)
                shutil.rmtree(staging)
                return CodexSnapshotResult("codex", snapshot_sha256, len(candidates), total_bytes,
                                           True, target)
            self._verify_snapshot(target, expected_digest=snapshot_sha256)
            return CodexSnapshotResult("codex", snapshot_sha256, len(candidates), total_bytes,
                                       False, target)
        except CodexSnapshotError:
            if staging.exists() and not locals().get("manifest_published", False):
                shutil.rmtree(staging, ignore_errors=True)
            raise
        except (OSError, ValueError, UnicodeError):
            if staging.exists() and not locals().get("manifest_published", False):
                shutil.rmtree(staging, ignore_errors=True)
            raise CodexSnapshotError("storage_unavailable") from None

    def _validate_source_root(self, path: Path) -> Path:
        try:
            if not path.is_absolute():
                raise ValueError
            root = _validate_private_directory(path).resolve(strict=True)
            info = root.stat(follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
                raise ValueError
            return root
        except (OSError, ValueError):
            raise CodexSnapshotError("unsafe_path") from None

    def _scan_source(self, root: Path) -> tuple[_Candidate, ...]:
        candidates: list[_Candidate] = []
        entries_seen = 0
        total_bytes = 0
        stack: list[tuple[Path, int]] = [(root, 0)]
        try:
            while stack:
                directory, depth = stack.pop()
                with os.scandir(_os_path(directory)) as entries:
                    for entry in entries:
                        entries_seen += 1
                        if entries_seen > self.limits.max_entries:
                            raise CodexSnapshotError("too_many_entries")
                        entry_path = directory / entry.name
                        # DirEntry.stat() reports st_nlink=0 for regular files on
                        # some Windows/Python combinations; Path.stat() returns
                        # the actual count used by the hard-link safety check.
                        info = _path_stat(entry_path)
                        if _is_reparse(info):
                            raise CodexSnapshotError("unsafe_path")
                        if stat.S_ISDIR(info.st_mode):
                            if depth + 1 > self.limits.max_depth:
                                raise CodexSnapshotError("too_deep")
                            stack.append((entry_path, depth + 1))
                            continue
                        if not stat.S_ISREG(info.st_mode):
                            raise CodexSnapshotError("unsafe_path")
                        if not entry.name.lower().endswith(".jsonl"):
                            continue
                        if info.st_nlink != 1:
                            raise CodexSnapshotError("unsafe_path")
                        relative = entry_path.relative_to(root).as_posix()
                        relative_path = PurePosixPath(relative)
                        if (relative_path.is_absolute() or not relative_path.parts
                                or any(part in ("", ".", "..") for part in relative_path.parts)
                                or any(ord(char) < 32 or ord(char) == 127 for char in relative)):
                            raise CodexSnapshotError("unsafe_path")
                        if info.st_size > self.limits.max_file_bytes:
                            raise CodexSnapshotError("file_too_large")
                        total_bytes += info.st_size
                        if total_bytes > self.limits.max_total_bytes:
                            raise CodexSnapshotError("total_too_large")
                        candidates.append(_Candidate(relative, entry_path, _signature(info)))
                        if len(candidates) > self.limits.max_files:
                            raise CodexSnapshotError("too_many_files")
        except CodexSnapshotError:
            raise
        except (OSError, ValueError):
            raise CodexSnapshotError("unsafe_path") from None
        if not candidates:
            raise CodexSnapshotError("no_candidates")
        candidates.sort(key=lambda item: item.relative_path)
        folded = [item.relative_path.casefold() for item in candidates]
        if len(folded) != len(set(folded)):
            raise CodexSnapshotError("unsafe_path")
        return tuple(candidates)

    def _copy_and_verify(self, candidate: _Candidate, files_root: Path) -> tuple[int, str]:
        destination = files_root.joinpath(*PurePosixPath(candidate.relative_path).parts)
        try:
            Path(_os_path(destination.parent)).mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                os.chmod(_os_path(destination.parent), 0o700)
            with open(_os_path(candidate.path), "rb") as source:
                opened = os.fstat(source.fileno())
                if _is_reparse(opened) or _signature(opened) != candidate.signature:
                    raise CodexSnapshotError("source_changed")
                digest = hashlib.sha256()
                byte_count = 0
                with open(_os_path(destination), "xb") as output:
                    if os.name != "nt":
                        os.chmod(_os_path(destination), 0o600)
                    while chunk := source.read(1024 * 1024):
                        byte_count += len(chunk)
                        if byte_count > self.limits.max_file_bytes:
                            raise CodexSnapshotError("file_too_large")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                final = os.fstat(source.fileno())
            if byte_count != candidate.signature[2] or _signature(final) != candidate.signature:
                raise CodexSnapshotError("source_changed")
            staged_size, staged_digest = _digest_file(destination)
            source_digest = digest.hexdigest()
            if staged_size != byte_count or staged_digest != source_digest:
                raise CodexSnapshotError("integrity_mismatch")
            return byte_count, source_digest
        except CodexSnapshotError:
            raise
        except (OSError, ValueError):
            raise CodexSnapshotError("storage_unavailable") from None

    @staticmethod
    def _write_manifest(directory: Path, manifest: dict[str, object]) -> None:
        target = directory / "manifest.json"
        payload = _canonical_json(manifest)
        if len(payload) > _MAX_MANIFEST_BYTES:
            raise CodexSnapshotError("manifest_invalid")
        try:
            with target.open("xb") as stream:
                if os.name != "nt":
                    target.chmod(0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            raise CodexSnapshotError("storage_unavailable") from None

    def _read_manifest(self, directory: Path) -> dict[str, object]:
        path = directory / "manifest.json"
        try:
            validate_private_journal_path(path)
            info = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or _is_reparse(info) or info.st_size > _MAX_MANIFEST_BYTES:
                raise ValueError
            payload = path.read_bytes()
            if len(payload) > _MAX_MANIFEST_BYTES:
                raise ValueError
            manifest = json.loads(payload)
            if not isinstance(manifest, dict):
                raise ValueError
            if set(manifest) != {
                "schema_version", "source_system", "file_count", "total_bytes", "files",
                "snapshot_sha256", "captured_at",
            }:
                raise ValueError
            files = manifest["files"]
            if (type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
                    or manifest["source_system"] != "codex"
                    or type(manifest["file_count"]) is not int
                    or type(manifest["total_bytes"]) is not int
                    or not isinstance(files, list) or not files
                    or len(files) != manifest["file_count"]
                    or len(files) > self.limits.max_files
                    or type(manifest["snapshot_sha256"]) is not str
                    or len(manifest["snapshot_sha256"]) != 64
                    or any(char not in "0123456789abcdef" for char in manifest["snapshot_sha256"])
                    or not isinstance(manifest["captured_at"], str)):
                raise ValueError
            return manifest
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            raise CodexSnapshotError("manifest_invalid") from None

    def _verify_snapshot(self, directory: Path, *, expected_digest: str) -> dict[str, object]:
        try:
            _validate_private_directory(directory)
            info = directory.stat(follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
                raise ValueError
            manifest = self._read_manifest(directory)
            files = manifest["files"]
            assert isinstance(files, list)
            stable_fields = {key: manifest[key] for key in (
                "schema_version", "source_system", "file_count", "total_bytes", "files",
            )}
            digest = hashlib.sha256(_canonical_json(stable_fields)).hexdigest()
            if digest != expected_digest or manifest["snapshot_sha256"] != digest:
                raise ValueError
            total = 0
            expected_paths: set[str] = set()
            folded_paths: set[str] = set()
            for record in files:
                if (not isinstance(record, dict) or set(record) != {"relative_path", "byte_count", "sha256"}
                        or not isinstance(record["relative_path"], str)
                        or type(record["byte_count"]) is not int
                        or record["byte_count"] < 0
                        or record["byte_count"] > self.limits.max_file_bytes
                        or type(record["sha256"]) is not str
                        or len(record["sha256"]) != 64
                        or any(char not in "0123456789abcdef" for char in record["sha256"])):
                    raise ValueError
                relative = PurePosixPath(record["relative_path"])
                if (relative.is_absolute() or not relative.parts
                        or any(part in ("", ".", "..") for part in relative.parts)
                        or not record["relative_path"].lower().endswith(".jsonl")
                        or any(ord(char) < 32 or ord(char) == 127 for char in record["relative_path"])
                        or record["relative_path"] in expected_paths):
                    raise ValueError
                folded = record["relative_path"].casefold()
                if folded in folded_paths:
                    raise ValueError
                folded_paths.add(folded)
                expected_paths.add(record["relative_path"])
                candidate = directory / "files" / Path(*relative.parts)
                validate_private_journal_path(candidate)
                file_info = _path_stat(candidate)
                if (not stat.S_ISREG(file_info.st_mode) or _is_reparse(file_info)
                        or file_info.st_nlink != 1):
                    raise ValueError
                size, file_digest = _digest_file(candidate)
                if size != record["byte_count"] or file_digest != record["sha256"]:
                    raise ValueError
                total += size
            if total != manifest["total_bytes"] or total > self.limits.max_total_bytes:
                raise ValueError
            actual_paths = self._list_snapshot_jsonl_paths(directory / "files")
            if actual_paths != expected_paths:
                raise ValueError
            return manifest
        except CodexSnapshotError:
            raise
        except (OSError, ValueError):
            raise CodexSnapshotError("integrity_mismatch") from None

    def _list_snapshot_jsonl_paths(self, root: Path) -> set[str]:
        result: set[str] = set()
        entries_seen = 0
        stack = [(root, 0)]
        while stack:
            directory, depth = stack.pop()
            if depth > self.limits.max_depth:
                raise ValueError
            with os.scandir(_os_path(directory)) as entries:
                for entry in entries:
                    entries_seen += 1
                    if entries_seen > self.limits.max_entries:
                        raise ValueError
                    entry_path = directory / entry.name
                    info = _path_stat(entry_path)
                    if _is_reparse(info):
                        raise ValueError
                    if stat.S_ISDIR(info.st_mode):
                        stack.append((entry_path, depth + 1))
                    elif stat.S_ISREG(info.st_mode) and entry.name.lower().endswith(".jsonl"):
                        result.add(entry_path.relative_to(root).as_posix())
                    else:
                        raise ValueError
        return result

    def _recover_complete_staging(self) -> None:
        try:
            staging_paths = [path for path in self.root.iterdir()
                             if path.name.startswith(".staging-")]
        except OSError:
            raise CodexSnapshotError("storage_unavailable") from None
        for staging in staging_paths:
            try:
                _validate_private_directory(staging)
                info = staging.stat(follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
                    raise CodexSnapshotError("recovery_required")
                manifest_path = staging / "manifest.json"
                if not manifest_path.is_file():
                    raise CodexSnapshotError("recovery_required")
                manifest = self._read_manifest(staging)
                digest = manifest["snapshot_sha256"]
                if not isinstance(digest, str):
                    raise CodexSnapshotError("recovery_required")
                self._verify_snapshot(staging, expected_digest=digest)
                target = self.root / digest
                if target.exists():
                    self._verify_snapshot(target, expected_digest=digest)
                    shutil.rmtree(staging)
                else:
                    os.rename(staging, target)
            except CodexSnapshotError:
                raise
            except (OSError, ValueError):
                raise CodexSnapshotError("recovery_required") from None
