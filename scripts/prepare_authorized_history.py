"""Prepare explicit, authorized raw History files in an existing private inbox.

Usage (repeat --source for each selected origin):
  python scripts/prepare_authorized_history.py --source codex=C:/private/sessions
      --source chatgpt=C:/private/export.zip --inbox C:/private/migration-inbox

This is PREPARATION ONLY. It copies stable original bytes and writes schema-v1
source manifests below authorized-history/. It never reads V2 stores, imports,
normalizes, changes configuration, discovers accounts, or contacts a Gateway.
After preparation, use the existing formal cognivault ingest_history_sources
tool with relative_manifest and expected_manifest_sha256 from the JSON result.
Then pin history_normalization_snapshot, run normalize_history_sources in
bounded batches and verify_canonical_history through the configured MCP Host.
The selected inbox must already be the approved private migration inbox, outside
Git and all authoritative Study/History/Assets/Memory storage. This CLI cannot
verify Gateway configuration; Gateway independently enforces that boundary.

Implementation plan: synthetic boundary/replay/change tests first (RED), then
explicit safe traversal, verified byte copies and bounded manifests (GREEN).
JSONL is checked for complete valid records; JSON/ZIP contract validation stays
with the existing Gateway normalization adapters. No attachment resolution or
guessed timestamps/roles are performed. Interrupted/changed files are skipped.
All sources share a 25,000-entry traversal budget and an 8-GiB selected-byte
budget. Exit 0 means preparation returned a summary (inspect skipped counts);
exit 1 reports output/format conflicts; exit 2 reports an unsafe boundary or
budget failure. A budget failure may leave verified byte snapshots from earlier
files, but never publishes a partial manifest for that failed run.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


FORMATS = {"codex": {".jsonl"}, "workbuddy": {".jsonl"},
           "hermes": {".json", ".jsonl"}, "chatgpt": {".json", ".zip"}}
EXCLUDED_DIRS = {"credentials", "security", "passwords", "browser", "browsers", "cookies", ".git"}
EXCLUDED_NAMES = re.compile(r"credential|password|token|secret|(?:^|[._-])auth(?:[._-]|$)", re.I)
MAX_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 25_000
MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
MAX_LINE = 16 * 1024 * 1024


class PreparationError(ValueError):
    """Only fixed, path-free codes may cross the CLI output boundary."""


class _PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise PreparationError("invalid_arguments")


def _unsafe(path):
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            return True
    return False


def _excluded(path):
    return any(p.casefold() in EXCLUDED_DIRS for p in path.parts) or bool(EXCLUDED_NAMES.search(path.name))


def _signature(info, *, include_ctime=True):
    identity = info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
    return (*identity, info.st_ctime_ns) if include_ctime else identity


def _hash_stream(stream, output=None):
    digest = hashlib.sha256()
    size = 0
    while block := stream.read(1024 * 1024):
        size += len(block)
        if size > MAX_BYTES:
            raise PreparationError("file_limit")
        digest.update(block)
        if output is not None:
            output.write(block)
    return digest.hexdigest(), size


def _validate_jsonl(path):
    seen = False
    with path.open("rb") as stream:
        while line := stream.readline(MAX_LINE + 1):
            if len(line) > MAX_LINE:
                raise PreparationError("invalid_jsonl")
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except (ValueError, UnicodeError, RecursionError):
                raise PreparationError("invalid_jsonl") from None
            if not isinstance(value, dict):
                raise PreparationError("invalid_jsonl")
            seen = True
    if not seen:
        raise PreparationError("invalid_jsonl")


def _safe_file(path):
    info = path.lstat()
    if _unsafe(path) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PreparationError("unsafe_source")
    return info


def _publish(temp, destination, digest, size):
    if destination.exists():
        try:
            _safe_file(destination)
        except PreparationError:
            raise PreparationError("output_conflict") from None
        with destination.open("rb") as stream:
            if _hash_stream(stream) != (digest, size):
                raise PreparationError("output_conflict")
        return
    # Exclusive creation preserves any existing evidence, including a concurrent
    # writer. Partial output after a crash is a conflict, never silently replaced.
    try:
        with destination.open("xb") as out, temp.open("rb") as stream:
            copied = _hash_stream(stream, out)
            out.flush()
            os.fsync(out.fileno())
        if copied != (digest, size):
            raise PreparationError("output_conflict")
    except FileExistsError:
        _publish(temp, destination, digest, size)


def _snapshot(path, folder):
    before = _safe_file(path)
    if before.st_size > MAX_BYTES:
        raise PreparationError("file_limit")
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=folder)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as out, path.open("rb") as stream:
            handle_before = os.fstat(stream.fileno())
            # Windows Python 3.12 can report birth time via path stat and change
            # time via fstat under the same ctime name. Compare common identity
            # across APIs, then keep each API's own full time baseline below.
            if _signature(handle_before, include_ctime=False) != _signature(before, include_ctime=False):
                raise PreparationError("source_changed")
            digest, size = _hash_stream(stream, out)
            out.flush()
            os.fsync(out.fileno())
            if _signature(os.fstat(stream.fileno())) != _signature(handle_before):
                raise PreparationError("source_changed")
        if _signature(_safe_file(path)) != _signature(before):
            raise PreparationError("source_changed")
        with path.open("rb") as stream:
            if _signature(os.fstat(stream.fileno())) != _signature(handle_before) or _hash_stream(stream) != (digest, size):
                raise PreparationError("source_changed")
            if _signature(os.fstat(stream.fileno())) != _signature(handle_before):
                raise PreparationError("source_changed")
        if _signature(_safe_file(path)) != _signature(before):
            raise PreparationError("source_changed")
        if path.suffix.lower() == ".jsonl":
            _validate_jsonl(temp)
        destination = folder / (digest + path.suffix.lower())
        _publish(temp, destination, digest, size)
        return destination, digest, size
    finally:
        temp.unlink(missing_ok=True)


def _candidates(root, skipped, budget):
    if root.is_file():
        budget["entries"] += 1
        if budget["entries"] > MAX_ENTRIES:
            raise PreparationError("entry_limit")
        yield root
        return
    def fail_walk(_error):
        raise PreparationError("unreadable_source")
    for current, dirs, files in os.walk(root, followlinks=False, onerror=fail_walk):
        current = Path(current)
        budget["entries"] += len(dirs) + len(files)
        if budget["entries"] > MAX_ENTRIES:
            raise PreparationError("entry_limit")
        if len(current.relative_to(root).parts) > 32:
            raise PreparationError("depth_limit")
        keep = []
        for name in sorted(dirs):
            path = current / name
            if _excluded(path) or _unsafe(path):
                skipped["excluded_directory"] += 1
            else:
                keep.append(name)
        dirs[:] = keep
        for name in sorted(files):
            yield current / name


def _ensure_directory(path):
    path.mkdir(exist_ok=True)
    if _unsafe(path) or not path.is_dir():
        raise PreparationError("unsafe_output")


def _write_manifest(folder, entries):
    raw = json.dumps({"schema_version": 1, "entries": entries}, ensure_ascii=True,
                     sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    fd, temporary = tempfile.mkstemp(prefix=".manifest-", dir=folder)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        destination = folder / (digest + ".json")
        _publish(temp, destination, digest, len(raw))
        return {"relative_manifest": "authorized-history/manifests/" + destination.name,
                "expected_manifest_sha256": digest, "entry_count": len(entries)}
    finally:
        temp.unlink(missing_ok=True)


def prepare(sources, inbox, *, batch_size=5000):
    if type(batch_size) is not int or not 1 <= batch_size <= 5000 or not sources:
        raise PreparationError("invalid_arguments")
    inbox = Path(inbox)
    if not inbox.is_absolute() or not inbox.is_dir() or _unsafe(inbox):
        raise PreparationError("unsafe_inbox")
    inbox = inbox.resolve()
    if any((p / ".git").exists() for p in (inbox, *inbox.parents)):
        raise PreparationError("inbox_in_git")
    roots = []
    for descriptor in sources:
        system, separator, location = descriptor.partition("=")
        root = Path(location)
        if not separator or system not in FORMATS or not root.is_absolute() or not root.exists() or _unsafe(root) or _excluded(root):
            raise PreparationError("invalid_source")
        root = root.resolve()
        if root == inbox or root.is_relative_to(inbox) or inbox.is_relative_to(root):
            raise PreparationError("source_output_overlap")
        roots.append((system, root))
    base = inbox / "authorized-history"
    files = base / "files"
    manifests = base / "manifests"
    for folder in (base, files, manifests):
        _ensure_directory(folder)
    entries = {}
    ambiguous = set()
    skipped = Counter()
    budget = {"entries": 0, "bytes": 0}
    for system, root in roots:
        for path in _candidates(root, skipped, budget):
            if _excluded(path) or path.suffix.lower() not in FORMATS[system]:
                skipped["excluded_file"] += 1
                continue
            try:
                size_hint = _safe_file(path).st_size
                if budget["bytes"] + size_hint > MAX_TOTAL_BYTES:
                    raise PreparationError("total_byte_limit")
                budget["bytes"] += size_hint
                destination, digest, size = _snapshot(path, files)
            except PreparationError as error:
                if str(error) == "total_byte_limit":
                    raise
                skipped[str(error)] += 1
                continue
            except OSError:
                skipped["unreadable_source"] += 1
                continue
            key = (system, digest)
            fmt = path.suffix.lower()[1:]
            if key in ambiguous:
                skipped["ambiguous_format"] += 1
                continue
            if key in entries and entries[key]["source_format"] != fmt:
                del entries[key]
                ambiguous.add(key)
                skipped["ambiguous_format"] += 2
                continue
            entries[key] = {"source_system": system, "source_format": fmt,
                "record_kind": "conversation_export" if system == "chatgpt" else "raw_session",
                "sha256": digest, "byte_count": size,
                "relative_path": "authorized-history/files/" + destination.name}
    ordered = [entries[key] for key in sorted(entries)]
    refs = [_write_manifest(manifests, ordered[n:n + batch_size]) for n in range(0, len(ordered), batch_size)]
    fatal = any(skipped[code] for code in ("output_conflict", "unsafe_output", "ambiguous_format"))
    return {"state": "PREPARED_WITH_ERRORS" if fatal else "PREPARED_ONLY", "prepared": len(ordered),
            "skipped": dict(sorted(skipped.items())), "manifests": refs}


def main(argv=None):
    parser = _PrivateArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", action="append", required=True, help="Explicit source_system=absolute_path; repeatable")
    parser.add_argument("--inbox", required=True, help="Existing approved private migration inbox")
    parser.add_argument("--batch-size", type=int, default=5000)
    try:
        args = parser.parse_args(argv)
        result = prepare(args.source, args.inbox, batch_size=args.batch_size)
    except PreparationError as error:
        print(json.dumps({"state": "PREPARATION_FAILED", "error_code": str(error)}), file=sys.stderr)
        return 2
    except OSError:
        print('{"state":"PREPARATION_FAILED","error_code":"filesystem_unavailable"}', file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 1 if result["state"] == "PREPARED_WITH_ERRORS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
