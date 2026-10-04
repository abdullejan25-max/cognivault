"""Bounded physical byte spans for JSONL members; no record parsing or persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import BinaryIO, Iterator


_MAX_RAW_LINE_BYTES = 16 * 1024 * 1024


class JSONLSpanError(ValueError):
    """Fixed, path-free error for invalid span inputs."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"JSONL span scan failed ({code})")


@dataclass(frozen=True)
class JSONLRecordSpan:
    """One physical line span; raw bytes are omitted for over-limit lines."""

    record_ordinal: int
    byte_start: int
    byte_end: int
    raw_line: bytes | None = field(repr=False)
    oversized: bool


def iter_jsonl_record_spans(
    stream: BinaryIO,
    *,
    max_line_bytes: int = _MAX_RAW_LINE_BYTES,
) -> Iterator[JSONLRecordSpan]:
    """Yield physical LF-delimited spans using bounded reads.

    The byte span includes LF when present (and therefore both bytes of CRLF).
    Blank and malformed lines still occupy a record ordinal. A trailing LF does
    not create another record, while a final unterminated non-empty line does.
    """
    if type(max_line_bytes) is not int or not 1 <= max_line_bytes <= _MAX_RAW_LINE_BYTES:
        raise JSONLSpanError("invalid_line_limit")
    if not callable(getattr(stream, "readline", None)):
        raise JSONLSpanError("invalid_stream")

    byte_offset = 0
    record_ordinal = 0
    while True:
        raw_line = stream.readline(max_line_bytes + 1)
        if type(raw_line) is not bytes:
            raise JSONLSpanError("invalid_stream_data")
        if not raw_line:
            return

        byte_start = byte_offset
        span_length = len(raw_line)
        oversized = span_length > max_line_bytes
        if oversized and not raw_line.endswith(b"\n"):
            while True:
                tail = stream.readline(max_line_bytes + 1)
                if type(tail) is not bytes:
                    raise JSONLSpanError("invalid_stream_data")
                if not tail:
                    break
                span_length += len(tail)
                if tail.endswith(b"\n"):
                    break

        byte_offset += span_length
        yield JSONLRecordSpan(
            record_ordinal=record_ordinal,
            byte_start=byte_start,
            byte_end=byte_offset,
            raw_line=None if oversized else raw_line,
            oversized=oversized,
        )
        record_ordinal += 1
