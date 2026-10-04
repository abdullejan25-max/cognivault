"""Synthetic document workflow through the real MCP client boundary."""

import base64
from io import BytesIO
import json
from pathlib import Path

import anyio
import pytest
from pypdf import PdfWriter
from jsonschema import validate
from mcp.shared.memory import create_connected_server_and_client_session

from cognivault.adapters.documents import DocumentInput, SQLiteDocumentStore
from cognivault.config import AppConfig
from cognivault.gateway import Gateway
import cognivault.gateway as gateway_module
from cognivault.contracts import GatewayError
from cognivault.transports.mcp_stdio import create_mcp_server
import cognivault.adapters.documents as documents_module


def test_document_mcp_ingest_search_fetch_and_safe_errors(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    store = SQLiteDocumentStore(root, tmp_path / "private.db")
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, document_store=store,
                      capabilities=frozenset({"read", "write", "ingest"}),
                      qmd_discoverable=lambda: False)
    body = b"# Synthetic\nTriangle proof.\f# Second\nCircle proof."

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert tools["fetch_document_page"].inputSchema["properties"]["page_number"]["maximum"] == 999
            for name in ("register_asset", "ingest_documents", "search_documents",
                         "fetch_document", "fetch_document_page", "fetch_asset"):
                assert tools[name].inputSchema["additionalProperties"] is False
            assert tools["ingest_documents"].annotations.readOnlyHint is False
            assert tools["search_documents"].annotations.readOnlyHint is True
            payload = {"documents": [{"title": "Invented lesson", "media_type": "text/plain",
                                      "content_base64": base64.b64encode(body).decode("ascii")}]}
            validate(payload, tools["ingest_documents"].inputSchema)
            ingested = await client.call_tool("ingest_documents", payload)
            assert ingested.isError is not True
            doc = ingested.structuredContent["documents"][0]
            assert doc["page_count"] == 2
            found = await client.call_tool("search_documents", {"query": "proof"})
            assert found.structuredContent["total"] == 2
            assert found.structuredContent["results"][0]["chunk_uri"].startswith("chunk://sha256/")
            fetched = await client.call_tool("fetch_document", {"document_uri": doc["document_uri"]})
            assert fetched.structuredContent["document"]["title"] == "Invented lesson"
            page = await client.call_tool("fetch_document_page", {"document_uri": doc["document_uri"],
                                                                "page_number": 2})
            assert page.structuredContent["page"]["text"] == "# Second\nCircle proof."
            asset = await client.call_tool("fetch_asset", {"asset_uri": doc["asset_uri"],
                                                           "offset": 0, "length": 16})
            assert base64.b64decode(asset.structuredContent["content_base64"]) == body[:16]
            for name, args in (
                ("ingest_documents", {"documents": [{"title": "bad", "media_type": "text/plain",
                                                    "content_base64": "!!!!"}]}),
                ("ingest_documents", {"documents": [{"title": "bad", "media_type": [],
                                                    "content_base64": base64.b64encode(body).decode("ascii")}]}),
                ("ingest_documents", {"documents": [{"title": "bad", "media_type": "application/pdf",
                                                    "content_base64": base64.b64encode(b"%PDF-1.4").decode("ascii"),
                                                    "supplied_pages": ["Invented"],
                                                    "supplied_page_origin": []}]}),
                ("fetch_document", {"document_uri": str(tmp_path)}),
                ("fetch_asset", {"asset_uri": doc["asset_uri"], "length": 65_537}),
            ):
                rejected = await client.call_tool(name, args)
                assert rejected.isError is True
                assert rejected.structuredContent["error"]["code"] != "INTERNAL_ERROR"
                assert str(tmp_path) not in json.dumps(rejected.structuredContent)
            too_many = await client.call_tool("ingest_documents", {"documents": payload["documents"] * 17})
            assert too_many.isError is True
            assert too_many.structuredContent["error"]["code"] == "PAYLOAD_TOO_LARGE"
            for response in (ingested, found, fetched, page, asset):
                assert str(tmp_path) not in json.dumps(response.structuredContent)

    anyio.run(check)


def test_mcp_can_resume_bounded_ocr_and_fetch_its_source_page(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    store = SQLiteDocumentStore(root, tmp_path / "private.db")
    output = BytesIO()
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=200, height=300)
    writer.write(output)
    scan = output.getvalue()
    monkeypatch.setattr(documents_module, "_ocr_runtime_available", lambda: False)
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, document_store=store,
                      capabilities=frozenset({"read", "ingest"}))

    async def check() -> None:
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            ingested = await client.call_tool("ingest_documents", {"documents": [{
                "title": "Synthetic scanned page", "media_type": "application/pdf",
                "content_base64": base64.b64encode(scan).decode("ascii"),
            }]})
            document = ingested.structuredContent["documents"][0]
            candidates = await client.call_tool("list_document_ocr_candidates", {
                "document_uri": document["document_uri"],
                "limit": 1,
            })
            assert candidates.structuredContent["page_numbers"] == [1]
            assert candidates.structuredContent["has_more"] is True
            monkeypatch.setattr(documents_module, "_ocr_runtime_available", lambda: True)
            monkeypatch.setattr(documents_module, "_ocr_pdf_pages",
                                lambda _data, numbers: {number: "Synthetic OCR discovery phrase"
                                                       for number in numbers})
            processed = await client.call_tool("process_document_ocr_pages", {
                "document_uri": document["document_uri"], "page_numbers": [1],
            })
            assert processed.structuredContent["processed_pages"] == [1]
            results = await client.call_tool("search_documents", {"query": "discovery phrase"})
            item = results.structuredContent["results"][0]
            assert item["document_uri"] == document["document_uri"]
            assert item["page_number"] == 1
            assert item["page_uri"] == document["document_uri"] + "/page/1"
            assert item["source_asset_uri"] == document["asset_uri"]
            assert item["text_origin"] == "pdf_ocr_derived"
            assert item["visual_resource_uri"] == document["document_uri"] + "/page/1/image"
            page = await client.read_resource(document["document_uri"] + "/page/1")
            assert page.contents[0].text == "Synthetic OCR discovery phrase"
            assert page.contents[0].meta["text_origin"] == "pdf_ocr_derived"
            assert page.contents[0].meta["source_asset_uri"] == document["asset_uri"]
            denied_gateway = Gateway(AppConfig("0.1.0", tmp_path), None, document_store=store,
                                     capabilities=frozenset({"read"}))
        async with create_connected_server_and_client_session(create_mcp_server(denied_gateway)) as client:
            denied = await client.call_tool("process_document_ocr_pages", {
                "document_uri": document["document_uri"], "page_numbers": [1],
            })
            assert denied.structuredContent["error"]["code"] == "PERMISSION_DENIED"

    anyio.run(check)


def test_unconfigured_document_tools_fail_closed(tmp_path: Path) -> None:
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, qmd_discoverable=lambda: False)

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            result = await client.call_tool("search_documents", {"query": "invented"})
            assert result.isError is True
            assert result.structuredContent["error"]["code"] == "STORAGE_UNAVAILABLE"

    anyio.run(check)


def test_ocr_candidate_query_rejects_invalid_gateway_bounds(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    store = SQLiteDocumentStore(root, tmp_path / "private.db")
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, document_store=store,
                      capabilities=frozenset({"read"}))
    document = store.ingest_documents([DocumentInput("Synthetic", "text/plain", b"Synthetic page")])[0]

    with pytest.raises(GatewayError) as error:
        gateway.list_document_ocr_candidates(document.uri, "one", 0)
    assert error.value.code == "INVALID_ARGUMENT"


def test_file_ingest_uses_configured_relative_root_and_rejects_escape(tmp_path: Path) -> None:
    from cognivault.contracts import GatewayError

    source_root = tmp_path / "sources"
    asset_root = tmp_path / "assets"
    source_root.mkdir()
    asset_root.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)
    payload = output.getvalue()
    (source_root / "lesson.pdf").write_bytes(payload)
    store = SQLiteDocumentStore(asset_root, tmp_path / "private.db")
    gateway = Gateway(AppConfig("0.1.0", tmp_path, asset_root=asset_root,
                                asset_ingest_root=source_root), None,
                      document_store=store, capabilities=frozenset({"read", "ingest"}))
    with pytest.raises(GatewayError) as traversal:
        gateway.ingest_document_file("../lesson.pdf")
    assert traversal.value.code == "INVALID_ARGUMENT"
    with pytest.raises(GatewayError) as absolute:
        gateway.ingest_document_file(str(source_root / "lesson.pdf"))
    assert absolute.value.code == "INVALID_ARGUMENT"
    with pytest.raises(GatewayError) as no_ingest:
        Gateway(AppConfig("0.1.0", tmp_path, asset_ingest_root=source_root), None,
                document_store=store).ingest_document_file("lesson.pdf")
    assert no_ingest.value.code == "PERMISSION_DENIED"
    assert str(source_root) not in json.dumps(gateway.ingest_document_file("lesson.pdf"))


def test_file_ingest_verifies_actual_open_handle_target(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "sources"
    asset_root = tmp_path / "assets"
    source_root.mkdir()
    asset_root.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)
    (source_root / "lesson.pdf").write_bytes(output.getvalue())
    store = SQLiteDocumentStore(asset_root, tmp_path / "private.db")
    gateway = Gateway(AppConfig("0.1.0", tmp_path, asset_root=asset_root,
                                asset_ingest_root=source_root), None,
                      document_store=store, capabilities=frozenset({"read", "ingest"}))
    outside = tmp_path / "outside.pdf"
    outside.write_bytes((source_root / "lesson.pdf").read_bytes())
    monkeypatch.setattr(gateway_module, "_opened_file_path", lambda _stream: outside)
    with pytest.raises(GatewayError) as error:
        gateway.ingest_document_file("lesson.pdf")
    assert error.value.code == "OUTSIDE_ALLOWLIST"
    assert not (tmp_path / "private.db").exists()


def test_file_ingest_tool_is_discovered_only_when_root_is_configured(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    source_root = tmp_path / "sources"
    root.mkdir()
    source_root.mkdir()
    store = SQLiteDocumentStore(root, tmp_path / "private.db")
    gateway = Gateway(AppConfig("0.1.0", tmp_path, asset_root=root,
                                asset_ingest_root=source_root), None, document_store=store,
                      capabilities=frozenset({"read", "ingest"}))
    default_gateway = Gateway(AppConfig("0.1.0", tmp_path), None)

    async def check() -> None:
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            assert "ingest_document_file" in tools
            result = await client.call_tool("ingest_document_file", {"relative_path": "../secret.pdf"})
            assert result.isError is True
            assert result.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
        async with create_connected_server_and_client_session(create_mcp_server(default_gateway)) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            assert "ingest_document_file" not in tools

    anyio.run(check)


def test_image_file_registration_preserves_large_source_bytes_and_provenance(tmp_path: Path) -> None:
    source_root = tmp_path / "inbox"
    asset_root = tmp_path / "assets"
    source_root.mkdir()
    asset_root.mkdir()
    jpeg = b"\xff\xd8" + b"synthetic-jpeg-payload" * 170_000
    png = b"\x89PNG\r\n\x1a\n" + b"synthetic-png-payload" * 180_000
    jpeg_two = b"\xff\xd8" + b"second-synthetic-jpeg-payload" * 130_000
    png_two = b"\x89PNG\r\n\x1a\n" + b"second-synthetic-png-payload" * 140_000
    photos = (("phone-1.jpg", "image/jpeg", jpeg, "Codex"),
              ("phone-2.png", "image/png", png, "ChatGPT"),
              ("phone-3.jpg", "image/jpeg", jpeg_two, "OtherAgent"),
              ("phone-4.png", "image/png", png_two, "Codex"))
    for name, _, content, _ in photos:
        (source_root / name).write_bytes(content)
    store = SQLiteDocumentStore(asset_root, tmp_path / "private.db")
    gateway = Gateway(AppConfig("0.1.0", tmp_path, asset_root=asset_root,
                                asset_ingest_root=source_root), None,
                      document_store=store, capabilities=frozenset({"read", "ingest"}))

    async def check() -> None:
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            tool = {item.name: item for item in (await client.list_tools()).tools}["register_asset"]
            schema = tool.inputSchema
            assert "relative_path" in schema["properties"]
            assert schema["oneOf"]
            records = []
            for relative_path, media_type, content, name in photos:
                arguments = {"media_type": media_type, "relative_path": relative_path,
                             "provenance": {"reported_agent": name,
                                            "reported_client": "synthetic-host",
                                            "run_id": "image-ingest-test"}}
                validate(arguments, schema)
                result = await client.call_tool("register_asset", arguments)
                assert result.isError is False
                asset = result.structuredContent["asset"]
                records.append(asset)
                assert asset["size"] == len(content)
                import hashlib
                digest = hashlib.sha256(content).hexdigest()
                assert asset["sha256"] == digest
                assert asset["asset_uri"] == "asset://sha256/" + digest
                provenance = asset["write_provenance"]
                assert provenance["data_origin"] == "source"
                assert provenance["actor_type"] == "external_client"
                assert provenance["reported_agent"] == name
                assert provenance["reported_client"] == "synthetic-host"
                assert provenance["identity_trust"] == "reported"
                assert provenance["recorded_at"].endswith("Z")
                assert (asset_root / digest[:2] / digest).read_bytes() == content
            assert records[0]["asset_uri"] != records[1]["asset_uri"]

            for invalid in (
                {"media_type": "image/jpeg", "content_base64": "eHg=",
                 "relative_path": "phone-1.jpg"},
                {"media_type": "image/jpeg", "relative_path": "../phone-1.jpg"},
                {"media_type": "image/jpeg", "relative_path": str(source_root / "phone-1.jpg")},
                {"media_type": "image/jpeg", "relative_path": "missing.jpg"},
                {"media_type": "image/png", "relative_path": "phone-1.jpg"},
            ):
                result = await client.call_tool("register_asset", invalid)
                assert result.isError is True
                assert result.structuredContent["error"]["code"] != "INTERNAL_ERROR"

            too_large = source_root / "too-large.jpg"
            with too_large.open("wb") as stream:
                stream.write(b"\xff\xd8")
                stream.truncate(documents_module.MAX_IMAGE_BYTES + 1)
            oversized = await client.call_tool("register_asset", {
                "media_type": "image/jpeg", "relative_path": "too-large.jpg",
            })
            assert oversized.isError is True
            assert oversized.structuredContent["error"]["code"] == "PAYLOAD_TOO_LARGE"

    anyio.run(check)


def test_image_file_registration_verifies_actual_open_handle_target(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "inbox"
    asset_root = tmp_path / "assets"
    source_root.mkdir()
    asset_root.mkdir()
    (source_root / "image.jpg").write_bytes(b"\xff\xd8synthetic")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"\xff\xd8synthetic")
    store = SQLiteDocumentStore(asset_root, tmp_path / "private.db")
    gateway = Gateway(AppConfig("0.1.0", tmp_path, asset_root=asset_root,
                                asset_ingest_root=source_root), None,
                      document_store=store, capabilities=frozenset({"read", "ingest"}))
    monkeypatch.setattr(gateway_module, "_opened_file_path", lambda _stream: outside)

    with pytest.raises(GatewayError) as error:
        gateway.register_asset_from_file("image.jpg", "image/jpeg")
    assert error.value.code == "OUTSIDE_ALLOWLIST"
    assert not (tmp_path / "private.db").exists()


def test_large_image_file_registration_requires_explicit_root_and_ingest_capability(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    store = SQLiteDocumentStore(root, tmp_path / "private.db")
    unconfigured = Gateway(AppConfig("0.1.0", tmp_path), None, document_store=store,
                           capabilities=frozenset({"read", "ingest"}))
    denied = Gateway(AppConfig("0.1.0", tmp_path, asset_ingest_root=tmp_path), None,
                     document_store=store, capabilities=frozenset({"read"}))

    async def check() -> None:
        async with create_connected_server_and_client_session(create_mcp_server(unconfigured)) as client:
            tool = {item.name: item for item in (await client.list_tools()).tools}["register_asset"]
            assert "relative_path" not in tool.inputSchema["properties"]
            result = await client.call_tool("register_asset", {
                "media_type": "image/jpeg", "relative_path": "image.jpg",
            })
            assert result.isError is True
        async with create_connected_server_and_client_session(create_mcp_server(denied)) as client:
            result = await client.call_tool("register_asset", {
                "media_type": "image/jpeg", "relative_path": "image.jpg",
            })
            assert result.isError is True
            assert result.structuredContent["error"]["code"] == "PERMISSION_DENIED"

    anyio.run(check)


def test_page_result_is_bounded_after_path_redaction(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    store = SQLiteDocumentStore(root, tmp_path / "private.db")
    source = ("/a and " * 10_000).encode()
    from cognivault.adapters.documents import DocumentInput
    doc = store.ingest_documents([DocumentInput("Invented", "text/plain", source)])[0]
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, document_store=store,
                      qmd_discoverable=lambda: False)
    result = gateway.fetch_document_page(doc.uri, 1)["page"]
    assert len(result["text"].encode("utf-8")) <= 100_000
    assert result["truncated"] is True
    assert "/a" not in result["text"]


def test_forward_slash_unc_paths_are_redacted_in_document_public_results(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    store = SQLiteDocumentStore(root, tmp_path / "private.db")
    from cognivault.adapters.documents import DocumentInput
    private = "//server/share/private/report.txt"
    text = ("# " + private + "\nSynthetic note " + private +
            " and https://example.org/a.txt and notes/file.md")
    doc = store.ingest_documents([DocumentInput(private, "text/plain", text.encode())])[0]
    gateway = Gateway(AppConfig("0.1.0", tmp_path), None, document_store=store,
                      qmd_discoverable=lambda: False)

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            results = [
                await client.call_tool("fetch_document", {"document_uri": doc.uri}),
                await client.call_tool("fetch_document_page", {"document_uri": doc.uri, "page_number": 1}),
                await client.call_tool("search_documents", {"query": "Synthetic"}),
                await client.call_tool("fetch_asset", {"asset_uri": doc.asset_uri, "length": 8}),
            ]
            for result in results:
                public = json.dumps(result.structuredContent)
                assert private not in public
                assert "private/report.txt" not in public
            page = results[1].structuredContent["page"]["text"]
            assert "[local path redacted]" in page
            assert "https://example.org/a.txt" in page
            assert "notes/file.md" in page

    anyio.run(check)
