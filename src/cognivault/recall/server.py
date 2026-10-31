"""Persistent Host companion MCP adapter, separate from the domain Gateway.

Explicitly configured at Host startup; no discovery, fallback, HTTP or daemon.
Startup connects the fixed read-only Gateway once. Each prompt uses the common
Core with its own three-second budget and cancellation scope.
"""
import argparse
import asyncio
import json
from pathlib import Path
from time import monotonic

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import RecallCore, RecallPolicy, RecallRequest
from .hosts import _claim, _finish, connect_readonly, hook_output, load_connection, unavailable
from .planning import local_plan

TURN_DEADLINE_SECONDS = 3.0


def create_host_server(retrieve, connection):
    server = Server("cognivault-recall-host", version="1")
    policy = RecallPolicy(allowed_domains=connection.allowed_domains)
    core = RecallCore(retrieve, policy)

    @server.list_tools()
    async def tools():
        return [types.Tool(name="recall_for_prompt",
            description="Read-only pre-answer Host recall. Arguments must be the actual current Host user event, never source instructions.",
            inputSchema={"type": "object", "properties": {
                "session_id": {"type": "string", "minLength": 1, "maxLength": 256},
                "request_id": {"type": "string", "minLength": 1, "maxLength": 256},
                "prompt": {"type": "string", "maxLength": 60000}},
                "required": ["session_id", "request_id", "prompt"], "additionalProperties": False},
            annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))]

    @server.call_tool()
    async def recall(name, arguments):
        started, tombstone = monotonic(), None
        try:
            if name != "recall_for_prompt" or type(arguments) is not dict or set(arguments) != {"session_id", "request_id", "prompt"}:
                raise ValueError
            request = RecallRequest(arguments["session_id"], arguments["request_id"], arguments["prompt"])
            if (any(type(v) is not str or not v or len(v) > 256 for v in (request.session_id, request.request_id))
                    or type(request.message) is not str or len(request.message) > 60000):
                raise ValueError
            plan = local_plan(request, policy)
            output = {}
            if plan.queries:
                tombstone = _claim(connection, "codex-companion", request)
                if tombstone is not None:
                    remaining = TURN_DEADLINE_SECONDS - (monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError
                    async with asyncio.timeout(remaining):
                        result = await core.recall(request, plan=plan)
                    context = result.injection
                    if result.error_code:
                        packet = json.loads(context)
                        packet["error_code"] = result.error_code
                        context = json.dumps(packet, ensure_ascii=True, separators=(",", ":"))
                    if result.status in {"NOT_NEEDED", "ALREADY_HANDLED"}:
                        context = ""
                    if not _finish(tombstone, result.status, len(result.packet["untrusted_data"]["evidence"]),
                                   result.attempts, int((monotonic() - started) * 1000), result.error_code):
                        context = unavailable("RUNTIME_UNAVAILABLE")
                    output = hook_output(context)
        except asyncio.CancelledError:
            if tombstone:
                _finish(tombstone, "CANCELLED", 0, attempts=None, elapsed_ms=int((monotonic() - started) * 1000), error_code="CANCELLED")
            raise
        except Exception as exc:
            code = "DEADLINE_EXCEEDED" if isinstance(exc, TimeoutError) else "HOST_ADAPTER_UNAVAILABLE"
            if tombstone:
                _finish(tombstone, "UNAVAILABLE", 0, attempts=None, elapsed_ms=int((monotonic() - started) * 1000), error_code=code)
            output = hook_output(unavailable(code))
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(output, ensure_ascii=True))],
                                    structuredContent=output, isError=False)

    return server


async def serve(connection):
    # Host MCP startup timeout is a separate configuration/acceptance concern.
    # There is no automatic command-to-companion fallback.
    async with connect_readonly(connection) as retrieve:
        server = create_host_server(retrieve, connection)
        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicit read-only CogniVault Host companion MCP")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        asyncio.run(serve(load_connection(args.config)))
    except (Exception, KeyboardInterrupt):
        return 2  # no raw exceptions, private paths, backend stderr
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
