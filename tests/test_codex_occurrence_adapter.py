import base64
import json
from io import BytesIO

import pytest

import cognivault.migration.codex_occurrence_adapter as adapter_module
from cognivault.migration.codex_occurrence_adapter import (
    CodexOccurrenceParseError,
    parse_codex_occurrence_span,
    parse_codex_occurrence_line,
)
from cognivault.migration.codex_jsonl_spans import iter_jsonl_record_spans


_SNAPSHOT = "b" * 64


def _parse(record: object, *, ordinal: int = 4) -> object:
    return parse_codex_occurrence_line(
        json.dumps(record, separators=(",", ":")).encode(),
        snapshot_sha256=_SNAPSHOT,
        source_member_ref="private/member.jsonl",
        record_ordinal=ordinal,
    )


def test_response_message_preserves_open_role_text_and_opaque_image_ref() -> None:
    private_text = "synthetic private body"
    image_data = base64.b64encode(b"synthetic image bytes").decode()
    record = {"type": "response_item", "timestamp": "raw-time-value", "ordinal": 123, "payload": {
        "type": "message", "id": "source-message-id", "role": "developer",
        "phase": "commentary",
        "content": [
            {"type": "input_text", "text": private_text},
            {"type": "input_image", "image_url": f"data:image/png;base64,{image_data}"},
        ],
    }}

    occurrence = _parse(record)

    assert occurrence.kind == "message"
    assert occurrence.record_ordinal == 4
    assert occurrence.role == "developer"
    assert occurrence.source_item_id == "source-message-id"
    assert occurrence.source_created_at is None
    assert occurrence.source_timestamp_raw == "raw-time-value"
    assert occurrence.conversation_ref is None
    assert occurrence.conversation_order is None
    assert occurrence.message_order is None
    assert occurrence.unknown_field_refs == ("record:4",)
    assert occurrence.content_blocks[0].text == private_text
    assert occurrence.content_blocks[1].source_ref == "record:4/content:1"
    assert image_data not in repr(occurrence)


def test_event_and_unknown_record_shapes_remain_opaque_occurrences() -> None:
    event = _parse({"type": "event_msg", "payload": {"type": "task_started", "secret": "sentinel"}}, ordinal=0)
    unknown = _parse({"type": "future_record", "payload": {"private": "sentinel"}}, ordinal=1)

    assert event.kind == "task_started"
    assert event.role is None
    assert event.content_blocks[0].source_ref == "record:0"
    assert unknown.kind == "unknown_record"
    assert unknown.content_blocks[0].source_ref == "record:1"
    assert "sentinel" not in repr(event)
    assert "sentinel" not in repr(unknown)


def test_unknown_content_block_is_referenced_without_mapping_its_value() -> None:
    occurrence = _parse({"type": "response_item", "payload": {
        "type": "message", "id": "message-id", "role": "future-role",
        "content": [{"type": "future_block", "payload": "private sentinel"}],
    }})

    assert occurrence.role == "future-role"
    assert occurrence.content_blocks[0].kind == "unknown_content"
    assert occurrence.content_blocks[0].text is None
    assert occurrence.content_blocks[0].source_ref == "record:4/content:0"
    assert "private sentinel" not in repr(occurrence)


def test_unhashable_unknown_content_tags_remain_opaque() -> None:
    occurrence = _parse({"type": "response_item", "payload": {
        "type": "message", "role": "user", "content": [{"type": {"future": "tag"}}],
    }})

    assert occurrence.content_blocks[0].kind == "unknown_content"
    assert occurrence.content_blocks[0].source_ref == "record:4/content:0"


@pytest.mark.parametrize(
    "line",
    [
        b'{"type":"response_item","type":"event_msg","payload":{"type":"message","role":"user","content":[]}}',
        b'{"type":"response_item","payload":{"type":"message","role":"user","role":"assistant","content":[]}}',
    ],
)
def test_parser_rejects_duplicate_json_keys_at_any_object_depth(line: bytes) -> None:
    with pytest.raises(CodexOccurrenceParseError) as error:
        parse_codex_occurrence_line(
            line, snapshot_sha256=_SNAPSHOT,
            source_member_ref="private/member.jsonl", record_ordinal=0,
        )

    assert error.value.code == "duplicate_json_key"
    assert "response_item" not in str(error.value)
    assert "assistant" not in str(error.value)


def test_span_adapter_preserves_physical_ordinal_byte_span_and_source_order_separately() -> None:
    raw = b"malformed\n" + (
        b'{"type":"response_item","ordinal":123,"payload":{"type":"message",'
        b'"role":"developer","content":[{"type":"input_text","text":"body"}]}}\r\n'
    )
    span = list(iter_jsonl_record_spans(BytesIO(raw), max_line_bytes=256))[1]

    occurrence = parse_codex_occurrence_span(
        span, snapshot_sha256=_SNAPSHOT, source_member_ref="private/member.jsonl",
    )

    assert occurrence.record_ordinal == 1
    assert occurrence.source_byte_start == len(b"malformed\n")
    assert occurrence.source_byte_end == len(raw)
    assert occurrence.message_order is None
    assert occurrence.content_blocks[0].text == "body"


def test_span_adapter_rejects_oversized_line_without_exposing_its_content() -> None:
    sentinel = b"private oversized record content"
    span = next(iter_jsonl_record_spans(BytesIO(sentinel), max_line_bytes=3))

    with pytest.raises(CodexOccurrenceParseError) as error:
        parse_codex_occurrence_span(
            span, snapshot_sha256=_SNAPSHOT, source_member_ref="private/member.jsonl",
        )

    assert error.value.code == "oversized_record"
    assert "private oversized" not in str(error.value)


def test_parser_rejects_non_object_records_and_excess_content_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(CodexOccurrenceParseError) as error:
        parse_codex_occurrence_line(
            b"[]", snapshot_sha256=_SNAPSHOT,
            source_member_ref="private/member.jsonl", record_ordinal=0,
        )
    assert error.value.code == "unsupported_record"

    monkeypatch.setattr(adapter_module, "_MAX_CONTENT_BLOCKS", 1)
    line = json.dumps({"type": "response_item", "payload": {
        "type": "message", "content": [
            {"type": "input_text", "text": "one"},
            {"type": "output_text", "text": "two"},
        ],
    }}).encode()
    with pytest.raises(CodexOccurrenceParseError) as error:
        parse_codex_occurrence_line(
            line, snapshot_sha256=_SNAPSHOT,
            source_member_ref="private/member.jsonl", record_ordinal=0,
        )
    assert error.value.code == "content_block_limit_exceeded"


def test_malformed_oversized_and_invalid_provenance_fail_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(CodexOccurrenceParseError) as error:
        parse_codex_occurrence_line(
            b"not-json", snapshot_sha256=_SNAPSHOT,
            source_member_ref="C:/private/path.jsonl", record_ordinal=0,
        )
    assert error.value.code == "malformed_json"
    assert "private" not in str(error.value)

    monkeypatch.setattr(adapter_module, "_MAX_LINE_BYTES", 2)
    with pytest.raises(CodexOccurrenceParseError) as error:
        parse_codex_occurrence_line(
            b"{}\n", snapshot_sha256=_SNAPSHOT,
            source_member_ref="C:/private/path.jsonl", record_ordinal=0,
        )
    assert error.value.code == "line_limit_exceeded"
    assert "private" not in str(error.value)

    with pytest.raises(CodexOccurrenceParseError) as error:
        parse_codex_occurrence_line(
            b"{}", snapshot_sha256="invalid private digest",
            source_member_ref="private path", record_ordinal=0,
        )
    assert error.value.code == "invalid_provenance"
    assert "private" not in str(error.value)
