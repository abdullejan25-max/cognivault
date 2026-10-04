"""MCP resource templates expose bounded source content with provenance."""

import anyio
import base64
from pathlib import Path
import pytest
from pypdf import PdfWriter
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session

from cognivault.adapters.documents import DocumentInput, SQLiteDocumentStore
from cognivault.config import AppConfig
from cognivault.gateway import Gateway
from cognivault.transports.mcp_stdio import create_mcp_server


def test_document_page_and_asset_resource_retrieval_is_bounded(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    store = SQLiteDocumentStore(root, tmp_path / "private.db")
    page = "Synthetic lesson page"
    document = store.ingest_documents([DocumentInput("Invented", "text/plain", page.encode())])[0]
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, document_store=store,
                      capabilities=frozenset({"read", "ingest"}))

    async def check() -> None:
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            templates = (await client.list_resource_templates()).resourceTemplates
            assert {item.name for item in templates} == {"document_page", "document_page_image", "asset"}
            contents = (await client.read_resource(document.uri + "/page/1")).contents
            expected_provenance = gateway.fetch_document_page(document.uri, 1)["page"]["write_provenance"]
            assert contents[0].text == page
            assert contents[0].meta == {"document_uri": document.uri, "page_number": 1,
                                        "source_asset_uri": document.asset_uri,
                                        "text_origin": "source_text",
                                        "text_layer_status": "not_applicable",
                                        "write_provenance": expected_provenance,
                                        "visual_resource_uri": document.uri + "/page/1/image",
                                        "truncated": False}
            image_data = b"\x89PNG\r\n\x1a\nsynthetic image bytes"
            image_record = store.register_asset(image_data, "image/png")
            asset = (await client.read_resource(image_record.uri)).contents[0]
            assert base64.b64decode(asset.blob) == image_data
            assert asset.mimeType == "image/png"
            assert asset.meta["asset_uri"] == image_record.uri
            assert asset.meta["write_provenance"]["data_origin"] == "source"

            oversized = store.register_asset(b"x" * 65_537, "application/octet-stream")
            with pytest.raises(McpError) as error:
                await client.read_resource(oversized.uri)
            assert "size limit" in str(error.value).lower()
            with pytest.raises(McpError) as missing:
                await client.read_resource("asset://sha256/" + "0" * 64)
            assert "not found" in str(missing.value).lower()

    anyio.run(check)

    read_disabled = Gateway(AppConfig("0.1.0", tmp_path), None, document_store=store,
                            capabilities=frozenset())

    async def denied() -> None:
        async with create_connected_server_and_client_session(create_mcp_server(read_disabled)) as client:
            assert (await client.list_resource_templates()).resourceTemplates == []
            with pytest.raises(McpError) as error:
                await client.read_resource(document.uri + "/page/1")
            assert "not permitted" in str(error.value).lower()

    anyio.run(denied)


def test_resource_unexpected_errors_are_sanitized(tmp_path: Path) -> None:
    class FailingGateway(Gateway):
        def fetch_asset(self, asset_uri: str, offset: int = 0, length: int = 65_536) -> dict:
            raise RuntimeError("private path C:/Users/example/secret.db")

    gateway = FailingGateway(AppConfig("0.1.0", tmp_path), None,
                             capabilities=frozenset({"read"}))

    async def check() -> None:
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            with pytest.raises(McpError) as error:
                await client.read_resource("asset://sha256/" + "0" * 64)
            assert "private path" not in str(error.value)
            assert "secret.db" not in str(error.value)
            assert "Local gateway failed" in str(error.value)

    anyio.run(check)


def test_document_page_image_is_a_bounded_mcp_binary_resource(tmp_path: Path) -> None:
    pytest.importorskip("pymupdf")
    root = tmp_path / "assets"
    root.mkdir()
    store = SQLiteDocumentStore(root, tmp_path / "private.db")
    output = __import__("io").BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.write(output)
    document = store.ingest_documents([
        DocumentInput("Synthetic visual page", "application/pdf", output.getvalue()),
    ])[0]
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, document_store=store,
                      capabilities=frozenset({"read", "ingest"}))

    async def check() -> None:
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            templates = (await client.list_resource_templates()).resourceTemplates
            image_template = next(item for item in templates if item.name == "document_page_image")
            assert image_template.mimeType == "image/png"
            resource = await client.read_resource(document.uri + "/page/1/image")
            image = resource.contents[0]
            data = base64.b64decode(image.blob)
            assert image.mimeType == "image/png"
            assert data.startswith(b"\x89PNG\r\n\x1a\n")
            assert image.meta["document_uri"] == document.uri
            assert image.meta["page_number"] == 1
            assert image.meta["source_asset_uri"] == document.asset_uri
            assert image.meta["rendered_from"] == "original_pdf_page"
            assert image.meta["write_provenance"]["data_origin"] == "deterministic_derived"
            assert len(data) <= 4 * 1024 * 1024

    anyio.run(check)
