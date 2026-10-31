"""Hermes v0.22.5 standalone, read-only pre_llm_call plugin (stdlib only).

Install as __init__.py beside plugin.yaml in an explicitly enabled directory
plugin. All retrieval stays on PluginContext.call_mcp; this module never owns an
MCP connection. The separately configured Python runs the shared Core bridge.
"""
from collections import OrderedDict
from contextlib import suppress
import contextvars
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
from time import monotonic

_STATUSES = {"COMPLETE", "PARTIAL", "NO_EVIDENCE", "UNAVAILABLE", "NOT_NEEDED", "ALREADY_HANDLED"}
_CODES = {"CANCELLED", "DEADLINE_EXCEEDED", "BRIDGE_UNAVAILABLE", "INVALID_BRIDGE_MESSAGE",
          "INVALID_BRIDGE_REQUEST", "INVALID_RESULT", "INVALID_RESPONSE", "RETRIEVE_FAILED",
          "NATIVE_MCP_DENIED", "CONFIGURATION_REQUIRED", "MISSING_HOST_IDENTITY"}


def _unavailable(code):
    return {"context": json.dumps({"schema_version": 1, "status": "UNAVAILABLE", "error_code": code,
        "instructions": {"missing_evidence_is_not_negative_fact": True, "disclose_missing_evidence": True},
        "untrusted_data": {"evidence": []}}, separators=(",", ":"))}


def _config(ctx):
    values = {key: ctx.get_config(key, None) for key in (
        "python_executable", "core_source_root", "gateway_server", "allowed_domains",
        "context_delivery_acknowledged")}
    if (any(type(values[k]) is not str or not values[k] or not Path(values[k]).is_absolute()
            for k in ("python_executable", "core_source_root"))
            or type(values["gateway_server"]) is not str
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", values["gateway_server"])
            or values["context_delivery_acknowledged"] is not True
            or type(values["allowed_domains"]) is not list or not values["allowed_domains"]
            or any(type(d) is not str or d not in {"memory", "history", "study"}
                   for d in values["allowed_domains"])
            or len(set(values["allowed_domains"])) != len(values["allowed_domains"])):
        raise ValueError("CONFIGURATION_REQUIRED")
    return values


def _message(value):
    if type(value) is list:
        if len(value) > 256:
            raise ValueError("MISSING_HOST_IDENTITY")
        value = "\n".join(part["text"] for part in value if type(part) is dict
                          and part.get("type") == "text" and type(part.get("text")) is str)
    if type(value) is not str or not value or len(value) > 60000:
        raise ValueError("MISSING_HOST_IDENTITY")
    return value


def _unwrap(envelope):
    # PluginContext._mcp_envelope, not an SDK CallToolResult. Never accept a
    # partial/truncated JSON head as evidence, including a structured sidecar.
    if type(envelope) is not dict or envelope.get("truncated") not in (None, False):
        raise ValueError("INVALID_RESPONSE")
    if envelope.get("ok") is not True:
        raise ValueError("RETRIEVE_FAILED")
    value = envelope.get("structuredContent", envelope.get("result"))
    if type(value) is str:
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            raise ValueError("INVALID_RESPONSE") from None
    if type(value) is not dict or value.get("isError") or "error" in value:
        raise ValueError("INVALID_RESPONSE")
    return value


def _check(deadline, is_interrupted):
    # This executes on the ORIGINAL hook thread. Its interrupt is observed but
    # never cleared or set by the adapter.
    if is_interrupted():
        raise ValueError("CANCELLED")
    if monotonic() >= deadline:
        raise ValueError("DEADLINE_EXCEEDED")


def _wait(results, deadline, is_interrupted):
    while True:
        _check(deadline, is_interrupted)
        try:
            return results.get(timeout=min(.01, max(.001, deadline - monotonic())))
        except queue.Empty:
            pass


def _native_call(ctx, config, arguments, deadline, is_interrupted, set_interrupt):
    results = queue.Queue(maxsize=1)
    guard = threading.Lock()
    state = {"active": False, "cancelled": False, "tid": None}

    def work():
        tid = threading.get_ident()
        outcome = None
        try:
            with guard:
                if state["cancelled"]:
                    return
                state.update(active=True, tid=tid)
            try:
                value = _unwrap(ctx.call_mcp(config["gateway_server"], "retrieve_evidence", arguments,
                                            timeout=max(.001, deadline - monotonic())))
                outcome = (True, value)
            except PermissionError:
                outcome = (False, "NATIVE_MCP_DENIED")
            except Exception as exc:
                code = exc.args[0] if isinstance(exc, ValueError) and exc.args else None
                outcome = (False, code if code in _CODES else "RETRIEVE_FAILED")
        finally:
            # The guard prevents a late cancellation from setting a reused tid
            # after this worker has completed. Never clear the parent Agent bit.
            with guard:
                set_interrupt(False, tid)
                state["active"] = False
        if outcome is not None:
            results.put(outcome)

    scope = contextvars.copy_context()
    threading.Thread(target=lambda: scope.run(work), daemon=True).start()
    try:
        ok, value = _wait(results, deadline, is_interrupted)
        if not ok:
            raise ValueError(value)
        return value
    finally:
        with guard:
            state["cancelled"] = True
            if state["active"]:
                set_interrupt(True, state["tid"])


def _bridge(ctx, config, request, deadline, is_interrupted, set_interrupt):
    child = None
    stopped = threading.Event()
    lines = queue.Queue(maxsize=1)
    try:
        _check(deadline, is_interrupted)
        child = subprocess.Popen(
            [config["python_executable"], "-X", "utf8", "-m", "cognivault.recall.bridge"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=os.environ | {"PYTHONPATH": config["core_source_root"], "PYTHONIOENCODING": "utf-8"},
            shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

        def read_lines():
            try:
                while not stopped.is_set():
                    raw = child.stdout.readline(262145)
                    if not raw or len(raw) > 262144 or not raw.endswith(b"\n"):
                        item = (False, "INVALID_BRIDGE_MESSAGE")
                    else:
                        item = (True, json.loads(raw))
                    while not stopped.is_set():
                        try:
                            lines.put(item, timeout=.01)
                            break
                        except queue.Full:
                            pass
                    if not item[0]:
                        break
            except Exception:
                with suppress(queue.Full):
                    lines.put_nowait((False, "INVALID_BRIDGE_MESSAGE"))

        threading.Thread(target=read_lines, daemon=True).start()

        def send(value):
            # UTF-8 preserves the Core's original input/window. ASCII escaping
            # would inflate valid 50k-char CJK prompts beyond the wire byte cap.
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
            if len(raw) > 262144:
                raise ValueError("INVALID_RESPONSE")
            # Even a bridge which stalls before reading stdin must remain
            # inside the same deadline. A large response can fill a pipe too.
            written = queue.Queue(maxsize=1)
            def write():
                try:
                    child.stdin.write(raw)
                    child.stdin.flush()
                    written.put(True)
                except Exception:
                    written.put(False)
            threading.Thread(target=write, daemon=True).start()
            if not _wait(written, deadline, is_interrupted):
                raise ValueError("BRIDGE_UNAVAILABLE")

        send({"request": request, "allowed_domains": config["allowed_domains"],
              "context_delivery_acknowledged": True})
        sequence = 0
        while True:
            ok, message = _wait(lines, deadline, is_interrupted)
            if not ok:
                raise ValueError(message)
            if type(message) is not dict:
                raise ValueError("INVALID_BRIDGE_MESSAGE")
            if message.get("type") == "result":
                if (set(message) != {"type", "status", "delivery_status", "injection"}
                        or message["status"] not in _STATUSES
                        or message["delivery_status"] not in {"READY", "EMPTY", "FAILED", "NOT_NEEDED"}
                        or type(message["injection"]) is not str or len(message["injection"]) > 8000):
                    raise ValueError("INVALID_RESULT")
                if message["injection"]:
                    packet = json.loads(message["injection"])
                    if type(packet) is not dict or packet.get("status") != message["status"]:
                        raise ValueError("INVALID_RESULT")
                return {"context": message["injection"]}
            arguments = message.get("arguments")
            if (set(message) != {"type", "id", "tool", "arguments"}
                    or message["type"] != "retrieve" or message["tool"] != "retrieve_evidence"
                    or type(message["id"]) is not int or message["id"] != sequence + 1 or sequence >= 3
                    or type(arguments) is not dict or set(arguments) != {"queries", "limit"}
                    or type(arguments["limit"]) is not int or arguments["limit"] != 3
                    or type(arguments["queries"]) is not dict or not arguments["queries"]
                    or set(arguments["queries"]) - set(config["allowed_domains"])
                    or any(type(q) is not str or not q.strip() or len(q) > 200 or any(ord(c) < 32 for c in q)
                           for q in arguments["queries"].values())):
                raise ValueError("INVALID_BRIDGE_REQUEST")
            sequence += 1
            value = _native_call(ctx, config, arguments, deadline, is_interrupted, set_interrupt)
            _check(deadline, is_interrupted)
            send({"id": sequence, "result": value})
    finally:
        stopped.set()
        if child is not None:
            # Kill only the disposable Core child. The Host's shared MCP
            # client/server is never disposed to cancel a request.
            with suppress(OSError):
                child.kill()
            with suppress(OSError, subprocess.TimeoutExpired):
                child.wait(timeout=.05)
            for stream in (child.stdin, child.stdout):
                with suppress(OSError):
                    stream.close()


def register(ctx):
    """Only pre-answer context delivery; no provider, write, sync or end hook."""
    handled = OrderedDict()
    lock = threading.Lock()

    def before_llm(*, session_id=None, turn_id=None, task_id=None, user_message=None, **kwargs):
        deadline = monotonic() + 3.0
        try:
            if (any(type(v) is not str or not v or len(v) > 256 for v in (session_id, turn_id))
                    or task_id is not None and (type(task_id) is not str or len(task_id) > 256)):
                raise ValueError("MISSING_HOST_IDENTITY")
            text = _message(user_message)
            config = _config(ctx)
            from tools.interrupt import is_interrupted, set_interrupt
            _check(deadline, is_interrupted)
            # Hash only configuration and native identity, never source/prompt.
            # No evidence or user-message history survives a callback.
            identity = hashlib.sha256(json.dumps([config, session_id, task_id, turn_id],
                                                sort_keys=True).encode()).digest()
            with lock:
                if identity in handled:
                    return {"context": ""}
                handled[identity] = None
                if len(handled) > 1024:
                    handled.popitem(last=False)
            return _bridge(ctx, config, {"session_id": session_id, "request_id": turn_id, "message": text},
                           deadline, is_interrupted, set_interrupt)
        except Exception as exc:
            code = exc.args[0] if isinstance(exc, ValueError) and exc.args else None
            return _unavailable(code if code in _CODES else "BRIDGE_UNAVAILABLE")

    ctx.register_hook("pre_llm_call", before_llm)
