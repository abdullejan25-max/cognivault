"""Narrow MCP contracts for derived history; no private paths are accepted."""
import hashlib
from mcp import types
from ..adapters.history import SQLiteHistoryBackend
from ..contracts import GatewayError
from ..normalization.contracts import encoded

SHAPES={"history_normalization_snapshot":(set(),set()),"canonical_history_summary":(set(),set()),
        "verify_canonical_history":({"reparse"},set()),
        "normalize_history_sources":({"source_set_sha256","cursor","limit","compact"},{"source_set_sha256"}),
        "search_canonical_conversations":({"source_system","query","offset","limit"},set()),
        "fetch_canonical_conversation":({"conversation_id","offset","limit","view_offset","view_limit","node_offset","node_limit"},{"conversation_id"}),
        "fetch_canonical_message":({"message_id","offset","length","evidence_offset","evidence_limit"},{"message_id"})}


def canonical_tools(gateway,read_only,write_only):
    if not isinstance(getattr(gateway,"history_backend",None),SQLiteHistoryBackend): return []
    string={"type":"string"}; offset={"type":"integer","minimum":0}; limit={"type":"integer","minimum":1,"maximum":100}
    definitions=[
        ("history_normalization_snapshot","Pin the immutable source set before deterministic normalization.",{}),
        ("canonical_history_summary","Count canonical conversations/messages/views and explained source-only outcomes.",{}),
        ("verify_canonical_history","Verify all canonical identities/provenance/evidence/counts. Optional exact-source reparse proves the deterministic transformation without returning private content.",{"reparse":{"type":"boolean"}}),
        ("search_canonical_conversations","Search shared canonical history by explicit source category or literal message text; no inference.",{"source_system":string,"query":string,"offset":offset,"limit":limit}),
        ("fetch_canonical_conversation","Read paginated source-specific sequence/tree views and nodes. Do not flatten branches. Message position is source-defined.",{"conversation_id":string,"offset":offset,"limit":limit,"view_offset":offset,"view_limit":limit,"node_offset":offset,"node_limit":limit}),
        ("fetch_canonical_message","Read a bounded canonical JSON byte range plus paginated evidence/provenance; unknown occurrence time remains null.",{"message_id":string,"offset":offset,"length":{"type":"integer","minimum":1,"maximum":65536},"evidence_offset":offset,"evidence_limit":limit}),
    ]
    if gateway.config.history_migration_inbox is not None:
        definitions.append(("normalize_history_sources","Deterministic derived history from acquired evidence only. Versioned identities, immutable source views, ambiguity guard, private receipts. Source set must match; rerun is idempotent.",
                            {"source_set_sha256":string,"cursor":offset,"limit":{"type":"integer","minimum":1,"maximum":64},"compact":{"type":"boolean"}}))
    return [types.Tool(name=n,description=d,inputSchema={"type":"object","properties":p,"required":list(SHAPES[n][1]),"additionalProperties":False},
                       annotations=write_only if n=="normalize_history_sources" else read_only) for n,d,p in definitions]


def call_canonical_tool(gateway,name,arguments):
    if name not in SHAPES: return None
    allowed,required=SHAPES[name]
    if type(arguments) is not dict or set(arguments)-allowed or not required<=set(arguments) or any(v is None for v in arguments.values()):
        raise GatewayError("INVALID_ARGUMENT","Invalid canonical tool arguments")
    args=dict(arguments); compact=args.pop("compact",False)
    if type(compact) is not bool: raise GatewayError("INVALID_ARGUMENT","Invalid canonical tool arguments")
    result=getattr(gateway,name)(**args)
    if compact:
        result=dict(result,results_sha256=hashlib.sha256(encoded(result["results"]).encode()).hexdigest(),results=[r for r in result["results"] if r["state"]=="error"])
    return result
