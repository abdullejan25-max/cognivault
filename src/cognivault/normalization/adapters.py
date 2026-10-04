"""Strict parsing. No external reads, clocks, model calls or guesses."""
import io
import json
import zipfile
from ..migration.codex_jsonl_spans import iter_jsonl_record_spans
from .contracts import result
from .native_sessions import codex, workbuddy, hermes
from .chatgpt import chatgpt


class ParseFailure(ValueError): pass


def _pairs(items):
    out = {}
    for k, v in items:
        if k in out: raise ParseFailure("duplicate_key")
        out[k] = v
    return out


def loads(raw):
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ParseFailure("non_finite")))


def rows(raw):
    for s in iter_jsonl_record_spans(io.BytesIO(raw)):
        if s.oversized: raise ParseFailure("line_limit")
        if not s.raw_line.strip(): continue
        value = loads(s.raw_line)
        if type(value) is not dict: raise ParseFailure("record_shape")
        yield value, {"line": s.record_ordinal + 1, "byte_start": s.byte_start, "byte_end": s.byte_end}


def normalize_source(system, fmt, raw, fingerprint):
    method = system + "-" + fmt + "-1"
    if type(raw) is not bytes or len(raw) > 512 * 1024 * 1024:
        return result(method, state="unsupported", reason="source_limit")
    try:
        if system == "codex" and fmt == "jsonl": return codex(rows(raw), method)
        if system == "workbuddy" and fmt == "jsonl": return workbuddy(rows(raw), method)
        if system == "hermes" and fmt in {"json", "jsonl"}:
            values = rows(raw) if fmt == "jsonl" else [(loads(raw), {"pointer": ""})]
            return hermes(values, method)
        if system == "chatgpt" and fmt in {"json", "zip"}:
            member = None
            if fmt == "zip":
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    names = [i for i in z.infolist() if i.filename == "conversations.json"]
                    if len(names) != 1 or names[0].file_size > 512 * 1024 * 1024:
                        return result(method, state="unsupported", reason="conversations_member_unavailable")
                    raw = z.read(names[0]); member = "conversations.json"
            return chatgpt(loads(raw), method, fingerprint, member)
        return result(method, state="unsupported", reason="no_proven_conversation_contract")
    except (ParseFailure, ValueError, TypeError, KeyError, UnicodeError, RecursionError, zipfile.BadZipFile):
        return result(method, state="malformed", reason="invalid_source_structure")
