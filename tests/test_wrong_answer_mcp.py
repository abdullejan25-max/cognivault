"""Synthetic Wrong Answer workflow through the public MCP protocol."""

import hashlib
from pathlib import Path

import anyio
from mcp.shared.memory import create_connected_server_and_client_session

from test_wrong_answers import analysis, gateway_with_sources
from cognivault.adapters.documents import SQLiteDocumentStore
from cognivault.config import AppConfig
from cognivault.gateway import Gateway
from cognivault.transports.mcp_stdio import create_mcp_server


def test_mcp_source_analysis_and_retrieval_share_one_contract(tmp_path):
    gateway, document_uri, asset_uri, study_id = gateway_with_sources(tmp_path)

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            expected = {"register_wrong_answer_source", "get_wrong_answer_bundle",
                        "search_wrong_answers", "save_wrong_answer_analysis",
                        "update_wrong_answer_analysis"}
            assert expected <= set(tools)
            assert tools["register_wrong_answer_source"].annotations.readOnlyHint is False
            assert tools["save_wrong_answer_analysis"].annotations.readOnlyHint is False
            assert tools["get_wrong_answer_bundle"].annotations.readOnlyHint is True

            original = await client.call_tool("fetch_asset", {"asset_uri": asset_uri})
            assert original.structuredContent["content_base64"]
            extracted = await client.call_tool("fetch_document_page", {
                "document_uri": document_uri, "page_number": 1,
            })
            assert extracted.structuredContent["page"]["text"] == "Question 1: 1/2 + 1/3"
            assert extracted.structuredContent["page"]["text_origin"] == "supplied_ocr_derived"

            registered = await client.call_tool("register_wrong_answer_source", {
                "source_uri": document_uri, "page_number": 1,
                "question_text": "1/2 + 1/3", "student_answer": "2/5",
                "provenance": {"reported_agent": "Codex", "reported_client": "synthetic-mcp"},
            })
            source = registered.structuredContent["source"]
            assert source["text_origin"] == "supplied_ocr_derived"

            saved = await client.call_tool("save_wrong_answer_analysis", {
                "source_id": source["source_id"], "analysis": analysis(),
                "source_refs": [document_uri, asset_uri], "study_relations": [study_id],
                "idempotency_key": "mcp-analysis-1",
                "provenance": {"reported_agent": "Codex", "reported_client": "synthetic-mcp", "run_id": "test-run"},
            })
            assert saved.isError is False
            assert saved.structuredContent["analysis"]["generated_by_agent"] is True
            analysis_provenance = saved.structuredContent["analysis"]["write_provenance"]
            assert analysis_provenance["reported_agent"] == "Codex"
            assert analysis_provenance["identity_trust"] == "reported"

            updated = await client.call_tool("update_wrong_answer_analysis", {
                "source_id": source["source_id"],
                "analysis": {**analysis(), "review_advice": "Synthetic follow-up"},
                "source_refs": [document_uri, asset_uri], "study_relations": [study_id],
                "idempotency_key": "mcp-analysis-2", "expected_version": 1,
                "provenance": {"reported_agent": "Codex", "reported_client": "synthetic-mcp", "run_id": "test-run-2"},
            })
            assert updated.isError is False
            assert updated.structuredContent["analysis"]["supersedes_analysis_id"] == \
                saved.structuredContent["analysis"]["analysis_id"]

            fetched = await client.call_tool("get_wrong_answer_bundle", {
                "source_id": source["source_id"],
            })
            assert fetched.structuredContent["bundle"]["source"] == source
            assert fetched.structuredContent["bundle"]["analyses"][0]["study_relations"] == [study_id]
            searched = await client.call_tool("search_wrong_answers", {"query": "denominator"})
            assert searched.structuredContent["results"][0]["source_id"] == source["source_id"]

            unsafe = await client.call_tool("save_wrong_answer_analysis", {
                "source_id": source["source_id"], "analysis": analysis(),
                "source_refs": [document_uri], "study_relations": ["study:../outside.md"],
                "idempotency_key": "mcp-analysis-unsafe",
            })
            assert unsafe.isError is True
            assert unsafe.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
            unknown = await client.call_tool("register_wrong_answer_source", {
                "source_uri": "document://sha256/" + "0" * 64,
                "question_text": "Synthetic question", "student_answer": "Synthetic answer",
            })
            assert unknown.isError is True
            assert unknown.structuredContent["error"]["code"] == "RESOURCE_NOT_FOUND"
            injected = await client.call_tool("search_wrong_answers", {"query": "denominator", "path": "C:/private"})
            assert injected.isError is True
            assert injected.structuredContent["error"]["code"] == "INVALID_ARGUMENT"

    anyio.run(check)


def test_projection_snapshot_pages_wrong_answers_only_with_projection_capability(tmp_path):
    gateway, document_uri, _, study_id = gateway_with_sources(tmp_path)
    source = gateway.register_wrong_answer_source(document_uri, "Synthetic question", "Synthetic answer")["source"]
    gateway.save_wrong_answer_analysis(
        source["source_id"], analysis(), [document_uri], [study_id], "projection-mcp-analysis", 0,
    )
    gateway.capabilities = frozenset({"projection"})

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert set(tools) == {"projection_snapshot"}
            started = await client.call_tool("projection_snapshot", {
                "domain": "wrong_answers", "operation": "begin",
            })
            token = started.structuredContent["snapshot_token"]
            sources = await client.call_tool("projection_snapshot", {
                "domain": "wrong_answers", "operation": "sources", "snapshot_token": token,
            })
            source_id = sources.structuredContent["sources"][0]["source_id"]
            records = await client.call_tool("projection_snapshot", {
                "domain": "wrong_answers", "operation": "records",
                "snapshot_token": token, "source_id": source_id,
            })
            assert started.structuredContent["total_sources"] == 1
            assert started.structuredContent["total_records"] == 1
            assert records.structuredContent["analyses"][0]["version"] == 1
            assert records.structuredContent["analyses"][0]["supersedes_analysis_id"] is None

    anyio.run(check)


def test_file_registered_original_image_can_be_saved_as_wrong_answer_source(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    assets = tmp_path / "assets"
    study = tmp_path / "study"
    inbox.mkdir()
    assets.mkdir()
    study.mkdir()
    (study / "fractions.md").write_text("# Fractions\nSynthetic reference", encoding="utf-8")
    original = b"\xff\xd8" + b"synthetic-original-jpeg" * 150_000
    (inbox / "practice.jpg").write_bytes(original)
    store = SQLiteDocumentStore(assets, tmp_path / "private.db")
    gateway = Gateway(AppConfig("0.1.0", study, asset_root=assets, asset_ingest_root=inbox), None,
                      document_store=store, capabilities=frozenset({"read", "write", "ingest"}),
                      qmd_discoverable=lambda: False)

    async def check() -> None:
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            registered = await client.call_tool("register_asset", {
                "media_type": "image/jpeg", "relative_path": "practice.jpg",
                "provenance": {"reported_agent": "SyntheticAgent",
                               "reported_client": "synthetic-mcp", "run_id": "image-wa-run"},
            })
            assert registered.isError is False
            asset = registered.structuredContent["asset"]
            digest = hashlib.sha256(original).hexdigest()
            assert asset["sha256"] == digest
            assert (assets / digest[:2] / digest).read_bytes() == original
            source_result = await client.call_tool("register_wrong_answer_source", {
                "source_uri": asset["asset_uri"],
                "question_text": "Synthetic fraction question",
                "student_answer": "Synthetic incorrect answer",
                "provenance": {"reported_agent": "SyntheticAgent",
                               "reported_client": "synthetic-mcp", "run_id": "image-wa-run"},
            })
            assert source_result.isError is False
            source = source_result.structuredContent["source"]
            assert source["source_uri"] == asset["asset_uri"]
            saved = await client.call_tool("save_wrong_answer_analysis", {
                "source_id": source["source_id"], "analysis": analysis(),
                "source_refs": [asset["asset_uri"]], "study_relations": ["study:fractions.md"],
                "idempotency_key": "synthetic-file-image-analysis",
                "provenance": {"reported_agent": "SyntheticAgent",
                               "reported_client": "synthetic-mcp", "run_id": "image-wa-analysis"},
            })
            assert saved.isError is False
            record = saved.structuredContent["analysis"]
            assert record["version"] == 1
            assert record["source_refs"] == [asset["asset_uri"]]
            assert record["write_provenance"]["source_refs"] == [asset["asset_uri"]]
            assert record["write_provenance"]["reported_agent"] == "SyntheticAgent"
            bundle = await client.call_tool("get_wrong_answer_bundle", {
                "source_id": source["source_id"],
            })
            assert bundle.isError is False
            assert bundle.structuredContent["bundle"]["source"]["source_uri"] == asset["asset_uri"]
            assert bundle.structuredContent["bundle"]["analyses"] == [record]

    anyio.run(check)
