"""Source-defined sessions only. Runtime/hook event names do not imply actors."""
from .contracts import message, result, view, encoded, timestamp

MAX_CANDIDATES = 100000


def native_key(value):
    return (type(value) is str and 1<=len(value)<=2048) or type(value) is int


def boundary(r, *fields):
    values={encoded(r[k]):r[k] for k in fields if native_key(r.get(k))}
    return (next(iter(values.values())) if len(values)==1 else None), len(values)>1


def conflicting_message_keys(v):
    keys={}
    for m in v["messages"]:
        key=encoded(m["native_key"])
        if key in keys and keys[key]!=m["message_id"]: return True
        keys[key]=m["message_id"]
    return False


def codex(rows, method):
    sessions, candidates, ignored = set(), [], 0
    for r, loc in rows:
        p = r.get("payload")
        if r.get("type") == "session_meta" and type(p) is dict and native_key(p.get("id")): sessions.add(p["id"])
        if r.get("type") == "response_item" and type(p) is dict and p.get("type") == "message":
            candidates.append((p, r.get("timestamp"), loc))
            if len(candidates) > MAX_CANDIDATES: return result(method, state="unsupported", reason="candidate_limit")
        else: ignored += 1
    if len(sessions) != 1: return result(method, state="ambiguous", candidates=len(candidates), ambiguous=len(candidates), ignored=ignored, reason="session_boundary_unproven")
    v = view("codex", next(iter(sessions)), {"record": "session_meta"})
    for p, time, loc in candidates:
        if p.get("status") is not None and p["status"] != "completed": continue
        m = message(v, p.get("id") if native_key(p.get("id")) else ["line", loc["line"]], p.get("role"), p.get("content"), time, loc, loc["line"], model=p.get("model") if type(p.get("model")) is str else None)
        if m: v["messages"].append(m)
    if conflicting_message_keys(v): return result(method,state="ambiguous",candidates=len(candidates),ambiguous=len(candidates),ignored=ignored,reason="native_message_identity_conflict")
    return result(method, [v], candidates=len(candidates), ambiguous=len(candidates)-len(v["messages"]), ignored=ignored)


def workbuddy(rows, method):
    sessions, candidates, ignored = set(), [], 0
    for r, loc in rows:
        if native_key(r.get("sessionId")): sessions.add(r["sessionId"])
        nested = r.get("message")
        p = r if "role" in r else nested if type(nested) is dict and "role" in nested else None
        if p is None: ignored += 1; continue
        candidates.append((r, p, loc))
        if len(candidates) > MAX_CANDIDATES: return result(method, state="unsupported", reason="candidate_limit")
    if not candidates: return result(method, ignored=ignored, reason="no_explicit_message_roles")
    if len(sessions) != 1: return result(method, state="ambiguous", candidates=len(candidates), ambiguous=len(candidates), ignored=ignored, reason="session_boundary_unproven")
    v = view("workbuddy", next(iter(sessions)), {"field": "sessionId"})
    for r, p, loc in candidates:
        if native_key(p.get("sessionId")) and p["sessionId"] not in sessions:
            return result(method,state="ambiguous",candidates=len(candidates),ambiguous=len(candidates),reason="session_boundary_conflict")
        if r.get("status") is not None and r["status"] != "completed": continue
        m = message(v, p.get("id") if native_key(p.get("id")) else ["line", loc["line"]], p.get("role"), p.get("content"), r.get("timestamp"), loc, loc["line"],
                    parent=r.get("parentId"), assertions={"format_status": "legacy_private_source", "source_type": r.get("type")})
        if m: v["messages"].append(m)
    if conflicting_message_keys(v): return result(method,state="ambiguous",candidates=len(candidates),ambiguous=len(candidates),ignored=ignored,reason="native_message_identity_conflict")
    return result(method, [v], candidates=len(candidates), ambiguous=len(candidates)-len(v["messages"]), ignored=ignored)


def hermes(rows, method):
    views, candidates, ambiguous, ignored, keys = [], 0, 0, 0, set()
    for r, rootloc in rows:
        if type(r) is not dict or type(r.get("messages")) is not list: ignored += 1; continue
        key, conflict = boundary(r,"id","session_id")
        if conflict or not native_key(key) or key in keys:
            return result(method, state="ambiguous", reason="session_boundary_conflict")
        keys.add(key)
        v = view("hermes", key, rootloc, title=r.get("title"),created=timestamp(r.get("started_at"),unix_seconds=True)[0],updated=timestamp(r.get("last_active"),unix_seconds=True)[0])
        for i, p in enumerate(r["messages"]):
            candidates += 1
            if candidates > MAX_CANDIDATES: return result(method, state="unsupported", reason="candidate_limit")
            if type(p) is not dict or p.get("session_id", key) != key: ambiguous += 1; continue
            loc = dict(rootloc, pointer=rootloc.get("pointer", "") + "/messages/" + str(i))
            assertions = {k:p[k] for k in ("active", "compacted", "observed", "display_kind", "tool_calls", "tool_call_id", "tool_name") if k in p}
            m = message(v, p.get("id") if native_key(p.get("id")) else ["position", i], p.get("role"), p.get("content"), p.get("timestamp"), loc, i,
                        unix_seconds=True, assertions=assertions)
            if m: v["messages"].append(m)
            else: ambiguous += 1
        if conflicting_message_keys(v): return result(method,state="ambiguous",candidates=candidates,ambiguous=candidates,reason="native_message_identity_conflict")
        views.append(v)
    return result(method, views, candidates=candidates, ambiguous=ambiguous, ignored=ignored, reason=None if views else "no_session_message_array")
