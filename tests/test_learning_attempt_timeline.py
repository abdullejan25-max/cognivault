"""Synthetic dated evidence only; no progress classifier or personal data.

An Agent must interpret original attempts separately from analysis revisions,
third-party claims and explicit uncertainty. These tests characterize storage
and Gateway evidence, not whether a student has mastered a skill.
"""

import base64

import pytest

from cognivault.adapters.documents import SQLiteDocumentStore
from cognivault.adapters.history import SQLiteHistoryBackend
from cognivault.config import AppConfig
from cognivault.contracts import HistoryImportItem
from cognivault.gateway import Gateway


@pytest.fixture
def timeline(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    history = SQLiteHistoryBackend(tmp_path / "synthetic-history.db")
    history.register_source("synthetic-attempts", "Invented student attempts")
    history.register_source("synthetic-third-party", "Invented unrelated speaker")
    records = [
        # Deliberately imported out of order. Identical answers on different
        # days must remain distinct occurrences, even if payloads deduplicate.
        HistoryImportItem("friction-later", "friction", "user",
                          "friction Attempt: F=ma; answer=2; outcome=correct.",
                          "2026-01-05T09:00:00Z"),
        HistoryImportItem("friction-first", "friction", "user",
                          "friction Attempt: ignored friction; answer=3; outcome=wrong.",
                          "2026-01-01T09:00:00Z"),
        HistoryImportItem("friction-repeat", "friction", "user",
                          "friction Attempt: ignored friction; answer=3; outcome=wrong.",
                          "2026-01-03T09:00:00Z"),
        HistoryImportItem("fractions-only", "fractions", "user",
                          "fractions Attempt: 1/2+1/3=2/5; outcome=wrong.",
                          "2026-01-02T09:00:00Z"),
        HistoryImportItem("fractions-uncertain", "fractions", "user",
                          "fractions UNKNOWN: I have not retested; mastery is unknown.",
                          "2026-01-06T09:00:00Z"),
        HistoryImportItem("friction-assistant", "friction", "assistant",
                          "friction THIRD_PARTY: a tutor claims mastery, without a new attempt.",
                          "2026-01-07T09:00:00Z"),
    ]
    ids = history.import_items("synthetic-attempts", records)
    other = history.import_items("synthetic-third-party", [HistoryImportItem(
        "other-student", "friction", "user",
        "friction THIRD_PARTY: another student's answer was correct.",
        "2026-01-08T09:00:00Z",
    )])[0]
    gateway = Gateway(
        AppConfig("0.8.0"), None, history_backend=history,
        document_store=SQLiteDocumentStore(assets, tmp_path / "synthetic-documents.db"),
        capabilities=frozenset({"read", "write", "ingest"}),
        qmd_discoverable=lambda: False,
    )
    return gateway, dict(zip((r.source_item_id for r in records), ids)), other


def test_distinct_dated_attempts_keep_wrong_repetition_and_later_correct_evidence(timeline):
    gateway, ids, other = timeline
    first = gateway.search_history(
        "friction", source_id="synthetic-attempts", conversation_id="friction",
        limit=2, offset=0,
    )
    tail = gateway.search_history(
        "friction", source_id="synthetic-attempts", conversation_id="friction",
        limit=2, offset=2,
    )
    rows = first["results"] + tail["results"]
    assert first["total"] == tail["total"] == 4
    assert first["has_more"] is True and tail["has_more"] is False
    assert [r["item_id"] for r in rows] == [
        ids["friction-assistant"], ids["friction-later"],
        ids["friction-repeat"], ids["friction-first"],
    ]
    assert other not in {r["item_id"] for r in rows}
    fetched = [gateway.fetch_history_item(row["item_id"])["item"] for row in rows]
    assert [r["created_at"] for r in fetched] == [
        "2026-01-07T09:00:00Z", "2026-01-05T09:00:00Z",
        "2026-01-03T09:00:00Z", "2026-01-01T09:00:00Z",
    ]
    assert len({r["item_id"] for r in fetched}) == 4
    assert fetched[2]["content_sha256"] == fetched[3]["content_sha256"]
    assert fetched[2]["source_item_id"] != fetched[3]["source_item_id"]
    assert "outcome=correct" in fetched[1]["content"]
    assert all("outcome=wrong" in r["content"] for r in fetched[2:])
    assert fetched[0]["role"] == "assistant"
    assert "THIRD_PARTY" in fetched[0]["content"]
    for row in fetched:
        assert row["source_id"] == "synthetic-attempts"
        assert row["conversation_id"] == "friction"
        assert row["write_provenance"]["original_created_at"] == row["created_at"]
        assert row["write_provenance"]["imported_at"] == row["imported_at"]


def test_not_retested_and_third_party_claims_remain_historical_untrusted_data(timeline):
    gateway, ids, other = timeline
    page = gateway.search_history(
        "fractions", source_id="synthetic-attempts", conversation_id="fractions", limit=5,
    )
    assert page["total"] == 2 and page["has_more"] is False
    assert [r["item_id"] for r in page["results"]] == [
        ids["fractions-uncertain"], ids["fractions-only"],
    ]
    uncertainty = gateway.fetch_history_item(ids["fractions-uncertain"])["item"]
    assert uncertainty["content"] == (
        "fractions UNKNOWN: I have not retested; mastery is unknown."
    )
    bundle = gateway.retrieve_evidence({"history": "fractions"}, limit=5)
    assert bundle["content_trust"] == "untrusted_data"
    assert bundle["answer_generated"] is False
    assert {e["citation"] for e in bundle["evidence"]} == {
        ids["fractions-uncertain"], ids["fractions-only"],
    }
    assert all(e["temporal_scope"] == "historical" for e in bundle["evidence"])
    assert "UNKNOWN" in bundle["evidence"][0]["excerpt"]
    third_party = gateway.fetch_history_item(other)["item"]
    assert third_party["source_id"] == "synthetic-third-party"
    assert "THIRD_PARTY" in third_party["content"]
    # Role=user alone does not identify the subject of a quoted claim.
    assert third_party["role"] == "user"


def test_agent_analysis_revisions_do_not_create_or_replace_student_attempts(timeline):
    gateway, ids, _other = timeline
    before = gateway.search_history("fractions", source_id="synthetic-attempts", limit=5)
    original = gateway.fetch_history_item(ids["fractions-only"])
    doc = gateway.ingest_documents([{
        "title": "Synthetic fractions exercise", "media_type": "text/plain",
        "content_base64": base64.b64encode(b"1/2 + 1/3 = ?").decode(),
    }])["documents"][0]
    source = gateway.register_wrong_answer_source(
        doc["document_uri"], "1/2 + 1/3 = ?", "2/5", page_number=1,
    )["source"]
    analysis = {
        "error_type": "denominator", "knowledge_points": ["fraction addition"],
        "reasoning": "Synthetic answer added denominators; no retest observed.",
        "correct_solution": "3/6 + 2/6 = 5/6.",
        "review_advice": "A separate dated student attempt is still needed.",
    }
    first = gateway.save_wrong_answer_analysis(
        source["source_id"], analysis, [doc["document_uri"]], [], "timeline-analysis-v1",
    )["analysis"]
    second = gateway.update_wrong_answer_analysis(
        source["source_id"], {**analysis, "review_advice": "Ask for a new attempt tomorrow."},
        [doc["document_uri"]], [], "timeline-analysis-v2", expected_version=1,
    )["analysis"]
    bundle = gateway.get_wrong_answer_bundle(source["source_id"])
    assert [a["version"] for a in bundle["analyses"]] == [2, 1]
    assert second["supersedes_analysis_id"] == first["analysis_id"]
    assert all(a["generated_by_agent"] is True for a in bundle["analyses"])
    assert all(a["write_provenance"]["data_origin"] == "agent_generated"
               for a in bundle["analyses"])
    assert bundle["source"] == source and source["student_answer"] == "2/5"
    assert gateway.search_history("fractions", source_id="synthetic-attempts", limit=5) == before
    assert gateway.fetch_history_item(ids["fractions-only"]) == original
