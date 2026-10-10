"""Candidate tools expose configured reviewer authority separately from writes."""
from mcp import types
from ..contracts import GatewayError

STRING = {"type":"string", "minLength":1, "maxLength":1000}
ID = {"type":"string", "pattern":r"^candidate:[0-9a-f]{32}$"}
CONTRACTS = {
    "find_memory_candidates": ({"query":{**STRING,"maxLength":200},"limit":{"type":"integer","minimum":1,"maximum":5}}, ["query"],
        "Read-only bounded History triage for potential goals/preferences/changes. Suggestions require Agent interpretation and original review; no automatic Memory write."),
    "propose_memory_candidate": ({
        "source_ref":{"type":"string","pattern":r"^(?:history|message):[0-9a-f]{64}$"},
        "quote":{**STRING,"maxLength":2000}, "subject":{**STRING,"maxLength":200},
        "predicate":{**STRING,"maxLength":120}, "value":{**STRING,"maxLength":4000},
        "classification":{"type":"string","enum":["fact_update","preference_change","historical_statement","inference"]},
        "confidence":{"type":"number","minimum":0,"maximum":1}, "basis":STRING},
        ["source_ref","quote","subject","predicate","value","classification","confidence","basis"],
        "Propose a pending interpretation of an exact original History quote. Confidence is reported, not verified. Candidates are excluded from Memory retrieval."),
    "list_memory_candidates": ({"limit":{"type":"integer","minimum":1,"maximum":20},"offset":{"type":"integer","minimum":0,"maximum":1000}}, [],
        "Read bounded candidate proposals and immutable reviews, including conflicts and original evidence."),
    "review_memory_candidate": ({"candidate_id":ID,"decision":{"type":"string","enum":["approve","reject"]},"note":STRING,
        "resolution":{"type":"string","enum":["none","confirmed_update"]}},["candidate_id","decision","note"],
        "Admin reviewer only: approve or reject a proposal after source/temporal/semantic review. Approval is final; conflicts require confirmed_update. Historical statements and inference cannot become current facts."),
    "commit_memory_candidate": ({"candidate_id":ID},["candidate_id"],
        "Promote an approved proposal with read+write permission. Revalidates source and target version. Replay is idempotent; no automatic approval.")}


def candidate_tools(gateway, read_only, write_only):
    if getattr(gateway,"memory_store",None) is None: return []
    capabilities = gateway.capabilities
    result = []
    for name,(properties,required,description) in CONTRACTS.items():
        read = name in {"list_memory_candidates","find_memory_candidates"}
        needed = {"read"} | ({"admin"} if name == "review_memory_candidate" else {"write"} if not read else set())
        if "admin" not in capabilities and not needed <= capabilities: continue
        result.append(types.Tool(name=name,description=description,inputSchema={"type":"object","properties":properties,"required":required,"additionalProperties":False},annotations=read_only if read else write_only))
    return result


def call_candidate_tool(gateway, name, arguments):
    if name not in CONTRACTS: return None
    properties,required,_ = CONTRACTS[name]
    if type(arguments) is not dict or not set(required) <= set(arguments) or set(arguments)-set(properties):
        raise GatewayError("INVALID_ARGUMENT","Invalid Memory candidate tool request")
    return getattr(gateway,name)(**arguments)
