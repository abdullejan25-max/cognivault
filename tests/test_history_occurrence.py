from dataclasses import FrozenInstanceError
import re

import pytest

from cognivault.migration.history_occurrence import (
    HistoryOccurrenceBlock,
    ImportedHistoryOccurrence,
)


def _occurrence(**overrides: object) -> ImportedHistoryOccurrence:
    values: dict[str, object] = {
        "schema_version": 1,
        "source_system": "codex",
        "snapshot_sha256": "a" * 64,
        "source_member_ref": "private/member.jsonl",
        "record_ordinal": 14,
        "kind": "message",
        "role": "developer",
        "content_blocks": (
            HistoryOccurrenceBlock(kind="input_text", text="private body sentinel"),
            HistoryOccurrenceBlock(kind="input_image", source_ref="private/image-ref"),
        ),
        "source_item_id": "private-message-id",
        "conversation_ref": "private-conversation-id",
        "conversation_title": "private title sentinel",
        "author": "private author sentinel",
        "model_name": "private model sentinel",
        "source_timestamp_raw": "private unclassified timestamp",
        "source_created_at": "raw-time-spelling",
        "source_updated_at": "raw-updated-time",
        "imported_at": "imported-time-observation",
        "import_batch_id": "private-batch-id",
        "conversation_order": 2,
        "message_order": 8,
        "branch_refs": ("private-branch-ref",),
        "unknown_field_refs": ("private-extension/path",),
        "conflict_codes": ("timestamp_conflict",),
    }
    values.update(overrides)
    return ImportedHistoryOccurrence(**values)  # type: ignore[arg-type]


def test_occurrence_preserves_open_values_boundaries_and_distinct_order() -> None:
    occurrence = _occurrence(source_byte_start=125, source_byte_end=190)

    assert occurrence.schema_version == 1
    assert occurrence.kind == "message"
    assert occurrence.role == "developer"
    assert tuple(block.kind for block in occurrence.content_blocks) == ("input_text", "input_image")
    assert occurrence.content_blocks[0].text == "private body sentinel"
    assert occurrence.content_blocks[1].source_ref == "private/image-ref"
    assert occurrence.record_ordinal == 14
    assert occurrence.source_byte_start == 125
    assert occurrence.source_byte_end == 190
    assert occurrence.conversation_order == 2
    assert occurrence.message_order == 8
    assert occurrence.source_created_at == "raw-time-spelling"
    assert occurrence.source_updated_at == "raw-updated-time"
    assert occurrence.source_timestamp_raw == "private unclassified timestamp"
    assert occurrence.imported_at == "imported-time-observation"
    assert occurrence.conflict_codes == ("timestamp_conflict",)


def test_occurrence_and_content_block_are_immutable() -> None:
    occurrence = _occurrence()

    with pytest.raises(FrozenInstanceError):
        occurrence.role = "assistant"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        occurrence.content_blocks[0].text = "changed"  # type: ignore[misc]


def test_occurrence_repr_hides_private_values_and_content() -> None:
    rendered = repr(_occurrence()) + repr(_occurrence().content_blocks)
    sentinels = (
        "private/member.jsonl", "private body sentinel", "private/image-ref",
        "private-message-id", "private-conversation-id", "private title sentinel",
        "private author sentinel", "private model sentinel", "private unclassified timestamp",
        "private-branch-ref",
        "private-extension/path", "125", "190",
    )

    assert all(sentinel not in rendered for sentinel in sentinels)
    assert re.search(r"[0-9a-f]{64}", rendered) is None


@pytest.mark.parametrize("overrides", [
    {"schema_version": 2},
    {"snapshot_sha256": "private bad hash"},
    {"record_ordinal": True},
    {"source_byte_start": 3},
    {"source_byte_start": True, "source_byte_end": 10},
    {"source_byte_start": -1, "source_byte_end": 10},
    {"source_byte_start": 10, "source_byte_end": 10},
    {"conversation_order": -1},
    {"source_member_ref": "private/path\nsecret"},
    {"content_blocks": [HistoryOccurrenceBlock(kind="text", text="not tuple")]},
    {"conflict_codes": ("private arbitrary value",)},
    {"content_blocks": ({"kind": "unknown"},)},
])
def test_invalid_occurrences_fail_with_fixed_redacted_message(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError) as error:
        _occurrence(**overrides)

    assert str(error.value) == "Invalid imported History occurrence"
    assert "private" not in str(error.value)


def test_open_role_and_kind_values_are_not_mapped() -> None:
    occurrence = _occurrence(role="future-role", kind="future-event")

    assert occurrence.role == "future-role"
    assert occurrence.kind == "future-event"
