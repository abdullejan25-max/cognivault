"""History through the real in-process MCP protocol."""

import json
from pathlib import Path

import anyio
from mcp.shared.memory import create_connected_server_and_client_session
from jsonschema import validate
import pytest

from cognivault.adapters.history import SQLiteHistoryBackend
from cognivault.config import AppConfig
from cognivault.contracts import HistoryImportItem
from cognivault.gateway import Gateway
from cognivault.transports.mcp_stdio import create_mcp_server


def test_history_tools_roundtrip_and_reject_hidden_paths(tmp_path: Path) -> None:
    store = SQLiteHistoryBackend(tmp_path / "private-history.db")
    store.register_source("invented", "Invented export")
    item_id = store.import_items("invented", [HistoryImportItem(
        "entry-1", "chat-1", "user", "Synthetic circle theorem", "2026-01-02T00:00:00Z",
    )])[0]
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, store, qmd_discoverable=lambda: False)

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            for name in ("list_history_sources", "search_history", "fetch_history_item"):
                assert tools[name].annotations.readOnlyHint is True
                assert tools[name].inputSchema["additionalProperties"] is False
            validate({"query": "circle", "source_id": "invented", "limit": 2, "offset": 0},
                     tools["search_history"].inputSchema)
            sources = await client.call_tool("list_history_sources", {})
            assert sources.structuredContent == {"ok": True, "sources": [
                {"source_id": "invented", "label": "Invented export", "item_count": 1},
            ]}
            search = await client.call_tool("search_history", {"query": "circle", "limit": 2})
            assert search.structuredContent["ok"] is True
            assert search.structuredContent["total"] == 1
            assert search.structuredContent["results"][0]["item_id"] == item_id
            fetched = await client.call_tool("fetch_history_item", {"item_id": item_id})
            assert fetched.structuredContent["item"]["content"] == "Synthetic circle theorem"
            for name, args in (
                ("list_history_sources", {"path": "C:/private"}),
                ("search_history", {"query": "circle", "limit": 21}),
                ("search_history", {"query": "circle", "source_id": "C:/private"}),
                ("fetch_history_item", {"item_id": "C:/private"}),
            ):
                rejected = await client.call_tool(name, args)
                assert rejected.isError is True
                assert rejected.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
                assert "C:/private" not in str(rejected.structuredContent)
            for response in (sources, search, fetched):
                assert str(tmp_path) not in json.dumps(response.structuredContent)

    anyio.run(check)


def test_projection_snapshot_pages_history_only_with_projection_capability(tmp_path: Path) -> None:
    store = SQLiteHistoryBackend(tmp_path / "private-history.db")
    store.register_source("synthetic-projection", "Synthetic projection")
    item_id = store.import_items("synthetic-projection", [HistoryImportItem(
        "entry-1", "chat-1", "user", "Synthetic projection record", "2026-01-02T00:00:00Z",
    )])[0]
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, store,
                      capabilities=frozenset({"projection"}), qmd_discoverable=lambda: False)

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert set(tools) == {"projection_snapshot"}
            validate({"domain": "history", "operation": "records", "snapshot_token": "history-v1:1:1",
                      "source_id": "synthetic-projection", "cursor": 0, "limit": 20},
                     tools["projection_snapshot"].inputSchema)
            started = await client.call_tool("projection_snapshot", {
                "domain": "history", "operation": "begin",
            })
            token = started.structuredContent["snapshot_token"]
            sources = await client.call_tool("projection_snapshot", {
                "domain": "history", "operation": "sources", "snapshot_token": token,
            })
            source_id = sources.structuredContent["sources"][0]["source_id"]
            records = await client.call_tool("projection_snapshot", {
                "domain": "history", "operation": "records", "snapshot_token": token,
                "source_id": source_id,
            })
            assert started.structuredContent["total_records"] == 1
            assert records.structuredContent["items"][0]["item_id"] == item_id
            assert records.structuredContent["items"][0]["content"] == "Synthetic projection record"

    anyio.run(check)


def test_unconfigured_history_tools_fail_closed(tmp_path: Path) -> None:
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, qmd_discoverable=lambda: False)

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            for name, args in (
                ("list_history_sources", {}),
                ("search_history", {"query": "synthetic"}),
                ("fetch_history_item", {"item_id": "history:" + "0" * 64}),
            ):
                result = await client.call_tool(name, args)
                assert result.isError is True
                assert result.structuredContent["error"]["code"] == "HISTORY_UNAVAILABLE"

    anyio.run(check)


def test_history_output_redacts_complete_local_paths_and_preserves_links(tmp_path: Path) -> None:
    store = SQLiteHistoryBackend(tmp_path / "private-history.db")
    store.register_source("invented", "Invented export")
    item_id = store.import_items("invented", [HistoryImportItem(
        "entry-1", "chat-1", "user",
        "Open C:/Users/fictional/My Folder/private.db, "
        r"C:\Users\fictional\My Folder\private.db, "
        r"\\server\share\My Folder\private.db, "
        "and /secret.db. "
        "Keep https://example.org/a.md and relative notes/file.md.",
        "2026-01-02T00:00:00Z",
    )])[0]
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, store, qmd_discoverable=lambda: False)
    direct = gateway.fetch_history_item(item_id)["item"]["content"]
    assert "[local path redacted]" in direct
    assert "Folder/private.db" not in direct
    assert r"Folder\private.db" not in direct
    assert r"\\server\share" not in direct
    assert "/secret.db" not in direct
    assert "https://example.org/a.md" in direct
    assert "notes/file.md" in direct

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            fetched = await client.call_tool("fetch_history_item", {"item_id": item_id})
            searched = await client.call_tool("search_history", {"query": "Open"})
            for response in (fetched, searched):
                public = json.dumps(response.structuredContent)
                assert "C:/Users/fictional/My Folder/private.db" not in public
                assert "Folder/private.db" not in public
                assert "Folder\\\\private.db" not in public
                assert "/secret.db" not in public
                assert "https://example.org/a.md" in public

    anyio.run(check)


@pytest.mark.parametrize(("path_text", "sensitive_tail"), [
    ("C:/Users/fictional/My Folder/private", "Folder/private"),
    ("C:/", "C:/"),
    ("/secret", "/secret"),
    ("/", "/"),
    ("C:/Users/x/archive.tar.gz", "archive.tar.gz"),
    ("C:/Users/a/My,Folder/private", ",Folder/private"),
    ("C:/Users/a/My;Folder/private", ";Folder/private"),
    ("C:/Users/a/private file.db", "file.db"),
    ("C:/Users/a/My Folder/private file.db", "file.db"),
    ("/home/a/private file.db", "file.db"),
    ("C:/Users/a/My Folder", "Folder"),
    (r"\\server\share\My Folder", "Folder"),
    ("/home/a/My Folder", "Folder"),
    (r"\\server\share\My Folder\private", r"Folder\private"),
])
def test_gateway_redacts_entire_absolute_path_token_without_suffix(
    tmp_path: Path, path_text: str, sensitive_tail: str,
) -> None:
    store = SQLiteHistoryBackend(tmp_path / "private-history.db")
    store.register_source("invented", "Invented export")
    item_id = store.import_items("invented", [HistoryImportItem(
        "entry-1", "chat-1", "user", path_text, "2026-01-02T00:00:00Z",
    )])[0]
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, store, qmd_discoverable=lambda: False)
    public = gateway.fetch_history_item(item_id)["item"]["content"]
    assert public == "[local path redacted]"
    assert path_text not in public
    assert sensitive_tail not in public


def test_mcp_redacts_multiextension_path_but_preserves_url_and_relative_text(tmp_path: Path) -> None:
    store = SQLiteHistoryBackend(tmp_path / "private-history.db")
    store.register_source("invented", "Invented export")
    item_id = store.import_items("invented", [HistoryImportItem(
        "entry-1", "chat-1", "user",
        "C:/Users/x/archive.tar.gz https://example.org/lesson and notes/file.md",
        "2026-01-02T00:00:00Z",
    )])[0]
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, store, qmd_discoverable=lambda: False)

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            result = await client.call_tool("fetch_history_item", {"item_id": item_id})
            text = result.structuredContent["item"]["content"]
            assert "archive.tar.gz" not in text
            assert ".gz" not in text
            assert "https://example.org/lesson" in text
            assert "notes/file.md" in text

    anyio.run(check)


def test_mcp_preserves_prose_and_relative_reference_after_absolute_path(tmp_path: Path) -> None:
    store = SQLiteHistoryBackend(tmp_path / "private-history.db")
    store.register_source("invented", "Invented export")
    item_id = store.import_items("invented", [HistoryImportItem(
        "entry-1", "chat-1", "user", "C:/secret and notes/file.md",
        "2026-01-02T00:00:00Z",
    )])[0]
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, store, qmd_discoverable=lambda: False)
    assert gateway.fetch_history_item(item_id)["item"]["content"] == (
        "[local path redacted] and notes/file.md"
    )

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            result = await client.call_tool("fetch_history_item", {"item_id": item_id})
            assert result.structuredContent["item"]["content"] == (
                "[local path redacted] and notes/file.md"
            )

    anyio.run(check)
