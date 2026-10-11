"""Safety of the isolated, copy-ready Desktop acceptance packet."""
import json
from pathlib import Path
import re
import sys
import tomllib

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_desktop_acceptance import prepare_desktop


def test_generated_host_configs_disable_inherited_production_server_names(tmp_path):
    """Synthetic projects must mask production names with isolated transports."""
    root = tmp_path / "desktop"
    prepare_desktop(root)
    repo = Path(__file__).resolve().parents[1]
    for name, capabilities in (("writer", ["read", "write"]), ("reader", ["read"])):
        project = root / name
        gateway_path = project / "gateway.toml"
        gateway = tomllib.loads(gateway_path.read_text(encoding="utf-8"))
        assert gateway["permissions"]["capabilities"] == capabilities
        for path in (gateway["history"]["database"], gateway["memory"]["database"],
                     gateway["assets"]["database"], gateway["assets"]["root"]):
            assert Path(path).is_relative_to(root)

        host = tomllib.loads((project / ".codex/config.toml").read_text(encoding="utf-8"))
        servers = host["mcp_servers"]
        assert set(servers) == {"cognivault_synthetic", "cognivault", "study_system"}
        for server_name, server in servers.items():
            assert server["command"] == Path(sys.executable).as_posix()
            assert server["args"] == [
                "-m", "cognivault.transports.mcp_stdio", "--config", gateway_path.as_posix(),
            ]
            assert server["cwd"] == repo.as_posix()
            assert server["enabled"] is (server_name == "cognivault_synthetic")
            assert server["required"] is (server_name == "cognivault_synthetic")


def test_prompt_packet_binds_revision_permission_and_restart_to_same_goal(tmp_path):
    """A misplaced/truncated ID must not turn a goal check into another fact."""
    root = tmp_path / "desktop"
    prepare_desktop(root)
    manifest = json.loads((root / "acceptance-manifest.json").read_text(encoding="utf-8"))
    target = manifest["target"]
    assert re.fullmatch(r"memory:[0-9a-f]{32}", target["memory_id"])
    assert target["subject"] == "self"
    assert target["predicate"] == "goal"
    for gate in ("revision", "permission_isolation", "restart_persistence"):
        prompt = (root / manifest["prompts"][gate]).read_text(encoding="utf-8")
        block = re.search(r"```json\n(.*?)\n```", prompt, re.DOTALL)
        assert block, f"{gate} must include a complete, independently copyable target"
        binding = json.loads(block.group(1))
        assert binding == target
    assert len(manifest["prompts"]) == 6
    assert manifest["evidence_type"] == "PREPARED ONLY"
    assert all(row["status"] == "BLOCKED" for row in json.loads(
        (root / "desktop-results.json").read_text(encoding="utf-8")
    ).values())


@pytest.mark.parametrize("memory_id", ["memory", "memory:short", ""])
def test_prompt_builder_refuses_missing_complete_target(memory_id):
    """Never generate a write prompt that silently falls back to source search."""
    import prepare_desktop_acceptance as acceptance
    with pytest.raises(ValueError, match="target"):
        acceptance.build_acceptance_prompts(
            {"memory_id": memory_id, "subject": "self", "predicate": "goal"},
            "message:" + "1" * 64,
        )
