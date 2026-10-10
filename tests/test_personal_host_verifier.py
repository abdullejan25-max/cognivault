"""Host traces may collect focused domain evidence over several native calls."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_personal_host import verify


def bundle(domains, *, ok=True):
    return {"type": "item.completed", "item": {
        "type": "mcp_tool_call", "server": "cognivault_synthetic",
        "tool": "retrieve_evidence", "status": "completed",
        "result": {"structured_content": {
            "ok": ok, "evidence": [{"domain": domain} for domain in domains],
        }},
    }}


def completed_answer():
    return [
        {"type": "item.completed", "item": {
            "type": "agent_message", "text": (
                "History message:synthetic, Memory memory:synthetic#version=1, "
                "textbook document://synthetic#page=1, wrong-answer://synthetic"
            ),
        }},
        {"type": "turn.completed"},
    ]


def test_successful_focused_bundles_can_supply_three_domains_together():
    personal = [bundle(["study"]), bundle(["history", "memory"]), *completed_answer()]
    result = verify(personal, [{"type": "turn.completed"}])
    assert result["status"] == "PASS"
    assert len(result["tool_calls"]) == 2


def test_failed_bundle_cannot_supply_missing_domains():
    personal = [bundle(["study"]), bundle(["history", "memory", "study"], ok=False),
                *completed_answer()]
    with pytest.raises(AssertionError, match="successful three-domain"):
        verify(personal, [{"type": "turn.completed"}])
