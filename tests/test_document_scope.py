"""Native scope boundaries with authored synthetic documents only."""
from pathlib import Path

import anyio
import pytest
from jsonschema import validate
from mcp.shared.memory import create_connected_server_and_client_session

from cognivault.adapters.documents import DocumentInput, SQLiteDocumentStore
from cognivault.config import AppConfig
from cognivault.contracts import GatewayError
from cognivault.gateway import Gateway
from cognivault.transports.mcp_stdio import create_mcp_server


@pytest.fixture
def scoped(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    store = SQLiteDocumentStore(assets, tmp_path / "documents.db")
    a, b = store.ingest_documents([
        DocumentInput("Invented A", "text/plain", b"needle A1\fneedle A2\fneedle A3"),
        DocumentInput("Invented B", "text/plain", b"needle B1\fneedle B2")])
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, document_store=store,
                      capabilities=frozenset({"read"}), qmd_discoverable=lambda: False)
    return store, gateway, a.uri, b.uri


def test_native_intersection_before_pagination_and_legacy_dto(scoped):
    store, gateway, a, b = scoped
    old = gateway.search_documents("needle", 20)
    assert old["total"] == 5
    page = gateway.search_documents("needle", 1, 1, source_ids=[a], page_range=[1, 2])
    assert page["total"] == 2 and not page["has_more"]
    assert [(r["document_uri"], r["page_number"]) for r in page["results"]] == [(a, 2)]
    assert set(page["results"][0]) == set(old["results"][0])
    assert store.search("needle", source_ids=[a, a], page_range=[2, 3]).total == 2
    assert store.search("needle", source_ids=[b], page_range=[3, 999]).total == 0
    assert gateway.search_documents("needle", source_ids=["document://sha256/" + "0" * 64])["total"] == 0
    assert gateway.search_documents("absent", source_ids=[a])["total"] == 0
    assert store.search("needle", source_ids=[a] * 64).total == 3
    assert store.search("needle", page_range=[999, 999]).total == 0


INVALID = [{"source_ids": x} for x in ([], "document://sha256/" + "0" * 64,
    ["file:///secret"], [True], ["document://sha256/" + "A" * 64], ["document://sha256/" + "0" * 64 + "\n"],
    ["document://sha256/" + "0" * 64] * 65)] + [
    {"page_range": x} for x in ([], [1], [1, 2, 3], [True, 2], [1, 1000], [0, 1], [2, 1], [1.0, 2], "1,2")]


@pytest.mark.parametrize("kwargs", INVALID)
def test_adapter_and_gateway_strict_scope(scoped, kwargs):
    store, gateway, _, _ = scoped
    for search in (store.search, gateway.search_documents):
        with pytest.raises(GatewayError) as error:
            search("needle", **kwargs)
        assert error.value.code == "INVALID_ARGUMENT"


def test_scoping_cannot_bypass_global_capacity(scoped):
    store, gateway, a, _ = scoped
    # Existing chunk splitting contract; 999 pages per document, >2048 overall.
    for marker in ("C", "D", "E"):
        store.ingest_documents([DocumentInput(marker, "text/plain", ("\f".join(["needle " + marker] * 700)).encode())])
    for kwargs in ({}, {"source_ids": [a]}, {"page_range": [999, 999]},
                   {"source_ids": [a], "page_range": [1, 1]}):
        with pytest.raises(GatewayError) as error:
            gateway.search_documents("needle", **kwargs)
        assert error.value.code == "PAYLOAD_TOO_LARGE"


def test_inclusive_maximum_physical_page(scoped):
    store, gateway, _, _ = scoped
    book = store.ingest_documents([DocumentInput("Invented last page", "text/plain",
        ("\f".join(["filler"] * 998 + ["needle last"])).encode())])[0]
    result = gateway.search_documents("needle", source_ids=[book.uri], page_range=[999, 999])
    assert result["total"] == 1
    assert result["results"][0]["page_number"] == 999


def test_actual_mcp_schema_and_forced_calls(scoped):
    _, gateway, a, _ = scoped

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            tool = next(t for t in (await client.list_tools()).tools if t.name == "search_documents")
            props = tool.inputSchema["properties"]
            assert props["source_ids"]["maxItems"] == 64
            assert props["source_ids"]["minItems"] == 1
            assert props["page_range"]["minItems"] == props["page_range"]["maxItems"] == 2
            assert props["page_range"]["items"]["maximum"] == 999
            assert tool.annotations.readOnlyHint and tool.inputSchema["additionalProperties"] is False
            for args in ({"query": "needle"}, {"query": "needle", "source_ids": [a] * 64, "page_range": [1, 2]},
                         {"query": "needle", "page_range": [999, 999]}):
                validate(args, tool.inputSchema)
                response = await client.call_tool("search_documents", args)
                assert not response.isError
            for kwargs in INVALID + [{"chapter_ids": ["unknown"]}]:
                rejected = await client.call_tool("search_documents", {"query": "needle", **kwargs})
                assert rejected.isError
                assert rejected.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
            readonly = Gateway(gateway.config, None, document_store=scoped[0], capabilities=frozenset(),
                               qmd_discoverable=lambda: False)
            with pytest.raises(GatewayError) as error:
                readonly.search_documents("needle", source_ids=[a])
            assert error.value.code == "PERMISSION_DENIED"
        async with create_connected_server_and_client_session(create_mcp_server(readonly)) as client:
            assert "search_documents" not in {tool.name for tool in (await client.list_tools()).tools}
            forced = await client.call_tool("search_documents", {"query": "needle", "source_ids": [a]})
            assert forced.isError
            assert forced.structuredContent["error"]["code"] == "PERMISSION_DENIED"
    anyio.run(check)
