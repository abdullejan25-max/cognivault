"""Entirely hand-authored exports. No real conversation or attachment fixtures."""

import base64
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from cognivault.migration.gemini_takeout import prepare_gemini_export, GeminiSourceError


HTML = b'<html><body><div class="outer-cell mdl-cell"><div>SYNTHETIC QUESTION</div></div></body></html>'
PRIMARY = "Takeout/My Activity/Gemini Apps/MyActivity.html"


def archive(tmp_path, monkeypatch, members=None):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    p = tmp_path / "input.zip"
    with zipfile.ZipFile(p, "w") as z:
        for name, data in (members or [(PRIMARY, HTML)]):
            z.writestr(name, data)
    return p


def payloads(plan):
    return [json.loads(Path(item["payload_path"]).read_text(encoding="utf-8")) for item in plan["writes"]]


def test_all_zip_bytes_are_preserved_and_attachment_chat_is_opaque(tmp_path, monkeypatch):
    p = archive(tmp_path, monkeypatch, [(PRIMARY, HTML),
        ("Takeout/My Activity/Gemini Apps/uploaded-chat.json", b'[{"role":"user","content":"SYNTHETIC ATTACHMENT"}]')])
    plan = prepare_gemini_export(p, tmp_path / "private")
    saved = payloads(plan)
    binary = b"".join(base64.b64decode(s["arguments"]["content_base64"]) for s in saved if s["tool"] == "register_asset")
    assert binary == p.read_bytes()
    assert plan["activity_count"] == 1
    assert plan["opaque_file_members"] == 1
    assert plan["canonical_messages_written"] == 0
    assert plan["conversation_count"] is None
    assert plan["message_count"] is None
    assert all(s["tool"] in {"register_asset", "ingest_documents"} for s in saved)


def test_renaming_export_does_not_change_identity_or_manifest(tmp_path, monkeypatch):
    p = archive(tmp_path, monkeypatch)
    first = prepare_gemini_export(p, tmp_path / "private")
    copy = tmp_path / "renamed.zip"
    shutil.copyfile(p, copy)
    second = prepare_gemini_export(copy, tmp_path / "private")
    assert first["source_fingerprint"] == second["source_fingerprint"]
    assert first["root_document_uri"] == second["root_document_uri"]
    assert first["root_asset_uri"] == second["root_asset_uri"]
    assert second["raw_archive_reused"] is True


def test_utf8_boundary_and_html_bytes_are_lossless(tmp_path, monkeypatch):
    text = ('<html><div class="outer-cell">' + '汉🙂字' * 16000 + '</div></html>').encode()
    p = archive(tmp_path, monkeypatch, [(PRIMARY, text)])
    plan = prepare_gemini_export(p, tmp_path / "private")
    parts = [s for s in payloads(plan) if s["kind"] == "primary_html"]
    raw = [base64.b64decode(s["arguments"]["documents"][0]["content_base64"]) for s in parts]
    assert len(raw) == 3
    assert b"".join(raw) == text
    assert all(len(r) <= 65536 and r.decode("utf-8") for r in raw)
    assert plan["primary_sha256"] == hashlib.sha256(text).hexdigest()


@pytest.mark.parametrize("members,code", [
    ([("Takeout/Gemini/gems.json", b'{}')], "activity_missing"),
    ([(PRIMARY, HTML), ("Takeout/My Activity/Gemini Apps/My Activity.html", HTML)], "activity_ambiguous"),
    ([(PRIMARY, b'<html>Gemini</html>')], "activity_missing"),
    ([(PRIMARY, HTML), ("../outside.txt", b'SYNTHETIC')], "unsafe_member"),
    ([(PRIMARY, HTML), ("/absolute.txt", b'SYNTHETIC')], "unsafe_member"),
])
def test_unsafe_or_unproven_primary_is_not_published(tmp_path, monkeypatch, members, code):
    p = archive(tmp_path, monkeypatch, members)
    with pytest.raises(GeminiSourceError, match=code):
        prepare_gemini_export(p, tmp_path / "private")
    assert not (tmp_path / "private/plan.json").exists()


def test_member_byte_duplicates_are_preserved_without_semantic_merge(tmp_path, monkeypatch):
    p = archive(tmp_path, monkeypatch, [(PRIMARY, HTML),
        ("Takeout/My Activity/Gemini Apps/a.bin", b'opaque'),
        ("Takeout/My Activity/Gemini Apps/b.bin", b'opaque')])
    plan = prepare_gemini_export(p, tmp_path / "private")
    assert plan["member_count"] == 3
    assert plan["exact_duplicate_member_copies"] == 1
    assert plan["opaque_file_members"] == 2


def test_html_uploads_are_opaque_even_with_activity_markers_or_invalid_utf8(tmp_path, monkeypatch):
    from cognivault.migration import gemini_takeout
    monkeypatch.setattr(gemini_takeout, "MAX_PRIMARY_BYTES", 512)
    p = archive(tmp_path, monkeypatch, [(PRIMARY, HTML),
        ("Takeout/My Activity/Gemini Apps/uploaded.html", HTML * 100),
        ("Takeout/My Activity/Gemini Apps/Uploads/MyActivity.html", b'\xff' + HTML)])
    plan = prepare_gemini_export(p, tmp_path / "private")
    assert plan["activity_count"] == 1
    assert plan["opaque_file_members"] == 2


def test_localized_primary_requires_audited_descriptor_and_validates_signature(tmp_path, monkeypatch):
    name = "Takeout/My Activity/Gemini Apps/LocalizedActivity.html"
    p = archive(tmp_path, monkeypatch, [(name, HTML)])
    plan = prepare_gemini_export(p, tmp_path / "private", primary_member=name)
    assert plan["activity_count"] == 1
    with pytest.raises(GeminiSourceError, match="activity_missing"):
        prepare_gemini_export(p, tmp_path / "other")


def test_true_oversized_primary_is_rejected(tmp_path, monkeypatch):
    from cognivault.migration import gemini_takeout
    monkeypatch.setattr(gemini_takeout, "MAX_PRIMARY_BYTES", 512)
    p = archive(tmp_path, monkeypatch, [(PRIMARY, HTML * 100)])
    with pytest.raises(GeminiSourceError, match="primary_limit"):
        prepare_gemini_export(p, tmp_path / "private")


@pytest.mark.parametrize("relative", [".", "payloads", "raw-archives", "plans"])
def test_output_ancestor_of_protected_subtree_is_rejected_before_any_write(tmp_path, monkeypatch, relative):
    p = archive(tmp_path, monkeypatch)
    output = tmp_path / "private"
    with pytest.raises(GeminiSourceError, match="protected_output"):
        prepare_gemini_export(p, output, protected_paths=(output / relative,))
    assert not output.exists()


def test_source_inside_output_is_rejected_before_writes(tmp_path, monkeypatch):
    p = archive(tmp_path, monkeypatch)
    with pytest.raises(GeminiSourceError, match="source_output_overlap"):
        prepare_gemini_export(p, tmp_path)


def test_failed_atomic_replace_keeps_previous_private_plan(tmp_path, monkeypatch):
    from cognivault.migration import gemini_takeout
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    target = tmp_path / "plan.json"
    target.write_bytes(b'{"previous":true}')
    def fail(*args):
        raise OSError("SYNTHETIC_FAILURE")
    monkeypatch.setattr(gemini_takeout.os, "replace", fail)
    with pytest.raises(OSError):
        gemini_takeout._save(target, {"next": True})
    assert target.read_bytes() == b'{"previous":true}'


def test_manifest_is_derived_source_evidence_with_no_private_absolute_locator(tmp_path, monkeypatch):
    p = archive(tmp_path, monkeypatch)
    plan = prepare_gemini_export(p, tmp_path / "private")
    root = payloads(plan)[-1]
    raw = base64.b64decode(root["arguments"]["documents"][0]["content_base64"])
    manifest = json.loads(raw)
    assert root["kind"] == "root_manifest"
    assert manifest["source_system"] == "gemini"
    assert manifest["record_kind"] == "source_only_archive"
    assert manifest["normalization_state"] == "not_normalized"
    assert manifest["archive_sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()
    assert manifest["archive_parts"][0]["offset"] == 0
    assert manifest["primary_html_parts"][0]["offset"] == 0
    assert str(tmp_path) not in raw.decode()


def test_unrelated_takeout_upload_does_not_become_primary(tmp_path, monkeypatch):
    p = archive(tmp_path, monkeypatch, [("Takeout/Drive/Gemini upload.html", HTML)])
    with pytest.raises(GeminiSourceError, match="activity_missing"):
        prepare_gemini_export(p, tmp_path / "private")


def test_bad_zip_is_rejected_with_fixed_error_class(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    p = tmp_path / "broken.zip"
    p.write_bytes(b'SYNTHETIC_NOT_ZIP')
    with pytest.raises(GeminiSourceError, match="invalid_zip"):
        prepare_gemini_export(p, tmp_path / "private")


def test_byte_identical_html_ranges_have_same_title_to_allow_gateway_reuse(tmp_path, monkeypatch):
    raw = b'<div class="outer-cell">' + b'a' * 220000 + b'</div>'
    p = archive(tmp_path, monkeypatch, [(PRIMARY, raw)])
    plan = prepare_gemini_export(p, tmp_path / "private")
    titles = {}
    duplicate = False
    for write, payload in zip(plan["writes"], payloads(plan), strict=True):
        if write["kind"] != "primary_html": continue
        title = payload["arguments"]["documents"][0]["title"]
        if write["asset_uri"] in titles:
            duplicate = True
            assert title == titles[write["asset_uri"]]
        titles[write["asset_uri"]] = title
    assert duplicate is True


def test_new_export_does_not_overwrite_previous_source_write_payloads(tmp_path, monkeypatch):
    p = archive(tmp_path, monkeypatch)
    first = prepare_gemini_export(p, tmp_path / "private")
    saved = [Path(w["payload_path"]).read_bytes() for w in first["writes"]]
    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as z:
        z.writestr(PRIMARY, HTML.replace(b'SYNTHETIC QUESTION', b'ANOTHER SYNTHETIC'))
    second = prepare_gemini_export(other, tmp_path / "private")
    assert first["root_document_uri"] != second["root_document_uri"]
    assert [Path(w["payload_path"]).read_bytes() for w in first["writes"]] == saved


def test_existing_text_document_constraints_are_checked_before_publishing_plan(tmp_path, monkeypatch):
    p = archive(tmp_path, monkeypatch, [(PRIMARY, b'<div class="outer-cell">before\f\fafter</div>')])
    with pytest.raises(GeminiSourceError, match="unindexable_html_range"):
        prepare_gemini_export(p, tmp_path / "private")
    assert not (tmp_path / "private/plan.json").exists()


def test_synthetic_native_mcp_import_rerun_and_lossless_readback(tmp_path, monkeypatch):
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session
    from cognivault.adapters.documents import SQLiteDocumentStore
    from cognivault.adapters.history import SQLiteHistoryBackend
    from cognivault.config import AppConfig
    from cognivault.gateway import Gateway
    from cognivault.transports.mcp_stdio import create_mcp_server

    raw = b'<div class="outer-cell">' + b'a' * 220000 + b'</div>'
    p = archive(tmp_path, monkeypatch, [(PRIMARY, raw)])
    plan = prepare_gemini_export(p, tmp_path / "private")
    asset_root = tmp_path / "isolated-assets"
    asset_root.mkdir()
    store = SQLiteDocumentStore(asset_root, tmp_path / "isolated-docs.sqlite3")
    history = SQLiteHistoryBackend(tmp_path / "isolated-history.sqlite3")
    history.register_source("synthetic", "Synthetic only")
    gateway = Gateway(AppConfig("0.6.0", tmp_path), None, history, document_store=store,
                      capabilities=frozenset({"read", "ingest"}))

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            identities = []
            for repeat in range(2):
                current = []
                for write, payload in zip(plan["writes"], payloads(plan), strict=True):
                    result = await client.call_tool(payload["tool"], payload["arguments"])
                    assert result.isError is not True
                    result = result.structuredContent
                    returned = result["asset"] if payload["tool"] == "register_asset" else result["documents"][0]
                    assert returned["asset_uri"] == write["asset_uri"]
                    if payload["tool"] == "ingest_documents":
                        assert returned["document_uri"] == write["document_uri"]
                        assert returned["write_provenance"]["data_origin"] == "deterministic_derived"
                    current.append(returned["asset_uri"])
                identities.append(current)
            assert identities[0] == identities[1]
            recovered = []
            for write in plan["writes"]:
                if write["kind"] != "archive_range": continue
                for offset in range(0, write["bytes"], 65536):
                    fetched = await client.call_tool("fetch_asset", {"asset_uri": write["asset_uri"],
                        "offset": offset, "length": min(65536, write["bytes"] - offset)})
                    assert fetched.isError is not True
                    recovered.append(base64.b64decode(fetched.structuredContent["content_base64"]))
            assert b"".join(recovered) == p.read_bytes()
            root = await client.call_tool("fetch_document", {"document_uri": plan["root_document_uri"]})
            assert root.structuredContent["document"]["write_provenance"]["source_refs"] == [plan["root_asset_uri"]]
            found = await client.call_tool("search_documents", {"query": "P13_GEMINI_SOURCE_ONLY_V1"})
            assert found.structuredContent["total"] == 1
            absent = await client.call_tool("search_documents", {"query": "SYNTHETIC_ABSENT_72bcf"})
            assert absent.structuredContent["total"] == 0
            canonical = await client.call_tool("search_history", {"query": "Gemini"})
            assert canonical.structuredContent["total"] == 0

    anyio.run(check)
