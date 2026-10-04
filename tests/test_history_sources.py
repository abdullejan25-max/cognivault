"""Source evidence tests contain only hand-authored synthetic bytes."""
import hashlib
import sqlite3

import pytest

from cognivault.adapters.history import SQLiteHistoryBackend
from cognivault.adapters.history_sources import SourceEvidenceStore, SourceFileInput
from cognivault.contracts import GatewayError
from cognivault.provenance import ReportedIdentity


def source(raw=b'{"synthetic":"unknown roles/time"}\n', **kw):
    values = dict(source_system="codex", source_format="jsonl", record_kind="raw_session",
                  content=raw, expected_sha256=hashlib.sha256(raw).hexdigest())
    return SourceFileInput(**(values | kw))


def store(tmp_path):
    path=tmp_path / "isolated.sqlite3"
    SQLiteHistoryBackend(path).initialize()
    return SourceEvidenceStore(path)


def test_exact_raw_evidence_dedup_rerun_and_nullable_occurrence(tmp_path):
    s=store(tmp_path)
    first=s.import_bytes(source(), reported_identity=ReportedIdentity("Synthetic", "Synthetic", "synthetic-1"))
    second=s.import_bytes(source(), reported_identity=ReportedIdentity("Different reported client"))
    assert first["source_id"]==second["source_id"]
    assert first["reused"] is False and second["reused"] is True
    assert first["source"]["occurred_at"] is None
    assert first["source"]["importer_version"]=="p13-source-evidence-1"
    assert first["source"]["source_fingerprint"]==first["source_id"].split(":")[1]
    assert first["source"]["write_provenance"]==second["source"]["write_provenance"]
    assert s.fetch(first["source_id"], 0, 65536)["content"]==source().content
    assert s.summary()["source_records"]==1
    assert s.summary()["stored_payloads"]==1
    assert s.summary()["canonical_messages_created"]==0


def test_malformed_binary_preserved_and_metadata_never_makes_messages(tmp_path):
    s=store(tmp_path)
    item=source(b'\xff\x00SYNTHETIC MALFORMED', source_system="workbuddy", source_format="json", record_kind="session_metadata")
    r=s.import_bytes(item)
    assert s.fetch(r["source_id"],0,65536)["content"]==item.content
    assert r["source"]["record_kind"]=="session_metadata"
    assert r["source"]["normalization_state"]=="not_normalized"


def test_same_bytes_distinct_origin_share_storage_and_preserve_origins(tmp_path):
    s=store(tmp_path)
    a=s.import_bytes(source())
    b=s.import_bytes(source(source_system="hermes"))
    assert a["source_id"]!=b["source_id"]
    assert s.summary()["source_records"]==2 and s.summary()["stored_payloads"]==1
    assert s.summary()["byte_equivalent_pairs"]==1


def test_bad_expected_digest_is_rejected_without_records(tmp_path):
    s=store(tmp_path)
    with pytest.raises(GatewayError,match="digest"):
        s.import_bytes(source(expected_sha256="0"*64))
    assert s.summary()["source_records"]==0


def test_reopen_read_provenance_integrity_and_bounded_source_search(tmp_path):
    s=store(tmp_path)
    first=s.import_bytes(source())
    reopened=SourceEvidenceStore(tmp_path/"isolated.sqlite3")
    assert reopened.search(source_system="codex")["total"]==1
    assert reopened.search(source_system="gemini")["total"]==0
    found=reopened.fetch(first["source_id"],0,1)
    assert found["content"]==b'{' and found["has_more"] is True
    assert found["source"]["write_provenance"]["source_system"]=="codex"
    assert found["source"]["write_provenance"]["data_origin"]=="imported"
    with pytest.raises(GatewayError): reopened.fetch(first["source_id"],0,65537)


def test_existing_legacy_reference_reuses_original_bytes_without_new_payload(tmp_path):
    from cognivault.adapters.legacy_sources import SQLiteLegacySourceStore, LegacySourceInput
    s=store(tmp_path)
    legacy=SQLiteLegacySourceStore(tmp_path/"isolated.sqlite3")
    legacy.initialize()
    raw=source().content
    old=legacy.import_batch([LegacySourceInput("codex-source","synthetic-record","codex_jsonl",raw,
                            hashlib.sha256(raw).hexdigest(),0)],import_batch_id="synthetic-batch")[0]
    linked=s.link_legacy(source_system="codex",legacy_record_id=old,source_format="jsonl",record_kind="raw_session")
    assert linked["source"]["evidence_refs"]==[old]
    assert s.summary()["stored_payloads"]==0
    assert s.fetch(linked["source_id"],0,65536)["content"]==raw
    repeated=s.import_bytes(source())
    assert repeated["source_id"]==linked["source_id"] and repeated["reused"] is True
    assert s.summary()["stored_payloads"]==0


def test_external_manifest_is_referenced_with_no_fake_payload(tmp_path):
    s=store(tmp_path)
    raw=b'SYNTHETIC ZIP BYTES'
    r=s.link_manifest(source_system="gemini",source_sha256=hashlib.sha256(raw).hexdigest(),
                      byte_count=len(raw),document_uri="document://sha256/"+"a"*64,
                      descriptor={"archive_parts":[{"asset_uri":"asset://sha256/"+"b"*64,"offset":0,"bytes":len(raw)}]})
    assert r["source"]["payload_kind"]=="manifest"
    assert r["source"]["evidence_refs"]==["document://sha256/"+"a"*64]
    assert s.summary()["stored_payloads"]==0
    with pytest.raises(GatewayError,match="resolver"):
        s.fetch(r["source_id"],0,1)


def test_interrupted_provenance_write_rolls_back_record_and_payload(tmp_path, monkeypatch):
    import cognivault.adapters.history_sources as module
    s=store(tmp_path)
    def fail(*args, **kwargs):
        raise GatewayError("CONFLICT", "Synthetic interrupted provenance write")
    monkeypatch.setattr(module, "insert_provenance", fail)
    with pytest.raises(GatewayError): s.import_bytes(source())
    assert s.summary()["source_records"] == 0
    assert s.summary()["stored_payloads"] == 0


def test_verification_hashes_all_original_bytes_and_rejects_bad_input_types(tmp_path):
    s=store(tmp_path)
    raw=b'SYNTHETIC DATA' * 12000
    r=s.import_bytes(source(raw))
    proof=s.verify(r["source_id"])
    assert proof["sha256"] == hashlib.sha256(raw).hexdigest()
    assert proof["byte_count"] == len(raw) and proof["verified"] is True
    with pytest.raises(GatewayError): s.import_bytes(source(source_system=[]))
    with pytest.raises(GatewayError): s.search(source_system=[])


def test_no_result_search_over_large_catalog_remains_bounded(tmp_path):
    from cognivault.adapters.history_sources import source_identity
    s=store(tmp_path)
    s.import_bytes(source())
    # Invented rows deliberately have no provenance: the no-result query must
    # never materialize their descriptors or attempt metadata validation.
    with sqlite3.connect(s.database_path) as c:
        c.executemany("INSERT INTO history_source_files VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",[
            (source_identity("other_agent",f'{i:064x}'),"other_agent","unknown","unknown",f'{i:064x}',0,None,
             "2026-01-01T00:00:00Z","inline",f'{i:064x}',"[]","{}") for i in range(25001)])
    assert s.search(query="NO_SYNTHETIC_RESULT")["total"]==0
    assert s.search(source_system="codex",limit=1)["total"]==1


def test_equivalent_occurrence_time_representation_reuses_source(tmp_path):
    s=store(tmp_path)
    first=s.import_bytes(source(occurred_at="2026-01-01T08:00:00+08:00"))
    second=s.import_bytes(source(occurred_at="2026-01-01T00:00:00Z"))
    assert second["reused"] is True and second["source_id"]==first["source_id"]
    assert first["source"]["occurred_at"]=="2026-01-01T00:00:00Z"


def test_large_blob_stream_verify_detects_corrupt_payload(tmp_path):
    s=store(tmp_path)
    raw=b'SYNTHETIC STREAM'*(1024*1024)
    r=s.import_bytes(source(raw))
    assert s.verify(r["source_id"])["verified"] is True
    assert s.fetch(r["source_id"],len(raw)-5,65536)["content"]==raw[-5:]
    with sqlite3.connect(s.database_path) as c:
        c.execute("DROP TRIGGER source_payload_no_update")
        c.execute("UPDATE history_source_payloads SET content=?",(b'corrupt',))
    with pytest.raises(GatewayError): s.verify(r["source_id"])
