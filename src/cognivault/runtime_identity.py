"""Private-data-free startup identity, not authenticated build attestation."""
import hashlib
import os
from pathlib import Path
import stat
from uuid import uuid4


def _regular(metadata, directory=False):
    return (not stat.S_ISLNK(metadata.st_mode)
            and not getattr(metadata, 'st_file_attributes', 0) & 0x400
            and (stat.S_ISDIR(metadata.st_mode) if directory else
                 stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1))


def _signature(metadata):
    # Access time may change because this diagnostic itself reads source bytes.
    return (metadata.st_dev, metadata.st_ino, metadata.st_size,
            metadata.st_mtime_ns, metadata.st_ctime_ns)


def source_fingerprint(root: Path) -> str | None:
    """Hash bounded Python package sources only, never configuration or stores.

    Captured at Gateway construction; it deliberately remains unchanged after
    a disk edit. This observes startup files, not every loaded Python code object.
    Unreadable, linked or oversized packages have no fingerprint.
    """
    try:
        if not _regular(root.lstat(), directory=True):
            return None
        paths = []
        pending = [(root, 0)]
        entries, directories = 0, 1
        while pending:
            parent, depth = pending.pop()
            with os.scandir(parent) as children:
                for child in children:
                    entries += 1
                    if entries > 1024:
                        return None
                    if child.name == '__pycache__':
                        continue
                    # Windows DirEntry metadata can report st_nlink=0. Use
                    # lstat for the real link count and reparse attributes.
                    metadata = Path(child.path).lstat()
                    if stat.S_ISDIR(metadata.st_mode):
                        directories += 1
                        if (directories > 128 or depth >= 8
                                or not _regular(metadata, directory=True)):
                            return None
                        pending.append((Path(child.path), depth + 1))
                    elif child.name.endswith('.py'):
                        if not _regular(metadata):
                            return None
                        paths.append(Path(child.path))
                        if len(paths) > 256:
                            return None
                    elif stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
                        return None
        if not paths:
            return None
        digest = hashlib.sha256()
        total = 0
        for path in sorted(paths):
            before = path.lstat()
            if not _regular(before) or before.st_size > 512 * 1024:
                return None
            total += before.st_size
            if total > 8 * 1024 * 1024:
                return None
            with path.open('rb') as stream:
                opened = os.fstat(stream.fileno())
                if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                    return None
                content = stream.read(512 * 1024 + 1)
                after = os.fstat(stream.fileno())
            if (len(content) != before.st_size or _signature(opened) != _signature(after)
                    or _signature(path.lstat()) != _signature(before)):
                return None
            digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0')
            digest.update(len(content).to_bytes(8, 'big') + content)
        return digest.hexdigest()
    except (OSError, ValueError, RuntimeError):
        return None


def startup_identity() -> dict:
    return {'instance_id': uuid4().hex,
            'startup_source_sha256': source_fingerprint(Path(__file__).parent)}
