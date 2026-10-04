from io import BytesIO

from cognivault.migration.codex_jsonl_spans import iter_jsonl_record_spans


def test_jsonl_spans_count_blank_and_malformed_lines_and_preserve_exact_offsets() -> None:
    raw = b'{}\r\nbad\n\n{"x":1}'

    spans = list(iter_jsonl_record_spans(BytesIO(raw), max_line_bytes=16))

    assert [
        (span.record_ordinal, span.byte_start, span.byte_end, span.raw_line, span.oversized)
        for span in spans
    ] == [
        (0, 0, 4, b'{}\r\n', False),
        (1, 4, 8, b'bad\n', False),
        (2, 8, 9, b'\n', False),
        (3, 9, 16, b'{"x":1}', False),
    ]
    assert [raw[span.byte_start:span.byte_end] for span in spans] == [span.raw_line for span in spans]


def test_jsonl_spans_do_not_add_a_phantom_record_after_trailing_lf() -> None:
    raw = b'{"x":1}\n'

    spans = list(iter_jsonl_record_spans(BytesIO(raw), max_line_bytes=16))

    assert [(span.record_ordinal, span.byte_start, span.byte_end) for span in spans] == [(0, 0, 8)]
    assert spans[0].raw_line == raw


def test_jsonl_spans_drain_oversized_lines_without_retaining_their_bytes() -> None:
    raw = b'12345\nx\n67890'

    spans = list(iter_jsonl_record_spans(BytesIO(raw), max_line_bytes=3))

    assert [
        (span.record_ordinal, span.byte_start, span.byte_end, span.raw_line, span.oversized)
        for span in spans
    ] == [
        (0, 0, 6, None, True),
        (1, 6, 8, b'x\n', False),
        (2, 8, 13, None, True),
    ]
    assert [raw[span.byte_start:span.byte_end] for span in spans] == [b'12345\n', b'x\n', b'67890']


def test_empty_jsonl_member_has_no_record_spans() -> None:
    assert list(iter_jsonl_record_spans(BytesIO(b''), max_line_bytes=16)) == []
