"""The installer must not invent data locations or replace existing profiles."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest

from cognivault.contracts import GatewayError
from cognivault.runtime import load_gateway_from_config


REPOSITORY = Path(__file__).resolve().parents[1]


def clone_setup_files(target: Path) -> Path:
    (target / "scripts").mkdir(parents=True)
    (target / ".codex").mkdir()
    for name in ("pyproject.toml", ".codex/setup_mcp.py", ".codex/config.example.toml"):
        shutil.copyfile(REPOSITORY / name, target / name)
    shutil.copyfile(REPOSITORY / "scripts/setup_local.py", target / "scripts/setup_local.py")
    return target


def run_setup(root: Path, *, host: str = "codex") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(root / "scripts/setup_local.py"),
         "--uv", str(Path(shutil.which("uv")).resolve()), "--host", host],
        cwd=root.parent, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )


def test_unconfigured_study_can_start_without_touching_data_or_discovering_qmd(tmp_path):
    config = tmp_path / "local.toml"
    config.write_text(
        '[gateway]\nversion="0.8.0"\n[study]\nbackend="not_configured"\n'
        '[history]\nbackend="not_configured"\n[assets]\nbackend="not_configured"\n'
        '[permissions]\ncapabilities=["read"]\n', encoding="utf-8",
    )
    gateway = load_gateway_from_config(config)
    assert gateway.health_report() == {
        "gateway_version": "0.8.0",
        "study": {"configured": False, "root_exists": False, "readable": False},
        "history": {"backend": "not_configured", "status": "not_configured"},
        "qmd": {"discoverable": False},
    }
    with pytest.raises(GatewayError) as error:
        gateway.search_study("synthetic question")
    assert error.value.code == "STUDY_UNAVAILABLE"
    with pytest.raises(GatewayError) as error:
        gateway.list_history_sources()
    assert error.value.code == "HISTORY_UNAVAILABLE"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["local.toml"]


@pytest.mark.parametrize("study", [
    'backend="not_configured"\nroot="/unused"\n',
    'backend="not_configured"\nqmd_executable="unused"\n',
    'backend="other"\n',
    'backend=["qmd"]\n',
])
def test_disabled_study_rejects_conflicting_configuration(tmp_path, study):
    config = tmp_path / "local.toml"
    config.write_text('[gateway]\nversion="0.8.0"\n[study]\n' + study
                      + '[history]\nbackend="not_configured"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid local configuration"):
        load_gateway_from_config(config)


def test_setup_creates_loadable_read_only_config_and_absolute_host_launcher(tmp_path):
    root = clone_setup_files(tmp_path / "学习工具 with spaces")
    result = run_setup(root)
    assert result.returncode == 0, result.stderr
    raw = tomllib.loads((root / "config.local.toml").read_text(encoding="utf-8"))
    assert raw == {
        "gateway": {"version": "0.8.0"},
        "study": {"backend": "not_configured"},
        "history": {"backend": "not_configured"},
        "assets": {"backend": "not_configured"},
        "permissions": {"capabilities": ["read"]},
    }
    assert load_gateway_from_config(root / "config.local.toml").config.study_root is None
    server = tomllib.loads((root / ".codex/config.toml").read_text(encoding="utf-8"))["mcp_servers"]["cognivault"]
    assert Path(server["command"]) == Path(shutil.which("uv")).resolve()
    assert server["required"] is True
    assert server["args"] == ["run", "--no-sync", "--project", root.as_posix(),
                              "python", "-m", "cognivault.transports.mcp_stdio",
                              "--config", (root / "config.local.toml").as_posix()]
    assert "尚未" in result.stdout


def test_repeated_setup_and_moved_checkout_preserve_private_config_bytes(tmp_path):
    root = clone_setup_files(tmp_path / "first checkout")
    private = b'# synthetic profile\r\n[gateway]\r\nversion="0.8.0"\r\n[permissions]\r\ncapabilities=["read","write"]\r\n'
    (root / "config.local.toml").write_bytes(private)
    assert run_setup(root).returncode == 0
    before = (root / ".codex/config.toml").read_bytes()
    assert run_setup(root).returncode == 0
    assert (root / ".codex/config.toml").read_bytes() == before
    moved = root.rename(tmp_path / "moved 学习工具")
    result = run_setup(moved)
    assert result.returncode == 0, result.stderr
    server = tomllib.loads((moved / ".codex/config.toml").read_text(encoding="utf-8"))["mcp_servers"]["cognivault"]
    assert server["cwd"] == moved.as_posix()
    assert (moved / "config.local.toml").read_bytes() == private


def test_invalid_private_config_stops_before_creating_host_config(tmp_path):
    root = clone_setup_files(tmp_path / "checkout")
    invalid = b"[unfinished\n"
    (root / "config.local.toml").write_bytes(invalid)
    result = run_setup(root)
    assert result.returncode != 0
    assert not (root / ".codex/config.toml").exists()
    assert (root / "config.local.toml").read_bytes() == invalid
    assert "基础安装完成" not in result.stdout


def test_unknown_host_config_is_preserved_and_install_does_not_claim_success(tmp_path):
    root = clone_setup_files(tmp_path / "checkout")
    private = b'[gateway]\nversion="0.8.0"\n'
    (root / "config.local.toml").write_bytes(private)
    custom = b'[mcp_servers.other]\ncommand="custom"\n'
    (root / ".codex/config.toml").write_bytes(custom)
    result = run_setup(root)
    assert result.returncode != 0
    assert (root / ".codex/config.toml").read_bytes() == custom
    assert (root / "config.local.toml").read_bytes() == private
    assert "基础安装完成" not in result.stdout


def test_other_host_install_outputs_json_without_editing_codex(tmp_path):
    root = clone_setup_files(tmp_path / "checkout")
    result = run_setup(root, host="none")
    assert result.returncode == 0, result.stderr
    assert not (root / ".codex/config.toml").exists()
    start = result.stdout.index('{\n  "command"')
    launcher, _ = json.JSONDecoder().raw_decode(result.stdout[start:])
    assert Path(launcher["command"]).is_absolute()
    assert launcher["args"][-1] == (root / "config.local.toml").as_posix()
    assert launcher["env"]["UV_PROJECT_ENVIRONMENT"] == (root / ".venv").as_posix()
