"""Read-only selection of the exact Codex snapshot recorded in a private registry."""

import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import Iterable

from ..adapters.documents import _path_has_reparse_point
from .codex_snapshot import (
    CodexSnapshotError, CodexSnapshotLimits, CodexSnapshotResult,
    PrivateCodexJSONLSnapshotStore, _MAX_MANIFEST_BYTES,
)
from .conversation_registry import ConversationSourceRecord, _validate_record
from .manifest import _paths_overlap, validate_private_journal_path


def _manifest_byte_digest(path: Path) -> str:
    with path.open("rb") as stream:
        raw = stream.read(_MAX_MANIFEST_BYTES + 1)
    if len(raw) > _MAX_MANIFEST_BYTES:
        raise CodexSnapshotError("manifest_invalid")
    return hashlib.sha256(raw).hexdigest()


def verify_registered_codex_snapshot(
    record: ConversationSourceRecord, *, snapshot_root: Path,
    protected_paths: Iterable[Path] = (),
) -> CodexSnapshotResult:
    """Verify snapshot and manifest hashes according to their distinct meanings.

    ``source_hash`` identifies stable manifest fields and selects one snapshot;
    ``manifest_hash`` identifies the original manifest bytes including capture
    metadata. The acquisition locator stays unchanged. This function creates no
    directories, initializes no registry, and does not rescan the live source.
    """
    try:
        _validate_record(record)
        if (record.source_system != "codex" or record.source_type != "local_history"
                or record.raw_format != "jsonl" or record.source_hash is None
                or record.manifest_hash is None or record.private_locator is None
                or not record.private_locator.startswith("path:")):
            raise CodexSnapshotError("invalid_registration")
    except (ValueError, TypeError, AttributeError):
        raise CodexSnapshotError("invalid_registration") from None

    try:
        root = Path(snapshot_root)
        acquisition = Path(record.private_locator.removeprefix("path:"))
        if (not root.is_absolute() or ".." in root.parts
                or _path_has_reparse_point(root) or not root.is_dir()
                or not acquisition.is_absolute() or ".." in acquisition.parts
                or _path_has_reparse_point(acquisition)):
            raise ValueError
        root = root.resolve(strict=True)
        acquisition = acquisition.resolve(strict=False)
        state_roots = [Path(value).resolve(strict=False)
                       for key in ("LOCALAPPDATA", "XDG_STATE_HOME")
                       if (value := os.environ.get(key)) and Path(value).is_absolute()]
        if not any(root.is_relative_to(state_root) for state_root in state_roots):
            raise ValueError
        protected = tuple(Path(path) for path in protected_paths)
        for path in (*protected, acquisition):
            if (not path.is_absolute() or _path_has_reparse_point(path)
                    or _paths_overlap(root, path.resolve(strict=False))):
                raise ValueError
        validate_private_journal_path(root / ".identity-boundary", protected_paths=protected)
        snapshot = root / record.source_hash
        if _path_has_reparse_point(snapshot) or snapshot.resolve(strict=False).parent != root:
            raise ValueError
    except (OSError, ValueError, TypeError):
        raise CodexSnapshotError("unsafe_path") from None

    # The snapshot store constructor creates its root. Reuse only its read-only
    # verifier, with the same limits, after explicitly validating an existing root.
    store = object.__new__(PrivateCodexJSONLSnapshotStore)
    store.root = root
    store.limits = CodexSnapshotLimits()
    try:
        manifest = store._read_manifest(snapshot)
        manifest_path = snapshot / "manifest.json"
        if _manifest_byte_digest(manifest_path) != record.manifest_hash:
            raise CodexSnapshotError("manifest_identity_mismatch")
        for member in manifest["files"]:
            if not isinstance(member, dict) or not isinstance(member.get("relative_path"), str):
                raise CodexSnapshotError("manifest_invalid")
            relative = member["relative_path"]
            if ("\\" in relative or ":" in relative
                    or any(part in ("", ".", "..") for part in relative.split("/"))
                    or PurePosixPath(relative).is_absolute()):
                raise CodexSnapshotError("unsafe_path")
        manifest = store._verify_snapshot(snapshot, expected_digest=record.source_hash)
        if _manifest_byte_digest(manifest_path) != record.manifest_hash:
            raise CodexSnapshotError("manifest_identity_mismatch")
        return CodexSnapshotResult(
            source_system="codex", snapshot_sha256=record.source_hash,
            file_count=manifest["file_count"], byte_count=manifest["total_bytes"],
            duplicate=True, stored_path=snapshot,
        )
    except CodexSnapshotError:
        raise
    except (OSError, ValueError, TypeError):
        raise CodexSnapshotError("integrity_mismatch") from None
