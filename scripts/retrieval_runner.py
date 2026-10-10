"""Owned synthetic P15 runtime; existing Gateway contracts, no backend changes."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from retrieval_metrics import validate_gold
from cognivault.config import AppConfig
from cognivault.contracts import GatewayError
from cognivault.gateway import Gateway
from cognivault.adapters.documents import SQLiteDocumentStore, _chunks
from cognivault.adapters.history import SQLiteHistoryBackend
from cognivault.adapters.study_qmd import QmdRuntime, QmdStudyBackend
from cognivault.normalization.contracts import identity

DOMAINS = ("study", "documents", "wrong_answer", "history")


def fixture_hash(manifest, cases):
    raw = json.dumps({"manifest": manifest, "cases": cases}, ensure_ascii=False,
                     sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def load_fixture():
    directory = REPO / "tests" / "fixtures" / "retrieval"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    cases = []
    for domain in DOMAINS:
        rows = json.loads((directory / domain / "cases.json").read_text(encoding="utf-8"))
        if len(rows) != 8 or len(manifest["candidates"][domain]) < 10:
            raise ValueError("P15 requires eight cases and at least ten candidates per domain")
        for case in rows:
            validate_gold(case)
            if case["domain"] != domain or case["fixture_revision"] != manifest["fixture_revision"]:
                raise ValueError("Fixture domain/revision differs")
        cases.extend(rows)
    if manifest["order"] != [case["case_id"] for case in cases] or len(set(manifest["order"])) != 32:
        raise ValueError("Case order differs")
    return manifest, cases, fixture_hash(manifest, cases)


def create_root(digest):
    root = Path(tempfile.mkdtemp(prefix="p15-"))
    token = secrets.token_hex(24)
    (root / "owner.json").write_text(json.dumps({"token": token, "fixture_hash": digest}), encoding="utf-8")
    return root, token


def check_root(root, token):
    root = Path(root)
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise ValueError("Owned runtime cannot be a link")
    resolved = root.resolve()
    if resolved.parent != Path(tempfile.gettempdir()).resolve() or not resolved.name.startswith("p15-"):
        raise ValueError("Only a fresh author-owned P15 temporary root is allowed")
    marker = resolved / "owner.json"
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("Missing ownership marker")
    owner = json.loads(marker.read_text(encoding="utf-8"))
    if not secrets.compare_digest(owner["token"], token):
        raise ValueError("Ownership token differs")
    return resolved


def _encoded(content):
    return base64.b64encode(content.encode()).decode()


def qmd_runtime(root, state):
    q = root / "qmd"
    cli = Path(state["qmd_cli"])
    return QmdRuntime(q, Path(state["qmd_node"]), cli, cli.parents[2] / "package.json",
                      q / "work", q / "index" / "index.sqlite", q / "config",
                      q / "cache", q / "home", q / "userprofile")


def gateway_for(root, state, profile="default"):
    base = root / profile
    config = AppConfig("p15-synthetic-1", root / "study", history_database=base / "history.db",
                       history_migration_inbox=base / "inbox")
    backend = QmdStudyBackend(config, runtime=qmd_runtime(root, state), timeout_seconds=60) if state.get("qmd_ready") else None
    return Gateway(config, backend, SQLiteHistoryBackend(base / "history.db"),
                   document_store=SQLiteDocumentStore(base / "assets", base / "documents.db"),
                   capabilities=frozenset({"read", "write", "ingest"}), qmd_discoverable=lambda: bool(backend))


def prepare(root, token, manifest, digest, qmd_node=None, qmd_cli=None):
    root = check_root(root, token)
    if any((root / name).exists() for name in ("study", "default", "state.json")):
        raise ValueError("Runtime is already initialized")
    (root / "study").mkdir()
    state = {"fixture_hash": digest, "fixture_revision": manifest["fixture_revision"],
             "aliases": {}, "profiles": manifest["profiles"], "qmd_ready": False}
    aliases = state["aliases"]
    for item in manifest["candidates"]["study"]:
        relative = item["id"].removeprefix("study:")
        if Path(relative).name != relative:
            raise ValueError("Study candidates must use authored basenames")
        (root / "study" / relative).write_text(item["text"], encoding="utf-8")
        aliases[item["id"]] = item["id"]
    for profile in manifest["profiles"]:
        base = root / profile
        (base / "assets").mkdir(parents=True)
        (base / "inbox").mkdir()
        SQLiteHistoryBackend(base / "history.db").initialize()
    gateway = gateway_for(root, state)
    for item in manifest["candidates"]["documents"]:
        pages = [item["page_texts"].get(str(page), f"Invented filler {item['id']} page {page}")
                 for page in range(1, item["pages"] + 1)]
        record = gateway.ingest_documents([{"title": item["title"], "media_type": "text/plain",
                    "content_base64": _encoded("\f".join(pages))}])["documents"][0]
        aliases[item["id"]] = record["document_uri"]
        for number, text in enumerate(pages, 1):
            # Deterministic native chunk identity derived from ingestion content, never a query.
            for index, chunk in enumerate(_chunks(record["document_uri"], number, text), 1):
                aliases[f"{item['id']}:p{number}:c{index}"] = chunk.uri
    # One native image shared by two different exercises (not a real personal asset).
    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    asset = gateway.register_asset("image/png", png)["asset"]["asset_uri"]
    aliases["shared-image"] = asset
    for item in manifest["candidates"]["wrong_answer"]:
        source = gateway.register_wrong_answer_source(asset, item["question"], item["answer"])["source"]
        aliases[item["id"]] = source["source_id"]
        gateway.save_wrong_answer_analysis(source["source_id"], item["analysis"], [asset], [], "p15-" + item["id"].replace(":", "-"), 0)
        if item["latest_analysis"]:
            gateway.update_wrong_answer_analysis(source["source_id"], item["latest_analysis"], [asset], [], "p15-latest-" + item["id"].replace(":", "-"), 1)
    entries = []
    inbox = root / "default" / "inbox"
    for index, item in enumerate(manifest["candidates"]["history"]):
        if item.get("source_only"):
            raw = json.dumps({"session_metadata": item["text"]}).encode()
            fmt, kind = "json", "session_metadata"
        elif item["source_system"] == "hermes":
            raw = json.dumps({"id": item["native_key"], "messages": [{"role": "user", "content": item["text"], "timestamp": 1767312000}]}).encode()
            fmt, kind = "json", "raw_session"
        else:
            rows = [{"type": "session_meta", "payload": {"id": item["native_key"]}},
                    {"type": "response_item", "timestamp": item["occurred_at"],
                     "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": item["text"]}]}}]
            raw = ("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n").encode()
            fmt, kind = "jsonl", "raw_session"
        name = f"source-{index}.{fmt}"
        (inbox / name).write_bytes(raw)
        entries.append({"relative_path": name, "source_system": item["source_system"], "source_format": fmt,
                        "record_kind": kind, "sha256": hashlib.sha256(raw).hexdigest(), "byte_count": len(raw)})
    raw_manifest = json.dumps({"schema_version": 1, "entries": entries}).encode()
    (inbox / "manifest.json").write_bytes(raw_manifest)
    imported = gateway.ingest_history_sources("manifest.json", hashlib.sha256(raw_manifest).hexdigest())
    if imported["errors"] or imported["has_more"]:
        raise GatewayError("FIXTURE_INGEST_FAILED", "Synthetic fixture ingestion is incomplete")
    for item, result in zip(manifest["candidates"]["history"], imported["results"]):
        aliases[item["source_id"]] = result["source_id"]
        aliases[item["id"]] = result["source_id"] if item.get("source_only") else identity("conversation", item["source_system"], item["native_key"])
    normalized = gateway.normalize_history_sources(gateway.history_normalization_snapshot()["source_set_sha256"], limit=64)
    if normalized["errors"] or normalized["has_more"]:
        raise ValueError("Synthetic canonical preparation is incomplete")
    # Independent identity/readback validation, never query-to-gold binding.
    validated = {"canonical": 0, "source_only": 0}
    for item in manifest["candidates"]["history"]:
        sid = aliases[item["source_id"]]
        if not gateway.verify_history_source(sid)["verified"]:
            raise ValueError("Fixture source verification failed")
        if item.get("source_only"):
            metadata = gateway.fetch_history_source(sid)["source"]
            if metadata["normalization_state"] == "normalized":
                raise ValueError("Metadata fixture unexpectedly became canonical")
            validated["source_only"] += 1
            continue
        conversation = gateway.fetch_canonical_conversation(aliases[item["id"]])
        excerpts = []
        for view in conversation["views"]:
            if view["source_id"] != sid:
                raise ValueError("Fixture canonical view source differs")
            for message in view["messages"]:
                readback = gateway.fetch_canonical_message(message["message_id"])
                payload = json.loads(base64.b64decode(readback["content_base64"]))
                excerpts.extend(part["text"] for part in payload["parts"] if "text" in part)
        if excerpts != [item["text"]]:
            raise ValueError("Fixture canonical text differs from independently authored content")
        validated["canonical"] += 1
    state["fixture_validation"] = validated
    # Separate native >2048 candidate contract profile; does not contaminate default quality.
    capacity = gateway_for(root, state, "capacity-2049")
    for batch, count in enumerate((999, 999, 51)):
        text = "\f".join(f"Invented capacity batch {batch} page {page}" for page in range(1, count + 1))
        capacity.ingest_documents([{"title": f"Invented capacity batch {batch}", "media_type": "text/plain", "content_base64": _encoded(text)}])
    if qmd_node and qmd_cli:
        state.update(qmd_node=str(Path(qmd_node).resolve()), qmd_cli=str(Path(qmd_cli).resolve()))
        for name in ("work", "index", "config", "cache", "home", "userprofile"):
            (root / "qmd" / name).mkdir(parents=True)
        (root / "qmd" / "config" / "index.yml").write_text(
            "collections:\n  studyvault:\n    path: " + (root / "study").as_posix() + "\n    pattern: '**/*.md'\n", encoding="utf-8")
        try:
            runtime = qmd_runtime(root, state)
            argv, environment = runtime.command_and_environment(root / "study")
            result = subprocess.run(argv + ["update"], env=environment, cwd=str(runtime.cwd),
                                    shell=False, capture_output=True, text=True, encoding="utf-8", timeout=120)
            if result.returncode:
                state["qmd_setup_error"] = "QMD_INDEX_UPDATE_FAILED"
            else:
                state["qmd_ready"] = True
                state["qmd_version"] = "2.8.3"
                state["qmd_cli_sha256"] = hashlib.sha256(Path(qmd_cli).read_bytes()).hexdigest()
                state["node_version"] = subprocess.run([str(qmd_node), "--version"], shell=False, capture_output=True,
                    text=True, timeout=10, env=environment).stdout.strip()
        except (GatewayError, OSError, subprocess.TimeoutExpired) as error:
            state["qmd_setup_error"] = error.code if isinstance(error, GatewayError) else "QMD_SETUP_FAILED"
    (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return state


def unsupported_filters(case):
    supported = {"source_system"} if case["domain"] == "history" else (
        {"source_ids", "page_range"} if case["domain"] == "documents" else set())
    return sorted(key for key, value in case.get("filters", {}).items() if value is not None and key not in supported)


def sample_query(gateway, case, state):
    start = time.perf_counter()
    raw, query_ms = None, None
    ranking, readbacks = [], []
    try:
        domain = case["domain"]
        if domain == "history":
            method = gateway.search_history_sources if case.get("path") == "source_only" else gateway.search_canonical_conversations
            raw = method(query=case["query"], source_system=case.get("filters", {}).get("source_system"), limit=10)
        elif domain == "documents":
            filters = case.get("filters", {})
            sources = filters.get("source_ids")
            # Alias binding comes exclusively from ingestion, never query results.
            native_scope = {}
            if sources is not None:
                native_scope["source_ids"] = [state["aliases"][alias] for alias in sources]
            if filters.get("page_range") is not None:
                native_scope["page_range"] = filters["page_range"]
            raw = gateway.search_documents(case["query"], 10, **native_scope)
        else:
            raw = getattr(gateway, "search_" + {"study": "study", "documents": "documents", "wrong_answer": "wrong_answers"}[domain])(case["query"], 10)
        query_ms = (time.perf_counter() - start) * 1000
        rows = raw.get("results", raw.get("sources", raw.get("conversations", [])))
        reverse = {native: alias for alias, native in state["aliases"].items()}
        # Bind every native search hit before any evidence readback can fail.
        for row in rows:
            evidence = row.get("snippet", "")
            if domain == "study":
                native = "study:" + row["source_path"]
                locator = {"source_id": reverse.get(native, native), "page_number": None, "chapter_id": None}
            elif domain == "documents":
                native = row["chunk_uri"]
                locator = {"source_id": reverse.get(row["document_uri"], row["document_uri"]), "page_number": row["page_number"], "chapter_id": None, "section": row["section"]}
            elif domain == "wrong_answer":
                native = row["source_id"]
                locator = {"source_id": reverse.get(row["source_uri"], row["source_uri"]), "page_number": row.get("page_number"), "chapter_id": None}
                evidence = json.dumps(row, ensure_ascii=False)
            elif case.get("path") == "source_only":
                native = row["source_id"]
                locator = {"source_id": reverse.get(native, native), "page_number": None, "chapter_id": None, "source_system": row["source_system"]}
            else:
                native = row["conversation_id"]
                # Canonical search exposes no source locator; readback supplies it.
                locator = {"source_id": None,
                           "page_number": None, "chapter_id": None, "source_system": row["source_system"]}
            alias = reverse.get(native, native)
            if domain == "history" and case.get("path") == "source_only":
                alias = next((a for a, value in state["aliases"].items() if a.startswith("source-only:") and value == native), alias)
                locator["source_id"] = next((a for a, value in state["aliases"].items() if a.startswith("history-source:") and value == native), locator["source_id"])
            ranking.append({"id": alias, "native_id": native, "locator": locator, "evidence": evidence})
        for row, hit in zip(rows, ranking):
            native = hit["native_id"]
            if domain == "documents" and "original_asset_source" in case.get("query_type", []):
                readbacks.append(gateway.fetch_document(row["document_uri"]))
                readbacks.append(gateway.fetch_document_page(row["document_uri"], row["page_number"]))
                readbacks.append(gateway.fetch_asset(row["source_asset_uri"], length=1024))
            elif domain == "wrong_answer":
                bundle = gateway.get_wrong_answer_bundle(native)
                readbacks.append(bundle)
                latest = max(bundle["analyses"], key=lambda analysis: analysis["version"], default=None)
                hit["evidence"] += "\n" + json.dumps(latest, ensure_ascii=False)
            elif domain == "history" and case.get("path") == "source_only":
                readback = gateway.fetch_history_source(native)
                readbacks.append(readback)
                hit["evidence"] = base64.b64decode(readback["content_base64"]).decode()
            elif domain == "history":
                fetched = gateway.fetch_canonical_conversation(native)
                readbacks.append(fetched)
                source_ids = {view["source_id"] for view in fetched["views"]}
                hit["locator"]["source_id"] = reverse.get(next(iter(source_ids)), next(iter(source_ids))) if len(source_ids) == 1 else None
                excerpts = []
                for view in fetched["views"]:
                    for message in view["messages"]:
                        message_readback = gateway.fetch_canonical_message(message["message_id"])
                        readbacks.append(message_readback)
                        payload = json.loads(base64.b64decode(message_readback["content_base64"]))
                        excerpts.extend(part["text"] for part in payload["parts"] if "text" in part)
                hit["evidence"] = "\n".join(excerpts)
        return {"status": "ok", "ranking": ranking, "native_result": raw, "readbacks": readbacks,
                "elapsed_ms": query_ms, "evidence_readback_ms": (time.perf_counter() - start) * 1000 - query_ms}
    except GatewayError as error:
        if query_ms is not None:
            return {"status": "error", "error": error.code, "readback_error": error.code,
                    "query_status": "ok", "ranking": ranking, "native_result": raw, "readbacks": readbacks,
                    "elapsed_ms": query_ms, "evidence_readback_ms": (time.perf_counter() - start) * 1000 - query_ms}
        return {"status": "error", "error": error.code, "ranking": [], "elapsed_ms": (time.perf_counter() - start) * 1000}


def worker(root, token, case_id):
    root = check_root(root, token)
    manifest, cases, digest = load_fixture()
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    if digest != state["fixture_hash"]:
        raise ValueError("Frozen fixture changed after preparation")
    case = next(case for case in cases if case["case_id"] == case_id)
    unsupported = unsupported_filters(case)
    if case["domain"] == "study" and not state["qmd_ready"]:
        unsupported.append("real_qmd_runtime")
        return {"status": "unsupported", "unsupported": unsupported, "ranking": [], "warm_ms": [],
                "runtime_error": state.get("qmd_setup_error", "QMD_RUNTIME_NOT_SUPPLIED")}
    gateway = gateway_for(root, state, case.get("profile", "default"))
    cold = sample_query(gateway, case, state)  # first Gateway query in fresh process
    warm = [sample_query(gateway, case, state) for _ in range(manifest["warm_samples"])]
    result = {**cold, "cold_ms": cold["elapsed_ms"], "warm_ms": [sample["elapsed_ms"] for sample in warm],
              "warm_samples": warm, "cold_definition": "fresh-process first Gateway query; prebuilt index, no OS cache flush"}
    if unsupported:
        result.update(status="unsupported", unsupported=unsupported, diagnostic_status=cold["status"])
    elif any(sample["status"] != "ok" for sample in warm) and case.get("measurement") != "contract":
        result.update(status="error", error="WARM_QUERY_ERROR")
    result["stable_rankings"] = all([hit["id"] for hit in sample["ranking"]] == [hit["id"] for hit in cold["ranking"]] for sample in warm)
    if not unsupported and (case.get("filters", {}).get("source_system")
            or case["domain"] == "documents" and any(value is not None for value in case.get("filters", {}).values())):
        control_case = {**case, "filters": {}}
        result["scope_control"] = sample_query(gateway, control_case, state)
        if case["domain"] == "documents":
            from retrieval_metrics import _in_scope
            outside = sorted(hit["id"] for hit in result["scope_control"]["ranking"]
                             if not _in_scope(hit, case["filters"]))
            returned = {hit["id"] for hit in cold["ranking"]}
            result["scope_evidence"] = {"excluded_ids": [alias for alias in outside if alias not in returned],
                "retained_gold_ids": sorted(returned & set(case["relevant_ids"]))}
    return result


def invoke_worker(root, token, case_id, timeout=420):
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("CODEX_HOST_E2E", "QMD_REAL_SMOKE_", "QMD_SMOKE_"))}
    environment["PYTHONPATH"] = str(REPO / "src")
    # Parent decoding does not control the Windows child's pipe encoding.
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    try:
        completed = subprocess.run([sys.executable, str(REPO / "scripts" / "evaluate_retrieval.py"),
            "--worker", str(root), "--token", token, "--case-id", case_id], shell=False,
            capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=environment)
        if completed.returncode:
            return {"status": "error", "error": "WORKER_FAILED", "ranking": [], "warm_ms": []}
        if not isinstance(completed.stdout, str):
            raise ValueError("Worker output is missing")
        result = json.loads(completed.stdout)
        if not isinstance(result, dict):
            raise ValueError("Worker output is not an observation object")
        return result
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "WORKER_TIMEOUT", "ranking": [], "warm_ms": []}
    except (ValueError, UnicodeError):
        return {"status": "error", "error": "WORKER_BAD_OUTPUT", "ranking": [], "warm_ms": []}
    except OSError:
        return {"status": "error", "error": "WORKER_START_FAILED", "ranking": [], "warm_ms": []}
