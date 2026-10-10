"""Memory permission and evidence resolution at the Gateway boundary.

Reference resolution proves availability, never semantic truth. Verified is an
explicit, reported caller review with a note and currently accessible sources.
"""
from .contracts import GatewayError
from .adapters.memory import _references, SQLiteMemoryStore
from .provenance import ReportedIdentity


def resolve_sources(gateway, refs):
    gateway._require_capability("read")
    result = []
    for ref in refs:
        try:
            if ref.startswith("history:"):
                source_time = gateway.fetch_history_item(ref)["item"]["created_at"]
            elif ref.startswith("message:"):
                source_time = gateway.fetch_canonical_message(ref, length=1, evidence_limit=1)["message"].get("occurred_at")
            elif ref.startswith("document://"):
                gateway.fetch_document(ref)
                source_time = None
            else:
                gateway.get_wrong_answer_bundle(ref, limit=1)
                source_time = None
            result.append({"source_ref": ref, "status": "resolved", "occurred_at":source_time})
        except GatewayError as error:
            result.append({"source_ref": ref, "status": "unavailable", "error_code": error.code})
    return result


def decorate(gateway, record):
    result = dict(record)
    result["source_resolution"] = resolve_sources(gateway, record["source_refs"])
    result["sources_resolved"] = all(r["status"] == "resolved" for r in result["source_resolution"])
    result["effective_epistemic_status"] = ("unverified" if record["epistemic_status"] == "verified" and not result["sources_resolved"] else record["epistemic_status"])
    result["evidence_verified"] = False  # Semantic truth is never inferred from ID existence.
    times = [r["occurred_at"] for r in result["source_resolution"] if r.get("occurred_at")]
    result["source_latest_at"] = max(times) if times else None
    result["currency_verified"] = False  # Recording now cannot make an old statement current.
    result["is_current"] = record["state"] == "active"
    return result


def execute(gateway, operation, arguments):
    write = operation in {"create", "revise", "retire"}
    gateway._require_capability("write" if write else "read")
    store = gateway.memory_store
    if store is None:
        raise GatewayError("STORAGE_UNAVAILABLE", "Memory is not configured")
    args = dict(arguments)
    if write:
        status = args.get("epistemic_status", "unverified")
        note = args.get("verification_note")
        SQLiteMemoryStore._assessment(status, note)
        refs = _references(args["source_refs"])
        if status == "verified":
            resolution = resolve_sources(gateway, refs)
            if not all(r["status"] == "resolved" for r in resolution):
                raise GatewayError("RESOURCE_NOT_FOUND", "Memory verification sources are unavailable")
        args["identity"] = ReportedIdentity.from_value(args.pop("provenance", None))
    if operation in {"revise", "retire"}:
        result = store.revise(**args, retire=operation == "retire")
    else:
        result = getattr(store, operation)(**args)
    # A write-only caller receives its own assertion receipt, never source contents.
    if "read" in gateway.capabilities or "admin" in gateway.capabilities:
        if "memory" in result:
            result["memory"] = decorate(gateway, result["memory"])
            latest = store.fetch(result["memory"]["memory_id"])["memory"]
            result["memory"]["is_current"] = result["memory"]["state"] == "active" and latest["version"] == result["memory"]["version"]
        for key in ("memories", "versions"):
            if key in result:
                latest_version = store.fetch(args["memory_id"])["memory"]["version"] if key == "versions" else None
                result[key] = [decorate(gateway, r) for r in result[key]]
                if key == "versions":
                    for r in result[key]:
                        r["is_current"] = r["state"] == "active" and r["version"] == latest_version
    elif write:
        receipt = result["memory"]
        fields = ("memory_id", "version", "subject", "predicate", "value", "state", "source_refs", "epistemic_status", "verification_note")
        if operation != "create":
            fields = tuple(k for k in fields if k not in {"subject", "predicate"})
        result["memory"] = {k: receipt[k] for k in fields}
        result["receipt_only"] = True
    return result
