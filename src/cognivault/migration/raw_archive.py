"""Private, byte-preserving storage for official conversation ZIP exports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import zipfile

from .manifest import validate_private_journal_path


_DEFAULT_LIMITS = (512 * 1024 * 1024, 50_000, 2 * 1024 * 1024 * 1024, 200)
_MAX_MANIFEST_BYTES = 4096


@dataclass(frozen=True)
class RawArchiveLimits:
    max_archive_bytes: int = _DEFAULT_LIMITS[0]
    max_members: int = _DEFAULT_LIMITS[1]
    max_uncompressed_bytes: int = _DEFAULT_LIMITS[2]
    max_member_compression_ratio: int = _DEFAULT_LIMITS[3]

    def __post_init__(self) -> None:
        limits = (
            (self.max_archive_bytes, _DEFAULT_LIMITS[0], False),
            (self.max_members, _DEFAULT_LIMITS[1], True),
            (self.max_uncompressed_bytes, _DEFAULT_LIMITS[2], True),
            (self.max_member_compression_ratio, _DEFAULT_LIMITS[3], False),
        )
        if any(type(value) is not int or value < (0 if zero_allowed else 1) or value > maximum
               for value, maximum, zero_allowed in limits):
            raise ValueError("Invalid raw archive limits")


@dataclass(frozen=True)
class ArchiveIngestResult:
    source_system: str
    sha256: str
    byte_count: int
    member_count: int
    uncompressed_byte_count: int
    duplicate: bool
    stored_path: Path = field(repr=False)


class RawArchiveError(RuntimeError):
    """An ingest failure with a fixed, path-free public code and message."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Raw archive ingest failed ({code})")


def default_raw_archive_root() -> Path:
    state_root = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    if not state_root or not Path(state_root).is_absolute():
        raise RawArchiveError("storage_unavailable")
    return Path(state_root) / "ChatGPTStudySystemV2" / "migration" / "raw-archives"


def _validate_state_root(path: Path) -> Path:
    try:
        # The journal validator's leaf is a regular file, whereas this API accepts a directory.
        # Validate a non-created leaf under it so a restarted store can reuse an existing root.
        resolved = validate_private_journal_path(path / ".root-validation").parent
        if resolved.exists() and not resolved.is_dir():
            raise ValueError
        configured = [Path(value).resolve(strict=False) for key in ("LOCALAPPDATA", "XDG_STATE_HOME")
                      if (value := os.environ.get(key)) and Path(value).is_absolute()]
        if not any(resolved.is_relative_to(root) for root in configured):
            raise ValueError
        if os.name != "nt":
            resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
            info = resolved.stat(follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError
            resolved.chmod(0o700)
            if resolved.stat().st_mode & 0o077:
                raise ValueError
        return resolved
    except (OSError, ValueError):
        raise RawArchiveError("unsafe_path") from None


def _validate_source(path: Path, root: Path) -> tuple[Path, os.stat_result]:
    try:
        if not path.is_absolute() or path.suffix.lower() != ".zip":
            raise ValueError
        validate_private_journal_path(path)
        resolved = path.resolve(strict=True)
        if resolved.is_relative_to(root) or root.is_relative_to(resolved):
            raise ValueError
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError
        return resolved, info
    except (OSError, ValueError):
        raise RawArchiveError("unsafe_path") from None


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            byte_count += len(chunk)
            digest.update(chunk)
    return byte_count, digest.hexdigest()


class PrivateRawArchiveStore:
    def __init__(self, root: Path | None = None, limits: RawArchiveLimits | None = None):
        self.root = _validate_state_root(Path(root) if root is not None else default_raw_archive_root())
        self.limits = limits or RawArchiveLimits()

    def ingest_zip(self, source_path: Path, *, source_system: str) -> ArchiveIngestResult:
        if source_system not in ("chatgpt", "gemini"):
            raise RawArchiveError("unsupported_source")
        source_path = Path(source_path)
        _, initial = _validate_source(source_path, self.root)
        if initial.st_size > self.limits.max_archive_bytes:
            raise RawArchiveError("archive_too_large")

        destination_dir = self.root / source_system
        try:
            destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                destination_dir.chmod(0o700)
            validate_private_journal_path(destination_dir / "placeholder")
            source_file = source_path.open("rb")
            opened = os.fstat(source_file.fileno())
            if (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino):
                raise RawArchiveError("source_changed")
            partial = destination_dir / f".{secrets.token_hex(16)}.partial"
            digest = hashlib.sha256()
            byte_count = 0
            try:
                with source_file, partial.open("xb") as output:
                    os.chmod(partial, 0o600) if os.name != "nt" else None
                    while chunk := source_file.read(1024 * 1024):
                        byte_count += len(chunk)
                        if byte_count > self.limits.max_archive_bytes:
                            raise RawArchiveError("archive_too_large")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                    final = os.fstat(source_file.fileno())
                if (byte_count != opened.st_size or
                        (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) !=
                        (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)):
                    raise RawArchiveError("source_changed")
                sha256 = digest.hexdigest()
                staged_byte_count, staged_sha256 = _hash_file(partial)
                if staged_byte_count != byte_count or staged_sha256 != sha256:
                    raise RawArchiveError("integrity_mismatch")
                member_count, uncompressed = self._verify_zip(partial)
                target = destination_dir / f"{sha256}.zip"
                try:
                    os.link(partial, target)
                    partial.unlink()
                    partial = None
                    duplicate = False
                except FileExistsError:
                    duplicate = True
                    try:
                        validate_private_journal_path(target)
                        target_info = target.stat(follow_symlinks=False)
                        if not stat.S_ISREG(target_info.st_mode):
                            raise RawArchiveError("integrity_mismatch")
                        existing_bytes, existing_sha256 = _hash_file(target)
                    except (OSError, ValueError):
                        raise RawArchiveError("integrity_mismatch") from None
                    if existing_bytes != byte_count or existing_sha256 != sha256:
                        raise RawArchiveError("integrity_mismatch")
                    existing_members, existing_uncompressed = self._verify_zip(target)
                    if (existing_members, existing_uncompressed) != (member_count, uncompressed):
                        raise RawArchiveError("integrity_mismatch")
                self._complete_or_validate_manifest(destination_dir, source_system, sha256,
                                                    byte_count, member_count, uncompressed)
                return ArchiveIngestResult(source_system, sha256, byte_count, member_count,
                                           uncompressed, duplicate, target)
            finally:
                if partial is not None:
                    partial.unlink(missing_ok=True)
        except RawArchiveError:
            raise
        except (OSError, ValueError):
            raise RawArchiveError("storage_unavailable") from None

    def _complete_or_validate_manifest(self, directory: Path, source_system: str,
                                       sha256: str, byte_count: int, member_count: int,
                                       uncompressed: int) -> None:
        target = directory / f"{sha256}.manifest.json"
        if not target.exists():
            try:
                self._write_manifest(directory, source_system, sha256, byte_count,
                                     member_count, uncompressed)
                return
            except FileExistsError:
                # Another ingest may have published the manifest first.
                pass
        try:
            validate_private_journal_path(target)
            with target.open("rb") as manifest_file:
                manifest_info = os.fstat(manifest_file.fileno())
                if (not stat.S_ISREG(manifest_info.st_mode)
                        or manifest_info.st_size > _MAX_MANIFEST_BYTES):
                    raise RawArchiveError("manifest_invalid")
                raw = manifest_file.read(_MAX_MANIFEST_BYTES + 1)
            if len(raw) > _MAX_MANIFEST_BYTES:
                raise RawArchiveError("manifest_invalid")
            manifest = json.loads(raw)
        except (OSError, ValueError, UnicodeError):
            raise RawArchiveError("manifest_invalid") from None
        expected = {
            "source_system": source_system,
            "sha256": sha256,
            "byte_count": byte_count,
            "member_count": member_count,
            "uncompressed_byte_count": uncompressed,
        }
        if (not isinstance(manifest, dict) or set(manifest) != set(expected) | {"ingested_at"}
                or any(type(manifest[key]) is not type(value) or manifest[key] != value
                       for key, value in expected.items())
                or not isinstance(manifest["ingested_at"], str)):
            raise RawArchiveError("manifest_invalid")
        try:
            timestamp = datetime.fromisoformat(manifest["ingested_at"].replace("Z", "+00:00"))
            if timestamp.utcoffset() != timezone.utc.utcoffset(None):
                raise ValueError
        except ValueError:
            raise RawArchiveError("manifest_invalid") from None

    def _verify_zip(self, path: Path) -> tuple[int, int]:
        try:
            with zipfile.ZipFile(path, "r") as archive:
                infos = archive.infolist()
                if len(infos) > self.limits.max_members:
                    raise RawArchiveError("too_many_members")
                uncompressed = 0
                for info in infos:
                    if info.flag_bits & 0x1:
                        raise RawArchiveError("encrypted_member")
                    uncompressed += info.file_size
                    if uncompressed > self.limits.max_uncompressed_bytes:
                        raise RawArchiveError("expansion_limit")
                    if info.file_size and (not info.compress_size or
                                           info.file_size > info.compress_size * self.limits.max_member_compression_ratio):
                        raise RawArchiveError("expansion_limit")
                if archive.testzip() is not None:
                    raise RawArchiveError("invalid_zip")
                return len(infos), uncompressed
        except RawArchiveError:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError, EOFError, NotImplementedError):
            raise RawArchiveError("invalid_zip") from None

    @staticmethod
    def _write_manifest(directory: Path, source_system: str, sha256: str, byte_count: int,
                        member_count: int, uncompressed: int) -> None:
        target = directory / f"{sha256}.manifest.json"
        payload = json.dumps({
            "source_system": source_system,
            "sha256": sha256,
            "byte_count": byte_count,
            "member_count": member_count,
            "uncompressed_byte_count": uncompressed,
            "ingested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        temp = directory / f".{secrets.token_hex(16)}.manifest.partial"
        try:
            with temp.open("xb") as output:
                if os.name != "nt":
                    os.chmod(temp, 0o600)
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.link(temp, target)
        finally:
            temp.unlink(missing_ok=True)
