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


def memory_tools(gateway, read_only, write_only):
    if gateway.memory_store is None:
        return []
    return [
        types.Tool(name="create_memory", description="Explicitly record a fact with claimed source references. Source existence is NOT verified; never infer facts automatically.",
                   inputSchema={"type": "object", "properties": {
                       "subject": {"type": "string", "minLength": 1, "maxLength": 200},
                       "predicate": {"type": "string", "minLength": 1, "maxLength": 120},
                       "value": TEXT, "source_refs": REFS, "idempotency_key": KEY},
                       "required": ["subject", "predicate", "value", "source_refs", "idempotency_key"],
                       "additionalProperties": False}, annotations=write_only),
        types.Tool(name="revise_memory", description="Append a corrected fact version. Requires optimistic expected_version.",
                   inputSchema={"type": "object", "properties": {
                       "memory_id": {"type": "string", "pattern": _ID}, "value": TEXT,
                       "source_refs": REFS, "expected_version": {"type": "integer", "minimum": 1},
                       "idempotency_key": KEY},
                       "required": ["memory_id", "value", "source_refs", "expected_version", "idempotency_key"],
                       "additionalProperties": False}, annotations=write_only),
        types.Tool(name="retire_memory", description="Invalidate a fact without erasing earlier evidence. Value is the retirement reason.",
                   inputSchema={"type": "object", "properties": {
                       "memory_id": {"type": "string", "pattern": _ID}, "value": TEXT,
                       "source_refs": REFS, "expected_version": {"type": "integer", "minimum": 1},
                       "idempotency_key": KEY},
                       "required": ["memory_id", "value", "source_refs", "expected_version", "idempotency_key"],
                       "additionalProperties": False}, annotations=write_only),
        types.Tool(name="fetch_memory", description="Read latest fact version and evidence status.",
                   inputSchema={"type": "object", "properties": {
                       "memory_id": {"type": "string", "pattern": _ID}},
                       "required": ["memory_id"], "additionalProperties": False}, annotations=read_only),
        types.Tool(name="search_memory", description="Search active durable facts by literal substring; evidence references are unverified.",
                   inputSchema={"type": "object", "properties": {
                       "query": {"type": "string", "minLength": 1, "maxLength": 500},
                       "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                       "offset": {"type": "integer", "minimum": 0, "maximum": 1000}},
                       "required": ["query"], "additionalProperties": False}, annotations=read_only),
    ]


def call_memory_tool(gateway, name, arguments):
    if name not in {"create_memory", "revise_memory", "retire_memory", "fetch_memory", "search_memory"}:
        return None
    if gateway.memory_store is None:
        raise GatewayError("STORAGE_UNAVAILABLE", "Memory is not configured")
    if type(arguments) is not dict:
        raise GatewayError("INVALID_ARGUMENT", "Invalid Memory tool request")
    required = {
        "create_memory": {"subject", "predicate", "value", "source_refs", "idempotency_key"},
        "revise_memory": {"memory_id", "value", "source_refs", "expected_version", "idempotency_key"},
        "retire_memory": {"memory_id", "value", "source_refs", "expected_version", "idempotency_key"},
        "fetch_memory": {"memory_id"},
        "search_memory": {"query"},
    }[name]
    allowed = required | ({"limit", "offset"} if name == "search_memory" else set())
    if not required <= set(arguments) or set(arguments) - allowed:
        raise GatewayError("INVALID_ARGUMENT", "Invalid Memory tool request")
    if name in {"create_memory", "revise_memory", "retire_memory"}:
        gateway._require_capability("write")
    else:
        gateway._require_capability("read")
    if name == "create_memory":
        return gateway.memory_store.create(**arguments)
    if name in {"revise_memory", "retire_memory"}:
        return gateway.memory_store.revise(
            arguments["memory_id"], arguments["value"], arguments["source_refs"],
            arguments["expected_version"], arguments["idempotency_key"],
            retire=name == "retire_memory")
    if name == "fetch_memory":
        return gateway.memory_store.fetch(**arguments)
    return gateway.memory_store.search(**arguments)
