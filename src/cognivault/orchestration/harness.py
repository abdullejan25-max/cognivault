"""Fail-closed Harness integration scaffold, not a live model executor.

The JSONL envelope was inspected in installed @deepseek-ai/dsh-headless
0.2.0-rc.2. Fixtures verify a deliberately narrow no-tool subset. No caller
can enable subprocess execution until profile/tool isolation is implemented
and independently verified. See docs/agent-orchestration-harness.md.
"""
import json

from .models import Descriptor, Result, Task, fingerprint


class HarnessHeadlessAdapter:
    """Configured protocol scaffold; all live execution remains explicitly blocked."""

    descriptor = Descriptor(
        "deepseek-harness", "0.2.0-rc.2",
        frozenset({"study.analyze", "history.ingest", "history.summarize",
                   "memory.propose", "evidence.retrieve"}),
        frozenset({"text", "structured"}), "external",
    )
    blocked_reason = "HARNESS_TOOL_ISOLATION_UNVERIFIED"

    def describe(self):
        return {"agent_id": self.descriptor.agent_id,
                "version": self.descriptor.version,
                "status": "BLOCKED", "blocked_reason": self.blocked_reason,
                "execution_enabled": False, "evidence_scope": "installed-source-and-synthetic-parser"}

    async def healthy(self) -> bool:
        # Installed executable/version alone is not a healthy execution channel.
        return False

    async def execute(self, task: Task, prepared: dict) -> Result:
        task.validate()
        if not task.external_allowed:
            raise ValueError("EXTERNAL_NOT_ALLOWED")
        # Do not inspect, serialize, log, or transfer the prepared material.
        raise ValueError(self.blocked_reason)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("HARNESS_INVALID_JSON")
        result[key] = value
    return result


def _json(text):
    try:
        return json.loads(text, object_pairs_hook=_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, RecursionError):
        raise ValueError("HARNESS_INVALID_JSON") from None


def _keys(event, required, optional=()):
    if type(event) is not dict or set(event) - set(required) - set(optional) or not set(required) <= set(event):
        raise ValueError("HARNESS_INVALID_EVENT")


def _nonempty(value):
    if type(value) is not str or not value or len(value) > 8192:
        raise ValueError("HARNESS_INVALID_EVENT")


def parse_headless_result(stdout: bytes, task: Task, descriptor: Descriptor) -> Result:
    """Validate a complete no-tool stream and bound model JSON to its request.

    Pure parser, never execution evidence. It rejects tools, truncated events,
    errors, unknown events, multiple runs, and output after the final event.
    The caller must separately verify subprocess exit status and isolation;
    this scaffold currently refuses to create any subprocess.
    """
    task.validate()
    descriptor.validate()
    if not task.external_allowed:
        raise ValueError("EXTERNAL_NOT_ALLOWED")
    if type(stdout) is not bytes or len(stdout) > 1_000_000:
        raise ValueError("HARNESS_OUTPUT_LIMIT")
    try:
        lines = stdout.decode("utf-8").splitlines()
    except UnicodeError:
        raise ValueError("HARNESS_INVALID_JSON") from None
    if not lines or len(lines) > 4096 or any(not line for line in lines):
        raise ValueError("HARNESS_INVALID_STREAM")
    phase = "session"
    turn = step = final = None
    for line in lines:
        event = _json(line)
        if type(event) is not dict:
            raise ValueError("HARNESS_INVALID_EVENT")
        kind = event.get("type")
        if type(kind) is not str:
            raise ValueError("HARNESS_INVALID_EVENT")
        if "truncated" in event:
            raise ValueError("HARNESS_TRUNCATED_EVENT")
        if kind in {"tool_call", "tool_result"}:
            raise ValueError("HARNESS_TOOL_EVENT_DENIED")
        if kind == "error":
            raise ValueError("HARNESS_RUN_ERROR")
        if phase == "done":
            raise ValueError("HARNESS_INVALID_STREAM")
        if kind == "session":
            _keys(event, ("type", "sessionId", "cwd"))
            _nonempty(event["sessionId"])
            _nonempty(event["cwd"])
            if phase != "session":
                raise ValueError("HARNESS_INVALID_STREAM")
            phase = "turn_start"
        elif kind == "status":
            _counter(event.get("turn"))
            if "step" in event:
                _counter(event["step"])
            status = event.get("phase")
            if status == "turn_start":
                _keys(event, ("type", "phase", "turn"))
                _counter(event["turn"])
                if phase != "turn_start":
                    raise ValueError("HARNESS_INVALID_STREAM")
                turn = event["turn"]
                phase = "step_start"
            elif status == "step_start":
                _keys(event, ("type", "phase", "turn", "step"))
                _counter(event["step"])
                if phase != "step_start" or event["turn"] != turn:
                    raise ValueError("HARNESS_INVALID_STREAM")
                step = event["step"]
                phase = "step_content"
            elif status == "step_end":
                _keys(event, ("type", "phase", "turn", "step"), ("usage",))
                if phase != "step_content" or event["turn"] != turn or event["step"] != step:
                    raise ValueError("HARNESS_INVALID_STREAM")
                if "usage" in event:
                    _usage(event["usage"])
                phase = "turn_end"
            elif status == "turn_end":
                _keys(event, ("type", "phase", "turn", "reason"))
                if phase != "turn_end" or event["turn"] != turn or event["reason"] != {"kind": "completed"}:
                    raise ValueError("HARNESS_INCOMPLETE_TURN")
                phase = "final"
            else:
                raise ValueError("HARNESS_INVALID_EVENT")
        elif kind in {"text", "thinking"}:
            _keys(event, ("type", "text"))
            if phase != "step_content" or type(event["text"]) is not str:
                raise ValueError("HARNESS_INVALID_STREAM")
        elif kind == "final":
            _keys(event, ("type", "text"))
            if phase != "final" or type(event["text"]) is not str:
                raise ValueError("HARNESS_INVALID_STREAM")
            final = _json(event["text"])
            phase = "done"
        else:
            raise ValueError("HARNESS_INVALID_EVENT")
    if phase != "done":
        raise ValueError("HARNESS_INCOMPLETE_STREAM")
    _keys(final, ("task_id", "correlation_id", "adapter_id", "adapter_version", "output",
                  "confidence", "evidence", "uncertainties"))
    if type(final["evidence"]) is not list or type(final["uncertainties"]) is not list:
        raise ValueError("HARNESS_INVALID_RESULT")
    result = Result(**{**final, "evidence": tuple(final["evidence"]),
                       "uncertainties": tuple(final["uncertainties"])})
    try:
        result.validate(task, descriptor)
        fingerprint(result.__dict__)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("HARNESS_INVALID_RESULT") from None
    return result


def _usage(usage):
    _keys(usage, ("inputTokens", "outputTokens"),
          ("totalTokens", "cacheReadTokens", "cacheWriteTokens", "reasoningTokens"))
    if any(type(value) is not int or value < 0 for value in usage.values()):
        raise ValueError("HARNESS_INVALID_EVENT")


def _counter(value):
    if type(value) is not int or not 1 <= value <= 1_000_000:
        raise ValueError("HARNESS_INVALID_EVENT")
