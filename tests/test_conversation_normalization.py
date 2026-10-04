"""All bytes here are invented. Test deterministic evidence, not personal exports."""
import json
import pytest
from cognivault.normalization.adapters import normalize_source


def run(system, records, fmt="jsonl", fingerprint="a" * 64):
    raw = ("\n".join(json.dumps(r) for r in records) + "\n").encode() if fmt == "jsonl" else json.dumps(records).encode()
    return normalize_source(system, fmt, raw, fingerprint)


def codex(text="Synthetic answer", time="2026-01-02T00:00:00Z"):
    return [{"type": "session_meta", "payload": {"id": "invented-session"}},
            {"type": "response_item", "timestamp": time, "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}}]


def test_codex_explicit_role_order_time_and_immutable_locator():
    result = run("codex", codex())
    m = result["views"][0]["messages"][0]
    assert m["role"] == "assistant" and m["text"] == "Synthetic answer"
    assert m["occurred_at"] == "2026-01-02T00:00:00Z" and m["confidence"] == "exact"
    assert m["locator"]["line"] == 2 and m["position"] == 2
    assert result["views"][0]["ordering"] == "source_sequence"


def test_missing_time_is_nullable_not_import_clock():
    result = run("codex", codex(time=None))
    m = result["views"][0]["messages"][0]
    assert m["occurred_at"] is None and m["time_quality"] == "unknown"
    assert m["confidence"] == "derived-safe"


@pytest.mark.parametrize("timestamp", ["yesterday", "2026-01-02", "2026-01-02T00:00:00", True])
def test_unreliable_time_is_not_guessed(timestamp):
    m = run("codex", codex(time=timestamp))["views"][0]["messages"][0]
    assert m["occurred_at"] is None and m["time_quality"] == "ambiguous"


def test_unknown_role_remains_source_only_without_losing_known_messages():
    rows = codex()
    rows.append({"type": "response_item", "payload": {"type": "message", "role": "mystery", "content": []}})
    result = run("codex", rows)
    assert result["candidates"] == 2 and result["ambiguous_messages"] == 1
    assert len(result["views"][0]["messages"]) == 1


def test_multiple_codex_session_boundaries_produce_no_guessed_conversation():
    result = run("codex", codex() + [{"type": "session_meta", "payload": {"id": "another"}}])
    assert result["state"] == "ambiguous" and result["views"] == []


@pytest.mark.parametrize("raw", [b'{"type":"session_meta","type":"response_item"}\n', b'{broken}\n', b'NaN\n'])
def test_malformed_json_and_duplicate_keys_never_partially_normalize(raw):
    result = normalize_source("codex", "jsonl", raw, "a" * 64)
    assert result["state"] == "malformed" and result["views"] == []


def test_native_session_snapshots_share_message_identity_but_changed_payload_is_variant():
    first = run("codex", codex())["views"][0]
    copied = run("codex", codex(), fingerprint="b" * 64)["views"][0]
    changed = run("codex", codex(text="Synthetic revision"))["views"][0]
    assert first["conversation_id"] == copied["conversation_id"]
    assert first["messages"][0]["message_id"] == copied["messages"][0]["message_id"]
    assert first["messages"][0]["message_id"] != changed["messages"][0]["message_id"]


def test_workbuddy_does_not_promote_roleless_tool_or_hook_events():
    rows = [{"sessionId": "invented-wb", "id": "u", "role": "user", "content": "Synthetic question", "timestamp": "2026-01-02T00:00:00Z"},
            {"sessionId": "invented-wb", "type": "assistant", "content": "Must stay evidence"},
            {"event": "Stop", "sender": "WorkBuddy", "content": "Hook output"}]
    result = run("workbuddy", rows)
    assert [m["role"] for m in result["views"][0]["messages"]] == ["user"]
    assert result["ignored_records"] == 2


def test_hermes_message_array_with_native_times_and_unknown_compaction_role():
    result = run("hermes", {"id": "invented-hermes", "messages": [
        {"id": 1, "role": "user", "content": "Synthetic", "timestamp": 1767312000.0},
        {"id": 2, "role": "compaction", "content": "Unknown actor", "timestamp": 1767312001.0}]}, fmt="json")
    assert result["candidates"] == 2 and result["ambiguous_messages"] == 1
    assert result["views"][0]["messages"][0]["occurred_at"] == "2026-01-02T00:00:00Z"


def graph():
    def node(key, parent, children, role, text):
        return {"id": key, "parent": parent, "children": children, "message": None if role is None else {
            "id": key, "author": {"role": role}, "create_time": 1767312000,
            "content": {"content_type": "text", "parts": [text]}, "metadata": {"is_visually_hidden_from_conversation": key == "b"}}}
    return [{"id": "invented-chatgpt", "title": "Synthetic branches", "mapping": {
        "root": node("root", None, ["u"], None, ""), "u": node("u", "root", ["a", "b"], "user", "Synthetic prompt"),
        "a": node("a", "u", [], "assistant", "First variant"), "b": node("b", "u", [], "assistant", "Second variant")}, "current_node": None}]


def test_chatgpt_branches_and_hidden_nodes_are_preserved_without_main_branch_guess():
    view = run("chatgpt", graph(), fmt="json")["views"][0]
    assert view["ordering"] == "parent_child" and len(view["nodes"]) == 4
    assert len(view["messages"]) == 3
    assert [m["parent_key"] for m in view["messages"] if m["role"] == "assistant"] == ["u", "u"]
    assert view["messages"][2]["assertions"]["hidden"] is True


def test_chatgpt_inconsistent_or_cyclic_graph_is_ambiguous_source_only():
    rows = graph()
    rows[0]["mapping"]["u"]["parent"] = "a"
    result = run("chatgpt", rows, fmt="json")
    assert result["state"] == "ambiguous" and result["views"] == []


def test_missing_attachment_is_evidence_metadata_not_fake_asset():
    rows = codex()
    rows[1]["payload"]["content"].append({"type": "input_image", "image_url": "unavailable-image"})
    m = run("codex", rows)["views"][0]["messages"][0]
    assert m["attachments"][0]["status"] == "unresolved"
    assert m["attachments"][0].get("asset_uri") is None


@pytest.mark.parametrize("system,fmt,raw", [("gemini", "zip", b"opaque activity archive"), ("basic_memory", "markdown", b"## User\nnot a proven boundary"), ("v1", "sqlite", b"raw DB evidence")])
def test_unsupported_formats_retain_source_only(system, fmt, raw):
    result = normalize_source(system, fmt, raw, "a" * 64)
    assert result["state"] == "unsupported" and result["views"] == []


def test_unknown_content_container_is_not_promoted_to_exact_text():
    rows=graph()
    rows[0]["mapping"]["a"]["message"]["content"]={"content_type":"unsupported_container","parts":["Opaque synthetic"]}
    r=run("chatgpt",rows,fmt="json")
    assert r["ambiguous_messages"] == 1 and len(r["views"][0]["messages"]) == 2


def test_incomplete_streamed_chatgpt_message_remains_source_only():
    rows=graph(); rows[0]["mapping"]["a"]["message"]["status"]="in_progress"
    r=run("chatgpt",rows,fmt="json")
    assert r["ambiguous_messages"] == 1 and len(r["views"][0]["messages"]) == 2


def test_malformed_child_node_never_escapes_parser():
    rows=graph(); rows[0]["mapping"]["a"]=[]
    r=run("chatgpt",rows,fmt="json")
    assert r["views"] == [] and r["state"] in {"ambiguous","malformed"}


def test_hermes_explicit_session_id_fallback_and_conflict_guard():
    r=run("hermes",{"id":None,"session_id":"synthetic","messages":[{"role":"user","content":"Synthetic"}]},fmt="json")
    assert len(r["views"]) == 1
    r=run("hermes",{"id":"one","session_id":"two","messages":[{"role":"user","content":"Synthetic"}]},fmt="json")
    assert r["views"] == [] and r["state"] == "ambiguous"


def test_conflicting_native_message_id_is_not_silently_accepted():
    rows=[{"sessionId":"synthetic","id":"m","role":"user","content":"First"},
          {"sessionId":"synthetic","id":"m","role":"user","content":"Revision"}]
    r=run("workbuddy",rows)
    assert r["views"] == [] and r["state"] == "ambiguous"


def test_illegal_text_part_is_ambiguous_instead_of_empty_exact_message():
    rows=codex(); rows[1]["payload"]["content"]=[{"type":"text","text":42}]
    r=run("codex",rows)
    assert r["ambiguous_messages"] == 1 and r["views"] == []


def test_explicit_incomplete_native_message_stays_source_only():
    r=run("workbuddy",[{"sessionId":"synthetic","id":"m","role":"assistant","status":"interrupted","content":"Partial bytes"}])
    assert r["ambiguous_messages"] == 1 and r["views"] == []
    rows=codex(); rows[1]["payload"]["status"]="in_progress"
    r=run("codex",rows)
    assert r["ambiguous_messages"] == 1 and r["views"] == []
