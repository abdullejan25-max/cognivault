"""Restored Memory readback uses only invented temporary stores."""
from dataclasses import replace

import pytest

from cognivault.adapters.memory import SQLiteMemoryStore
from cognivault.contracts import GatewayError
from cognivault.gateway import Gateway
from cognivault.recovery.io import sqlite_proof
from cognivault.recovery.service import domain_readback


def fixture(tmp_path):
    from test_evidence_bundle import scenario
    from test_memory_candidates import propose
    g, ref, *_, memory = scenario(tmp_path)
    g.capabilities |= {"admin"}
    mid = memory["memory"]["memory_id"]
    g.revise_memory(mid, "revised synthetic goal", [ref], 1, "recovery-revision",
                    target_guard=g.fetch_memory(mid)["memory"]["target_guard"], provenance={"reported_agent": "synthetic"})
    approved = propose(g, ref, predicate="long_term_goal")
    g.review_memory_candidate(approved["candidate_id"], "approve", "Reviewed original")
    g.commit_memory_candidate(approved["candidate_id"])
    propose(g, ref, predicate="pending_goal")
    proof = sqlite_proof(g.memory_store.database_path)
    # The selected adapter is already a synthetic isolated store; make all reads
    # obey the same capability restriction as a real restored Gateway.
    g.capabilities = frozenset({"read"})
    return g, {"files": [{"relative": "memory.sqlite3", "sqlite_proof": proof}]}


def test_memory_readback_versions_provenance_and_candidate_states(tmp_path, monkeypatch):
    g, manifest = fixture(tmp_path)
    calls = []
    for name in ("fetch_memory", "memory_versions", "fetch_memory_request", "fetch_memory_candidate", "list_memory_candidates", "verify_memory_integrity"):
        original = getattr(g, name)
        def record(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)
        monkeypatch.setattr(g, name, record)
    before = g.memory_store.database_path.read_bytes()
    proof = domain_readback(g, manifest)["memory"]
    assert proof["state"] == "verified" and proof["integrity_verified"]
    assert proof["facts"] == 2 and proof["versions"] == 3
    assert proof["pending_candidates"] == 1 and proof["approved_candidates"] == 1
    assert proof["source_refs_preserved"] and proof["provenance_preserved"]
    assert proof["expected_readback_sha256"] == proof["actual_readback_sha256"]
    assert set(calls) == {"fetch_memory", "memory_versions", "fetch_memory_request", "fetch_memory_candidate", "list_memory_candidates", "verify_memory_integrity"}
    assert g.memory_store.database_path.read_bytes() == before
    assert "memory:" not in str(proof) and str(tmp_path) not in str(proof)


@pytest.mark.parametrize("damage", ["manifest", "gateway", "integrity"])
def test_memory_readback_rejects_count_payload_or_integrity_mismatch(tmp_path, monkeypatch, damage):
    g, manifest = fixture(tmp_path)
    if damage == "manifest":
        manifest["files"][0]["sqlite_proof"]["tables"]["memory_versions"]["rows"] += 1
    elif damage == "gateway":
        original = g.fetch_memory
        def altered(*args, **kwargs):
            result = original(*args, **kwargs)
            result["memory"]["source_refs"] = []
            return result
        monkeypatch.setattr(g, "fetch_memory", altered)
    else:
        monkeypatch.setattr(g, "verify_memory_integrity", lambda: {"verified": False, "complete": False})
    with pytest.raises(GatewayError):
        domain_readback(g, manifest)


def test_memory_readback_legacy_unconfigured_and_initialized_empty(tmp_path):
    from cognivault.config import AppConfig
    g = Gateway(AppConfig("0.8.0"), None)
    assert domain_readback(g, {"files": []})["memory"]["state"] == "not_configured"
    path = tmp_path / "memory.db"
    g.memory_store = SQLiteMemoryStore(path)
    g.config = replace(g.config, memory_database=path)
    g.memory_store.initialize()
    result = domain_readback(g, {"files": [{"relative": "memory.sqlite3", "sqlite_proof": sqlite_proof(path)}]})["memory"]
    assert result["state"] == "empty" and result["integrity_verified"]
    assert result["versions_read_back"] == 0


def test_full_snapshot_restore_includes_memory_gateway_readback(tmp_path, monkeypatch):
    import shutil
    from test_recovery import setup
    (tmp_path / 'invented-memory').mkdir()
    source, _ = fixture(tmp_path / 'invented-memory')
    g = setup(tmp_path)
    g.config = replace(g.config, memory_database=source.memory_store.database_path)
    g.memory_store = source.memory_store
    disk = shutil.disk_usage
    monkeypatch.setattr(shutil, 'disk_usage', lambda p: type(disk(p))(
        disk(p).total + 64 * 1024**3, disk(p).used, disk(p).free + 64 * 1024**3))
    g.create_recovery_snapshot('memory-gateway-proof')
    proof = g.verify_recovery_snapshot('memory-gateway-proof', restore=True)['domain_readback']['memory']
    assert proof['state'] == 'verified' and proof['integrity_verified']
    assert proof['versions'] == 3 and proof['versions_read_back'] == 3
    assert proof['approved_candidates'] == 1 and proof['pending_candidates'] == 1


def test_memory_gateway_readback_is_explicitly_bounded(tmp_path):
    from cognivault.config import AppConfig
    path = tmp_path / 'memory.db'
    store = SQLiteMemoryStore(path)
    ref = 'history:' + 'a' * 64
    first = store.create('synthetic', 'choice', 'version 1', [ref], 'create')['memory']
    for version in range(2, 26):
        store.revise(first['memory_id'], f'version {version}', [ref], version - 1, f'revise-{version}')
    g = Gateway(AppConfig('0.8.0', memory_database=path), None, memory_store=store)
    unavailable = g.fetch_memory(first['memory_id'])['memory']
    assert not unavailable['sources_resolved']
    assert unavailable['source_resolution'][0]['status'] == 'unavailable'
    proof = domain_readback(g, {'files': [{'relative': 'memory.sqlite3', 'sqlite_proof': sqlite_proof(path)}]})['memory']
    assert proof['versions'] == 25 and proof['versions_read_back'] == 20
    assert proof['requests_read_back'] == 20
    assert proof['state'] == 'verified'
    assert proof['source_refs_preserved'] and proof['provenance_preserved']
    assert 'source_refs_verified' not in proof
    assert 'source availability, semantic truth and currency not established' in proof['scope']
    assert proof['scope'].startswith('bounded_gateway_payloads;')
