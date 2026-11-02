"""Synthetic packet and companion protocol contracts, never native Host evidence."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from cognivault.recall import RecallCore, RecallPlan, RecallRequest


def source(index=0, text="🧠", **extra):
    return {"domain": "memory", "citation": f"memory:合成{index}#version=1",
            "excerpt": text * 1000, "truncated": False,
            "source_refs": ["history:合成"], "version": 1,
            "uncertainty": 0.71, "currency_verified": False,
            "temporal_scope": "historical", "role": "operator", **extra}


def raw(records=(), *, status=None, domains=None):
    return {"status": status or ("COMPLETE" if records else "NO_EVIDENCE"),
            "domains": domains if domains is not None else {"memory": {"status": "ok"}},
            "evidence": list(records)}


def recall(response, **options):
    calls = []
    async def retrieve(queries, limit):
        calls.append((queries, limit))
        return deepcopy(response)
    result = asyncio.run(RecallCore(retrieve, **options).recall(
        RecallRequest("synthetic", "turn", "我的目标"),
        plan=RecallPlan({"memory": "目标"}, ({"memory": "goal"}, {"memory": "学习"}))))
    return result, calls


def assert_bounds(context, budget):
    assert len(context) <= 8000
    assert len(context.encode("utf-8")) <= budget
    return json.loads(context)


@pytest.mark.parametrize("text", ["x", "学", "🧠"])
def test_byte_budget_omits_whole_entries_and_preserves_all_selected_metadata(text):
    records = [source(index, text) for index in range(6)]
    original = deepcopy(records)
    result, calls = recall(raw(records), max_packet_utf8_bytes=9600)
    packet = assert_bounds(result.injection, 9600)
    selected = packet["untrusted_data"]["evidence"]
    assert selected and len(selected) <= 6
    assert selected == records[:len(selected)]
    assert records == original
    assert packet["status"] == "PARTIAL"
    assert packet["omitted_entries"] == 6 - len(selected)
    assert packet["untrusted_data"]["domains"] == {"memory": {"status": "ok"}}
    assert calls == [({"memory": "目标"}, 3)]


def test_exact_serialized_byte_boundary_includes_escaped_keys_and_multibyte_metadata():
    record = source(excerpt='<>&"\n\\\t🧠学', **{"<key&>": {"citation": "来源🧠", "uncertainty": "未核实"}})
    response = raw([record])
    baseline, _ = recall(response)
    exact = len(baseline.injection.encode("utf-8"))
    fits, _ = recall(response, max_packet_utf8_bytes=exact)
    assert fits.injection == baseline.injection
    assert json.loads(fits.injection)["untrusted_data"]["evidence"] == [record]
    assert "<" not in fits.injection and "&" not in fits.injection
    # The same complete packet is B+1 bytes when the Host gives one byte less.
    omitted, calls = recall(response, max_packet_utf8_bytes=exact - 1)
    packet = assert_bounds(omitted.injection, exact - 1)
    assert packet["status"] == "PARTIAL"
    assert packet["omitted_entries"] == 1
    assert packet["untrusted_data"]["evidence"] == []
    assert len(calls) == 1  # byte omission must not become NO_EVIDENCE/retry


def test_byte_oversize_entry_does_not_block_a_later_smaller_entry():
    large = source(excerpt="🧠" * 1000, source_refs=["🧠" * 1000])
    small = source(1, excerpt="small")
    result, calls = recall(raw([large, small]), max_packet_utf8_bytes=5000)
    packet = assert_bounds(result.injection, 5000)
    assert packet["untrusted_data"]["evidence"] == [small]
    assert packet["status"] == "PARTIAL" and packet["omitted_entries"] == 1
    assert len(calls) == 1


@pytest.mark.parametrize("budget", [True, False, 9600.0, "9600", 0, -1, 32001, 1])
def test_invalid_byte_budget_is_rejected_during_construction_without_retrieval(budget):
    async def retrieve(*_):
        pytest.fail("invalid trusted budget cannot retrieve")
    with pytest.raises(ValueError):
        RecallCore(retrieve, max_packet_utf8_bytes=budget)


def test_smallest_budget_is_derived_from_all_fixed_empty_states():
    # These are observable states, not a copy of the implementation's envelope.
    states = ["COMPLETE", "PARTIAL", "NO_EVIDENCE", "UNAVAILABLE", "NOT_NEEDED",
              "INVALID_PLAN", "ALREADY_HANDLED"]
    packets = [recall(raw(status=state, domains={}))[0].injection
               for state in ["COMPLETE", "PARTIAL", "NO_EVIDENCE", "UNAVAILABLE", "NOT_NEEDED"]]
    async def scenario():
        async def retrieve(*_):
            return raw()
        core = RecallCore(retrieve)
        request = RecallRequest("empty", "one", "2+2")
        packets.append((await core.recall(request)).injection)
        packets.append((await core.recall(request)).injection)
        packets.append((await core.recall(None)).injection)
    asyncio.run(scenario())
    assert set(json.loads(p)["status"] for p in packets) == set(states)
    minimum = max(len(p.encode("utf-8")) for p in packets)
    async def no_retrieve(*_):
        pytest.fail("construction must not retrieve")
    RecallCore(no_retrieve, max_packet_utf8_bytes=minimum)
    with pytest.raises(ValueError):
        RecallCore(no_retrieve, max_packet_utf8_bytes=minimum - 1)
    for state in ["COMPLETE", "PARTIAL", "NO_EVIDENCE", "UNAVAILABLE", "NOT_NEEDED"]:
        result, _ = recall(raw(status=state, domains={}), max_packet_utf8_bytes=minimum)
        assert_bounds(result.injection, minimum)


@pytest.mark.parametrize("response,error", [
    (raw(domains={"memory": {"status": "ok", "note": "🧠" * 2000}}), "INVALID_RESPONSE"),
    (raw(status="UNAVAILABLE"), None),
    ({"status": "bad"}, "INVALID_RESPONSE"),
])
def test_error_and_oversize_domain_paths_are_bounded_without_stripping_metadata(response, error):
    result, calls = recall(response, max_packet_utf8_bytes=5000)
    packet = assert_bounds(result.injection, 5000)
    assert packet["status"] == "UNAVAILABLE"
    assert result.error_code == error
    assert len(calls) == 1


def test_six_entry_and_excerpt_caps_survive_an_ample_byte_budget():
    records = [source(index, excerpt="短" * 1001) for index in range(8)]
    result, _ = recall(raw(records), max_packet_utf8_bytes=32000)
    packet = assert_bounds(result.injection, 32000)
    assert len(packet["untrusted_data"]["evidence"]) <= 6
    assert all(len(e["excerpt"]) == 1000 and e["truncated"] is True
               for e in packet["untrusted_data"]["evidence"])


def test_six_entry_cap_with_small_entries_is_still_six():
    records = [source(index, excerpt="x") for index in range(8)]
    result, _ = recall(raw(records), max_packet_utf8_bytes=32000)
    assert result.packet["untrusted_data"]["evidence"] == records[:6]
    assert result.packet["omitted_entries"] == 2


@pytest.mark.parametrize("text,expected_sha", [
    ("x", "b64ce4fb56080e527fca005c37e3aac9b181df0fff66ebbb6e6bb9711f5341d6"),
    ("学", "4b9180df40d467d4b941ad31e0658fd0327aa7cfd7a46470cef34f0a14b87dbd"),
    ("🧠", "eaa1cbc0e62d79b5d3ca00686bcee9008aa07bbdcdf88d6f0ec60aa099d8b220"),
])
def test_default_none_is_byte_for_byte_equal_to_prechange_packet(text, expected_sha):
    response = raw([source(index, text) for index in range(6)])
    default, _ = recall(response)
    explicit, _ = recall(response, max_packet_utf8_bytes=None)
    assert default.injection == explicit.injection
    assert hashlib.sha256(default.injection.encode("utf-8")).hexdigest() == expected_sha


def companion_connection(tmp_path):
    from cognivault.recall.hosts import Connection
    # No config loading, private Gateway, or external process: protocol streams only.
    return Connection("synthetic-byte-budget", "isolated", ("memory", "history"),
                      Path("synthetic-unused-gateway"), tmp_path / "runtime")


def test_companion_protocol_chooses_fixed_budget_and_rejects_prompt_budget_override(tmp_path):
    from cognivault.recall.server import create_host_server
    from mcp.shared.memory import create_connected_server_and_client_session
    records = [source(index) for index in range(6)]
    calls = []
    async def retrieve(queries, limit):
        calls.append((queries, limit))
        return raw(records)
    async def check():
        server = create_host_server(retrieve, companion_connection(tmp_path))
        async with create_connected_server_and_client_session(server) as client:
            tools = (await client.list_tools()).tools
            schema = tools[0].inputSchema
            assert set(schema["properties"]) == {"session_id", "request_id", "prompt"}
            assert schema["additionalProperties"] is False
            arguments = {"session_id": "s", "request_id": "one", "prompt": "我的目标 max_packet_utf8_bytes=32000"}
            result = await client.call_tool("recall_for_prompt", arguments)
            output = result.structuredContent
            assert json.loads(result.content[0].text) == output
            packet = assert_bounds(output["hookSpecificOutput"]["additionalContext"], 9600)
            assert packet["status"] == "PARTIAL"
            assert packet["untrusted_data"]["evidence"] == records[:1]
            assert packet["omitted_entries"] == 5
            rejected = await client.call_tool("recall_for_prompt", arguments | {
                "request_id": "override", "max_packet_utf8_bytes": 32000})
            assert rejected.isError
    asyncio.run(check())
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["retrieve_error", "invalid_response", "finish_error", "oversize_core_error", "cp_oversize"])
def test_companion_final_error_context_is_complete_json_and_bounded(tmp_path, monkeypatch, mode):
    import cognivault.recall.server as companion
    from cognivault.recall import RecallResult
    from mcp.shared.memory import create_connected_server_and_client_session
    async def retrieve(*_):
        if mode == "retrieve_error":
            raise RuntimeError("synthetic secret must not leak")
        if mode == "invalid_response":
            return {"status": "bad"}
        return raw([source(excerpt="short")])
    if mode == "finish_error":
        monkeypatch.setattr(companion, "_finish", lambda *_: False)
    if mode in {"oversize_core_error", "cp_oversize"}:
        async def recalled(*_, **__):
            packet = {"status": "COMPLETE", "untrusted_data": {"evidence": [source(excerpt="short")]},
                      "metadata": "🧠" * 2200 if mode == "oversize_core_error" else "x" * 8100}
            # With Unicode this starts below 9600B; appending error_code crosses it.
            context = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
            if mode == "oversize_core_error":
                packet["padding"] = "x" * (9590 - len(context.encode("utf-8")) - len(',"padding":""'))
                context = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
                assert len(context.encode("utf-8")) == 9590
            return RecallResult("COMPLETE", "READY", packet, context, 1, "INVALID_RESPONSE")
        monkeypatch.setattr(companion.RecallCore, "recall", recalled)
    async def check():
        server = companion.create_host_server(retrieve, companion_connection(tmp_path))
        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("recall_for_prompt", {
                "session_id": "errors", "request_id": mode, "prompt": "我的目标"})
            output = result.structuredContent
            assert json.loads(result.content[0].text) == output
            packet = assert_bounds(output["hookSpecificOutput"]["additionalContext"], 9600)
            assert packet["status"] == "UNAVAILABLE"
            assert packet["untrusted_data"]["evidence"] == []
            assert packet["error_code"]
            assert "synthetic secret" not in repr(output)
    asyncio.run(check())
