"""Real QMD 2.8.3 disposable-runtime smoke with synthetic data only."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from cognivault.adapters.study_qmd import QmdRuntime, QmdStudyBackend
from cognivault.config import AppConfig


def _file_manifest(root: Path) -> dict[str, dict[str, int | str]]:
    manifest: dict[str, dict[str, int | str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        stat = path.stat()
        manifest[relative] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "mtime_ns": stat.st_mtime_ns,
        }
    return manifest


def _manifest_delta(before: dict, after: dict) -> dict[str, list[str]]:
    created = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(
        path for path in set(before) & set(after)
        if before[path] != after[path]
    )
    return {"created": created, "modified": modified, "removed": removed}


def _run_qmd(argv: list[str], environment: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        shell=False,
        cwd=str(cwd),
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def test_real_qmd_update_and_search_are_contained_to_disposable_runtime(tmp_path: Path, monkeypatch) -> None:
    node_value = os.environ.get("QMD_SMOKE_NODE_EXE")
    cli_value = os.environ.get("QMD_SMOKE_CLI_ENTRYPOINT")
    if not node_value or not cli_value:
        pytest.skip("set QMD_SMOKE_NODE_EXE and QMD_SMOKE_CLI_ENTRYPOINT for the real QMD smoke")

    disposable = tmp_path / "disposable-root"
    qmd_root = disposable / "qmd-runtime"
    synthetic_study = disposable / "synthetic-study"
    sentinel = tmp_path / "runtime-external-sentinel"
    for directory in (
        qmd_root / "config",
        qmd_root / "cache",
        qmd_root / "home",
        qmd_root / "userprofile",
        qmd_root / "index",
        qmd_root / "work",
        synthetic_study,
        sentinel / "nested",
    ):
        directory.mkdir(parents=True)

    sentinel_files = {
        sentinel / "sentinel.txt": "synthetic sentinel; must not change\n",
        sentinel / "nested" / "sentinel.json": '{"sentinel":"unchanged"}\n',
    }
    for path, content in sentinel_files.items():
        path.write_text(content, encoding="utf-8")

    source = synthetic_study / "invented-algebra.md"
    source.write_text(
        "# Invented Algebra\n\nSynthetic-only theorem for all blue triangles.\n",
        encoding="utf-8",
    )
    config = qmd_root / "config" / "index.yml"
    config.write_text(
        "collections:\n"
        "  studyvault:\n"
        f"    path: {synthetic_study.as_posix()}\n"
        "    pattern: '**/*.md'\n",
        encoding="utf-8",
    )

    node = Path(node_value)
    cli = Path(cli_value)
    package = cli.parents[2] / "package.json"
    runtime = QmdRuntime(
        runtime_root=qmd_root,
        node_executable=node,
        cli_entrypoint=cli,
        package_json=package,
        cwd=qmd_root / "work",
        index_path=qmd_root / "index" / "index.sqlite",
        config_dir=qmd_root / "config",
        cache_dir=qmd_root / "cache",
        home_dir=qmd_root / "home",
        userprofile_dir=qmd_root / "userprofile",
    )
    executable_argv, environment = runtime.command_and_environment(synthetic_study)
    assert all(Path(environment[key]).is_relative_to(disposable) for key in (
        "INDEX_PATH", "QMD_CONFIG_DIR", "XDG_CACHE_HOME", "HOME", "USERPROFILE",
    ))
    assert Path(runtime.cwd).is_relative_to(disposable)
    assert executable_argv == [str(node.resolve()), str(cli.resolve())]
    assert Path(executable_argv[0]).name.casefold() == "node.exe"
    assert Path(executable_argv[1]).suffix.casefold() == ".js"
    assert all(not value.casefold().endswith((".cmd", ".ps1", ".bat")) for value in executable_argv)

    sentinel_before = _file_manifest(sentinel)
    runtime_before = _file_manifest(qmd_root)

    update = _run_qmd(executable_argv + ["update"], environment, runtime.cwd)
    assert update.returncode == 0, json.dumps({
        "returncode": update.returncode,
        "stdout": update.stdout,
        "stderr": update.stderr,
    }, ensure_ascii=False)
    assert "Indexed: 1 new" in update.stdout

    calls: list[tuple[list[str], dict]] = []
    completed_searches: list[subprocess.CompletedProcess[str]] = []

    def runner(argv: list[str], **kwargs):
        calls.append((argv, kwargs))
        completed = subprocess.run(argv, **kwargs)
        completed_searches.append(completed)
        return completed

    backend = QmdStudyBackend(
        AppConfig(gateway_version="0.1.0", study_root=synthetic_study),
        runtime=runtime,
        runner=runner,
        timeout_seconds=60,
    )
    result = backend.search("--all", 5)
    assert result == []
    assert len(calls) == 1
    assert completed_searches[0].returncode == 0
    assert json.loads(completed_searches[0].stdout) == []
    search_argv, search_options = calls[0]
    assert search_argv[-2:] == ["--", "--all"]
    assert search_options["shell"] is False
    assert search_options["cwd"] == str(runtime.cwd)
    assert search_options["env"] == environment

    runtime_after = _file_manifest(qmd_root)
    sentinel_after = _file_manifest(sentinel)
    delta = _manifest_delta(runtime_before, runtime_after)
    print(json.dumps({"runtime_delta": delta}, ensure_ascii=False, sort_keys=True))
    assert delta["created"] or delta["modified"]
    assert delta["removed"] == []
    assert sentinel_after == sentinel_before
    monkeypatch.delenv("QMD_SMOKE_NODE_EXE", raising=False)
    monkeypatch.delenv("QMD_SMOKE_CLI_ENTRYPOINT", raising=False)
