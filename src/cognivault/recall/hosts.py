"""Explicit read-only command-hook connection. Never discovers private settings.

This independent MCP SDK path is not evidence of a native Desktop connection.
Only the trusted hook command supplies configuration; stdin is user data.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import stat
import tempfile
from time import monotonic
import tomllib

from . import RecallCore, RecallPolicy, RecallRequest
from .planning import local_plan

MAX_TOMBSTONES = 2048
ERROR_CODES = {"INVALID_CONFIGURATION", "READ_ONLY_REQUIRED", "RUNTIME_MUST_BE_OUTSIDE_GIT",
               "RUNTIME_MUST_BE_USER_LOCAL", "UNSAFE_RUNTIME", "RUNTIME_CAPACITY", "RUNTIME_BUSY",
               "MISSING_HOST_IDENTITY", "INVALID_EVENT", "READ_ONLY_TOOL_UNAVAILABLE", "DOMAIN_NOT_ALLOWED",
               "RETRIEVE_FAILED", "INVALID_RESPONSE"}
_rpc_identity = ContextVar("recall_rpc_identity", default=None)


@dataclass(frozen=True)
class Connection:
    configuration_id: str
    profile: str
    allowed_domains: tuple[str, ...]
    gateway_config: Path
    runtime_dir: Path


def _absolute(value, *, runtime=False):
    if type(value) is not str or not value or not Path(value).is_absolute():
        raise ValueError("INVALID_CONFIGURATION")
    path = Path(value)
    if runtime:
        for p in (path, *path.parents):
            try:
                info = p.lstat()
            except FileNotFoundError:
                continue
            if p.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("UNSAFE_RUNTIME")
    return path.resolve()


def _read_toml(path):
    with Path(path).open("rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("INVALID_CONFIGURATION")
    return tomllib.loads(data.decode("utf-8"))


def load_connection(path: Path) -> Connection:
    raw = _read_toml(path)
    fields = {"configuration_id", "profile", "allowed_domains", "context_delivery_acknowledged", "gateway_config", "runtime_dir"}
    if (set(raw) != fields or raw["context_delivery_acknowledged"] is not True
            or type(raw["configuration_id"]) is not str
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", raw["configuration_id"])
            or raw["profile"] not in {"isolated", "production"}
            or type(raw["allowed_domains"]) is not list or not raw["allowed_domains"]):
        raise ValueError("INVALID_CONFIGURATION")
    domains = tuple(raw["allowed_domains"])
    RecallPolicy(allowed_domains=domains)
    gateway = _absolute(raw["gateway_config"])
    if _read_toml(gateway).get("permissions") != {"capabilities": ["read"]}:
        raise ValueError("READ_ONLY_REQUIRED")
    runtime = _absolute(raw["runtime_dir"], runtime=True)
    if any((p / ".git").exists() for p in (runtime, *runtime.parents)):
        raise ValueError("RUNTIME_MUST_BE_OUTSIDE_GIT")
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA")
        if not root or not runtime.is_relative_to(Path(root).resolve()):
            raise ValueError("RUNTIME_MUST_BE_USER_LOCAL")
    return Connection(raw["configuration_id"], raw["profile"], domains, gateway, runtime)


def command_request(host, payload, environment) -> RecallRequest:
    if type(payload) is not dict:
        raise ValueError("INVALID_EVENT")
    identity = payload.get("turn_id") if host == "codex" else environment.get("CODEBUDDY_CONVERSATION_REQUEST_ID") if host == "workbuddy" else None
    session, prompt = payload.get("session_id"), payload.get("prompt")
    if (any(type(v) is not str or not v or len(v) > 256 for v in (session, identity))
            or type(prompt) is not str or len(prompt) > 60000):
        raise ValueError("MISSING_HOST_IDENTITY")
    return RecallRequest(session, identity, prompt)


def unavailable(code="UNAVAILABLE"):
    # No exception text, original prompt, private paths or backend stderr.
    return json.dumps({"schema_version": 1, "status": "UNAVAILABLE", "error_code": code,
                       "instructions": {"missing_evidence_is_not_negative_fact": True,
                                        "disclose_missing_evidence": True},
                       "untrusted_data": {"evidence": []}}, separators=(",", ":"))


def hook_output(context):
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}} if context else {}


def _claim(connection, host, request):
    root = connection.runtime_dir
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The Windows root is restricted to LocalAppData and inherits user-state ACLs.
    # POSIX owner-only permissions are enforced explicitly.
    if os.name != "nt":
        if root.stat().st_uid != os.getuid():
            raise ValueError("UNSAFE_RUNTIME")
        root.chmod(0o700)
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("UNSAFE_RUNTIME")
    key = hashlib.sha256(json.dumps([connection.configuration_id, connection.profile, host,
                                    request.session_id, request.request_id]).encode()).hexdigest()
    target = root / (key + ".json")
    if target.exists():
        return None
    lock = root / ".claim-lock"
    try:
        guard = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError("RUNTIME_BUSY") from None
    try:
        os.close(guard)
        if target.exists():
            return None
        # Bounded last-N replay window. Remove only this module's validated,
        # completed metadata; active/crashed CLAIMED and foreign files survive.
        owned, completed = [], []
        for index, item in enumerate(root.iterdir()):
            if index >= 8192:
                raise ValueError("RUNTIME_CAPACITY")
            if not re.fullmatch(r"[0-9a-f]{64}\.json", item.name):
                continue
            owned.append(item)
            info = item.lstat()
            if not stat.S_ISREG(info.st_mode) or item.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                continue
            try:
                if info.st_size > 1024:
                    continue
                data = json.loads(item.read_text(encoding="utf-8"))
                if (isinstance(data, dict) and set(data) == {"status", "count", "attempts", "elapsed_ms", "error_code"}
                        and data["status"] in {"COMPLETE", "PARTIAL", "NO_EVIDENCE", "UNAVAILABLE", "NOT_NEEDED", "INVALID_PLAN", "CANCELLED"}):
                    completed.append((info.st_mtime_ns, item))
            except (ValueError, OSError):
                continue
        for _, item in sorted(completed, key=lambda pair: pair[0]):
            if len(owned) < MAX_TOMBSTONES:
                break
            item.unlink()
            owned.remove(item)
        if len(owned) >= MAX_TOMBSTONES:
            raise ValueError("RUNTIME_CAPACITY")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    finally:
        lock.unlink()
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({"status": "CLAIMED", "count": 0, "attempts": 0, "elapsed_ms": 0, "error_code": None}, stream)
    return target


def _finish(target, status, count, attempts=0, elapsed_ms=0, error_code=None):
    # Atomic replacement lets another process distinguish completed metadata
    # from a still-CLAIMED entry without reading half a JSON record.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=".recall-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"status": status, "count": count, "attempts": attempts,
                       "elapsed_ms": elapsed_ms, "error_code": error_code}, stream)
        os.replace(temporary, target)
        return True
    except OSError:
        return False
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def unwrap_mcp(result):
    if isinstance(result, dict):
        if result.get("isError"):
            raise ValueError("RETRIEVE_FAILED")
        structured = result.get("structuredContent")
        content = result.get("content", [])
    else:
        if result.isError:
            raise ValueError("RETRIEVE_FAILED")
        structured, content = result.structuredContent, result.content
    if isinstance(structured, dict):
        return structured
    for part in content:
        text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
        if text is not None:
            return json.loads(text)
    raise ValueError("INVALID_RESPONSE")


@asynccontextmanager
async def readonly_session(reader, writer, connection):
    import anyio
    from mcp import ClientSession, types
    # Observe the public JSON-RPC wire ID, never SDK-private request counters.
    class TrackedWriter:
        def __getattr__(self, key):
            return getattr(writer, key)
        async def __aenter__(self):
            await writer.__aenter__()
            return self
        async def __aexit__(self, *args):
            return await writer.__aexit__(*args)
        async def send(self, message):
            value = message.message.root
            identity = _rpc_identity.get()
            if identity is not None and getattr(value, "method", None) == "tools/call":
                identity["id"] = value.id
            await writer.send(message)

    async with ClientSession(reader, TrackedWriter()) as client:
        await client.initialize()
        tools = (await client.list_tools()).tools
        tool = next((t for t in tools if t.name == "retrieve_evidence"), None)
        if tool is None or tool.annotations is None or tool.annotations.readOnlyHint is not True:
            raise ValueError("READ_ONLY_TOOL_UNAVAILABLE")
        async def retrieve(queries, limit):
            if set(queries) - set(connection.allowed_domains):
                raise ValueError("DOMAIN_NOT_ALLOWED")
            identity = {}
            token = _rpc_identity.set(identity)
            try:
                return unwrap_mcp(await client.call_tool("retrieve_evidence", {"queries": queries, "limit": limit}))
            except asyncio.CancelledError:
                if "id" in identity:
                    # MCP SDK1 does not translate cancelled local waits into a
                    # cancellation notification. Notify only this RPC; never
                    # close the shared session to cancel one prompt.
                    with anyio.move_on_after(.05, shield=True):
                        await client.send_notification(types.ClientNotification(types.CancelledNotification(
                            params=types.CancelledNotificationParams(requestId=identity["id"]))))
                raise
            finally:
                _rpc_identity.reset(token)
        yield retrieve


@asynccontextmanager
async def connect_readonly(connection):
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client
    # Fixed executable/module/arguments: never a user prompt or arbitrary shell.
    params = StdioServerParameters(command=sys.executable,
        args=["-X", "utf8", "-m", "cognivault.transports.mcp_stdio", "--config", str(connection.gateway_config)],
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[2]), "PYTHONIOENCODING": "utf-8"})
    with open(os.devnull, "w", encoding="utf-8") as stderr:
        async with stdio_client(params, errlog=stderr) as (reader, writer):
            async with readonly_session(reader, writer, connection) as retrieve:
                yield retrieve


async def run_command(host, payload, environment, config_path, *, deadline_seconds=3.0):
    started = monotonic()
    tombstone = None
    attempts = 0
    try:
        request = command_request(host, payload, environment)
        connection = load_connection(config_path)
        policy = RecallPolicy(allowed_domains=connection.allowed_domains)
        plan = local_plan(request, policy)
        if not plan.queries:
            return {}
        tombstone = _claim(connection, host, request)
        if tombstone is None:
            return {}
        remaining = deadline_seconds - (monotonic() - started)
        if remaining <= 0:
            raise TimeoutError
        async with asyncio.timeout(remaining):
            async with connect_readonly(connection) as retrieve:
                async def counted_retrieve(queries, limit):
                    nonlocal attempts
                    attempts += 1
                    return await retrieve(queries, limit)
                core = RecallCore(counted_retrieve, RecallPolicy(allowed_domains=connection.allowed_domains,
                                                        deadline_seconds=min(3.0, remaining)))
                result = await core.recall(request, plan=plan)
                context = "" if result.status in {"ALREADY_HANDLED", "NOT_NEEDED"} else result.injection
                if context and result.error_code:
                    packet = json.loads(context)
                    packet["error_code"] = result.error_code
                    context = json.dumps(packet, ensure_ascii=True, separators=(",", ":"))
                if not _finish(tombstone, result.status, len(result.packet["untrusted_data"]["evidence"]),
                               result.attempts, int((monotonic() - started) * 1000), result.error_code):
                    return hook_output(unavailable("RUNTIME_UNAVAILABLE"))
                return hook_output(context)
    except asyncio.CancelledError:
        if tombstone:
            _finish(tombstone, "CANCELLED", 0, attempts=attempts, elapsed_ms=int((monotonic() - started) * 1000), error_code="CANCELLED")
        raise
    except Exception as exc:
        if isinstance(exc, TimeoutError):
            code = "DEADLINE_EXCEEDED"
        elif isinstance(exc, FileNotFoundError):
            code = "CONFIGURATION_MISSING"
        elif isinstance(exc, ValueError) and exc.args and exc.args[0] in ERROR_CODES:
            code = exc.args[0]
        else:
            code = "CONNECTION_UNAVAILABLE"
        if tombstone:
            _finish(tombstone, "UNAVAILABLE", 0, attempts=attempts, elapsed_ms=int((monotonic() - started) * 1000), error_code=code)
        return hook_output(unavailable(code))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicit CogniVault read-only recall command hook")
    parser.add_argument("--host", choices=["codex", "workbuddy"], required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError
        payload = json.loads(raw)
        output = asyncio.run(run_command(args.host, payload, os.environ, args.config))
    except (Exception, KeyboardInterrupt):
        output = hook_output(unavailable("INVALID_EVENT_OR_CANCELLED"))
    print(json.dumps(output, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
