"""Additive immutable derived History. This store is owned by the Gateway."""
import base64
from contextlib import closing
import hashlib
import json
import re
import sqlite3
from .history import SQLiteHistoryBackend
from .history_sources import SourceEvidenceStore, SYSTEMS
from ..contracts import GatewayError
from ..normalization.contracts import VERSION, encoded, identity
from ..provenance import ensure_provenance_schema, get_provenance, insert_provenance, utc_now

TABLES = ("p13_conversations", "p13_messages", "p13_views", "p13_message_evidence", "p13_normalization_outcomes", "p13_view_nodes")


def valid_id(value, kind):
    if type(value) is not str or not re.fullmatch(kind+r":[0-9a-f]{64}", value):
        raise GatewayError("INVALID_ARGUMENT", "Invalid canonical identity")


def bounds(offset, limit, maximum):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= maximum:
        raise GatewayError("INVALID_ARGUMENT", "Invalid canonical range")


class CanonicalHistoryStore:
    def __init__(self, path):
        self.history = SQLiteHistoryBackend(path)

    @staticmethod
    def exists(c):
        return c.execute("SELECT 1 FROM sqlite_master WHERE name='p13_normalization_outcomes' AND type='table'").fetchone() is not None

    @staticmethod
    def source_rows(c):
        if not SourceEvidenceStore._exists(c): return []
        return c.execute("SELECT source_id,source_system,source_format,sha256,byte_count FROM history_source_files ORDER BY source_id").fetchall()

    @classmethod
    def snapshot_at(cls, c):
        rows = cls.source_rows(c)
        return {"source_set_sha256":hashlib.sha256(encoded([list(r) for r in rows]).encode()).hexdigest(),
                "source_count":len(rows), "normalization_version":VERSION}

    def snapshot(self):
        with closing(self.history._connect()) as c: return self.snapshot_at(c)

    @staticmethod
    def schema(c):
        for sql in (
            "CREATE TABLE IF NOT EXISTS p13_conversations(conversation_id TEXT PRIMARY KEY, source_system TEXT NOT NULL, native_key_digest TEXT NOT NULL, normalized_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS p13_messages(message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES p13_conversations, role TEXT NOT NULL, text TEXT NOT NULL, payload BLOB NOT NULL, metadata TEXT NOT NULL, content_sha256 TEXT NOT NULL, content_bytes INTEGER NOT NULL, normalized_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS p13_views(view_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES p13_conversations, source_id TEXT NOT NULL REFERENCES history_source_files, version TEXT NOT NULL, payload TEXT NOT NULL, normalized_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS p13_message_evidence(view_id TEXT NOT NULL REFERENCES p13_views, message_id TEXT NOT NULL REFERENCES p13_messages, position INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(view_id,position))",
            "CREATE TABLE IF NOT EXISTS p13_normalization_outcomes(source_id TEXT NOT NULL REFERENCES history_source_files, version TEXT NOT NULL, result_sha256 TEXT NOT NULL, payload TEXT NOT NULL, normalized_at TEXT NOT NULL, PRIMARY KEY(source_id,version))",
            "CREATE TABLE IF NOT EXISTS p13_view_nodes(view_id TEXT NOT NULL REFERENCES p13_views, position INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(view_id,position))",
            "CREATE INDEX IF NOT EXISTS p13_message_conversation ON p13_messages(conversation_id)",
            "CREATE INDEX IF NOT EXISTS p13_message_normalization ON p13_messages(normalized_at)",
            "CREATE INDEX IF NOT EXISTS p13_conversation_normalization ON p13_conversations(normalized_at)",
            "CREATE INDEX IF NOT EXISTS p13_view_source ON p13_views(source_id)",
            "CREATE INDEX IF NOT EXISTS p13_evidence_message ON p13_message_evidence(message_id)",
        ): c.execute(sql)
        for table in TABLES:
            for operation in ("UPDATE", "DELETE"):
                c.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'immutable canonical evidence'); END")
        ensure_provenance_schema(c, "p13_canonical_history", backfill=False)

    def outcome(self, source_id):
        with closing(self.history._connect()) as c:
            if not self.exists(c): return None
            row=c.execute("SELECT payload FROM p13_normalization_outcomes WHERE source_id=? AND version=?",(source_id,VERSION)).fetchone()
            return json.loads(row[0]) if row else None

    def persist(self, source, result, expected_snapshot):
        try:
            return self._persist(source,result,expected_snapshot)
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE","Canonical transaction failed") from None

    def _persist(self, source, result, expected_snapshot):
        digest=hashlib.sha256(encoded(result).encode()).hexdigest()
        sid=source["source_id"]
        with closing(self.history._connect(write=True)) as c, c:
            self.schema(c); c.commit(); c.execute("BEGIN IMMEDIATE")
            if self.snapshot_at(c)["source_set_sha256"] != expected_snapshot:
                raise GatewayError("CONFLICT", "History source set changed")
            prior=c.execute("SELECT result_sha256,payload FROM p13_normalization_outcomes WHERE source_id=? AND version=?",(sid,VERSION)).fetchone()
            if prior:
                if prior[0] != digest: raise GatewayError("CONFLICT", "Normalization version result conflicts")
                return json.loads(prior[1]), True
            counts={"new_messages":0,"new_conversations":0,"new_views":0}
            now=utc_now()
            def provenance(kind, key, occurred=None):
                insert_provenance(c, record_type=kind, record_id=key, version=1, data_origin="deterministic_derived", actor_type="system",
                                  source_refs=[sid], original_created_at=occurred, source_system=source["source_system"], legacy_status="imported")
            for v in result["views"]:
                cid=v["conversation_id"]
                if c.execute("INSERT OR IGNORE INTO p13_conversations VALUES (?,?,?,?)",(cid, v["source_system"], v["native_key_digest"], now)).rowcount:
                    counts["new_conversations"] += 1; provenance("canonical_conversation",cid,v["created_at"])
                vid=identity("view",sid,VERSION,cid,v["locator"])
                view_payload={k:value for k,value in v.items() if k not in {"messages","nodes"}}
                view_payload.update(method=result["method"],version=VERSION,source_fingerprint=source["source_fingerprint"],source_imported_at=source["imported_at"])
                c.execute("INSERT INTO p13_views VALUES (?,?,?,?,?,?)",(vid,cid,sid,VERSION,encoded(view_payload),now))
                provenance("canonical_view",vid,v["created_at"]); counts["new_views"] += 1
                for pos,node in enumerate(v["nodes"]):
                    node_payload={k:value for k,value in node.items() if k != "children"}
                    node_payload["children_count"]=len(node["children"])
                    c.execute("INSERT INTO p13_view_nodes VALUES (?,?,?)",(vid,pos,encoded(node_payload)))
                for m in v["messages"]:
                    mid=m["message_id"]
                    core={k:value for k,value in m.items() if k not in {"locator","position","parent_key"}}
                    raw=encoded(core).encode()
                    metadata={k:m[k] for k in ("message_id","conversation_id","role","occurred_at","time_quality","confidence","model")}
                    if c.execute("INSERT OR IGNORE INTO p13_messages VALUES (?,?,?,?,?,?,?,?,?)",(mid,cid,m["role"],m["text"],raw,encoded(metadata),hashlib.sha256(raw).hexdigest(),len(raw),now)).rowcount:
                        counts["new_messages"] += 1; provenance("canonical_message",mid,m["occurred_at"])
                    else:
                        if c.execute("SELECT payload FROM p13_messages WHERE message_id=?",(mid,)).fetchone()[0] != raw:
                            raise GatewayError("CONFLICT", "Canonical message identity conflicts")
                    evidence={"source_id":sid,"source_fingerprint":source["source_fingerprint"],"source_imported_at":source["imported_at"],
                              "locator":m["locator"],"parent_key":m["parent_key"],"confidence":m["confidence"],"time_quality":m["time_quality"],
                              "method":result["method"],"version":VERSION,"normalized_at":now}
                    c.execute("INSERT INTO p13_message_evidence VALUES (?,?,?,?)",(vid,mid,m["position"],encoded(evidence)))
            outcome={k:value for k,value in result.items() if k != "views"}
            outcome.update(source_id=sid,source_fingerprint=source["source_fingerprint"],source_imported_at=source["imported_at"],normalized_at=now,
                           views=len(result["views"]),message_appearances=sum(len(v["messages"]) for v in result["views"]),
                           exact=sum(m["confidence"]=="exact" for v in result["views"] for m in v["messages"]),
                           derived_safe=sum(m["confidence"]=="derived-safe" for v in result["views"] for m in v["messages"]),**counts)
            c.execute("INSERT INTO p13_normalization_outcomes VALUES (?,?,?,?,?)",(sid,VERSION,digest,encoded(outcome),now))
            provenance("normalization_outcome",identity("normalization",sid,VERSION))
            return outcome, False

    def summary(self):
        with closing(self.history._connect()) as c:
            if not self.exists(c): return {"conversations":0,"messages":0,"views":0,"source_outcomes":0,"by_state":{},"by_source_system":{},"candidates":0,"ambiguous_messages":0,"exact_appearances":0,"derived_safe_appearances":0,"retained_source_only":0}
            outcomes=[json.loads(r[0]) for r in c.execute("SELECT payload FROM p13_normalization_outcomes WHERE version=?",(VERSION,))]
            counts={name:c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for name,table in zip(("conversations","messages","views","source_outcomes"),(TABLES[0],TABLES[1],TABLES[2],TABLES[4]))}
            states={}
            for r in outcomes: states[r["state"]]=states.get(r["state"],0)+1
            return dict(counts,by_state=states,by_source_system=dict(c.execute("SELECT source_system,COUNT(*) FROM p13_conversations GROUP BY source_system")),
                        candidates=sum(r["candidates"] for r in outcomes),ambiguous_messages=sum(r["ambiguous_messages"] for r in outcomes),
                        exact_appearances=sum(r["exact"] for r in outcomes),derived_safe_appearances=sum(r["derived_safe"] for r in outcomes),
                        message_appearances=sum(r["message_appearances"] for r in outcomes),retained_source_only=sum(r["message_appearances"]==0 for r in outcomes))

    def search(self, *, source_system=None, query="", limit=20, offset=0):
        bounds(offset,limit,100)
        if source_system is not None and (type(source_system) is not str or source_system not in SYSTEMS) or type(query) is not str or len(query)>500:
            raise GatewayError("INVALID_ARGUMENT","Invalid canonical search")
        with closing(self.history._connect()) as c:
            if not self.exists(c): return {"total":0,"conversations":[],"has_more":False}
            where=" WHERE (? IS NULL OR source_system=?) AND (instr(lower(conversation_id),?)>0 OR EXISTS(SELECT 1 FROM p13_messages m WHERE m.conversation_id=p13_conversations.conversation_id AND instr(lower(m.text),?)>0))"
            args=(source_system,source_system,query.casefold(),query.casefold())
            total=c.execute("SELECT COUNT(*) FROM p13_conversations"+where,args).fetchone()[0]
            rows=c.execute("SELECT * FROM p13_conversations"+where+" ORDER BY conversation_id LIMIT ? OFFSET ?",(*args,limit,offset)).fetchall()
            return {"total":total,"has_more":offset+limit<total,"conversations":[dict(r) for r in rows]}

    def conversation(self, cid, *, offset=0, limit=50, view_offset=0, view_limit=20, node_offset=0, node_limit=50):
        valid_id(cid,"conversation"); bounds(offset,limit,100); bounds(view_offset,view_limit,100)
        bounds(node_offset,node_limit,100)
        with closing(self.history._connect()) as c:
            row=c.execute("SELECT * FROM p13_conversations WHERE conversation_id=?",(cid,)).fetchone() if self.exists(c) else None
            if row is None: raise GatewayError("RESOURCE_NOT_FOUND","Canonical conversation not found")
            views=[]
            total_views=c.execute("SELECT COUNT(*) FROM p13_views WHERE conversation_id=?",(cid,)).fetchone()[0]
            for r in c.execute("SELECT * FROM p13_views WHERE conversation_id=? ORDER BY view_id LIMIT ? OFFSET ?",(cid,view_limit,view_offset)):
                value=json.loads(r["payload"])
                value.update(view_id=r["view_id"], source_id=r["source_id"], normalized_at=r["normalized_at"])
                total=c.execute("SELECT COUNT(*) FROM p13_message_evidence WHERE view_id=?",(r["view_id"],)).fetchone()[0]
                value["messages"]=[dict(e) for e in c.execute("SELECT message_id,position FROM p13_message_evidence WHERE view_id=? ORDER BY position LIMIT ? OFFSET ?",(r["view_id"],limit,offset))]
                value.update(total_messages=total,has_more=offset+limit<total)
                total_nodes=c.execute("SELECT COUNT(*) FROM p13_view_nodes WHERE view_id=?",(r["view_id"],)).fetchone()[0]
                value.update(nodes=[json.loads(n[0]) for n in c.execute("SELECT payload FROM p13_view_nodes WHERE view_id=? ORDER BY position LIMIT ? OFFSET ?",(r["view_id"],node_limit,node_offset))],total_nodes=total_nodes,has_more_nodes=node_offset+node_limit<total_nodes)
                views.append(value)
            return {"conversation":dict(row),"provenance":get_provenance(c,"canonical_conversation",cid,1),"views":views,"total_views":total_views,"has_more_views":view_offset+view_limit<total_views}

    def message(self, mid, *, offset=0, length=65536, evidence_offset=0, evidence_limit=20):
        valid_id(mid,"message"); bounds(offset,length,65536); bounds(evidence_offset,evidence_limit,100)
        with closing(self.history._connect()) as c:
            row=c.execute("SELECT rowid,metadata,content_sha256,content_bytes,normalized_at FROM p13_messages WHERE message_id=?",(mid,)).fetchone() if self.exists(c) else None
            if row is None: raise GatewayError("RESOURCE_NOT_FOUND","Canonical message not found")
            if offset>row["content_bytes"]: raise GatewayError("INVALID_ARGUMENT","Invalid canonical range")
            with c.blobopen("p13_messages","payload",row["rowid"],readonly=True) as blob:
                blob.seek(offset); raw=blob.read(length)
            total_evidence=c.execute("SELECT COUNT(*) FROM p13_message_evidence WHERE message_id=?",(mid,)).fetchone()[0]
            evidence=[dict(json.loads(r["payload"]),view_id=r["view_id"],position=r["position"]) for r in c.execute("SELECT * FROM p13_message_evidence WHERE message_id=? ORDER BY view_id,position LIMIT ? OFFSET ?",(mid,evidence_limit,evidence_offset))]
            metadata=json.loads(row["metadata"])
            metadata.update(normalized_at=row["normalized_at"],normalization_version=VERSION,provenance=get_provenance(c,"canonical_message",mid,1),
                            source_refs=sorted({e["source_id"] for e in evidence}),evidence=evidence,total_evidence=total_evidence,has_more_evidence=evidence_offset+evidence_limit<total_evidence)
            return {"message":metadata,"content_base64":base64.b64encode(raw).decode(),"content_bytes":row["content_bytes"],"content_sha256":row["content_sha256"],"offset":offset,"has_more":offset+length<row["content_bytes"]}

    def search_messages(self, query, limit=5, offset=0):
        if type(query) is not str or not 1 <= len(query.strip()) <= 500 or "\x00" in query:
            raise GatewayError("INVALID_ARGUMENT", "Invalid canonical message query")
        bounds(offset, limit, 20)
        if offset > 1000:
            raise GatewayError("INVALID_ARGUMENT", "Invalid canonical message offset")
        with closing(self.history._connect()) as c:
            if not self.exists(c):
                return {"results":[], "total":0, "has_more":False}
            args = (query.lower(),)
            total = c.execute("SELECT COUNT(*) FROM p13_messages WHERE instr(lower(text),?)>0", args).fetchone()[0]
            rows = c.execute("SELECT message_id,metadata,content_sha256,substr(text,1,500) snippet FROM p13_messages WHERE instr(lower(text),?)>0 ORDER BY message_id LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
            results = []
            for row in rows:
                metadata = json.loads(row["metadata"])
                refs = [r[0] for r in c.execute("SELECT DISTINCT v.source_id FROM p13_message_evidence e JOIN p13_views v USING(view_id) WHERE e.message_id=? ORDER BY v.source_id LIMIT 16", (row["message_id"],))]
                results.append({"message_id":row["message_id"], "snippet":row["snippet"], "content_sha256":row["content_sha256"], "occurred_at":metadata.get("occurred_at"), "role":metadata.get("role"), "source_refs":refs})
            return {"results":results, "total":total, "has_more":offset+len(rows)<total}
