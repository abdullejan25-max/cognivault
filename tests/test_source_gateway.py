"""Synthetic-only tests of formal source ingestion, not production storage."""
import base64
from dataclasses import replace
import hashlib
import json

import anyio
from mcp.shared.memory import create_connected_server_and_client_session
import pytest

from cognivault.adapters.history import SQLiteHistoryBackend
from cognivault.config import AppConfig
from cognivault.contracts import GatewayError
from cognivault.gateway import Gateway
from cognivault.transports.mcp_stdio import create_mcp_server


def setup(tmp_path, capabilities=frozenset({"read", "ingest"})):
    inbox=tmp_path/"private-inbox"
    inbox.mkdir()
    db=tmp_path/"history.sqlite3"
    history=SQLiteHistoryBackend(db)
    history.initialize()
    g=Gateway(AppConfig("0.6.0", history_database=db, history_migration_inbox=inbox),
              None, history, capabilities=capabilities)
    return g, inbox


def manifest(inbox, *, raw=b'{"synthetic":"no guessed roles"}\n', path="input.bin", digest=None):
    (inbox/"input.bin").write_bytes(raw)
    entry={"source_system":"workbuddy", "source_format":"json", "record_kind":"session_metadata",
           "sha256":digest or hashlib.sha256(raw).hexdigest(), "byte_count":len(raw), "relative_path":path}
    content=json.dumps({"schema_version":1, "entries":[entry, entry]}).encode()
    (inbox/"manifest.json").write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def test_gateway_source_manifest_exact_rerun_readback_and_durable_receipt(tmp_path):
    g,inbox=setup(tmp_path)
    digest=manifest(inbox)
    first=g.ingest_history_sources("manifest.json", digest, limit=1)
    second=g.ingest_history_sources("manifest.json", digest, cursor=1, limit=10)
    assert first["imported"]==1 and second["reused"]==1
    assert first["results"][0]["source_id"]==second["results"][0]["source_id"]
    assert first["has_more"] is True and second["has_more"] is False
    assert first["results"][0]["verified"] is True
    assert (inbox/first["receipt"]["relative_path"]).is_file()
    identity=first["results"][0]["source_id"]
    fetched=g.fetch_history_source(identity)
    assert base64.b64decode(fetched["content_base64"])==(inbox/"input.bin").read_bytes()
    assert g.search_history_sources(source_system="workbuddy")["total"]==1
    assert g.search_history_sources(query="SYNTHETIC_NO_RESULT")["total"]==0
    assert g.history_source_summary()["source_records"]==1


@pytest.mark.parametrize("path", ["../input.bin", "C:/private/input.bin", "input.bin:stream", ".\\input.bin"])
def test_gateway_inbox_path_escape_is_accounted_without_import(tmp_path, path):
    g,inbox=setup(tmp_path)
    result=g.ingest_history_sources("manifest.json", manifest(inbox,path=path))
    assert result["errors"]==2
    assert g.history_source_summary()["source_records"]==0
    assert all(r["error_code"]=="INVALID_ARGUMENT" for r in result["results"])


def test_gateway_digest_mismatch_and_permission_and_missing_inbox(tmp_path):
    g,inbox=setup(tmp_path)
    digest=manifest(inbox, digest="0"*64)
    assert g.ingest_history_sources("manifest.json",digest)["errors"]==2
    with pytest.raises(GatewayError): g.ingest_history_sources("manifest.json","0"*64)
    g.capabilities=frozenset({"read"})
    with pytest.raises(GatewayError,match="Capability"): g.ingest_history_sources("manifest.json",digest)
    g.capabilities=frozenset({"ingest", "read"})
    g.config=replace(g.config,history_migration_inbox=None)
    with pytest.raises(GatewayError): g.ingest_history_sources("manifest.json",digest)


def test_gateway_rejects_authoritative_target_and_git_inbox(tmp_path):
    g,inbox=setup(tmp_path)
    digest=manifest(inbox)
    g.config=replace(g.config,asset_root=inbox)
    with pytest.raises(GatewayError): g.ingest_history_sources("manifest.json",digest)
    g.config=replace(g.config,asset_root=None)
    (tmp_path/".git").mkdir()
    with pytest.raises(GatewayError): g.ingest_history_sources("manifest.json",digest)


def test_native_mcp_source_tools_ingest_fetch_rerun_and_validation(tmp_path):
    g,inbox=setup(tmp_path)
    digest=manifest(inbox)
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            listed={t.name:t for t in (await client.list_tools()).tools}
            assert listed["ingest_history_sources"].annotations.destructiveHint is False
            args={"relative_manifest":"manifest.json", "expected_manifest_sha256":digest}
            first=(await client.call_tool("ingest_history_sources",args)).structuredContent
            assert first["ok"] is True and first["imported"]==1 and first["reused"]==1
            compact=(await client.call_tool("ingest_history_sources",args|{"compact":True})).structuredContent
            assert compact["reused"]==2 and compact["results"]==[] and len(compact["results_sha256"])==64
            result=(await client.call_tool("history_source_summary",{})).structuredContent
            assert result["source_records"]==1
            bad=(await client.call_tool("history_source_summary",{"injected":"synthetic"})).structuredContent
            assert bad["ok"] is False
    anyio.run(check)


def test_existing_large_gemini_root_manifest_is_linked_by_bounded_ranges(tmp_path):
    from cognivault.adapters.documents import SQLiteDocumentStore
    g,inbox=setup(tmp_path)
    assets=tmp_path/"assets"
    assets.mkdir()
    docs=SQLiteDocumentStore(assets,tmp_path/"documents.sqlite3")
    g.document_store=docs
    raw=b'SYNTHETIC ZIP EVIDENCE'
    h=hashlib.sha256(raw).hexdigest()
    asset=g.register_asset("application/octet-stream",base64.b64encode(raw).decode())["asset"]["asset_uri"]
    root={"schema_version":1,"marker":"P13_GEMINI_SOURCE_ONLY_V1","source_system":"gemini",
          "archive_sha256":h,"archive_bytes":len(raw),"archive_parts":[{"offset":0,"bytes":len(raw),"sha256":h,"asset_uri":asset}],
          "opaque_synthetic_annotation":"a"*66000}
    rootbytes=json.dumps(root).encode()
    doc=g.ingest_documents([{"title":"Synthetic Gemini root","media_type":"text/plain","content_base64":base64.b64encode(rootbytes).decode()}])["documents"][0]["document_uri"]
    entry={"source_system":"gemini","source_format":"zip","record_kind":"conversation_export","sha256":h,"byte_count":len(raw),"document_uri":doc}
    data=json.dumps({"schema_version":1,"entries":[entry]}).encode()
    (inbox/"manifest.json").write_bytes(data)
    result=g.ingest_history_sources("manifest.json",hashlib.sha256(data).hexdigest())
    assert result["errors"]==0 and result["imported"]==1
    assert g.history_source_summary()["stored_payloads"]==0
    assert base64.b64decode(g.fetch_history_source(result["results"][0]["source_id"])["content_base64"])==raw
