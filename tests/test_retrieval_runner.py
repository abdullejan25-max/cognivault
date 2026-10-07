"""Safety and synthetic Gateway integration, not a quality baseline run."""
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def runner():
    assert (Path(__file__).resolve().parents[1] / "scripts/retrieval_runner.py").exists(), "runner missing"
    import retrieval_runner
    return retrieval_runner


def test_frozen_fixture_shape_hash_and_roundtrip():
    r = runner()
    manifest, cases, digest = r.load_fixture()
    assert len(cases) == 32
    assert len(digest) == 64
    for domain in ("study", "documents", "wrong_answer", "history"):
        assert len([c for c in cases if c["domain"] == domain]) == 8
        assert len(manifest["candidates"][domain]) >= 10
    assert r.fixture_hash(json.loads(json.dumps(manifest)), json.loads(json.dumps(cases))) == digest


def test_existing_arbitrary_root_is_rejected_without_mutation(tmp_path):
    r = runner()
    marker = tmp_path / "sentinel"
    marker.write_text("keep")
    with pytest.raises(ValueError):
        r.check_root(tmp_path, "unknown-token")
    assert list(tmp_path.iterdir()) == [marker]


def test_synthetic_setup_binds_ids_without_querying_and_reopens_gateway(monkeypatch):
    r = runner()
    from cognivault.gateway import Gateway
    def forbidden(*args, **kwargs):
        raise AssertionError("Fixture alias binding must not search")
    for name in ("search_documents", "search_wrong_answers", "search_canonical_conversations", "search_history_sources", "search_study"):
        monkeypatch.setattr(Gateway, name, forbidden)
    ingests = []
    native_ingest = Gateway.ingest_history_sources
    def tracked_ingest(gateway, *args, **kwargs):
        result = native_ingest(gateway, *args, **kwargs)
        ingests.append(result)
        return result
    monkeypatch.setattr(Gateway, "ingest_history_sources", tracked_ingest)
    manifest, cases, digest = r.load_fixture()
    root, token = r.create_root(digest)
    state = r.prepare(root, token, manifest, digest)
    assert state["fixture_hash"] == digest
    assert state["aliases"]["book-a:p65:c1"].startswith("chunk://")
    assert state["aliases"]["conversation:fractions"].startswith("conversation:")
    assert state["aliases"]["wrong:fractions"].startswith("wrong-answer://")
    assert state["profiles"] == ["default", "capacity-2049"]
    assert len(ingests) == 1 and ingests[0]["imported"] == 17 and ingests[0]["errors"] == 0
    monkeypatch.undo()
    gateway = r.gateway_for(root, state)
    result = gateway.search_documents("p15_absent_integration_only", 10)
    assert result["results"] == []
    case = {"domain": "documents", "path": "documents", "query": "p15_absent_integration_only", "filters": {}}
    sample = r.sample_query(gateway, case, state)
    assert sample["status"] == "ok" and sample["ranking"] == []
    assert sample["elapsed_ms"] >= 0
    probe = {**case, "case_id": "integration-only", "domain": "documents"}
    monkeypatch.setattr(r, "load_fixture", lambda: (manifest, [probe], digest))
    observed = r.worker(root, token, "integration-only")
    assert len(observed["warm_ms"]) == 5
    assert all(sample["status"] == "ok" for sample in observed["warm_samples"])
    assert observed["cold_ms"] >= 0 and observed["stable_rankings"]
    with pytest.raises(r.GatewayError) as error:
        r.gateway_for(root, state, "capacity-2049").search_documents("integration-only", 10)
    assert error.value.code == "PAYLOAD_TOO_LARGE"


def test_unsupported_filters_are_not_client_filtered():
    r = runner()
    assert r.unsupported_filters({"domain": "documents", "filters": {"source_ids": ["book-a"]}}) == ["source_ids"]
    assert r.unsupported_filters({"domain": "history", "filters": {"source_system": "codex"}}) == []


def test_worker_timeout_does_not_export_stderr_or_root(monkeypatch):
    r = runner()
    import subprocess
    seen = {}
    def timeout(argv, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(argv, 1, stderr="C:/private/secret")
    monkeypatch.setattr(subprocess, "run", timeout)
    result = r.invoke_worker(Path("C:/synthetic-private"), "token", "documents-01", 1)
    assert result == {"status": "error", "error": "WORKER_TIMEOUT", "ranking": [], "warm_ms": []}
    assert seen["shell"] is False and seen["timeout"] == 1
    assert "secret" not in json.dumps(result)


def test_cli_validation_roundtrip_has_no_private_paths():
    import subprocess
    script = Path(__file__).resolve().parents[1] / "scripts/evaluate_retrieval.py"
    assert script.exists(), "CLI missing"
    result = subprocess.run([sys.executable, str(script), "--validate"], shell=False,
                             capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["cases"] == 32 and output["fixture_revision"] == "p15-synthetic-1"
    assert "C:/" not in result.stdout and "C:\\" not in result.stdout


def test_real_qmd_owned_runtime_integration_only():
    import os
    node, cli = os.environ.get("P15_TEST_QMD_NODE"), os.environ.get("P15_TEST_QMD_CLI")
    if not node or not cli:
        pytest.skip("Explicit real QMD runtime not supplied; never a fake quality backend")
    r = runner()
    manifest, _, digest = r.load_fixture()
    root, token = r.create_root(digest)
    state = r.prepare(root, token, manifest, digest, node, cli)
    assert state["qmd_ready"], state.get("qmd_setup_error")
    runtime = r.qmd_runtime(root, state)
    _, environment = runtime.command_and_environment(root / "study")
    for name in ("INDEX_PATH", "QMD_CONFIG_DIR", "XDG_CACHE_HOME", "HOME", "USERPROFILE"):
        assert Path(environment[name]).is_relative_to(root)
    # This sentinel is not one of the gold cases and creates no quality score.
    result = r.gateway_for(root, state).search_study("p15_integration_absent_amber_whale", 10)
    assert result["results"] == []
    assert state["qmd_version"] == "2.8.3" and state["node_version"].startswith("v")


def test_report_manifest_roundtrip_and_frozen_unsupported_even_on_worker_failure(monkeypatch):
    import evaluate_retrieval as cli
    monkeypatch.setattr(cli, "prepare", lambda *args: {"qmd_ready": False})
    monkeypatch.setattr(cli, "invoke_worker", lambda *args: {"status": "error", "error": "WORKER_TIMEOUT", "ranking": [], "warm_ms": []})
    path = cli.run()
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["summary"]["coverage"]["quality_supported"] == len(report["quality_supported_case_ids"])
    assert report["qmd"]["qmd_ready"] is False
    assert report["code_files_sha256"]["scripts/retrieval_runner.py"]
    assert report["fixture_hash"] == runner().load_fixture()[2]
    assert str(path.parent) not in path.read_text(encoding="utf-8")


def test_bad_worker_output_is_recorded_as_error(monkeypatch):
    import subprocess
    r = runner()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "bad json", "private"))
    result = r.invoke_worker(Path("C:/synthetic-private"), "token", "documents-01")
    assert result["error"] == "WORKER_BAD_OUTPUT" and result["status"] == "error"


def test_wrong_answer_evidence_does_not_use_superseded_analysis():
    r = runner()
    class NativeShape:
        def search_wrong_answers(self, query, limit):
            return {"results": [{"source_id": "native-source", "source_uri": "native-asset", "question_text": "Invented Q"}]}
        def get_wrong_answer_bundle(self, source_id):
            return {"analyses": [{"version": 1, "reasoning": "superseded evidence"},
                                 {"version": 2, "reasoning": "latest evidence"}]}
    sample = r.sample_query(NativeShape(), {"domain": "wrong_answer", "query": "probe", "filters": {}},
                            {"aliases": {"wrong:probe": "native-source", "shared-image": "native-asset"}})
    assert "latest evidence" in sample["ranking"][0]["evidence"]
    assert "superseded evidence" not in sample["ranking"][0]["evidence"]
    assert len(sample["readbacks"][0]["analyses"]) == 2


def test_parent_keeps_actual_unscoped_diagnostic_status(monkeypatch):
    import evaluate_retrieval as cli
    monkeypatch.setattr(cli, "prepare", lambda *args: {"qmd_ready": False})
    monkeypatch.setattr(cli, "invoke_worker", lambda *args: {"status": "unsupported", "diagnostic_status": "ok", "ranking": [], "warm_ms": []})
    report = json.loads(cli.run().read_text(encoding="utf-8"))
    observed = next(case["observation"] for case in report["cases"] if case["case_id"] == "documents-01")
    assert observed["diagnostic_status"] == "ok"
