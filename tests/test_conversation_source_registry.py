from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import stat

import pytest

from cognivault.migration.conversation_registry import (
    ConversationSourceRecord,
    ConversationSourceRegistry,
    default_conversation_registry_path,
)


def _record(**overrides) -> ConversationSourceRecord:
    values = {
        "source_id": "source-" + "a" * 32,
        "source_system": "gemini",
        "source_type": "official_export",
        "acquisition_method": "official_export",
        "discovered": True,
        "accessible": False,
        "export_status": "processing",
        "import_status": "not_started",
        "raw_format": None,
        "stable_identity": None,
        "conversation_count": None,
        "message_count": None,
        "earliest_known_time": None,
        "latest_known_time": None,
        "source_hash": None,
        "manifest_hash": None,
        "imported_count": 0,
        "deduplicated_count": 0,
        "unresolved_count": 0,
        "coverage_notes": ("official_export_pending",),
        "private_locator": None,
    }
    values.update(overrides)
    return ConversationSourceRecord(**values)


def test_record_accepts_fixed_vocabulary_and_utc_timestamps() -> None:
    record = _record(earliest_known_time="2026-09-27T08:05:00Z")

    assert record.source_system == "gemini"
    assert record.earliest_known_time == "2026-09-27T08:05:00Z"


@pytest.mark.parametrize(
    "overrides",
    [
        {"export_status": "user said click this"},
        {"source_system": "personal-account-name"},
        {"source_id": "C:\\private\\source"},
        {"conversation_count": -1},
        {"message_count": True},
        {"imported_count": -1},
        {"earliest_known_time": "2026-09-27T08:05:00"},
        {"earliest_known_time": "not-a-time"},
        {"source_hash": "not-a-hash"},
        {"manifest_hash": "a" * 63},
        {"coverage_notes": ("real chat title",)},
        {"private_locator": "private path with\ncontrol"},
    ],
)
def test_record_rejects_unbounded_or_malformed_metadata(overrides: dict) -> None:
    with pytest.raises(ValueError, match="Invalid conversation source record"):
        _record(**overrides)


def test_public_summary_contains_only_aggregate_statuses(tmp_path: Path) -> None:
    registry_path = tmp_path / "private" / "sources.sqlite3"
    synthetic_locator = "path:" + str((registry_path.parent / "export.zip").resolve())
    registry = ConversationSourceRegistry(registry_path)
    registry.upsert(_record(
        source_id="source-" + "b" * 32,
        stable_identity="opaque-" + "e" * 32,
        source_hash="c" * 64,
        manifest_hash="d" * 64,
        private_locator=synthetic_locator,
        coverage_notes=("official_export_pending",),
    ))

    summary = registry.public_summary()

    assert summary == {
        "source_count": 1,
        "discovered_count": 1,
        "accessible_count": 0,
        "export_status_counts": {"processing": 1},
        "import_status_counts": {"not_started": 1},
        "source_reported_conversation_count_sum": 0,
        "conversation_count_known_sources": 0,
        "source_reported_message_count_sum": 0,
        "message_count_known_sources": 0,
        "source_reported_imported_item_count_sum": 0,
        "source_reported_deduplicated_item_count_sum": 0,
        "source_reported_unresolved_item_count_sum": 0,
    }
    rendered = repr(summary)
    for private_value in (
        "source-b", "opaque-" + "e" * 32, "c" * 64, "d" * 64,
        synthetic_locator, "gemini",
    ):
        assert private_value not in rendered


def test_registry_round_trips_and_replaces_by_source_id(tmp_path: Path) -> None:
    registry_path = tmp_path / "private" / "sources.sqlite3"
    registry = ConversationSourceRegistry(registry_path)
    first = _record(source_id="source-" + "a" * 32)
    second = _record(source_id="source-" + "b" * 32, source_system="chatgpt")
    assert "source-" not in repr(first)
    registry.upsert(first)
    registry.upsert(second)
    updated = replace(first, export_status="available", accessible=True)
    registry.upsert(updated)

    reopened = ConversationSourceRegistry(registry_path)

    assert reopened.list_sources() == (updated, second)


def test_jsonl_source_round_trips_without_inventing_conversation_counts(tmp_path: Path) -> None:
    registry = ConversationSourceRegistry(tmp_path / "private" / "sources.sqlite3")
    source = _record(
        source_system="codex",
        source_type="local_history",
        acquisition_method="bounded_local_inventory",
        export_status="available",
        import_status="blocked",
        raw_format="jsonl",
        conversation_count=None,
        message_count=None,
        unresolved_count=82,
        source_hash="a" * 64,
        manifest_hash="b" * 64,
        private_locator="path:" + str((tmp_path / "private" / "snapshot").resolve()),
    )
    registry.upsert(source)

    reopened = ConversationSourceRegistry(registry.path)
    restored = reopened.list_sources()[0]
    summary = reopened.public_summary()

    assert restored == source
    assert restored.raw_format == "jsonl"
    assert restored.conversation_count is None
    assert restored.message_count is None
    assert summary["conversation_count_known_sources"] == 0
    assert summary["message_count_known_sources"] == 0
    assert "jsonl" not in repr(summary)
    assert "a" * 64 not in repr(summary)


def test_registry_rejects_repository_path(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    with pytest.raises(ValueError, match="Invalid private conversation registry"):
        ConversationSourceRegistry(repository_root / "registry.sqlite3")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes are not portable to Windows")
def test_registry_storage_is_owner_only_on_posix(tmp_path: Path) -> None:
    registry_path = tmp_path / "private" / "sources.sqlite3"
    registry = ConversationSourceRegistry(registry_path)
    assert stat.S_IMODE(registry_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(registry.path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes are not portable to Windows")
def test_registry_rejects_shared_existing_directory_without_chmod(tmp_path: Path) -> None:
    registry_dir = tmp_path / "shared"
    registry_dir.mkdir()
    os.chmod(registry_dir, 0o755)
    before = stat.S_IMODE(registry_dir.stat().st_mode)
    if before & 0o077 == 0:
        pytest.skip("The test filesystem does not preserve shared permission bits")

    with pytest.raises(ValueError, match="Invalid private conversation registry"):
        ConversationSourceRegistry(registry_dir / "sources.sqlite3")

    assert stat.S_IMODE(registry_dir.stat().st_mode) == before


@pytest.mark.skipif(os.name != "nt", reason="Windows per-user state path restriction")
def test_registry_rejects_windows_path_outside_user_state_root(tmp_path: Path) -> None:
    outside_user_state = Path(tmp_path.anchor) / "non-user-state" / "sources.sqlite3"
    with pytest.raises(ValueError, match="Invalid private conversation registry"):
        ConversationSourceRegistry(outside_user_state)


def test_default_registry_path_uses_only_configured_state_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert default_conversation_registry_path() == (
        tmp_path / "local" / "ChatGPTStudySystemV2" / "migration" /
        "conversation-source-registry" / "sources.sqlite3"
    )

    monkeypatch.delenv("LOCALAPPDATA")
    assert default_conversation_registry_path() == (
        tmp_path / "xdg" / "ChatGPTStudySystemV2" / "migration" /
        "conversation-source-registry" / "sources.sqlite3"
    )

    monkeypatch.delenv("XDG_STATE_HOME")
    with pytest.raises(RuntimeError, match="Private local registry location is not configured"):
        default_conversation_registry_path()
