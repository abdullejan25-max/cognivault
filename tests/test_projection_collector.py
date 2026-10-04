import json
import sqlite3
from pathlib import Path

import pytest

from cognivault.adapters.documents import DocumentInput, SQLiteDocumentStore
from cognivault.adapters.history import SQLiteHistoryBackend
from cognivault.adapters.wrong_answers import SQLiteWrongAnswerStore
from cognivault.config import AppConfig
from cognivault.contracts import HistoryImportItem
from cognivault.gateway import Gateway
from cognivault.obsidian_projection import render_projection
from cognivault.projection_collector import (
    ProjectionCollectionError,
    collect_projection,
    collect_wrong_answer_projection,
)
from cognivault.obsidian_writer import write_projection


def test_wrong_answer_only_collection_excludes_unconfigured_history(tmp_path):
    from cognivault.adapters.history import NotConfiguredHistoryBackend
    from cognivault.contracts import GatewayError

    gateway = _projection_gateway(tmp_path, source_count=1, analysis_count=2)
    gateway.history_backend = NotConfiguredHistoryBackend()
    collected = collect_wrong_answer_projection(gateway)
    assert collected.wrong_answer_source_count == 1
    assert collected.wrong_answer_analysis_count == 2
    assert collected.snapshot.history_sources == ()
    assert collected.snapshot.history_items == ()
    assert collected.snapshot.legacy_sources == ()
    rendered = render_projection(collected.snapshot)
    page = next(text for path, text in rendered.items() if path.startswith('WrongAnswers/items/'))
    assert 'version: 2\n' in page and '## Version 3' not in page
    with pytest.raises(GatewayError, match='HISTORY_UNAVAILABLE'):
        collect_projection(gateway)


def test_wrong_answer_only_collection_enforces_projection_permission(tmp_path):
    from cognivault.contracts import GatewayError

    gateway = _projection_gateway(tmp_path, source_count=1, analysis_count=1)
    gateway.capabilities = frozenset({'read'})
    with pytest.raises(GatewayError, match='PERMISSION_DENIED'):
        collect_wrong_answer_projection(gateway)


def test_wrong_answer_only_collection_rejects_incomplete_snapshot(tmp_path, monkeypatch):
    gateway = _projection_gateway(tmp_path, source_count=1, analysis_count=1)
    original = gateway.projection_snapshot

    def missing_analysis(domain, operation, **kwargs):
        page = original(domain, operation, **kwargs)
        if operation == 'records':
            page['analyses'] = []
        return page

    monkeypatch.setattr(gateway, 'projection_snapshot', missing_analysis)
    with pytest.raises(ProjectionCollectionError):
        collect_wrong_answer_projection(gateway)


def _projection_gateway(tmp_path: Path, *, source_count: int = 21,
                        analysis_count: int = 21) -> Gateway:
    assets = tmp_path / "assets"
    assets.mkdir()
    document_store = SQLiteDocumentStore(assets, tmp_path / "documents.db")
    document = document_store.ingest_documents([DocumentInput(
        "Synthetic question source", "application/pdf", b"%PDF-1.4 synthetic",
        ("Synthetic page",), "text_layer",
    )])[0]
    history = SQLiteHistoryBackend(tmp_path / "history.db")
    for index in range(source_count):
        source_id = f"history-{index:02d}"
        history.register_source(source_id, f"Synthetic source {index}")
        history.import_items(source_id, [HistoryImportItem(
            f"item-{index:02d}", f"conversation-{index:02d}", "user",
            f"Synthetic message {index}", "2026-09-01T00:00:00Z",
        )])
    wrong_store = SQLiteWrongAnswerStore(document_store)
    first_wrong_source = None
    for index in range(source_count):
        created = wrong_store.register_source(
            document.uri, f"Synthetic question {index}", "Synthetic wrong answer",
        )
        first_wrong_source = first_wrong_source or created
    gateway = Gateway(
        AppConfig("0.1.0", None, history_database=history.database_path),
        None, history_backend=history, document_store=document_store,
        capabilities=frozenset({"write", "projection"}), qmd_discoverable=lambda: False,
    )
    for version in range(analysis_count):
        gateway.save_wrong_answer_analysis(
            first_wrong_source["source_id"],
            {"error_type": "synthetic", "knowledge_points": ["synthetic"],
             "reasoning": f"Synthetic reasoning {version}",
             "correct_solution": "Synthetic solution", "review_advice": "Synthetic review"},
            [document.uri], [], f"collector-analysis-{version}", version,
        )
    gateway.capabilities = frozenset({"projection"})
    return gateway


def test_collector_pages_all_sources_reconciles_counts_and_builds_renderer_input(tmp_path: Path) -> None:
    gateway = _projection_gateway(tmp_path)

    result = collect_projection(gateway)

    assert result.history_source_count == 21
    assert result.history_item_count == 21
    assert result.wrong_answer_source_count == 21
    assert result.wrong_answer_analysis_count == 21
    assert result.history_snapshot_token.startswith("history-v1:")
    assert result.wrong_answer_snapshot_token.startswith("wrong-v1:")
    assert len(result.snapshot.history_sources) == 21
    assert len(result.snapshot.history_items) == 21
    assert len(result.snapshot.wrong_answer_bundles) == 21
    assert max(len(bundle["analyses"]) for bundle in result.snapshot.wrong_answer_bundles) == 21
    assert render_projection(result.snapshot)


def test_collector_renderer_and_writer_form_synthetic_one_way_pipeline(
        tmp_path: Path) -> None:
    gateway = _projection_gateway(tmp_path, source_count=1, analysis_count=1)
    with sqlite3.connect(gateway.document_store.database_path) as connection:
        audit_before = connection.execute("SELECT COUNT(*) FROM operation_audit").fetchone()[0]

    collected = collect_projection(gateway)
    rendered = render_projection(collected.snapshot)
    target = tmp_path / "V2Projection"
    write_projection(rendered, target)

    manifest = json.loads((target / ".projection-manifest.json").read_text(encoding="utf-8"))
    assert manifest == {"schema_version": 1, "files": sorted(rendered)}
    assert all((target / relative).is_file() for relative in rendered)
    with sqlite3.connect(gateway.document_store.database_path) as connection:
        audit_after = connection.execute("SELECT COUNT(*) FROM operation_audit").fetchone()[0]
    assert audit_after == audit_before


def test_collector_rejects_domain_count_mismatch_without_returning_partial_snapshot(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = _projection_gateway(tmp_path, source_count=1, analysis_count=1)
    original = gateway.projection_snapshot

    def wrong_total(domain: str, operation: str, **kwargs):
        page = original(domain, operation, **kwargs)
        if domain == "history" and operation == "begin":
            page["total_records"] += 1
        return page

    monkeypatch.setattr(gateway, "projection_snapshot", wrong_total)
    with pytest.raises(ProjectionCollectionError):
        collect_projection(gateway)


def test_collector_rejects_nonadvancing_page_cursor(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = _projection_gateway(tmp_path, source_count=1, analysis_count=1)
    original = gateway.projection_snapshot

    def stuck_cursor(domain: str, operation: str, **kwargs):
        page = original(domain, operation, **kwargs)
        if domain == "history" and operation == "sources":
            page["has_more"] = True
            page["next_cursor"] = kwargs.get("cursor", 0)
        return page

    monkeypatch.setattr(gateway, "projection_snapshot", stuck_cursor)
    with pytest.raises(ProjectionCollectionError):
        collect_projection(gateway)


def test_collector_rejects_boolean_count_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = _projection_gateway(tmp_path, source_count=1, analysis_count=1)
    original = gateway.projection_snapshot

    def boolean_count(domain: str, operation: str, **kwargs):
        page = original(domain, operation, **kwargs)
        if domain == "wrong_answers" and operation == "records":
            page["total_records"] = True
        return page

    monkeypatch.setattr(gateway, "projection_snapshot", boolean_count)
    with pytest.raises(ProjectionCollectionError):
        collect_projection(gateway)
