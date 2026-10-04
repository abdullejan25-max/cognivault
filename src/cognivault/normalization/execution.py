"""Gateway-only normalization and durable private receipts."""
from contextlib import closing
import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4
from .adapters import normalize_source
from .contracts import encoded, result
from ..adapters.canonical_history import CanonicalHistoryStore, bounds
from ..adapters.documents import _path_has_reparse_point
from ..contracts import GatewayError
from ..migration.gateway_sources import private_inbox, resolve_manifest
from ..provenance import utc_now


def candidate_result(gateway, store, source):
    system,fmt=source["source_system"],source["source_format"]
    supported=(system in {"codex","workbuddy"} and fmt=="jsonl") or (system=="hermes" and fmt in {"json","jsonl"}) or (system=="chatgpt" and fmt in {"json","zip"})
    return normalize_source(system,fmt,source_bytes(gateway,store,source),source["source_fingerprint"]) if supported else result(system+"-"+fmt+"-1",state="unsupported",reason="no_proven_conversation_contract")


def source_bytes(gateway, store, source):
    """One bounded original BLOB read with checksum, inside the configured Gateway."""
    if source["payload_kind"] == "manifest":
        raw = b"".join(resolve_manifest(gateway,source,o,min(65536,source["byte_count"]-o)) for o in range(0,source["byte_count"],65536))
    else:
        table, column, key = ("history_source_payloads","sha256",source["sha256"]) if source["payload_kind"]=="inline" else ("legacy_source_records","source_record_id",source["payload_ref"])
        with closing(store.history._connect()) as c:
            row=c.execute(f"SELECT rowid FROM {table} WHERE {column}=?",(key,)).fetchone()
            if row is None: raise GatewayError("CONFLICT","Source payload is missing")
            with c.blobopen(table,"content",row[0],readonly=True) as blob:
                if len(blob)!=source["byte_count"]: raise GatewayError("CONFLICT","Source byte count conflicts")
                raw=blob.read()
    if len(raw)!=source["byte_count"] or hashlib.sha256(raw).hexdigest()!=source["sha256"]:
        raise GatewayError("CONFLICT","Source checksum conflicts")
    return raw


def normalize(gateway, source_set_sha256, *, cursor=0, limit=16):
    gateway._require_capability("read"); gateway._require_capability("ingest")
    bounds(cursor,limit,64)
    if type(source_set_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}",source_set_sha256):
        raise GatewayError("INVALID_ARGUMENT","Invalid source set fingerprint")
    root=private_inbox(gateway)
    canonical=gateway._canonical_history_store(); store=gateway._source_evidence_store()
    with closing(store.history._connect()) as c:
        c.execute("BEGIN")
        snap=canonical.snapshot_at(c)
        if snap["source_set_sha256"]!=source_set_sha256: raise GatewayError("CONFLICT","History source set changed")
        rows=canonical.source_rows(c)[cursor:cursor+limit]
    outcomes=[]
    for row in rows:
        sid=row["source_id"]
        try:
            source=store.metadata(sid)
            prior=canonical.outcome(sid)
            if prior:
                outcomes.append(dict(prior,reused=True,new_conversations=0,new_messages=0,new_views=0)); continue
            parsed=candidate_result(gateway,store,source)
            outcome,reused=canonical.persist(source,parsed,source_set_sha256)
            outcomes.append(dict(outcome,reused=reused))
        except GatewayError as error:
            if error.code=="CONFLICT" and canonical.snapshot()["source_set_sha256"]!=source_set_sha256: raise
            outcomes.append({"source_id":sid,"state":"error","error_code":error.code,"reused":False,"new_messages":0,"new_conversations":0,"new_views":0})
    if canonical.snapshot()["source_set_sha256"]!=source_set_sha256:
        raise GatewayError("CONFLICT","History source set changed")
    response={"source_set_sha256":source_set_sha256,"source_count":snap["source_count"],"cursor":cursor,"next_cursor":cursor+len(rows),
              "has_more":cursor+len(rows)<snap["source_count"],"results":outcomes,"recorded_at":utc_now(),
              "reused_sources":sum(r["reused"] for r in outcomes),"errors":sum(r["state"]=="error" for r in outcomes),
              **{key:sum(r[key] for r in outcomes) for key in ("new_conversations","new_messages","new_views")}}
    folder=root/"normalization-receipts"
    io_folder=folder
    if os.name=="nt" and not str(folder).startswith("\\\\?\\"):
        value=str(folder.absolute())
        io_folder=Path("\\\\?\\UNC\\"+value[2:] if value.startswith("\\\\") else "\\\\?\\"+value)
    try:
        io_folder.mkdir(exist_ok=True)
        if _path_has_reparse_point(io_folder): raise GatewayError("OUTSIDE_ALLOWLIST","Unsafe receipt target")
        name=source_set_sha256+"-"+str(cursor)+"-"+uuid4().hex+".json"
        data=encoded(response).encode()
        with (io_folder/name).open("xb") as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
    except OSError: raise GatewayError("STORAGE_UNAVAILABLE","Normalization receipt could not be persisted") from None
    response["receipt"]={"relative_path":"normalization-receipts/"+name,"sha256":hashlib.sha256(data).hexdigest()}
    return response
