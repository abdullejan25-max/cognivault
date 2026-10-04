"""Synthetic QMD output tests; no installed QMD or personal data is used."""

import json
import os
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import subprocess

import pytest

from cognivault.adapters.study_qmd import QmdRuntime, QmdStudyBackend
from cognivault.config import AppConfig
from cognivault.contracts import GatewayError
from cognivault.gateway import Gateway


class FakeRunner:
    def __init__(self, stdout: str = "[]", error: Exception | None = None, returncode: int = 0):
        self.stdout = stdout
        self.error = error
        self.returncode = returncode
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv: list[str], **kwargs):
        self.calls.append((argv, kwargs))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "private stderr")


_SYNTHETIC_QMD_EXECUTABLE = str(Path(__file__).resolve())


def _study(tmp_path: Path) -> tuple[AppConfig, Path]:
    root = tmp_path / "study"
    source = root / "数学" / "synthetic.md"
    source.parent.mkdir(parents=True)
    source.write_text("Invented algebra lesson", encoding="utf-8")
    return AppConfig(gateway_version="0.1.0", study_root=root), source


def _backend(config: AppConfig, runner: FakeRunner, **kwargs) -> QmdStudyBackend:
    return QmdStudyBackend(
        config,
        qmd_executable="approved-qmd",
        approved_qmd_version="2.8.3",
        runner=runner,
        resolver=lambda name: _SYNTHETIC_QMD_EXECUTABLE if name == "approved-qmd" else None,
        timeout_seconds=9.0,
        **kwargs,
    )


def _hit(uri: str = "qmd://studyvault/数学/synthetic.md", **fields) -> dict:
    return {"file": uri, "title": "Synthetic chapter", "snippet": "Imaginary text", "score": 0.75, "docid": "synthetic-1", **fields}


def _runtime(tmp_path: Path) -> QmdRuntime:
    root = tmp_path / "runtime"
    package = root / "qmd-package"
    (package / "dist" / "cli").mkdir(parents=True)
    (root / "bin").mkdir()
    for directory in (root / "config", root / "cache", root / "home", root / "userprofile", root / "work"):
        directory.mkdir()
    node = root / "bin" / "node.exe"
    node.write_bytes(b"synthetic node")
    cli = package / "dist" / "cli" / "qmd.js"
    cli.write_text("// synthetic qmd cli", encoding="utf-8")
    (package / "package.json").write_text(
        '{"name":"@tobilu/qmd","version":"2.8.3"}', encoding="utf-8"
    )
    index = root / "index" / "index.sqlite"
    index.parent.mkdir()
    index.write_bytes(b"synthetic index")
    return QmdRuntime(
        runtime_root=root,
        node_executable=node,
        cli_entrypoint=cli,
        package_json=package / "package.json",
        cwd=root / "work",
        index_path=index,
        config_dir=root / "config",
        cache_dir=root / "cache",
        home_dir=root / "home",
        userprofile_dir=root / "userprofile",
    )


def test_runtime_builds_fixed_native_node_command_and_isolated_environment(tmp_path: Path, monkeypatch) -> None:
    config, _ = _study(tmp_path)
    runtime = _runtime(tmp_path)
    runner = FakeRunner()
    monkeypatch.setenv("PRIVATE_API_TOKEN", "synthetic-secret")
    monkeypatch.setenv("QMD_CONFIG_DIR", "C:/protected/config")
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")

    QmdStudyBackend(config, runtime=runtime, runner=runner).search("--help", 5)

    argv, options = runner.calls[0]
    assert argv == [
        str(runtime.node_executable), str(runtime.cli_entrypoint), "search", "--format", "json",
        "--collection", "studyvault", "-n", "5", "--", "--help",
    ]
    assert options["shell"] is False
    assert options["cwd"] == str(runtime.cwd)
    assert options["env"] == {
        "PATH": str(runtime.node_executable.parent),
        "SYSTEMROOT": r"C:\Windows",
        "INDEX_PATH": str(runtime.index_path),
        "QMD_CONFIG_DIR": str(runtime.config_dir),
        "XDG_CACHE_HOME": str(runtime.cache_dir),
        "HOME": str(runtime.home_dir),
        "USERPROFILE": str(runtime.userprofile_dir),
    }


@pytest.mark.parametrize("field", ["cwd", "index_path", "config_dir", "cache_dir", "home_dir", "userprofile_dir"])
def test_runtime_rejects_path_escape(tmp_path: Path, field: str) -> None:
    config, _ = _study(tmp_path)
    runtime = _runtime(tmp_path)
    runtime = replace(runtime, **{field: tmp_path / "outside"})
    runner = FakeRunner()

    with pytest.raises(GatewayError) as exc:
        QmdStudyBackend(config, runtime=runtime, runner=runner).search("valid", 5)

    assert exc.value.code == "QMD_RUNTIME_UNSAFE"
    assert runner.calls == []


@pytest.mark.parametrize("field", ["node_executable", "cli_entrypoint"])
def test_runtime_rejects_script_or_unreviewed_launcher(tmp_path: Path, field: str) -> None:
    config, _ = _study(tmp_path)
    runtime = _runtime(tmp_path)
    unsafe = runtime.runtime_root / ("qmd.cmd" if field == "node_executable" else "qmd.ps1")
    unsafe.write_text("unsafe launcher", encoding="utf-8")
    runtime = replace(runtime, **{field: unsafe})

    with pytest.raises(GatewayError) as exc:
        QmdStudyBackend(config, runtime=runtime, runner=FakeRunner()).search("valid", 5)

    assert exc.value.code == "QMD_RUNTIME_UNSAFE"


def test_search_uses_one_fixed_argument_array_and_controlled_execution(tmp_path: Path, monkeypatch) -> None:
    config, _ = _study(tmp_path)
    runner = FakeRunner(json.dumps([_hit()], ensure_ascii=False))
    monkeypatch.setenv("PRIVATE_API_TOKEN", "synthetic-secret")
    backend = _backend(config, runner)
    query = "qmd query; $(echo unsafe) --collection private --full-path"

    result = Gateway(config, backend).search_study(query, 3)

    assert len(runner.calls) == 1
    argv, options = runner.calls[0]
    assert argv == [
        _SYNTHETIC_QMD_EXECUTABLE, "search", "--format", "json", "--collection", "studyvault",
        "-n", "3", "--", query,
    ]
    assert options["shell"] is False
    assert options["timeout"] == 9.0
    assert options["cwd"] == str(config.study_root.resolve(strict=True))
    assert options["capture_output"] is True
    assert options["text"] is True
    assert options["encoding"] == "utf-8"
    assert options["errors"] == "strict"
    assert options["stdin"] == subprocess.DEVNULL
    assert "PRIVATE_API_TOKEN" not in options["env"]
    assert set(options["env"]).issubset({
        "PATH", "SYSTEMROOT", "WINDIR", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME",
        "XDG_CONFIG_HOME", "TEMP", "TMP",
    })
    assert result["backend"] == "qmd_bm25"
    assert result["results"][0]["source_path"] == "数学/synthetic.md"
    assert result["results"][0]["source_id"] == "study:数学/synthetic.md"
    assert result["results"][0]["score"] == 0.75
    assert str(tmp_path) not in str(result)


def test_runtime_provider_is_entered_only_for_the_search_process(tmp_path: Path, monkeypatch) -> None:
    config, _ = _study(tmp_path)
    runtime = _runtime(tmp_path)
    runner = FakeRunner()
    events: list[str] = []
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")

    @contextmanager
    def runtime_provider():
        events.append("entered")
        try:
            yield runtime
        finally:
            events.append("exited")

    QmdStudyBackend(config, runtime_provider=runtime_provider, runner=runner).search("valid", 5)

    assert events == ["entered", "exited"]
    assert len(runner.calls) == 1


@pytest.mark.parametrize("query", ["--all", "--index=private", "--collection", "query", "embed", "pull"])
def test_option_looking_query_is_only_a_positional_search_argument(tmp_path: Path, query: str) -> None:
    config, _ = _study(tmp_path)
    runner = FakeRunner()

    _backend(config, runner).search(query, 5)

    assert len(runner.calls) == 1
    argv, options = runner.calls[0]
    assert argv == [
        _SYNTHETIC_QMD_EXECUTABLE, "search", "--format", "json", "--collection", "studyvault",
        "-n", "5", "--", query,
    ]
    assert options["shell"] is False


@pytest.mark.parametrize("snippet_state", ["missing", "null"])
def test_empty_body_without_snippet_returns_empty_text(tmp_path: Path, snippet_state: str) -> None:
    config, _ = _study(tmp_path)
    record = _hit()
    if snippet_state == "missing":
        record.pop("snippet")
    else:
        record["snippet"] = None
    runner = FakeRunner(json.dumps([record], ensure_ascii=False))

    result = Gateway(config, _backend(config, runner)).search_study("valid", 5)

    assert result["results"][0]["snippet"] == ""
    assert result["results"][0]["source_path"] == "数学/synthetic.md"


@pytest.mark.parametrize("snippet", [0, ["not", "text"], {"raw": "object"}])
def test_nonstring_snippet_is_rejected(tmp_path: Path, snippet) -> None:
    config, _ = _study(tmp_path)
    runner = FakeRunner(json.dumps([_hit(snippet=snippet)], ensure_ascii=False))

    with pytest.raises(GatewayError) as exc:
        _backend(config, runner).search("valid", 5)

    assert exc.value.code == "BACKEND_BAD_OUTPUT"


def test_approved_version_mismatch_rejects_without_resolving_or_running(tmp_path: Path) -> None:
    config, _ = _study(tmp_path)
    runner = FakeRunner()
    resolver_calls: list[str] = []

    def resolver(name: str):
        resolver_calls.append(name)
        return _SYNTHETIC_QMD_EXECUTABLE

    backend = QmdStudyBackend(
        config, qmd_executable="approved-qmd", approved_qmd_version="2.8.2",
        resolver=resolver, runner=runner,
    )

    with pytest.raises(GatewayError) as exc:
        backend.search("valid", 5)

    assert exc.value.code == "QMD_VERSION_UNSUPPORTED"
    assert resolver_calls == []
    assert runner.calls == []


def test_config_version_mismatch_rejects_without_spawn(tmp_path: Path) -> None:
    config, _ = _study(tmp_path)
    config = AppConfig(gateway_version="0.1.0", study_root=config.study_root, expected_qmd_version="2.8.2")
    runner = FakeRunner()

    with pytest.raises(GatewayError) as exc:
        _backend(config, runner).search("valid", 5)

    assert exc.value.code == "QMD_VERSION_UNSUPPORTED"
    assert runner.calls == []


def test_missing_executable_rejects_without_spawn(tmp_path: Path) -> None:
    config, _ = _study(tmp_path)
    runner = FakeRunner()
    backend = QmdStudyBackend(
        config, qmd_executable="approved-qmd", approved_qmd_version="2.8.3",
        resolver=lambda _: None, runner=runner,
    )

    with pytest.raises(GatewayError) as exc:
        backend.search("valid", 5)

    assert exc.value.code == "QMD_NOT_FOUND"
    assert runner.calls == []


@pytest.mark.parametrize(
    "resolved",
    ["qmd", "C:/synthetic/bin/qmd.cmd", "C:/synthetic/bin/qmd.bat", "C:/synthetic/bin/qmd.ps1"],
)
def test_unsafe_or_unresolved_launcher_is_never_spawned(tmp_path: Path, resolved: str) -> None:
    config, _ = _study(tmp_path)
    runner = FakeRunner()
    backend = QmdStudyBackend(
        config, qmd_executable="approved-qmd", approved_qmd_version="2.8.3",
        resolver=lambda _: resolved, runner=runner,
    )

    with pytest.raises(GatewayError) as exc:
        backend.search("algebra & echo unsafe", 5)

    assert exc.value.code == "QMD_NOT_FOUND"
    assert runner.calls == []


def test_non_studyvault_local_config_never_spawns(tmp_path: Path) -> None:
    config, _ = _study(tmp_path)
    config = AppConfig(gateway_version="0.1.0", study_root=config.study_root, collection="private")
    runner = FakeRunner()

    with pytest.raises(GatewayError) as exc:
        _backend(config, runner).search("valid", 5)

    assert exc.value.code == "STUDY_UNAVAILABLE"
    assert runner.calls == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (subprocess.TimeoutExpired("private query", 9, stderr="private stderr"), "BACKEND_TIMEOUT"),
        (FileNotFoundError("C:/private/qmd.exe"), "QMD_NOT_FOUND"),
        (OSError("private path"), "QMD_NOT_FOUND"),
    ],
)
def test_runner_failures_have_stable_safe_errors(tmp_path: Path, error: Exception, code: str) -> None:
    config, _ = _study(tmp_path)
    runner = FakeRunner(error=error)

    with pytest.raises(GatewayError) as exc:
        _backend(config, runner).search("private query", 5)

    assert exc.value.code == code
    assert "private" not in str(exc.value)


@pytest.mark.parametrize("stdout", ["not json", "{}", '"hello"', '[42]', '[{"title": "missing source"}]', '[{"file": 3, "title": "x", "snippet": "y"}]', '[{"file": "qmd://studyvault/数学/synthetic.md", "title": "x", "snippet": "y", "score": NaN}]'])
def test_malformed_json_or_hit_shape_is_rejected(tmp_path: Path, stdout: str) -> None:
    config, _ = _study(tmp_path)

    with pytest.raises(GatewayError) as exc:
        _backend(config, FakeRunner(stdout)).search("valid", 5)

    assert exc.value.code == "BACKEND_BAD_OUTPUT"
    assert stdout not in str(exc.value)


def test_more_hits_than_requested_is_rejected(tmp_path: Path) -> None:
    config, _ = _study(tmp_path)
    runner = FakeRunner(json.dumps([_hit(), _hit()]))

    with pytest.raises(GatewayError) as exc:
        _backend(config, runner).search("valid", 1)

    assert exc.value.code == "BACKEND_BAD_OUTPUT"


@pytest.mark.parametrize("score", [True, "0.8", float("nan"), float("inf"), 10**400])
def test_nonfinite_or_nonnumeric_score_is_rejected(tmp_path: Path, score) -> None:
    config, _ = _study(tmp_path)
    runner = FakeRunner(json.dumps([_hit(score=score)]))

    with pytest.raises(GatewayError) as exc:
        _backend(config, runner).search("valid", 5)

    assert exc.value.code == "BACKEND_BAD_OUTPUT"


@pytest.mark.parametrize(
    "uri",
    [
        "qmd://private/数学/synthetic.md",
        "qmd://studyvault/../outside.md",
        "qmd://studyvault/%2e%2e/outside.md",
        "qmd://studyvault//outside.md",
        "qmd://studyvault/C:/private/lesson.md",
        "qmd://studyvault/%5Coutside.md",
        "qmd://studyvault/%5C%5Cserver/share.md",
        "qmd://studyvault/数学/missing.md",
        "qmd://studyvault/数学/synthetic.md?private=1",
        "C:/private/lesson.md",
        "file:///private/lesson.md",
    ],
)
def test_unsafe_or_missing_qmd_source_is_rejected(tmp_path: Path, uri: str) -> None:
    config, _ = _study(tmp_path)
    runner = FakeRunner(json.dumps([_hit(uri)]))

    with pytest.raises(GatewayError) as exc:
        _backend(config, runner).search("valid", 5)

    assert exc.value.code == "OUTSIDE_ALLOWLIST"
    assert "private" not in str(exc.value)


def test_resolved_link_or_junction_escape_is_rejected_deterministically(tmp_path: Path, monkeypatch) -> None:
    config, _ = _study(tmp_path)
    raw_link = config.study_root / "linked.md"
    outside = tmp_path / "outside.md"
    outside.write_text("Synthetic external note", encoding="utf-8")
    original_resolve = Path.resolve

    def resolve(path: Path, *args, **kwargs):
        if path == raw_link:
            return outside
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    runner = FakeRunner(json.dumps([_hit("qmd://studyvault/linked.md")]))

    with pytest.raises(GatewayError) as exc:
        _backend(config, runner).search("valid", 5)

    assert exc.value.code == "OUTSIDE_ALLOWLIST"


def test_nonzero_backend_exit_does_not_expose_stderr_or_scan_files(tmp_path: Path, monkeypatch) -> None:
    config, _ = _study(tmp_path)
    runner = FakeRunner(returncode=7)
    monkeypatch.setattr(os, "walk", lambda *args, **kwargs: pytest.fail("fallback scan"))

    with pytest.raises(GatewayError) as exc:
        _backend(config, runner).search("valid", 5)

    assert exc.value.code == "STUDY_UNAVAILABLE"
    assert "private stderr" not in str(exc.value)
    assert len(runner.calls) == 1
