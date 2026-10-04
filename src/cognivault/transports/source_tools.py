"""Narrow MCP contracts for additive source-only History operations."""
import hashlib
import json
from mcp import types

from ..adapters.history import SQLiteHistoryBackend
from ..contracts import GatewayError


def source_tools(gateway,read_only,write_only,provenance):
    if not isinstance(getattr(gateway,"history_backend",None),SQLiteHistoryBackend): return []
    string={"type":"string"}
    identity={"type":"string","pattern":r"^source-file:[0-9a-f]{64}$"}
    def tool(name,description,properties,required=(),write=False):
        return types.Tool(name=name,description=description,inputSchema={"type":"object","properties":properties,
            "required":list(required),"additionalProperties":False},annotations=write_only if write else read_only)
    result=[
        tool("history_source_summary","Count immutable source evidence; source counts are distinct from canonical messages.",{}),
        tool("search_history_sources","Search deterministic source metadata by category or source identity. No role or conversation inference.",
             {"source_system":string,"query":{"type":"string","maxLength":500},"limit":{"type":"integer","minimum":1,"maximum":100},"offset":{"type":"integer","minimum":0}}),
        tool("fetch_history_source","Read up to 64 KiB of exact original source bytes, with provenance. Does not normalize.",
             {"source_id":identity,"offset":{"type":"integer","minimum":0},"length":{"type":"integer","minimum":1,"maximum":65536}},["source_id"]),
        tool("verify_history_source","Gateway verifies full source checksum and provenance without returning source content.",
             {"source_id":identity},["source_id"]),
    ]
    if gateway.config.history_migration_inbox is not None:
        result.append(tool("ingest_history_sources","Source-only ingestion from an explicitly configured private inbox. Exact hash/byte validation, existing-evidence reuse, deterministic identities, per-entry results and durable private receipt. Never generates canonical messages.",
             {"relative_manifest":{"type":"string","minLength":1,"maxLength":500},"expected_manifest_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
              "cursor":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":1,"maximum":128},"provenance":provenance,
              "compact":{"type":"boolean","default":False,"description":"Return aggregate counts, error entries and checksum; full per-entry evidence remains in the durable private receipt."}},
             ["relative_manifest","expected_manifest_sha256"],True))
    return result


def call_source_tool(gateway,name,arguments):
    names={"history_source_summary","search_history_sources","fetch_history_source","verify_history_source","ingest_history_sources"}
    if name not in names: return None
    shapes={"history_source_summary":(set(),set()),
            "search_history_sources":({"source_system","query","limit","offset"},set()),
            "fetch_history_source":({"source_id","offset","length"},{"source_id"}),
            "verify_history_source":({"source_id"},{"source_id"}),
            "ingest_history_sources":({"relative_manifest","expected_manifest_sha256","cursor","limit","provenance","compact"},{"relative_manifest","expected_manifest_sha256"})}
    allowed,required=shapes[name]
    if type(arguments) is not dict or set(arguments)-allowed or not required<=set(arguments) or any(v is None for v in arguments.values()):
        raise GatewayError("INVALID_ARGUMENT","Invalid source tool arguments")
    values=dict(arguments)
    compact=values.pop("compact",False) if name=="ingest_history_sources" else False
    if type(compact) is not bool: raise GatewayError("INVALID_ARGUMENT","Invalid source tool arguments")
    result=getattr(gateway,name)(**values)
    if compact:
        result={**result,"results_sha256":hashlib.sha256(json.dumps(result["results"],sort_keys=True,separators=(",",":")).encode()).hexdigest(),
                "results":[r for r in result["results"] if r["disposition"]=="error"]}
    return result
