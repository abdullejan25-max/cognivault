"""Strict MCP contracts for opt-in durable Memory."""
from mcp import types
from ..contracts import GatewayError

_REF = r"^(?:history:[0-9a-f]{64}|message:[0-9a-f]{64}|(?:document|wrong-answer)://sha256/[0-9a-f]{64})$"
_ID = r"^memory:[0-9a-f]{32}$"
_KEY = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$"
REFS = {"type": "array", "minItems": 1, "maxItems": 16, "uniqueItems": True,
        "items": {"type": "string", "pattern": _REF}}
TEXT = {"type": "string", "minLength": 1, "maxLength": 4000}
KEY = {"type": "string", "pattern": _KEY}
GUARD = {"type": "object", "properties": {
    "memory_id": {"type": "string", "pattern": _ID},
    "subject": {"type": "string", "minLength": 1, "maxLength": 200},
    "predicate": {"type": "string", "minLength": 1, "maxLength": 120},
    "version": {"type": "integer", "minimum": 1, "maximum": 1000000},
    "record_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
    "required": ["memory_id", "subject", "predicate", "version", "record_sha256"],
    "additionalProperties": False}

ASSESSMENT = {
    "epistemic_status": {"type":"string", "enum":["unverified","verified","inference"]},
    "verification_note": {"type":"string", "minLength":1, "maxLength":1000},
    "provenance": {"type":"object", "properties": {k:{"type":"string", "minLength":1, "maxLength":120} for k in ("reported_agent","reported_client","run_id")}, "additionalProperties":False},
}


def memory_tools(gateway, read_only, write_only):
    if getattr(gateway, "memory_store", None) is None:
        return []
    return [
        types.Tool(name="create_memory", description="Record an explicit versioned assertion. Verified requires reported review notes and accessible sources; existence never proves truth.",
                   inputSchema={"type": "object", "properties": {
                       "subject": {"type": "string", "minLength": 1, "maxLength": 200},
                       "predicate": {"type": "string", "minLength": 1, "maxLength": 120},
                       "value": TEXT, "source_refs": REFS, "idempotency_key": KEY, **ASSESSMENT},
                       "required": ["subject", "predicate", "value", "source_refs", "idempotency_key"],
                       "additionalProperties": False}, annotations=write_only),
        types.Tool(name="revise_memory", description="Append a corrected version bound to fetch_memory.target_guard and expected_version. Confirm the full intended ID and subject/predicate; never select a write target from source search or guess an incomplete ID. The guard does not authorize a write.",
                   inputSchema={"type": "object", "properties": {
                       "memory_id": {"type": "string", "pattern": _ID}, "value": TEXT,
                       "source_refs": REFS, "expected_version": {"type": "integer", "minimum": 1, "maximum": 1000000},
                       "idempotency_key": KEY, "target_guard": GUARD, **ASSESSMENT},
                       "required": ["memory_id", "value", "source_refs", "expected_version", "idempotency_key", "target_guard"],
                       "additionalProperties": False}, annotations=write_only),
        types.Tool(name="retire_memory", description="Invalidate an explicitly confirmed full ID using its fetched target_guard. Preserve earlier evidence; value is the retirement reason.",
                   inputSchema={"type": "object", "properties": {
                       "memory_id": {"type": "string", "pattern": _ID}, "value": TEXT,
                       "source_refs": REFS, "expected_version": {"type": "integer", "minimum": 1, "maximum": 1000000},
                       "idempotency_key": KEY, "target_guard": GUARD, "provenance": ASSESSMENT["provenance"]},
                       "required": ["memory_id", "value", "source_refs", "expected_version", "idempotency_key", "target_guard"],
                       "additionalProperties": False}, annotations=write_only),
        types.Tool(name="fetch_memory", description="Read a fact version, evidence status and target_guard. Copy the guard unchanged for an explicitly authorized revision; historical guards cannot authorize a new current write.",
                   inputSchema={"type": "object", "properties": {
                       "memory_id": {"type": "string", "pattern": _ID}, "version": {"type":"integer", "minimum":1, "maximum":1000000}},
                       "required": ["memory_id"], "additionalProperties": False}, annotations=read_only),
        types.Tool(name="search_memory", description="Search current assertions by subject, predicate, value or source reference; returns live source resolution and reported epistemic status.",
                   inputSchema={"type": "object", "properties": {
                       "query": {"type": "string", "minLength": 1, "maxLength": 500},
                       "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                       "offset": {"type": "integer", "minimum": 0, "maximum": 1000}},
                       "required": ["query"], "additionalProperties": False}, annotations=read_only),
        types.Tool(name="memory_versions", description="Read bounded historical assertions; superseded versions are not current facts.", inputSchema={"type":"object", "properties":{"memory_id":{"type":"string","pattern":_ID}, "limit":{"type":"integer","minimum":1,"maximum":20}, "offset":{"type":"integer","minimum":0,"maximum":1000}}, "required":["memory_id"], "additionalProperties":False}, annotations=read_only),
        types.Tool(name="fetch_memory_request", description="Read the committed idempotency-key binding and original version receipt without replaying a write. Requires read capability; a key is not an authorization credential.", inputSchema={"type":"object", "properties":{"idempotency_key":KEY}, "required":["idempotency_key"], "additionalProperties":False}, annotations=read_only),
    ]


def call_memory_tool(gateway, name, arguments):
    if name not in {"create_memory", "revise_memory", "retire_memory", "fetch_memory", "search_memory", "memory_versions", "fetch_memory_request"}:
        return None
    if getattr(gateway, "memory_store", None) is None:
        raise GatewayError("STORAGE_UNAVAILABLE", "Memory is not configured")
    if type(arguments) is not dict:
        raise GatewayError("INVALID_ARGUMENT", "Invalid Memory tool request")
    required = {
        "create_memory": {"subject", "predicate", "value", "source_refs", "idempotency_key"},
        "revise_memory": {"memory_id", "value", "source_refs", "expected_version", "idempotency_key", "target_guard"},
        "retire_memory": {"memory_id", "value", "source_refs", "expected_version", "idempotency_key", "target_guard"},
        "fetch_memory": {"memory_id"},
        "search_memory": {"query"},
        "memory_versions": {"memory_id"},
        "fetch_memory_request": {"idempotency_key"},
    }[name]
    optional = {
        "create_memory": set(ASSESSMENT), "revise_memory": set(ASSESSMENT),
        "retire_memory": {"provenance"}, "fetch_memory": {"version"},
        "search_memory": {"limit", "offset"}, "memory_versions": {"limit", "offset"},
        "fetch_memory_request": set(),
    }[name]
    if not required <= set(arguments) or set(arguments) - required - optional or ("version" in arguments and arguments["version"] is None):
        raise GatewayError("INVALID_ARGUMENT", "Invalid Memory tool request")
    if any(k in arguments and arguments[k] is None for k in ("verification_note", "provenance")):
        raise GatewayError("INVALID_ARGUMENT", "Invalid Memory tool request")
    return getattr(gateway, name)(**arguments)
