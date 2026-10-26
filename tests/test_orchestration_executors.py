"""No external calls: HTTP boundary uses synthetic responses and MockTransport."""
import asyncio
from dataclasses import replace
import importlib
import json

import httpx
import pytest

from cognivault.orchestration.models import Task


def module():
    return importlib.import_module("cognivault.orchestration.executors")


def task(**changes):
    return replace(Task("synthetic-1", "corr-1", "history.summarize", {},
                        frozenset({"read"}), external_allowed=True), **changes)


def answer(**changes):
    value = dict(task_id="synthetic-1", correlation_id="corr-1",
                 adapter_id="https-chat", adapter_version="1",
                 output={"summary": "A synthetic blue triangle."}, confidence=.95,
                 evidence=["synthetic-source"], uncertainties=[])
    value.update(changes)
    return value


def response(**changes):
    value = dict(model="synthetic-provider-model", choices=[{
        "finish_reason": "stop", "message": {"role": "assistant",
        "content": json.dumps(answer())}}])
    value.update(changes)
    return value


def adapter(handler, **kwargs):
    return module().HttpsChatAdapter(model="synthetic-requested-model",
        transport=httpx.MockTransport(handler), **kwargs)


def test_missing_auth_refuses_before_serializing_or_network(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    calls = []
    value = adapter(lambda request: calls.append(request))
    with pytest.raises(module().AdapterError, match="^AUTH_REQUIRED$"):
        asyncio.run(value.execute(task(), {"not_serializable": object()}))
    assert calls == []
    with pytest.raises(module().AdapterError, match="^AUTH_REQUIRED$"):
        asyncio.run(value.healthy())


def test_tls_ignores_environment_ca_overrides_without_disabling_verification(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    import ssl
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    private = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'synthetic-untrusted-root')])
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(private.public_key()).serial_number(1)
        .not_valid_before(datetime(2020,1,1,tzinfo=timezone.utc))
        .not_valid_after(datetime(2099,1,1,tzinfo=timezone.utc))
        .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
        .sign(private,hashes.SHA256()))
    ca_file = tmp_path/'untrusted-ca.pem'
    ca_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    untrusted_der = certificate.public_bytes(serialization.Encoding.DER)
    contexts, create = [], ssl.create_default_context
    def capture(*args, **kwargs):
        context = create(*args, **kwargs)
        contexts.append(context)
        return context
    monkeypatch.setattr(ssl, 'create_default_context', capture)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-secret')
    monkeypatch.setenv('SSL_CERT_FILE', str(ca_file))
    monkeypatch.setenv('SSL_CERT_DIR', str(tmp_path/'untrusted-roots'))
    async def send(client, request, **kwargs):
        return httpx.Response(200, json=response(), request=request)
    # Keep actual TLS transport construction, replace only the network send.
    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    value = module().HttpsChatAdapter(model='synthetic-requested-model')
    result = asyncio.run(value.execute(task(), {'text':'synthetic'}))
    assert result.output == {'summary':'A synthetic blue triangle.'}
    assert contexts
    assert all(untrusted_der not in context.get_ca_certs(binary_form=True) for context in contexts)
    assert all(context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname for context in contexts)


def test_actual_request_has_only_explicit_context_and_no_tools_or_task_inputs(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    requests = []
    def serve(request):
        requests.append(request)
        return httpx.Response(200, json=response())
    value = adapter(serve)
    result, evidence = asyncio.run(value.execute_with_evidence(
        task(inputs={"never_send": "private-placeholder"}), {"text": "synthetic"}))
    sent = json.loads(requests[0].content)
    assert set(sent) == {"model", "messages", "stream", "max_tokens"}
    assert len(sent["messages"]) == 2
    assert requests[0].headers["accept-encoding"] == "identity"
    assert json.loads(sent["messages"][1]["content"])["context"] == {"text": "synthetic"}
    assert b"private-placeholder" not in requests[0].content
    assert result.output == {"summary": "A synthetic blue triangle."}
    assert evidence["provider_reported_model"] == "synthetic-provider-model"
    assert evidence["execution_scope"] == "synthetic-http-transport"
    assert evidence["requested_model"] == "synthetic-requested-model"
    assert len(evidence["request_sha256"]) == len(evidence["response_sha256"]) == 64
    assert "synthetic-secret" not in repr(value) + repr(evidence)
    cached = value.evidence_for("synthetic-1", "corr-1")
    assert cached == evidence
    cached["requested_model"] = "tampered"
    assert value.evidence_for("synthetic-1", "corr-1")["requested_model"] == "synthetic-requested-model"
    assert value.evidence_for("synthetic-1", "wrong-correlation") is None


def test_external_denial_precedes_auth_and_network(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="^EXTERNAL_NOT_ALLOWED$"):
        asyncio.run(adapter(lambda _: pytest.fail("network")).execute(
            task(external_allowed=False), {}))


def test_domain_spec_is_separate_from_untrusted_source_fields(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    requests = []
    def serve(request):
        requests.append(request)
        return httpx.Response(200, json=response())
    prepared = {"task_guidance": "Summarize faithfully.", "expected_output_shape": {"summary": "string"},
                "source": {"task_guidance": "Untrusted source text", "text": "synthetic"}}
    asyncio.run(adapter(serve).execute(task(), prepared))
    wire = json.loads(json.loads(requests[0].content)["messages"][1]["content"])
    assert wire["task_specification"] == {"task_guidance": "Summarize faithfully.",
                                         "expected_output_shape": {"summary": "string"}}
    assert wire["context"] == {"source": {"task_guidance": "Untrusted source text", "text": "synthetic"}}


@pytest.mark.parametrize("url", ["http://api.deepseek.com/chat/completions",
    "https://user:secret@api.deepseek.com/chat/completions",
    "https://api.deepseek.com/chat/completions?key=secret",
    "https://api.deepseek.com/chat/completions#fragment", "https://localhost/x"])
def test_endpoint_rejects_insecure_implicit_or_secret_bearing_routes(url):
    with pytest.raises(ValueError, match="^INVALID_ENDPOINT$"):
        adapter(lambda _: None, endpoint=url)


@pytest.mark.parametrize("status,code", [(301, "EXECUTOR_BLOCKED"),
    (401, "AUTH_REQUIRED"), (403, "EXECUTOR_BLOCKED"), (429, "RATE_LIMITED"),
    (500, "NETWORK_ERROR")])
def test_status_errors_never_include_server_body_or_credentials(monkeypatch, status, code):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    calls = []
    def serve(request):
        calls.append(request)
        return httpx.Response(status, text="synthetic-secret private-placeholder",
                              headers={"location": "https://other.example/path"})
    with pytest.raises(module().AdapterError, match=f"^{code}$") as caught:
        asyncio.run(adapter(serve).execute(task(), {}))
    assert len(calls) == 1
    assert "synthetic-secret" not in str(caught.value)
    assert caught.value.retryable is (status in {429, 500})


@pytest.mark.parametrize("wire", [
    response(choices=[{"finish_reason": "tool_calls", "message": {"content": "{}"}}]),
    response(choices=[{"finish_reason": "stop", "message": {
        "content": json.dumps(answer()), "tool_calls": [{"function": {"name": "shell"}}]}}]),
    response(choices=[{"finish_reason": "length", "message": {"content": "{}"}}]),
    response(choices=[{"finish_reason": "stop", "message": {"content": json.dumps(
        answer(task_id="wrong-task"))}}]),
    response(model=""), response(choices=[]),
])
def test_rejects_tool_requests_truncation_unbound_results_and_missing_model(monkeypatch, wire):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    with pytest.raises(module().AdapterError, match="^INVALID_RESPONSE$"):
        asyncio.run(adapter(lambda _: httpx.Response(200, json=wire)).execute(task(), {}))


def test_output_cap_and_duplicate_keys_fail_closed(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    for content in [b"x" * 1_000_001, b'{"model":"first","model":"second"}']:
        with pytest.raises(module().AdapterError, match="^INVALID_RESPONSE$"):
            asyncio.run(adapter(lambda _: httpx.Response(200, content=content)).execute(task(), {}))


def test_transport_error_is_redacted(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    def serve(request):
        raise httpx.ConnectError("synthetic-secret private-placeholder", request=request)
    with pytest.raises(module().AdapterError, match="^NETWORK_ERROR$"):
        asyncio.run(adapter(serve).execute(task(), {}))


@pytest.mark.parametrize("key", ["synthetic\nsecret", "synthetic secret", "合成secret"])
def test_invalid_auth_cannot_escape_as_header_encoding_traceback(monkeypatch, key):
    monkeypatch.setenv("DEEPSEEK_API_KEY", key)
    with pytest.raises(module().AdapterError, match="^EXECUTOR_BLOCKED$"):
        asyncio.run(adapter(lambda _: pytest.fail("network")).execute(task(), {}))


def test_direct_execution_rejects_unsupported_capabilities(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    with pytest.raises(module().AdapterError, match="^EXECUTOR_BLOCKED$"):
        asyncio.run(adapter(lambda _: pytest.fail("network")).execute(
            task(required_capabilities=frozenset({"image"})), {}))


def test_timeout_and_cancellation_reach_async_transport(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    async def run(cancel):
        started, stopped = asyncio.Event(), asyncio.Event()
        async def serve(request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        operation = asyncio.create_task(adapter(serve).execute(task(timeout_s=.05), {}))
        await started.wait()
        if cancel:
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
        else:
            with pytest.raises(module().AdapterError, match="^EXECUTOR_TIMEOUT$"):
                await operation
        assert stopped.is_set()
    asyncio.run(run(False))
    asyncio.run(run(True))


def test_explicit_model_refusal_has_distinct_failure(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    wire = response(choices=[{"finish_reason": "stop", "message": {
        "role": "assistant", "content": None, "refusal": "synthetic refusal"}}])
    with pytest.raises(module().AdapterError, match="^MODEL_REFUSED$"):
        asyncio.run(adapter(lambda _: httpx.Response(200, json=wire)).execute(task(), {}))


def test_failure_clears_prior_evidence_and_cache_is_bounded(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    def serve(request):
        binding = json.loads(json.loads(request.content)["messages"][1]["content"])
        wire = response()
        wire["choices"][0]["message"]["content"] = json.dumps(answer(task_id=binding["task_id"]))
        return httpx.Response(200, json=wire)
    value = adapter(serve)
    async def fill():
        for index in range(65):
            await value.execute(task(task_id=f"synthetic-{index}"), {})
    asyncio.run(fill())
    assert value.evidence_for("synthetic-0", "corr-1") is None
    assert value.evidence_for("synthetic-64", "corr-1") is not None
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    with pytest.raises(module().AdapterError, match="^AUTH_REQUIRED$"):
        asyncio.run(value.execute(task(task_id="synthetic-64"), {}))
    assert value.evidence_for("synthetic-64", "corr-1") is None


def test_compressed_wire_cannot_expand_past_response_cap(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    class CompressedBomb(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("compressed body must be rejected before decompression")
            yield b""
    with pytest.raises(module().AdapterError, match="^INVALID_RESPONSE$"):
        asyncio.run(adapter(lambda _: httpx.Response(200,
            stream=CompressedBomb(), headers={"content-encoding": "gzip"}
        )).execute(task(), {}))


def harness_adapter(tmp_path):
    from cognivault.orchestration.harness_no_tools import HarnessNoToolsAdapter
    installation = tmp_path / "installation"
    (installation / "resources").mkdir(parents=True)
    (installation / "DeepSeek Harness.exe").touch()
    (installation / "resources/app.asar").touch()
    return HarnessNoToolsAdapter(installation_root=installation,
        credential_path=tmp_path / "operator-credentials.yaml")


def test_harness_stdout_binding_evidence_and_isolated_argv(monkeypatch, tmp_path):
    import sys
    value = harness_adapter(tmp_path)
    spawn = asyncio.create_subprocess_exec
    calls = []
    async def launch(*args, **kwargs):
        calls.append((args, kwargs))
        script = '''import sys,json
r=json.loads(sys.stdin.buffer.read()); b=r['binding']
result={k:b[k] for k in ('task_id','correlation_id','adapter_id','adapter_version')}
result.update(output={'summary':'synthetic'},confidence=.95,evidence=['synthetic-source'],uncertainties=[])
e=dict(adapter_id=b['adapter_id'],adapter_version=b['adapter_version'],requested_model=r['model'],provider_reported_model='deepseek-flash',request_sha256='a'*64,response_sha256='b'*64,execution_scope='external-model')
print(json.dumps(dict(result_text=json.dumps(result),evidence=e,isolation=dict(tools=0,profile_loader=False,sessions=False,network_requests=1))))
'''
        return await spawn(sys._base_executable, "-c", script, **kwargs)
    monkeypatch.setenv("NODE_OPTIONS", "never inherit")
    monkeypatch.setenv("HTTPS_PROXY", "never inherit")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    result, evidence = asyncio.run(value.execute_with_evidence(task(), {"text": "synthetic"}))
    assert result.output == {"summary": "synthetic"}
    assert evidence["response_sha256"] == "b" * 64
    args, kwargs = calls[0]
    assert args[1] == "--expose-internals"
    assert args[2].endswith("harness_runtime.mjs")
    assert "shell" not in kwargs
    assert not {"NODE_OPTIONS", "HTTPS_PROXY", "DEEPSEEK_API_KEY"} & set(kwargs["env"])
    assert kwargs["env"]["DSH_HOME"] != str(tmp_path)
    assert kwargs["env"]["HOME"] == kwargs["env"]["DSH_HOME"]
    assert value.evidence_for("synthetic-1", "corr-1") == evidence


def test_harness_missing_program_and_spawn_failure_are_sanitized(monkeypatch, tmp_path):
    value = harness_adapter(tmp_path)
    async def launch(*args, **kwargs):
        raise OSError("private-placeholder synthetic-secret")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    with pytest.raises(module().AdapterError, match="^EXECUTOR_BLOCKED$"):
        asyncio.run(value.execute(task(), {}))


@pytest.mark.parametrize("mode", ["stdout-limit", "stderr-limit", "timeout", "cancel"])
def test_harness_process_limits_timeout_and_cancellation_reap_process(monkeypatch, tmp_path, mode):
    import sys
    value = harness_adapter(tmp_path)
    spawn = asyncio.create_subprocess_exec
    processes = []
    started = None
    async def launch(*args, **kwargs):
        script = ("import sys,time;sys.stdout.buffer.write(b'x'*1000001);sys.stdout.flush();time.sleep(60)"
            if mode == "stdout-limit" else
            "import sys,time;sys.stderr.buffer.write(b'x'*65537);sys.stderr.flush();time.sleep(60)"
            if mode == "stderr-limit" else "import time;time.sleep(60)")
        process = await spawn(sys._base_executable, "-c", script, **kwargs)
        processes.append(process)
        started.set()
        return process
    # Keep the OS taskkill launcher real; redirect only the Harness executable.
    async def dispatch(*args, **kwargs):
        if str(args[0]).endswith("DeepSeek Harness.exe"):
            return await launch(*args, **kwargs)
        return await spawn(*args, **kwargs)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", dispatch)
    async def run():
        nonlocal started
        started = asyncio.Event()
        operation = asyncio.create_task(value.execute(task(timeout_s=.3 if mode == "timeout" else 10), {}))
        await started.wait()
        if mode == "cancel":
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
        else:
            code = "EXECUTOR_TIMEOUT" if mode == "timeout" else "INVALID_RESPONSE"
            with pytest.raises(module().AdapterError, match=f"^{code}$"):
                await operation
        assert processes[0].returncode is not None
        assert value.evidence_for("synthetic-1", "corr-1") is None
    asyncio.run(run())


@pytest.mark.skipif(__import__('os').name != 'nt', reason="Windows process tree boundary")
def test_harness_windows_stop_reaps_descendant(tmp_path):
    import ctypes
    import os
    import sys
    from cognivault.orchestration.harness_no_tools import HarnessNoToolsAdapter
    async def run():
        marker = tmp_path / "child.pid"
        script = ("import subprocess,sys,time;from pathlib import Path;"
                  "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],creationflags=0x08000000);"
                  "Path(sys.argv[1]).write_text(str(p.pid));time.sleep(60)")
        process = await asyncio.create_subprocess_exec(sys.executable,"-c",script,str(marker),creationflags=0x08000000)
        try:
            async with asyncio.timeout(5):
                while not marker.exists():
                    await asyncio.sleep(.02)
            pid = int(marker.read_text())
            handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
            assert handle
            try:
                await HarnessNoToolsAdapter._stop(process)
                assert ctypes.windll.kernel32.WaitForSingleObject(handle, 5000) == 0
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        finally:
            if process.returncode is None:
                await HarnessNoToolsAdapter._stop(process)
    asyncio.run(run())


@pytest.mark.skipif(__import__('os').name != 'nt', reason="Windows Job containment")
def test_guarded_process_cannot_spawn_child_even_when_parent_exits(tmp_path):
    import sys
    from cognivault.orchestration import process_guard
    async def run():
        marker = tmp_path / "child.pid"
        denied = tmp_path / "denied"
        script = ("import subprocess,sys;from pathlib import Path;"
                  "marker=Path(sys.argv[1]);denied=Path(sys.argv[2]);\n"
                  "try:\n p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],creationflags=0x08000000);marker.write_text(str(p.pid))\n"
                  "except OSError:\n denied.write_text('denied')\n")
        process = await process_guard.spawn(sys._base_executable,"-c",script,str(marker),str(denied),
            stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        await asyncio.wait_for(process.wait(), 5)
        await process_guard.stop(process)
        assert denied.read_text() == 'denied'
        assert not marker.exists()
    asyncio.run(run())


@pytest.mark.skipif(__import__('os').name != 'nt', reason="Windows suspended startup")
def test_guard_assign_failure_never_runs_application(monkeypatch, tmp_path):
    import sys
    from cognivault.orchestration import process_guard
    spawn = asyncio.create_subprocess_exec
    processes = []
    async def observe(*args, **kwargs):
        value = await spawn(*args, **kwargs)
        processes.append(value)
        return value
    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe)
    monkeypatch.setattr(process_guard._kernel, "AssignProcessToJobObject", lambda *_: False)
    async def run():
        marker = tmp_path / "ran"
        with pytest.raises(module().AdapterError, match="^EXECUTOR_BLOCKED$"):
            await process_guard.spawn(sys._base_executable, "-c",
                "from pathlib import Path;import sys;Path(sys.argv[1]).write_text('ran')", str(marker))
        assert not marker.exists()
        assert processes[0].returncode is not None
    asyncio.run(run())


@pytest.mark.skipif(__import__('os').name != 'nt', reason="Windows suspended startup cancellation")
def test_guard_cancellation_during_spawn_keeps_handle_for_cleanup(monkeypatch, tmp_path):
    import sys
    from cognivault.orchestration import process_guard
    spawn = asyncio.create_subprocess_exec
    processes = []
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            value = await spawn(*args, **kwargs)
            processes.append(value)
            started.set()
            await release.wait()
            return value
        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
        marker = tmp_path / "ran"
        operation = asyncio.create_task(process_guard.spawn(sys._base_executable, "-c",
            "from pathlib import Path;import sys;Path(sys.argv[1]).write_text('ran')", str(marker)))
        await started.wait()
        operation.cancel()
        release.set()
        try:
            with pytest.raises(asyncio.CancelledError):
                await operation
            assert processes[0].returncode is not None
            assert not marker.exists()
        finally:
            if processes[0].returncode is None:
                processes[0].kill()
                await processes[0].wait()
    asyncio.run(run())


@pytest.mark.skipif(__import__('os').name == 'nt', reason="POSIX process group boundary")
def test_guard_posix_kills_descendant_after_group_leader_exits(tmp_path):
    import os
    import signal
    import sys
    from pathlib import Path
    from cognivault.orchestration import process_guard
    async def run():
        marker = tmp_path / 'child.pid'
        script = ("import subprocess,sys;from pathlib import Path;"
                  "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                  "Path(sys.argv[1]).write_text(str(p.pid))")
        parent = await process_guard.spawn(sys.executable,'-c',script,str(marker),
            stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        await parent.wait()
        child = int(marker.read_text())
        try:
            await process_guard.stop(parent)
            async with asyncio.timeout(5):
                while True:
                    try:
                        os.kill(child, 0)
                        state = Path(f'/proc/{child}/stat')
                        if state.exists() and state.read_text().split()[2] == 'Z':
                            break
                    except ProcessLookupError:
                        break
                    await asyncio.sleep(.02)
        finally:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
    asyncio.run(run())
