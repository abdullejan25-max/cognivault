"""Candidate review acceptance uses invented History only."""
import pytest
from test_evidence_bundle import scenario
from cognivault.contracts import GatewayError


def propose(g, ref, **changes):
    args = dict(source_ref=ref, quote="engineering is my goal", subject="self", predicate="goal",
                value="engineering", classification="fact_update", confidence=0.8,
                basis="Explicit user goal in original History")
    args.update(changes)
    return g.propose_memory_candidate(**args)["candidate"]


def test_pending_review_commit_reopen_and_duplicate(tmp_path):
    g, ref, *_ = scenario(tmp_path)
    c = propose(g, ref, predicate="long_term_goal")
    assert c["state"] == "pending" and c["source_time"] == "2026-01-01T00:00:00Z"
    assert propose(g, ref, predicate="long_term_goal")["candidate_id"] == c["candidate_id"]
    assert g.search_memory("long_term_goal")["total"] == 0
    with pytest.raises(GatewayError):
        g.commit_memory_candidate(c["candidate_id"])
    with pytest.raises(GatewayError) as e:
        g.review_memory_candidate(c["candidate_id"], "approve", "Reviewed original")
    assert e.value.code == "PERMISSION_DENIED"
    g.capabilities |= {"admin"}
    g.review_memory_candidate(c["candidate_id"], "approve", "Explicitly confirmed still current")
    first = g.commit_memory_candidate(c["candidate_id"])
    assert first["memory"]["value"] == "engineering"
    assert g.commit_memory_candidate(c["candidate_id"])["reused"]

    assert g.list_memory_candidates()["candidates"][0]["state"] == "approved"
    from cognivault.adapters.memory import SQLiteMemoryStore
    g.memory_store = SQLiteMemoryStore(g.memory_store.database_path)
    assert g.list_memory_candidates()["candidates"][0]["review"]["note"] == "Explicitly confirmed still current"
    assert g.commit_memory_candidate(c["candidate_id"])["reused"]


def test_semantic_paraphrase_requires_review_and_preserves_canonical_fact(tmp_path):
    g, ref, *_, memory = scenario(tmp_path)
    mid = memory["memory"]["memory_id"]
    c = propose(g, ref, predicate="职业规划", value="希望学习工程专业",
                semantic_links=[dict(memory_id=mid, expected_version=1,
                                     relation="same_as", reason="工程专业目标的不同措辞")])
    assert c["relations"][0]["snapshot"]["value"] == "engineering with physics"
    assert g.search_memory("职业规划")["total"] == 0
    g.capabilities |= {"admin"}
    with pytest.raises(GatewayError):
        g.review_memory_candidate(c["candidate_id"], "approve", "Reviewed")
    g.review_memory_candidate(c["candidate_id"], "approve", "Confirmed equivalent", "confirmed_update")
    receipt = g.commit_memory_candidate(c["candidate_id"])
    assert receipt["memory"]["memory_id"] == mid
    assert receipt["memory"]["value"] == "engineering with physics"
    assert g.commit_memory_candidate(c["candidate_id"])["reused"]
    assert propose(g,ref,predicate="职业规划",value="希望学习工程专业",semantic_links=[dict(memory_id=mid,expected_version=1,relation="same_as",reason="工程专业目标的不同措辞")])["candidate_id"] == c["candidate_id"]


def test_supersedes_rejects_undated_or_nonlater_change(tmp_path):
    g, ref, *_, memory = scenario(tmp_path)
    c = propose(g, ref, semantic_links=[dict(memory_id=memory["memory"]["memory_id"],
                expected_version=1, relation="supersedes", reason="声称目标变化")])
    g.capabilities |= {"admin"}
    with pytest.raises(GatewayError) as e:
        g.review_memory_candidate(c["candidate_id"], "approve", "Confirmed", "confirmed_update")
    assert e.value.code == "CONFLICT"


def test_future_history_remains_pending_instead_of_becoming_current_fact(tmp_path):
    from cognivault.contracts import HistoryImportItem
    g, *_ = scenario(tmp_path)
    future = g.history_backend.import_items("synthetic-decision", [HistoryImportItem(
        "future", "learning", "user", "My study goal is reviewing friction.",
        "2999-01-01T00:00:00Z")])[0]
    c = propose(g, future, quote="My study goal is reviewing friction.",
                predicate="study_goal", value="reviewing friction")
    g.capabilities |= {"admin"}
    with pytest.raises(GatewayError) as e:
        g.review_memory_candidate(c["candidate_id"], "approve", "Confirmed current learning goal")
    assert e.value.code == "CONFLICT"
    assert g.fetch_memory_candidate(c["candidate_id"])["candidate"]["state"] == "pending"
    assert g.search_memory("study_goal")["total"] == 0
    g.review_memory_candidate(c["candidate_id"], "reject", "Source date is in the future")


def test_commit_rechecks_source_time_after_clock_correction(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import cognivault.memory_candidates as candidates
    from cognivault.contracts import HistoryImportItem
    g, *_ = scenario(tmp_path)
    future = g.history_backend.import_items("synthetic-decision", [HistoryImportItem(
        "clock-skew", "learning", "user", "My study goal is reviewing friction.",
        "2999-01-01T00:00:00Z")])[0]
    c = propose(g, future, quote="My study goal is reviewing friction.",
                predicate="study_goal", value="reviewing friction")
    g.capabilities |= {"admin"}
    class FutureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(3000, 1, 1, tzinfo=timezone.utc)
    with monkeypatch.context() as clock:
        clock.setattr(candidates, "datetime", FutureClock)
        g.review_memory_candidate(c["candidate_id"], "approve", "Reviewed with an incorrect system clock")
    with pytest.raises(GatewayError) as e:
        g.commit_memory_candidate(c["candidate_id"])
    assert e.value.code == "CONFLICT"
    assert g.search_memory("study_goal")["total"] == 0


def test_source_integrity_feedback_and_historical_guard(tmp_path):
    g, ref, *_, memory = scenario(tmp_path)
    for changes in ({"quote":"fabricated quote"}, {"source_ref":memory["memory"]["memory_id"]}):
        with pytest.raises(GatewayError): propose(g, ref, **changes)
    c = propose(g, ref, classification="historical_statement")
    g.capabilities |= {"admin"}
    with pytest.raises(GatewayError):
        g.review_memory_candidate(c["candidate_id"], "approve", "Make current")
    g.review_memory_candidate(c["candidate_id"], "reject", "Past statement, not current")
    with pytest.raises(GatewayError): g.commit_memory_candidate(c["candidate_id"])


def test_conflict_requires_confirmation_and_optimistic_version(tmp_path):
    g, ref, *_, memory = scenario(tmp_path)
    mid = memory["memory"]["memory_id"]
    c = propose(g, ref)
    assert c["relations"][0]["kind"] == "possible_conflict"
    g.capabilities |= {"admin"}
    with pytest.raises(GatewayError): g.review_memory_candidate(c["candidate_id"], "approve", "Reviewed")
    g.review_memory_candidate(c["candidate_id"], "approve", "Confirmed changed goal", resolution="confirmed_update")
    assert g.fetch_memory(mid)["memory"]["version"] == 1
    g.revise_memory(mid, "medicine", [ref], 1, "concurrent-update",
                    target_guard=g.fetch_memory(mid)["memory"]["target_guard"])
    with pytest.raises(GatewayError) as e: g.commit_memory_candidate(c["candidate_id"])
    assert e.value.code == "CONFLICT"
    assert g.fetch_memory(mid)["memory"]["value"] == "medicine"


def test_native_mcp_candidate_permissions_and_review(tmp_path):
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session
    from cognivault.transports.mcp_stdio import create_mcp_server
    g, ref, *_ = scenario(tmp_path)
    async def run():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert "propose_memory_candidate" in names and "review_memory_candidate" not in names
            r = (await client.call_tool("propose_memory_candidate", dict(source_ref=ref,quote="engineering is my goal",subject="self",predicate="goal2",value="engineering",classification="fact_update",confidence=0.8,basis="Explicit original quote"))).structuredContent
            assert r["ok"] and r["candidate"]["state"] == "pending"
            cid = r["candidate"]["candidate_id"]
            denied = await client.call_tool("review_memory_candidate",dict(candidate_id=cid,decision="approve",note="Reviewed"))
            assert denied.isError
            g.capabilities |= {"admin"}
            approved = (await client.call_tool("review_memory_candidate",dict(candidate_id=cid,decision="approve",note="Confirmed still current"))).structuredContent
            assert approved["ok"]
            committed = (await client.call_tool("commit_memory_candidate",dict(candidate_id=cid))).structuredContent
            assert committed["ok"] and committed["memory"]["version"] == 1
    anyio.run(run)


def test_find_is_bounded_and_does_not_write_candidate_or_memory(tmp_path):
    g, ref, *_ = scenario(tmp_path)
    before = g.memory_store.database_path.read_bytes()
    result = g.find_memory_candidates("物理",limit=1)
    assert result["suggestions"][0]["source_ref"] == ref
    assert not result["persisted"] and result["suggestions"][0]["confidence"] == "unknown"
    assert g.list_memory_candidates()["total"] == 0
    assert before == g.memory_store.database_path.read_bytes()
    g.capabilities = frozenset({"write"})
    with pytest.raises(GatewayError): g.find_memory_candidates("goal")


def test_chinese_explicit_learning_decision_is_found_without_persisting(tmp_path):
    from cognivault.contracts import HistoryImportItem
    g, *_ = scenario(tmp_path)
    recent = g.history_backend.import_items("synthetic-decision", [HistoryImportItem(
        "recent-learning", "learning", "user", "现在正式决定先复习受力分析。",
        "2026-10-09T00:00:00Z")])[0]
    result = g.find_memory_candidates("受力分析")
    assert result["suggestions"] and result["suggestions"][0]["source_ref"] == recent
    assert "决定" in result["suggestions"][0]["cues"]
    assert result["suggestions"][0]["confidence"] == "unknown"
    assert not result["persisted"] and g.list_memory_candidates()["total"] == 0


def test_meaningful_punctuation_and_duplicate_promotion_replay(tmp_path):
    g, ref, *_ = scenario(tmp_path)
    g.create_memory("self","language","C++",[ref],"language")
    c = propose(g,ref,predicate="language",value="C")
    assert c["relations"][0]["kind"] == "possible_conflict"
    dup = propose(g,ref,value="engineering with physics")
    g.capabilities |= {"admin"}
    g.review_memory_candidate(dup["candidate_id"],"approve","Reviewed original and confirmed current")
    first = g.commit_memory_candidate(dup["candidate_id"])
    mid = first["memory"]["memory_id"]
    g.revise_memory(mid,"medicine",[ref],first["memory"]["version"],"later",
                    target_guard=g.fetch_memory(mid)["memory"]["target_guard"])
    replay = g.commit_memory_candidate(dup["candidate_id"])
    assert replay["reused"] and replay["memory"]["version"] == first["memory"]["version"]
    assert not replay["memory"]["is_current"]


def test_semantic_learning_update_packet_and_consideration_guard(tmp_path):
    from cognivault.contracts import HistoryImportItem
    g, ref, doc, wrong, memory = scenario(tmp_path)
    newer = g.history_backend.import_items("synthetic-decision",[HistoryImportItem("two","recent","user","现在正式决定先复习受力分析。","2026-10-09T00:00:00Z")])[0]
    c = propose(g,newer,quote="现在正式决定先复习受力分析。",value="先复习受力分析",claim_mode="decided",
                evidence_refs=[doc["document_uri"],wrong["source_id"]],semantic_links=[dict(memory_id=memory["memory"]["memory_id"],expected_version=1,relation="supersedes",reason="较新的明确学习目标替换旧目标")])
    packet = g.fetch_memory_candidate(c["candidate_id"])
    assert not packet["competing_memories"][0]["changed"]
    assert packet["candidate"]["claim_mode"] == "decided"
    g.capabilities |= {"admin"}
    g.review_memory_candidate(c["candidate_id"],"approve","核对新旧来源确认学习目标变化","confirmed_update")
    updated = g.commit_memory_candidate(c["candidate_id"])["memory"]
    assert updated["version"] == 2 and doc["document_uri"] in updated["source_refs"]
    assert g.fetch_memory_candidate(c["candidate_id"])["competing_memories"][0]["changed"]
    evidence = g.retrieve_evidence({"memory":"受力分析","history":"chose","study":"physics"})
    assert evidence["status"] == "COMPLETE"
    assert any(e["citation"] == updated["memory_id"]+"#version=2" for e in evidence["evidence"])
    considered = propose(g,newer,quote="现在正式决定先复习受力分析。",predicate="alternative",claim_mode="consideration")
    with pytest.raises(GatewayError): g.review_memory_candidate(considered["candidate_id"],"approve","Cannot infer decision")


def test_unicode_slot_and_semantic_argument_validation(tmp_path):
    g, ref, *_ = scenario(tmp_path)
    g.create_memory("Ａlice","goal","engineering",[ref],"unicode")
    c = propose(g,ref,subject="Alice")
    assert c["relations"][0]["kind"] == "duplicate"
    for links in ([],[dict(memory_id="bad",expected_version=True,relation="same_as",reason="x")],
                  [dict(memory_id="bad",expected_version=1,relation="unknown",reason="x")]):
        with pytest.raises(GatewayError): propose(g,ref,semantic_links=links)
    with pytest.raises(GatewayError): propose(g,ref,evidence_refs=["document://fabricated"])



def test_contradiction_pending_and_equivalent_source_capacity(tmp_path):
    from cognivault.contracts import HistoryImportItem
    g, ref, *_ = scenario(tmp_path)
    refs = g.history_backend.import_items("synthetic-decision",[
        HistoryImportItem(str(i),"source","user","engineering is my goal", "2026-01-01T00:00:00Z") for i in range(17)])
    target = g.create_memory("self","bounded","engineering",list(refs[:16]),"bounded",epistemic_status="verified",verification_note="Synthetic originals")
    link = dict(memory_id=target["memory"]["memory_id"],expected_version=1,relation="same_as",reason="Equivalent goal statement")
    c = propose(g,refs[16],predicate="bounded",semantic_links=[link])
    g.capabilities |= {"admin"}
    with pytest.raises(GatewayError) as e: g.review_memory_candidate(c["candidate_id"],"approve","Confirmed","confirmed_update")
    assert e.value.code == "CONFLICT" and g.fetch_memory_candidate(c["candidate_id"])["candidate"]["state"] == "pending"
    link["relation"] = "contradicts"
    conflict = propose(g,ref,predicate="bounded",value="medicine",semantic_links=[link])
    assert conflict["relations"][-1]["reason"] == "Equivalent goal statement"
    with pytest.raises(GatewayError): g.commit_memory_candidate(conflict["candidate_id"])
