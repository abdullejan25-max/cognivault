"""Original-History proposals, append-only review, explicitly authorized promotion.

Semantic interpretations and confidence are reported by the Agent. Neither a
matching quote nor a review establishes independent truth. Pending candidates
are separate from assertions and are never returned by Memory search.
"""
import base64
from datetime import datetime, timezone
from contextlib import closing
import json
import re
import sqlite3
import unicodedata
from uuid import uuid4

from .adapters.memory import _text, _digest
from .contracts import GatewayError
from .provenance import utc_now

CLASSES = {"fact_update", "preference_change", "historical_statement", "inference"}
CID = re.compile(r"candidate:[0-9a-f]{32}\Z")


def find(g, query, limit=5):
    """Bounded lexical triage; the Agent must interpret and quote originals."""
    store_for(g,"read")
    query = _text(query,200)
    if type(limit) is not int or not 1 <= limit <= 5: raise invalid()
    from .evidence import retrieve
    bundle = retrieve(g,{"history":query},limit)
    suggestions = []
    seen = set()
    for e in bundle["evidence"]:
        if e.get("role") != "user" or e["citation"] in seen: continue
        text = e["excerpt"].casefold()
        cues = [cue for cue in ("目标","打算","喜欢","不再","最近","goal","prefer","plan","chose") if cue in text]
        if not cues: continue
        seen.add(e["citation"])
        suggestions.append(dict(source_ref=e["citation"],excerpt=e["excerpt"],source_time=e.get("occurred_at"),
                                cues=cues,needs_original_review=True,confidence="unknown",state="suggestion"))
        if len(suggestions) == limit: break
    return {"suggestions":suggestions,"history_status":bundle["status"],"persisted":False,
            "extraction_basis":"bounded lexical cues; Agent interpretation required; untrusted source data"}


def invalid():
    return GatewayError("INVALID_ARGUMENT", "Invalid Memory candidate request")


def store_for(g, capability):
    g._require_capability(capability)
    g._require_capability("read")
    if g.memory_store is None:
        raise GatewayError("STORAGE_UNAVAILABLE", "Memory is not configured")
    return g.memory_store


def schema(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS memory_candidates(
            candidate_id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL,
            payload TEXT NOT NULL, proposed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_candidate_reviews(
            candidate_id TEXT PRIMARY KEY REFERENCES memory_candidates(candidate_id),
            decision TEXT NOT NULL, note TEXT NOT NULL, resolution TEXT NOT NULL,
            reviewed_at TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS candidates_no_update BEFORE UPDATE ON memory_candidates
            BEGIN SELECT RAISE(ABORT,'immutable candidate'); END;
        CREATE TRIGGER IF NOT EXISTS candidates_no_delete BEFORE DELETE ON memory_candidates
            BEGIN SELECT RAISE(ABORT,'immutable candidate'); END;
        CREATE TRIGGER IF NOT EXISTS candidate_reviews_no_update BEFORE UPDATE ON memory_candidate_reviews
            BEGIN SELECT RAISE(ABORT,'immutable review'); END;
        CREATE TRIGGER IF NOT EXISTS candidate_reviews_no_delete BEFORE DELETE ON memory_candidate_reviews
            BEGIN SELECT RAISE(ABORT,'immutable review'); END;
    """)


def original(g, ref):
    if type(ref) is not str:
        raise invalid()
    if re.fullmatch(r"history:[0-9a-f]{64}", ref):
        r = g.fetch_history_item(ref)["item"]
        return dict(text=r["content"], source_time=r["created_at"], role=r["role"],
                    content_sha256=r["content_sha256"])
    if re.fullmatch(r"message:[0-9a-f]{64}", ref):
        r = g.fetch_canonical_message(ref, length=65536, evidence_limit=1)
        if r["has_more"]:
            raise GatewayError("INVALID_ARGUMENT", "Candidate source exceeds bounded review size")
        return dict(text=base64.b64decode(r["content_base64"]).decode("utf-8"),
                    source_time=r["message"].get("occurred_at"), role=r["message"].get("role"),
                    content_sha256=r["content_sha256"])
    raise invalid()  # No derived Memory feedback as a candidate source.


def source_metadata(g, refs):
    """Fingerprint original metadata without storing another copy of the text."""
    from .memory_service import resolve_sources
    result = resolve_sources(g,refs)
    for r in result:
        if r["source_ref"].startswith(("history:","message:")) and r["status"] == "resolved":
            source = original(g,r["source_ref"])
            r.update(content_sha256=source["content_sha256"],role=source["role"],occurred_at=source["source_time"])
    return result


def validate_semantic_sources(g,c):
    for relation in c["relations"]:
        if "source_metadata" in relation:
            if source_metadata(g,relation["snapshot"]["source_refs"]) != relation["source_metadata"]:
                raise GatewayError("CONFLICT", "Competing original source metadata changed; propose again")


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def read(con, cid):
    if type(cid) is not str or CID.fullmatch(cid) is None:
        raise invalid()
    exists = con.execute("SELECT 1 FROM sqlite_master WHERE name='memory_candidates'").fetchone()
    row = con.execute("SELECT * FROM memory_candidates WHERE candidate_id=?", (cid,)).fetchone() if exists else None
    if row is None:
        raise GatewayError("RESOURCE_NOT_FOUND", "Memory candidate was not found")
    review = con.execute("SELECT * FROM memory_candidate_reviews WHERE candidate_id=?", (cid,)).fetchone()
    result = json.loads(row["payload"])
    result.update(candidate_id=cid, proposed_at=row["proposed_at"],
                  state="approved" if review and review["decision"] == "approve" else "rejected" if review else "pending",
                  review=dict(review) if review else None, interpretation_trust="reported")
    return result


def propose(g, source_ref, quote, subject, predicate, value, classification, confidence, basis, semantic_links=None, claim_mode="explicit", evidence_refs=None):
    s = store_for(g, "write")
    quote, subject, predicate, value, basis = (_text(quote, 2000), _text(subject, 200),
                                             _text(predicate, 120), _text(value, 4000), _text(basis, 1000))
    if type(classification) is not str or classification not in CLASSES or type(confidence) not in {int, float} or not 0 <= confidence <= 1:
        raise invalid()
    if type(claim_mode) is not str or claim_mode not in {"explicit", "consideration", "decided"}: raise invalid()
    if evidence_refs is not None:
        from .adapters.memory import _references
        from .memory_service import resolve_sources
        evidence_refs = _references(evidence_refs)
        if len(evidence_refs) > 5 or not all(r["status"] == "resolved" for r in resolve_sources(g, evidence_refs)):
            raise GatewayError("INVALID_ARGUMENT", "Candidate supporting evidence is unavailable or too large")
    source = original(g, source_ref)
    if quote not in source["text"]:
        raise GatewayError("INVALID_ARGUMENT", "Candidate quote is absent from original History")
    relations = []
    stale_target = False
    matches = s.matching_slot(subject, predicate)
    # Bounded detection must never be represented as exhaustive semantic matching.
    for m in matches["memories"]:
        if normalized(m["subject"]) == normalized(subject) and normalized(m["predicate"]) == normalized(predicate):
            relations.append(dict(memory_id=m["memory_id"], version=m["version"],
                                  kind="duplicate" if normalized(m["value"]) == normalized(value) else "possible_conflict"))
    if semantic_links is not None:
        if type(semantic_links) is not list or not 1 <= len(semantic_links) <= 3:
            raise invalid()
        seen = set()
        for link in semantic_links:
            if type(link) is not dict or set(link) != {"memory_id", "expected_version", "relation", "reason"}:
                raise invalid()
            if type(link["relation"]) is not str or link["relation"] not in {"same_as", "contradicts", "supersedes"} or type(link["expected_version"]) is not int or link["expected_version"] < 1:
                raise invalid()
            reason = _text(link["reason"],1000)
            target = g.fetch_memory(link["memory_id"])["memory"]
            if target["memory_id"] in seen:
                raise GatewayError("CONFLICT", "Semantic target is repeated")
            if target["version"] != link["expected_version"] or target["state"] != "active":
                stale_target = True
                target = g.fetch_memory(link["memory_id"],version=link["expected_version"])["memory"]
            seen.add(target["memory_id"])
            relations = [r for r in relations if r["memory_id"] != target["memory_id"]]
            relations.append(dict(memory_id=target["memory_id"], version=target["version"],
                kind=link["relation"], reason=reason, snapshot=target, source_metadata=source_metadata(g,target["source_refs"]), interpretation_trust="reported"))
    payload = dict(source_ref=source_ref, quote=quote, source_time=source["source_time"],
                   source_role=source["role"], content_sha256=source["content_sha256"],
                   subject=subject, predicate=predicate, value=value, classification=classification,
                   confidence=confidence, basis=basis, relations=relations,
                   relation_detection="bounded_normalized_slot; semantic interpretation requires review",
                   relation_scan_truncated=matches["total"] > 20)
    if semantic_links is not None: payload["semantic_links"] = semantic_links
    if claim_mode != "explicit": payload["claim_mode"] = claim_mode
    if evidence_refs is not None: payload["evidence_refs"] = evidence_refs
    # Relations can change; the same proposed interpretation still keeps its initial snapshot.
    digest = _digest({k:v for k,v in payload.items() if k not in {"relations", "relation_scan_truncated"}})
    try:
        with closing(s._connect(write=True)) as con:
            s._schema(con); schema(con); con.commit()
            with con:
                con.execute("BEGIN IMMEDIATE")
                prior = con.execute("SELECT candidate_id FROM memory_candidates WHERE digest=?", (digest,)).fetchone()
                cid = prior[0] if prior else "candidate:" + uuid4().hex
                if not prior:
                    if stale_target: raise GatewayError("CONFLICT", "Semantic target changed; propose against its current version")
                    con.execute("INSERT INTO memory_candidates VALUES (?,?,?,?)", (cid, digest, json.dumps(payload, ensure_ascii=False), utc_now()))
                    s._audit(con, "propose_memory_candidate", cid)
                return {"candidate":read(con,cid), "reused":bool(prior)}
    except sqlite3.Error:
        raise GatewayError("STORAGE_UNAVAILABLE", "Memory candidate storage is unavailable") from None


def listing(g, limit=5, offset=0):
    s = store_for(g, "read")
    if type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or not 0 <= offset <= 1000:
        raise invalid()
    s._validate_path()
    if not s.database_path.exists(): return {"candidates":[], "total":0}
    try:
        with closing(s._connect()) as con:
            con.execute("BEGIN")
            if not con.execute("SELECT 1 FROM sqlite_master WHERE name='memory_candidates'").fetchone():
                return {"candidates":[], "total":0}
            total = con.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0]
            ids = con.execute("SELECT candidate_id FROM memory_candidates ORDER BY proposed_at DESC,candidate_id LIMIT ? OFFSET ?", (limit,offset)).fetchall()
            return {"candidates":[read(con,r[0]) for r in ids], "total":total}
    except sqlite3.Error:
        raise GatewayError("STORAGE_UNAVAILABLE", "Memory candidate storage is unavailable") from None


def fetching(g, candidate_id):
    """Read the immutable proposal alongside live, permission-checked targets."""
    s = store_for(g,"read")
    try:
        with closing(s._connect()) as con:
            con.execute("BEGIN")
            c = read(con,candidate_id)
            pinned = [(r,s._record(con,r["memory_id"])) for r in c["relations"]]
        from .memory_service import decorate
        targets = []
        for r,record in pinned:
            if record is None:
                targets.append(dict(relation=r,unavailable="RESOURCE_NOT_FOUND",changed=True))
            else:
                current = decorate(g,record)
                targets.append(dict(relation=r,current=current,changed=current["version"] != r["version"]))
        return {"candidate":c,"competing_memories":targets,"content_trust":"untrusted_data",
                "currency_verified":False,"snapshot_scope":"Memory versions pinned; original domains independently resolved", "review_instruction":"Compare original quote, source times, competing sources and relation reasons; reported confidence is not independent truth."}
    except sqlite3.Error:
        raise GatewayError("STORAGE_UNAVAILABLE", "Memory candidate storage is unavailable") from None


def review(g, candidate_id, decision, note, resolution="none"):
    s = store_for(g, "admin")  # A configured reviewer capability, never a reported Agent name.
    note = _text(note,1000)
    if type(decision) is not str or type(resolution) is not str or decision not in {"approve", "reject"} or resolution not in {"none", "confirmed_update"}:
        raise invalid()
    try:
        with closing(s._connect(write=True)) as con:
            with con:
                con.execute("BEGIN IMMEDIATE")
                c = read(con,candidate_id)
                if c["review"]:
                    if (c["review"]["decision"],c["review"]["note"],c["review"]["resolution"]) != (decision,note,resolution):
                        raise GatewayError("CONFLICT", "Candidate review is already final")
                    return {"candidate":c,"reused":True}
                if decision == "approve":
                    validate_semantic_sources(g,c)
                    fresh = original(g,c["source_ref"])
                    if (fresh["content_sha256"],fresh["role"],fresh["source_time"]) != (c["content_sha256"],c["source_role"],c["source_time"]):
                        raise GatewayError("CONFLICT", "Candidate source metadata changed")
                    if c.get("claim_mode") == "consideration":
                        raise GatewayError("CONFLICT", "A considered choice cannot become a decided current fact")
                    if c["classification"] in {"historical_statement", "inference"} or c["source_role"] != "user" or not c["source_time"] or c["relation_scan_truncated"] or len(c["relations"]) > 1:
                        raise GatewayError("CONFLICT", "Candidate needs clarification before current-fact promotion")
                    if any(r["kind"] in {"possible_conflict", "same_as", "contradicts", "supersedes"} for r in c["relations"]) and resolution != "confirmed_update":
                        raise GatewayError("CONFLICT", "Candidate conflict needs explicit confirmation")
                    for r in c["relations"]:
                        if r["kind"] == "same_as" and len(set(r["snapshot"]["source_refs"]+[c["source_ref"]]+c.get("evidence_refs",[]))) > 16:
                            raise GatewayError("CONFLICT", "Equivalent fact exceeds bounded source capacity; clarify evidence")
                        if "snapshot" in r and (not r["snapshot"].get("sources_resolved") or r["snapshot"].get("effective_epistemic_status") == "inference"):
                            raise GatewayError("CONFLICT", "Semantic target needs resolved non-inference evidence")
                        if r["kind"] == "supersedes":
                            times = [x.get("occurred_at") for x in r["snapshot"].get("source_resolution", [])]
                            try:
                                dates = [datetime.fromisoformat(t.replace("Z", "+00:00")) for t in times if t]
                                if not dates or any(d.tzinfo is None for d in dates): raise ValueError()
                                old = max(d.astimezone(timezone.utc) for d in dates)
                                new = datetime.fromisoformat(c["source_time"].replace("Z", "+00:00"))
                                if new.tzinfo is None or not old < new <= datetime.now(timezone.utc): raise ValueError()
                            except (ValueError, TypeError):
                                raise GatewayError("CONFLICT", "Supersession needs a strictly later dated original source") from None
                con.execute("INSERT INTO memory_candidate_reviews VALUES (?,?,?,?,?)", (candidate_id,decision,note,resolution,utc_now()))
                s._audit(con,"review_memory_candidate",candidate_id)
                return {"candidate":read(con,candidate_id),"reused":False}
    except sqlite3.Error:
        raise GatewayError("STORAGE_UNAVAILABLE", "Memory candidate storage is unavailable") from None


def commit(g, candidate_id):
    s = store_for(g,"write")
    with closing(s._connect()) as con:
        c = read(con,candidate_id)
    if c["state"] != "approved":
        raise GatewayError("CONFLICT", "Candidate has no approved review")
    validate_semantic_sources(g,c)
    source = original(g,c["source_ref"])
    if source["content_sha256"] != c["content_sha256"] or c["quote"] not in source["text"] or source["role"] != c["source_role"] or source["source_time"] != c["source_time"]:
        raise GatewayError("CONFLICT", "Candidate source has changed")
    args = dict(value=c["value"],source_refs=sorted(set([c["source_ref"]]+c.get("evidence_refs",[]))),idempotency_key="candidate-"+candidate_id[10:],
                epistemic_status="verified",verification_note=c["review"]["note"])
    if c["relations"]:
        relation = c["relations"][0]
        if relation["kind"] == "same_as":
            args["value"] = relation["snapshot"]["value"]
            args["source_refs"] = sorted(set(relation["snapshot"]["source_refs"] + args["source_refs"]))
        # Use the store's immutable request receipt even for duplicate values.
        # A newly reviewed source/assessment is evidence, not a new invented fact.
        return g.revise_memory(relation["memory_id"],expected_version=relation["version"],**args)
    return g.create_memory(c["subject"],c["predicate"],**args)
