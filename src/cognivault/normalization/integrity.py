"""Whole derived-store verification inside Gateway, without exposing payloads."""
from contextlib import closing
import hashlib
import json
from .contracts import VERSION, encoded, identity
from ..adapters.canonical_history import TABLES
from ..provenance import get_provenance
from ..contracts import GatewayError


def verify(gateway, *, reparse=False):
    gateway._require_capability("read")
    if type(reparse) is not bool: raise GatewayError("INVALID_ARGUMENT","Invalid canonical verification arguments")
    canonical=gateway._canonical_history_store(); store=gateway._source_evidence_store()
    digest=hashlib.sha256(); errors=set(); messages=0; outcomes=0
    expected={t:set() for t in TABLES}; expected_provenance=set()
    with closing(store.history._connect()) as c:
        c.execute("BEGIN")
        if not canonical.exists(c):
            return {"verified":False,"error_classes":["normalization_not_started"],"messages_verified":0,"source_outcomes_verified":0,"canonical_sha256":digest.hexdigest()}
        for table in TABLES:
            for row in c.execute(f"SELECT * FROM {table} ORDER BY 1,2"):
                digest.update(encoded([table,[{"sha256":hashlib.sha256(v).hexdigest(),"bytes":len(v)} if type(v) is bytes else v for v in row]]).encode()+b"\n")
        for row in c.execute("SELECT * FROM write_provenance WHERE record_type IN ('canonical_conversation','canonical_message','canonical_view','normalization_outcome') ORDER BY record_type,record_id,version"):
            digest.update(encoded(["provenance",list(row)]).encode()+b"\n")
        for row in c.execute("SELECT * FROM p13_messages"):
            m=json.loads(row["payload"]); messages+=1
            if json.loads(row["metadata"])!={k:m[k] for k in ("message_id","conversation_id","role","occurred_at","time_quality","confidence","model")}:
                errors.add("canonical_metadata_mismatch")
            if hashlib.sha256(row["payload"]).hexdigest()!=row["content_sha256"] or len(row["payload"])!=row["content_bytes"]:
                errors.add("canonical_payload_mismatch")
            payload={k:m[k] for k in ("role","parts","occurred_at","model","assertions")}
            payload["raw_time"]=m["raw_timestamp"]
            want=identity("message",m["conversation_id"],m["native_key"],payload)
            p=get_provenance(c,"canonical_message",row["message_id"],1)
            if want!=row["message_id"] or row["role"]!=m["role"] or row["text"]!=m["text"] or row["conversation_id"]!=m["conversation_id"]:
                errors.add("canonical_identity_mismatch")
            if p is None or p["data_origin"]!="deterministic_derived" or p["original_created_at"]!=m["occurred_at"]:
                errors.add("canonical_provenance_mismatch")
            refs=[r[0] for r in c.execute("SELECT DISTINCT v.source_id FROM p13_views v JOIN p13_message_evidence e ON e.view_id=v.view_id WHERE e.message_id=?",(row["message_id"],))]
            if not refs or p is not None and not set(p["source_refs"])<=set(refs): errors.add("missing_message_evidence")
            if m["confidence"] not in {"exact","derived-safe"} or (m["confidence"]=="exact" and (m["time_quality"]!="reliable" or m["occurred_at"] is None)):
                errors.add("confidence_mismatch")
        for row in c.execute("SELECT * FROM p13_normalization_outcomes WHERE version=?",(VERSION,)):
            outcomes+=1; sid=row["source_id"]; value=json.loads(row["payload"])
            count=c.execute("SELECT COUNT(*) FROM p13_message_evidence e JOIN p13_views v ON v.view_id=e.view_id WHERE v.source_id=? AND v.version=?",(sid,VERSION)).fetchone()[0]
            if value["message_appearances"]!=count: errors.add("outcome_accounting_mismatch")
            if get_provenance(c,"normalization_outcome",identity("normalization",sid,VERSION),1) is None: errors.add("outcome_provenance_missing")
            if reparse:
                from .execution import candidate_result
                parsed=candidate_result(gateway,store,store.metadata(sid))
                if hashlib.sha256(encoded(parsed).encode()).hexdigest()!=row["result_sha256"]: errors.add("transformation_reparse_mismatch")
                if not materialization_matches(c,store.metadata(sid),parsed,value): errors.add("canonical_materialization_mismatch")
                expected["p13_normalization_outcomes"].add((sid,VERSION))
                expected_provenance.add(("normalization_outcome",identity("normalization",sid,VERSION),1))
                for v in parsed["views"]:
                    cid=v["conversation_id"]; vid=identity("view",sid,VERSION,cid,v["locator"])
                    expected["p13_conversations"].add((cid,)); expected["p13_views"].add((vid,))
                    expected_provenance.update({("canonical_conversation",cid,1),("canonical_view",vid,1)})
                    for pos in range(len(v["nodes"])): expected["p13_view_nodes"].add((vid,pos))
                    for m in v["messages"]:
                        expected["p13_messages"].add((m["message_id"],)); expected_provenance.add(("canonical_message",m["message_id"],1))
                        expected["p13_message_evidence"].add((vid,m["message_id"],m["position"]))
        for row in c.execute("SELECT e.payload,v.source_id FROM p13_message_evidence e JOIN p13_views v ON v.view_id=e.view_id"):
            e=json.loads(row[0]); sid=row[1]
            source=c.execute("SELECT imported_at FROM history_source_files WHERE source_id=?",(sid,)).fetchone()
            if source is None or e["source_id"]!=sid or e["source_fingerprint"]!=sid.split(":",1)[1] or e["source_imported_at"]!=source[0] or e["version"]!=VERSION:
                errors.add("source_evidence_mismatch")
        if outcomes!=canonical.snapshot_at(c)["source_count"]: errors.add("unexplained_source_set")
        if reparse:
            columns={"p13_conversations":"conversation_id","p13_messages":"message_id","p13_views":"view_id",
                     "p13_message_evidence":"view_id,message_id,position","p13_normalization_outcomes":"source_id,version","p13_view_nodes":"view_id,position"}
            for table,keys in columns.items():
                if {tuple(r) for r in c.execute(f"SELECT {keys} FROM {table}")}!=expected[table]: errors.add("unexpected_canonical_record_set")
            actual_provenance={tuple(r) for r in c.execute("SELECT record_type,record_id,version FROM write_provenance WHERE record_type IN ('canonical_conversation','canonical_message','canonical_view','normalization_outcome')")}
            if actual_provenance!=expected_provenance: errors.add("canonical_provenance_record_set_mismatch")
        if not provenance_relations_match(c): errors.add("canonical_provenance_relation_mismatch")
    return {"verified":not errors,"error_classes":sorted(errors),"messages_verified":messages,"source_outcomes_verified":outcomes,
            "canonical_sha256":digest.hexdigest(),"reparse_verified":reparse and not errors}


def materialization_matches(c, source, parsed, outcome):
    """Compare parsed source evidence to actual derived rows, not just a stored digest."""
    sid=source["source_id"]
    if any(outcome.get(k)!=v for k,v in parsed.items() if k!="views"): return False
    expected_counts={"views":len(parsed["views"]),"message_appearances":sum(len(v["messages"]) for v in parsed["views"]),
                     "exact":sum(m["confidence"]=="exact" for v in parsed["views"] for m in v["messages"]),
                     "derived_safe":sum(m["confidence"]=="derived-safe" for v in parsed["views"] for m in v["messages"]),"new_views":len(parsed["views"])}
    for name,table,key,kind in (("new_conversations","p13_conversations","conversation_id","canonical_conversation"),("new_messages","p13_messages","message_id","canonical_message")):
        expected_counts[name]=sum(get_provenance(c,kind,r[0],1)["source_refs"]==[sid] for r in c.execute(f"SELECT {key} FROM {table} WHERE normalized_at=?",(outcome["normalized_at"],)) if get_provenance(c,kind,r[0],1) is not None)
    if any(outcome.get(k)!=v for k,v in expected_counts.items()): return False
    if outcome.get("source_id")!=sid or outcome.get("source_fingerprint")!=source["source_fingerprint"] or outcome.get("source_imported_at")!=source["imported_at"]: return False
    if c.execute("SELECT COUNT(*) FROM p13_views WHERE source_id=? AND version=?",(sid,VERSION)).fetchone()[0]!=len(parsed["views"]): return False
    for v in parsed["views"]:
        cid=v["conversation_id"]; vid=identity("view",sid,VERSION,cid,v["locator"])
        conv=c.execute("SELECT source_system,native_key_digest FROM p13_conversations WHERE conversation_id=?",(cid,)).fetchone()
        if conv is None or tuple(conv)!=(v["source_system"],v["native_key_digest"]): return False
        row=c.execute("SELECT * FROM p13_views WHERE view_id=?",(vid,)).fetchone()
        expected={k:value for k,value in v.items() if k not in {"messages","nodes"}}
        expected.update(method=parsed["method"],version=VERSION,source_fingerprint=source["source_fingerprint"],source_imported_at=source["imported_at"])
        if row is None or row["conversation_id"]!=cid or row["source_id"]!=sid or row["version"]!=VERSION or row["normalized_at"]!=outcome["normalized_at"] or json.loads(row["payload"])!=expected: return False
        nodes=[json.loads(n[0]) for n in c.execute("SELECT payload FROM p13_view_nodes WHERE view_id=? ORDER BY position",(vid,))]
        want_nodes=[dict({k:value for k,value in n.items() if k!="children"},children_count=len(n["children"])) for n in v["nodes"]]
        if nodes!=want_nodes: return False
        evidence=c.execute("SELECT message_id,position,payload FROM p13_message_evidence WHERE view_id=? ORDER BY position",(vid,)).fetchall()
        if len(evidence)!=len(v["messages"]): return False
        for e,m in zip(evidence,sorted(v["messages"],key=lambda m:m["position"])):
            if e["message_id"]!=m["message_id"] or e["position"]!=m["position"]: return False
            expected_e={"source_id":sid,"source_fingerprint":source["source_fingerprint"],"source_imported_at":source["imported_at"],
                        "locator":m["locator"],"parent_key":m["parent_key"],"confidence":m["confidence"],"time_quality":m["time_quality"],
                        "method":parsed["method"],"version":VERSION,"normalized_at":row["normalized_at"]}
            if json.loads(e["payload"])!=expected_e: return False
            core={k:value for k,value in m.items() if k not in {"locator","position","parent_key"}}
            stored=c.execute("SELECT content_sha256 FROM p13_messages WHERE message_id=?",(m["message_id"],)).fetchone()
            if stored is None or stored[0]!=hashlib.sha256(encoded(core).encode()).hexdigest(): return False
    return True


def provenance_relations_match(c):
    queries={
        "canonical_conversation":("SELECT conversation_id,source_system FROM p13_conversations","SELECT DISTINCT source_id FROM p13_views WHERE conversation_id=?"),
        "canonical_message":("SELECT m.message_id,c.source_system FROM p13_messages m JOIN p13_conversations c ON c.conversation_id=m.conversation_id","SELECT DISTINCT v.source_id FROM p13_views v JOIN p13_message_evidence e ON e.view_id=v.view_id WHERE e.message_id=?"),
        "canonical_view":("SELECT v.view_id,s.source_system FROM p13_views v JOIN history_source_files s ON s.source_id=v.source_id","SELECT source_id FROM p13_views WHERE view_id=?"),
        "normalization_outcome":("SELECT o.source_id,s.source_system FROM p13_normalization_outcomes o JOIN history_source_files s ON s.source_id=o.source_id",None)}
    for kind,(rows,refs_query) in queries.items():
        for key,system in c.execute(rows):
            pid=identity("normalization",key,VERSION) if kind=="normalization_outcome" else key
            p=get_provenance(c,kind,pid,1)
            refs={r[0] for r in c.execute(refs_query,(key,))} if refs_query else {key}
            if p is None or p["data_origin"]!="deterministic_derived" or p["actor_type"]!="system" or p["source_system"]!=system or p["identity_trust"]!="unavailable" or not p["source_refs"] or not set(p["source_refs"])<=refs: return False
            if kind=="canonical_conversation":
                first=c.execute("SELECT source_id,payload FROM p13_views WHERE conversation_id=? ORDER BY rowid LIMIT 1",(key,)).fetchone()
                if first is None or p["source_refs"]!=[first[0]] or p["original_created_at"]!=json.loads(first[1])["created_at"]: return False
            elif kind=="canonical_view":
                row=c.execute("SELECT source_id,payload FROM p13_views WHERE view_id=?",(key,)).fetchone()
                if p["source_refs"]!=[row[0]] or p["original_created_at"]!=json.loads(row[1])["created_at"]: return False
            elif kind=="normalization_outcome":
                if p["source_refs"]!=[key] or p["original_created_at"] is not None: return False
            else:
                first=c.execute("SELECT v.source_id FROM p13_message_evidence e JOIN p13_views v ON v.view_id=e.view_id WHERE e.message_id=? ORDER BY e.rowid LIMIT 1",(key,)).fetchone()
                if first is None or p["source_refs"]!=[first[0]]: return False
    return True
