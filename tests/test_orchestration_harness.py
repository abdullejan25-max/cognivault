"""Synthetic protocol fixtures only; no installed Harness, profiles or credentials."""
import asyncio
from dataclasses import replace
import importlib
import json

import pytest

from cognivault.orchestration.models import Task


def harness():
    return importlib.import_module("cognivault.orchestration.harness")


def request(**changes):
    task = Task("synthetic-1", "correlation-1", "study.analyze", {},
                frozenset({"read"}), external_allowed=True)
    return replace(task, **changes)


def answer(**changes):
    result = {
        "task_id": "synthetic-1", "correlation_id": "correlation-1",
        "adapter_id": "deepseek-harness", "adapter_version": "0.2.0-rc.2",
        "output": {"answer": "synthetic"}, "confidence": .95,
        "evidence": ["synthetic-source"], "uncertainties": [],
    }
    result.update(changes)
    return result


def events(final=None):
    # Envelope keys are from installed dsh-headless json-stream-BA-F3lfb.js.
    return [
        {"type": "session", "sessionId": "session-synthetic", "cwd": "isolated"},
        {"type": "status", "phase": "turn_start", "turn": 1},
        {"type": "status", "phase": "step_start", "turn": 1, "step": 1},
        {"type": "text", "text": "synthetic"},
        {"type": "status", "phase": "step_end", "turn": 1, "step": 1},
        {"type": "status", "phase": "turn_end", "turn": 1, "reason": {"kind": "completed"}},
        {"type": "final", "text": json.dumps(final if final is not None else answer())},
    ]


def wire(records):
    return ("\n".join(json.dumps(e) for e in records) + "\n").encode()


def parse(records):
    return harness().parse_headless_result(wire(records), request(), harness().HarnessHeadlessAdapter.descriptor)


def test_complete_stream_binds_exact_task_and_adapter_identity():
    result = parse(events())
    assert result.output == {"answer": "synthetic"}
    assert result.task_id == "synthetic-1"
    assert result.evidence == ("synthetic-source",)


@pytest.mark.parametrize("changes", [
    {"task_id": "another"}, {"correlation_id": "another"},
    {"adapter_id": "another"}, {"adapter_version": "another"},
    {"confidence": True}, {"confidence": float("nan")},
    {"evidence": "invented"}, {"uncertainties": "none"},
    {"output": []}, {"extra": "ignored?"},
])
def test_rejects_unbound_or_malformed_model_result(changes):
    with pytest.raises(ValueError):
        parse(events(answer(**changes)))


@pytest.mark.parametrize("mutate", [
    lambda r: r[:-1],
    lambda r: r + [r[-1]],
    lambda r: r[1:],
    lambda r: [r[0], r[-1]],
    lambda r: r[:5] + [{**r[5], "reason": {"kind": "error"}}] + r[6:],
    lambda r: r[:4] + r[5:],
    lambda r: r[:3] + [{"type": "tool_call", "tool": "pwsh", "callId": "x", "input": {}}] + r[3:],
    lambda r: r[:3] + [{"type": "tool_result", "callId": "x", "status": "completed", "result": "x"}] + r[3:],
    lambda r: r[:3] + [{"type": "unknown"}] + r[3:],
    lambda r: [{**r[0], "truncated": True}] + r[1:],
    lambda r: r[:3] + [{"type": "error", "message": "DO_NOT_LEAK"}] + r[3:],
])
def test_rejects_incomplete_out_of_order_error_and_tool_streams(mutate):
    with pytest.raises(ValueError) as exc:
        parse(mutate(events()))
    assert "DO_NOT_LEAK" not in str(exc.value)


@pytest.mark.parametrize("data", [b'not JSON\n', b'[]\n', b'{"type":"session","type":"final"}\n', b'\xff\n'])
def test_rejects_invalid_wire_format_without_leaking_input(data):
    with pytest.raises(ValueError):
        harness().parse_headless_result(data, request(), harness().HarnessHeadlessAdapter.descriptor)


def test_parser_bounds_total_output_and_requires_external_authorization():
    module = harness()
    with pytest.raises(ValueError, match="HARNESS_OUTPUT_LIMIT"):
        module.parse_headless_result(b"x" * (1_000_000 + 1), request(), module.HarnessHeadlessAdapter.descriptor)
    with pytest.raises(ValueError, match="EXTERNAL_NOT_ALLOWED"):
        module.parse_headless_result(wire(events()), request(external_allowed=False), module.HarnessHeadlessAdapter.descriptor)


def test_adapter_is_not_healthy_and_never_starts_a_model_while_isolation_unverified():
    adapter = harness().HarnessHeadlessAdapter()
    assert asyncio.run(adapter.healthy()) is False
    with pytest.raises(ValueError, match="HARNESS_TOOL_ISOLATION_UNVERIFIED"):
        asyncio.run(adapter.execute(request(), {"synthetic": "no external transfer"}))
    with pytest.raises(ValueError, match="EXTERNAL_NOT_ALLOWED"):
        asyncio.run(adapter.execute(request(external_allowed=False), {}))



def test_rejects_untyped_event_discriminant_and_invalid_counters():
    for bad in [[], {}, None]:
        with pytest.raises(ValueError):
            parse([{"type": bad}])
    for bad in [True, "1", 0, -1]:
        records = events()
        records[1]["turn"] = bad
        with pytest.raises(ValueError):
            parse(records)


def test_explicit_blocked_description_does_not_claim_connected():
    description = harness().HarnessHeadlessAdapter().describe()
    assert description["status"] == "BLOCKED"
    assert description["blocked_reason"] == "HARNESS_TOOL_ISOLATION_UNVERIFIED"
    assert description["execution_enabled"] is False


@pytest.mark.parametrize("index,key", [(2, "turn"), (2, "step"), (4, "turn"), (4, "step"), (5, "turn")])
def test_boolean_counter_cannot_equal_a_valid_numeric_counter(index, key):
    records = events()
    records[index][key] = True
    with pytest.raises(ValueError):
        parse(records)


def test_numeric_usage_is_validated():
    records = events()
    records[4]["usage"] = {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}
    assert parse(records).output == {"answer": "synthetic"}
    records[4]["usage"]["outputTokens"] = True
    with pytest.raises(ValueError):
        parse(records)
