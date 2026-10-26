"""Installed Harness kernel with an explicit no-tool composition, not a profile.

Only operator-supplied installation/credential paths are accepted. They never
come from Task, prepared context, model output, discovery, or a user profile.
"""
import asyncio
from collections import OrderedDict
import json
import os
from pathlib import Path
import tempfile

from .executors import HttpsChatAdapter, separate_prepared
from .harness import _json
from .models import AdapterError, Descriptor, Result, Task, identifier
from . import process_guard


class HarnessNoToolsAdapter(HttpsChatAdapter):
    descriptor = Descriptor("deepseek-harness-no-tools", "0.2.1-rc.3.1",
        HttpsChatAdapter.descriptor.kinds, frozenset({"text", "structured"}), "external")

    def __init__(self, *, installation_root: Path, credential_path: Path,
                 model: str = "deepseek-flash", provider: str = "deepseek-account"):
        installation_root, credential_path = Path(installation_root), Path(credential_path)
        if (not installation_root.is_absolute() or not credential_path.is_absolute()
                or not identifier(model) or provider not in {"deepseek-account", "deepseek-official"}):
            raise ValueError("INVALID_EXECUTOR_CONFIG")
        self._installation = installation_root
        self._credentials = credential_path
        self.model, self.provider = model, provider
        self._evidence = OrderedDict()

    async def healthy(self) -> bool:
        # Program-file readiness only; credentials are consumed by the standard
        # installed service at execution, never read by this Python adapter.
        if not (self._installation / "DeepSeek Harness.exe").is_file():
            raise AdapterError("EXECUTOR_BLOCKED")
        if not (self._installation / "resources/app.asar").is_file():
            raise AdapterError("EXECUTOR_BLOCKED")
        return True

    async def execute_with_evidence(self, task: Task, prepared: dict) -> tuple[Result, dict]:
        task.validate()
        cache_key = task.task_id, task.correlation_id
        self._evidence.pop(cache_key, None)
        if not task.external_allowed:
            raise ValueError("EXTERNAL_NOT_ALLOWED")
        if (task.kind not in self.descriptor.kinds
                or not task.required_capabilities <= self.descriptor.capabilities
                or type(prepared) is not dict):
            raise AdapterError("EXECUTOR_BLOCKED")
        await self.healthy()
        try:
            specification, context = separate_prepared(prepared)
            body = json.dumps({"model": self.model, "provider": self.provider,
                "timeout_ms": max(1, int(task.timeout_s * 1000)), "binding": {
                    "task_id": task.task_id, "correlation_id": task.correlation_id,
                    "adapter_id": self.descriptor.agent_id,
                    "adapter_version": self.descriptor.version,
                    "operation": task.kind, "task_specification": specification, "context": context}},
                ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (ValueError, TypeError, RecursionError):
            raise AdapterError("EXECUTOR_BLOCKED") from None
        if len(body) > 1_000_000:
            raise AdapterError("EXECUTOR_BLOCKED")
        # No shell, cmd launcher, inherited Node options, proxy, TLS override,
        # API-key environments, profile directory, or invocation .env loader.
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "LOCALAPPDATA"}}
        with tempfile.TemporaryDirectory(prefix="cognivault-harness-") as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir()
            env.update(ELECTRON_RUN_AS_NODE="1", DSH_HOME=str(home), HOME=str(home),
                       USERPROFILE=str(home),
                       COGNIVAULT_HARNESS_MODULES=str(self._installation / "resources/app.asar/dsh/node_modules"),
                       COGNIVAULT_HARNESS_CREDENTIAL_PATH=str(self._credentials))
            process = None
            try:
                async with asyncio.timeout(task.timeout_s):
                    process = await process_guard.spawn(
                        str(self._installation / "DeepSeek Harness.exe"), "--expose-internals",
                        str(Path(__file__).with_name("harness_runtime.mjs")),
                        cwd=str(root), env=env, stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                        creationflags=0x08000000 if os.name == "nt" else 0)
                    # Drain both streams concurrently to avoid pipe deadlocks.
                    async def send():
                        process.stdin.write(body)
                        await process.stdin.drain()
                        process.stdin.close()
                    stdout, stderr, _ = await asyncio.gather(
                        self._bounded(process.stdout, 1_000_000),
                        self._bounded(process.stderr, 65536), send())
                    code = await process.wait()
            except TimeoutError:
                if process is not None:
                    await self._stop(process)
                raise AdapterError("EXECUTOR_TIMEOUT", retryable=True) from None
            except OSError:
                if process is not None:
                    await self._stop(process)
                raise AdapterError("EXECUTOR_BLOCKED") from None
            except BaseException:
                if process is not None:
                    await self._stop(process)
                raise
            finally:
                if process is not None:
                    await self._stop(process)
            try:
                wire = _json(stdout.decode("utf-8"))
            except (ValueError, UnicodeError):
                raise AdapterError("INVALID_RESPONSE") from None
            if type(wire) is not dict:
                raise AdapterError("INVALID_RESPONSE")
            if code != 0:
                error = wire.get("error_code")
                if error not in {"AUTH_REQUIRED", "EXECUTOR_BLOCKED", "MODEL_REFUSED", "NETWORK_ERROR",
                                 "RATE_LIMITED", "INVALID_RESPONSE", "EXECUTOR_TIMEOUT"}:
                    error = "EXECUTOR_BLOCKED"
                raise AdapterError(error, retryable=error in {"NETWORK_ERROR", "RATE_LIMITED", "EXECUTOR_TIMEOUT"})
            try:
                evidence = wire["evidence"]
                if (stderr or set(wire) != {"result_text", "evidence", "isolation"}
                        or wire["isolation"] != {"tools": 0, "profile_loader": False,
                                                 "sessions": False, "network_requests": 1}
                        or set(evidence) != {"requested_model", "provider_reported_model",
                            "request_sha256", "response_sha256", "execution_scope", "adapter_id", "adapter_version"}
                        or evidence["requested_model"] != self.model
                        or evidence["adapter_id"] != self.descriptor.agent_id
                        or evidence["adapter_version"] != self.descriptor.version
                        or evidence["execution_scope"] != "external-model"
                        or any(len(evidence[key]) != 64 or any(c not in "0123456789abcdef" for c in evidence[key])
                               for key in ("request_sha256", "response_sha256"))):
                    raise ValueError()
                # Reuse the exact complete Result binding checks, not a wrapper
                # that would turn arbitrary assistant text into trusted output.
                result, _ = self._result(json.dumps({"model": evidence["provider_reported_model"],
                    "choices": [{"finish_reason": "stop", "message": {
                        "role": "assistant", "content": wire["result_text"]}}]}).encode(), task)
            except (ValueError, TypeError, KeyError, AttributeError):
                raise AdapterError("INVALID_RESPONSE") from None
            self._evidence[cache_key] = dict(evidence)
            while len(self._evidence) > 64:
                self._evidence.popitem(last=False)
            return result, dict(evidence)

    @staticmethod
    async def _bounded(stream, limit):
        chunks, size = [], 0
        while chunk := await stream.read(65536):
            size += len(chunk)
            if size > limit:
                raise AdapterError("INVALID_RESPONSE")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    async def _stop(process):
        if hasattr(process, "_cognivault_job") or hasattr(process, "_cognivault_pgid"):
            await process_guard.stop(process)
            return
        # Best effort compatibility for an unguarded, still-live process. Product
        # execution always enters the guard before its initial instruction.
        if process.returncode is not None:
            return
        if os.name == "nt":
            # An argument array, with the absolute OS executable; never a shell
            # command. /T reaps descendants even though no process tool is mounted.
            killer = await asyncio.create_subprocess_exec(
                str(Path(os.environ["SYSTEMROOT"]) / "System32/taskkill.exe"),
                "/PID", str(process.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                creationflags=0x08000000)
            try:
                await asyncio.wait_for(killer.wait(), timeout=5)
            except TimeoutError:
                killer.kill()
                await killer.wait()
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()
