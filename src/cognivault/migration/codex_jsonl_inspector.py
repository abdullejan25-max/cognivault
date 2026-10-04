"""Bounded structural inspection of an already verified private Codex snapshot."""

from __future__ import annotations

import base64
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .codex_snapshot import (
    CodexSnapshotError,
    PrivateCodexJSONLSnapshotStore,
    _canonical_json,
    _os_path,
)


_MAX_LINE_BYTES = 16 * 1024 * 1024
_MAX_RECORDS = 1_000_000
_MAX_ID_CHARS = 256
_MAX_TRACKED_MESSAGE_IDS = 250_000
_MAX_INLINE_IMAGE_DECODED_BYTES = 512 * 1024 * 1024
_MAX_UNIQUE_INLINE_IMAGE_BYTES = 256 * 1024 * 1024
_MAX_UNIQUE_INLINE_IMAGES = 100_000
_HEX_256 = re.compile(r"[0-9a-f]{64}\Z")
_RECORD_TYPES = frozenset({"session_meta", "event_msg", "response_item", "turn_context", "compacted"})
_RESPONSE_TYPES = frozenset({
    "message", "function_call", "function_call_output", "reasoning", "custom_tool_call",
    "web_search_call",
})
_EVENT_TYPES = frozenset({"task_started", "task_complete", "user_message", "agent_message"})
_ROLES = frozenset({"user", "assistant", "developer", "system", "tool"})
_CONTENT_TYPES = frozenset({"input_text", "output_text", "input_image"})
_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
_FIELD_KEYS = frozenset({
    "id", "session_id", "thread_id", "parent_thread_id", "forked_from_id", "ordinal",
    "branch", "title", "author", "model", "model_provider", "timestamp", "created_at",
    "updated_at", "content", "attachment", "attachments", "images", "local_images", "files",
    "source_order", "conversation_order", "message_order", "role", "type", "text", "image_url",
    "file_id", "audio_url", "meta", "git", "phase",
})


@dataclass(frozen=True)
class CodexJSONLInspection:
    file_count: int
    byte_count: int
    line_count: int
    valid_json_record_count: int
    malformed_json_line_count: int
    duplicate_json_key_line_count: int
    non_object_record_count: int
    oversized_line_count: int
    record_type_counts: tuple[tuple[str, int], ...]
    field_presence_counts: tuple[tuple[str, str, str, int], ...]
    response_type_counts: tuple[tuple[str, int], ...]
    role_counts: tuple[tuple[str, int], ...]
    content_block_counts: tuple[tuple[str, int], ...]
    session_metadata_record_count: int
    session_metadata_id_count: int
    unique_session_metadata_id_count: int
    session_id_pair_count: int
    session_id_pair_match_count: int
    session_id_pair_nonmatching_count: int
    session_ids_repeated_across_files: int
    files_with_multiple_session_ids: int
    message_record_count: int
    message_id_count: int
    duplicate_message_id_groups: int
    duplicate_message_id_occurrences: int
    duplicate_groups_with_role_conflicts: int
    duplicate_groups_with_content_conflicts: int
    duplicate_groups_with_timestamp_conflicts: int
    duplicate_groups_crossing_files: int
    parseable_message_timestamp_count: int
    utc_message_timestamp_count: int
    parseable_record_ordinal_count: int
    adjacent_equal_record_ordinal_count: int
    record_ordinal_regression_count: int
    inline_image_payload_count: int
    valid_inline_image_payload_count: int
    invalid_inline_image_payload_count: int
    inline_image_decoded_bytes: int
    unique_inline_image_count: int
    unique_inline_image_bytes: int
    image_mime_counts: tuple[tuple[str, int], ...]


class CodexJSONLInspectionError(RuntimeError):
    """Inspection failure with a fixed, path-free code and message."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Codex JSONL inspection failed ({code})")


class _DuplicateJSONKey(ValueError):
    pass


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey
        result[key] = value
    return result


def _category(value: object, allowed: frozenset[str]) -> str:
    return value if type(value) is str and value in allowed else "other"


def _timestamp_state(value: object) -> tuple[bool, bool]:
    if type(value) is not str:
        return False, False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False, False
    if parsed.tzinfo is None:
        return True, False
    return True, parsed.utcoffset().total_seconds() == 0


def _drain_line(stream) -> None:
    while True:
        tail = stream.readline(_MAX_LINE_BYTES + 1)
        if not tail or tail.endswith(b"\n"):
            return


def _read_json_line(raw: bytes) -> tuple[object | None, bool, bool]:
    try:
        return json.loads(raw, object_pairs_hook=_reject_duplicate_json_keys), True, False
    except _DuplicateJSONKey:
        return None, False, True
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
        return None, False, False


def inspect_codex_snapshot(
    store: PrivateCodexJSONLSnapshotStore,
    snapshot_path: Path,
    *,
    expected_digest: str,
) -> CodexJSONLInspection:
    """Verify then count source structures without returning source values."""
    if type(expected_digest) is not str or _HEX_256.fullmatch(expected_digest) is None:
        raise CodexJSONLInspectionError("snapshot_invalid")
    try:
        manifest = store._verify_snapshot(Path(snapshot_path), expected_digest=expected_digest)
    except CodexSnapshotError:
        raise CodexJSONLInspectionError("snapshot_invalid") from None

    record_types: Counter[str] = Counter()
    field_presence: Counter[tuple[str, str, str]] = Counter()
    response_types: Counter[str] = Counter()
    roles: Counter[str] = Counter()
    content_types: Counter[str] = Counter()
    image_mimes: Counter[str] = Counter()
    session_id_files: dict[str, set[int]] = {}
    message_ids: dict[str, dict[str, object]] = {}
    image_hashes: dict[bytes, int] = {}
    line_count = valid_json = malformed = duplicate_json_key_lines = non_objects = oversized = 0
    session_meta_records = session_meta_ids = message_records = 0
    session_id_pairs = matching_session_id_pairs = nonmatching_session_id_pairs = 0
    files_with_multiple_session_ids = 0
    parseable_timestamps = utc_timestamps = 0
    parseable_ordinals = adjacent_equal_ordinals = ordinal_regressions = 0
    inline_images = valid_images = invalid_images = image_bytes = unique_image_bytes = 0
    file_index = -1

    try:
        files = manifest["files"]
        assert isinstance(files, list)
        for entry in files:
            file_index += 1
            relative = PurePosixPath(entry["relative_path"])
            path = Path(snapshot_path) / "files" / Path(*relative.parts)
            local_session_ids: set[str] = set()
            previous_record_ordinal: int | None = None
            with open(_os_path(path), "rb") as stream:
                while True:
                    raw = stream.readline(_MAX_LINE_BYTES + 1)
                    if not raw:
                        break
                    line_count += 1
                    if line_count > _MAX_RECORDS:
                        raise CodexJSONLInspectionError("record_limit_exceeded")
                    if len(raw) > _MAX_LINE_BYTES:
                        oversized += 1
                        previous_record_ordinal = None
                        if not raw.endswith(b"\n"):
                            _drain_line(stream)
                        continue
                    record, valid, duplicate_json_keys = _read_json_line(raw)
                    duplicate_json_key_lines += int(duplicate_json_keys)
                    if not valid:
                        malformed += int(not duplicate_json_keys)
                        previous_record_ordinal = None
                        continue
                    valid_json += 1
                    if not isinstance(record, dict):
                        non_objects += 1
                        previous_record_ordinal = None
                        continue

                    ordinal = record.get("ordinal")
                    if type(ordinal) is int and ordinal >= 0:
                        parseable_ordinals += 1
                        if previous_record_ordinal is not None:
                            adjacent_equal_ordinals += int(ordinal == previous_record_ordinal)
                            ordinal_regressions += int(ordinal < previous_record_ordinal)
                        previous_record_ordinal = ordinal
                    else:
                        previous_record_ordinal = None

                    envelope = _category(record.get("type"), _RECORD_TYPES)
                    record_types[envelope] += 1
                    for key in record.keys() & _FIELD_KEYS:
                        field_presence[("record", envelope, key)] += 1
                    payload = record.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    for key in payload.keys() & _FIELD_KEYS:
                        field_presence[("payload", envelope, key)] += 1
                    nested_meta = payload.get("meta")
                    if isinstance(nested_meta, dict):
                        for key in nested_meta.keys() & _FIELD_KEYS:
                            field_presence[("payload.meta", envelope, key)] += 1
                    nested_content = payload.get("content")
                    if isinstance(nested_content, list):
                        for block in nested_content:
                            if isinstance(block, dict):
                                for key in block.keys() & _FIELD_KEYS:
                                    field_presence[("content_block", envelope, key)] += 1
                    if envelope == "session_meta":
                        session_meta_records += 1
                        meta = payload.get("meta")
                        session_id = payload.get("session_id")
                        if type(session_id) is not str and isinstance(meta, dict):
                            session_id = meta.get("session_id")
                        identifier = payload.get("id")
                        if type(identifier) is not str and isinstance(meta, dict):
                            identifier = meta.get("id")
                        if type(session_id) is str and 0 < len(session_id) <= _MAX_ID_CHARS \
                                and type(identifier) is str and 0 < len(identifier) <= _MAX_ID_CHARS:
                            session_id_pairs += 1
                            matching_session_id_pairs += int(session_id == identifier)
                            nonmatching_session_id_pairs += int(session_id != identifier)
                        nested_identifier = meta.get("id") if isinstance(meta, dict) else None
                        valid_identifier = type(identifier) is str and 0 < len(identifier) <= _MAX_ID_CHARS
                        valid_nested_identifier = (
                            type(nested_identifier) is str
                            and 0 < len(nested_identifier) <= _MAX_ID_CHARS
                        )
                        if valid_identifier and valid_nested_identifier and identifier != nested_identifier:
                            raise CodexJSONLInspectionError("session_metadata_id_conflict")
                        if not valid_identifier:
                            identifier = nested_identifier
                        if type(identifier) is str and 0 < len(identifier) <= _MAX_ID_CHARS:
                            session_meta_ids += 1
                            local_session_ids.add(identifier)
                            session_id_files.setdefault(identifier, set()).add(file_index)
                    if envelope == "event_msg":
                        record_types[f"event:{_category(payload.get('type'), _EVENT_TYPES)}"] += 1
                    if envelope != "response_item":
                        continue

                    response_kind = _category(payload.get("type"), _RESPONSE_TYPES)
                    response_types[response_kind] += 1
                    if response_kind != "message":
                        continue

                    message_records += 1
                    role = _category(payload.get("role"), _ROLES)
                    roles[role] += 1
                    content = payload.get("content")
                    if not isinstance(content, list):
                        content_types["non_array"] += 1
                    else:
                        for block in content:
                            if not isinstance(block, dict):
                                content_types["other"] += 1
                                continue
                            block_type = _category(block.get("type"), _CONTENT_TYPES)
                            content_types[block_type] += 1
                            if block_type != "input_image":
                                continue
                            inline_images += 1
                            value = block.get("image_url")
                            if type(value) is not str or len(value) > _MAX_LINE_BYTES \
                                    or not value.startswith("data:") or "," not in value:
                                invalid_images += 1
                                continue
                            header, encoded = value.split(",", 1)
                            header_parts = header[5:].split(";")
                            media_type = header_parts[0].lower()
                            image_mimes[media_type if media_type in _IMAGE_MIME_TYPES else "other"] += 1
                            if "base64" not in header_parts[1:]:
                                invalid_images += 1
                                continue
                            try:
                                decoded = base64.b64decode(encoded, validate=True)
                            except (ValueError, base64.binascii.Error):
                                invalid_images += 1
                                continue
                            if not decoded:
                                invalid_images += 1
                                continue
                            valid_images += 1
                            image_bytes += len(decoded)
                            digest = hashlib.sha256(decoded).digest()
                            if image_bytes > _MAX_INLINE_IMAGE_DECODED_BYTES:
                                raise CodexJSONLInspectionError("image_byte_limit_exceeded")
                            if digest not in image_hashes:
                                if len(image_hashes) >= _MAX_UNIQUE_INLINE_IMAGES:
                                    raise CodexJSONLInspectionError("unique_image_limit_exceeded")
                                next_unique_image_bytes = unique_image_bytes + len(decoded)
                                if next_unique_image_bytes > _MAX_UNIQUE_INLINE_IMAGE_BYTES:
                                    raise CodexJSONLInspectionError("unique_image_byte_limit_exceeded")
                                image_hashes[digest] = len(decoded)
                                unique_image_bytes = next_unique_image_bytes

                    identifier = payload.get("id")
                    if type(identifier) is not str or not 0 < len(identifier) <= _MAX_ID_CHARS:
                        continue
                    if identifier not in message_ids and len(message_ids) >= _MAX_TRACKED_MESSAGE_IDS:
                        raise CodexJSONLInspectionError("message_id_limit_exceeded")
                    timestamp = record.get("timestamp")
                    content_signature = hashlib.sha256(_canonical_json(content)).digest()
                    timestamp_signature = hashlib.sha256(_canonical_json(timestamp)).digest()
                    summary = message_ids.get(identifier)
                    if summary is None:
                        message_ids[identifier] = {
                            "roles": {role}, "timestamps": {timestamp_signature},
                            "content_signatures": {content_signature},
                            "files": {file_index}, "occurrences": 1,
                        }
                    else:
                        summary["roles"].add(role)
                        summary["timestamps"].add(timestamp_signature)
                        summary["content_signatures"].add(content_signature)
                        summary["files"].add(file_index)
                        summary["occurrences"] += 1
                    parsed, utc = _timestamp_state(timestamp)
                    parseable_timestamps += int(parsed)
                    utc_timestamps += int(utc)
            files_with_multiple_session_ids += int(len(local_session_ids) > 1)
            # Keep the per-file set local; only counts escape below.
            del local_session_ids
    except CodexJSONLInspectionError:
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        raise CodexJSONLInspectionError("snapshot_read_failed") from None

    repeated_session_ids = sum(len(files) > 1 for files in session_id_files.values())
    duplicate_groups = [row for row in message_ids.values() if row["occurrences"] > 1]
    duplicate_occurrences = sum(row["occurrences"] - 1 for row in duplicate_groups)
    return CodexJSONLInspection(
        file_count=manifest["file_count"],
        byte_count=manifest["total_bytes"],
        line_count=line_count,
        valid_json_record_count=valid_json,
        malformed_json_line_count=malformed,
        duplicate_json_key_line_count=duplicate_json_key_lines,
        non_object_record_count=non_objects,
        oversized_line_count=oversized,
        record_type_counts=tuple(sorted(record_types.items())),
        field_presence_counts=tuple(sorted(
            (scope, envelope, key, count)
            for (scope, envelope, key), count in field_presence.items()
        )),
        response_type_counts=tuple(sorted(response_types.items())),
        role_counts=tuple(sorted(roles.items())),
        content_block_counts=tuple(sorted(content_types.items())),
        session_metadata_record_count=session_meta_records,
        session_metadata_id_count=session_meta_ids,
        unique_session_metadata_id_count=len(session_id_files),
        session_id_pair_count=session_id_pairs,
        session_id_pair_match_count=matching_session_id_pairs,
        session_id_pair_nonmatching_count=nonmatching_session_id_pairs,
        session_ids_repeated_across_files=repeated_session_ids,
        files_with_multiple_session_ids=files_with_multiple_session_ids,
        message_record_count=message_records,
        message_id_count=len(message_ids),
        duplicate_message_id_groups=len(duplicate_groups),
        duplicate_message_id_occurrences=duplicate_occurrences,
        duplicate_groups_with_role_conflicts=sum(len(row["roles"]) > 1 for row in duplicate_groups),
        duplicate_groups_with_content_conflicts=sum(
            len(row["content_signatures"]) > 1 for row in duplicate_groups
        ),
        duplicate_groups_with_timestamp_conflicts=sum(len(row["timestamps"]) > 1 for row in duplicate_groups),
        duplicate_groups_crossing_files=sum(len(row["files"]) > 1 for row in duplicate_groups),
        parseable_message_timestamp_count=parseable_timestamps,
        utc_message_timestamp_count=utc_timestamps,
        parseable_record_ordinal_count=parseable_ordinals,
        adjacent_equal_record_ordinal_count=adjacent_equal_ordinals,
        record_ordinal_regression_count=ordinal_regressions,
        inline_image_payload_count=inline_images,
        valid_inline_image_payload_count=valid_images,
        invalid_inline_image_payload_count=invalid_images,
        inline_image_decoded_bytes=image_bytes,
        unique_inline_image_count=len(image_hashes),
        unique_inline_image_bytes=unique_image_bytes,
        image_mime_counts=tuple(sorted(image_mimes.items())),
    )
