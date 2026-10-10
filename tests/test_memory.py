"""Synthetic, isolated Memory acceptance cases. Never touches private runtime."""
import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from cognivault.adapters.memory import SQLiteMemoryStore
from cognivault.config import AppConfig
from cognivault.contracts import GatewayError
from cognivault.gateway import Gateway
from cognivault.transports.mcp_stdio import create_mcp_server

REF = "history:" + "a" * 64


def gateway(tmp_path, caps=frozenset({"read", "write"})):
    return Gateway(AppConfig("0.8.0"), None,
                   memory_store=SQLiteMemoryStore(tmp_path / "memory.db"),
                   capabilities=caps)


def test_versioned_lifecycle_reopen_and_evidence_status(tmp_path):
    g = gateway(tmp_path)
    first = g.memory_store.create("subject", "choice", "物化生", [REF], "create-1")
    mid = first["memory"]["memory_id"]
    assert first["memory"]["version"] == 1
    assert first["memory"]["evidence_verified"] is False
    assert g.memory_store.create("subject", "choice", "物化生", [REF], "create-1")["reused"] is True
    assert g.memory_store.search("物化生")["total"] == 1
    next_fact = g.memory_store.revise(mid, "物化地", [REF], 1, "revise-1")
    assert next_fact["memory"]["version"] == 2
    assert next_fact["memory"]["write_provenance"]["supersedes_provenance_id"]
    assert g.memory_store.search("物化生")["total"] == 0
    assert g.memory_store.search("物化地")["total"] == 1
    with pytest.raises(GatewayError) as conflict:
        g.memory_store.revise(mid, "obsolete", [REF], 1, "stale")
    assert conflict.value.code == "CONFLICT"
    retired = g.memory_store.revise(mid, "retired after correction", [REF], 2, "retire-1", retire=True)
    assert retired["memory"]["state"] == "retired"
    assert g.memory_store.search("物化地")["total"] == 0
    reopened = SQLiteMemoryStore(tmp_path / "memory.db")
    assert reopened.fetch(mid)["memory"]["version"] == 3
    assert reopened.revise(mid, "again", [REF], 2, "retire-1", retire=True)["reused"] is True


def test_invalid_claims_and_idempotency_conflict(tmp_path):
    s = gateway(tmp_path).memory_store
    with pytest.raises(GatewayError):
        s.create("x", "y", "z", ["C:/private/data"], "request-1")
    assert not (tmp_path / "memory.db").exists()
    s.create("x", "y", "z", [REF], "request-1")
    with pytest.raises(GatewayError) as conflict:
        s.create("x", "y", "different", [REF], "request-1")
    assert conflict.value.code == "CONFLICT"


def test_mcp_permissions_and_rejection(tmp_path):
    g = gateway(tmp_path, frozenset({"read"}))
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert "search_memory" in names
            assert "create_memory" not in names
            result = await client.call_tool("create_memory", {
                "subject": "synthetic", "predicate": "test", "value": "a",
                "source_refs": [REF], "idempotency_key": "attempt-1"})
            assert result.structuredContent["error"]["code"] == "PERMISSION_DENIED"
            bad = await client.call_tool("fetch_memory", {"memory_id": "invalid"})
            assert bad.isError
            assert bad.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
    anyio.run(check)
    assert not (tmp_path / "memory.db").exists()


def test_mcp_round_trip(tmp_path):
    g = gateway(tmp_path)
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            args = {"subject": "math", "predicate": "topic", "value": "gradient",
                    "source_refs": [REF], "idempotency_key": "create-key"}
            created = (await client.call_tool("create_memory", args)).structuredContent
            assert created["ok"] and not created["reused"]
            mid = created["memory"]["memory_id"]
            revised = (await client.call_tool("revise_memory", {
                "memory_id": mid, "value": "gradients", "source_refs": [REF],
                "expected_version": 1, "idempotency_key": "revise-key"})).structuredContent
            assert revised["memory"]["version"] == 2
            found = (await client.call_tool("search_memory", {"query": "gradients"})).structuredContent
            assert found["total"] == 1
            assert found["memories"][0]["evidence_verified"] is False
    anyio.run(check)
