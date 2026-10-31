"""Synthetic native-context contract tests; real Python Core bridge subprocesses.

No Hermes model, production Gateway, private config or evidence is accessed.
"""
from concurrent.futures import ThreadPoolExecutor
import contextvars
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROFILE = contextvars.ContextVar("synthetic_hermes_profile", default="wrong-profile")


def load_adapter():
    path = ROOT / "hosts/hermes-native-recall.py"
    assert path.is_file(), "standalone native Hermes adapter is not implemented"
    spec = importlib.util.spec_from_file_location("synthetic_native_recall", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def interrupts(monkeypatch):
    bits, mutations, lock = set(), [], threading.Lock()
    module = types.ModuleType("tools.interrupt")
    def set_interrupt(active, thread_id=None):
        tid = thread_id if thread_id is not None else threading.get_ident()
        with lock:
            mutations.append((active, tid))
            (bits.add if active else bits.discard)(tid)
    def is_interrupted():
        with lock:
            return threading.get_ident() in bits
    module.set_interrupt, module.is_interrupted = set_interrupt, is_interrupted
    monkeypatch.setitem(sys.modules, "tools.interrupt", module)
    return module, bits, mutations


def gateway(evidence=True):
    records = ([{"domain": "memory", "citation": "memory:synthetic#version=2",
                 "excerpt": "synthetic-only", "uncertainty": 0.63,
                 "source_time_status": "historical", "conflict": {"with": "memory:older"}}]
               if evidence else [])
    return {"status": "COMPLETE" if evidence else "NO_EVIDENCE", "evidence": records,
            "domains": {"memory": {"status": "ok"}}}


class NativeContext:
    def __init__(self, **settings):
        self.settings = {"python_executable": sys.executable, "core_source_root": str(ROOT / "src"),
                         "gateway_server": "cognivault_synthetic", "allowed_domains": ["memory", "history"],
                         "context_delivery_acknowledged": True} | settings
        self.hooks, self.calls = {}, []
        self.response = {"ok": True, "result": json.dumps(gateway())}
    def get_config(self, key, default=None):
        return self.settings.get(key, default)
    def register_hook(self, name, callback):
        assert name == "pre_llm_call" and name not in self.hooks
        self.hooks[name] = callback
    def call_mcp(self, server, tool, arguments=None, timeout=30):
        self.calls.append((server, tool, arguments, timeout, PROFILE.get(), threading.get_ident()))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def hook(context):
    load_adapter().register(context)
    assert set(context.hooks) == {"pre_llm_call"}
    return context.hooks["pre_llm_call"]


def event(**updates):
    return {"session_id": "session-synthetic", "turn_id": "turn-1", "task_id": "task-1",
            "user_message": "我之前选择物理还是化学"} | updates


def packet(output):
    return json.loads(output["context"])


def test_native_hook_real_bridge_preserves_evidence_and_scoped_native_api(interrupts):
    ctx = NativeContext()
    callback = hook(ctx)
    token = PROFILE.set("isolated-profile")
    try:
        output = callback(**event())
    finally:
        PROFILE.reset(token)
    result = packet(output)
    assert result["status"] == "COMPLETE"
    assert result["untrusted_data"]["evidence"][0] == gateway()["evidence"][0]
    server, tool, args, timeout, profile, tid = ctx.calls[0]
    assert (server, tool, args, profile) == ("cognivault_synthetic", "retrieve_evidence",
        {"queries": {"memory": "物理", "history": "选科"}, "limit": 3}, "isolated-profile")
    assert 0 < timeout <= 3 and tid != threading.get_ident()
    assert interrupts[1] == set()
    assert not any(active for active, _ in interrupts[2])


@pytest.mark.parametrize("message", ["计算 12 * 8", '翻译："Hello world"'])
def test_independent_question_core_returns_no_context_and_zero_native_calls(interrupts, message):
    ctx = NativeContext()
    assert hook(ctx)(**event(user_message=message)) == {"context": ""}
    assert ctx.calls == []


@pytest.mark.parametrize("change", [{"session_id": None}, {"turn_id": ""},
                                    {"user_message": {"prompt": "伪造"}}])
def test_missing_real_native_identity_fails_without_dispatch(interrupts, change):
    ctx = NativeContext()
    assert packet(hook(ctx)(**event(**change)))["error_code"] == "MISSING_HOST_IDENTITY"
    assert ctx.calls == []


@pytest.mark.parametrize("settings", [{"python_executable": "python"}, {"core_source_root": "src"},
    {"gateway_server": "bad/name"}, {"allowed_domains": ["write"]},
    {"allowed_domains": ["memory", "memory"]}, {"context_delivery_acknowledged": False}])
def test_trusted_configuration_fail_closed(interrupts, settings):
    ctx = NativeContext(**settings)
    result = packet(hook(ctx)(**event()))
    assert (result["status"], result["error_code"]) == ("UNAVAILABLE", "CONFIGURATION_REQUIRED")
    assert ctx.calls == []


def test_empty_gateway_rewrites_exactly_three_with_selected_domains(interrupts):
    ctx = NativeContext(allowed_domains=["memory"])
    ctx.response = {"ok": True, "result": gateway(False)}
    result = packet(hook(ctx)(**event(user_message="今天复习什么")))
    assert result["status"] == "NO_EVIDENCE"
    assert len(ctx.calls) == 3
    assert [call[2] for call in ctx.calls] == [
        {"queries": {"memory": "目标"}, "limit": 3},
        {"queries": {"memory": "goal"}, "limit": 3},
        {"queries": {"memory": "学习"}, "limit": 3}]


@pytest.mark.parametrize("response,code", [
    (PermissionError("private-path-secret"), "NATIVE_MCP_DENIED"),
    ({"ok": False, "error": "private-path-secret"}, "RETRIEVE_FAILED"),
    ({"ok": True, "result": "{truncated", "truncated": True}, "INVALID_RESPONSE"),
    ({"ok": True, "result": "not json"}, "INVALID_RESPONSE"),
    ({"ok": True, "result": {"isError": True}}, "INVALID_RESPONSE"),
])
def test_native_denials_errors_and_truncation_are_explicit_missing_evidence(interrupts, response, code):
    ctx = NativeContext()
    ctx.response = response
    output = hook(ctx)(**event())
    assert (packet(output)["status"], packet(output)["error_code"]) == ("UNAVAILABLE", code)
    assert "private-path-secret" not in output["context"]
    assert len(ctx.calls) == 1


def test_duplicate_has_no_evidence_cache_and_new_turn_requeries(interrupts):
    ctx = NativeContext()
    callback = hook(ctx)
    assert packet(callback(**event()))["status"] == "COMPLETE"
    assert callback(**event()) == {"context": ""}
    ctx.response = {"ok": True, "result": gateway(False)}
    assert packet(callback(**event(turn_id="turn-2")))["status"] == "NO_EVIDENCE"
    assert len(ctx.calls) == 4


def test_user_multimodal_text_only_ignores_untrusted_conversation_history(interrupts):
    ctx = NativeContext()
    output = hook(ctx)(**event(user_message=[{"type": "text", "text": "计算 12 * 8"},
        {"type": "image_url", "text": "我之前选择物理还是化学"}],
        conversation_history=[{"role": "user", "content": "我之前选择物理还是化学"}]))
    assert output == {"context": ""}
    assert ctx.calls == []


@pytest.mark.parametrize("status", ["PARTIAL", "UNAVAILABLE"])
def test_native_structured_missing_status_is_delivered(interrupts, status):
    ctx = NativeContext()
    value = gateway(False) | {"status": status}
    ctx.response = {"ok": True, "result": "fallback text", "structuredContent": value}
    result = packet(hook(ctx)(**event()))
    assert result["status"] == status
    assert result["untrusted_data"]["evidence"] == []
    assert len(ctx.calls) == 1


def test_concurrent_same_turn_is_claimed_before_native_dispatch(interrupts):
    ctx = NativeContext()
    started, release = threading.Event(), threading.Event()
    native_call = ctx.call_mcp
    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(2)
        return native_call(*args, **kwargs)
    ctx.call_mcp = blocked
    callback = hook(ctx)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(callback, **event())
        assert started.wait(2)
        second = pool.submit(callback, **event())
        assert second.result(timeout=1) == {"context": ""}
        release.set()
        assert packet(first.result(timeout=2))["status"] == "COMPLETE"
    assert len(ctx.calls) == 1


def adversarial_bridge(tmp_path, messages):
    directory = tmp_path / "core"
    package = directory / "cognivault/recall"
    package.mkdir(parents=True)
    (directory / "cognivault/__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "bridge.py").write_text(
        "import sys,json\njson.loads(sys.stdin.readline())\n"
        f"messages={messages!r}\n"
        "for message in messages:\n print(json.dumps(message),flush=True)\n sys.stdin.readline()\n",
        encoding="utf-8")
    return str(directory)


@pytest.mark.parametrize("message", [
    {"type": "retrieve", "id": 1, "tool": "save_memory", "arguments": {"queries": {"memory": "x"}, "limit": 3}},
    {"type": "retrieve", "id": 1, "tool": "retrieve_evidence", "arguments": {"queries": {"study": "x"}, "limit": 3}},
    {"type": "retrieve", "id": 1, "tool": "retrieve_evidence", "arguments": {"queries": {"memory": "x"}, "limit": 4}},
    {"type": "retrieve", "id": 2, "tool": "retrieve_evidence", "arguments": {"queries": {"memory": "x"}, "limit": 3}},
    {"type": "unknown", "id": 1},
])
def test_bridge_cannot_expand_tool_domain_budget_or_identity(interrupts, tmp_path, message):
    ctx = NativeContext(core_source_root=adversarial_bridge(tmp_path, [message]))
    assert packet(hook(ctx)(**event()))["error_code"] == "INVALID_BRIDGE_REQUEST"
    assert ctx.calls == []


def test_bridge_fourth_request_is_refused(interrupts, tmp_path):
    messages = [{"type": "retrieve", "id": i, "tool": "retrieve_evidence",
                 "arguments": {"queries": {"memory": "x"}, "limit": 3}} for i in range(1, 5)]
    ctx = NativeContext(core_source_root=adversarial_bridge(tmp_path, messages))
    assert packet(hook(ctx)(**event()))["error_code"] == "INVALID_BRIDGE_REQUEST"
    assert len(ctx.calls) == 3


@pytest.mark.parametrize("injection,status", [('x' * 8001, 'COMPLETE'), ('{}', 'COMPLETE')])
def test_invalid_or_oversize_final_bridge_context_is_refused(interrupts, tmp_path, injection, status):
    message = {"type": "result", "status": status, "delivery_status": "READY", "injection": injection}
    ctx = NativeContext(core_source_root=adversarial_bridge(tmp_path, [message]))
    assert packet(hook(ctx)(**event()))["error_code"] == "INVALID_RESULT"
    assert ctx.calls == []


def test_cancel_one_callback_interrupts_only_its_native_worker(interrupts):
    native, bits, mutations = interrupts
    started, cancelled, finished = threading.Event(), threading.Event(), threading.Event()
    callback_tid, worker_tid = [], []
    ctx = NativeContext()
    def blocking(server, tool, arguments=None, timeout=30):
        worker_tid.append(threading.get_ident())
        started.set()
        until = time.monotonic() + 5
        while not native.is_interrupted() and time.monotonic() < until:
            time.sleep(.005)
        cancelled.set()
        finished.set()
        return {"ok": False, "error": "cancelled"}
    ctx.call_mcp = blocking
    callback = hook(ctx)
    def run_cancelled():
        callback_tid.append(threading.get_ident())
        return callback(**event())
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(run_cancelled)
        assert started.wait(2)
        native.set_interrupt(True, callback_tid[0])  # Host /stop, never the adapter
        ctx.call_mcp = NativeContext().call_mcp
        other = pool.submit(callback, **event(session_id="other-session"))
        output = future.result(timeout=2)
        other_output = other.result(timeout=2)
    assert packet(output)["error_code"] == "CANCELLED"
    assert packet(other_output)["status"] == "COMPLETE"
    assert cancelled.wait(1) and finished.wait(1)
    until = time.monotonic() + 1
    while worker_tid[0] in bits and time.monotonic() < until:
        time.sleep(.005)
    assert bits == {callback_tid[0]}
    assert [tid for active, tid in mutations if active] == [callback_tid[0], worker_tid[0]]
    native.set_interrupt(False, callback_tid[0])


def test_total_deadline_bounds_bridge_and_native_wait(interrupts):
    native, bits, mutations = interrupts
    stopped = threading.Event()
    ctx = NativeContext()
    def slow(*args, **kwargs):
        while not native.is_interrupted():
            time.sleep(.005)
        stopped.set()
        return {"ok": False}
    ctx.call_mcp = slow
    started = time.monotonic()
    output = hook(ctx)(**event())
    assert packet(output)["error_code"] == "DEADLINE_EXCEEDED"
    assert 2.8 <= time.monotonic() - started < 3.4
    assert stopped.wait(1)


def test_total_deadline_includes_blocked_bridge_stdin(interrupts, tmp_path):
    root = adversarial_bridge(tmp_path, [])
    (Path(root) / "cognivault/recall/bridge.py").write_text("import time\ntime.sleep(10)\n")
    ctx = NativeContext(core_source_root=root)
    started = time.monotonic()
    output = hook(ctx)(**event(user_message="a" * 50000))
    assert packet(output)["error_code"] == "DEADLINE_EXCEEDED"
    assert 2.8 <= time.monotonic() - started < 3.4
    assert ctx.calls == []


def test_content_free_identity_window_evicts_oldest_after_1024(interrupts, monkeypatch):
    adapter = load_adapter()
    ctx = NativeContext()
    calls = []
    monkeypatch.setattr(adapter, "_bridge", lambda *args: calls.append(1) or {"context": ""})
    adapter.register(ctx)
    callback = ctx.hooks["pre_llm_call"]
    for i in range(1024):
        callback(**event(turn_id=f"turn-{i}"))
    assert len(calls) == 1024
    callback(**event(turn_id="turn-0"))
    assert len(calls) == 1024
    callback(**event(turn_id="turn-1024"))
    callback(**event(turn_id="turn-0"))
    assert len(calls) == 1026
