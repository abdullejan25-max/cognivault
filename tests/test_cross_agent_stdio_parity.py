"""Synthetic MCP clients share Gateway state across independent process restarts."""

import anyio
import base64
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

def test_independent_stdio_clients_share_workflow_and_versioned_gateway_state(tmp_path: Path) -> None:
    study = tmp_path / "study"
    assets = tmp_path / "assets"
    study.mkdir()
    assets.mkdir()
    (study / "fractions.md").write_text("# Fractions\nSynthetic reference", encoding="utf-8")

    asset_database = tmp_path / "assets.db"
    config = tmp_path / "runtime.toml"
    config.write_text(
        '[gateway]\nversion="0.1.0"\n[study]\n'
        f'root={json.dumps(study.as_posix())}\nqmd_collection="studyvault"\n'
        'qmd_version="2.8.3"\nqmd_executable="synthetic-qmd-not-installed"\n'
        '[history]\nbackend="not_configured"\n'
        '[assets]\nbackend="sqlite"\n'
        f'root={json.dumps(assets.as_posix())}\n'
        f'database={json.dumps(asset_database.as_posix())}\n'
        '[permissions]\ncapabilities=["read", "write", "ingest"]\n',
        encoding="utf-8",
    )
    repository = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repository / "src") + os.pathsep + env.get("PYTHONPATH", "")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-B", "-m", "cognivault.transports.mcp_stdio", "--config", str(config)],
        env=env,
        cwd=repository,
    )

    async def run_client(operation):
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as client:
                await client.initialize()
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                workflow_resource = next(
                    resource for resource in (await client.list_resources()).resources
                    if str(resource.uri) == "study-workflow://wrong-answer"
                )
                workflow = (await client.read_resource(str(workflow_resource.uri))).contents[0].text
                return await operation(client, tools, workflow)

    source_state: dict[str, object] = {}

    async def client_a_writes(client, tools, workflow):
        required = {
            "register_wrong_answer_source", "get_wrong_answer_bundle", "search_wrong_answers",
            "save_wrong_answer_analysis", "update_wrong_answer_analysis",
        }
        assert required <= set(tools)
        assert "explicit request to save" in workflow.lower()
        ingested = await client.call_tool("ingest_documents", {
            "documents": [{
                "title": "Synthetic parity source / SYNTHETIC ONLY",
                "media_type": "text/plain",
                "content_base64": base64.b64encode(b"Synthetic source evidence").decode("ascii"),
            }],
            "provenance": {"reported_agent": "SyntheticClientA", "reported_client": "stdio-test"},
        })
        assert ingested.isError is not True
        document_uri = ingested.structuredContent["documents"][0]["document_uri"]
        document = await client.call_tool("fetch_document", {"document_uri": document_uri})
        source_state["document"] = document.structuredContent
        source_state["schemas"] = {name: tool.model_dump(mode="json") for name, tool in tools.items()}
        assert document.structuredContent["document"]["write_provenance"]["reported_agent"] == "SyntheticClientA"
        registered = await client.call_tool("register_wrong_answer_source", {
            "source_uri": document_uri,
            "question_text": "Synthetic parity question: add 1/2 and 1/3.",
            "student_answer": "2/5",
            "provenance": {"reported_agent": "SyntheticClientA", "reported_client": "stdio-test"},
        })
        source = registered.structuredContent["source"]
        source_state["source_id"] = source["source_id"]
        source_state["workflow"] = workflow
        saved = await client.call_tool("save_wrong_answer_analysis", {
            "source_id": source["source_id"],
            "analysis": {
                "error_type": "denominator",
                "knowledge_points": ["fraction addition"],
                "reasoning": "Synthetic client A analysis.",
                "correct_solution": "3/6 + 2/6 = 5/6.",
                "review_advice": "Practice a second synthetic sum.",
            },
            "source_refs": [document_uri],
            "study_relations": ["study:fractions.md"],
            "idempotency_key": "synthetic-client-a-v1",
            "expected_version": 0,
            "provenance": {"reported_agent": "SyntheticClientA", "reported_client": "stdio-test"},
        })
        first = saved.structuredContent["analysis"]
        assert first["version"] == 1
        assert first["write_provenance"]["identity_trust"] == "reported"
        source_state["analysis_v1_id"] = first["analysis_id"]
        initial = await client.call_tool("get_wrong_answer_bundle", {"source_id": source_state["source_id"]})
        source_state["initial_bundle"] = initial.structuredContent["bundle"]
        return set(tools)

    async def client_b_reads_and_updates(client, tools, workflow):
        assert set(tools) == source_state["tool_names"]
        assert {name: tool.model_dump(mode="json") for name, tool in tools.items()} == source_state["schemas"]
        assert workflow == source_state["workflow"]
        # B discovers object IDs itself through the shared Gateway.
        searched = await client.call_tool("search_wrong_answers", {"query": "parity question"})
        assert searched.structuredContent["total"] == 1
        discovered_id = searched.structuredContent["results"][0]["source_id"]
        fetched = await client.call_tool("get_wrong_answer_bundle", {"source_id": discovered_id})
        bundle = fetched.structuredContent["bundle"]
        assert bundle["source"]["source_id"] == source_state["source_id"]
        assert [analysis["version"] for analysis in bundle["analyses"]] == [1]
        assert bundle == source_state["initial_bundle"]
        document_uri = bundle["source"]["source_uri"]

        update_arguments = {
            "source_id": discovered_id,
            "analysis": {
                "error_type": "denominator",
                "knowledge_points": ["fraction addition"],
                "reasoning": "Synthetic client B follow-up.",
                "correct_solution": "3/6 + 2/6 = 5/6.",
                "review_advice": "Practice one more synthetic sum.",
            },
            "source_refs": [document_uri],
            "study_relations": ["study:fractions.md"],
            "idempotency_key": "synthetic-client-b-v2",
            "expected_version": 1,
            "provenance": {"reported_agent": "SyntheticClientB", "reported_client": "stdio-test"},
        }
        updated = await client.call_tool("update_wrong_answer_analysis", update_arguments)
        second = updated.structuredContent["analysis"]
        assert second["version"] == 2
        assert second["supersedes_analysis_id"] == source_state["analysis_v1_id"]
        assert second["write_provenance"]["reported_agent"] == "SyntheticClientB"
        assert second["write_provenance"]["identity_trust"] == "reported"

        replay = await client.call_tool("update_wrong_answer_analysis", update_arguments)
        assert replay.structuredContent == updated.structuredContent
        assert replay.structuredContent["analysis"]["analysis_id"] == second["analysis_id"]
        assert replay.structuredContent["analysis"]["version"] == 2

        stale = await client.call_tool("update_wrong_answer_analysis", {
            **update_arguments,
            "idempotency_key": "synthetic-client-b-stale-v1",
        })
        assert stale.isError is True
        assert stale.structuredContent["error"]["code"] == "CONFLICT"
        latest = await client.call_tool("get_wrong_answer_bundle", {"source_id": discovered_id})
        source_state["final_bundle"] = latest.structuredContent["bundle"]
        absent = await client.call_tool("search_wrong_answers", {"query": "synthetic-absent-marker"})
        assert absent.structuredContent["total"] == 0
        assert absent.structuredContent["results"] == []

    async def client_c_discovers_after_both_exit(client, tools, workflow):
        assert set(tools) == source_state["tool_names"]
        assert {name: tool.model_dump(mode="json") for name, tool in tools.items()} == source_state["schemas"]
        assert workflow == source_state["workflow"]
        searched = await client.call_tool("search_wrong_answers", {"query": "parity question"})
        assert searched.structuredContent["total"] == 1
        discovered_id = searched.structuredContent["results"][0]["source_id"]
        fetched = await client.call_tool("get_wrong_answer_bundle", {"source_id": discovered_id})
        bundle = fetched.structuredContent["bundle"]
        assert bundle == source_state["final_bundle"]
        assert bundle["source"] == source_state["initial_bundle"]["source"]
        assert bundle["total"] == 2
        assert bundle["has_more"] is False
        assert len(bundle["analyses"]) == 2
        assert len({item["analysis_id"] for item in bundle["analyses"]}) == 2
        document = await client.call_tool("fetch_document", {"document_uri": bundle["source"]["source_uri"]})
        assert document.structuredContent == source_state["document"]
        versions = {analysis["version"]: analysis for analysis in
                    fetched.structuredContent["bundle"]["analyses"]}
        assert set(versions) == {1, 2}
        assert versions[1] == source_state["initial_bundle"]["analyses"][0]
        assert versions[2]["supersedes_analysis_id"] == versions[1]["analysis_id"]
        assert versions[2]["review_advice"] == "Practice one more synthetic sum."
        assert versions[1]["write_provenance"]["reported_agent"] == "SyntheticClientA"
        assert versions[2]["write_provenance"]["reported_agent"] == "SyntheticClientB"
        assert all(item["write_provenance"]["identity_trust"] == "reported" for item in versions.values())

    async def check() -> None:
        with anyio.fail_after(90):
            source_state["tool_names"] = await run_client(client_a_writes)
            await run_client(client_b_reads_and_updates)
            await run_client(client_c_discovers_after_both_exit)

    anyio.run(check)
