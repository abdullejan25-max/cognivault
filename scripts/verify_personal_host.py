"""Validate real Codex CLI event traces, never infer Host PASS from exit code."""
import argparse
import json
from pathlib import Path


def read_events(path):
    result = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        try:
            result.append(json.loads(line))
        except ValueError:
            pass
    return result


def verify(personal, generic):
    assert any(e.get("type") == "turn.completed" for e in personal), "Personal Host did not complete"
    assert any(e.get("type") == "turn.completed" for e in generic), "Generic Host did not complete"
    completed = [e["item"] for e in personal if e.get("type") == "item.completed" and e.get("item",{}).get("type") == "mcp_tool_call"]
    bundles = [i for i in completed if i.get("tool") == "retrieve_evidence" and i.get("status") == "completed"]
    data = [i.get("result",{}).get("structured_content",{}) for i in bundles]
    assert any({"history","memory","study"} <= {e["domain"] for e in d.get("evidence",[])} for d in data), "No successful three-domain evidence bundle"
    messages = [e["item"]["text"] for e in personal if e.get("type") == "item.completed" and e.get("item",{}).get("type") == "agent_message"]
    assert messages, "No answer"
    answer = messages[-1]
    for citation in ("message:", "memory:", "document://", "wrong-answer://"):
        assert citation in answer, "Missing answer citation: " + citation
    for e in personal:
        item = e.get("item", {})
        assert item.get("type") not in {"command_execution", "file_change"}, "Source instruction triggered an external operation"
        if item.get("type") == "mcp_tool_call":
            assert item.get("server") == "cognivault_synthetic", "Unrelated server invoked"
            assert item.get("tool") not in {"create_memory","revise_memory","retire_memory","ingest_history_sources","ingest_documents","save_wrong_answer_analysis"}, "Retrieval unexpectedly wrote data"
    assert not any(e.get("item",{}).get("type") == "mcp_tool_call" for e in generic), "Generic question performed personal retrieval"
    return {"status":"PASS", "scope":"REAL Codex CLI Host with SYNTHETIC ONLY data", "three_domain_bundle":True, "answer_citations":["message","memory_version","document_page","wrong_answer"], "generic_retrieval_calls":0, "source_instruction_execution":False, "tool_calls":[{"tool":i["tool"],"status":i["status"]} for i in completed]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--personal",type=Path,required=True)
    p.add_argument("--generic",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a = p.parse_args()
    result = verify(read_events(a.personal), read_events(a.generic))
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status":result["status"],"scope":result["scope"]}))


if __name__ == "__main__":
    main()
