"""Pure, bounded Codex rollout-line adapter for synthetic migration preparation."""

from __future__ import annotations

import json
import re

from .history_occurrence import HistoryOccurrenceBlock, ImportedHistoryOccurrence
from .codex_jsonl_spans import JSONLRecordSpan


_MAX_LINE_BYTES = 16 * 1024 * 1024
_MAX_CONTENT_BLOCKS = 10_000
_MAX_ID_CHARS = 4096
_MAX_TIMESTAMP_CHARS = 128
_HEX_256 = re.compile(r"[0-9a-f]{64}\Z")
_RECORD_TYPES = frozenset({"session_meta", "event_msg", "response_item", "turn_context", "compacted"})
_TEXT_BLOCK_TYPES = frozenset({"input_text", "output_text"})
_OPAQUE_BLOCK_TYPES = frozenset({"input_image", "input_audio"})


class CodexOccurrenceParseError(ValueError):
    """Fixed, path-free failure for the bounded single-line adapter."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Codex occurrence parse failed ({code})")


class _DuplicateJSONKey(ValueError):
    pass


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey
        result[key] = value
    return result


def _is_safe_string(value: object, *, maximum: int) -> bool:
    return type(value) is str and 0 < len(value) <= maximum \
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)


def _opaque_record_ref(record_ordinal: int) -> HistoryOccurrenceBlock:
    return HistoryOccurrenceBlock(kind="opaque_record", source_ref=f"record:{record_ordinal}")


def parse_codex_occurrence_line(
    raw_line: bytes,
    *,
    snapshot_sha256: str,
    source_member_ref: str,
    record_ordinal: int,
    source_byte_start: int | None = None,
    source_byte_end: int | None = None,
) -> ImportedHistoryOccurrence:
    """Map only explicit source fields; opaque data stays addressable in the raw snapshot."""
    if type(raw_line) is not bytes:
        raise CodexOccurrenceParseError("invalid_line")
    if len(raw_line) > _MAX_LINE_BYTES:
        raise CodexOccurrenceParseError("line_limit_exceeded")
    if type(snapshot_sha256) is not str or _HEX_256.fullmatch(snapshot_sha256) is None \
            or not _is_safe_string(source_member_ref, maximum=_MAX_ID_CHARS) \
            or type(record_ordinal) is not int or record_ordinal < 0 \
            or (source_byte_start is None) != (source_byte_end is None) \
            or (source_byte_start is not None and (
                type(source_byte_start) is not int or type(source_byte_end) is not int
                or source_byte_start < 0 or source_byte_end <= source_byte_start
                or source_byte_end - source_byte_start != len(raw_line)
            )):
        raise CodexOccurrenceParseError("invalid_provenance")
    try:
        record = json.loads(raw_line, object_pairs_hook=_reject_duplicate_json_keys)
    except _DuplicateJSONKey:
        raise CodexOccurrenceParseError("duplicate_json_key") from None
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
        raise CodexOccurrenceParseError("malformed_json") from None
    if not isinstance(record, dict):
        raise CodexOccurrenceParseError("unsupported_record")

    envelope = record.get("type")
    payload = record.get("payload")
    if type(envelope) is not str or envelope not in _RECORD_TYPES or not isinstance(payload, dict):
        return ImportedHistoryOccurrence(
            schema_version=1,
            source_system="codex",
            snapshot_sha256=snapshot_sha256,
            source_member_ref=source_member_ref,
            record_ordinal=record_ordinal,
            source_byte_start=source_byte_start,
            source_byte_end=source_byte_end,
            kind="unknown_record",
            role=None,
            content_blocks=(_opaque_record_ref(record_ordinal),),
            source_created_at=None,
            unknown_field_refs=(f"record:{record_ordinal}",),
        )

    payload_kind = payload.get("type")
    kind = payload_kind if _is_safe_string(payload_kind, maximum=80) else envelope
    role = None
    source_item_id = None
    content_blocks: list[HistoryOccurrenceBlock] = []
    has_opaque_values = False

    is_message = envelope == "response_item" and payload_kind == "message"
    if is_message:
        candidate_role = payload.get("role")
        role = candidate_role if _is_safe_string(candidate_role, maximum=80) else None
        candidate_id = payload.get("id")
        source_item_id = candidate_id if _is_safe_string(candidate_id, maximum=_MAX_ID_CHARS) else None
        has_opaque_values = (candidate_role is not None and role is None) \
            or (candidate_id is not None and source_item_id is None)
        if "ordinal" in record or "phase" in payload \
                or set(record) - {"type", "timestamp", "payload", "ordinal"} \
                or set(payload) - {"type", "id", "role", "content", "phase"}:
            has_opaque_values = True
        content = payload.get("content")
        if isinstance(content, list):
            if len(content) > _MAX_CONTENT_BLOCKS:
                raise CodexOccurrenceParseError("content_block_limit_exceeded")
            for index, block in enumerate(content):
                source_ref = f"record:{record_ordinal}/content:{index}"
                if not isinstance(block, dict):
                    content_blocks.append(HistoryOccurrenceBlock(kind="unknown_content", source_ref=source_ref))
                    has_opaque_values = True
                    continue
                block_type = block.get("type")
                if type(block_type) is not str:
                    content_blocks.append(HistoryOccurrenceBlock(kind="unknown_content", source_ref=source_ref))
                    has_opaque_values = True
                elif block_type in _TEXT_BLOCK_TYPES:
                    if set(block) - {"type", "text"}:
                        has_opaque_values = True
                    text_value = block.get("text")
                    if type(text_value) is str and len(text_value) <= 1024 * 1024:
                        content_blocks.append(HistoryOccurrenceBlock(kind=block_type, text=text_value))
                    else:
                        content_blocks.append(HistoryOccurrenceBlock(kind=block_type, source_ref=source_ref))
                        has_opaque_values = True
                elif block_type in _OPAQUE_BLOCK_TYPES:
                    content_blocks.append(HistoryOccurrenceBlock(kind=block_type, source_ref=source_ref))
                else:
                    content_blocks.append(HistoryOccurrenceBlock(kind="unknown_content", source_ref=source_ref))
                    has_opaque_values = True
        else:
            content_blocks.append(_opaque_record_ref(record_ordinal))
            has_opaque_values = True
    else:
        content_blocks.append(_opaque_record_ref(record_ordinal))

    source_timestamp = record.get("timestamp")
    raw_timestamp = source_timestamp if _is_safe_string(
        source_timestamp, maximum=_MAX_TIMESTAMP_CHARS,
    ) else None
    if source_timestamp is not None and raw_timestamp is None:
        has_opaque_values = True
    try:
        return ImportedHistoryOccurrence(
            schema_version=1,
            source_system="codex",
            snapshot_sha256=snapshot_sha256,
            source_member_ref=source_member_ref,
            record_ordinal=record_ordinal,
            source_byte_start=source_byte_start,
            source_byte_end=source_byte_end,
            kind=kind,
            role=role,
            content_blocks=tuple(content_blocks),
            source_item_id=source_item_id,
            source_timestamp_raw=raw_timestamp,
            unknown_field_refs=(f"record:{record_ordinal}",) if has_opaque_values else (),
        )
    except ValueError:
        raise CodexOccurrenceParseError("invalid_record") from None


def parse_codex_occurrence_span(
    span: JSONLRecordSpan,
    *,
    snapshot_sha256: str,
    source_member_ref: str,
) -> ImportedHistoryOccurrence:
    """Adapt one retained synthetic line span without guessing source order."""
    if type(span) is not JSONLRecordSpan:
        raise CodexOccurrenceParseError("invalid_record_span")
    if type(span.oversized) is not bool:
        raise CodexOccurrenceParseError("invalid_record_span")
    if span.oversized:
        raise CodexOccurrenceParseError("oversized_record")
    if type(span.raw_line) is not bytes \
            or type(span.record_ordinal) is not int or span.record_ordinal < 0 \
            or type(span.byte_start) is not int or span.byte_start < 0 \
            or type(span.byte_end) is not int or span.byte_end <= span.byte_start \
            or span.byte_end - span.byte_start != len(span.raw_line):
        raise CodexOccurrenceParseError("invalid_record_span")
    return parse_codex_occurrence_line(
        span.raw_line,
        snapshot_sha256=snapshot_sha256,
        source_member_ref=source_member_ref,
        record_ordinal=span.record_ordinal,
        source_byte_start=span.byte_start,
        source_byte_end=span.byte_end,
    )
