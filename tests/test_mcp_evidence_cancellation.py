"""Request cancellation through the canonical MCP stdio server and an owned child."""

from contextlib import asynccontextmanager, contextmanager
import os
from pathlib import Path
import select
import sys

import anyio
from mcp import StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.message import SessionMessage
import pytest


BACKEND = """
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep

query = sys.argv[-1]
pid = Path(query + ".pid")
pid.with_suffix(".tmp").write_text(str(os.getpid()), encoding="ascii")
pid.with_suffix(".tmp").replace(pid)
if query == "tree":
    with subprocess.Popen([sys.executable, __file__, "descendant"]) as child:
        sys.exit(child.wait())
deadline = monotonic() + 12
while not Path(query + ".release").exists():
    if monotonic() >= deadline:
        sys.exit(2)
    sleep(0.02)
Path(query + ".finished").touch()
print(json.dumps([]))
"""


def _serve(root, backend_timeout):
    from cognivault.adapters.study_qmd import QmdStudyBackend
    from cognivault.config import AppConfig
    from cognivault.gateway import Gateway
    from cognivault.transports.mcp_stdio import _serve_stdio, create_mcp_server

    class SyntheticGateway(Gateway):
        def search_documents(self, query, limit):
            (root / f"{query}.documents").touch()
            return {"results": []}

        def search_wrong_answers(self, query, limit):
            return {"results": []}

    config = AppConfig("0.9.1", study_root=root)
    # Windows virtual-environment executables can be redirectors. Use the
    # interpreter itself so the observed PID is the process the adapter owns.
    backend = QmdStudyBackend(
        config, approved_qmd_version="2.8.3", resolver=lambda _: sys._base_executable,
        timeout_seconds=backend_timeout,
    )
    gateway = SyntheticGateway(config, backend, capabilities=frozenset({"read"}))
    async def serve():
        # Leave only enough default worker slots for the SDK's stdin and stdout.
        anyio.to_thread.current_default_thread_limiter().total_tokens = 2
        await _serve_stdio(create_mcp_server(gateway))

    anyio.run(serve)


class _Connection:
    def __init__(self, receive, send):
        self.receive = receive
        self.send = send
        self.responses = {}

    async def request(self, method, request_id=None, params=None):
        frame = {"jsonrpc": "2.0", "method": method}
        if request_id is not None:
            frame["id"] = request_id
        if params is not None:
            frame["params"] = params
        await self.send.send(SessionMessage(types.JSONRPCMessage.model_validate(frame)))

    async def retrieve(self, request_id, query):
        await self.request("tools/call", request_id, {
            "name": "retrieve_evidence", "arguments": {"queries": {"study": query}, "limit": 3},
        })

    async def response(self, request_id, timeout=3):
        with anyio.fail_after(timeout):
            while request_id not in self.responses:
                message = (await self.receive.receive()).message.root
                assert isinstance(message, (types.JSONRPCResponse, types.JSONRPCError))
                assert message.id not in self.responses, "duplicate response"
                self.responses[message.id] = message
        return self.responses[request_id]


@asynccontextmanager
async def _connection(root, backend_timeout=10):
    (root / "search").write_text(BACKEND, encoding="utf-8")
    repository = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(
        command=sys.executable, args=[__file__, str(root), str(backend_timeout)], cwd=repository,
        env={"PYTHONPATH": str(repository / "src")},
    )
    async with stdio_client(params) as (receive, send):
        client = _Connection(receive, send)
        try:
            await client.request("initialize", 1, {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "synthetic-cancellation-test", "version": "1"},
            })
            assert isinstance(await client.response(1, timeout=25), types.JSONRPCResponse)
            await client.request("notifications/initialized")
            yield client
        finally:
            for query in ("a", "b", "descendant"):
                (root / f"{query}.release").touch()


async def _started(root, query):
    pid_file = root / f"{query}.pid"
    with anyio.fail_after(3):
        while not pid_file.exists():
            await anyio.sleep(0.02)
    return int(pid_file.read_text(encoding="ascii"))


@contextmanager
def _process_exited(pid):
    if sys.platform == "win32":
        import _winapi

        handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, pid)
        try:
            yield lambda: _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_OBJECT_0
        finally:
            _winapi.CloseHandle(handle)
    elif hasattr(os, "pidfd_open"):
        descriptor = os.pidfd_open(pid)
        try:
            yield lambda: bool(select.select([descriptor], [], [], 0)[0])
        finally:
            os.close(descriptor)
    else:
        def exited():
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            return False

        yield exited


def test_stdio_ping_returns_while_evidence_backend_is_still_running(tmp_path):
    async def check():
        async with _connection(tmp_path) as client:
            await client.retrieve(2, "a")
            pid = await _started(tmp_path, "a")
            with _process_exited(pid) as exited:
                await client.request("ping", 3)
                assert isinstance(await client.response(3), types.JSONRPCResponse)
                assert not exited()
            (tmp_path / "a.release").touch()
            assert (await client.response(2)).result["structuredContent"]["status"] == "NO_EVIDENCE"

    anyio.run(check)


def test_stdio_cancel_stops_the_owned_backend_before_release(tmp_path):
    async def check():
        async with _connection(tmp_path) as client:
            await client.retrieve(2, "a")
            pid = await _started(tmp_path, "a")
            with _process_exited(pid) as exited:
                await client.request("notifications/cancelled", params={"requestId": 2})
                with anyio.move_on_after(3):
                    while not exited():
                        await anyio.sleep(0.02)
                assert exited(), "cancelled backend is still running before release"
            assert isinstance(await client.response(2), types.JSONRPCError)
            assert not (tmp_path / "a.finished").exists()
            assert not (tmp_path / "a.documents").exists()

    anyio.run(check)


def test_stdio_cancel_isolated_from_concurrent_and_later_evidence_requests(tmp_path):
    async def check():
        async with _connection(tmp_path) as client:
            await client.retrieve(2, "a")
            a_pid = await _started(tmp_path, "a")
            await client.retrieve(3, "b")
            b_pid = await _started(tmp_path, "b")
            assert a_pid != b_pid
            with _process_exited(a_pid) as a_exited, _process_exited(b_pid) as b_exited:
                await client.request("ping", 4)
                assert isinstance(await client.response(4), types.JSONRPCResponse)
                assert not a_exited() and not b_exited()
                await client.request("notifications/cancelled", params={"requestId": 2})
                assert isinstance(await client.response(2), types.JSONRPCError)
                with anyio.move_on_after(3):
                    while not a_exited():
                        await anyio.sleep(0.02)
                assert a_exited()
                assert not b_exited()
                (tmp_path / "b.release").touch()
                assert (await client.response(3)).result["structuredContent"]["status"] == "NO_EVIDENCE"
                (tmp_path / "a.pid").unlink()
                await client.retrieve(5, "a")
                await _started(tmp_path, "a")
                (tmp_path / "a.release").touch()
                assert (await client.response(5)).result["structuredContent"]["status"] == "NO_EVIDENCE"
                # The old request ID must not affect a later request.
                await client.request("notifications/cancelled", params={"requestId": 2})
                await client.request("ping", 6)
                assert isinstance(await client.response(6), types.JSONRPCResponse)

    anyio.run(check)


def test_stdio_evidence_backend_timeout_keeps_public_error_and_connection(tmp_path):
    async def check():
        async with _connection(tmp_path, backend_timeout=5) as client:
            await client.retrieve(2, "a")
            pid = await _started(tmp_path, "a")
            with _process_exited(pid) as exited:
                response = await client.response(2, timeout=7)
                assert exited()
            bundle = response.result["structuredContent"]
            assert bundle["status"] == "PARTIAL"
            assert bundle["domains"]["study"]["errors"] == [{"backend": "qmd", "code": "BACKEND_TIMEOUT"}]
            assert bundle["limits"]["per_backend"] == 3
            assert not (tmp_path / "a.finished").exists()
            (tmp_path / "b.release").touch()
            await client.retrieve(3, "b")
            assert (await client.response(3)).result["structuredContent"]["status"] == "NO_EVIDENCE"

    anyio.run(check)


@pytest.mark.skipif(
    sys.platform != "win32" and not hasattr(os, "pidfd_open"),
    reason="requires process handles for both the leader and its descendant",
)
def test_stdio_cancel_stops_owned_descendant_with_inherited_output_pipes(tmp_path):
    async def check():
        async with _connection(tmp_path) as client:
            await client.retrieve(2, "tree")
            parent = await _started(tmp_path, "tree")
            descendant = await _started(tmp_path, "descendant")
            with _process_exited(parent) as parent_exited, _process_exited(descendant) as child_exited:
                assert not parent_exited() and not child_exited()
                await client.request("notifications/cancelled", params={"requestId": 2})
                assert isinstance(await client.response(2), types.JSONRPCError)
                with anyio.fail_after(3):
                    while not (parent_exited() and child_exited()):
                        await anyio.sleep(0.02)
                assert not (tmp_path / "descendant.finished").exists()
            await client.request("ping", 3)
            assert isinstance(await client.response(3), types.JSONRPCResponse)

    anyio.run(check)


if __name__ == "__main__":
    _serve(Path(sys.argv[1]), float(sys.argv[2]))
