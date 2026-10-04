"""Dual-profile ingress coverage uses invented targets and official in-memory MCP."""

import base64
import hashlib
import json
from pathlib import Path
import struct
import zlib

import anyio
from mcp.shared.memory import create_connected_server_and_client_session

from cognivault.runtime import load_gateway_from_config
from cognivault.transports.mcp_stdio import create_mcp_server


MARKER = "P13_PRODUCTION_INGRESS_SMOKE_SYNTHETIC_DUAL_PROFILE"
LABEL = MARKER + " SYNTHETIC ONLY"
PROVENANCE = {
    "reported_agent": "SyntheticAgent",
    "reported_client": "synthetic-in-memory-mcp",
    "run_id": MARKER,
}
WRITE_TOOLS = {
    "register_asset", "ingest_documents", "register_wrong_answer_source",
    "save_wrong_answer_analysis", "update_wrong_answer_analysis",
}


def _original_png() -> bytes:
    """Handmade valid one-pixel PNG; the tEXt chunk labels its invented evidence."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"Description\x00" + (LABEL + " 7 + 5; answer 11").encode("ascii"))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
            + chunk(b"IEND", b""))


def _profiles(tmp_path: Path) -> tuple[Path, Path]:
    study = tmp_path / "invented-study"
    assets = tmp_path / "invented-assets"
    study.mkdir()
    assets.mkdir()
    common = (
        '[gateway]\nversion="0.6.0"\n[study]\n'
        f'root={json.dumps(study.as_posix())}\n'
        'qmd_collection="studyvault"\nqmd_version="2.8.3"\n'
        'qmd_executable="synthetic-qmd-not-installed"\n'
        '[history]\nbackend="not_configured"\n[assets]\nbackend="sqlite"\n'
        f'root={json.dumps(assets.as_posix())}\n'
        f'database={json.dumps((tmp_path / "invented-assets.db").as_posix())}\n'
    )
    readonly = tmp_path / "synthetic-readonly.toml"
    ingress = tmp_path / "synthetic-ingress.toml"
    for path, capabilities in ((readonly, ["read"]), (ingress, ["read", "ingest", "write"])):
        path.write_text(common + '[permissions]\ncapabilities=' + json.dumps(capabilities) + '\n',
                        encoding="utf-8")
    return readonly, ingress


def _analysis() -> dict:
    return {
        "error_type": LABEL + " invented addition error",
        "knowledge_points": [LABEL + " addition"],
        "reasoning": LABEL + " invented answer 11 differs from 7 + 5.",
        "correct_solution": LABEL + " 7 + 5 = 12.",
        "review_advice": LABEL + " initial review advice.",
    }


def test_dual_profiles_enforce_permissions_and_persist_exactly_two_versions(tmp_path: Path) -> None:
    # Catches permission bypass, profile target drift, duplicate replay, stale append,
    # overwritten evidence/versions and state lost when a fresh Gateway reconnects.
    readonly_path, ingress_path = _profiles(tmp_path)
    original = _original_png()
    asset_args = {
        "media_type": "image/png", "content_base64": base64.b64encode(original).decode("ascii"),
        "provenance": PROVENANCE,
    }
    source_args = {
        "source_uri": "asset://sha256/" + hashlib.sha256(original).hexdigest(),
        "question_text": LABEL + " What is 7 + 5?",
        "student_answer": LABEL + " 11",
        "provenance": PROVENANCE,
    }
    save_args = {
        "source_id": "wrong-answer://sha256/" + "0" * 64,
        "analysis": _analysis(), "source_refs": [source_args["source_uri"]],
        "study_relations": [], "idempotency_key": MARKER + "-v1",
        "expected_version": 0, "provenance": PROVENANCE,
    }

    async def check() -> None:
        with anyio.fail_after(30):
            readonly = load_gateway_from_config(readonly_path)
            async with create_connected_server_and_client_session(create_mcp_server(readonly)) as client:
                tools = {tool.name for tool in (await client.list_tools()).tools}
                assert WRITE_TOOLS.isdisjoint(tools)
                assert {"fetch_asset", "search_wrong_answers", "get_wrong_answer_bundle"} <= tools
                # Direct calls must be denied too; discovery alone is not authorization.
                for name, arguments in (
                    ("register_asset", asset_args),
                    ("ingest_documents", {"documents": [{"title": LABEL, "media_type": "text/plain",
                        "content_base64": base64.b64encode(LABEL.encode()).decode("ascii")}]}),
                    ("register_wrong_answer_source", source_args),
                    ("save_wrong_answer_analysis", save_args),
                    ("update_wrong_answer_analysis", dict(save_args, expected_version=1)),
                ):
                    denied = await client.call_tool(name, arguments)
                    assert denied.isError is True
                    assert denied.structuredContent["error"]["code"] == "PERMISSION_DENIED"
                empty = (await client.call_tool("search_wrong_answers", {"query": MARKER})).structuredContent
                assert empty["total"] == 0 and empty["results"] == []
                missing = await client.call_tool("fetch_asset", {"asset_uri": source_args["source_uri"]})
                assert missing.isError is True
                # Denied writes must not initialize the previously absent asset store.
                assert missing.structuredContent["error"]["code"] == "STORAGE_UNAVAILABLE"

            ingress = load_gateway_from_config(ingress_path)
            async with create_connected_server_and_client_session(create_mcp_server(ingress)) as client:
                tools = {tool.name for tool in (await client.list_tools()).tools}
                assert WRITE_TOOLS <= tools
                await client.read_resource("study-workflow://wrong-answer")
                registered = await client.call_tool("register_asset", asset_args)
                assert registered.isError is False
                asset = registered.structuredContent["asset"]
                assert asset["asset_uri"] == source_args["source_uri"]
                assert asset["sha256"] == hashlib.sha256(original).hexdigest()
                fetched = await client.call_tool("fetch_asset", {"asset_uri": asset["asset_uri"]})
                assert base64.b64decode(fetched.structuredContent["content_base64"]) == original

                registered = await client.call_tool("register_wrong_answer_source", source_args)
                assert registered.isError is False
                source = registered.structuredContent["source"]
                repeated = await client.call_tool("register_wrong_answer_source", source_args)
                assert repeated.structuredContent == registered.structuredContent
                first_args = dict(save_args, source_id=source["source_id"])
                saved = await client.call_tool("save_wrong_answer_analysis", first_args)
                assert saved.isError is False
                first = saved.structuredContent["analysis"]
                assert first["version"] == 1 and first["supersedes_analysis_id"] is None
                replay = await client.call_tool("save_wrong_answer_analysis", first_args)
                assert replay.structuredContent == saved.structuredContent

                update_args = dict(first_args, expected_version=1, idempotency_key=MARKER + "-v2",
                    analysis={**_analysis(), "review_advice": LABEL + " appended review advice."})
                updated = await client.call_tool("update_wrong_answer_analysis", update_args)
                assert updated.isError is False
                second = updated.structuredContent["analysis"]
                assert second["version"] == 2
                assert second["supersedes_analysis_id"] == first["analysis_id"]
                replay = await client.call_tool("update_wrong_answer_analysis", update_args)
                assert replay.structuredContent == updated.structuredContent
                stale = await client.call_tool("update_wrong_answer_analysis",
                    dict(update_args, idempotency_key=MARKER + "-stale"))
                assert stale.isError is True
                assert stale.structuredContent["error"]["code"] == "CONFLICT"
                for record in (first, second):
                    assert record["source_refs"] == [asset["asset_uri"]]
                    assert record["study_relations"] == []
                    assert record["write_provenance"]["identity_trust"] == "reported"

            # Reload the preserved read-only profile and discover the record by marker.
            fresh = load_gateway_from_config(readonly_path)
            async with create_connected_server_and_client_session(create_mcp_server(fresh)) as client:
                assert WRITE_TOOLS.isdisjoint({tool.name for tool in (await client.list_tools()).tools})
                found = (await client.call_tool("search_wrong_answers", {"query": MARKER})).structuredContent
                assert found["total"] == 1
                discovered = found["results"][0]["source_id"]
                bundle = (await client.call_tool("get_wrong_answer_bundle", {"source_id": discovered})).structuredContent["bundle"]
                assert bundle["source"] == source
                assert bundle["total"] == 2 and bundle["has_more"] is False
                assert bundle["analyses"][0]["version"] == 2
                assert sorted(bundle["analyses"], key=lambda record: record["version"]) == [first, second]
                assert sorted(record["version"] for record in bundle["analyses"]) == [1, 2]
                fetched = await client.call_tool("fetch_asset", {"asset_uri": bundle["source"]["source_uri"]})
                assert base64.b64decode(fetched.structuredContent["content_base64"]) == original
                absent = (await client.call_tool("search_wrong_answers", {"query": MARKER + "_NO_RESULT"})).structuredContent
                assert absent["total"] == 0 and absent["results"] == []

    anyio.run(check)
