"""Safety of the isolated, copy-ready Desktop acceptance packet."""
import json
from pathlib import Path
import re
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_desktop_acceptance import prepare_desktop


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
