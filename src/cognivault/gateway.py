"""Transport-neutral local application operations."""

import math
import os
import base64
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
from typing import Callable
import unicodedata
from uuid import uuid4

from .adapters.history import NotConfiguredHistoryBackend
from .adapters.documents import (MAX_IMAGE_BYTES, MAX_PDF_BYTES, DocumentInput, SQLiteDocumentStore,
                                 _opened_file_path, _path_has_reparse_point,
                                 _stream_signature)
from .config import AppConfig
from .contracts import (
    BackendHealth,
    BackendStudyHit,
    GatewayError,
    HistoryBackend,
    HistoryItem,
    HistoryPage,
    HistorySource,
    SafeStudyResult,
    StudyBackend,
)
from .policy import safe_relative_source_path
from .provenance import ReportedIdentity


_PUBLIC_BACKEND_MESSAGES = {
    "INVALID_ARGUMENT": "Study request is invalid",
    "STUDY_UNAVAILABLE": "Study search is unavailable",
    "QMD_NOT_FOUND": "Study search executable is unavailable",
    "QMD_RUNTIME_UNSAFE": "Study runtime is unsafe",
    "QMD_VERSION_UNSUPPORTED": "Study search version is unsupported",
    "BACKEND_TIMEOUT": "Study search timed out",
    "BACKEND_BAD_OUTPUT": "Study backend returned invalid results",
    "OUTSIDE_ALLOWLIST": "Study source is outside the allowed root",
    "INTERNAL_ERROR": "Study search failed",
}

_PROJECTION_PROVENANCE_ENUMS = {
    "data_origin": {"source", "deterministic_derived", "agent_generated", "imported", "system_generated"},
    "actor_type": {"external_client", "importer", "system", "unknown"},
    "identity_trust": {"reported", "unavailable"},
    "legacy_status": {"native", "imported", "pre_provenance"},
}
_PROJECTION_SOURCE_REF = re.compile(r"(?:asset|document)://sha256/[0-9a-f]{64}\Z")
_PROJECTION_WRONG_SOURCE = re.compile(r"wrong-answer://sha256/[0-9a-f]{64}\Z")

_ABSOLUTE_PATH_START = re.compile(
    r"(?:[A-Za-z]:[\\/](?![\\/])|(?<![\w:/\\])//(?=[^/\s])|\\\\|"
    r"\\(?=(?:Users|Documents|ProgramData|Program Files|Windows)[\\/])|"
    r"(?<![\w:/\\])/(?!/))"
)
_PATH_TERMINATORS = frozenset("!?\r\n<>[]{}()\"'")


def _redact_local_paths(value: str) -> str:
    """Consume complete absolute-path tokens, including spaces and extensionless names.

    Spaces are part of the path unless followed by an explicit prose connector
    or a URL. Unquoted free text is otherwise ambiguous.
    """
    pieces = []
    position = 0
    while match := _ABSOLUTE_PATH_START.search(value, position):
        pieces.append(value[position:match.start()])
        end = match.end()
        while end < len(value):
            char = value[end]
            if char.isspace():
                following = value[end:].lstrip()
                if following.startswith(("https://", "http://")) \
                        or re.match(r"(?:and|or|then|but)\s+", following, flags=re.IGNORECASE):
                    break
            if char in ",;" and (end + 1 == len(value) or value[end + 1].isspace()):
                break
            if char in _PATH_TERMINATORS or (char == "." and
                                               (end + 1 == len(value) or value[end + 1].isspace())):
                break
            end += 1
        pieces.append("[local path redacted]")
        position = end
    pieces.append(value[position:])
    return "".join(pieces)


class Gateway:
    def __init__(
        self,
        config: AppConfig,
        study_backend: StudyBackend | None,
        history_backend: HistoryBackend | None = None,
        qmd_discoverable: Callable[[], bool] | None = None,
        document_store: SQLiteDocumentStore | None = None,
        memory_store=None,
        capabilities: frozenset[str] = frozenset({"read"}),
    ) -> None:
        if type(capabilities) is not frozenset or not capabilities <= {
            "read", "write", "ingest", "projection", "admin",
        }:
            raise ValueError("Invalid Gateway capabilities")
        self.config = config
        self.study_backend = study_backend
        self.history_backend = history_backend or NotConfiguredHistoryBackend()
        self.qmd_discoverable = qmd_discoverable or (lambda: shutil.which("qmd") is not None)
        self.document_store = document_store
        self.memory_store = memory_store
        self.capabilities = capabilities

    def _require_capability(self, capability: str) -> None:
        if capability not in self.capabilities and "admin" not in self.capabilities:
            raise GatewayError("PERMISSION_DENIED", "Capability is not enabled")

    def _documents(self) -> SQLiteDocumentStore:
        if self.document_store is None:
            raise GatewayError("STORAGE_UNAVAILABLE", "Documents are unavailable")
        return self.document_store

    @staticmethod
    def _document_metadata(record) -> dict:
        return {"document_uri": record.uri, "asset_uri": record.asset_uri,
                "title": Gateway._clean_text(_redact_local_paths(record.title))[:200],
                "media_type": record.media_type, "page_count": record.page_count,
                "text_origin": record.text_origin,
                "text_layer_status": record.text_layer_status}

    @staticmethod
    def _decode_asset(value: str) -> bytes:
        if type(value) is not str or len(value) > 2_796_208:
            raise GatewayError("PAYLOAD_TOO_LARGE", "Asset exceeds size limit")
        try:
            return base64.b64decode(value, validate=True)
        except (ValueError, base64.binascii.Error):
            raise GatewayError("INVALID_ARGUMENT", "Invalid asset encoding") from None

    def register_asset(self, media_type: str, content_base64: str,
                       provenance: dict | None = None) -> dict:
        self._require_capability("ingest")
        identity = ReportedIdentity.from_value(provenance)
        store = self._documents()
        record = store.register_asset(self._decode_asset(content_base64), media_type, identity)
        return {"asset": {"asset_uri": record.uri, "media_type": record.media_type,
                          "size": record.size, "sha256": record.sha256,
                          "write_provenance": store.get_write_provenance("asset", record.uri)}}

    def register_asset_from_file(self, relative_path: str, media_type: str,
                                 provenance: dict | None = None) -> dict:
        """Register an unchanged image from the configured private local inbox."""
        self._require_capability("ingest")
        identity = ReportedIdentity.from_value(provenance)
        configured_root = self.config.asset_ingest_root
        if configured_root is None:
            raise GatewayError("STORAGE_UNAVAILABLE", "Asset file ingestion is not configured")
        if type(media_type) is not str or media_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise GatewayError("UNSUPPORTED_MEDIA_TYPE", "File registration supports image assets only")
        if type(relative_path) is not str or not 1 <= len(relative_path) <= 500 \
                or "\x00" in relative_path or any(ord(char) < 32 for char in relative_path):
            raise GatewayError("INVALID_ARGUMENT", "Invalid asset source")
        windows_path = PureWindowsPath(relative_path)
        posix_path = PurePosixPath(relative_path)
        parts = windows_path.parts
        if windows_path.is_absolute() or posix_path.is_absolute() or windows_path.drive \
                or not parts or any(part in {"", ".", ".."} or ":" in part for part in parts):
            raise GatewayError("INVALID_ARGUMENT", "Invalid asset source")
        root = Path(configured_root)
        candidate = root.joinpath(*parts)
        try:
            if _path_has_reparse_point(root) or _path_has_reparse_point(candidate):
                raise GatewayError("OUTSIDE_ALLOWLIST", "Asset source is outside the allowed root")
            canonical_root = root.resolve(strict=True)
            canonical_file = candidate.resolve(strict=True)
            if not canonical_file.is_relative_to(canonical_root) or not canonical_file.is_file():
                raise GatewayError("OUTSIDE_ALLOWLIST", "Asset source is outside the allowed root")
            with canonical_file.open("rb") as stream:
                opened_path = _opened_file_path(stream)
                if opened_path is None:
                    raise GatewayError("STORAGE_UNAVAILABLE", "Asset source cannot be verified")
                try:
                    opened_path = opened_path.resolve(strict=True)
                except (OSError, RuntimeError):
                    raise GatewayError("OUTSIDE_ALLOWLIST", "Asset source is outside the allowed root") from None
                if not opened_path.is_relative_to(canonical_root):
                    raise GatewayError("OUTSIDE_ALLOWLIST", "Asset source is outside the allowed root")
                before = _stream_signature(stream)
                size = before[2]
                if size <= 0 or size > MAX_IMAGE_BYTES:
                    raise GatewayError("PAYLOAD_TOO_LARGE", "Asset source exceeds size limit")
                content = stream.read(MAX_IMAGE_BYTES + 1)
                if len(content) != size or len(content) > MAX_IMAGE_BYTES \
                        or _stream_signature(stream) != before:
                    raise GatewayError("CONFLICT", "Asset source changed during registration")
        except GatewayError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise GatewayError("RESOURCE_NOT_FOUND", "Asset source was not found") from None
        store = self._documents()
        record = store.register_asset(content, media_type, identity)
        return {"asset": {"asset_uri": record.uri, "media_type": record.media_type,
                          "size": record.size, "sha256": record.sha256,
                          "write_provenance": store.get_write_provenance("asset", record.uri)}}

    def ingest_documents(self, documents: list[dict], provenance: dict | None = None) -> dict:
        self._require_capability("ingest")
        identity = ReportedIdentity.from_value(provenance)
        if type(documents) is not list or not documents:
            raise GatewayError("INVALID_ARGUMENT", "Invalid document batch")
        if len(documents) > 16:
            raise GatewayError("PAYLOAD_TOO_LARGE", "Document batch exceeds limit")
        entries = []
        for raw in documents:
            if type(raw) is not dict or set(raw) - {"title", "media_type", "content_base64", "supplied_pages", "supplied_page_origin"} \
                    or not {"title", "media_type", "content_base64"} <= set(raw):
                raise GatewayError("INVALID_ARGUMENT", "Invalid document")
            pages = raw.get("supplied_pages")
            if pages is not None and (type(pages) is not list or any(type(p) is not str for p in pages)):
                raise GatewayError("INVALID_ARGUMENT", "Invalid supplied pages")
            entries.append(DocumentInput(raw["title"], raw["media_type"],
                                         self._decode_asset(raw["content_base64"]),
                                         tuple(pages) if pages is not None else None,
                                         raw.get("supplied_page_origin")))
        store = self._documents()
        records = store.ingest_documents(entries, identity)
        results = [self._document_metadata(record) for record in records]
        for record, result in zip(records, results, strict=True):
            result["write_provenance"] = store.get_write_provenance("document", record.uri)
        return {"documents": results}

    def ingest_document_file(self, relative_path: str, title: str | None = None,
                             provenance: dict | None = None) -> dict:
        """Ingest one PDF from an explicitly configured local source root."""
        self._require_capability("ingest")
        identity = ReportedIdentity.from_value(provenance)
        configured_root = self.config.asset_ingest_root
        if configured_root is None:
            raise GatewayError("STORAGE_UNAVAILABLE", "Document file ingestion is not configured")
        if type(relative_path) is not str or not 1 <= len(relative_path) <= 500 \
                or "\x00" in relative_path or any(ord(char) < 32 for char in relative_path):
            raise GatewayError("INVALID_ARGUMENT", "Invalid document source")
        windows_path = PureWindowsPath(relative_path)
        posix_path = PurePosixPath(relative_path)
        parts = windows_path.parts
        if windows_path.is_absolute() or posix_path.is_absolute() or windows_path.drive \
                or not parts or any(part in {"", ".", ".."} or ":" in part for part in parts):
            raise GatewayError("INVALID_ARGUMENT", "Invalid document source")
        root = Path(configured_root)
        candidate = root.joinpath(*parts)
        try:
            if _path_has_reparse_point(root) or _path_has_reparse_point(candidate):
                raise GatewayError("OUTSIDE_ALLOWLIST", "Document source is outside the allowed root")
            canonical_root = root.resolve(strict=True)
            canonical_file = candidate.resolve(strict=True)
            if not canonical_file.is_relative_to(canonical_root) or not canonical_file.is_file():
                raise GatewayError("OUTSIDE_ALLOWLIST", "Document source is outside the allowed root")
            with canonical_file.open("rb") as stream:
                opened_path = _opened_file_path(stream)
                if opened_path is None:
                    raise GatewayError("STORAGE_UNAVAILABLE", "Document source cannot be verified")
                try:
                    opened_path = opened_path.resolve(strict=True)
                except (OSError, RuntimeError):
                    raise GatewayError("OUTSIDE_ALLOWLIST", "Document source is outside the allowed root") from None
                if not opened_path.is_relative_to(canonical_root):
                    raise GatewayError("OUTSIDE_ALLOWLIST", "Document source is outside the allowed root")
                before = _stream_signature(stream)
                size = before[2]
                if size <= 0 or size > MAX_PDF_BYTES:
                    raise GatewayError("PAYLOAD_TOO_LARGE", "Document source exceeds size limit")
                content = stream.read(MAX_PDF_BYTES + 1)
                if len(content) != size or len(content) > MAX_PDF_BYTES \
                        or _stream_signature(stream) != before:
                    raise GatewayError("CONFLICT", "Document source changed during ingestion")
        except GatewayError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise GatewayError("RESOURCE_NOT_FOUND", "Document source was not found") from None
        document_title = title if title is not None else canonical_file.stem
        store = self._documents()
        records = store.ingest_documents([
            DocumentInput(document_title, "application/pdf", content),
        ], identity)
        results = [self._document_metadata(record) for record in records]
        for record, result in zip(records, results, strict=True):
            result["write_provenance"] = store.get_write_provenance("document", record.uri)
        return {"documents": results}

    def search_documents(self, query: str, limit: int = 5, offset: int = 0, *,
                         source_ids: list[str] | None = None, page_range: list[int] | None = None) -> dict:
        self._require_capability("read")
        page = self._documents().search(query, limit, offset, source_ids=source_ids, page_range=page_range)
        return {"total": page.total, "has_more": page.has_more,
                "results": [{"chunk_uri": item.uri, "document_uri": item.document_uri,
                             "page_number": item.page_number,
                             "page_uri": f"{item.document_uri}/page/{item.page_number}",
                             "source_asset_uri": item.source_asset_uri,
                             "text_origin": item.text_origin,
                             "visual_resource_uri": (
                                 f"{item.document_uri}/page/{item.page_number}/image"
                                 if item.media_type == "application/pdf" else None
                             ),
                             "section": self._clean_text(_redact_local_paths(item.section))[:200],
                             "snippet": self._clean_text(_redact_local_paths(item.text))[:500]}
                            for item in page.items]}

    def fetch_document(self, document_uri: str) -> dict:
        self._require_capability("read")
        record = self._documents().fetch_document(document_uri)
        if record is None:
            raise GatewayError("RESOURCE_NOT_FOUND", "Document was not found")
        metadata = self._document_metadata(record)
        metadata["write_provenance"] = self._documents().get_write_provenance("document", record.uri)
        return {"document": metadata}

    def fetch_document_page(self, document_uri: str, page_number: int) -> dict:
        self._require_capability("read")
        page = self._documents().fetch_page(document_uri, page_number)
        if page is None:
            raise GatewayError("RESOURCE_NOT_FOUND", "Document page was not found")
        text = _redact_local_paths(page.text)
        encoded = text.encode("utf-8")
        truncated = len(encoded) > 100_000
        if truncated:
            text = encoded[:100_000].decode("utf-8", errors="ignore")
        document = self._documents().fetch_document(document_uri)
        page_provenance = self._documents().get_write_provenance(
            "document_page", f"{page.document_uri}#page={page.page_number}"
        )
        return {"page": {"document_uri": page.document_uri,
                         "source_asset_uri": document.asset_uri if document is not None else None,
                         "page_number": page.page_number,
                         "text": text, "text_origin": page.text_origin,
                         "text_layer_status": page.text_layer_status, "truncated": truncated,
                         "write_provenance": page_provenance}}

    def list_document_ocr_candidates(self, document_uri: str, limit: int = 100, offset: int = 0) -> dict:
        self._require_capability("read")
        if type(limit) is not int or not 1 <= limit <= 999 \
                or type(offset) is not int or not 0 <= offset <= 999:
            raise GatewayError("INVALID_ARGUMENT", "Invalid OCR candidate query")
        store = self._documents()
        document = store.fetch_document(document_uri)
        if document is None:
            raise GatewayError("RESOURCE_NOT_FOUND", "Document was not found")
        pages = store.list_ocr_candidates(document_uri, min(limit + 1, 999), offset)
        has_more = len(pages) > limit
        return {"document_uri": document.uri, "page_numbers": list(pages[:limit]),
                "limit": limit, "offset": offset, "has_more": has_more}

    def process_document_ocr_pages(self, document_uri: str, page_numbers: list[int],
                                   provenance: dict | None = None) -> dict:
        self._require_capability("ingest")
        identity = ReportedIdentity.from_value(provenance)
        store = self._documents()
        record = store.process_ocr_pages(document_uri, page_numbers, identity)
        metadata = self._document_metadata(record)
        metadata["write_provenance"] = store.get_write_provenance("document", record.uri)
        metadata["page_provenance"] = [self._documents().get_write_provenance(
            "document_page", f"{record.uri}#page={number}"
        ) for number in page_numbers]
        return {"document": metadata, "processed_pages": page_numbers}

    def fetch_document_page_image(self, document_uri: str, page_number: int) -> dict:
        self._require_capability("read")
        store = self._documents()
        page = store.fetch_page(document_uri, page_number)
        if page is None:
            raise GatewayError("RESOURCE_NOT_FOUND", "Document page was not found")
        content, width, height, source_asset_uri = store.render_page_image(document_uri, page_number)
        return {"document_uri": document_uri, "page_number": page_number,
                "source_asset_uri": source_asset_uri, "text_origin": page.text_origin,
                "mime_type": "image/png", "width": width, "height": height, "content": content,
                "write_provenance": store.get_write_provenance(
                    "document_page", f"{document_uri}#page={page_number}")}

    def fetch_asset(self, asset_uri: str, offset: int = 0, length: int = 65_536) -> dict:
        self._require_capability("read")
        store = self._documents()
        record = store.fetch_asset_record(asset_uri)
        if record is None:
            raise GatewayError("RESOURCE_NOT_FOUND", "Asset was not found")
        data = store.fetch_asset(asset_uri, offset, length)
        return {"asset_uri": record.uri, "media_type": record.media_type, "size": record.size,
                "offset": offset, "content_base64": base64.b64encode(data).decode("ascii"),
                "has_more": offset + len(data) < record.size,
                "write_provenance": store.get_write_provenance("asset", record.uri)}

    def _wrong_answers(self):
        from .adapters.wrong_answers import SQLiteWrongAnswerStore

        if self.document_store is None:
            raise GatewayError("STORAGE_UNAVAILABLE", "Wrong-answer storage is unavailable")
        return SQLiteWrongAnswerStore(self.document_store)

    @staticmethod
    def _safe_wrong_source(source: dict) -> dict:
        source = dict(source)
        for field in ("question_text", "student_answer"):
            source[field] = Gateway._clean_text(_redact_local_paths(source[field]))[:10_000]
        return source

    def register_wrong_answer_source(self, source_uri: str, question_text: str,
                                     student_answer: str, page_number: int | None = None,
                                     provenance: dict | None = None) -> dict:
        self._require_capability("write")
        identity = ReportedIdentity.from_value(provenance)
        record = self._wrong_answers().register_source(source_uri, question_text,
                                                      student_answer, page_number, identity)
        return {"source": self._safe_wrong_source(record)}

    @staticmethod
    def _validate_study_relation_shape(relations: list[str]) -> list[str]:
        if type(relations) is not list or len(relations) > 16 or any(type(item) is not str for item in relations) \
                or len(set(relations)) != len(relations):
            raise GatewayError("INVALID_ARGUMENT", "Invalid study relations")
        for relation in relations:
            if not relation.startswith("study:") or len(relation) <= 6:
                raise GatewayError("INVALID_ARGUMENT", "Invalid study relation")
            raw_relative = relation[6:]
            candidate = Path(raw_relative)
            windows_candidate = PureWindowsPath(raw_relative)
            if (candidate.is_absolute() or candidate.drive or windows_candidate.is_absolute()
                    or windows_candidate.drive or ".." in candidate.parts or ".." in windows_candidate.parts) \
                    or any(part == "" for part in raw_relative.replace("\\", "/").split("/")):
                raise GatewayError("INVALID_ARGUMENT", "Invalid study relation")
        return relations

    def _validate_study_relations(self, relations: list[str]) -> list[str]:
        self._validate_study_relation_shape(relations)
        if not relations:
            return relations
        root = self.config.study_root
        if root is None or not root.is_dir():
            raise GatewayError("STUDY_UNAVAILABLE", "Study references are unavailable")
        from .policy import safe_relative_source_path
        for relation in relations:
            raw_relative = relation[6:]
            try:
                safe = safe_relative_source_path(root, raw_relative)
            except GatewayError as error:
                if error.code == "OUTSIDE_ALLOWLIST":
                    raise GatewayError("RESOURCE_NOT_FOUND", "Study reference was not found") from None
                raise
            if safe != relation[6:].replace("\\", "/"):
                raise GatewayError("INVALID_ARGUMENT", "Invalid study relation")
        return relations

    def save_wrong_answer_analysis(self, source_id: str, analysis: dict,
                                   source_refs: list[str], study_relations: list[str],
                                   idempotency_key: str, expected_version: int = 0,
                                   provenance: dict | None = None) -> dict:
        self._require_capability("write")
        store = self._wrong_answers()
        identity = ReportedIdentity.from_value(provenance)
        relations = self._validate_study_relation_shape(study_relations)
        record = store.lookup_analysis(source_id, analysis, source_refs,
                                       relations, idempotency_key, expected_version, identity)
        if record is None:
            self._validate_study_relations(relations)
            record = store.write_analysis(source_id, analysis, source_refs,
                                          relations, idempotency_key, expected_version, identity)
        record["reasoning"] = self._history_text(record["reasoning"], 10_000)
        record["correct_solution"] = self._history_text(record["correct_solution"], 10_000)
        record["review_advice"] = self._history_text(record["review_advice"], 10_000)
        record["error_type"] = self._history_text(record["error_type"], 200)
        record["knowledge_points"] = [self._history_text(item, 200) for item in record["knowledge_points"]]
        return {"analysis": record}

    def update_wrong_answer_analysis(self, source_id: str, analysis: dict,
                                     source_refs: list[str], study_relations: list[str],
                                     idempotency_key: str, expected_version: int,
                                     provenance: dict | None = None) -> dict:
        return self.save_wrong_answer_analysis(source_id, analysis, source_refs,
                                               study_relations, idempotency_key, expected_version,
                                               provenance)

    def get_wrong_answer_bundle(self, source_id: str, limit: int = 20, offset: int = 0) -> dict:
        self._require_capability("read")
        source, analyses, total = self._wrong_answers().bundle(source_id, limit, offset)
        safe_analyses = []
        for analysis in analyses:
            for field in ("reasoning", "correct_solution", "review_advice"):
                analysis[field] = self._history_text(analysis[field], 10_000)
            analysis["error_type"] = self._history_text(analysis["error_type"], 200)
            analysis["knowledge_points"] = [self._history_text(item, 200)
                                            for item in analysis["knowledge_points"]]
            safe_analyses.append(analysis)
        return {"source": self._safe_wrong_source(source),
                "analyses": safe_analyses, "total": total,
                "has_more": offset + len(analyses) < total}

    def search_wrong_answers(self, query: str, limit: int = 5, offset: int = 0) -> dict:
        self._require_capability("read")
        results, total = self._wrong_answers().search(query, limit, offset)
        return {"total": total, "has_more": offset + len(results) < total,
                "results": [self._safe_wrong_source(source) for source in results]}

    def projection_snapshot(self, domain: str, operation: str, *,
                            snapshot_token: str | None = None, cursor: int = 0,
                            source_id: str | None = None, limit: int = 20) -> dict:
        """Read a bounded per-store projection page under its opt-in capability."""
        self._require_capability("projection")
        if type(domain) is not str or domain not in {"history", "wrong_answers", "legacy_sources"}:
            raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")
        if domain == "legacy_sources":
            from .adapters.history import SQLiteHistoryBackend
            from .adapters.legacy_sources import SQLiteLegacySourceStore
            backend = self._history_backend()
            if not isinstance(backend, SQLiteHistoryBackend) or source_id is not None:
                raise GatewayError("INVALID_ARGUMENT", "Invalid projection snapshot request")
            page = SQLiteLegacySourceStore(backend.database_path).projection_snapshot(
                operation, snapshot_token=snapshot_token, cursor=cursor, limit=limit)
            if "records" in page:
                safe_records = []
                for record in page["records"]:
                    safe = {key: record[key] for key in ("source_record_id", "source_id", "source_type",
                            "source_order", "source_created_at", "imported_at", "source_refs", "byte_count")}
                    safe["write_provenance"] = {key: record["write_provenance"][key] for key in
                                                ("data_origin", "actor_type", "identity_trust", "legacy_status")}
                    safe_records.append(safe)
                page["records"] = safe_records
            return page
        if domain == "history":
            backend = self._history_backend()
            reader = getattr(backend, "projection_snapshot", None)
            if reader is None:
                raise GatewayError("HISTORY_UNAVAILABLE", "History projection is unavailable")
            page = reader(operation, snapshot_token=snapshot_token, cursor=cursor,
                          source_id=source_id, limit=limit)
            if operation == "records":
                page["items"] = [self._history_item(item, full=True) for item in page["items"]]
            elif operation == "sources":
                for history_source in page["sources"]:
                    history_source["label"] = self._history_text(history_source["label"], 120)
            return page

        page = self._wrong_answers().projection_snapshot(
            operation, snapshot_token=snapshot_token, cursor=cursor,
            source_id=source_id, limit=limit,
        )
        if operation == "sources":
            page["sources"] = [self._safe_projection_source(source) for source in page["sources"]]
        elif operation == "records":
            for analysis in page["analyses"]:
                if type(analysis) is not dict or any(
                        type(analysis.get(field)) is not str
                        for field in ("analysis_id", "source_id", "reasoning", "correct_solution",
                                      "review_advice", "error_type")
                ) or type(analysis.get("version")) is not int \
                        or type(analysis.get("knowledge_points")) is not list:
                    raise GatewayError("BACKEND_BAD_OUTPUT", "Projection analysis is invalid")
                for field in ("reasoning", "correct_solution", "review_advice"):
                    analysis[field] = self._history_text(analysis[field], 10_000)
                analysis["error_type"] = self._history_text(analysis["error_type"], 200)
                analysis["knowledge_points"] = [self._history_text(item, 200)
                                                 for item in analysis["knowledge_points"]]
                refs = analysis.get("source_refs")
                if type(refs) is not list or len(refs) > 16 \
                        or any(type(ref) is not str or not _PROJECTION_SOURCE_REF.fullmatch(ref)
                               for ref in refs):
                    raise GatewayError("BACKEND_BAD_OUTPUT", "Projection source references are invalid")
                try:
                    analysis["study_relations"] = self._validate_study_relation_shape(
                        analysis.get("study_relations"),
                    )
                except GatewayError:
                    raise GatewayError("BACKEND_BAD_OUTPUT", "Projection study references are invalid") from None
                analysis["write_provenance"] = self._projection_provenance(
                    analysis.get("write_provenance"),
                )
        return page

    @classmethod
    def _safe_projection_source(cls, source: dict) -> dict:
        if type(source) is not dict or type(source.get("source_id")) is not str \
                or not _PROJECTION_WRONG_SOURCE.fullmatch(source["source_id"]) \
                or type(source.get("source_uri")) is not str \
                or not _PROJECTION_SOURCE_REF.fullmatch(source["source_uri"]):
            raise GatewayError("BACKEND_BAD_OUTPUT", "Projection source is invalid")
        safe = cls._safe_wrong_source(source)
        safe["write_provenance"] = cls._projection_provenance(source.get("write_provenance"))
        return safe

    @staticmethod
    def _projection_provenance(value: object) -> dict | None:
        """Expose only the categorical provenance fields consumed by the renderer."""
        if value is None:
            return None
        if type(value) is not dict or any(type(value.get(field)) is not str
                                          or value[field] not in allowed
                                          for field, allowed in _PROJECTION_PROVENANCE_ENUMS.items()):
            raise GatewayError("BACKEND_BAD_OUTPUT", "Projection provenance is invalid")
        return {field: value[field] for field in _PROJECTION_PROVENANCE_ENUMS}

    def health_report(self) -> dict:
        self._require_capability("read")
        root = self.config.study_root
        root_exists = root is not None and root.is_dir()
        readable = bool(root_exists and os.access(root, os.R_OK))
        try:
            history = self.history_backend.probe()
        except Exception:
            raise GatewayError("INTERNAL_ERROR", "Health check failed", uuid4().hex) from None
        history_public = {"backend": "unavailable", "status": "unavailable"}
        if (
            type(history) is BackendHealth
            and type(history.backend) is str
            and type(history.status) is str
            and history.backend == "not_configured"
            and history.status == "not_configured"
        ):
            history_public = {"backend": "not_configured", "status": "not_configured"}
        elif (
            type(history) is BackendHealth and history.backend == "sqlite"
            and history.status in {"ready", "unavailable"}
        ):
            history_public = {"backend": "sqlite", "status": history.status}
        try:
            qmd_found = bool(self.qmd_discoverable())
        except Exception:
            qmd_found = False
        return {
            "gateway_version": self.config.gateway_version,
            "study": {"configured": root is not None, "root_exists": root_exists, "readable": readable},
            "history": history_public,
            "qmd": {"discoverable": qmd_found},
        }

    def _history_backend(self) -> HistoryBackend:
        if isinstance(self.history_backend, NotConfiguredHistoryBackend):
            raise GatewayError("HISTORY_UNAVAILABLE", "History is unavailable")
        return self.history_backend

    def _legacy_source_store(self):
        from .adapters.history import SQLiteHistoryBackend
        from .adapters.legacy_sources import SQLiteLegacySourceStore
        self._require_capability("read")
        if not isinstance(self._history_backend(), SQLiteHistoryBackend):
            raise GatewayError("HISTORY_UNAVAILABLE", "History is unavailable")
        return SQLiteLegacySourceStore(self.history_backend.database_path)

    def list_legacy_sources(self) -> dict:
        return {"sources": list(self._legacy_source_store().list_sources())}

    def _source_evidence_store(self):
        from .adapters.history import SQLiteHistoryBackend
        from .adapters.history_sources import SourceEvidenceStore
        if not isinstance(self._history_backend(), SQLiteHistoryBackend):
            raise GatewayError("HISTORY_UNAVAILABLE", "History is unavailable")
        return SourceEvidenceStore(self.history_backend.database_path)

    def ingest_history_sources(self, relative_manifest, expected_manifest_sha256, *, cursor=0, limit=128, provenance=None):
        from .migration.gateway_sources import ingest
        return ingest(self,relative_manifest,expected_manifest_sha256,cursor=cursor,limit=limit,provenance=provenance)

    def history_source_summary(self):
        self._require_capability("read")
        return self._source_evidence_store().summary()

    def _canonical_history_store(self):
        from .adapters.canonical_history import CanonicalHistoryStore
        return CanonicalHistoryStore(self._source_evidence_store().database_path)

    def history_normalization_snapshot(self):
        self._require_capability("read")
        return self._canonical_history_store().snapshot()

    def normalize_history_sources(self, source_set_sha256, *, cursor=0, limit=16):
        from .normalization.execution import normalize
        return normalize(self,source_set_sha256,cursor=cursor,limit=limit)

    def canonical_history_summary(self):
        self._require_capability("read")
        return self._canonical_history_store().summary()

    def recovery_snapshot_plan(self):
        from .recovery.service import plan
        return plan(self)

    def recovery_snapshot_status(self, snapshot_key, expected_manifest_sha256=None):
        from .recovery.status import status
        return status(self, snapshot_key, expected_manifest_sha256)

    def create_recovery_snapshot(self, snapshot_key):
        from .recovery.service import create
        return create(self, snapshot_key)

    def verify_recovery_snapshot(self, snapshot_key, *, restore=False):
        from .recovery.service import verify
        return verify(self, snapshot_key, restore=restore)

    def verify_canonical_history(self, *, reparse=False):
        from .normalization.integrity import verify
        return verify(self,reparse=reparse)

    def search_canonical_conversations(self, *, source_system=None, query="", limit=20, offset=0):
        self._require_capability("read")
        return self._canonical_history_store().search(source_system=source_system,query=query,limit=limit,offset=offset)

    def fetch_canonical_conversation(self, conversation_id, *, offset=0, limit=50, view_offset=0, view_limit=20, node_offset=0, node_limit=50):
        self._require_capability("read")
        return self._canonical_history_store().conversation(conversation_id,offset=offset,limit=limit,view_offset=view_offset,view_limit=view_limit,node_offset=node_offset,node_limit=node_limit)

    def fetch_canonical_message(self, message_id, *, offset=0, length=65536, evidence_offset=0, evidence_limit=20):
        self._require_capability("read")
        return self._canonical_history_store().message(message_id,offset=offset,length=length,evidence_offset=evidence_offset,evidence_limit=evidence_limit)

    def search_history_sources(self, *, source_system=None, query="", limit=20, offset=0):
        self._require_capability("read")
        return self._source_evidence_store().search(source_system=source_system,query=query,limit=limit,offset=offset)

    def fetch_history_source(self, source_id, offset=0, length=65536):
        from .migration.gateway_sources import resolve_manifest
        self._require_capability("read")
        r=self._source_evidence_store().fetch(source_id,offset,length,manifest_resolver=lambda s,o,n:resolve_manifest(self,s,o,n))
        return {"source":r["source"],"offset":r["offset"],"content_base64":base64.b64encode(r["content"]).decode("ascii"),"has_more":r["has_more"]}

    def verify_history_source(self, source_id):
        from .migration.gateway_sources import resolve_manifest
        self._require_capability("read")
        return self._source_evidence_store().verify(source_id,manifest_resolver=lambda s,o,n:resolve_manifest(self,s,o,n))

    @staticmethod
    def _public_legacy_source(record: dict) -> dict:
        result = dict(record)
        result["source_item_id"] = _redact_local_paths(result["source_item_id"])
        if "snippet" in result:
            result["snippet"] = _redact_local_paths(result["snippet"])
        return result

    def search_legacy_sources(self, query: str, *, source_id: str | None = None,
                              limit: int = 5, offset: int = 0) -> dict:
        result = self._legacy_source_store().search(query, source_id, limit, offset)
        return {**result, "results": [self._public_legacy_source(row) for row in result["results"]]}

    def fetch_legacy_source(self, source_record_id: str, offset: int = 0, length: int = 16384) -> dict:
        return {"source": self._public_legacy_source(
            self._legacy_source_store().fetch(source_record_id, offset, length))}

    @staticmethod
    def _history_text(value: str, max_length: int) -> str:
        if type(value) is not str:
            raise GatewayError("BACKEND_BAD_OUTPUT", "History backend returned invalid results")
        return Gateway._clean_text(_redact_local_paths(value))[:max_length]

    @staticmethod
    def _history_item(item: HistoryItem, *, full: bool) -> dict:
        if type(item) is not HistoryItem or not re.fullmatch(r"history:[0-9a-f]{64}", item.item_id):
            raise GatewayError("BACKEND_BAD_OUTPUT", "History backend returned invalid results")
        if any(type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value)
               for value in (item.source_id, item.source_item_id, item.conversation_id)):
            raise GatewayError("BACKEND_BAD_OUTPUT", "History backend returned invalid results")
        if item.role not in {"user", "assistant", "system", "tool"} \
                or type(item.created_at) is not str \
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", item.created_at) \
                or type(item.content_sha256) is not str \
                or not re.fullmatch(r"[0-9a-f]{64}", item.content_sha256):
            raise GatewayError("BACKEND_BAD_OUTPUT", "History backend returned invalid results")
        for value, pattern in (
            (item.imported_at, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z"),
            (item.source_system, r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}"),
            (item.import_batch_id, r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}"),
        ):
            if value is not None and (type(value) is not str or not re.fullmatch(pattern, value)):
                raise GatewayError("BACKEND_BAD_OUTPUT", "History backend returned invalid results")
        provenance = item.write_provenance
        if provenance is not None:
            fields = {"provenance_id", "record_type", "record_id", "version", "data_origin",
                      "actor_type", "identity_trust", "reported_agent", "reported_client", "run_id",
                      "source_refs", "recorded_at", "supersedes_provenance_id", "original_created_at",
                      "imported_at", "source_system", "import_batch_id", "legacy_status"}
            if type(provenance) is not dict or set(provenance) != fields \
                    or provenance["record_type"] != "history_item" \
                    or provenance["record_id"] != item.item_id or provenance["version"] != 1 \
                    or provenance["data_origin"] != "imported" \
                    or provenance["actor_type"] not in {"importer", "unknown"} \
                    or provenance["identity_trust"] not in {"reported", "unavailable"} \
                    or provenance["legacy_status"] not in {"native", "imported", "pre_provenance"} \
                    or provenance["original_created_at"] != item.created_at \
                    or provenance["supersedes_provenance_id"] is not None:
                raise GatewayError("BACKEND_BAD_OUTPUT", "History backend returned invalid results")
            if not re.fullmatch(r"provenance:[0-9a-f]{32}", str(provenance["provenance_id"])) \
                    or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z",
                                        str(provenance["recorded_at"])):
                raise GatewayError("BACKEND_BAD_OUTPUT", "History backend returned invalid results")
            refs = provenance["source_refs"]
            if type(refs) is not list or refs != [f"history-source://{item.source_id}"]:
                raise GatewayError("BACKEND_BAD_OUTPUT", "History backend returned invalid results")
            for key in ("reported_agent", "reported_client", "run_id"):
                value = provenance[key]
                if value is not None and (type(value) is not str or not 1 <= len(value) <= 120
                        or any(ord(char) < 32 or char in "/\\" for char in value)):
                    raise GatewayError("BACKEND_BAD_OUTPUT", "History backend returned invalid results")
            if (provenance["imported_at"], provenance["source_system"], provenance["import_batch_id"]) != \
                    (item.imported_at, item.source_system, item.import_batch_id):
                raise GatewayError("BACKEND_BAD_OUTPUT", "History backend returned invalid results")
        result = {
            "item_id": item.item_id, "source_id": item.source_id,
            "source_item_id": item.source_item_id, "conversation_id": item.conversation_id,
            "role": item.role, "created_at": item.created_at,
            "content_sha256": item.content_sha256,
            "imported_at": item.imported_at, "source_system": item.source_system,
            "import_batch_id": item.import_batch_id,
            "write_provenance": item.write_provenance,
        }
        content = Gateway._history_text(item.content, 100_000 if full else 500)
        result["content" if full else "snippet"] = content
        if not full:
            result["truncated"] = len(item.content) > 500
        return result

    def list_history_sources(self) -> dict:
        self._require_capability("read")
        backend = self._history_backend()
        try:
            sources = backend.list_sources()
            if not isinstance(sources, (tuple, list)) or len(sources) > 10_000:
                raise GatewayError("BACKEND_BAD_OUTPUT", "History backend returned invalid results")
            safe = []
            for source in sources:
                if type(source) is not HistorySource or type(source.source_id) is not str \
                        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", source.source_id) \
                        or type(source.item_count) is not int or source.item_count < 0:
                    raise GatewayError("BACKEND_BAD_OUTPUT", "History backend returned invalid results")
                safe.append({"source_id": source.source_id,
                             "label": self._history_text(source.label, 120),
                             "item_count": source.item_count})
            return {"sources": safe}
        except GatewayError:
            raise
        except Exception:
            raise GatewayError("INTERNAL_ERROR", "History operation failed", uuid4().hex) from None

    def search_history(self, query: str, *, source_id: str | None = None,
                       conversation_id: str | None = None, limit: int = 5,
                       offset: int = 0) -> dict:
        self._require_capability("read")
        from .adapters.history import valid_logical_id

        backend = self._history_backend()
        if type(query) is not str or not 1 <= len(query.strip()) <= 500 or "\x00" in query \
                or type(limit) is not int or not 1 <= limit <= 20 \
                or type(offset) is not int or not 0 <= offset <= 1000 \
                or (source_id is not None and not valid_logical_id(source_id)) \
                or (conversation_id is not None and not valid_logical_id(conversation_id)):
            raise GatewayError("INVALID_ARGUMENT", "Invalid History search")
        try:
            page = backend.search(query, source_id=source_id,
                                  conversation_id=conversation_id, limit=limit, offset=offset)
            if type(page) is not HistoryPage or type(page.total) is not int or page.total < 0 \
                    or type(page.has_more) is not bool or not isinstance(page.items, tuple) \
                    or len(page.items) > limit:
                raise GatewayError("BACKEND_BAD_OUTPUT", "History backend returned invalid results")
            return {"total": page.total, "has_more": page.has_more,
                    "results": [self._history_item(item, full=False) for item in page.items]}
        except GatewayError:
            raise
        except Exception:
            raise GatewayError("INTERNAL_ERROR", "History operation failed", uuid4().hex) from None

    def fetch_history_item(self, item_id: str) -> dict:
        self._require_capability("read")
        backend = self._history_backend()
        if type(item_id) is not str or not re.fullmatch(r"history:[0-9a-f]{64}", item_id):
            raise GatewayError("INVALID_ARGUMENT", "Invalid History item ID")
        try:
            item = backend.fetch(item_id)
            if item is None:
                raise GatewayError("RESOURCE_NOT_FOUND", "History item was not found")
            return {"item": self._history_item(item, full=True)}
        except GatewayError:
            raise
        except Exception:
            raise GatewayError("INTERNAL_ERROR", "History operation failed", uuid4().hex) from None

    def search_study(self, query: str, limit: int = 5) -> dict:
        self._require_capability("read")
        if not isinstance(query, str):
            raise GatewayError("INVALID_ARGUMENT", "Query must be text")
        query = query.strip()
        if not 1 <= len(query) <= 500 or "\x00" in query:
            raise GatewayError("INVALID_ARGUMENT", "Query length or content is invalid")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise GatewayError("INVALID_ARGUMENT", "Limit must be an integer from 1 to 20")

        root = self.config.study_root
        if root is None or not root.is_dir() or not os.access(root, os.R_OK) or self.study_backend is None:
            raise GatewayError("STUDY_UNAVAILABLE", "Study search is unavailable")
        try:
            try:
                raw_hits = self.study_backend.search(query, limit)
            except GatewayError as exc:
                correlation_id = uuid4().hex if exc.code == "INTERNAL_ERROR" else None
                raise GatewayError(exc.code, _PUBLIC_BACKEND_MESSAGES[exc.code], correlation_id) from None
            if not isinstance(raw_hits, (list, tuple)) or len(raw_hits) > limit:
                raise GatewayError("BACKEND_BAD_OUTPUT", "Study backend returned invalid results")
            backend_name = self.study_backend.name
            if not isinstance(backend_name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", backend_name):
                raise GatewayError("BACKEND_BAD_OUTPUT", "Study backend returned invalid results")
            safe_hits = [self._safe_hit(root, hit, backend_name) for hit in raw_hits]
        except GatewayError:
            raise
        except TimeoutError:
            raise GatewayError("BACKEND_TIMEOUT", "Study search timed out") from None
        except FileNotFoundError:
            raise GatewayError("QMD_NOT_FOUND", "Study search executable is unavailable") from None
        except Exception:
            raise GatewayError("INTERNAL_ERROR", "Study search failed", uuid4().hex) from None
        return {
            "backend": backend_name,
            "truncated": any(hit.truncated for hit in safe_hits),
            "results": [hit.to_dict() for hit in safe_hits],
        }

    def _safe_hit(self, root: Path, hit: BackendStudyHit, backend_name: str) -> SafeStudyResult:
        if not isinstance(hit, BackendStudyHit):
            raise GatewayError("BACKEND_BAD_OUTPUT", "Study backend returned invalid results")
        if not isinstance(hit.title, str) or not isinstance(hit.snippet, str):
            raise GatewayError("BACKEND_BAD_OUTPUT", "Study backend returned invalid results")
        score = hit.score
        numeric_score = None
        if score is not None:
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise GatewayError("BACKEND_BAD_OUTPUT", "Study backend returned invalid results")
            try:
                numeric_score = float(score)
            except (OverflowError, TypeError, ValueError):
                raise GatewayError("BACKEND_BAD_OUTPUT", "Study backend returned invalid results") from None
            if not math.isfinite(numeric_score):
                raise GatewayError("BACKEND_BAD_OUTPUT", "Study backend returned invalid results")
        relative = safe_relative_source_path(root, hit.source_path)
        title = self._clean_text(_redact_local_paths(hit.title))[:200]
        snippet = self._clean_text(_redact_local_paths(hit.snippet))
        truncated = len(snippet) > 500
        return SafeStudyResult(
            title=title,
            snippet=snippet[:500],
            source_id=f"study:{relative}",
            source_path=relative,
            retrieval_backend=backend_name,
            score=numeric_score,
            truncated=truncated,
        )

    @staticmethod
    def _clean_text(value: str) -> str:
        return " ".join("".join(" " if unicodedata.category(char).startswith("C") else char for char in value).split())

    def create_memory(self, subject, predicate, value, source_refs, idempotency_key, *, epistemic_status="unverified", verification_note=None, provenance=None):
        from .memory_service import execute
        return execute(self, "create", dict(subject=subject, predicate=predicate, value=value, source_refs=source_refs, idempotency_key=idempotency_key, epistemic_status=epistemic_status, verification_note=verification_note, provenance=provenance))

    def revise_memory(self, memory_id, value, source_refs, expected_version, idempotency_key, *, epistemic_status="unverified", verification_note=None, provenance=None):
        from .memory_service import execute
        return execute(self, "revise", dict(memory_id=memory_id, value=value, source_refs=source_refs, expected_version=expected_version, idempotency_key=idempotency_key, epistemic_status=epistemic_status, verification_note=verification_note, provenance=provenance))

    def retire_memory(self, memory_id, value, source_refs, expected_version, idempotency_key, *, provenance=None):
        from .memory_service import execute
        return execute(self, "retire", dict(memory_id=memory_id, value=value, source_refs=source_refs, expected_version=expected_version, idempotency_key=idempotency_key, provenance=provenance))

    def fetch_memory(self, memory_id, *, version=None):
        from .memory_service import execute
        return execute(self, "fetch", dict(memory_id=memory_id, version=version))

    def search_memory(self, query, limit=5, offset=0):
        from .memory_service import execute
        return execute(self, "search", dict(query=query, limit=limit, offset=offset))

    def memory_versions(self, memory_id, limit=5, offset=0):
        from .memory_service import execute
        return execute(self, "versions", dict(memory_id=memory_id, limit=limit, offset=offset))

    def retrieve_evidence(self, queries, limit=3):
        from .evidence import retrieve
        return retrieve(self, queries, limit)

    def search_canonical_messages(self, query, limit=5, offset=0):
        self._require_capability("read")
        return self._canonical_history_store().search_messages(query, limit, offset)
