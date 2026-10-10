"""Synthetic wrong-answer records and agent-authored analysis only."""

import base64
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from cognivault.adapters.documents import DocumentInput, SQLiteDocumentStore
import cognivault.adapters.wrong_answers as wrong_answer_adapter
from cognivault.config import AppConfig
from cognivault.contracts import GatewayError
from cognivault.gateway import Gateway


def gateway_with_sources(tmp_path: Path) -> tuple[Gateway, str, str, str]:
    assets = tmp_path / "assets"
    assets.mkdir()
    study = tmp_path / "study"
    study.mkdir()
    (study / "fractions.md").write_text("# Fractions\nSynthetic lesson", encoding="utf-8")
    store = SQLiteDocumentStore(assets, tmp_path / "private.db")
    doc = store.ingest_documents([DocumentInput(
        "Invented exercise", "application/pdf", b"%PDF-1.4 invented",
        ("Question 1: 1/2 + 1/3",), "ocr",
    )])[0]
    gateway = Gateway(AppConfig("0.1.0", study), None, document_store=store,
                      capabilities=frozenset({"read", "write", "ingest"}),
                      qmd_discoverable=lambda: False)
    return gateway, doc.uri, doc.asset_uri, "study:fractions.md"


def analysis() -> dict:
    return {"error_type": "denominator", "knowledge_points": ["fraction addition"],
            "reasoning": "The student added denominators.",
            "correct_solution": "Use sixths: 3/6 + 2/6 = 5/6.",
            "review_advice": "Practice a second fraction sum."}


@pytest.mark.parametrize("page_count,page_number", [(65, 64), (65, 65), (999, 999)])
def test_wrong_answer_source_accepts_existing_document_pages(tmp_path, page_count, page_number):
    assets = tmp_path / "assets"
    assets.mkdir()
    gateway = Gateway(
        AppConfig("0.1.0", tmp_path), None,
        document_store=SQLiteDocumentStore(assets, tmp_path / "synthetic.db"),
        capabilities=frozenset({"read", "write", "ingest"}),
        qmd_discoverable=lambda: False,
    )
    text = "\f".join(f"Synthetic page {number}" for number in range(1, page_count + 1))
    document = gateway.ingest_documents([{
        "title": "Synthetic paginated exercise", "media_type": "text/plain",
        "content_base64": base64.b64encode(text.encode()).decode(),
    }])["documents"][0]
    uri = document["document_uri"]
    page = gateway.fetch_document_page(uri, page_number)["page"]
    assert page["text"] == f"Synthetic page {page_number}"
    source = gateway.register_wrong_answer_source(uri, "Synthetic Q", "Synthetic A", page_number)["source"]
    assert source["page_number"] == page_number
    assert source["source_uri"] == uri
    assert source["text_origin"] == page["text_origin"]
    assert gateway.register_wrong_answer_source(uri, "Synthetic Q", "Synthetic A", page_number)["source"] == source
    assert gateway.get_wrong_answer_bundle(source["source_id"])["source"] == source


@pytest.mark.parametrize("page_number,code", [(1000, "INVALID_ARGUMENT"), (65, "RESOURCE_NOT_FOUND")])
def test_wrong_answer_source_preserves_document_page_errors(tmp_path, page_number, code):
    gateway, uri, _, _ = gateway_with_sources(tmp_path)
    with pytest.raises(GatewayError) as error:
        gateway.register_wrong_answer_source(uri, "Synthetic Q", "Synthetic A", page_number)
    assert error.value.code == code


@pytest.mark.parametrize("page_number", [1, 65, 999])
def test_wrong_answer_asset_source_rejects_page_number(tmp_path, page_number):
    gateway, _, asset_uri, _ = gateway_with_sources(tmp_path)
    with pytest.raises(GatewayError) as error:
        gateway.register_wrong_answer_source(asset_uri, "Synthetic Q", "Synthetic A", page_number)
    assert error.value.code == "INVALID_ARGUMENT"


def test_source_is_immutable_and_idempotent(tmp_path: Path) -> None:
    gateway, doc_uri, _, _ = gateway_with_sources(tmp_path)
    data = {"source_uri": doc_uri, "page_number": 1,
            "question_text": "1/2 + 1/3", "student_answer": "2/5"}
    first = gateway.register_wrong_answer_source(**data)["source"]
    assert first["source_uri"] == doc_uri
    assert first["text_origin"] == "supplied_ocr_derived"
    assert first["source_id"].startswith("wrong-answer://sha256/")
    assert gateway.register_wrong_answer_source(**data)["source"] == first
    changed = gateway.register_wrong_answer_source(**{**data, "student_answer": "1/5"})["source"]
    assert changed["source_id"] != first["source_id"]
    assert gateway.get_wrong_answer_bundle(first["source_id"])["source"] == first


def test_projection_snapshot_watermark_paginates_sources_and_analysis_versions(tmp_path: Path) -> None:
    gateway, doc_uri, _, study_ref = gateway_with_sources(tmp_path)
    source_a = gateway.register_wrong_answer_source(doc_uri, "Synthetic A", "Answer A")["source"]
    source_b = gateway.register_wrong_answer_source(doc_uri, "Synthetic B", "Answer B")["source"]
    gateway.save_wrong_answer_analysis(
        source_a["source_id"], analysis(), [doc_uri], [study_ref], "projection-analysis-a", 0,
    )
    store = gateway._wrong_answers()
    started = store.projection_snapshot("begin")
    gateway.register_wrong_answer_source(doc_uri, "Synthetic later", "Answer later")
    gateway.save_wrong_answer_analysis(
        source_a["source_id"], analysis(), [doc_uri], [study_ref], "projection-analysis-b", 1,
    )

    source_page = store.projection_snapshot("sources", snapshot_token=started["snapshot_token"], limit=1)
    source_tail = store.projection_snapshot(
        "sources", snapshot_token=started["snapshot_token"],
        cursor=source_page["next_cursor"], limit=1,
    )
    analysis_page = store.projection_snapshot(
        "records", snapshot_token=started["snapshot_token"], source_id=source_a["source_id"], limit=1,
    )

    assert started["total_sources"] == 2
    assert started["total_records"] == 1
    assert source_page["sources"][0]["source_id"] == source_a["source_id"]
    assert source_page["sources"][0]["analysis_count"] == 1
    assert source_tail["sources"][0]["source_id"] == source_b["source_id"]
    assert source_tail["sources"][0]["analysis_count"] == 0
    assert [row["version"] for row in analysis_page["analyses"]] == [1]
    assert analysis_page["has_more"] is False


def test_projection_snapshot_rejects_invalid_token_and_source(tmp_path: Path) -> None:
    gateway, _, _, _ = gateway_with_sources(tmp_path)
    store = gateway._wrong_answers()
    with pytest.raises(GatewayError) as invalid_action:
        store.projection_snapshot("all")
    assert invalid_action.value.code == "INVALID_ARGUMENT"
    started = store.projection_snapshot("begin")
    with pytest.raises(GatewayError) as invalid_token:
        store.projection_snapshot("sources", snapshot_token="wrong-v1:0:0:extra")
    assert invalid_token.value.code == "INVALID_ARGUMENT"
    with pytest.raises(GatewayError) as invalid_source:
        store.projection_snapshot("records", snapshot_token=started["snapshot_token"], source_id="C:/private")
    assert invalid_source.value.code == "INVALID_ARGUMENT"


def test_projection_snapshot_rejects_noncontinuation_cursor_and_enforces_input_budget(
    tmp_path: Path, monkeypatch,
) -> None:
    gateway, doc_uri, _, study_ref = gateway_with_sources(tmp_path)
    source = gateway.register_wrong_answer_source(doc_uri, "Synthetic question", "Synthetic answer")["source"]
    gateway.save_wrong_answer_analysis(
        source["source_id"], analysis(), [doc_uri], [study_ref], "projection-budget", 0,
    )
    store = gateway._wrong_answers()
    started = store.projection_snapshot("begin")
    with pytest.raises(GatewayError) as invalid_cursor:
        store.projection_snapshot("records", snapshot_token=started["snapshot_token"],
                                  source_id=source["source_id"], cursor=99)
    assert invalid_cursor.value.code == "INVALID_ARGUMENT"

    monkeypatch.setattr(wrong_answer_adapter, "_PROJECTION_MAX_BYTES", 1)
    with pytest.raises(GatewayError) as oversized:
        store.projection_snapshot("begin")
    assert oversized.value.code == "PAYLOAD_TOO_LARGE"


def test_gateway_projection_snapshot_reuses_safe_wrong_answer_serializers(tmp_path: Path) -> None:
    gateway, doc_uri, _, study_ref = gateway_with_sources(tmp_path)
    source = gateway.register_wrong_answer_source(
        doc_uri, "C:\\private\\question.txt", "Synthetic answer",
    )["source"]
    supplied_analysis = analysis()
    supplied_analysis["reasoning"] = "C:\\private\\analysis.txt"
    gateway.save_wrong_answer_analysis(
        source["source_id"], supplied_analysis, [doc_uri], [study_ref], "projection-safe-dto", 0,
    )
    with sqlite3.connect(gateway.document_store.database_path) as connection:
        audit_count_before = connection.execute("SELECT COUNT(*) FROM operation_audit").fetchone()[0]
    gateway.capabilities = frozenset({"projection"})

    started = gateway.projection_snapshot("wrong_answers", "begin")
    source_page = gateway.projection_snapshot(
        "wrong_answers", "sources", snapshot_token=started["snapshot_token"],
    )
    result_source = source_page["sources"][0]
    records = gateway.projection_snapshot(
        "wrong_answers", "records", snapshot_token=started["snapshot_token"],
        source_id=result_source["source_id"],
    )

    assert result_source["question_text"] == "[local path redacted]"
    assert records["analyses"][0]["reasoning"] == "[local path redacted]"
    with sqlite3.connect(gateway.document_store.database_path) as connection:
        audit_count_after = connection.execute("SELECT COUNT(*) FROM operation_audit").fetchone()[0]
    assert audit_count_after == audit_count_before


def test_gateway_projection_redacts_provenance_identity_and_rejects_bad_stored_relations(
        tmp_path: Path) -> None:
    gateway, doc_uri, _, study_ref = gateway_with_sources(tmp_path)
    source = gateway.register_wrong_answer_source(doc_uri, "Question", "Answer")['source']
    gateway.save_wrong_answer_analysis(
        source["source_id"], analysis(), [doc_uri], [study_ref], "projection-provenance", 0,
        {"reported_agent": "SyntheticAgent"},
    )
    with sqlite3.connect(gateway.document_store.database_path) as connection:
        connection.execute("DROP TRIGGER write_provenance_no_update")
        connection.execute(
            "UPDATE write_provenance SET reported_agent=? WHERE record_type='wrong_answer_analysis'",
            ("C:\\private\\agent",),
        )
    gateway.capabilities = frozenset({"projection"})
    started = gateway.projection_snapshot("wrong_answers", "begin")
    result = gateway.projection_snapshot(
        "wrong_answers", "records", snapshot_token=started["snapshot_token"],
        source_id=source["source_id"],
    )
    provenance = result["analyses"][0]["write_provenance"]
    assert provenance == {
        "data_origin": "agent_generated", "actor_type": "external_client",
        "identity_trust": "reported", "legacy_status": "native",
    }

    with sqlite3.connect(gateway.document_store.database_path) as connection:
        connection.execute("DROP TRIGGER wrong_analyses_no_update")
        connection.execute("UPDATE wrong_analyses SET study_relations='[\"study:../secret\"]'")
    with pytest.raises(GatewayError) as error:
        gateway.projection_snapshot(
            "wrong_answers", "records", snapshot_token=started["snapshot_token"],
            source_id=source["source_id"],
        )
    assert error.value.code == "BACKEND_BAD_OUTPUT"


def test_source_evidence_must_have_a_verified_asset_blob(tmp_path: Path) -> None:
    gateway, doc_uri, _, _ = gateway_with_sources(tmp_path)
    document = gateway.document_store.fetch_document(doc_uri)
    asset = gateway.document_store.fetch_asset_record(document.asset_uri)
    (gateway.document_store.asset_root / asset.sha256[:2] / asset.sha256).unlink()
    with pytest.raises(GatewayError) as error:
        gateway.register_wrong_answer_source(doc_uri, "Question", "Wrong", 1)
    assert error.value.code == "STORAGE_UNAVAILABLE"


def test_distinct_problems_on_one_page_have_distinct_immutable_ids(tmp_path: Path) -> None:
    gateway, doc_uri, _, _ = gateway_with_sources(tmp_path)
    first = gateway.register_wrong_answer_source(doc_uri, "Question one", "Answer one", 1)["source"]
    second = gateway.register_wrong_answer_source(doc_uri, "Question two", "Answer two", 1)["source"]
    assert first["source_id"] != second["source_id"]
    assert gateway.get_wrong_answer_bundle(first["source_id"])["source"]["question_text"] == "Question one"
    assert gateway.get_wrong_answer_bundle(second["source_id"])["source"]["question_text"] == "Question two"


def test_preexisting_page_identity_is_reused_for_same_source(tmp_path: Path) -> None:
    gateway, doc_uri, _, _ = gateway_with_sources(tmp_path)
    old_id = "wrong-answer://sha256/" + hashlib.sha256(
        json.dumps([doc_uri, 1], ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    con = gateway.document_store._connect(write=True)
    try:
        gateway._wrong_answers()._schema(con)
        con.execute("INSERT INTO wrong_sources VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (old_id, doc_uri, 1, "Legacy question", "Legacy answer",
                     "supplied_ocr_derived", "2026-09-25T00:00:00Z"))
        con.execute("DELETE FROM schema_migrations WHERE scope='wrong_answers'")
        con.commit()
    finally:
        con.close()
    registered = gateway.register_wrong_answer_source(doc_uri, "Legacy question", "Legacy answer", 1)["source"]
    assert registered["source_id"] == old_id
    assert registered["write_provenance"]["legacy_status"] == "pre_provenance"
    assert registered["write_provenance"]["reported_agent"] is None


def test_analysis_versions_provenance_and_validation(tmp_path: Path) -> None:
    gateway, doc_uri, asset_uri, study_id = gateway_with_sources(tmp_path)
    source = gateway.register_wrong_answer_source(doc_uri, "1/2 + 1/3", "2/5", 1)["source"]
    args = {"source_id": source["source_id"], "analysis": analysis(),
            "source_refs": [doc_uri, asset_uri], "study_relations": [study_id],
            "idempotency_key": "analysis-1"}
    first = gateway.save_wrong_answer_analysis(**args)["analysis"]
    assert first["version"] == 1
    assert first["generated_by_agent"] is True
    assert first["provenance"] == "agent_supplied"
    assert first["source_refs"] == [doc_uri, asset_uri]
    assert first["study_relations"] == [study_id]
    assert first["created_at"].endswith("Z")
    assert first["supersedes_analysis_id"] is None
    assert first["write_provenance"]["data_origin"] == "agent_generated"
    assert first["write_provenance"]["identity_trust"] == "unavailable"
    assert gateway.save_wrong_answer_analysis(**args)["analysis"] == first
    second = gateway.update_wrong_answer_analysis(**{**args, "analysis": {**analysis(),
        "review_advice": "Try a third fraction sum."}, "idempotency_key": "analysis-2",
        "expected_version": 1})["analysis"]
    assert second["version"] == 2
    assert second["supersedes_analysis_id"] == first["analysis_id"]
    assert second["write_provenance"]["supersedes_provenance_id"] == \
        first["write_provenance"]["provenance_id"]
    bundle = gateway.get_wrong_answer_bundle(source["source_id"])
    assert [item["version"] for item in bundle["analyses"]] == [2, 1]
    assert bundle["source"] == source
    assert gateway.search_wrong_answers("denominator")["results"][0]["source_id"] == source["source_id"]


def test_analysis_records_reported_agent_and_client_without_authentication_claim(tmp_path: Path) -> None:
    gateway, doc_uri, _, study_id = gateway_with_sources(tmp_path)
    source = gateway.register_wrong_answer_source(doc_uri, "Synthetic problem", "Synthetic answer")["source"]
    record = gateway.save_wrong_answer_analysis(
        source["source_id"], analysis(), [doc_uri], [study_id], "reported-identities",
        provenance={"reported_agent": "ChatGPT", "reported_client": "mcp-host", "run_id": "run-7"},
    )["analysis"]
    provenance = record["write_provenance"]
    assert provenance["reported_agent"] == "ChatGPT"
    assert provenance["reported_client"] == "mcp-host"
    assert provenance["run_id"] == "run-7"
    assert provenance["identity_trust"] == "reported"
    assert provenance["source_refs"] == [doc_uri]
    assert provenance["actor_type"] == "external_client"
    assert provenance["recorded_at"].endswith("Z")


def test_uninitialized_configured_store_supports_wrong_answer_search_and_save(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    study = tmp_path / "study"
    study.mkdir()
    (study / "fractions.md").write_text("# Fractions\nSynthetic lesson", encoding="utf-8")
    database = tmp_path / "private.db"
    store = SQLiteDocumentStore(assets, database)
    gateway = Gateway(AppConfig("0.1.0", study), None, document_store=store,
                      capabilities=frozenset({"read", "write", "ingest"}),
                      qmd_discoverable=lambda: False)

    assert not database.exists()
    assert gateway.search_wrong_answers("fraction") == {
        "total": 0, "has_more": False, "results": [],
    }
    assert not database.exists()

    asset = gateway.register_asset(
        "image/png", base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic image").decode(),
        provenance={"reported_agent": "Codex", "reported_client": "pytest"},
    )["asset"]
    source = gateway.register_wrong_answer_source(
        asset["asset_uri"], "Synthetic fraction problem", "Synthetic wrong answer",
        provenance={"reported_agent": "Codex", "reported_client": "pytest", "run_id": "run-1"},
    )["source"]
    saved = gateway.save_wrong_answer_analysis(
        source["source_id"], analysis(), [asset["asset_uri"]], ["study:fractions.md"], "empty-db-save",
        provenance={"reported_agent": "Codex", "reported_client": "pytest", "run_id": "run-2"},
    )["analysis"]

    assert saved["version"] == 1
    assert saved["write_provenance"]["data_origin"] == "agent_generated"
    assert saved["write_provenance"]["source_refs"] == [asset["asset_uri"]]
    assert saved["write_provenance"]["run_id"] == "run-2"
    revised = gateway.update_wrong_answer_analysis(
        source["source_id"], {**analysis(), "review_advice": "Synthetic revision"},
        [asset["asset_uri"]], ["study:fractions.md"], "empty-db-revision", 1,
        provenance={"reported_agent": "Codex", "reported_client": "pytest", "run_id": "run-3"},
    )["analysis"]
    assert revised["version"] == 2
    assert revised["supersedes_analysis_id"] == saved["analysis_id"]
    assert revised["write_provenance"]["supersedes_provenance_id"] == \
        saved["write_provenance"]["provenance_id"]
    fetched = gateway.get_wrong_answer_bundle(source["source_id"])
    assert fetched["analyses"] == [revised, saved]
    assert gateway.search_wrong_answers("fraction")["results"][0]["source_id"] == source["source_id"]


def test_invalid_references_schema_and_version_leave_store_unchanged(tmp_path: Path) -> None:
    gateway, doc_uri, asset_uri, study_id = gateway_with_sources(tmp_path)
    source = gateway.register_wrong_answer_source(doc_uri, "1/2 + 1/3", "2/5", 1)["source"]
    base = {"source_id": source["source_id"], "analysis": analysis(),
            "source_refs": [doc_uri], "study_relations": [study_id],
            "idempotency_key": "analysis-1"}
    failures = [
        ({**base, "source_refs": ["asset://sha256/" + "0" * 64]}, "RESOURCE_NOT_FOUND"),
        ({**base, "source_refs": [asset_uri]}, "INVALID_ARGUMENT"),
        ({**base, "study_relations": ["study:missing.md"]}, "RESOURCE_NOT_FOUND"),
        ({**base, "study_relations": ["study:../secret.md"]}, "INVALID_ARGUMENT"),
        ({**base, "analysis": {**analysis(), "extra": "forbidden"}}, "INVALID_ARGUMENT"),
        ({**base, "analysis": {**analysis(), "knowledge_points": []}}, "INVALID_ARGUMENT"),
    ]
    for invalid, code in failures:
        with pytest.raises(GatewayError) as error:
            gateway.save_wrong_answer_analysis(**invalid)
        assert error.value.code == code
    assert gateway.get_wrong_answer_bundle(source["source_id"])["analyses"] == []
    gateway.save_wrong_answer_analysis(**base)
    with pytest.raises(GatewayError) as error:
        gateway.save_wrong_answer_analysis(**{**base, "analysis": {**analysis(),
            "reasoning": "changed"}})
    assert error.value.code == "CONFLICT"
    with pytest.raises(GatewayError) as error:
        gateway.update_wrong_answer_analysis(**{**base, "idempotency_key": "analysis-2",
            "expected_version": 0})
    assert error.value.code == "CONFLICT"
    assert len(gateway.get_wrong_answer_bundle(source["source_id"])["analyses"]) == 1


def test_asset_source_and_safe_bounded_search(tmp_path: Path) -> None:
    gateway, _, asset_uri, _ = gateway_with_sources(tmp_path)
    source = gateway.register_wrong_answer_source(asset_uri, "Invented?", "Wrong.")["source"]
    assert source["page_number"] is None
    assert source["text_origin"] == "asset_only"
    assert gateway.search_wrong_answers("Invented", limit=1)["results"][0]["source_id"] == source["source_id"]
    with pytest.raises(GatewayError) as error:
        gateway.search_wrong_answers("Invented", limit=21)
    assert error.value.code == "INVALID_ARGUMENT"


def test_empty_wrong_answer_store_searches_as_empty_and_fetch_fails_safely(tmp_path: Path) -> None:
    gateway, _, _, _ = gateway_with_sources(tmp_path)
    assert gateway.search_wrong_answers("not present") == {
        "total": 0, "has_more": False, "results": [],
    }
    with pytest.raises(GatewayError) as error:
        gateway.get_wrong_answer_bundle("wrong-answer://sha256/" + "0" * 64)
    assert error.value.code == "RESOURCE_NOT_FOUND"


def test_public_source_and_analysis_redact_absolute_local_paths(tmp_path: Path) -> None:
    gateway, doc_uri, _, study_id = gateway_with_sources(tmp_path)
    source = gateway.register_wrong_answer_source(doc_uri, "Question C:/Users/private/exercise.png", "Wrong")['source']
    source_public = gateway.get_wrong_answer_bundle(source["source_id"])["source"]
    assert "C:/Users/private/exercise.png" not in source_public["question_text"]
    assert "exercise.png" not in source_public["question_text"]

    supplied = {**analysis(), "reasoning": "See /private/archive.tar.gz for the synthetic note."}
    saved = gateway.save_wrong_answer_analysis(source["source_id"], supplied, [doc_uri],
                                               [study_id], "path-redaction")['analysis']
    assert "/private/archive.tar.gz" not in saved["reasoning"]
    assert "archive.tar.gz" not in saved["reasoning"]


def test_study_relations_must_resolve_under_explicit_study_root(tmp_path: Path) -> None:
    gateway, doc_uri, _, _ = gateway_with_sources(tmp_path)
    source = gateway.register_wrong_answer_source(doc_uri, "Question", "Wrong", 1)["source"]
    for relation, expected in (("study:missing.md", "RESOURCE_NOT_FOUND"),
                               ("study:../outside.md", "INVALID_ARGUMENT"),
                               ("study:C:/private.md", "INVALID_ARGUMENT")):
        with pytest.raises(GatewayError) as error:
            gateway.save_wrong_answer_analysis(source["source_id"], analysis(), [doc_uri],
                                               [relation], "bad-relation")
        assert error.value.code == expected


def test_idempotent_analysis_retry_survives_removed_study_relation(tmp_path: Path) -> None:
    gateway, doc_uri, _, study_id = gateway_with_sources(tmp_path)
    source = gateway.register_wrong_answer_source(doc_uri, "Question", "Wrong", 1)["source"]
    args = {"source_id": source["source_id"], "analysis": analysis(),
            "source_refs": [doc_uri], "study_relations": [study_id],
            "idempotency_key": "repeat-after-move"}
    first = gateway.save_wrong_answer_analysis(**args)["analysis"]
    (tmp_path / "study" / "fractions.md").unlink()
    assert gateway.save_wrong_answer_analysis(**args)["analysis"] == first


def test_idempotent_analysis_retry_survives_missing_blob(tmp_path: Path) -> None:
    gateway, doc_uri, _, study_id = gateway_with_sources(tmp_path)
    source = gateway.register_wrong_answer_source(doc_uri, "Question", "Wrong", 1)["source"]
    args = {"source_id": source["source_id"], "analysis": analysis(),
            "source_refs": [doc_uri], "study_relations": [study_id],
            "idempotency_key": "repeat-after-asset-loss"}
    first = gateway.save_wrong_answer_analysis(**args)["analysis"]
    document = gateway.document_store.fetch_document(doc_uri)
    asset = gateway.document_store.fetch_asset_record(document.asset_uri)
    (gateway.document_store.asset_root / asset.sha256[:2] / asset.sha256).unlink()
    assert gateway.save_wrong_answer_analysis(**args)["analysis"] == first


def test_legacy_idempotency_digest_still_replays_after_provenance_upgrade(tmp_path: Path) -> None:
    gateway, doc_uri, _, study_id = gateway_with_sources(tmp_path)
    source = gateway.register_wrong_answer_source(doc_uri, "Question", "Wrong", 1)["source"]
    args = {"source_id": source["source_id"], "analysis": analysis(),
            "source_refs": [doc_uri], "study_relations": [study_id],
            "idempotency_key": "legacy-retry"}
    first = gateway.save_wrong_answer_analysis(**args)["analysis"]
    legacy_payload = json.dumps([source["source_id"], analysis(), [doc_uri], [study_id], 0],
                                ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    legacy_digest = hashlib.sha256(legacy_payload.encode()).hexdigest()
    with sqlite3.connect(gateway.document_store.database_path) as connection:
        connection.execute("UPDATE wrong_analysis_requests SET payload_sha256=? WHERE idempotency_key=?",
                           (legacy_digest, "legacy-retry"))
    assert gateway.save_wrong_answer_analysis(**args)["analysis"] == first


def test_whitespace_cannot_bypass_wrong_answer_text_limits(tmp_path: Path) -> None:
    gateway, doc_uri, _, _ = gateway_with_sources(tmp_path)
    with pytest.raises(GatewayError) as error:
        gateway.register_wrong_answer_source(doc_uri, " " * 10_001 + "Q", "A")
    assert error.value.code == "INVALID_ARGUMENT"
