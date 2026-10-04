"""Immutable, non-persisted occurrence envelopes for migration preparation."""

from __future__ import annotations

from dataclasses import dataclass, field
import re


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CONFLICT_CODES = frozenset({
    "role_conflict", "content_conflict", "timestamp_conflict", "ordering_conflict",
    "duplicate_source_item_id", "conversation_unresolved", "attachment_unresolved",
    "branch_unresolved",
})
_MAX_LABEL_CHARS = 80
_MAX_PRIVATE_REF_CHARS = 4096
_MAX_TEXT_CHARS = 1024 * 1024
_MAX_TIMESTAMP_CHARS = 128


def _has_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _valid_optional_string(value: object, *, maximum: int, controls: bool = True) -> bool:
    return value is None or (
        type(value) is str
        and len(value) <= maximum
        and (not controls or not _has_control(value))
    )


@dataclass(frozen=True)
class HistoryOccurrenceBlock:
    """One typed source block; sensitive text and references are redacted from repr."""

    kind: str
    text: str | None = field(default=None, repr=False)
    source_ref: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.kind) is not str or not self.kind or len(self.kind) > _MAX_LABEL_CHARS \
                or _has_control(self.kind):
            raise ValueError("Invalid imported History occurrence block")
        if not _valid_optional_string(self.text, maximum=_MAX_TEXT_CHARS, controls=False):
            raise ValueError("Invalid imported History occurrence block")
        if not _valid_optional_string(self.source_ref, maximum=_MAX_PRIVATE_REF_CHARS):
            raise ValueError("Invalid imported History occurrence block")
        if self.text is None and self.source_ref is None:
            raise ValueError("Invalid imported History occurrence block")


@dataclass(frozen=True)
class ImportedHistoryOccurrence:
    """Versioned source occurrence; not a canonical message or a History write item."""

    schema_version: int
    source_system: str
    snapshot_sha256: str = field(repr=False)
    source_member_ref: str = field(repr=False)
    record_ordinal: int
    kind: str
    role: str | None
    content_blocks: tuple[HistoryOccurrenceBlock, ...]
    source_byte_start: int | None = field(default=None, repr=False)
    source_byte_end: int | None = field(default=None, repr=False)
    source_item_id: str | None = field(default=None, repr=False)
    conversation_ref: str | None = field(default=None, repr=False)
    conversation_title: str | None = field(default=None, repr=False)
    author: str | None = field(default=None, repr=False)
    model_name: str | None = field(default=None, repr=False)
    source_timestamp_raw: str | None = field(default=None, repr=False)
    source_created_at: str | None = field(default=None, repr=False)
    source_updated_at: str | None = field(default=None, repr=False)
    imported_at: str | None = field(default=None, repr=False)
    import_batch_id: str | None = field(default=None, repr=False)
    conversation_order: int | None = None
    message_order: int | None = None
    branch_refs: tuple[str, ...] = field(default=(), repr=False)
    unknown_field_refs: tuple[str, ...] = field(default=(), repr=False)
    conflict_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        optional_private = (
            self.source_item_id, self.conversation_ref, self.conversation_title,
            self.author, self.model_name, self.import_batch_id,
        )
        timestamps = (
            self.source_timestamp_raw, self.source_created_at,
            self.source_updated_at, self.imported_at,
        )
        orders = (self.record_ordinal, self.conversation_order, self.message_order)
        byte_range = (self.source_byte_start, self.source_byte_end)
        if type(self.schema_version) is not int or self.schema_version != 1 \
                or type(self.source_system) is not str or not self.source_system \
                or len(self.source_system) > _MAX_LABEL_CHARS or _has_control(self.source_system) \
                or type(self.snapshot_sha256) is not str or _SHA256.fullmatch(self.snapshot_sha256) is None \
                or not _valid_optional_string(self.source_member_ref, maximum=_MAX_PRIVATE_REF_CHARS) \
                or not self.source_member_ref \
                or type(self.record_ordinal) is not int or self.record_ordinal < 0 \
                or (byte_range[0] is None) != (byte_range[1] is None) \
                or (byte_range[0] is not None and (
                    type(byte_range[0]) is not int or type(byte_range[1]) is not int
                    or byte_range[0] < 0 or byte_range[1] <= byte_range[0]
                )) \
                or type(self.kind) is not str or not self.kind or len(self.kind) > _MAX_LABEL_CHARS \
                or _has_control(self.kind) \
                or not _valid_optional_string(self.role, maximum=_MAX_LABEL_CHARS) \
                or type(self.content_blocks) is not tuple \
                or any(type(block) is not HistoryOccurrenceBlock for block in self.content_blocks) \
                or any(not _valid_optional_string(value, maximum=_MAX_PRIVATE_REF_CHARS)
                       for value in optional_private) \
                or any(not _valid_optional_string(value, maximum=_MAX_TIMESTAMP_CHARS)
                       for value in timestamps) \
                or any(value is not None and (type(value) is not int or value < 0)
                       for value in orders[1:]) \
                or type(self.branch_refs) is not tuple \
                or type(self.unknown_field_refs) is not tuple \
                or any(not _valid_optional_string(value, maximum=_MAX_PRIVATE_REF_CHARS)
                       or value is None for value in self.branch_refs + self.unknown_field_refs) \
                or type(self.conflict_codes) is not tuple \
                or any(type(code) is not str or code not in _CONFLICT_CODES for code in self.conflict_codes):
            raise ValueError("Invalid imported History occurrence")
