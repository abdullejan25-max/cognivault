"""Canonical tests use temporary isolated targets and fully invented sources."""
import base64
from dataclasses import replace
import hashlib
import json
import pytest
import anyio
from mcp.shared.memory import create_connected_server_and_client_session
from cognivault.adapters.history import SQLiteHistoryBackend
from cognivault.adapters.history_sources import SourceFileInput, SourceEvidenceStore
from cognivault.config import AppConfig
from cognivault.contracts import GatewayError
from cognivault.gateway import Gateway
from cognivault.transports.mcp_stdio import create_mcp_server


def setup(tmp_path):
    db = tmp_path / "history.sqlite3"
    SQLiteHistoryBackend(db).initialize()
    inbox = tmp_path / "private-inbox"; inbox.mkdir()
    g = Gateway(AppConfig("0.6.0", history_database=db, history_migration_inbox=inbox), None,
                SQLiteHistoryBackend(db), capabilities=frozenset({"read", "ingest"}))
    return g, SourceEvidenceStore(db)


def source(store, *, suffix="", time=None, role="user", malformed=False):
    rows = [{"type":"session_meta", "payload":{"id":"synthetic-session"}},
            {"type":"response_item", "timestamp":time, "payload":{"type":"message", "role":role,
             "content":[{"type":"input_text", "text":"Synthetic content"+suffix}]}}]
    raw = ("\n".join(json.dumps(r) for r in rows)+"\n"+('{broken}\n' if malformed else '')).encode()
    return store.import_bytes(SourceFileInput("codex", "jsonl", "raw_session", raw, hashlib.sha256(raw).hexdigest()))["source_id"]


def test_normalization_reopen_rerun_source_provenance_and_nullable_time(tmp_path):
    g,s=setup(tmp_path); sid=source(s)
    snap=g.history_normalization_snapshot()
    first=g.normalize_history_sources(snap["source_set_sha256"])
    assert first["new_messages"] == 1 and first["new_conversations"] == 1
    rerun=g.normalize_history_sources(snap["source_set_sha256"])
    assert rerun["reused_sources"] == 1 and rerun["new_messages"] == 0
    summary=g.canonical_history_summary()
    assert summary["messages"] == 1 and summary["source_outcomes"] == 1
    c=g.search_canonical_conversations(source_system="codex")["conversations"][0]
    v=g.fetch_canonical_conversation(c["conversation_id"])["views"][0]
    m=g.fetch_canonical_message(v["messages"][0]["message_id"])
    assert m["message"]["occurred_at"] is None
    assert m["message"]["provenance"]["data_origin"] == "deterministic_derived"
    assert m["message"]["source_refs"] == [sid]
    assert m["message"]["provenance"]["source_refs"] == [sid]
    assert g.search_history_sources()["sources"][0]["normalization_state"] == "normalized"
    reopened=Gateway(g.config,None,SQLiteHistoryBackend(s.database_path),capabilities=g.capabilities)
    assert reopened.fetch_canonical_message(v["messages"][0]["message_id"]) == m
    assert g.search_canonical_conversations(query="SYNTHETIC_NO_RESULT")["total"] == 0
    assert g.history_source_summary()["canonical_messages_created"] == 0


def test_source_set_addition_requires_new_snapshot_and_retains_existing_results(tmp_path):
    g,s=setup(tmp_path); source(s)
    old=g.history_normalization_snapshot()["source_set_sha256"]
    source(s,suffix=" revision")
    with pytest.raises(GatewayError,match="source set"): g.normalize_history_sources(old)
    snap=g.history_normalization_snapshot()
    first=g.normalize_history_sources(snap["source_set_sha256"])
    assert first["new_conversations"] == 1 and first["new_messages"] == 2
    assert g.canonical_history_summary()["views"] == 2


def test_malformed_source_is_durable_source_only_with_atomic_zero_messages(tmp_path):
    g,s=setup(tmp_path); sid=source(s,malformed=True)
    first=g.normalize_history_sources(g.history_normalization_snapshot()["source_set_sha256"])
    assert first["new_messages"] == 0 and first["results"][0]["state"] == "malformed"
    assert g.canonical_history_summary()["by_state"] == {"malformed":1}
    assert g.verify_history_source(sid)["verified"] is True
    assert g.normalize_history_sources(first["source_set_sha256"])["reused_sources"] == 1
    assert first["receipt"]["sha256"]


def test_message_read_is_bounded_and_position_is_not_timestamp_order(tmp_path):
    g,s=setup(tmp_path)
    source(s,suffix="x"*100000,time="2026-01-02T00:00:00Z")
    g.normalize_history_sources(g.history_normalization_snapshot()["source_set_sha256"])
    c=g.search_canonical_conversations()["conversations"][0]
    v=g.fetch_canonical_conversation(c["conversation_id"])["views"][0]
    assert v["messages"][0]["position"] == 2
    mid=v["messages"][0]["message_id"]
    first=g.fetch_canonical_message(mid,length=100)
    second=g.fetch_canonical_message(mid,offset=100,length=100)
    assert len(base64.b64decode(first["content_base64"])) == 100
    assert first["has_more"] is True and first["content_base64"] != second["content_base64"]
    with pytest.raises(GatewayError): g.fetch_canonical_message(mid,length=1000000)


def test_gateway_permissions_and_mcp_contract_and_no_result(tmp_path):
    g,s=setup(tmp_path); source(s)
    snap=g.history_normalization_snapshot()["source_set_sha256"]
    g.capabilities=frozenset({"read"})
    with pytest.raises(GatewayError): g.normalize_history_sources(snap)
    g.capabilities=frozenset({"read","ingest"})
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            tools={t.name for t in (await client.list_tools()).tools}
            assert "normalize_history_sources" in tools and "search_canonical_conversations" in tools
            r=(await client.call_tool("normalize_history_sources",{"source_set_sha256":snap})).structuredContent
            assert r["ok"] is True and r["new_messages"] == 1
            bad=(await client.call_tool("canonical_history_summary",{"extra":"value"})).structuredContent
            assert bad["ok"] is False
    anyio.run(check)


def test_canonical_store_failure_rolls_back_every_derived_row(tmp_path):
    from cognivault.normalization.adapters import normalize_source
    g,s=setup(tmp_path); sid=source(s)
    metadata=s.metadata(sid)
    raw=s.fetch(sid)["content"]
    parsed=normalize_source("codex","jsonl",raw,metadata["source_fingerprint"])
    original=parsed["views"][0]["messages"][0]
    parsed["views"][0]["messages"].append(dict(original,message_id="message:"+"b"*64))
    with pytest.raises(GatewayError):
        g._canonical_history_store().persist(metadata,parsed,g.history_normalization_snapshot()["source_set_sha256"])
    assert g.canonical_history_summary()["messages"] == 0
    assert g.canonical_history_summary()["conversations"] == 0


def test_source_addition_during_reuse_returns_conflict_not_old_complete_set(tmp_path,monkeypatch):
    g,s=setup(tmp_path); source(s)
    snap=g.history_normalization_snapshot()["source_set_sha256"]
    g.normalize_history_sources(snap)
    store=g._canonical_history_store()
    original=store.outcome
    def race(sid):
        result=original(sid)
        source(s,suffix="Added while reused")
        return result
    monkeypatch.setattr(store,"outcome",race)
    monkeypatch.setattr(g,"_canonical_history_store",lambda:store)
    with pytest.raises(GatewayError,match="source set"): g.normalize_history_sources(snap)


def test_integrity_proves_every_message_and_reparse_result_then_detects_corruption(tmp_path):
    import sqlite3
    g,s=setup(tmp_path); source(s)
    g.normalize_history_sources(g.history_normalization_snapshot()["source_set_sha256"])
    first=g.verify_canonical_history(reparse=True)
    assert first["verified"] is True and first["messages_verified"] == 1 and first["source_outcomes_verified"] == 1
    assert g.verify_canonical_history(reparse=True)["canonical_sha256"] == first["canonical_sha256"]
    with sqlite3.connect(s.database_path) as c:
        c.execute("DROP TRIGGER p13_messages_no_update")
        c.execute("UPDATE p13_messages SET role='assistant'")
    assert g.verify_canonical_history()["verified"] is False


@pytest.mark.parametrize("field",["source_id","source_fingerprint","source_imported_at","version"])
def test_source_evidence_mismatch_returns_bounded_safe_field_diagnostics(tmp_path,field):
    import sqlite3
    g,s=setup(tmp_path); sid=source(s)
    g.normalize_history_sources(g.history_normalization_snapshot()["source_set_sha256"],limit=64)
    marker="private-import-timestamp-should-not-leak"
    with sqlite3.connect(s.database_path) as c:
        c.execute("DROP TRIGGER p13_message_evidence_no_update")
        row=c.execute("SELECT rowid,payload FROM p13_message_evidence").fetchone()
        evidence=json.loads(row[1]); evidence[field]=marker
        c.execute("UPDATE p13_message_evidence SET payload=? WHERE rowid=?",(json.dumps(evidence),row[0]))

    result=g.verify_canonical_history()
    assert result["verified"] is False
    assert result["error_classes"] == ["source_evidence_mismatch"]
    diagnostic=result["diagnostics"]["samples"][0]
    assert diagnostic["source_id"] == sid
    assert diagnostic["record_class"] == "message_evidence"
    assert diagnostic["field"] == field
    assert len(diagnostic["expected_sha256"]) == len(diagnostic["actual_sha256"]) == 64
    assert result["diagnostics"]["by_field"] == {field: 1}
    assert marker not in json.dumps(result)


def test_source_evidence_version_is_checked_against_owning_view_version(tmp_path):
    import sqlite3
    g,s=setup(tmp_path); source(s)
    g.normalize_history_sources(g.history_normalization_snapshot()["source_set_sha256"])
    prior_version="p12-normalize-1"
    with sqlite3.connect(s.database_path) as c:
        c.execute("DROP TRIGGER p13_views_no_update")
        c.execute("DROP TRIGGER p13_message_evidence_no_update")
        row=c.execute("SELECT view_id,payload FROM p13_views").fetchone()
        view=json.loads(row[1]); view["version"]=prior_version
        c.execute("UPDATE p13_views SET version=?,payload=? WHERE view_id=?",
                  (prior_version,json.dumps(view),row[0]))
        evidence=c.execute("SELECT rowid,payload FROM p13_message_evidence WHERE view_id=?",(row[0],)).fetchone()
        payload=json.loads(evidence[1]); payload["version"]=prior_version
        c.execute("UPDATE p13_message_evidence SET payload=? WHERE rowid=?",
                  (json.dumps(payload),evidence[0]))
        c.execute("DROP TRIGGER p13_normalization_outcomes_no_update")
        outcome=c.execute("SELECT payload FROM p13_normalization_outcomes").fetchone()[0]
        outcome_payload=json.loads(outcome); outcome_payload["message_appearances"]=0
        c.execute("UPDATE p13_normalization_outcomes SET payload=?",(json.dumps(outcome_payload),))

    result=g.verify_canonical_history()
    assert result["verified"] is True, result["error_classes"]
    assert result["diagnostics"]["count"] == 0
    with sqlite3.connect(s.database_path) as c:
        c.execute("UPDATE p13_message_evidence SET payload=json_set(payload,'$.version','wrong-version')")
    mismatch=g.verify_canonical_history()
    assert mismatch["error_classes"] == ["source_evidence_mismatch"]
    assert mismatch["diagnostics"]["by_field"] == {"version":1}


def test_source_evidence_diagnostics_are_bounded_and_count_all_mismatches(tmp_path):
    import sqlite3
    g,s=setup(tmp_path)
    for index in range(21): source(s,suffix=f" evidence-{index}")
    g.normalize_history_sources(g.history_normalization_snapshot()["source_set_sha256"],limit=64)
    with sqlite3.connect(s.database_path) as c:
        c.execute("DROP TRIGGER p13_message_evidence_no_update")
        c.execute("UPDATE p13_message_evidence SET payload=json_set(payload,'$.source_imported_at','private-marker')")

    diagnostics=g.verify_canonical_history()["diagnostics"]
    assert diagnostics["count"] == 21
    assert diagnostics["by_field"] == {"source_imported_at": 21}
    assert len(diagnostics["samples"]) == 20
    assert diagnostics["truncated"] is True


def test_malformed_evidence_payload_is_reported_without_echoing_it(tmp_path):
    import sqlite3
    g,s=setup(tmp_path); source(s)
    g.normalize_history_sources(g.history_normalization_snapshot()["source_set_sha256"])
    marker="private-malformed-evidence-value"
    with sqlite3.connect(s.database_path) as c:
        c.execute("DROP TRIGGER p13_message_evidence_no_update")
        c.execute("UPDATE p13_message_evidence SET payload=?",("{broken:"+marker,))

    result=g.verify_canonical_history()
    assert result["error_classes"] == ["source_evidence_mismatch"]
    assert result["diagnostics"]["by_field"] == {"payload": 1}
    assert marker not in json.dumps(result)


def test_conversation_view_pagination_reports_all_views(tmp_path):
    g,s=setup(tmp_path); source(s); source(s,suffix="Other evidence")
    g.normalize_history_sources(g.history_normalization_snapshot()["source_set_sha256"])
    cid=g.search_canonical_conversations()["conversations"][0]["conversation_id"]
    a=g.fetch_canonical_conversation(cid,view_limit=1)
    b=g.fetch_canonical_conversation(cid,view_limit=1,view_offset=1)
    assert a["total_views"] == 2 and a["has_more_views"] is True and b["has_more_views"] is False
    assert a["views"][0]["view_id"] != b["views"][0]["view_id"]


def test_tree_nodes_are_paged_independently_of_messages(tmp_path):
    g,s=setup(tmp_path)
    mapping={"root":{"id":"root","parent":None,"children":["u"]+[str(i) for i in range(200)],"message":None},
             "u":{"id":"u","parent":"root","children":[],"message":{"id":"u","author":{"role":"user"},"content":{"content_type":"text","parts":["Synthetic"]}}}}
    mapping.update({str(i):{"id":str(i),"parent":"root","children":[],"message":None} for i in range(200)})
    raw=json.dumps([{"id":"synthetic-tree","mapping":mapping}]).encode()
    s.import_bytes(SourceFileInput("chatgpt","json","conversation_export",raw,hashlib.sha256(raw).hexdigest()))
    g.normalize_history_sources(g.history_normalization_snapshot()["source_set_sha256"])
    cid=g.search_canonical_conversations()["conversations"][0]["conversation_id"]
    a=g.fetch_canonical_conversation(cid,limit=1,node_limit=1)
    b=g.fetch_canonical_conversation(cid,limit=1,node_limit=1,node_offset=1)
    assert len(a["views"][0]["nodes"]) == 1 and a["views"][0]["total_nodes"] == 202
    assert a["views"][0]["has_more_nodes"] is True
    assert a["views"][0]["nodes"][0]["key"] != b["views"][0]["nodes"][0]["key"]
    assert len(json.dumps(a)) < 10000


def test_small_message_range_never_selects_or_decodes_full_payload(tmp_path,monkeypatch):
    g,s=setup(tmp_path); source(s,suffix="x"*2000000)
    g.normalize_history_sources(g.history_normalization_snapshot()["source_set_sha256"])
    cid=g.search_canonical_conversations()["conversations"][0]["conversation_id"]
    mid=g.fetch_canonical_conversation(cid)["views"][0]["messages"][0]["message_id"]
    store=g._canonical_history_store(); original=store.history._connect; queries=[]
    def connection(*args,**kwargs):
        c=original(*args,**kwargs); c.set_trace_callback(queries.append); return c
    monkeypatch.setattr(store.history,"_connect",connection)
    monkeypatch.setattr(g,"_canonical_history_store",lambda:store)
    r=g.fetch_canonical_message(mid,length=1)
    assert len(base64.b64decode(r["content_base64"])) == 1
    assert not any("SELECT * FROM p13_messages" in q or "SELECT payload FROM p13_messages" in q for q in queries)


@pytest.mark.parametrize("kind",["metadata","view","evidence","node"])
def test_reparse_verification_detects_corrupted_read_metadata_and_structure(tmp_path,kind):
    import sqlite3
    g,s=setup(tmp_path)
    mapping={"r":{"id":"r","parent":None,"children":["u"],"message":None},
             "u":{"id":"u","parent":"r","children":[],"message":{"id":"u","author":{"role":"user"},"content":{"content_type":"text","parts":["Synthetic"]}}}}
    raw=json.dumps([{"id":"synthetic","mapping":mapping}]).encode()
    s.import_bytes(SourceFileInput("chatgpt","json","conversation_export",raw,hashlib.sha256(raw).hexdigest()))
    g.normalize_history_sources(g.history_normalization_snapshot()["source_set_sha256"])
    table={"metadata":"p13_messages","view":"p13_views","evidence":"p13_message_evidence","node":"p13_view_nodes"}[kind]
    column="metadata" if kind=="metadata" else "payload"
    with sqlite3.connect(s.database_path) as c:
        c.execute(f"DROP TRIGGER {table}_no_update")
        row=c.execute(f"SELECT rowid,{column} FROM {table} LIMIT 1").fetchone()
        value=json.loads(row[1])
        if kind=="metadata": value["role"]="assistant"
        elif kind=="node": value["parent_key"]="u"
        else: value["locator"]={"pointer":"/wrong"}
        c.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?",(json.dumps(value),row[0]))
    assert g.verify_canonical_history(reparse=True)["verified"] is False


@pytest.mark.parametrize("kind",["view_relation","exact_count","orphan_conversation","orphan_evidence","conversation_provenance","view_provenance"])
def test_complete_materialization_verification_rejects_extra_rows_wrong_counts_or_missing_provenance(tmp_path,kind):
    import sqlite3
    g,s=setup(tmp_path); source(s)
    g.normalize_history_sources(g.history_normalization_snapshot()["source_set_sha256"])
    assert g.verify_canonical_history(reparse=True)["verified"] is True
    with sqlite3.connect(s.database_path) as c:
        if kind=="view_relation":
            c.execute("DROP TRIGGER p13_views_no_update")
            c.execute("UPDATE p13_views SET conversation_id=?",("conversation:"+"f"*64,))
        elif kind=="exact_count":
            c.execute("DROP TRIGGER p13_normalization_outcomes_no_update")
            value=json.loads(c.execute("SELECT payload FROM p13_normalization_outcomes").fetchone()[0]); value["exact"]=999
            c.execute("UPDATE p13_normalization_outcomes SET payload=?",(json.dumps(value),))
        elif kind=="orphan_conversation":
            c.execute("INSERT INTO p13_conversations VALUES (?,?,?,?)",("conversation:"+"f"*64,"codex","native:"+"f"*64,"2026-01-01T00:00:00Z"))
        elif kind=="orphan_evidence":
            value=c.execute("SELECT message_id,position,payload FROM p13_message_evidence").fetchone()
            c.execute("INSERT INTO p13_message_evidence VALUES (?,?,?,?)",("view:"+"f"*64,*value))
        else:
            c.execute("DROP TRIGGER write_provenance_no_delete")
            c.execute("DELETE FROM write_provenance WHERE record_type=?",("canonical_conversation" if kind=="conversation_provenance" else "canonical_view",))
    assert g.verify_canonical_history(reparse=True)["verified"] is False


@pytest.mark.parametrize("kind",["canonical_conversation","canonical_view","normalization_outcome"])
def test_unknown_source_time_cannot_be_fabricated_in_derived_provenance(tmp_path,kind):
    import sqlite3
    g,s=setup(tmp_path); source(s)
    g.normalize_history_sources(g.history_normalization_snapshot()["source_set_sha256"])
    with sqlite3.connect(s.database_path) as c:
        c.execute("DROP TRIGGER write_provenance_no_update")
        c.execute("UPDATE write_provenance SET original_created_at='2026-01-01T00:00:00Z' WHERE record_type=?",(kind,))
    assert g.verify_canonical_history(reparse=True)["verified"] is False
