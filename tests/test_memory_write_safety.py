"""Synthetic regressions for the Desktop wrong-target incident."""
import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from cognivault.contracts import GatewayError
from cognivault.transports.mcp_stdio import create_mcp_server
from test_memory import gateway, REF


def test_same_version_wrong_target_is_rejected_without_consuming_key(tmp_path):
    g = gateway(tmp_path)
    goal = g.create_memory("self", "goal", "engineering", [REF], "goal")["memory"]
    priority = g.create_memory("self", "学习重点", "受力分析", [REF], "priority")["memory"]
    assert "target_guard" in goal
    args = dict(value="new", source_refs=[REF], expected_version=1,
                idempotency_key="desktop-revision", target_guard=goal["target_guard"])
    with pytest.raises(GatewayError) as error:
        g.revise_memory(priority["memory_id"], **args)
    assert error.value.code == "CONFLICT"
    assert g.memory_versions(priority["memory_id"])["total"] == 1
    with pytest.raises(GatewayError) as missing:
        g.fetch_memory_request("desktop-revision")
    assert missing.value.code == "RESOURCE_NOT_FOUND"
    revised = g.revise_memory(goal["memory_id"], **args)
    assert revised["memory"]["version"] == 2


def test_missing_guard_mcp_cannot_repeat_original_accident(tmp_path):
    g = gateway(tmp_path)
    memory = g.create_memory("self", "goal", "engineering", [REF], "goal")["memory"]
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            result = await client.call_tool("revise_memory", {
                "memory_id": memory["memory_id"], "expected_version": 1,
                "value": "wrong", "source_refs": [REF], "idempotency_key": "original-shape"})
            assert result.isError
            assert result.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
    anyio.run(check)
    assert g.memory_versions(memory["memory_id"])["total"] == 1


def test_guard_checks_slot_and_payload_for_retirement(tmp_path):
    g = gateway(tmp_path)
    memory = g.create_memory("self", "goal", "engineering", [REF], "goal")["memory"]
    assert "target_guard" in memory
    for change in ({"predicate": "学习重点"}, {"record_sha256": "0" * 64}):
        guard = {**memory["target_guard"], **change}
        with pytest.raises(GatewayError) as error:
            g.retire_memory(memory["memory_id"], "incorrect", [REF], 1, "retire", target_guard=guard)
        assert error.value.code == "CONFLICT"
    assert g.fetch_memory(memory["memory_id"])["memory"]["state"] == "active"


def test_stale_guard_conflicts_but_exact_replay_returns_historical_receipt(tmp_path):
    g = gateway(tmp_path)
    original = g.create_memory("self", "goal", "engineering", [REF], "goal")["memory"]
    assert "target_guard" in original
    args = dict(memory_id=original["memory_id"], value="physics", source_refs=[REF],
                expected_version=1, idempotency_key="revision", target_guard=original["target_guard"])
    second = g.revise_memory(**args)["memory"]
    g.revise_memory(second["memory_id"], "mathematics", [REF], 2, "later", target_guard=second["target_guard"])
    replay = g.revise_memory(**args)
    assert replay["reused"] and replay["memory"]["version"] == 2
    assert replay["memory"]["is_current"] is False
    with pytest.raises(GatewayError) as error:
        g.revise_memory(**{**args, "idempotency_key": "stale"})
    assert error.value.code == "CONFLICT"
    receipt = g.fetch_memory_request("revision")
    assert receipt["request"]["memory_id"] == original["memory_id"]
    assert receipt["request"]["version"] == 2
    assert receipt["memory"]["is_current"] is False


def test_request_query_is_read_only_and_requires_read_capability(tmp_path, monkeypatch):
    g = gateway(tmp_path)
    created = g.create_memory("self", "goal", "physics", [REF], "receipt")["memory"]
    # No source remains available; the successful request receipt is still inspectable.
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    connect = g.memory_store._connect
    def read_connect(*, write=False):
        assert write is False, "request inspection must not open a writer"
        return connect(write=False)
    monkeypatch.setattr(g.memory_store, "_connect", read_connect)
    receipt = g.fetch_memory_request("receipt")
    assert receipt["memory"]["memory_id"] == created["memory_id"]
    assert receipt["memory"]["sources_resolved"] is False
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before
    g.capabilities = frozenset({"write"})
    with pytest.raises(GatewayError) as denied:
        g.fetch_memory_request("receipt")
    assert denied.value.code == "PERMISSION_DENIED"


def test_unknown_request_and_bad_guard_do_not_initialize_database(tmp_path):
    g = gateway(tmp_path)
    with pytest.raises(GatewayError) as error:
        g.fetch_memory_request("unknown")
    assert error.value.code == "RESOURCE_NOT_FOUND"
    with pytest.raises(GatewayError) as invalid:
        g.revise_memory("memory:" + "a" * 32, "value", [REF], 1, "bad", target_guard={})
    assert invalid.value.code == "INVALID_ARGUMENT"
    assert not list(tmp_path.iterdir())


def test_mcp_discovery_and_request_query_match_permissions(tmp_path):
    g = gateway(tmp_path)
    memory = g.create_memory("self", "goal", "physics", [REF], "known")["memory"]
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert "target_guard" in tools["revise_memory"].inputSchema["required"]
            result = await client.call_tool("fetch_memory_request", {"idempotency_key": "known"})
            assert result.structuredContent["ok"]
            assert result.structuredContent["memory"]["memory_id"] == memory["memory_id"]
            bad = await client.call_tool("fetch_memory_request", {"idempotency_key": "../unknown"})
            assert bad.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
        g.capabilities = frozenset({"write"})
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            assert "fetch_memory_request" not in {tool.name for tool in (await client.list_tools()).tools}
            denied = await client.call_tool("fetch_memory_request", {"idempotency_key": "known"})
            assert denied.structuredContent["error"]["code"] == "PERMISSION_DENIED"
    anyio.run(check)


def test_consumed_key_cannot_move_to_other_valid_target(tmp_path):
    g = gateway(tmp_path)
    first = g.create_memory("self", "goal", "engineering", [REF], "one")["memory"]
    other = g.create_memory("self", "学习重点", "受力分析", [REF], "two")["memory"]
    g.revise_memory(first["memory_id"], "physics", [REF], 1, "consumed", target_guard=first["target_guard"])
    with pytest.raises(GatewayError) as conflict:
        g.revise_memory(other["memory_id"], "friction", [REF], 1, "consumed", target_guard=other["target_guard"])
    assert conflict.value.code == "CONFLICT"
    assert g.fetch_memory_request("consumed")["request"]["memory_id"] == first["memory_id"]
    assert g.memory_versions(other["memory_id"])["total"] == 1


def test_concurrent_guarded_revisions_only_one_can_commit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    g = gateway(tmp_path)
    original = g.create_memory("self", "goal", "engineering", [REF], "original")["memory"]
    barrier = Barrier(2)
    def update(index):
        client = gateway(tmp_path)
        barrier.wait(timeout=10)
        try:
            return client.revise_memory(original["memory_id"], f"goal-{index}", [REF], 1,
                                        f"concurrent-{index}", target_guard=original["target_guard"])
        except GatewayError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(update, (1, 2)))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert results.count("CONFLICT") == 1
    assert g.memory_versions(original["memory_id"])["total"] == 2


def test_verified_receipt_survives_source_unavailability(tmp_path, monkeypatch):
    from test_evidence_bundle import scenario
    g, ref, *_ = scenario(tmp_path)
    g.create_memory("self", "reviewed", "physics", [ref], "verified-request",
                    epistemic_status="verified", verification_note="Synthetic reviewed assertion")
    def unavailable(*args, **kwargs):
        raise GatewayError("RESOURCE_NOT_FOUND", "Synthetic source unavailable")
    monkeypatch.setattr(g, "fetch_canonical_message", unavailable)
    monkeypatch.setattr(g, "fetch_history_item", unavailable)
    receipt = g.fetch_memory_request("verified-request")
    assert receipt["memory"]["epistemic_status"] == "verified"
    assert receipt["memory"]["effective_epistemic_status"] == "unverified"
    assert receipt["memory"]["sources_resolved"] is False
