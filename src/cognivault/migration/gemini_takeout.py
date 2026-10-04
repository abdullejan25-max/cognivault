"""Offline source-only planning; execution uses existing native Gateway tools.

Returned plans/payloads are private. No MCP client, target DB, canonical message,
attachment execution, or timestamp/role inference belongs in this module.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import tempfile
import zipfile

from ..adapters.documents import DocumentInput, _normalize_pages
from ..contracts import GatewayError
from .manifest import validate_private_journal_path
from .raw_archive import PrivateRawArchiveStore, RawArchiveError

VERSION = "p13-gemini-source-1"
MARKER = "P13_GEMINI_SOURCE_ONLY_V1"
BINARY_RANGE = 512 * 1024
HTML_RANGE = 64 * 1024
MAX_PRIMARY_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 90_000


class GeminiSourceError(ValueError):
    """Fixed public error classes; do not include source content or locations."""


class _ActivityCounter(HTMLParser):
    def __init__(self):
        super().__init__()
        self.count = 0

    def handle_starttag(self, tag, attrs):
        if tag == "div" and "outer-cell" in dict(attrs).get("class", "").split():
            self.count += 1


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _refs(raw):
    digest = _digest(raw)
    return "asset://sha256/" + digest, "document://sha256/" + _digest(("text/plain\0" + digest).encode())


def _save(path, value, *, protected_paths=()):
    validate_private_journal_path(path, protected_paths=protected_paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        validate_private_journal_path(path, protected_paths=protected_paths)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _utf8_ranges(raw):
    raw.decode("utf-8")  # Reject invalid source encoding; no replacement decoding.
    start = 0
    while start < len(raw):
        end = min(start + HTML_RANGE, len(raw))
        while end < len(raw) and raw[end] & 0xC0 == 0x80:
            end -= 1
        piece = raw[start:end]
        # Existing text Document normalization rejects NUL/empty pages. Validate before writes.
        if "\x00" in piece.decode() or not piece.decode().strip():
            raise GeminiSourceError("unindexable_html_range")
        yield start, piece
        start = end


def _safe_member(name):
    posix, windows = PurePosixPath(name), PureWindowsPath(name)
    if not name or posix.is_absolute() or windows.is_absolute() or windows.drive \
            or "\\" in name or ".." in posix.parts or any(ord(c) < 32 for c in name):
        raise GeminiSourceError("unsafe_member")


def prepare_gemini_export(source_path: Path, output_root: Path, *, protected_paths=(), primary_member=None) -> dict:
    """Preserve an explicit export and publish a private deterministic native-write plan."""
    source_path, output_root = Path(source_path), Path(output_root)
    try:
        resolved_output = output_root.resolve()
        protected_paths = tuple(Path(p).resolve() for p in protected_paths)
        for protected in protected_paths:
            if resolved_output.is_relative_to(protected) or protected.is_relative_to(resolved_output):
                raise GeminiSourceError("protected_output")
        if source_path.resolve().is_relative_to(resolved_output):
            raise GeminiSourceError("source_output_overlap")
        if primary_member is not None:
            if not isinstance(primary_member, str):
                raise GeminiSourceError("invalid_primary_descriptor")
            _safe_member(primary_member)
        validate_private_journal_path(output_root / "plan.json", protected_paths=tuple(protected_paths))
        if any((p / ".git").exists() for p in (output_root, *output_root.parents)):
            raise GeminiSourceError("unsafe_output")
        for protected in protected_paths:
            if source_path.resolve().is_relative_to(Path(protected).resolve()):
                raise GeminiSourceError("protected_input")
        archived = PrivateRawArchiveStore(output_root / "raw-archives").ingest_zip(source_path, source_system="gemini")
        members, primary = [], []
        with zipfile.ZipFile(archived.stored_path) as z:
            for index, info in enumerate(z.infolist()):
                _safe_member(info.filename)
                if info.is_dir():
                    members.append({"index": index, "name": info.filename, "directory": True, "bytes": 0})
                    continue
                h = hashlib.sha256()
                with z.open(info) as stream:
                    while block := stream.read(65536):
                        h.update(block)
                row = {"index": index, "name": info.filename, "directory": False,
                       "bytes": info.file_size, "sha256": h.hexdigest(), "crc32": info.CRC}
                members.append(row)
                member_path = PurePosixPath(info.filename)
                parts = {p.casefold() for p in member_path.parts}
                activity_scope = bool(parts & {"my activity", "myactivity", "我的活动", "我的活動"})
                # Uploaded HTML is opaque, even when it contains Takeout-looking tags.
                # Localized/renamed primary members require an explicit audited descriptor.
                official_primary = (member_path.name.casefold() in {"myactivity.html", "my activity.html"}
                                    and "gemini" in member_path.parent.name.casefold()
                                    and member_path.parent.parent.name.casefold() in
                                    {"my activity", "myactivity", "我的活动", "我的活動"})
                selected = info.filename == primary_member if primary_member is not None else official_primary
                if selected and info.filename.lower().endswith(".html") and activity_scope and any("gemini" in p for p in parts):
                    if info.file_size > MAX_PRIMARY_BYTES:
                        raise GeminiSourceError("primary_limit")
                    raw = z.read(info)
                    parser = _ActivityCounter()
                    parser.feed(raw.decode("utf-8")); parser.close()
                    if parser.count:
                        primary.append((row, raw, parser.count))
        if not primary:
            raise GeminiSourceError("activity_missing")
        if len(primary) != 1:
            raise GeminiSourceError("activity_ambiguous")
        primary_row, html, activity_count = primary[0]
        html_ranges = list(_utf8_ranges(html))
        writes, archive_parts, html_parts = [], [], []

        def schedule(raw, kind, offset, index):
            asset_uri, document_uri = _refs(raw)
            title = f"Gemini source-only {kind}"
            if kind != "archive_range":
                try:
                    _normalize_pages(DocumentInput(title, "text/plain", raw))
                except GatewayError:
                    raise GeminiSourceError("unindexable_html_range") from None
            arguments = ({"media_type": "application/octet-stream", "content_base64": base64.b64encode(raw).decode()}
                         if kind == "archive_range" else {"documents": [{"title": title, "media_type": "text/plain",
                         "content_base64": base64.b64encode(raw).decode()}]})
            tool = "register_asset" if kind == "archive_range" else "ingest_documents"
            payload_path = output_root / "payloads" / archived.sha256 / f"{kind}-{index:05d}.json"
            payload = {"kind": kind, "tool": tool, "arguments": arguments}
            _save(payload_path, payload, protected_paths=protected_paths)
            descriptor = {"kind": kind, "offset": offset, "bytes": len(raw), "sha256": _digest(raw),
                          "asset_uri": asset_uri}
            if tool == "ingest_documents": descriptor["document_uri"] = document_uri
            writes.append({**descriptor, "payload_path": str(payload_path), "tool": tool})
            return descriptor

        with archived.stored_path.open("rb") as stream:
            offset = 0
            while raw := stream.read(BINARY_RANGE):
                archive_parts.append(schedule(raw, "archive_range", offset, len(archive_parts)))
                offset += len(raw)
        for offset, raw in html_ranges:
            html_parts.append(schedule(raw, "primary_html", offset, len(html_parts)))
        duplicate_copies = sum(c - 1 for c in Counter(m["sha256"] for m in members if not m["directory"]).values())
        manifest = {"schema_version": 1, "marker": MARKER, "importer_version": VERSION,
                    "source_system": "gemini", "record_kind": "source_only_archive",
                    "original_format": "google_takeout_my_activity_zip", "normalization_state": "not_normalized",
                    "archive_sha256": archived.sha256, "archive_bytes": archived.byte_count,
                    "archive_parts": archive_parts, "members": members,
                    "primary_member_index": primary_row["index"], "primary_sha256": primary_row["sha256"],
                    "primary_html_parts": html_parts, "activity_count": activity_count,
                    "conversation_count": None, "message_count": None,
                    "attachment_policy": "opaque original members preserved in complete archive",
                    "timestamp_policy": "original source fields only; import time is not occurrence time",
                    "semantic_dedup": "no automatic merge", "exact_duplicate_member_copies": duplicate_copies}
        raw_manifest = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        if len(raw_manifest) > MAX_MANIFEST_BYTES:
            raise GeminiSourceError("manifest_limit")
        root = schedule(raw_manifest, "root_manifest", 0, 0)
        plan = {"schema_version": 1, "importer_version": VERSION,
                "source_fingerprint": _digest(("gemini\0" + archived.sha256).encode()),
                "archive_sha256": archived.sha256, "archive_bytes": archived.byte_count,
                "raw_archive_path": str(archived.stored_path), "original_source_path": str(source_path),
                "raw_archive_reused": archived.duplicate, "writes": writes,
                "root_document_uri": root["document_uri"], "root_asset_uri": root["asset_uri"],
                "primary_sha256": primary_row["sha256"], "primary_bytes": len(html),
                "member_count": len(members), "file_members": sum(not m["directory"] for m in members),
                "opaque_file_members": sum(not m["directory"] for m in members) - 1,
                "attachment_count": None,
                "activity_count": activity_count, "conversation_count": None, "message_count": None,
                "canonical_messages_written": 0, "exact_duplicate_member_copies": duplicate_copies}
        _save(output_root / "plans" / (archived.sha256 + ".json"), plan, protected_paths=protected_paths)
        _save(output_root / "plan.json", plan, protected_paths=protected_paths)
        return plan
    except GeminiSourceError:
        raise
    except RawArchiveError as error:
        raise GeminiSourceError(error.code) from None
    except (OSError, ValueError, UnicodeError, zipfile.BadZipFile):
        raise GeminiSourceError("source_unavailable") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare a private source-only Gemini native Gateway write plan")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--primary-member", help="Exact member name from a private format audit")
    args = parser.parse_args(argv)
    try:
        plan = prepare_gemini_export(args.source, args.output_root, primary_member=args.primary_member)
        print(json.dumps({"state": "PLAN_ONLY", "activity_count": plan["activity_count"],
                          "member_count": plan["member_count"], "canonical_messages_written": 0}))
        return 0
    except GeminiSourceError as error:
        print(json.dumps({"state": "FAILED", "error_code": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
