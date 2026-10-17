"""Bounded, read-only structural verification for native Memory storage."""
from contextlib import closing
import json
import re
import sqlite3

from ..contracts import GatewayError
from ..provenance import ReportedIdentity
from .memory import MEMORY_ID, REQUEST_KEY, RECORD_HASH, _text, _references, _digest

def _schema_matches(con, store, candidates):
    """Compare constraints and trigger bodies, not only their names."""
    from ..memory_candidates import schema
    with closing(sqlite3.connect(":memory:")) as expected:
        store._schema(expected)
        if candidates:
            schema(expected)
        def definitions(connection):
            return {(row[0], row[1]): re.sub(r"\s+", " ", row[2]).strip().casefold()
                    for row in connection.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL LIMIT 10001")}
        return definitions(con) == definitions(expected)


def _candidate_payload(con, store, row):
    from ..memory_candidates import CID, CLASSES
    if type(row["candidate_id"]) is not str or not CID.fullmatch(row["candidate_id"]):
        raise ValueError()
    p = json.loads(row["payload"])
    required = {"source_ref", "quote", "source_time", "source_role", "content_sha256", "subject", "predicate", "value", "classification", "confidence", "basis", "relations", "relation_detection", "relation_scan_truncated"}
    if type(p) is not dict or not required <= set(p) or set(p) - required - {"claim_mode", "semantic_links", "evidence_refs"}:
        raise ValueError()
    if row["digest"] != _digest({k: v for k, v in p.items() if k not in {"relations", "relation_scan_truncated"}}):
        raise ValueError()
    if not re.fullmatch(r"(?:history|message):[0-9a-f]{64}", p["source_ref"]):
        raise ValueError()
    for name, maximum in (("quote", 2000), ("subject", 200), ("predicate", 120), ("value", 4000), ("basis", 1000), ("source_role", 100)):
        _text(p[name], maximum)
    _text(row["proposed_at"], 100)
    if p["source_time"] is not None:
        _text(p["source_time"], 100)
    if not RECORD_HASH.fullmatch(p["content_sha256"]) or p["classification"] not in CLASSES \
            or type(p["confidence"]) not in {int, float} or not 0 <= p["confidence"] <= 1 \
            or p.get("claim_mode", "explicit") not in {"explicit", "consideration", "decided"} \
            or type(p["relation_scan_truncated"]) is not bool \
            or p["relation_detection"] != "bounded_normalized_slot; semantic interpretation requires review":
        raise ValueError()
    if "evidence_refs" in p and len(_references(p["evidence_refs"])) > 5:
        raise ValueError()
    if type(p["relations"]) is not list or len(p["relations"]) > 23:
        raise ValueError()
    seen = set()
    for r in p["relations"]:
        if type(r) is not dict or r["kind"] not in {"duplicate", "possible_conflict", "same_as", "contradicts", "supersedes"} \
                or type(r["version"]) is not int or r["version"] < 1 or r["memory_id"] in seen:
            raise ValueError()
        seen.add(r["memory_id"])
        target = store._record(con, r["memory_id"], r["version"])
        if target is None:
            raise ValueError()
        if r["kind"] in {"same_as", "contradicts", "supersedes"}:
            _text(r["reason"], 1000)
            snap = r["snapshot"]
            if type(snap) is not dict or any(snap[field] != target[field] for field in
                    ("memory_id", "version", "subject", "predicate", "value", "state", "source_refs", "target_guard")) \
                    or r["interpretation_trust"] != "reported" or type(r["source_metadata"]) is not list:
                raise ValueError()
    if "semantic_links" in p:
        links = p["semantic_links"]
        if type(links) is not list or not 1 <= len(links) <= 3:
            raise ValueError()
        for link in links:
            if type(link) is not dict or set(link) != {"memory_id", "expected_version", "relation", "reason"} \
                    or not any((r["memory_id"], r["version"], r["kind"], r.get("reason")) ==
                               (link["memory_id"], link["expected_version"], link["relation"], link["reason"]) for r in p["relations"]):
                raise ValueError()
    return p


def _candidate_and_audit_checks(con, store, rows, report):
    candidates, reviews = {}, {}
    for row in rows.get("memory_candidates", []):
        try:
            candidates[row["candidate_id"]] = _candidate_payload(con, store, row)
        except (GatewayError, ValueError, TypeError, KeyError, IndexError):
            report("candidate_record")
    for row in rows.get("memory_candidate_reviews", []):
        try:
            if row["candidate_id"] not in candidates or row["decision"] not in {"approve", "reject"} or row["resolution"] not in {"none", "confirmed_update"}:
                raise ValueError()
            _text(row["note"], 1000)
            _text(row["reviewed_at"], 100)
            reviews[row["candidate_id"]] = row
        except (GatewayError, ValueError, TypeError):
            report("candidate_review")
    for request in rows["memory_requests"]:
        # Request keys are public and their prefix is not reserved. Restrict
        # this consistency check to an exact key with an approved proposal;
        # neither a prefix nor a pending proposal proves generated provenance.
        # The legacy schema cannot disambiguate an ordinary caller deliberately
        # reusing the exact key of an approved proposal. This is binding
        # consistency within that scope, not authentication of request origin.
        if type(request["idempotency_key"]) is not str or not re.fullmatch(r"candidate-[0-9a-f]{32}", request["idempotency_key"]):
            continue
        cid = "candidate:" + request["idempotency_key"][10:]
        p, review = candidates.get(cid), reviews.get(cid)
        if p is None or review is None or review["decision"] != "approve":
            continue
        record = store._record(con, request["memory_id"], request["version"])
        refs = sorted(set([p["source_ref"]] + p.get("evidence_refs", [])))
        value, relations = p["value"], p["relations"]
        if relations and relations[0]["kind"] == "same_as":
            value = relations[0]["snapshot"]["value"]
            refs = sorted(set(refs + relations[0]["snapshot"]["source_refs"]))
        digest_payload = dict(value=value, source_refs=refs, epistemic_status="verified", verification_note=review["note"])
        if relations:
            relation = relations[0]
            target = store._record(con, relation["memory_id"], relation["version"])
            digest_payload.update(op="revise", memory_id=relation["memory_id"], expected_version=relation["version"],
                                  state="active", target_guard=target["target_guard"])
        else:
            digest_payload.update(op="create", subject=p["subject"], predicate=p["predicate"])
        if request["request_sha256"] != _digest(digest_payload):
            report("candidate_commit_binding")
        if record is None or record["value"] != value or record["source_refs"] != refs \
                or record["epistemic_status"] != "verified" or record["verification_note"] != review["note"] \
                or record["state"] != "active" or len(relations) > 1:
            report("candidate_commit_binding")
        elif relations:
            if record["memory_id"] != relations[0]["memory_id"] or record["version"] not in {relations[0]["version"], relations[0]["version"] + 1}:
                report("candidate_commit_binding")
        elif (record["subject"], record["predicate"]) != (p["subject"], p["predicate"]):
            report("candidate_commit_binding")
    for row in rows["operation_audit"]:
        if row["operation"] in {"create_memory", "revise_memory", "retire_memory"}:
            exists = con.execute("SELECT 1 FROM memory_facts WHERE memory_id=?", (row["resource_id"],)).fetchone()
        elif row["operation"] in {"propose_memory_candidate", "review_memory_candidate"}:
            exists = row["resource_id"] in candidates
        else:
            exists = False
        try:
            _text(row["occurred_at"], 100)
            if not exists or row["resource_type"] != "memory" or row["outcome"] != "success":
                raise ValueError()
        except (GatewayError, ValueError, TypeError):
            report("audit_record")


def verify_integrity(store) -> dict:
    """Bounded read snapshot; reports structure, never source truth or content."""
    result = {"verified": False, "complete": False, "facts": 0, "versions": 0,
              "checked_versions": 0, "diagnostics": [], "candidate_schema": "absent"}
    diagnostics = result["diagnostics"]

    def report(code):
        if code not in diagnostics:
            diagnostics.append(code)

    steps = 0

    def budget():
        nonlocal steps
        steps += 1
        return int(steps > 10000)  # At most ten million SQLite VM instructions.

    try:
        with closing(store._connect()) as con:
            con.set_progress_handler(budget, 1000)
            con.execute("BEGIN")
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"memory_facts", "memory_versions", "memory_requests",
                        "memory_assessments", "write_provenance", "schema_migrations", "operation_audit"}
            if not required <= tables:
                report("required_schema")
                return result
            candidates = {"memory_candidates", "memory_candidate_reviews"} & tables
            result["candidate_schema"] = "present" if len(candidates) == 2 else "absent"
            if len(candidates) == 1:
                report("candidate_schema")
            if not _schema_matches(con, store, len(candidates) == 2):
                report("schema_contract")
            all_rows = {}
            for table in sorted(required | candidates):
                rows = con.execute(f'SELECT * FROM "{table}" LIMIT 10001').fetchall()
                if len(rows) > 10000:
                    report("row_budget_exceeded")
                    return result
                all_rows[table] = rows
            if con.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                report("sqlite_quick_check")
            if con.execute("PRAGMA foreign_key_check").fetchone() is not None:
                report("foreign_key_check")
            result["facts"] = con.execute("SELECT count(*) FROM memory_facts").fetchone()[0]
            result["versions"] = con.execute("SELECT count(*) FROM memory_versions").fetchone()[0]
            if max(result["facts"], result["versions"]) > 10000:
                report("row_budget_exceeded")
                return result
            facts = list(con.execute("SELECT * FROM memory_facts LIMIT 10001"))
            versions = list(con.execute("SELECT * FROM memory_versions ORDER BY memory_id,version LIMIT 10001"))
            sequence = {}
            prior_provenance = {}
            retired = set()
            for fact in facts:
                try:
                    if MEMORY_ID.fullmatch(fact["memory_id"]) is None:
                        raise ValueError()
                    _text(fact["subject"], 200)
                    _text(fact["predicate"], 120)
                    _text(fact["created_at"], 100)
                except (GatewayError, ValueError, TypeError):
                    report("fact_record")
            for row in versions:
                result["checked_versions"] += 1
                mid, version = row["memory_id"], row["version"]
                if mid in retired:
                    report("retirement_sequence")
                if row["state"] == "retired":
                    retired.add(mid)
                if type(version) is not int or version != sequence.get(mid, 0) + 1:
                    report("version_sequence")
                sequence[mid] = version
                try:
                    _text(row["value"], 4000)
                    _text(row["recorded_at"], 100)
                    refs = _references(json.loads(row["source_refs"]))
                    if row["state"] not in {"active", "retired"}:
                        raise ValueError()
                    assessment = con.execute("SELECT * FROM memory_assessments WHERE memory_id=? AND version=?", (mid, version)).fetchone()
                    if assessment is None:
                        report("assessment_coverage")
                    else:
                        store._assessment(assessment["epistemic_status"], assessment["verification_note"])
                except (GatewayError, ValueError, TypeError):
                    refs = None
                    report("version_record")
                prov = con.execute("SELECT * FROM write_provenance WHERE record_type='memory' AND record_id=? AND version=?", (mid, version)).fetchone()
                if prov is None:
                    report("provenance_coverage")
                else:
                    try:
                        identity = ReportedIdentity(prov["reported_agent"], prov["reported_client"], prov["run_id"])
                        expected_trust = "reported" if any((identity.reported_agent, identity.reported_client, identity.run_id)) else "unavailable"
                        if _references(json.loads(prov["source_refs"])) != refs \
                                or prov["supersedes_provenance_id"] != prior_provenance.get(mid) \
                                or prov["data_origin"] not in {"source", "deterministic_derived", "agent_generated", "imported", "legacy_import", "system_generated"} \
                                or prov["actor_type"] not in {"external_client", "importer", "system", "unknown"} \
                                or prov["identity_trust"] != expected_trust \
                                or prov["legacy_status"] not in {"native", "imported", "pre_provenance"}:
                            report("provenance_record")
                    except (GatewayError, ValueError, TypeError):
                        report("provenance_record")
                    prior_provenance[mid] = prov["provenance_id"]
                if not con.execute("SELECT 1 FROM memory_requests WHERE memory_id=? AND version=? LIMIT 1", (mid, version)).fetchone():
                    report("request_coverage")
            if any(fact["memory_id"] not in sequence for fact in facts):
                report("fact_coverage")
            requests = con.execute("SELECT r.* FROM memory_requests r LEFT JOIN memory_versions v ON r.memory_id=v.memory_id AND r.version=v.version WHERE v.memory_id IS NULL LIMIT 1").fetchone()
            if requests is not None:
                report("request_reference")
            requests = con.execute("SELECT idempotency_key,request_sha256 FROM memory_requests LIMIT 10001").fetchall()
            if len(requests) > 10000:
                report("row_budget_exceeded")
                return result
            for request in requests:
                if type(request[0]) is not str or REQUEST_KEY.fullmatch(request[0]) is None \
                        or type(request[1]) is not str or RECORD_HASH.fullmatch(request[1]) is None:
                    report("request_record")
            if con.execute("SELECT 1 FROM write_provenance p LEFT JOIN memory_versions v ON p.record_id=v.memory_id AND p.version=v.version WHERE p.record_type='memory' AND v.memory_id IS NULL LIMIT 1").fetchone():
                report("provenance_reference")
            _candidate_and_audit_checks(con, store, all_rows, report)
            result["complete"] = True
            result["verified"] = not diagnostics
            return result
    except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError):
        report("sqlite_check_incomplete")
        return result

