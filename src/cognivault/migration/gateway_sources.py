"""Gateway-owned private inbox ingestion. No Agent-side access to target storage."""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from uuid import uuid4

from ..adapters.documents import _opened_file_path, _path_has_reparse_point, _stream_signature
from ..adapters.history_sources import MAX_SOURCE_BYTES, SourceFileInput
from ..contracts import GatewayError
from ..provenance import ReportedIdentity, utc_now


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result: raise ValueError("duplicate key")
            result[key]=value
        return result
    try:
        return json.loads(raw.decode("utf-8"),object_pairs_hook=unique)
    except (ValueError,UnicodeError,RecursionError):
        raise GatewayError("INVALID_ARGUMENT","Invalid migration manifest") from None


def private_inbox(gateway):
    configured=gateway.config.history_migration_inbox
    if configured is None: raise GatewayError("STORAGE_UNAVAILABLE","History migration inbox is not configured")
    try:
        if _path_has_reparse_point(configured): raise GatewayError("OUTSIDE_ALLOWLIST","Unsafe migration inbox")
        root=Path(configured).resolve(strict=True)
        if not root.is_dir() or any((p/".git").exists() for p in (root,*root.parents)):
            raise GatewayError("OUTSIDE_ALLOWLIST","Unsafe migration inbox")
        for protected in (gateway.config.study_root,gateway.config.history_database,
                          gateway.config.asset_root,gateway.config.asset_database):
            if protected is not None:
                protected=Path(protected).resolve()
                if root.is_relative_to(protected) or protected.is_relative_to(root):
                    raise GatewayError("OUTSIDE_ALLOWLIST","Migration inbox overlaps authoritative storage")
        return root
    except (OSError,RuntimeError):
        raise GatewayError("STORAGE_UNAVAILABLE","History migration inbox is unavailable") from None


def read_input(root, relative, expected_hash, limit):
    if type(relative) is not str or not 1<=len(relative)<=500 or "\\" in relative \
            or any(ord(c)<32 for c in relative) or ":" in relative \
            or type(expected_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}",expected_hash):
        raise GatewayError("INVALID_ARGUMENT","Invalid migration input")
    parts=relative.split("/")
    if PurePosixPath(relative).is_absolute() or PureWindowsPath(relative).drive or any(p in {"",".",".."} for p in parts):
        raise GatewayError("INVALID_ARGUMENT","Invalid migration input")
    try:
        candidate=root.joinpath(*parts)
        if _path_has_reparse_point(candidate): raise GatewayError("OUTSIDE_ALLOWLIST","Unsafe migration input")
        target=candidate.resolve(strict=True)
        if not target.is_relative_to(root) or not target.is_file(): raise GatewayError("OUTSIDE_ALLOWLIST","Unsafe migration input")
        with target.open("rb") as stream:
            opened=_opened_file_path(stream)
            if opened is None or not opened.resolve(strict=True).is_relative_to(root) or os.fstat(stream.fileno()).st_nlink!=1:
                raise GatewayError("OUTSIDE_ALLOWLIST","Unsafe migration input")
            before=_stream_signature(stream)
            if before[2]>limit: raise GatewayError("PAYLOAD_TOO_LARGE","Migration input exceeds limit")
            content=stream.read(limit+1)
            if len(content)!=before[2] or _stream_signature(stream)!=before or _digest(content)!=expected_hash:
                raise GatewayError("CONFLICT","Migration input checksum verification failed")
            return content
    except (OSError,RuntimeError):
        raise GatewayError("RESOURCE_NOT_FOUND","Migration input is unavailable") from None


def resolve_manifest(gateway, source, offset, length):
    parts=source["descriptor"]["archive_parts"]
    end=min(offset+length,source["byte_count"])
    result=[]
    for part in parts:
        start=max(offset,part["offset"])
        stop=min(end,part["offset"]+part["bytes"])
        if start<stop:
            fetched=gateway.fetch_asset(part["asset_uri"],start-part["offset"],stop-start)
            result.append(base64.b64decode(fetched["content_base64"],validate=True))
    return b''.join(result)


def link_gemini(gateway, entry, identity):
    doc=gateway.fetch_document(entry["document_uri"])["document"]
    first=gateway.fetch_asset(doc["asset_uri"],0,65536)
    if first["size"]>90000: raise GatewayError("PAYLOAD_TOO_LARGE","Source manifest exceeds limit")
    content=bytearray(base64.b64decode(first["content_base64"],validate=True))
    if first["has_more"]:
        rest=gateway.fetch_asset(doc["asset_uri"],len(content),first["size"]-len(content))
        content.extend(base64.b64decode(rest["content_base64"],validate=True))
    if len(content)!=first["size"] or _digest(content)!=doc["asset_uri"].rsplit("/",1)[-1]:
        raise GatewayError("CONFLICT","Source manifest checksum verification failed")
    manifest=_json(content)
    if type(manifest) is not dict or manifest.get("schema_version")!=1 \
            or manifest.get("marker")!="P13_GEMINI_SOURCE_ONLY_V1" \
            or manifest.get("source_system")!="gemini" or entry["source_system"]!="gemini" \
            or manifest.get("archive_sha256")!=entry["sha256"] or manifest.get("archive_bytes")!=entry["byte_count"]:
        raise GatewayError("CONFLICT","Source manifest identity verification failed")
    parts=manifest.get("archive_parts")
    if type(parts) is not list or not 1<=len(parts)<=1024: raise GatewayError("INVALID_ARGUMENT","Invalid source manifest")
    digest=hashlib.sha256()
    offset=0
    for part in parts:
        if type(part) is not dict or type(part.get("bytes")) is not int or not 1<=part["bytes"]<=524288 \
                or part.get("offset")!=offset or part.get("asset_uri")!="asset://sha256/"+str(part.get("sha256")):
            raise GatewayError("INVALID_ARGUMENT","Invalid source archive range")
        piece=bytearray()
        for cursor in range(0,part["bytes"],65536):
            value=gateway.fetch_asset(part["asset_uri"],cursor,min(65536,part["bytes"]-cursor))
            piece.extend(base64.b64decode(value["content_base64"],validate=True))
        if len(piece)!=part["bytes"] or _digest(piece)!=part["sha256"]:
            raise GatewayError("CONFLICT","Source archive checksum verification failed")
        digest.update(piece)
        offset+=len(piece)
    if offset!=entry["byte_count"] or digest.hexdigest()!=entry["sha256"]:
        raise GatewayError("CONFLICT","Source archive checksum verification failed")
    return gateway._source_evidence_store().link_manifest(source_system="gemini",source_sha256=entry["sha256"],
        byte_count=entry["byte_count"],document_uri=entry["document_uri"],descriptor=manifest,reported_identity=identity)


def ingest(gateway, relative_manifest, expected_manifest_sha256, *, cursor=0, limit=128, provenance=None):
    gateway._require_capability("ingest")
    # Linking existing evidence and mandatory readback also require read permission.
    gateway._require_capability("read")
    identity=ReportedIdentity.from_value(provenance)
    if type(cursor) is not int or cursor<0 or type(limit) is not int or not 1<=limit<=128:
        raise GatewayError("INVALID_ARGUMENT","Invalid source batch")
    root=private_inbox(gateway)
    manifest=_json(read_input(root,relative_manifest,expected_manifest_sha256,2*1024*1024))
    if type(manifest) is not dict or set(manifest)!={"schema_version","entries"} or type(manifest["schema_version"]) is not int \
            or manifest["schema_version"]!=1 or type(manifest["entries"]) is not list or len(manifest["entries"])>5000:
        raise GatewayError("INVALID_ARGUMENT","Invalid migration manifest")
    entries=manifest["entries"]
    if cursor>len(entries): raise GatewayError("INVALID_ARGUMENT","Invalid source cursor")
    store=gateway._source_evidence_store()
    results=[]
    for index in range(cursor,min(cursor+limit,len(entries))):
        entry=entries[index]
        try:
            common={"source_system","source_format","record_kind","sha256","byte_count"}
            if type(entry) is not dict or not common<=set(entry) or set(entry)-common not in ({"relative_path"},{"legacy_record_id"},{"document_uri"}) \
                    or type(entry["byte_count"]) is not int or not 0<=entry["byte_count"]<=MAX_SOURCE_BYTES:
                raise GatewayError("INVALID_ARGUMENT","Invalid source entry")
            if "relative_path" in entry:
                raw=read_input(root,entry["relative_path"],entry["sha256"],MAX_SOURCE_BYTES)
                if len(raw)!=entry["byte_count"]: raise GatewayError("CONFLICT","Source byte count conflicts")
                result=store.import_bytes(SourceFileInput(entry["source_system"],entry["source_format"],entry["record_kind"],raw,entry["sha256"]),reported_identity=identity)
            elif "legacy_record_id" in entry:
                result=store.link_legacy(source_system=entry["source_system"],legacy_record_id=entry["legacy_record_id"],
                    source_format=entry["source_format"],record_kind=entry["record_kind"],expected_sha256=entry["sha256"],expected_bytes=entry["byte_count"],reported_identity=identity)
            else:
                if entry["source_format"]!="zip" or entry["record_kind"]!="conversation_export":
                    raise GatewayError("INVALID_ARGUMENT","Invalid archive reference")
                result=link_gemini(gateway,entry,identity)
            proof=store.verify(result["source_id"],manifest_resolver=lambda s,o,n:resolve_manifest(gateway,s,o,n))
            p=result["source"]["write_provenance"]
            results.append({"index":index,"source_id":result["source_id"],"sha256":proof["sha256"],"byte_count":proof["byte_count"],
                            "disposition":"reused" if result["reused"] else "imported","verified":True,
                            "provenance_sha256":_digest(json.dumps(p,sort_keys=True,separators=(",",":")).encode())})
        except GatewayError as error:
            results.append({"index":index,"disposition":"error","error_code":error.code})
    response={"manifest_sha256":expected_manifest_sha256,"cursor":cursor,"next_cursor":cursor+len(results),
              "total_entries":len(entries),"has_more":cursor+len(results)<len(entries),"results":results,
              **{name:sum(r["disposition"]==state for r in results) for name,state in (("imported","imported"),("reused","reused"),("errors","error"))},
              "canonical_messages_created":0,"recorded_at":utc_now()}
    folder=root/"receipts"
    try:
        folder.mkdir(exist_ok=True)
        if _path_has_reparse_point(folder): raise GatewayError("OUTSIDE_ALLOWLIST","Unsafe receipt target")
        name=expected_manifest_sha256+"-"+str(cursor)+"-"+uuid4().hex+".json"
        data=json.dumps(response,sort_keys=True,separators=(",",":")).encode()
        with (folder/name).open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise GatewayError("STORAGE_UNAVAILABLE","Migration receipt could not be persisted") from None
    response["receipt"]={"relative_path":"receipts/"+name,"sha256":_digest(data)}
    return response
