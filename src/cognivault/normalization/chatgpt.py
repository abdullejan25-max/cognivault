"""Official export graph contract; real acquisition is independently validated."""
from .contracts import message, result, view, timestamp
from .native_sessions import native_key, boundary, conflicting_message_keys, MAX_CANDIDATES


def _pointer(s): return str(s).replace("~", "~0").replace("/", "~1")


def _valid_graph(mapping):
    roots = 0
    if len(mapping)>MAX_CANDIDATES or any(type(k) is not str or not 1<=len(k)<=2048 for k in mapping): return False
    if any(type(n) is not dict or type(n.get("children")) is not list for n in mapping.values()): return False
    for key, node in mapping.items():
        if type(node) is not dict or node.get("id", key) != key or type(node.get("children")) is not list: return False
        parent, children = node.get("parent"), node["children"]
        if parent is None: roots += 1
        elif parent not in mapping or key not in mapping[parent].get("children", []): return False
        if len(set(children)) != len(children): return False
        if any(c not in mapping or mapping[c].get("parent") != key for c in children): return False
    if roots != 1: return False
    done = set()
    for key in mapping:
        visiting = set()
        while key is not None and key not in done:
            if key in visiting: return False
            visiting.add(key); key = mapping[key].get("parent")
        done.update(visiting)
    return True


def chatgpt(payload, method, fingerprint, member):
    if type(payload) is not list: return result(method, state="unsupported", reason="export_object_unproven")
    views, candidates, ambiguous = [], 0, 0
    for i, r in enumerate(payload):
        if type(r) is not dict or type(r.get("mapping")) is not dict or not _valid_graph(r["mapping"]):
            return result(method, state="ambiguous", reason="inconsistent_branch_graph")
        key, conflict = boundary(r,"id","conversation_id")
        if conflict: return result(method,state="ambiguous",reason="session_boundary_conflict")
        if not native_key(key): key = [fingerprint, "conversation_object", i]
        loc = {"pointer": "/"+str(i), "member": member}
        v = view("chatgpt", key, loc, title=r.get("title"), ordering="parent_child",
                 created=timestamp(r.get("create_time"), unix_seconds=True)[0], updated=timestamp(r.get("update_time"), unix_seconds=True)[0])
        for pos, (nodekey, node) in enumerate(r["mapping"].items()):
            node_loc = dict(loc, pointer=loc["pointer"]+"/mapping/"+_pointer(nodekey))
            v["nodes"].append({"key":nodekey, "parent_key":node.get("parent"), "children":node["children"], "locator":node_loc, "has_message":node.get("message") is not None})
            p = node.get("message")
            if p is None: continue
            candidates += 1
            if candidates > MAX_CANDIDATES: return result(method, state="unsupported", reason="candidate_limit")
            if type(p) is not dict or type(p.get("author")) is not dict: ambiguous += 1; continue
            if p.get("status") is not None and p["status"] != "finished_successfully":
                ambiguous += 1; continue
            metadata = p.get("metadata") if type(p.get("metadata")) is dict else {}
            assertions = {"hidden": metadata.get("is_visually_hidden_from_conversation"), "status": p.get("status"), "end_turn": p.get("end_turn")}
            m = message(v, p.get("id") if native_key(p.get("id")) else nodekey, p["author"].get("role"), p.get("content"), p.get("create_time"),
                        dict(node_loc, pointer=node_loc["pointer"]+"/message"), pos, unix_seconds=True, parent=node.get("parent"), assertions=assertions,
                        model=metadata.get("model_slug") if type(metadata.get("model_slug")) is str else None)
            if m: v["messages"].append(m)
            else: ambiguous += 1
        if conflicting_message_keys(v): return result(method,state="ambiguous",candidates=candidates,ambiguous=candidates,reason="native_message_identity_conflict")
        views.append(v)
    return result(method, views, candidates=candidates, ambiguous=ambiguous)
