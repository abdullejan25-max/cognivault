"""Explicit capabilities guard every persistent write surface."""

import anyio
import base64
import os
from pathlib import Path
import subprocess

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from cognivault.adapters.documents import SQLiteDocumentStore
from cognivault.adapters.history import SQLiteHistoryBackend
import cognivault.adapters.documents as documents_adapter
from cognivault.config import AppConfig
from cognivault.contracts import GatewayError
from cognivault.gateway import Gateway
from cognivault.runtime import load_gateway_from_config
from cognivault.transports.mcp_stdio import create_mcp_server


def _gateway(tmp_path: Path, capabilities=frozenset({"read"})) -> Gateway:
    root = tmp_path / "assets"
    root.mkdir(exist_ok=True)
    store = SQLiteDocumentStore(root, tmp_path / "private.db")
    return Gateway(AppConfig("0.1.0", tmp_path), None, document_store=store,
                   capabilities=capabilities, qmd_discoverable=lambda: False)


def test_document_ingestion_requires_ingest_capability(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    with pytest.raises(GatewayError, match="Capability is not enabled") as error:
        gateway.ingest_documents([{"title": "Synthetic", "media_type": "text/plain",
                                   "content_base64": base64.b64encode(b"synthetic").decode()}])
    assert error.value.code == "PERMISSION_DENIED"
    assert not (tmp_path / "private.db").exists()


def test_wrong_answer_write_requires_write_capability(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path, frozenset({"read", "ingest"}))
    with pytest.raises(GatewayError) as error:
        gateway.register_wrong_answer_source("document://sha256/" + "0" * 64, "Q", "A")
    assert error.value.code == "PERMISSION_DENIED"
    assert "C:" not in error.value.message


def test_mcp_discovery_exposes_only_enabled_write_tools(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    expected_read = {"retrieve_evidence", "search_canonical_messages", "health_report", "search_study", "list_history_sources", "search_history",
                     "fetch_history_item", "search_documents",
                     "fetch_document", "fetch_document_page", "list_document_ocr_candidates",
                     "fetch_asset", "get_wrong_answer_bundle",
                     "search_wrong_answers", "list_legacy_sources", "search_legacy_sources",
                     "fetch_legacy_source"}

    async def check() -> None:
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            names = {item.name for item in (await client.list_tools()).tools}
            assert names == expected_read
            denied = await client.call_tool("register_wrong_answer_source", {
                "source_uri": "document://sha256/" + "0" * 64,
                "question_text": "Synthetic question", "student_answer": "Synthetic answer",
            })
            assert denied.isError is True
            assert denied.structuredContent["error"]["code"] == "PERMISSION_DENIED"

    anyio.run(check)


def test_ingest_capability_exposes_document_ingestion_but_not_analysis_write(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path, frozenset({"read", "ingest"}))

    async def check() -> None:
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            names = {item.name for item in (await client.list_tools()).tools}
            assert {"register_asset", "ingest_documents", "process_document_ocr_pages"} <= names
            assert "register_wrong_answer_source" not in names
            assert "save_wrong_answer_analysis" not in names

    anyio.run(check)


@pytest.mark.parametrize("capabilities", [frozenset({"read"}), frozenset({"ingest"})])
def test_normalization_discovery_requires_read_and_ingest_without_creating_database(tmp_path, capabilities):
    database = tmp_path / "synthetic-history.sqlite3"
    inbox = tmp_path / "synthetic-inbox"
    inbox.mkdir()
    gateway = Gateway(
        AppConfig("0.6.0", history_database=database, history_migration_inbox=inbox),
        None, SQLiteHistoryBackend(database), capabilities=capabilities,
        qmd_discoverable=lambda: False,
    )

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            denied = await client.call_tool("normalize_history_sources", {"source_set_sha256": "0" * 64})
            assert denied.isError is True
            assert denied.structuredContent["error"]["code"] == "PERMISSION_DENIED"
            assert not database.exists()
            assert list(inbox.iterdir()) == []
            assert "normalize_history_sources" not in names
        assert not database.exists()
        assert list(inbox.iterdir()) == []

    anyio.run(check)


def test_config_capabilities_are_explicit_and_default_read_only(tmp_path: Path) -> None:
    study = tmp_path / "study"
    study.mkdir()
    base = ('[gateway]\nversion="0.1.0"\n[study]\n'
            f'root="{study.as_posix()}"\nqmd_collection="studyvault"\nqmd_version="2.8.3"\n'
            'qmd_executable="missing-qmd"\n[history]\nbackend="not_configured"\n')
    path = tmp_path / "config.toml"
    path.write_text(base, encoding="utf-8")
    assert load_gateway_from_config(path).capabilities == frozenset({"read"})
    path.write_text(base + '[permissions]\ncapabilities=["read", "write"]\n', encoding="utf-8")
    assert load_gateway_from_config(path).capabilities == frozenset({"read", "write"})
    path.write_text(base + '[permissions]\ncapabilities=["projection"]\n', encoding="utf-8")
    assert load_gateway_from_config(path).capabilities == frozenset({"projection"})
    for invalid in ('[permissions]\ncapabilities=["read", "unknown"]\n',
                    '[permissions]\ncapabilities="read"\n',
                    '[permissions]\ncapabilities=["read", "read"]\n'):
        path.write_text(base + invalid, encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid local configuration"):
            load_gateway_from_config(path)


def test_read_capability_is_required_at_gateway_not_only_discovery(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path, frozenset())
    with pytest.raises(GatewayError) as error:
        gateway.search_documents("synthetic")
    assert error.value.code == "PERMISSION_DENIED"


def test_bulk_projection_requires_its_separate_capability(tmp_path: Path) -> None:
    read_gateway = _gateway(tmp_path, frozenset({"read"}))
    with pytest.raises(GatewayError) as denied:
        read_gateway.projection_snapshot("history", "begin")
    assert denied.value.code == "PERMISSION_DENIED"

    projection_gateway = _gateway(tmp_path, frozenset({"projection"}))
    with pytest.raises(GatewayError) as unavailable:
        projection_gateway.projection_snapshot("history", "begin")
    assert unavailable.value.code == "HISTORY_UNAVAILABLE"


def test_document_storage_rejects_windows_reparse_point_ancestors(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    store = SQLiteDocumentStore(root, tmp_path / "private.db")
    original = documents_adapter._is_reparse_point
    monkeypatch.setattr(documents_adapter, "_is_reparse_point",
                        lambda path: Path(path) == root or original(Path(path)))
    with pytest.raises(GatewayError) as error:
        store._connect(write=True)
    assert error.value.code == "STORAGE_UNAVAILABLE"


def test_document_storage_rejects_reparse_point_database_path(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    database = tmp_path / "private.db"
    store = SQLiteDocumentStore(root, database)
    original = documents_adapter._is_reparse_point
    monkeypatch.setattr(documents_adapter, "_is_reparse_point",
                        lambda path: Path(path) == database or original(Path(path)))
    with pytest.raises(GatewayError) as error:
        store._connect(write=True)
    assert error.value.code == "STORAGE_UNAVAILABLE"


def test_windows_junction_asset_root_is_rejected(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction behavior requires Windows")
    target = tmp_path / "outside-target"
    junction = tmp_path / "asset-junction"
    target.mkdir()
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", f'mklink /J "{junction}" "{target}"'],
        capture_output=True, text=True, check=False,
    )
    if created.returncode != 0:
        pytest.skip("this host cannot create NTFS junctions")
    try:
        store = SQLiteDocumentStore(junction, tmp_path / "private.db")
        with pytest.raises(GatewayError) as error:
            store._connect(write=True)
        assert error.value.code == "STORAGE_UNAVAILABLE"
        assert not (tmp_path / "private.db").exists()
    finally:
        junction.rmdir()


def test_document_write_audit_contains_only_logical_metadata(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path, frozenset({"read", "ingest"}))
    result = gateway.ingest_documents([{"title": "Synthetic", "media_type": "text/plain",
                                       "content_base64": base64.b64encode(b"Synthetic private text").decode()}])
    import sqlite3
    with sqlite3.connect(tmp_path / "private.db") as con:
        events = con.execute("SELECT operation, resource_type, resource_id, outcome FROM operation_audit").fetchall()
    assert events == [("ingest_document", "document", result["documents"][0]["document_uri"], "success")]
    assert "Synthetic private text" not in repr(events)
