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
    g.revise_memory(mid, "medicine", [ref], 1, "concurrent-update")
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
    g.revise_memory(mid,"medicine",[ref],first["memory"]["version"],"later")
    replay = g.commit_memory_candidate(dup["candidate_id"])
    assert replay["reused"] and replay["memory"]["version"] == first["memory"]["version"]
    assert not replay["memory"]["is_current"]
