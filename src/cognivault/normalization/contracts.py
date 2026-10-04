"""Versioned pure contracts. IDs contain no path or migration clock."""
from datetime import datetime, timezone
import hashlib
import json
import math

VERSION = "p13-normalize-1"
ROLES = frozenset({"user", "assistant", "system", "developer", "tool", "function"})


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identity(kind, *values):
    return kind + ":" + hashlib.sha256(encoded(values).encode()).hexdigest()


def timestamp(value, *, unix_seconds=False):
    if value is None: return None, "unknown"
    try:
        if unix_seconds and type(value) in (int, float) and math.isfinite(value):
            dt = datetime.fromtimestamp(value, timezone.utc)
        elif type(value) is str and "T" in value:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None: return None, "ambiguous"
        else: return None, "ambiguous"
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), "reliable"
    except (ValueError, TypeError, OverflowError, OSError):
        return None, "ambiguous"


def content_parts(content):
    if type(content) is str: parts = [{"type": "text", "text": content}]
    elif type(content) is list and all(type(p) in (str, dict) for p in content):
        parts = [{"type": "text", "text": p} if type(p) is str else p for p in content]
    elif type(content) is dict and content.get("content_type") in {"text", "multimodal_text"} and type(content.get("parts")) is list:
        return content_parts(content["parts"])
    else: return None
    if any(p.get("type") in {"text", "input_text", "output_text"} and type(p.get("text")) is not str for p in parts): return None
    text = "".join(p["text"] for p in parts if p.get("type") in {"text", "input_text", "output_text"} and type(p.get("text")) is str)
    attachments = [{"part_index": i, "status": "unresolved", "source_part": p} for i, p in enumerate(parts)
                   if p.get("type") not in {"text", "input_text", "output_text"}]
    return parts, text, attachments


def view(system, native_key, locator, *, title=None, ordering="source_sequence", created=None, updated=None):
    return {"conversation_id": identity("conversation", system, native_key), "native_key_digest": identity("native", native_key),
            "source_system": system, "locator": locator, "title": title if type(title) is str else None,
            "ordering": ordering, "created_at": created, "updated_at": updated, "messages": [], "nodes": []}


def message(v, key, role, content, raw_time, locator, position, *, unix_seconds=False, parent=None, assertions=None, model=None):
    if type(role) is not str or role not in ROLES: return None
    mapped = content_parts(content)
    if mapped is None: return None
    parts, text, attachments = mapped
    occurred, quality = timestamp(raw_time, unix_seconds=unix_seconds)
    payload = {"role": role, "parts": parts, "occurred_at": occurred, "raw_time": raw_time, "model": model, "assertions": assertions or {}}
    return {"message_id": identity("message", v["conversation_id"], key, payload), "conversation_id": v["conversation_id"],
            "role": role, "parts": parts, "text": text, "occurred_at": occurred, "raw_timestamp": raw_time,
            "time_quality": quality, "confidence": "exact" if quality == "reliable" else "derived-safe",
            "locator": locator, "position": position, "native_key": key, "parent_key": parent,
            "attachments": attachments, "assertions": assertions or {}, "model": model}


def result(method, views=(), *, state=None, candidates=0, ambiguous=0, ignored=0, reason=None):
    views = [v for v in views if v["messages"]]
    return {"version": VERSION, "method": method, "state": state or ("partial" if views and ambiguous else "normalized" if views else "ambiguous" if ambiguous else "unsupported"),
            "reason": reason, "views": views, "candidates": candidates, "ambiguous_messages": ambiguous, "ignored_records": ignored}
