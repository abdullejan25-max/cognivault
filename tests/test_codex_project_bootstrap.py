"""Portable setup for Codex Desktop's project-scoped MCP configuration."""

import re
import shutil
import subprocess
import sys
import tomllib

import pytest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = REPOSITORY / ".codex" / "setup_mcp.py"
CONFIG_TEMPLATE = REPOSITORY / ".codex" / "config.example.toml"
LEGACY_TEMPLATE = REPOSITORY / "tests" / "fixtures" / "codex_generated_pre_cognivault.toml"


def test_setup_uses_absolute_uv_without_requiring_updated_host_path(tmp_path):
    repository = _clone_bootstrap_files(tmp_path / "checkout")
    (repository / "config.local.toml").write_text('[gateway]\nversion="0.8.0"\n', encoding="utf-8")
    uv_path = Path(shutil.which("uv")).resolve()
    result = subprocess.run(
        [sys.executable, str(repository / ".codex/setup_mcp.py"), "--uv", str(uv_path)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    server = tomllib.loads((repository / ".codex/config.toml").read_text(encoding="utf-8"))["mcp_servers"]["cognivault"]
    assert Path(server["command"]) == uv_path
    assert server["env"]["UV_PROJECT_ENVIRONMENT"] == (repository / ".venv").as_posix()


def test_installer_refuses_custom_relative_uv_command_without_overwriting_it(tmp_path):
    repository = _clone_bootstrap_files(tmp_path / "custom checkout")
    (repository / "config.local.toml").write_text('[gateway]\nversion="0.8.0"\n', encoding="utf-8")
    assert _run_setup(repository, tmp_path).returncode == 0
    config_path = repository / ".codex/config.toml"
    custom = config_path.read_text(encoding="utf-8").replace("tool_timeout_sec = 60", "tool_timeout_sec = 120")
    config_path.write_text(custom, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(repository / ".codex/setup_mcp.py"), "--uv", shutil.which("uv")],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert config_path.read_text(encoding="utf-8") == custom


def test_setup_does_not_treat_a_custom_absolute_program_as_owned_uv_config(tmp_path):
    repository = _clone_bootstrap_files(tmp_path / "checkout")
    (repository / "config.local.toml").write_text('[gateway]\nversion="0.8.0"\n', encoding="utf-8")
    initial = subprocess.run(
        [sys.executable, str(repository / ".codex/setup_mcp.py"), "--uv", shutil.which("uv")],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert initial.returncode == 0
    config_path = repository / ".codex/config.toml"
    custom = re.sub(r'(?m)^command = .*$',
        'command = "' + (tmp_path / "custom-launcher.exe").as_posix() + '"',
        config_path.read_text(encoding="utf-8"), count=1)
    config_path.write_text(custom, encoding="utf-8")
    result = _run_setup(repository, tmp_path)
    assert result.returncode != 0
    assert config_path.read_text(encoding="utf-8") == custom


def _clone_bootstrap_files(target: Path) -> Path:
    codex_directory = target / ".codex"
    codex_directory.mkdir(parents=True)
    (target / "pyproject.toml").write_text("[project]\nname = 'clone'\n", encoding="utf-8")
    (codex_directory / "setup_mcp.py").write_bytes(SETUP_SCRIPT.read_bytes())
    (codex_directory / "config.example.toml").write_bytes(CONFIG_TEMPLATE.read_bytes())
    return target


def _run_setup(repository: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repository / ".codex" / "setup_mcp.py")],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _legacy_generated_config(repository: Path) -> str:
    return (LEGACY_TEMPLATE.read_text(encoding="utf-8")
            .replace("__PROJECT_ROOT__", repository.resolve().as_posix())
            .replace("__SOURCE_ROOT__", (repository.resolve() / "src").as_posix())
            .replace("__LOCAL_CONFIG_PATH__", (repository.resolve() / "config.local.toml").as_posix()))


@pytest.mark.parametrize("no_sync", [True, False])
@pytest.mark.parametrize("move_checkout", [True, False])
def test_setup_migrates_only_recognized_old_generated_gateway_registration(
    tmp_path: Path, no_sync: bool, move_checkout: bool,
) -> None:
    repository = _clone_bootstrap_files(tmp_path / "old checkout")
    (repository / "config.local.toml").write_text('[gateway]\nversion="0.1.0"\n', encoding="utf-8")
    previous = _legacy_generated_config(repository)
    if not no_sync:
        previous = previous.replace('    "--no-sync",\n', "", 1)
    (repository / ".codex" / "config.toml").write_text(previous, encoding="utf-8")
    if move_checkout:
        repository = repository.rename(tmp_path / "new checkout")

    result = _run_setup(repository, tmp_path)

    assert result.returncode == 0, result.stderr
    migrated = tomllib.loads((repository / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert set(migrated["mcp_servers"]) == {"cognivault"}
    assert migrated["mcp_servers"]["cognivault"]["args"][6] == "cognivault.transports.mcp_stdio"
    assert migrated["mcp_servers"]["cognivault"]["cwd"] == repository.resolve().as_posix()


@pytest.mark.parametrize("change", ["remove_marker", "custom_timeout", "extra_server", "dual_gateway"])
def test_setup_preserves_unrecognized_old_and_dual_gateway_configs(
    tmp_path: Path, change: str,
) -> None:
    repository = _clone_bootstrap_files(tmp_path / "custom checkout")
    (repository / "config.local.toml").write_text('[gateway]\nversion="0.1.0"\n', encoding="utf-8")
    previous = _legacy_generated_config(repository)
    if change == "remove_marker":
        previous = previous.split("\n", 1)[1]
    elif change == "custom_timeout":
        previous = previous.replace("tool_timeout_sec = 60", "tool_timeout_sec = 120")
    elif change == "extra_server":
        previous += '\n[mcp_servers.custom]\ncommand="custom"\n'
    else:
        # Even a compatible canonical registration must not make a dual config acceptable.
        first = _run_setup(repository, tmp_path)
        assert first.returncode == 0, first.stderr
        canonical = (repository / ".codex" / "config.toml").read_text(encoding="utf-8")
        previous += "\n[mcp_servers." + canonical.split("[mcp_servers.", 1)[1]
    config_path = repository / ".codex" / "config.toml"
    config_path.write_text(previous, encoding="utf-8")

    result = _run_setup(repository, tmp_path)

    assert result.returncode != 0
    assert config_path.read_text(encoding="utf-8") == previous


def test_setup_generates_explicit_paths_for_a_clone_opened_from_another_directory(
    tmp_path: Path,
) -> None:
    repository = _clone_bootstrap_files(tmp_path / "clone with spaces" / "学习系统")
    (repository / "config.local.toml").write_text(
        '[gateway]\nversion = "0.1.0"\n', encoding="utf-8"
    )

    result = _run_setup(repository, tmp_path)

    assert result.returncode == 0, result.stderr
    generated_path = repository / ".codex" / "config.toml"
    generated_text = generated_path.read_text(encoding="utf-8")
    config = tomllib.loads(generated_text)
    assert set(config["mcp_servers"]) == {"cognivault"}
    server = config["mcp_servers"]["cognivault"]
    assert server["command"] == "uv"
    assert server["cwd"] == repository.resolve().as_posix()
    assert server["args"] == [
        "run",
        "--no-sync",
        "--project",
        repository.resolve().as_posix(),
        "python",
        "-m",
        "cognivault.transports.mcp_stdio",
        "--config",
        (repository.resolve() / "config.local.toml").as_posix(),
    ]
    assert server["enabled"] is True
    assert server["required"] is True
    assert server["env"] == {"PYTHONPATH": (repository / "src").resolve().as_posix()}
    assert server["startup_timeout_sec"] == 30
    assert server["tool_timeout_sec"] == 60
    assert server["default_tools_approval_mode"] == "writes"


def test_setup_refreshes_its_previous_generated_config_for_the_new_launcher_contract(
    tmp_path: Path,
) -> None:
    repository = _clone_bootstrap_files(tmp_path / "existing checkout")
    (repository / "config.local.toml").write_text(
        '[gateway]\nversion = "0.1.0"\n', encoding="utf-8"
    )
    config_path = repository / ".codex" / "config.toml"

    first = _run_setup(repository, tmp_path)
    assert first.returncode == 0, first.stderr
    generated = config_path.read_text(encoding="utf-8")
    previous = generated.replace('    "--no-sync",\n', "", 1)
    assert previous != generated
    config_path.write_text(previous, encoding="utf-8")

    result = _run_setup(repository, tmp_path)

    assert result.returncode == 0, result.stderr
    assert config_path.read_text(encoding="utf-8") == generated


def test_setup_preserves_a_working_machine_local_desktop_configuration(
    tmp_path: Path,
) -> None:
    repository = _clone_bootstrap_files(tmp_path / "existing checkout")
    (repository / "config.local.toml").write_text(
        '[gateway]\nversion = "0.1.0"\n', encoding="utf-8"
    )
    config_path = repository / ".codex" / "config.toml"
    local_config = (
        '[mcp_servers.cognivault]\n'
        'command = "uv"\n'
        'args = ["run", "--project", ".", "python", "-m", '
        '"cognivault.transports.mcp_stdio", "--config", '
        '"config.local.toml"]\n'
        f'cwd = "{repository.resolve().as_posix()}"\n'
        'enabled = true\n'
        'required = true\n'
        'startup_timeout_sec = 30\n'
        'tool_timeout_sec = 60\n'
        'default_tools_approval_mode = "writes"\n'
    )
    config_path.write_text(local_config, encoding="utf-8")

    result = _run_setup(repository, tmp_path)

    assert result.returncode == 0, result.stderr
    assert config_path.read_text(encoding="utf-8") == local_config


def test_setup_refreshes_its_own_config_after_the_checkout_is_moved(tmp_path: Path) -> None:
    repository = _clone_bootstrap_files(tmp_path / "original checkout")
    (repository / "config.local.toml").write_text(
        '[gateway]\nversion = "0.1.0"\n', encoding="utf-8"
    )
    first = _run_setup(repository, tmp_path)
    assert first.returncode == 0, first.stderr

    moved_parent = tmp_path / "moved"
    moved_parent.mkdir()
    moved_repository = repository.rename(moved_parent / "checkout with spaces")
    result = _run_setup(moved_repository, tmp_path)

    assert result.returncode == 0, result.stderr
    config = tomllib.loads(
        (moved_repository / ".codex" / "config.toml").read_text(encoding="utf-8")
    )
    assert set(config["mcp_servers"]) == {"cognivault"}
    server = config["mcp_servers"]["cognivault"]
    assert server["cwd"] == moved_repository.resolve().as_posix()
    assert server["args"][3] == moved_repository.resolve().as_posix()
    assert server["args"][-1] == (moved_repository.resolve() / "config.local.toml").as_posix()
    assert server["env"]["PYTHONPATH"] == (moved_repository / "src").resolve().as_posix()


def test_setup_refuses_to_overwrite_an_unrecognized_existing_configuration(
    tmp_path: Path,
) -> None:
    repository = _clone_bootstrap_files(tmp_path / "existing checkout")
    (repository / "config.local.toml").write_text(
        '[gateway]\nversion = "0.1.0"\n', encoding="utf-8"
    )
    config_path = repository / ".codex" / "config.toml"
    local_config = '[mcp_servers.cognivault]\ncommand = "uv"\ncwd = "."\nrequired = true\n'
    config_path.write_text(local_config, encoding="utf-8")

    result = _run_setup(repository, tmp_path)

    assert result.returncode != 0
    assert config_path.read_text(encoding="utf-8") == local_config


def test_setup_requires_a_valid_private_runtime_config_before_creating_host_config(
    tmp_path: Path,
) -> None:
    repository = _clone_bootstrap_files(tmp_path / "clean checkout")

    result = _run_setup(repository, tmp_path)

    assert result.returncode != 0
    assert "config.local.toml" in result.stderr
    assert not (repository / ".codex" / "config.toml").exists()


def test_public_codex_template_is_portable_and_local_config_is_untracked() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".codex/config.toml"],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode != 0

    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", ".codex/config.toml"],
        cwd=REPOSITORY,
        check=False,
    )
    assert ignored.returncode == 0

    template_text = CONFIG_TEMPLATE.read_text(encoding="utf-8")
    assert "__PROJECT_ROOT__" in template_text
    assert "__LOCAL_CONFIG_PATH__" in template_text
    assert "C:/Users/" not in template_text
    assert "C:\\Users\\" not in template_text
    assert re.search(r"(?m)^\s*cw[dD]\s*=\s*\"[A-Za-z]:[/\\]", template_text) is None


def test_gitignore_covers_private_runtime_data_without_hiding_text_fixtures() -> None:
    ignored_paths = [
        "config.local.toml",
        ".codex/config.toml",
        "var/private.db",
        "var/history.sqlite3-wal",
        "var/history.sqlite3-shm",
        "var/history.sqlite3-journal",
        "StudyVault/lesson.md",
        "History/export.json",
        "history/export.json",
        "personal-history/export.json",
        "wrong-answer-store/source.json",
        "Assets/original.bin",
        "assets/original.bin",
        "asset-store/original.bin",
        "asset-ingest/question.jpg",
        "indexes/private.sqlite",
        "inbox/import.json",
        "staging/history.json",
        "ocr-cache/page.json",
        "qmd-private-index/index.sqlite",
        "chatgpt-export/conversations.json",
        "chat-exports/conversations.json",
        "docs/debug.log",
    ]
    for path in ignored_paths:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", path],
            cwd=REPOSITORY,
            check=False,
        )
        assert result.returncode == 0, f"expected {path} to be ignored"

    for path in ("tests/fixtures/example.md", "tests/fixtures/example.json"):
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", path],
            cwd=REPOSITORY,
            check=False,
        )
        assert result.returncode == 1, f"expected synthetic text fixture {path} to be trackable"

    binary_fixture = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", "tests/fixtures/example.png"],
        cwd=REPOSITORY,
        check=False,
    )
    assert binary_fixture.returncode == 0
