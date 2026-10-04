"""Opt-in Codex CLI Host smoke with explicit inline MCP config and synthetic data."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from cognivault.adapters.history import SQLiteHistoryBackend
from cognivault.contracts import HistoryImportItem


def test_codex_host_calls_health_and_history_with_inline_server_override(tmp_path: Path) -> None:
    if os.environ.get("CODEX_HOST_E2E") != "1":
        pytest.skip("set CODEX_HOST_E2E=1 to run the real Codex CLI Host smoke")
    codex = shutil.which("codex")
    if codex is None:
        pytest.skip("Codex CLI is unavailable on this host")
    if shutil.which("uv") is None:
        pytest.skip("uv is unavailable on the Codex Host PATH")

    study = tmp_path / "study"
    study.mkdir()
    history_db = tmp_path / "history.db"
    history = SQLiteHistoryBackend(history_db)
    history.register_source("synthetic-export", "Synthetic export")
    history.import_items("synthetic-export", [HistoryImportItem(
        "entry-1", "conversation-1", "user", "Codex synthetic marker",
        "2026-09-25T12:00:00Z",
    )])
    config = tmp_path / "runtime.toml"
    config.write_text(
        '[gateway]\nversion="0.1.0"\n[study]\n'
        f'root={json.dumps(study.as_posix())}\nqmd_collection="studyvault"\nqmd_version="2.8.3"\n'
        'qmd_executable="not-used"\n[history]\nbackend="sqlite"\n'
        f'database={json.dumps(history_db.as_posix())}\n'
        '[permissions]\ncapabilities=["read"]\n',
        encoding="utf-8",
    )
    repository = Path(__file__).resolve().parents[1]
    server_args = ["run", "--project", str(repository), "python", "-m",
                   "cognivault.transports.mcp_stdio", "--config", str(config)]
    command = [
        codex, "-C", str(tmp_path), "-s", "read-only", "--ask-for-approval", "never",
        "-c", f'mcp_servers.phase9_study.command={json.dumps("uv")}',
        "-c", f'mcp_servers.phase9_study.args={json.dumps(server_args)}',
        "-c", f'mcp_servers.phase9_study.cwd={json.dumps(str(repository))}',
        "-c", "mcp_servers.phase9_study.required=true",
        "-c", "mcp_servers.phase9_study.startup_timeout_sec=30",
        "exec", "--ephemeral", "--ignore-user-config", "--json", "--skip-git-repo-check",
        'Use the phase9_study MCP server. Call health_report and search_history with query '
        '"synthetic marker". Do not use other tools. Report the history source ID and health status.',
    ]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=120, check=False)
    diagnostic = (result.stdout + result.stderr).replace(str(tmp_path), "[synthetic-temp]")
    assert result.returncode == 0, "Codex CLI Host E2E failed: " + diagnostic[-2000:]
    events = []
    for line in result.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    def nested_tool_calls(value):
        if isinstance(value, dict):
            if value.get("type") == "mcp_tool_call":
                yield value
            for child in value.values():
                yield from nested_tool_calls(child)
        elif isinstance(value, list):
            for child in value:
                yield from nested_tool_calls(child)

    tool_calls = [call for event in events for call in nested_tool_calls(event)]
    serialized_calls = [json.dumps(call, ensure_ascii=False) for call in tool_calls]
    assert any("health_report" in call for call in serialized_calls), diagnostic[-2000:]
    assert any("search_history" in call for call in serialized_calls), diagnostic[-2000:]
    assert "synthetic-export" in result.stdout
    assert "ready" in result.stdout
