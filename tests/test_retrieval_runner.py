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
    assert r.unsupported_filters({"domain": "documents", "filters": {"source_ids": ["book-a"], "page_range": [64, 65]}}) == []
    assert r.unsupported_filters({"domain": "documents", "filters": {"chapter_ids": ["chapter-a"]}}) == ["chapter_ids"]
    assert r.unsupported_filters({"domain": "history", "filters": {"source_system": "codex"}}) == []


def test_document_native_scope_and_unscoped_controls_retain_frozen_gold():
    r = runner()
    from retrieval_metrics import evaluate_case
    manifest, cases, digest = r.load_fixture()
    assert digest == "07ebc25e54c2003c24913fa5dbe8603d1a8b85a3fb681c7e65be6d7b82a4e8ad"
    root, token = r.create_root(digest)
    r.prepare(root, token, manifest, digest)
    expected_excluded = {
        "documents-01": ["book-b:p65:c1"],
        "documents-02": ["book-a:p66:c1"],
        "documents-06": ["book-b:p65:c1", "book-lantern-festival:p1:c1"]}
    for case in cases:
        if case["case_id"] not in expected_excluded:
            continue
        observation = r.worker(root, token, case["case_id"])
        result = evaluate_case(case, observation)
        assert result["filter_case"] == 1 and result["scope_pair"]["pass"]
        assert result["scope_pair"]["retained_gold"] == len(case["relevant_ids"])
        assert observation["scope_evidence"]["excluded_ids"] == expected_excluded[case["case_id"]]
        assert observation["scope_evidence"]["retained_gold_ids"] == sorted(case["relevant_ids"])
        assert observation["stable_rankings"] and len(observation["warm_ms"]) == 5


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


def test_real_qmd_incremental_and_fresh_rebuild_equal_hits_and_worker_stability():
    """Acceptance of existing QMD behavior; no gold queries or production index."""
    import os
    import subprocess
    from dataclasses import replace
    from cognivault.adapters.study_qmd import QmdStudyBackend
    node, cli = os.environ.get("P15_TEST_QMD_NODE"), os.environ.get("P15_TEST_QMD_CLI")
    if not node or not cli:
        pytest.skip("Explicit real QMD runtime not supplied")
    r = runner()
    manifest, _, digest = r.load_fixture()
    root, token = r.create_root(digest)
    state = r.prepare(root, token, manifest, digest, node, cli)
    assert state["qmd_ready"], state.get("qmd_setup_error")
    runtime = r.qmd_runtime(root, state)
    query = "p15_consistency_amber_whale"
    first = root / "study" / "consistency-first.md"
    first.write_text(query + " invented initial evidence", encoding="utf-8")

    def update(target):
        argv, environment = target.command_and_environment(root / "study")
        completed = subprocess.run(argv + ["update"], env=environment, cwd=target.cwd,
            shell=False, capture_output=True, encoding="utf-8", timeout=120)
        assert completed.returncode == 0, "Owned synthetic index update failed"

    update(runtime)
    gateway = r.gateway_for(root, state)
    before = gateway.search_study(query, 10)
    assert len(before["results"]) == 1
    # Change existing authored text and add a second authored file; preserve both.
    first.write_text(query + " invented revised evidence", encoding="utf-8")
    (root / "study" / "consistency-second.md").write_text(query + " invented second evidence", encoding="utf-8")
    update(runtime)
    incremental = gateway.search_study(query, 10)
    assert len(incremental["results"]) == 2
    fresh_root = root / "qmd-rebuild"
    for name in ("work", "index", "config", "cache", "home", "userprofile"):
        (fresh_root / name).mkdir(parents=True)
    (fresh_root / "config" / "index.yml").write_text(
        (runtime.config_dir / "index.yml").read_text(encoding="utf-8"), encoding="utf-8")
    fresh = replace(runtime, runtime_root=fresh_root, cwd=fresh_root / "work",
        index_path=fresh_root / "index" / "index.sqlite", config_dir=fresh_root / "config",
        cache_dir=fresh_root / "cache", home_dir=fresh_root / "home", userprofile_dir=fresh_root / "userprofile")
    update(fresh)
    rebuilt_gateway = r.Gateway(gateway.config, QmdStudyBackend(gateway.config, runtime=fresh, timeout_seconds=60))
    rebuilt = rebuilt_gateway.search_study(query, 10)
    assert {row["source_path"] for row in incremental["results"]} == {row["source_path"] for row in rebuilt["results"]}
    # Real fresh worker uses the prebuilt incremental index and repeats five times.
    import json
    program = """import json,sys
from pathlib import Path
import retrieval_runner as r
root=Path(sys.argv[1]); state=json.loads((root/'state.json').read_text())
gateway=r.gateway_for(root,state)
print(json.dumps([gateway.search_study(sys.argv[2],10)['results'] for _ in range(6)]))
"""
    environment = dict(os.environ, PYTHONPATH=str(r.REPO / "scripts") + os.pathsep + str(r.REPO / "src"),
                       PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    child = subprocess.run([sys.executable, "-c", program, str(root), query], env=environment,
        shell=False, capture_output=True, encoding="utf-8", timeout=180)
    assert child.returncode == 0, "Owned synthetic fresh-worker search failed"
    samples = json.loads(child.stdout)
    rankings = [[row["source_path"] for row in sample] for sample in samples]
    assert len(rankings) == 6 and len(rankings[0]) == 2
    assert all(ranking == rankings[0] for ranking in rankings[1:])


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


def test_real_chinese_worker_roundtrip_overrides_inherited_output_encoding(monkeypatch):
    import subprocess
    r = runner()
    native_run = subprocess.run
    monkeypatch.setenv("PYTHONIOENCODING", "cp936")
    monkeypatch.setenv("PYTHONUTF8", "0")
    program = "import json; print(json.dumps({'status':'ok','ranking':[{'evidence':'\\u5206\\u6bcd \\u00b7 \\u793a\\u4f8b'}]},ensure_ascii=False))"
    def real_child(argv, **kwargs):
        # Exercise real Python stdout bytes using exactly the runner's child environment.
        return native_run([sys.executable, "-c", program], **kwargs)
    monkeypatch.setattr(subprocess, "run", real_child)
    result = r.invoke_worker(Path("C:/synthetic-private"), "token", "integration-only")
    assert result["status"] == "ok"
    assert result["ranking"][0]["evidence"] == "分母 · 示例"


@pytest.mark.parametrize("stdout", [None, "null", "[]"])
def test_none_or_nonobject_worker_output_is_conservative_error(monkeypatch, stdout):
    import subprocess
    r = runner()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout, "private"))
    result = r.invoke_worker(Path("C:/synthetic-private"), "token", "integration-only")
    assert result == {"status": "error", "error": "WORKER_BAD_OUTPUT", "ranking": [], "warm_ms": []}


def test_readback_failure_preserves_all_search_hits_and_query_only_timing(monkeypatch):
    r = runner()
    rows = [{"chunk_uri": "native-one", "document_uri": "native-book", "page_number": 65,
             "section": "Invented", "snippet": "Invented one", "source_asset_uri": "native-asset"},
            {"chunk_uri": "native-two", "document_uri": "native-book", "page_number": 66,
             "section": "Invented", "snippet": "Invented two", "source_asset_uri": "native-asset"}]
    raw = {"results": rows}
    class NativeShape:
        def search_documents(self, query, limit):
            return raw
        def fetch_document(self, uri):
            return {"document": {"document_uri": uri}}
        def fetch_document_page(self, uri, page):
            raise r.GatewayError("INTERNAL_ERROR", "private diagnostic excluded")
    clock_values = iter([0, .01, 1.01])
    monkeypatch.setattr(r.time, "perf_counter", lambda: next(clock_values))
    observed = r.sample_query(NativeShape(), {"domain": "documents", "query": "probe",
        "query_type": ["original_asset_source"], "filters": {}}, {"aliases": {
        "book-a": "native-book", "book-a:p65:c1": "native-one", "book-a:p66:c1": "native-two"}})
    assert observed["status"] == "error" and observed["readback_error"] == "INTERNAL_ERROR"
    assert observed["query_status"] == "ok" and observed["native_result"] == raw
    assert [hit["id"] for hit in observed["ranking"]] == ["book-a:p65:c1", "book-a:p66:c1"]
    assert observed["elapsed_ms"] == 10 and observed["evidence_readback_ms"] == 1000
    assert observed["readbacks"] == [{"document": {"document_uri": "native-book"}}]
    # The retained diagnostic ranking never earns retrieval quality credit on error.
    import retrieval_metrics
    from test_retrieval_evaluation import gold
    score = retrieval_metrics.evaluate_case(gold(), observed)
    assert score["recall@10"] == score["mrr@10"] == 0 and not score["empty_prediction"]
