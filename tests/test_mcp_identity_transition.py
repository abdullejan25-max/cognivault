"""Synthetic subprocess checks for the deprecated stdio entry point only."""

import json
import os
from pathlib import Path
import sys
import tempfile

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest


@pytest.mark.parametrize("module", [
    "cognivault.transports.mcp_stdio",
    "chatgpt_study_system.transports.mcp_stdio",  # Deprecated startup compatibility.
])
def test_stdio_entry_points_serve_one_canonical_identity(tmp_path: Path, module: str) -> None:
    study = tmp_path / "synthetic-study"
    study.mkdir()
    config = tmp_path / "synthetic.toml"
    config.write_text(
        '[gateway]\nversion="0.1.0"\n[study]\n'
        f'root={json.dumps(study.as_posix())}\nqmd_executable="synthetic-qmd-not-installed"\n'
        'qmd_collection="studyvault"\nqmd_version="2.8.3"\n'
        '[history]\nbackend="not_configured"\n[permissions]\ncapabilities=["read"]\n',
        encoding="utf-8",
    )
    repository = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repository / "src")
    params = StdioServerParameters(
        command=sys.executable, args=["-B", "-m", module, "--config", str(config)],
        env=env, cwd=repository,
    )

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr:
        async def check() -> None:
            with anyio.fail_after(30):
                async with stdio_client(params, errlog=stderr) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as client:
                        initialized = await client.initialize()
                        assert initialized.serverInfo.name == "cognivault"
                        assert initialized.serverInfo.version == "0.7.0"
                        health = await client.call_tool("health_report", {})
                        assert health.structuredContent["ok"] is True
                        workflow = await client.read_resource("study-workflow://wrong-answer")
                        assert "original visual" in workflow.contents[0].text.lower()

        anyio.run(check)
        stderr.seek(0)
        notice = stderr.read()
    if module.startswith("chatgpt_study_system."):
        assert "deprecated" in notice.lower()
        assert "cognivault.transports.mcp_stdio" in notice
    else:
        assert "deprecated" not in notice.lower()
