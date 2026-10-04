"""Immutable source evidence in the configured History DB, separate from messages."""
from contextlib import closing
from dataclasses import dataclass
import base64
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .history import SQLiteHistoryBackend, _normalize_timestamp
from .legacy_sources import SQLiteLegacySourceStore
from ..contracts import GatewayError
from ..provenance import ReportedIdentity, ensure_provenance_schema, get_provenance, insert_provenance, utc_now

MAX_SOURCE_BYTES = 512 * 1024 * 1024
SYSTEMS = frozenset({"codex", "workbuddy", "hermes", "basic_memory", "v1", "gemini", "chatgpt", "other_agent"})
FORMATS = frozenset({"json", "jsonl", "markdown", "html", "zip", "sqlite", "plain_text", "unknown"})
KINDS = frozenset({"conversation_export", "session_metadata", "runtime_session_dump", "legacy_archive", "raw_session", "unknown"})
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE = re.compile(r"source-file:[0-9a-f]{64}\Z")
_REF = re.compile(r"(?:(?:asset|document)://sha256/|legacy-source:)[0-9a-f]{64}\Z")


def source_identity(system, digest):
    if type(system) is not str or system not in SYSTEMS or type(digest) is not str or not _HASH.fullmatch(digest):
        raise GatewayError("INVALID_ARGUMENT", "Invalid source identity")
    return "source-file:" + hashlib.sha256((system + "\0" + digest).encode()).hexdigest()


@dataclass(frozen=True)
class SourceFileInput:
    source_system: str
    source_format: str
    record_kind: str
    content: bytes
    expected_sha256: str
    occurred_at: str | None = None


class SourceEvidenceStore:
    def __init__(self, database_path: Path):
        self.history = SQLiteHistoryBackend(database_path)
        self.database_path = self.history.database_path

    @staticmethod
    def _exists(c):
        return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='history_source_files'").fetchone() is not None

    @staticmethod
    def _schema(c):
        # execute individually: executescript would commit an active transaction.
        for sql in (
            "CREATE TABLE IF NOT EXISTS history_source_payloads (sha256 TEXT PRIMARY KEY, content BLOB NOT NULL)",
            "CREATE TABLE IF NOT EXISTS history_source_files (source_id TEXT PRIMARY KEY, source_system TEXT NOT NULL, source_format TEXT NOT NULL, record_kind TEXT NOT NULL, sha256 TEXT NOT NULL, byte_count INTEGER NOT NULL, occurred_at TEXT, imported_at TEXT NOT NULL, payload_kind TEXT NOT NULL, payload_ref TEXT NOT NULL, evidence_refs TEXT NOT NULL, descriptor TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS history_source_digests ON history_source_files(sha256)",
            "CREATE TRIGGER IF NOT EXISTS source_file_no_update BEFORE UPDATE ON history_source_files BEGIN SELECT RAISE(ABORT,'immutable source evidence'); END",
            "CREATE TRIGGER IF NOT EXISTS source_file_no_delete BEFORE DELETE ON history_source_files BEGIN SELECT RAISE(ABORT,'immutable source evidence'); END",
            "CREATE TRIGGER IF NOT EXISTS source_payload_no_update BEFORE UPDATE ON history_source_payloads BEGIN SELECT RAISE(ABORT,'immutable source payload'); END",
            "CREATE TRIGGER IF NOT EXISTS source_payload_no_delete BEFORE DELETE ON history_source_payloads BEGIN SELECT RAISE(ABORT,'immutable source payload'); END",
        ): c.execute(sql)
        ensure_provenance_schema(c, "history_source_files", backfill=False)

    @staticmethod
    def _metadata(c, row):
        result=dict(row)
        result["evidence_refs"]=json.loads(result["evidence_refs"])
        result["descriptor"]=json.loads(result["descriptor"])
        p=get_provenance(c,"history_source_file",result["source_id"],1)
        if result["source_id"] != source_identity(result["source_system"],result["sha256"]) or p is None \
                or p["source_refs"] != result["evidence_refs"] or p["source_system"] != result["source_system"] \
                or p["data_origin"] != "imported" or p["actor_type"] != "importer" \
                or p["original_created_at"] != result["occurred_at"] or p["imported_at"] != result["imported_at"]:
            raise GatewayError("CONFLICT","Source evidence integrity verification failed")
        result["write_provenance"]=p
        # These identify the immutable v1 table/identity contract. A later
        # importer must use its own schema scope instead of relabelling v1 rows.
        result["source_schema_version"]=1
        result["importer_version"]="p13-source-evidence-1"
        result["source_fingerprint"]=result["source_id"].split(":",1)[1]
        outcome=None
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='p13_normalization_outcomes'").fetchone():
            outcome=c.execute("SELECT payload FROM p13_normalization_outcomes WHERE source_id=? AND version='p13-normalize-1'",(result["source_id"],)).fetchone()
        result["normalization_state"]=json.loads(outcome[0])["state"] if outcome else "not_normalized"
        return result

    def _register(self, *, system, fmt, kind, digest, size, payload_kind, payload_ref,
                  evidence_refs=(), descriptor=None, occurred_at=None, content=None, reported_identity=None):
        identity=source_identity(system,digest)
        if type(fmt) is not str or fmt not in FORMATS or type(kind) is not str or kind not in KINDS or type(size) is not int or not 0 <= size <= MAX_SOURCE_BYTES \
                or payload_kind not in {"inline","legacy","manifest"} \
                or any(type(ref) is not str or not _REF.fullmatch(ref) for ref in evidence_refs):
            raise GatewayError("INVALID_ARGUMENT","Invalid source evidence")
        if occurred_at is not None: occurred_at=_normalize_timestamp(occurred_at)
        if reported_identity is not None and type(reported_identity) is not ReportedIdentity:
            raise GatewayError("INVALID_ARGUMENT","Invalid source provenance")
        try:
            with closing(self.history._connect(write=True)) as c, c:
                self._schema(c)
                c.commit()
                c.execute("BEGIN IMMEDIATE")
                existing=c.execute("SELECT * FROM history_source_files WHERE source_id=?",(identity,)).fetchone()
                if existing:
                    if (existing["source_format"],existing["record_kind"],existing["byte_count"],existing["occurred_at"]) != (fmt,kind,size,occurred_at):
                        raise GatewayError("CONFLICT","Source evidence metadata conflicts")
                    return {"source_id":identity,"reused":True,"source":self._metadata(c,existing)}
                if payload_kind=="inline":
                    c.execute("INSERT OR IGNORE INTO history_source_payloads VALUES (?,?)",(digest,content))
                imported=utc_now()
                c.execute("INSERT INTO history_source_files VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (identity,system,fmt,kind,digest,size,occurred_at,imported,payload_kind,payload_ref,
                           json.dumps(list(evidence_refs),separators=(",",":")),json.dumps(descriptor or {},sort_keys=True,separators=(",",":"))))
                insert_provenance(c,record_type="history_source_file",record_id=identity,version=1,
                                  data_origin="imported",actor_type="importer",source_refs=list(evidence_refs),
                                  original_created_at=occurred_at,imported_at=imported,source_system=system,
                                  legacy_status="imported",identity=reported_identity)
                row=c.execute("SELECT * FROM history_source_files WHERE source_id=?",(identity,)).fetchone()
                return {"source_id":identity,"reused":False,"source":self._metadata(c,row)}
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE","Source evidence storage is unavailable") from None

    def import_bytes(self, item: SourceFileInput, *, reported_identity=None):
        if type(item) is not SourceFileInput or type(item.content) is not bytes or len(item.content)>MAX_SOURCE_BYTES:
            raise GatewayError("INVALID_ARGUMENT","Invalid source input")
        if hashlib.sha256(item.content).hexdigest()!=item.expected_sha256:
            raise GatewayError("INVALID_ARGUMENT","Source digest mismatch")
        return self._register(system=item.source_system,fmt=item.source_format,kind=item.record_kind,
                              digest=item.expected_sha256,size=len(item.content),payload_kind="inline",
                              payload_ref=item.expected_sha256,content=item.content,occurred_at=item.occurred_at,
                              reported_identity=reported_identity)

    def link_legacy(self, *, source_system, legacy_record_id, source_format, record_kind,
                    expected_sha256=None, expected_bytes=None, reported_identity=None):
        legacy=SQLiteLegacySourceStore(self.database_path)
        old=legacy.fetch(legacy_record_id,0,1)
        digest=old["source_hash"]
        size=old["byte_count"]
        if expected_sha256 is not None and expected_sha256!=digest or expected_bytes is not None and expected_bytes!=size:
            raise GatewayError("CONFLICT","Legacy source identity conflicts")
        legacy.verify_record(legacy_record_id,expected_sha256=digest,expected_bytes=size)
        return self._register(system=source_system,fmt=source_format,kind=record_kind,digest=digest,size=size,
                              payload_kind="legacy",payload_ref=legacy_record_id,evidence_refs=(legacy_record_id,),
                              reported_identity=reported_identity)

    def link_manifest(self, *, source_system, source_sha256, byte_count, document_uri, descriptor, reported_identity=None):
        if type(document_uri) is not str or not re.fullmatch(r"document://sha256/[0-9a-f]{64}",document_uri) \
                or type(descriptor) is not dict:
            raise GatewayError("INVALID_ARGUMENT","Invalid source manifest reference")
        return self._register(system=source_system,fmt="zip",kind="conversation_export",digest=source_sha256,
                              size=byte_count,payload_kind="manifest",payload_ref=document_uri,evidence_refs=(document_uri,),
                              descriptor=descriptor,reported_identity=reported_identity)

    def metadata(self, identity):
        if type(identity) is not str or not _SOURCE.fullmatch(identity):
            raise GatewayError("INVALID_ARGUMENT","Invalid source reference")
        try:
            with closing(self.history._connect()) as c:
                row=c.execute("SELECT * FROM history_source_files WHERE source_id=?",(identity,)).fetchone() if self._exists(c) else None
                if row is None: raise GatewayError("RESOURCE_NOT_FOUND","Source evidence was not found")
                return self._metadata(c,row)
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE","Source evidence storage is unavailable") from None

    def fetch(self, identity, offset=0, length=65536, *, manifest_resolver=None):
        if type(offset) is not int or offset<0 or type(length) is not int or not 1<=length<=65536:
            raise GatewayError("INVALID_ARGUMENT","Invalid source byte range")
        source=self.metadata(identity)
        if offset>source["byte_count"]: raise GatewayError("INVALID_ARGUMENT","Invalid source byte range")
        if source["payload_kind"]=="legacy":
            pieces=[]
            for position in range(offset,min(offset+length,source["byte_count"]),16384):
                raw=SQLiteLegacySourceStore(self.database_path).fetch(source["payload_ref"],position,
                               min(16384,offset+length-position))["original_byte_range"]
                pieces.append(base64.b64decode(raw["content_base64"],validate=True))
            content=b''.join(pieces)
        elif source["payload_kind"]=="manifest":
            if manifest_resolver is None: raise GatewayError("STORAGE_UNAVAILABLE","Source manifest resolver is unavailable")
            content=manifest_resolver(source,offset,length)
        else:
            with closing(self.history._connect()) as c:
                row=c.execute("SELECT rowid FROM history_source_payloads WHERE sha256=?",(source["sha256"],)).fetchone()
                if row is None: raise GatewayError("CONFLICT","Source payload is missing")
                with c.blobopen("history_source_payloads","content",row[0],readonly=True) as blob:
                    if len(blob)!=source["byte_count"]: raise GatewayError("CONFLICT","Source byte length verification failed")
                    blob.seek(offset)
                    content=blob.read(length)
        if len(content)!=min(length,source["byte_count"]-offset):
            raise GatewayError("CONFLICT","Source byte length verification failed")
        return {"source":source,"content":content,"offset":offset,"has_more":offset+len(content)<source["byte_count"]}

    def summary(self):
        with closing(self.history._connect()) as c:
            if not self._exists(c): return {"source_records":0,"stored_payloads":0,"byte_equivalent_pairs":0,"canonical_messages_created":0,"by_source_system":{}}
            count=c.execute("SELECT COUNT(*) FROM history_source_files").fetchone()[0]
            payloads=c.execute("SELECT COUNT(*) FROM history_source_payloads").fetchone()[0]
            pairs=c.execute("SELECT COALESCE(SUM(n*(n-1)/2),0) FROM (SELECT COUNT(*) AS n FROM history_source_files GROUP BY sha256)").fetchone()[0]
            categories=dict(c.execute("SELECT source_system,COUNT(*) FROM history_source_files GROUP BY source_system"))
            return {"source_records":count,"stored_payloads":payloads,"byte_equivalent_pairs":pairs,"canonical_messages_created":0,"by_source_system":categories}

    def verify(self, identity, *, manifest_resolver=None):
        source=self.metadata(identity)
        digest=hashlib.sha256()
        size=0
        if source["payload_kind"]=="legacy":
            SQLiteLegacySourceStore(self.database_path).verify_record(source["payload_ref"],
                    expected_sha256=source["sha256"],expected_bytes=source["byte_count"])
            return {"source_id":identity,"verified":True,"sha256":source["sha256"],"byte_count":source["byte_count"],"provenance":source["write_provenance"]}
        if source["payload_kind"]=="inline":
            with closing(self.history._connect()) as c:
                row=c.execute("SELECT rowid FROM history_source_payloads WHERE sha256=?",(source["sha256"],)).fetchone()
                if row is None: raise GatewayError("CONFLICT","Source payload is missing")
                with c.blobopen("history_source_payloads","content",row[0],readonly=True) as blob:
                    while part:=blob.read(1024*1024):
                        digest.update(part)
                        size+=len(part)
        else:
            for position in range(0,source["byte_count"],65536):
                part=self.fetch(identity,position,65536,manifest_resolver=manifest_resolver)["content"]
                digest.update(part)
                size+=len(part)
        if digest.hexdigest()!=source["sha256"] or size!=source["byte_count"]:
            raise GatewayError("CONFLICT","Source checksum verification failed")
        return {"source_id":identity,"verified":True,"sha256":digest.hexdigest(),"byte_count":size,
                "provenance":source["write_provenance"]}

    def search(self, *, source_system=None, query="", limit=20, offset=0):
        if source_system is not None and (type(source_system) is not str or source_system not in SYSTEMS) or type(query) is not str or len(query)>500 \
                or type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or offset<0:
            raise GatewayError("INVALID_ARGUMENT","Invalid source search")
        with closing(self.history._connect()) as c:
            if not self._exists(c): return {"total":0,"has_more":False,"sources":[]}
            where=" WHERE (? IS NULL OR source_system=?) AND instr(lower(source_id||' '||source_system||' '||source_format||' '||record_kind),?)>0"
            params=(source_system,source_system,query.casefold())
            total=c.execute("SELECT COUNT(*) FROM history_source_files"+where,params).fetchone()[0]
            rows=c.execute("SELECT * FROM history_source_files"+where+" ORDER BY source_id LIMIT ? OFFSET ?",(*params,limit,offset)).fetchall()
            return {"total":total,"has_more":offset+limit<total,"sources":[self._metadata(c,r) for r in rows]}
