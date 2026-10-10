"""Isolated Memory behavior, security and concurrency acceptance."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import pytest
from cognivault.adapters.memory import SQLiteMemoryStore
from cognivault.adapters.history import SQLiteHistoryBackend
from cognivault.config import AppConfig
from cognivault.contracts import GatewayError, HistoryImportItem
from cognivault.gateway import Gateway

REF = "history:" + "a" * 64

def store(tmp_path):
    return SQLiteMemoryStore(tmp_path / "memory.db")

def test_semantic_duplicate_and_conflicting_slot(tmp_path):
    s = store(tmp_path)
    a = s.create("self", "choice", "physics", [REF], "a")
    b = s.create("self", "choice", "physics", [REF], "b")
    assert b["reused"] and a["memory"]["memory_id"] == b["memory"]["memory_id"]
    with pytest.raises(GatewayError) as e:
        s.create("self", "choice", "chemistry", [REF], "c")
    assert e.value.code == "CONFLICT"
    mid = a["memory"]["memory_id"]
    noop = s.revise(mid, "physics", [REF], 1, "d")
    assert noop["reused"] and noop["memory"]["version"] == 1
    s.revise(mid, "chemistry", [REF], 1, "e")
    assert [r["version"] for r in s.versions(mid)["versions"]] == [2, 1]
    assert s.fetch(mid, version=1)["memory"]["value"] == "physics"

def test_concurrent_same_fact_and_revision_conflict(tmp_path):
    s = store(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda i: s.create("self", "goal", "physics", [REF], f"key-{i}"), range(8)))
    assert len({r["memory"]["memory_id"] for r in records}) == 1
    mid = records[0]["memory"]["memory_id"]
    def revise(i):
        try:
            return s.revise(mid, f"goal-{i}", [REF], 1, f"rev-{i}")
        except GatewayError as e:
            return e.code
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(revise, range(4)))
    assert results.count("CONFLICT") == 3
    assert len(s.versions(mid)["versions"]) == 2

def test_atomic_rollback_after_provenance(tmp_path, monkeypatch):
    s = store(tmp_path)
    def fail(*args):
        raise sqlite3.OperationalError("injected audit failure")
    with monkeypatch.context() as m:
        m.setattr(s, "_audit", fail)
        with pytest.raises(GatewayError):
            s.create("self", "goal", "physics", [REF], "a")
    assert s.search("physics")["total"] == 0
    assert not s.create("self", "goal", "physics", [REF], "a")["reused"]

def test_reads_do_not_create_or_change_storage(tmp_path):
    s = store(tmp_path)
    assert s.search("x")["total"] == 0
    assert not s.database_path.exists()
    mid = s.create("self", "goal", "physics", [REF], "a")["memory"]["memory_id"]
    before = s.database_path.read_bytes(), s.database_path.stat().st_mtime_ns
    s.fetch(mid); s.search(REF); s.versions(mid)
    assert before == (s.database_path.read_bytes(), s.database_path.stat().st_mtime_ns)
    assert s.search(REF)["total"] == 1

def test_gateway_verified_requires_real_source_and_review(tmp_path):
    h = SQLiteHistoryBackend(tmp_path / "history.db")
    h.register_source("synthetic", "Synthetic")
    ref = h.import_items("synthetic", [HistoryImportItem("one", "chat", "user", "physics goal", "2026-01-01T00:00:00Z")])[0]
    g = Gateway(AppConfig("0.8.0"), None, h, memory_store=store(tmp_path), capabilities=frozenset({"read", "write"}))
    with pytest.raises(GatewayError):
        g.create_memory("self", "goal", "physics", [REF], "bad", epistemic_status="verified", verification_note="Reviewed explicit user statement")
    a = g.create_memory("self", "goal", "physics", [ref], "good", epistemic_status="verified", verification_note="Reviewed explicit user statement", provenance={"reported_agent":"codex"})
    assert a["memory"]["sources_resolved"] is True
    assert a["memory"]["verification_trust"] == "reported"
    assert a["memory"]["write_provenance"]["reported_agent"] == "codex"
    reader = Gateway(g.config, None, memory_store=store(tmp_path), capabilities=frozenset({"read"}))
    result = reader.fetch_memory(a["memory"]["memory_id"])["memory"]
    assert result["effective_epistemic_status"] == "unverified"
    assert not result["sources_resolved"]
    with pytest.raises(GatewayError) as e:
        reader.create_memory("self", "x", "x", [ref], "denied")
    assert e.value.code == "PERMISSION_DENIED"

def test_unsafe_path_validation(tmp_path):
    with pytest.raises(ValueError):
        SQLiteMemoryStore(tmp_path / ".." / "outside.db")

def test_mcp_rejects_false_verification_and_validates_history(tmp_path):
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session
    from cognivault.transports.mcp_stdio import create_mcp_server
    g = Gateway(AppConfig("0.8.0"), None, memory_store=store(tmp_path), capabilities=frozenset({"read", "write"}))
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as c:
            args = dict(subject="self", predicate="goal", value="physics", source_refs=[REF], idempotency_key="a", epistemic_status="inference")
            a = (await c.call_tool("create_memory", args)).structuredContent
            assert a["ok"] and a["memory"]["epistemic_status"] == "inference"
            mid = a["memory"]["memory_id"]
            versions = (await c.call_tool("memory_versions", {"memory_id":mid})).structuredContent
            assert versions["ok"] and len(versions["versions"]) == 1
            bad = await c.call_tool("fetch_memory", {"memory_id":mid, "version":True})
            assert bad.isError
    anyio.run(check)


def test_old_pr10_records_read_without_migration_and_requests_replay(tmp_path):
    s = store(tmp_path)
    a = s.create("self", "goal", "physics", [REF], "old")
    mid = a["memory"]["memory_id"]
    with sqlite3.connect(s.database_path) as c:
        c.execute("DROP TABLE memory_assessments")
        from cognivault.adapters.memory import _digest
        c.execute("UPDATE memory_requests SET request_sha256=? WHERE idempotency_key='old'", (_digest(dict(op="create",subject="self",predicate="goal",value="physics",source_refs=[REF])),))
    before = s.database_path.read_bytes()
    assert s.fetch(mid)["memory"]["epistemic_status"] == "unverified"
    assert before == s.database_path.read_bytes()
    assert s.create("self", "goal", "physics", [REF], "old")["reused"]


def test_memory_runtime_rejects_colliding_paths(tmp_path):
    from cognivault.runtime import load_gateway_from_config
    config = tmp_path / "gateway.toml"
    db = (tmp_path / "data.db").as_posix()
    config.write_text('[gateway]\nversion="0.8.0"\n[study]\nbackend="not_configured"\n[history]\nbackend="sqlite"\ndatabase="'+db+'"\n[memory]\nbackend="sqlite"\ndatabase="'+db+'"\n')
    with pytest.raises(ValueError):
        load_gateway_from_config(config)


def test_linked_sqlite_auxiliary_is_rejected(tmp_path):
    import os
    s = store(tmp_path)
    target = tmp_path / "outside"
    target.write_bytes(b"sentinel")
    aux = tmp_path / "memory.db-journal"
    os.link(target, aux)
    with pytest.raises(GatewayError):
        s.create("self", "goal", "physics", [REF], "key")
    assert target.read_bytes() == b"sentinel"


def test_memory_recovery_includes_versions_and_relocates_store(tmp_path, monkeypatch):
    from dataclasses import replace
    from test_recovery import setup
    from cognivault.recovery import service
    import shutil
    g = setup(tmp_path)
    disk = shutil.disk_usage
    monkeypatch.setattr(shutil, "disk_usage", lambda p: type(disk(p))(disk(p).total+64*1024**3,disk(p).used,disk(p).free+64*1024**3))
    g.capabilities = frozenset({"read","write","admin"})
    path = tmp_path / "production" / "memory.db"
    g.config = replace(g.config, memory_database=path)
    g.memory_store = SQLiteMemoryStore(path)
    mid = g.create_memory("self", "goal", "physics", [REF], "a")["memory"]["memory_id"]
    g.revise_memory(mid, "engineering", [REF], 1, "b")
    snapshot = g.create_recovery_snapshot("memory-synthetic")
    assert snapshot["verified"]
    restored = g.verify_recovery_snapshot("memory-synthetic", restore=True)
    folder = g.config.recovery_root / "restores" / "memory-synthetic"
    isolated = service.restored_gateway(g, folder)
    assert isolated.memory_store.database_path != path
    assert isolated.fetch_memory(mid)["memory"]["value"] == "engineering"
    assert len(isolated.memory_versions(mid)["versions"]) == 2


def test_replayed_write_receipt_does_not_claim_current_fact(tmp_path):
    g = Gateway(AppConfig("0.8.0"), None, memory_store=store(tmp_path), capabilities=frozenset({"read","write"}))
    args = dict(subject="self", predicate="goal", value="physics", source_refs=[REF], idempotency_key="one")
    mid = g.create_memory(**args)["memory"]["memory_id"]
    g.revise_memory(mid, "engineering", [REF], 1, "two")
    replay = g.create_memory(**args)
    assert replay["reused"] and replay["memory"]["version"] == 1
    assert replay["memory"]["is_current"] is False


def test_write_only_dedup_receipt_cannot_read_another_actor(tmp_path):
    g = Gateway(AppConfig("0.8.0"), None, memory_store=store(tmp_path), capabilities=frozenset({"read","write"}))
    g.create_memory("self", "goal", "physics", [REF], "one", provenance={"reported_agent":"private-original-agent", "run_id":"private-original-run"})
    g.capabilities = frozenset({"write"})
    r = g.create_memory("self", "goal", "physics", [REF], "two")
    assert "private-original" not in str(r)
    assert "write_provenance" not in r["memory"] and "recorded_at" not in r["memory"]
    assert r["receipt_only"]


def test_write_only_revision_receipt_does_not_disclose_identity(tmp_path):
    g = Gateway(AppConfig("0.8.0"), None, memory_store=store(tmp_path), capabilities=frozenset({"read","write"}))
    mid = g.create_memory("secret subject", "secret predicate", "old", [REF], "one")["memory"]["memory_id"]
    g.capabilities = frozenset({"write"})
    result = g.revise_memory(mid, "new", [REF], 1, "two")
    assert "subject" not in result["memory"] and "predicate" not in result["memory"]


def test_search_uses_one_snapshot_during_concurrent_retirement(tmp_path, monkeypatch):
    s = store(tmp_path)
    mid = s.create("self", "goal", "physics", [REF], "one")["memory"]["memory_id"]
    with sqlite3.connect(s.database_path) as c:
        c.execute("PRAGMA journal_mode=WAL")
    original = s._record
    done = False
    def interleave(con, memory_id, version=None):
        nonlocal done
        if not done:
            done = True
            SQLiteMemoryStore(s.database_path).revise(mid, "retired", [REF], 1, "two", retire=True)
        return original(con, memory_id, version)
    monkeypatch.setattr(s, "_record", interleave)
    result = s.search("physics")
    assert result["total"] == 1 and result["memories"][0]["state"] == "active"
    assert result["memories"][0]["value"] == "physics"


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_memory_runtime_rejects_other_database_auxiliaries(tmp_path, suffix):
    from cognivault.runtime import load_gateway_from_config
    db = (tmp_path / "history.db").as_posix()
    c = tmp_path / "config.toml"
    c.write_text('[gateway]\nversion="0.8.0"\n[study]\nbackend="not_configured"\n[history]\nbackend="sqlite"\ndatabase="'+db+'"\n[memory]\nbackend="sqlite"\ndatabase="'+db+suffix+'"\n')
    with pytest.raises(ValueError):
        load_gateway_from_config(c)
