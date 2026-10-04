"""Protocol tests use only an in-memory MCP client and invented Study data."""

import json
from pathlib import Path
import socket
from unittest.mock import patch

import anyio
from jsonschema import ValidationError, validate
from mcp.shared.memory import create_connected_server_and_client_session
import pytest

from cognivault.config import AppConfig
from cognivault.contracts import BackendStudyHit, GatewayError
from cognivault.gateway import Gateway
from cognivault.runtime import load_gateway_from_config
from cognivault.transports.mcp_stdio import create_mcp_server


class SyntheticStudyBackend:
    name = "synthetic"

    def __init__(self):
        self.calls = []

    def search(self, query: str, limit: int):
        self.calls.append((query, limit))
        return [BackendStudyHit("Invented lesson", "A simple example", "untrusted", "lesson.md", 0.8)]


def _gateway(tmp_path: Path):
    root = tmp_path / "invented-study"
    root.mkdir()
    (root / "lesson.md").write_text("Synthetic notes only.", encoding="utf-8")
    backend = SyntheticStudyBackend()
    gateway = Gateway(AppConfig("0.1.0", root), backend, qmd_discoverable=lambda: False,
                      capabilities=frozenset({"read", "write", "ingest"}))
    return gateway, backend


def test_protocol_reports_installed_package_version(tmp_path: Path) -> None:
    gateway, _ = _gateway(tmp_path)

    async def check():
        with patch("cognivault.transports.mcp_stdio.distribution_version", return_value="9.8.7") as installed_version:
            async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
                initialized = await client.initialize()
                assert initialized.serverInfo.name == "cognivault"
                assert initialized.serverInfo.version == "9.8.7"
            installed_version.assert_called_once_with("cognivault")

    anyio.run(check)


def test_protocol_lists_tools_with_narrow_inputs_and_correct_write_hints(tmp_path: Path) -> None:
    gateway, _ = _gateway(tmp_path)

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            listed = await client.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            read_names = {"health_report", "search_study", "list_history_sources",
                          "list_legacy_sources", "search_legacy_sources", "fetch_legacy_source",
                          "search_history", "fetch_history_item", "search_documents",
                          "fetch_document", "fetch_document_page", "list_document_ocr_candidates",
                          "fetch_asset",
                          "get_wrong_answer_bundle", "search_wrong_answers"}
            write_names = {"register_asset", "ingest_documents", "register_wrong_answer_source",
                           "process_document_ocr_pages", "save_wrong_answer_analysis",
                           "update_wrong_answer_analysis"}
            assert set(tools) == read_names | write_names
            assert tools["health_report"].inputSchema == {
                "type": "object", "properties": {}, "additionalProperties": False,
            }
            search_schema = tools["search_study"].inputSchema
            assert set(search_schema["properties"]) == {"query", "limit"}
            assert search_schema["required"] == ["query"]
            assert search_schema["additionalProperties"] is False
            for name in read_names:
                assert tools[name].annotations.readOnlyHint is True
            for name in write_names:
                assert tools[name].annotations.readOnlyHint is False
            for tool in tools.values():
                assert tool.inputSchema["additionalProperties"] is False
            for name in ("register_asset", "register_wrong_answer_source", "get_wrong_answer_bundle",
                         "save_wrong_answer_analysis", "update_wrong_answer_analysis"):
                assert "study-workflow://wrong-answer" in tools[name].description

    anyio.run(check)


def test_projection_snapshot_tool_is_hidden_from_read_and_gated_at_call_time(tmp_path: Path) -> None:
    gateway, _ = _gateway(tmp_path)

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert "projection_snapshot" not in names
            result = await client.call_tool("projection_snapshot", {
                "domain": "history", "operation": "begin",
            })
            assert result.isError is True
            assert result.structuredContent["error"]["code"] == "PERMISSION_DENIED"

    anyio.run(check)


def test_projection_snapshot_tool_requires_and_uses_projection_capability(tmp_path: Path) -> None:
    gateway, _ = _gateway(tmp_path)
    gateway.capabilities = frozenset({"projection"})

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            listed = await client.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert set(tools) == {"projection_snapshot"}
            tool = tools["projection_snapshot"]
            assert tool.annotations.readOnlyHint is True
            assert tool.inputSchema["additionalProperties"] is False
            assert tool.inputSchema["properties"]["limit"]["maximum"] == 20
            result = await client.call_tool("projection_snapshot", {
                "domain": "history", "operation": "begin",
            })
            assert result.isError is True
            assert result.structuredContent["error"]["code"] == "HISTORY_UNAVAILABLE"

    anyio.run(check)


def test_wrong_answer_workflow_is_discoverable_from_mcp_resource_and_prompt(tmp_path: Path) -> None:
    gateway, _ = _gateway(tmp_path)

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            resources = await client.list_resources()
            workflow = next(item for item in resources.resources
                            if str(item.uri) == "study-workflow://wrong-answer")
            assert workflow.mimeType == "text/markdown"
            assert "Use this workflow" in workflow.description
            body = (await client.read_resource(str(workflow.uri))).contents[0].text
            assert "original visual" in body.lower()
            assert "OCR" in body
            assert "search_study" in body
            assert "save_wrong_answer_analysis" in body
            assert "reported identity" in body.lower()
            assert "explicit request to save" in body.lower()

            prompts = await client.list_prompts()
            prompt = next(item for item in prompts.prompts if item.name == "wrong_answer_workflow")
            assert "study-workflow://wrong-answer" in prompt.description
            rendered = await client.get_prompt("wrong_answer_workflow")
            assert rendered.messages[0].content.text == body

    anyio.run(check)


def test_protocol_query_schema_rejects_empty_long_and_nul_text(tmp_path: Path) -> None:
    gateway, _ = _gateway(tmp_path)

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            tools = await client.list_tools()
            schema = next(tool.inputSchema for tool in tools.tools if tool.name == "search_study")
            invalid_accepted = []
            for query in ("", "x" * 501, "a\x00b"):
                try:
                    validate({"query": query}, schema)
                except ValidationError:
                    continue
                invalid_accepted.append(query)
            assert invalid_accepted == []
            validate({"query": "x" * 500}, schema)

    anyio.run(check)


def test_protocol_health_preserves_gateway_summary(tmp_path: Path) -> None:
    gateway, _ = _gateway(tmp_path)

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            response = await client.call_tool("health_report", {})
            assert response.isError is False
            assert response.structuredContent == {
                "ok": True,
                "gateway_version": "0.1.0",
                "study": {"configured": True, "root_exists": True, "readable": True},
                "history": {"backend": "not_configured", "status": "not_configured"},
                "qmd": {"discoverable": False},
            }
            assert str(tmp_path) not in json.dumps(response.structuredContent)

    anyio.run(check)


def test_protocol_search_preserves_sources_and_default_limit(tmp_path: Path) -> None:
    gateway, backend = _gateway(tmp_path)

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            response = await client.call_tool("search_study", {"query": "invented topic"})
            assert response.isError is False
            assert response.structuredContent == {
                "ok": True,
                "backend": "synthetic",
                "truncated": False,
                "results": [{
                    "title": "Invented lesson", "snippet": "A simple example",
                    "source_id": "study:lesson.md", "source_path": "lesson.md",
                    "retrieval_backend": "synthetic", "truncated": False, "score": 0.8,
                }],
            }
            assert backend.calls == [("invented topic", 5)]
            assert str(tmp_path) not in json.dumps(response.structuredContent)

    anyio.run(check)


def test_protocol_rejects_hidden_arguments_before_gateway_call(tmp_path: Path) -> None:
    gateway, backend = _gateway(tmp_path)

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            for injected in ("path", "collection", "executable", "subcommand"):
                response = await client.call_tool(
                    "search_study", {"query": "invented", injected: r"C:\private\secret"}
                )
                assert response.isError is True
                assert response.structuredContent == {
                    "ok": False, "error": {"code": "INVALID_ARGUMENT", "message": "Invalid tool arguments"},
                }
                assert r"C:\private" not in str(response.content)
            assert backend.calls == []

    anyio.run(check)


def test_protocol_rejects_invalid_search_values_and_health_arguments(tmp_path: Path) -> None:
    gateway, backend = _gateway(tmp_path)

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            for args in ({"query": " "}, {"query": "a\x00b"}, {"query": "x", "limit": 0},
                         {"query": "x", "limit": True}, {"query": "x" * 501}, {}):
                response = await client.call_tool("search_study", args)
                assert response.isError is True
                assert response.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
            health = await client.call_tool("health_report", {"path": "private"})
            assert health.isError is True
            assert health.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
            assert backend.calls == []

    anyio.run(check)


def test_protocol_sanitizes_gateway_error_and_unexpected_exception() -> None:
    class FailingGateway:
        def health_report(self):
            raise RuntimeError(r"C:\private\token=secret")

        def search_study(self, query: str, limit: int):
            raise GatewayError("QMD_NOT_FOUND", r"C:\private\qmd.exe", r"C:\private\bad-id")

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(FailingGateway())) as client:
            search = await client.call_tool("search_study", {"query": "private query"})
            assert search.isError is True
            assert search.structuredContent == {
                "ok": False,
                "error": {"code": "QMD_NOT_FOUND", "message": "Study search executable is unavailable"},
            }
            health = await client.call_tool("health_report", {})
            assert health.isError is True
            assert health.structuredContent["error"]["code"] == "INTERNAL_ERROR"
            assert len(health.structuredContent["error"]["correlation_id"]) == 32
            for response in (search, health):
                combined = str(response.content) + str(response.structuredContent)
                assert r"C:\private" not in combined
                assert "private query" not in combined
                assert "token=secret" not in combined

    anyio.run(check)


def test_mcp_tool_calls_do_not_bind_an_application_listener(tmp_path: Path) -> None:
    gateway, _ = _gateway(tmp_path)

    async def check():
        # Windows creates a transient socketpair while starting asyncio itself.
        # This patch begins after the event loop is ready and covers our server.
        with patch.object(socket.socket, "bind", side_effect=AssertionError("application listener created")):
            async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
                assert (await client.call_tool("health_report", {})).isError is False
                assert (await client.call_tool("search_study", {"query": "invented"})).isError is False

    anyio.run(check)


def test_explicit_synthetic_config_builds_gateway_without_running_qmd(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "invented-study"
    root.mkdir()
    config_file = tmp_path / "local.toml"
    config_file.write_text(
        '[gateway]\nversion = "0.1.0"\n[study]\n'
        f'root = "{root.as_posix()}"\n'
        'qmd_executable = "missing-synthetic-qmd"\nqmd_collection = "studyvault"\n'
        'qmd_version = "2.8.3"\n[history]\nbackend = "not_configured"\n',
        encoding="utf-8",
    )
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("QMD ran")))
    gateway = load_gateway_from_config(config_file)
    report = gateway.health_report()
    assert report["study"] == {"configured": True, "root_exists": True, "readable": True}
    assert report["history"] == {"backend": "not_configured", "status": "not_configured"}
    assert not (tmp_path / "var").exists()
    assert not list(tmp_path.rglob("*.db"))


def test_runtime_snapshot_config_defers_source_access_until_search(tmp_path: Path) -> None:
    root = tmp_path / "invented-study"
    root.mkdir()
    config_file = tmp_path / "local.toml"
    config_file.write_text(
        '[gateway]\nversion = "0.1.0"\n[study]\n'
        f'root = "{root.as_posix()}"\nqmd_collection = "studyvault"\nqmd_version = "2.8.3"\n'
        '[study.qmd_runtime]\n'
        'node_executable = "C:/synthetic/node.exe"\n'
        'cli_entrypoint = "C:/synthetic/qmd.js"\n'
        'config = "C:/synthetic/index.yml"\n'
        'index = "C:/synthetic/index.sqlite"\n'
        '[history]\nbackend = "not_configured"\n',
        encoding="utf-8",
    )

    gateway = load_gateway_from_config(config_file)

    assert gateway.study_backend is not None
    assert gateway.study_backend.runtime_provider is not None
    assert gateway.health_report()["study"] == {"configured": True, "root_exists": True, "readable": True}


@pytest.mark.parametrize(
    ("version", "root"),
    [
        (r"C:\private\user", "C:/synthetic/study"),
        ("0.1.0", "relative-study"),
    ],
)
def test_local_config_rejects_unsafe_version_or_relative_root(tmp_path: Path, version: str, root: str) -> None:
    config_file = tmp_path / "local.toml"
    config_file.write_text(
        f'[gateway]\nversion = "{version.replace(chr(92), "/")}"\n'
        f'[study]\nroot = "{root}"\n'
        'qmd_executable = "synthetic-qmd"\nqmd_collection = "studyvault"\n'
        'qmd_version = "2.8.3"\n[history]\nbackend = "not_configured"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid local configuration"):
        load_gateway_from_config(config_file)
