"""P15 CLI: --validate is read-only; --run creates a fresh synthetic temp root."""
import argparse
import hashlib
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys

from retrieval_metrics import evaluate_case, summarize
from retrieval_runner import REPO, load_fixture, create_root, prepare, invoke_worker, worker, unsupported_filters


def run(qmd_node=None, qmd_cli=None):
    manifest, cases, digest = load_fixture()
    root, token = create_root(digest)
    state = prepare(root, token, manifest, digest, qmd_node, qmd_cli)
    supported = [case["case_id"] for case in cases if not unsupported_filters(case)
                 and case.get("measurement") != "contract"
                 and (case["domain"] != "study" or state["qmd_ready"])]
    observations = [invoke_worker(root, token, case["case_id"]) for case in cases]
    for case, observation in zip(cases, observations):
        unsupported = unsupported_filters(case)
        if case["domain"] == "study" and not state["qmd_ready"]:
            unsupported.append("real_qmd_runtime")
        if unsupported:
            observation.update(diagnostic_status=observation["status"], status="unsupported", unsupported=unsupported)
    results = [evaluate_case(case, observed) for case, observed in zip(cases, observations)]
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, shell=False, capture_output=True,
                            text=True, timeout=10).stdout.strip()
    versions = {package: importlib.metadata.version(package) for package in ("mcp", "pypdf")}
    report = {"evidence": "SYNTHETIC ONLY", "production_host": "UNKNOWN",
        "generated_at": datetime.now(timezone.utc).isoformat(), "code_commit": commit,
        "fixture_revision": manifest["fixture_revision"], "fixture_hash": digest,
        "code_files_sha256": {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
                              for name in ("scripts/evaluate_retrieval.py", "scripts/retrieval_runner.py", "scripts/retrieval_metrics.py")},
        "fixture_validation": state.get("fixture_validation"),
        "python": platform.python_version(), "dependencies": versions,
        "qmd": {key: state.get(key) for key in ("qmd_ready", "qmd_version", "qmd_cli_sha256", "node_version", "qmd_setup_error")},
        "profiles": manifest["profiles"], "backends": {"study": "qmd_bm25", "documents": "native scan", "wrong_answer": "native scan", "history": "native canonical"},
        "limit": manifest["limit"], "seed": manifest["seed"], "order": manifest["order"],
        "quality_supported_case_ids": supported, "cases": results, "summary": summarize(results),
        "legacy_history": {"path": "legacy", "status": "not_measured", "reason": "Separate legacy item unit; 32-case dataset targets canonical/source-only paths"}}
    # Roundtrip before publishing the machine report, always in the author-owned root.
    encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if json.loads(encoded) != report:
        raise ValueError("Report roundtrip differs")
    destination = root / "result.json"
    destination.write_text(encoded, encoding="utf-8")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--validate", action="store_true")
    action.add_argument("--run", action="store_true")
    action.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--qmd-node", type=Path)
    parser.add_argument("--qmd-cli", type=Path)
    parser.add_argument("--token", help=argparse.SUPPRESS)
    parser.add_argument("--case-id", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if bool(arguments.qmd_node) != bool(arguments.qmd_cli):
        parser.error("Supply both explicit QMD Node and JS CLI paths")
    if arguments.validate:
        manifest, cases, digest = load_fixture()
        print(json.dumps({"fixture_revision": manifest["fixture_revision"], "fixture_hash": digest,
                          "cases": len(cases), "profiles": manifest["profiles"]}))
    elif arguments.worker:
        print(json.dumps(worker(arguments.worker, arguments.token, arguments.case_id), ensure_ascii=False))
    else:
        print(run(arguments.qmd_node, arguments.qmd_cli))


if __name__ == "__main__":
    main()
