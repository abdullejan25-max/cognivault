"""Explicit local asset and document registry with deterministic text extraction."""

import hashlib
import contextvars
from io import BytesIO
from contextlib import closing, contextmanager
import os
from pathlib import Path
import re
import sqlite3
import stat
import unicodedata
from threading import RLock
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from pypdf import PdfReader, apply_configuration
from pypdf.errors import LimitReachedError
from pypdf.generic import (ArrayObject, ContentStream, DecodedStreamObject, DictionaryObject,
                           EncodedStreamObject, IndirectObject)
import pypdf._page as pypdf_page

from ..contracts import GatewayError
from ..provenance import (ReportedIdentity, ensure_provenance_schema, get_provenance,
                          insert_provenance)


MAX_ASSET_BYTES = 2_097_152
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_PAGE_TEXT = 100_000
MAX_TOTAL_TEXT = 1_000_000
MAX_PAGES = 999
MAX_OCR_PAGES = 8
MAX_OCR_PAGES_PER_BATCH = 16
MAX_OCR_PIXELS = 12_000_000
MAX_RENDER_PIXELS = 6_000_000
MAX_RENDER_BYTES = 4 * 1024 * 1024
PAGE_RENDER_DPI = 144
MAX_PDF_STREAM_BYTES = 1_000_000
MAX_PDF_FONT_STREAM_BYTES = 4_000_000
MAX_PDF_DECODED_BYTES = 32_000_000
MAX_PDF_FORM_EXPANSION_BYTES = 32_000_000
MAX_PDF_PAGE_TREE_ENTRIES = MAX_PAGES * 32
MAX_XFORM_INVOCATIONS = 64
MAX_PDF_RESOURCE_VISITS = 2_048
MAX_BATCH = 16
MAX_FETCH_BYTES = 65_536
_MEDIA = frozenset({"text/plain", "application/pdf", "image/png", "image/jpeg", "image/webp", "application/octet-stream"})
_PDF_LOG_CONTEXT: contextvars.ContextVar[object | None] = contextvars.ContextVar("pdf_log_context", default=None)
_PDF_DECODE_BUDGET: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "pdf_decode_budget", default=None
)
_PDF_DECODED_STREAM_ORIGIN: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "pdf_decoded_stream_origin", default=None
)
_PDF_LOG_LOCK = RLock()


_DOC_URI = re.compile(r"document://sha256/[0-9a-f]{64}\Z")
_ASSET_URI = re.compile(r"asset://sha256/[0-9a-f]{64}\Z")


class _PdfDecodedStreamBudget:
    def __init__(self) -> None:
        self.streams: dict[int, object] = {}
        self.decoded_bytes = 0


_ORIGINAL_DECODED_STREAM_GET_DATA = DecodedStreamObject.get_data
_ORIGINAL_ENCODED_STREAM_GET_DATA = EncodedStreamObject.get_data


# PdfReader decodes XRef and object streams during construction, before the
# page/Form/font preflight can inspect them. Count both encoded and already-
# decoded streams so early XRef/ObjStm bytes share the aggregate budget too.
def _record_pdf_decoded_stream(stream, size: int) -> None:
    budget = _PDF_DECODE_BUDGET.get()
    if not isinstance(budget, _PdfDecodedStreamBudget) or id(stream) in budget.streams:
        return
    if budget.decoded_bytes + size > MAX_PDF_DECODED_BYTES:
        raise LimitReachedError("PDF decoded streams exceed total size limit")
    budget.streams[id(stream)] = stream
    budget.decoded_bytes += size


def _bounded_decoded_stream_get_data(stream) -> bytes:
    data = _ORIGINAL_DECODED_STREAM_GET_DATA(stream)
    origin = _PDF_DECODED_STREAM_ORIGIN.get()
    # Count an encoded stream by its stable source identity, but only when the
    # decoded object is that stream's own cached/returned expansion. Nested
    # decoded streams (for example JBIG2Globals) retain their own identities.
    accounting_stream = origin if origin is not None and origin.decoded_self is stream else stream
    _record_pdf_decoded_stream(accounting_stream, len(data))
    return data


def _bounded_encoded_stream_get_data(stream) -> bytes:
    token = _PDF_DECODED_STREAM_ORIGIN.set(stream)
    try:
        return _ORIGINAL_ENCODED_STREAM_GET_DATA(stream)
    finally:
        _PDF_DECODED_STREAM_ORIGIN.reset(token)


DecodedStreamObject.get_data = _bounded_decoded_stream_get_data
EncodedStreamObject.get_data = _bounded_encoded_stream_get_data


@contextmanager
def _pdf_decode_budget():
    budget = _PdfDecodedStreamBudget()
    token = _PDF_DECODE_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _PDF_DECODE_BUDGET.reset(token)


def _is_reparse_point(path: Path) -> bool:
    """Detect symlinks and Windows junction/mount-point reparse attributes."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _path_has_reparse_point(path: Path) -> bool:
    current = Path(path.anchor)
    if _is_reparse_point(current):
        return True
    for part in path.parts[1:]:
        current = current / part
        if _is_reparse_point(current):
            return True
    return False


def _audit(con: sqlite3.Connection, operation: str, resource_type: str,
           resource_id: str, outcome: str = "success") -> None:
    occurred_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    con.execute("INSERT INTO operation_audit(operation, resource_type, resource_id, occurred_at, outcome) "
                "VALUES (?, ?, ?, ?, ?)", (operation, resource_type, resource_id, occurred_at, outcome))


def _file_signature(info) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size,
            getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000)),
            getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000)))


def _stream_signature(stream) -> tuple[int, ...]:
    info = os.fstat(stream.fileno())
    signature = _file_signature(info)
    if os.name == "nt":
        import ctypes
        import msvcrt

        class FileBasicInfo(ctypes.Structure):
            _fields_ = [("CreationTime", ctypes.c_longlong), ("LastAccessTime", ctypes.c_longlong),
                        ("LastWriteTime", ctypes.c_longlong), ("ChangeTime", ctypes.c_longlong),
                        ("FileAttributes", ctypes.c_ulong)]

        basic = FileBasicInfo()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        get_basic_info = kernel.GetFileInformationByHandleEx
        get_basic_info.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        get_basic_info.restype = ctypes.c_int
        ok = get_basic_info(
            ctypes.c_void_p(msvcrt.get_osfhandle(stream.fileno())), 0,
            ctypes.byref(basic), ctypes.sizeof(basic),
        )
        return (*signature, int(basic.ChangeTime) if ok else None)
    return (*signature, info.st_ctime_ns if hasattr(info, "st_ctime_ns") else int(info.st_ctime * 1e9))


def _opened_file_path(stream) -> Path | None:
    """Return the path resolved by the already-open OS file handle, when supported."""
    if os.name == "nt":
        import ctypes
        import msvcrt

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        get_final_path = kernel.GetFinalPathNameByHandleW
        get_final_path.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        get_final_path.restype = ctypes.c_uint32
        handle = ctypes.c_void_p(msvcrt.get_osfhandle(stream.fileno()))
        buffer = ctypes.create_unicode_buffer(32_768)
        length = get_final_path(handle, buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            return None
        result = buffer.value
        if result.startswith("\\\\?\\UNC\\"):
            result = "\\\\" + result[8:]
        elif result.startswith("\\\\?\\"):
            result = result[4:]
        return Path(result)
    if os.name == "posix":
        handle_link = Path(f"/proc/self/fd/{stream.fileno()}")
        if handle_link.exists():
            return Path(os.readlink(handle_link))
    return None


@dataclass(frozen=True)
class DocumentInput:
    title: str
    media_type: str
    content: bytes
    supplied_pages: tuple[str, ...] | None = None
    supplied_page_origin: str | None = None


@dataclass(frozen=True)
class AssetRecord:
    uri: str
    media_type: str
    size: int
    sha256: str


@dataclass(frozen=True)
class DocumentRecord:
    uri: str
    asset_uri: str
    title: str
    media_type: str
    page_count: int
    text_origin: str

    @property
    def text_layer_status(self) -> str:
        return _text_layer_status(self.text_origin)


@dataclass(frozen=True)
class PageRecord:
    document_uri: str
    page_number: int
    text: str
    text_origin: str

    @property
    def text_layer_status(self) -> str:
        return _text_layer_status(self.text_origin)


@dataclass(frozen=True)
class ChunkRecord:
    uri: str
    document_uri: str
    page_number: int
    section: str
    text: str
    source_asset_uri: str | None = None
    text_origin: str | None = None
    media_type: str | None = None


@dataclass(frozen=True)
class SearchPage:
    items: tuple[ChunkRecord, ...]
    total: int
    has_more: bool


def _validate_asset(data: bytes, media_type: str) -> tuple[str, str]:
    if type(data) is not bytes or not data:
        raise GatewayError("INVALID_ARGUMENT", "Invalid asset")
    if type(media_type) is not str or media_type not in _MEDIA:
        raise GatewayError("UNSUPPORTED_MEDIA_TYPE", "Media type is unsupported")
    size_limit = (MAX_PDF_BYTES if media_type == "application/pdf" else
                  MAX_IMAGE_BYTES if media_type in {"image/png", "image/jpeg", "image/webp"} else
                  MAX_ASSET_BYTES)
    if len(data) > size_limit:
        raise GatewayError("PAYLOAD_TOO_LARGE", "Asset exceeds size limit")
    if media_type == "text/plain":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise GatewayError("INVALID_ARGUMENT", "Invalid UTF-8 text") from None
        if b"\x00" in data:
            raise GatewayError("INVALID_ARGUMENT", "Invalid UTF-8 text")
    if media_type == "application/pdf" and not data.startswith(b"%PDF-"):
        raise GatewayError("INVALID_ARGUMENT", "Invalid PDF header")
    if media_type == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise GatewayError("INVALID_ARGUMENT", "Invalid image header")
    if media_type == "image/jpeg" and not data.startswith(b"\xff\xd8"):
        raise GatewayError("INVALID_ARGUMENT", "Invalid image header")
    if media_type == "image/webp" and not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        raise GatewayError("INVALID_ARGUMENT", "Invalid image header")
    digest = hashlib.sha256(data).hexdigest()
    return digest, "asset://sha256/" + digest


def _ocr_pdf_page(content: bytes, page_number: int) -> str:
    """Run optional local Tesseract OCR for one 1-based page; never interpret its text."""
    try:
        import pymupdf
    except ImportError:
        raise GatewayError("UNSUPPORTED_MEDIA_TYPE", "PDF OCR support is not installed") from None
    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            page = document[page_number - 1]
            pixels = page.rect.width * page.rect.height * (300 / 72) ** 2
            if pixels > MAX_OCR_PIXELS:
                raise GatewayError("PAYLOAD_TOO_LARGE", "OCR page exceeds size limit")
            textpage = page.get_textpage_ocr(language="chi_sim+eng", dpi=300, full=True,
                                             tessdata=str(_tessdata_path(pymupdf)))
            return page.get_text("text", textpage=textpage)
    except GatewayError:
        raise
    except Exception:
        raise GatewayError("INVALID_ARGUMENT", "PDF page OCR could not be completed") from None


def _ocr_pdf_pages(content: bytes, page_numbers: tuple[int, ...]) -> dict[int, str]:
    """OCR a bounded batch while opening and parsing the PDF only once."""
    try:
        import pymupdf
    except ImportError:
        raise GatewayError("UNSUPPORTED_MEDIA_TYPE", "PDF OCR support is not installed") from None
    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            if document.needs_pass and not document.authenticate(""):
                raise GatewayError("UNSUPPORTED_MEDIA_TYPE", "PDF requires an unavailable password")
            results = {}
            tessdata = str(_tessdata_path(pymupdf))
            for number in page_numbers:
                page = document[number - 1]
                pixels = page.rect.width * page.rect.height * (300 / 72) ** 2
                if pixels > MAX_OCR_PIXELS:
                    raise GatewayError("PAYLOAD_TOO_LARGE", "OCR page exceeds size limit")
                textpage = page.get_textpage_ocr(language="chi_sim+eng", dpi=300,
                                                 full=True, tessdata=tessdata)
                results[number] = page.get_text("text", textpage=textpage)
            return results
    except GatewayError:
        raise
    except Exception:
        raise GatewayError("INVALID_ARGUMENT", "PDF page OCR could not be completed") from None


def _tessdata_path(pymupdf) -> Path:
    configured = os.environ.get("TESSDATA_PREFIX")
    return Path(configured) if configured else Path(pymupdf.get_tessdata())


def _ocr_runtime_available() -> bool:
    """Check PyMuPDF and language data; a Tesseract CLI on PATH is not required."""
    try:
        import pymupdf
    except ImportError:
        return False
    try:
        tessdata = _tessdata_path(pymupdf)
        return all((tessdata / f"{language}.traineddata").is_file()
                   for language in ("eng", "chi_sim"))
    except Exception:
        return False


def _resolve_pdf_object(value):
    seen: set[int] = set()
    for _ in range(64):
        if not hasattr(value, "get_object"):
            return value
        if id(value) in seen:
            raise GatewayError("INVALID_ARGUMENT", "Cyclic PDF references are unsupported")
        seen.add(id(value))
        resolved = value.get_object()
        if resolved is value:
            return value
        value = resolved
    raise GatewayError("INVALID_ARGUMENT", "PDF reference chain exceeds limit")


def _inherited_pdf_resources(value):
    if hasattr(value, "get_inherited"):
        return value.get_inherited(key="/Resources", default=None)
    return value.get("/Resources") if hasattr(value, "get") else None


def _normalize_pdf_font_objects(reader) -> None:
    """Resolve bounded indirect font-metric members before pypdf text extraction.

    pypdf currently assumes array members in some valid font dictionaries are
    direct objects. This is particularly visible for /W, /Widths and
    /Encoding /Differences emitted by document generators.
    """
    visited_values: set[int] = set()
    visited_resources: set[int] = set()
    visits = [0]

    def normalize(value, depth: int = 0):
        visits[0] += 1
        if visits[0] > 100_000 or depth > 48:
            raise GatewayError("PAYLOAD_TOO_LARGE", "PDF font structure exceeds size limit")
        if isinstance(value, IndirectObject):
            return normalize(value.get_object(), depth + 1)
        if id(value) in visited_values:
            return value
        if isinstance(value, ArrayObject):
            visited_values.add(id(value))
            for index, member in enumerate(value):
                resolved = normalize(member, depth + 1)
                if resolved is not member:
                    value[index] = resolved
        elif isinstance(value, DictionaryObject) and not hasattr(value, "get_data"):
            visited_values.add(id(value))
            for key in tuple(value.keys()):
                member = value[key]
                resolved = normalize(member, depth + 1)
                if resolved is not member:
                    value[key] = resolved
        return value

    def visit_resources(value, depth: int = 0) -> None:
        if value is None:
            return
        resources = _resolve_pdf_object(value)
        if id(resources) in visited_resources:
            return
        if depth > 32 or len(visited_resources) >= MAX_PDF_RESOURCE_VISITS:
            raise GatewayError("PAYLOAD_TOO_LARGE", "PDF font resources exceed size limit")
        visited_resources.add(id(resources))
        fonts = _resolve_pdf_object(resources.get("/Font"))
        if hasattr(fonts, "values"):
            for font_ref in fonts.values():
                font = _resolve_pdf_object(font_ref)
                normalize(font)
        xobjects = _resolve_pdf_object(resources.get("/XObject"))
        if hasattr(xobjects, "values"):
            for reference in xobjects.values():
                obj = _resolve_pdf_object(reference)
                if hasattr(obj, "get") and obj.get("/Subtype") == "/Form":
                    visit_resources(_inherited_pdf_resources(obj), depth + 1)

    for page in reader.pages:
        if hasattr(page, "get") or hasattr(page, "get_inherited"):
            visit_resources(_inherited_pdf_resources(page))


def _preflight_pdf_streams(reader) -> tuple[bool, ...]:
    """Bound decoded page/Form/font streams before extraction can cache or expand them."""
    seen: set[int] = set()
    stream_sizes: dict[int, int] = {}
    page_form_overflow: list[bool] = []

    def count_stream(stream, maximum: int = MAX_PDF_STREAM_BYTES) -> int:
        stream = _resolve_pdf_object(stream)
        if not hasattr(stream, "get_data"):
            return 0
        if id(stream) in seen:
            size = stream_sizes[id(stream)]
            if size > maximum:
                raise GatewayError("PAYLOAD_TOO_LARGE", "PDF stream exceeds size limit")
            return size
        seen.add(id(stream))
        data = stream.get_data()
        size = len(data)
        if hasattr(stream, "decoded_self"):
            stream.decoded_self = None
        if size > maximum:
            raise GatewayError("PAYLOAD_TOO_LARGE", "PDF stream exceeds size limit")
        try:
            _record_pdf_decoded_stream(stream, size)
        except LimitReachedError:
            raise GatewayError("PAYLOAD_TOO_LARGE", "PDF decoded content exceeds size limit") from None
        stream_sizes[id(stream)] = size
        return size

    def count_font_descriptor(font) -> None:
        descriptor = _resolve_pdf_object(font.get("/FontDescriptor")) \
            if hasattr(font, "get") and font.get("/FontDescriptor") is not None else None
        if descriptor is None:
            return
        for key in ("/FontFile", "/FontFile2", "/FontFile3"):
            if key in descriptor:
                count_stream(descriptor[key], MAX_PDF_FONT_STREAM_BYTES)

    def count_font_streams(resources) -> None:
        resources = _resolve_pdf_object(resources)
        fonts = _resolve_pdf_object(resources.get("/Font")) if resources is not None else None
        if not hasattr(fonts, "values"):
            return
        for reference in fonts.values():
            font = _resolve_pdf_object(reference)
            to_unicode = font.get("/ToUnicode") if hasattr(font, "get") else None
            count_stream(to_unicode, MAX_PDF_FONT_STREAM_BYTES)
            encoding = font.get("/Encoding") if hasattr(font, "get") else None
            encoding = _resolve_pdf_object(encoding) if encoding is not None else None
            if hasattr(encoding, "get_data"):
                count_stream(encoding, MAX_PDF_FONT_STREAM_BYTES)
            descendants = _resolve_pdf_object(font.get("/DescendantFonts")) \
                if hasattr(font, "get") and font.get("/DescendantFonts") is not None else ()
            for child_reference in descendants:
                child = _resolve_pdf_object(child_reference)
                count_font_descriptor(child)
            count_font_descriptor(font)

    def visit_resources(resources, active_resources: set[int], active_forms: set[int],
                        visits: list[int], depth: int = 0) -> int:
        resources = _resolve_pdf_object(resources)
        if resources is None:
            return 0
        resource_id = id(resources)
        if resource_id in active_resources:
            raise GatewayError("INVALID_ARGUMENT", "Cyclic PDF resources are unsupported")
        if depth > 32:
            raise GatewayError("PAYLOAD_TOO_LARGE", "PDF resource nesting exceeds limit")
        visits[0] += 1
        if visits[0] > MAX_PDF_RESOURCE_VISITS:
            raise GatewayError("PAYLOAD_TOO_LARGE", "PDF resource count exceeds limit")
        active_resources.add(resource_id)
        count_font_streams(resources)
        xobjects = _resolve_pdf_object(resources.get("/XObject"))
        if not hasattr(xobjects, "items"):
            active_resources.remove(resource_id)
            return 0
        form_total = 0
        for _name, reference in xobjects.items():
            obj = _resolve_pdf_object(reference)
            if obj.get("/Subtype") == "/Form":
                decoded_size = count_stream(obj)
                form_id = id(obj)
                if form_id in active_forms:
                    raise GatewayError("INVALID_ARGUMENT", "Cyclic PDF form resources are unsupported")
                form_total += decoded_size
                if form_total * MAX_XFORM_INVOCATIONS > MAX_PDF_FORM_EXPANSION_BYTES:
                    raise GatewayError("PAYLOAD_TOO_LARGE", "PDF form expansion exceeds size limit")
                active_forms.add(form_id)
                form_total += visit_resources(_inherited_pdf_resources(obj), active_resources,
                                              active_forms, visits, depth + 1)
                active_forms.remove(form_id)
        active_resources.remove(resource_id)
        return form_total

    def count_form_invocations(streams, resources, active_forms: set[int], calls: list[int]) -> bool:
        if streams is None:
            return False
        operations = ContentStream(streams, reader).operations
        resources = _resolve_pdf_object(resources)
        xobjects = _resolve_pdf_object(resources.get("/XObject")) if resources is not None else None
        for operands, operator in operations:
            if operator != b"Do" or not operands or not hasattr(xobjects, "get"):
                continue
            obj = _resolve_pdf_object(xobjects.get(str(operands[0])))
            if not hasattr(obj, "get") or obj.get("/Subtype") != "/Form":
                continue
            calls[0] += 1
            if calls[0] > MAX_XFORM_INVOCATIONS:
                return True
            form_id = id(obj)
            if form_id in active_forms:
                raise GatewayError("INVALID_ARGUMENT", "Cyclic PDF form resources are unsupported")
            active_forms.add(form_id)
            nested_resources = _inherited_pdf_resources(obj)
            if count_form_invocations(obj, nested_resources, active_forms, calls):
                active_forms.remove(form_id)
                return True
            active_forms.remove(form_id)
        return False

    for page in reader.pages:
        contents = _resolve_pdf_object(page.get("/Contents"))
        if isinstance(contents, (list, tuple)):
            for stream in contents:
                count_stream(stream)
        else:
            count_stream(contents)
        inherited_resources = _inherited_pdf_resources(page)
        visit_resources(inherited_resources, set(), set(), [0])
        contents = page.get("/Contents")
        try:
            page_form_overflow.append(count_form_invocations(contents, inherited_resources, set(), [0]))
        except GatewayError:
            raise
        except Exception:
            # A damaged content/Form stream is eligible for OCR rather than partial extraction.
            page_form_overflow.append(True)
    return tuple(page_form_overflow)


def _extract_pdf_pages(content: bytes, max_ocr_pages: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        with _pdf_decode_budget(), apply_configuration(
            maximum_declared_stream_length=MAX_PDF_BYTES,
            array_based_stream_maximum_output_length=MAX_PDF_FONT_STREAM_BYTES,
            zlib_maximum_output_length=MAX_PDF_FONT_STREAM_BYTES,
            lzw_maximum_output_length=MAX_PDF_FONT_STREAM_BYTES,
            run_length_maximum_output_length=MAX_PDF_FONT_STREAM_BYTES,
            jbig2_maximum_output_length=MAX_PDF_FONT_STREAM_BYTES,
            image_maximum_buffer_size=MAX_PDF_STREAM_BYTES,
            xmp_maximum_input_length=MAX_PDF_STREAM_BYTES,
            xmp_maximum_element_count=10_000,
            page_tree_maximum_entries=MAX_PDF_PAGE_TREE_ENTRIES,
            page_tree_maximum_depth=32,
            outline_maximum_entries=10_000,
            outline_maximum_depth=32,
            xform_maximum_invocations_per_extraction=MAX_XFORM_INVOCATIONS,
        ):
            reader = PdfReader(BytesIO(content), strict=True, root_object_recovery_limit=2_048)
            if reader.is_encrypted:
                try:
                    decrypted = reader.decrypt("")
                except Exception:
                    decrypted = 0
                if not decrypted:
                    raise GatewayError("UNSUPPORTED_MEDIA_TYPE", "PDF requires an unavailable password")
            page_count = len(reader.pages)
            if not 1 <= page_count <= MAX_PAGES:
                raise GatewayError("PAYLOAD_TOO_LARGE", "Document page count exceeds limit")
            _normalize_pdf_font_objects(reader)
            page_form_overflow = _preflight_pdf_streams(reader)
            pages: list[str] = []
            origins: list[str] = []
            no_text_pages: list[int] = []
            suspicious_pages: list[int] = []
            total_text_bytes = 0
            for number, (page, xform_limit_exceeded) in enumerate(
                    zip(reader.pages, page_form_overflow, strict=True), 1):
                if xform_limit_exceeded:
                    text = ""
                else:
                    token = object()
                    capture = [False]
                    with _PDF_LOG_LOCK:
                        original_warning = pypdf_page.logger_warning
                        context_token = _PDF_LOG_CONTEXT.set(token)

                        def capture_warning(message: str, *, source: str, **values) -> None:
                            if (_PDF_LOG_CONTEXT.get() is token and source == "pypdf._page"
                                    and message.startswith(("Impossible to decode XFormObject", "Exceeded ",
                                                            "Detected cyclic form XObject reference"))):
                                capture[0] = True
                            original_warning(message, source=source, **values)

                        pypdf_page.logger_warning = capture_warning
                        try:
                            text = page.extract_text() or ""
                        except LimitReachedError:
                            raise GatewayError("PAYLOAD_TOO_LARGE", "PDF content stream exceeds size limit") from None
                        except Exception:
                            text = ""
                        finally:
                            pypdf_page.logger_warning = original_warning
                            _PDF_LOG_CONTEXT.reset(context_token)
                    if capture[0]:
                        text = ""
                if xform_limit_exceeded:
                    text = ""
                if text.strip() and not _suspicious_text_layer(text):
                    page_bytes = len(text.encode("utf-8"))
                    if page_bytes > MAX_PAGE_TEXT:
                        raise GatewayError("PAYLOAD_TOO_LARGE", "Document page exceeds size limit")
                    total_text_bytes += page_bytes
                    if total_text_bytes > MAX_TOTAL_TEXT:
                        raise GatewayError("PAYLOAD_TOO_LARGE", "Document text exceeds size limit")
                    pages.append(text)
                    origins.append("pdf_text_layer")
                elif text.strip():
                    # Never index clearly broken Unicode maps as if they were source text.
                    pages.append("")
                    origins.append("pdf_text_layer_suspicious")
                    suspicious_pages.append(number)
                else:
                    pages.append("")
                    origins.append("pdf_ocr_unavailable")
                    no_text_pages.append(number)

            if no_text_pages or suspicious_pages:
                if not _ocr_runtime_available():
                    for number in no_text_pages:
                        origins[number - 1] = "pdf_ocr_unavailable"
                else:
                    # Spend the bounded OCR budget on image-only pages first, then
                    # use the remainder to recover pages with suspicious text maps.
                    selected = no_text_pages[:max_ocr_pages]
                    selected.extend(suspicious_pages[:max(0, max_ocr_pages - len(selected))])
                    for number in no_text_pages[max_ocr_pages:]:
                        origins[number - 1] = "pdf_ocr_limit_reached"
                    for number in selected:
                        try:
                            pages[number - 1] = _ocr_pdf_page(content, number)
                            origins[number - 1] = (
                                "pdf_ocr_derived" if pages[number - 1].strip() else "pdf_ocr_empty"
                            )
                        except GatewayError as error:
                            if error.code == "INVALID_ARGUMENT":
                                origins[number - 1] = "pdf_ocr_failed"
                                continue
                            if error.code != "UNSUPPORTED_MEDIA_TYPE":
                                raise
                            origins[number - 1] = (
                                "pdf_ocr_unavailable" if number in no_text_pages
                                else "pdf_text_layer_suspicious"
                            )
                            for remaining in selected[selected.index(number) + 1:]:
                                if remaining in no_text_pages:
                                    origins[remaining - 1] = "pdf_ocr_unavailable"
                            break
            return tuple(pages), tuple(origins)
    except GatewayError:
        raise
    except LimitReachedError:
        raise GatewayError("PAYLOAD_TOO_LARGE", "PDF stream exceeds size limit") from None
    except Exception:
        raise GatewayError("INVALID_ARGUMENT", "PDF could not be parsed") from None


def _document_origin(page_origins: tuple[str, ...]) -> str:
    unique = set(page_origins)
    if len(unique) == 1:
        return page_origins[0]
    aggregate_parts = []
    if "pdf_text_layer" in unique:
        aggregate_parts.append("text")
    if "pdf_ocr_derived" in unique:
        aggregate_parts.append("ocr")
    if "pdf_ocr_empty" in unique:
        aggregate_parts.append("ocr_empty")
    if "pdf_text_layer_suspicious" in unique:
        aggregate_parts.append("suspicious")
    if "pdf_ocr_unavailable" in unique:
        aggregate_parts.append("ocr_unavailable")
    if "pdf_ocr_limit_reached" in unique:
        aggregate_parts.append("ocr_limit_reached")
    if "pdf_ocr_failed" in unique:
        aggregate_parts.append("ocr_failed")
    if aggregate_parts:
        return "mixed_pdf_" + "_and_".join(aggregate_parts)
    if "pdf_text_layer_suspicious" in unique:
        if "pdf_text_layer" in unique and "pdf_ocr_derived" in unique:
            return "mixed_pdf_text_ocr_and_suspicious"
        if "pdf_text_layer" in unique:
            return "mixed_pdf_text_and_suspicious"
        if "pdf_ocr_derived" in unique:
            return "mixed_ocr_and_suspicious"
        return "pdf_text_layer_suspicious"
    if unique == {"pdf_text_layer", "pdf_ocr_derived"}:
        return "mixed_pdf_text_and_ocr"
    return page_origins[0]


def _text_layer_status(origin: str) -> str:
    if "ocr_empty" in origin:
        return "ocr_empty"
    if "ocr_derived" in origin:
        return "ocr_derived"
    if "ocr_unavailable" in origin:
        return "ocr_unavailable"
    if "ocr_limit_reached" in origin:
        return "ocr_limit_reached"
    if "ocr_failed" in origin:
        return "ocr_failed"
    if "suspicious" in origin:
        return "suspicious"
    if origin.startswith("mixed_"):
        return "mixed"
    if "pdf_text_layer" in origin:
        return "unverified"
    return "not_applicable"


def _suspicious_text_layer(text: str) -> bool:
    """Conservatively flag deterministic Unicode corruption; never infer semantics."""
    if not text:
        return False
    characters = [character for character in text if not character.isspace()]
    if not characters:
        return False
    private_use = sum(unicodedata.category(character) == "Co" for character in characters)
    replacements = sum(character == "\ufffd" for character in characters)
    controls = sum(unicodedata.category(character) == "Cc" for character in characters)
    printable = sum(character.isprintable() for character in characters)
    if replacements or controls:
        return True
    if private_use and private_use / len(characters) >= 0.02:
        return True
    if len(characters) >= 20 and printable / len(characters) < 0.85:
        return True
    if len(characters) >= 16:
        frequencies = {}
        for character in characters:
            frequencies[character] = frequencies.get(character, 0) + 1
        if len(frequencies) <= 2 and max(frequencies.values()) / len(characters) >= 0.75:
            return True
    return False


def _normalize_pages(item: DocumentInput, max_ocr_pages: int = MAX_OCR_PAGES
                     ) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    if type(item) is not DocumentInput or type(item.title) is not str or not 1 <= len(item.title) <= 200 \
            or "\x00" in item.title or any(ord(c) < 32 for c in item.title):
        raise GatewayError("INVALID_ARGUMENT", "Invalid document")
    if item.media_type == "text/plain":
        if item.supplied_pages is not None or item.supplied_page_origin is not None:
            raise GatewayError("INVALID_ARGUMENT", "Plain text pages come from source bytes")
        try:
            text = item.content.decode("utf-8")
        except UnicodeDecodeError:
            raise GatewayError("INVALID_ARGUMENT", "Invalid UTF-8 text") from None
        pages = tuple(text.replace("\r\n", "\n").replace("\r", "\n").split("\f"))
        origins = ("source_text",) * len(pages)
        origin = "source_text"
    elif item.media_type == "application/pdf":
        if item.supplied_page_origin is not None and (type(item.supplied_page_origin) is not str
                                                     or item.supplied_page_origin not in {"text_layer", "ocr"}):
            raise GatewayError("INVALID_ARGUMENT", "Invalid supplied page origin")
        if item.supplied_pages is None:
            if item.supplied_page_origin is not None:
                raise GatewayError("INVALID_ARGUMENT", "Supplied page origin requires supplied pages")
            pages, origins = _extract_pdf_pages(item.content, max_ocr_pages)
            origin = _document_origin(origins)
        else:
            if type(item.supplied_pages) is not tuple or not item.supplied_pages:
                raise GatewayError("INVALID_ARGUMENT", "Invalid supplied pages")
            pages = item.supplied_pages
            origin = "supplied_ocr_derived" if item.supplied_page_origin == "ocr" else "supplied_extraction"
            origins = (origin,) * len(pages)
    else:
        raise GatewayError("UNSUPPORTED_MEDIA_TYPE", "Document media type is unsupported")
    if not 1 <= len(pages) <= MAX_PAGES or len(origins) != len(pages):
        raise GatewayError("PAYLOAD_TOO_LARGE", "Document page count exceeds limit")
    normalized = []
    total = 0
    for page, page_origin in zip(pages, origins, strict=True):
        if type(page) is not str or "\x00" in page:
            raise GatewayError("INVALID_ARGUMENT", "Invalid document page")
        text = unicodedata.normalize("NFC", page.replace("\r\n", "\n").replace("\r", "\n")).strip()
        if not text and page_origin not in {"pdf_ocr_derived", "pdf_ocr_empty", "pdf_ocr_unavailable",
                                             "pdf_ocr_limit_reached", "pdf_ocr_failed",
                                             "pdf_text_layer_suspicious"}:
            raise GatewayError("INVALID_ARGUMENT", "Empty document page")
        if len(text.encode("utf-8")) > MAX_PAGE_TEXT:
            raise GatewayError("PAYLOAD_TOO_LARGE", "Document page exceeds limit")
        total += len(text)
        if total > MAX_TOTAL_TEXT:
            raise GatewayError("PAYLOAD_TOO_LARGE", "Document text exceeds limit")
        normalized.append(text)
    return tuple(normalized), origins, origin


def _chunks(uri: str, page_number: int, text: str) -> tuple[ChunkRecord, ...]:
    sections: list[tuple[str, str]] = []
    heading = ""
    body: list[str] = []
    for line in text.split("\n"):
        if re.match(r"^#{1,6} +\S", line):
            if body:
                sections.append((heading, "\n".join(body).strip()))
            heading = line.lstrip("# ").strip()[:200]
            body = [line]
        else:
            body.append(line)
    if body:
        sections.append((heading, "\n".join(body).strip()))
    result = []
    for section, content in sections:
        for start in range(0, len(content), 1000):
            piece = content[start:start + 1000]
            digest = hashlib.sha256(f"{uri}\x00{page_number}\x00{len(result)}\x00{piece}".encode()).hexdigest()
            result.append(ChunkRecord("chunk://sha256/" + digest, uri, page_number, section, piece))
    return tuple(result)


class SQLiteDocumentStore:
    """All paths remain private; callers address blobs and pages by logical IDs."""

    def __init__(self, asset_root: Path, database_path: Path) -> None:
        self.asset_root = Path(asset_root)
        self.database_path = Path(database_path)
        if not self.asset_root.is_absolute() or not self.database_path.is_absolute():
            raise ValueError("Asset root and database must be absolute")

    def _ready(self) -> None:
        if not self.asset_root.is_dir() or not self.database_path.parent.is_dir() \
                or _path_has_reparse_point(self.asset_root) \
                or _path_has_reparse_point(self.database_path.parent) \
                or _path_has_reparse_point(self.database_path):
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
        try:
            for suffix in ("", "-wal", "-shm", "-journal"):
                path = Path(str(self.database_path) + suffix)
                if _path_has_reparse_point(path):
                    raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
                try:
                    info = path.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
        except OSError:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None

    def _connect(self, *, write: bool = False) -> sqlite3.Connection:
        self._ready()
        if not write and not self.database_path.is_file():
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
        try:
            if write:
                con = sqlite3.connect(self.database_path)
            else:
                con = sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)
            try:
                self._ready()
            except Exception:
                con.close()
                raise
            con.row_factory = sqlite3.Row
            return con
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None

    @staticmethod
    def _schema(con: sqlite3.Connection) -> None:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS assets(uri TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
                media_type TEXT NOT NULL, size INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS documents(uri TEXT PRIMARY KEY, asset_uri TEXT NOT NULL,
                title TEXT NOT NULL, media_type TEXT NOT NULL, page_count INTEGER NOT NULL,
                text_origin TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pages(document_uri TEXT NOT NULL, page_number INTEGER NOT NULL,
                text TEXT NOT NULL, text_origin TEXT NOT NULL,
                PRIMARY KEY(document_uri, page_number));
            CREATE TABLE IF NOT EXISTS chunks(uri TEXT PRIMARY KEY, document_uri TEXT NOT NULL,
                page_number INTEGER NOT NULL, section TEXT NOT NULL, text TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operation_audit(
                audit_id INTEGER PRIMARY KEY, operation TEXT NOT NULL, resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL, occurred_at TEXT NOT NULL, outcome TEXT NOT NULL);
        """)
        ensure_provenance_schema(con, "documents")

    def get_write_provenance(self, record_type: str, record_id: str,
                             version: int | None = None) -> dict | None:
        with closing(self._connect()) as con:
            return get_provenance(con, record_type, record_id, version)

    def _write_blob(self, digest: str, data: bytes, created: list[Path]) -> None:
        directory = self.asset_root / digest[:2]
        if _path_has_reparse_point(directory):
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
        directory.mkdir(exist_ok=True)
        if _path_has_reparse_point(directory):
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
        target = directory / digest
        if target.exists():
            if _path_has_reparse_point(target) or target.stat().st_nlink != 1 \
                    or not stat.S_ISREG(target.stat(follow_symlinks=False).st_mode) \
                    or target.stat().st_size != len(data):
                raise GatewayError("CONFLICT", "Asset conflicts with existing data")
            with target.open("rb") as stream:
                before = _stream_signature(stream)
                if os.fstat(stream.fileno()).st_nlink != 1:
                    raise GatewayError("CONFLICT", "Asset conflicts with existing data")
                snapshot = stream.read(len(data) + 1)
                if snapshot != data or hashlib.sha256(snapshot).hexdigest() != digest \
                        or _stream_signature(stream) != before \
                        or os.fstat(stream.fileno()).st_nlink != 1 or target.stat().st_nlink != 1 \
                        or _path_has_reparse_point(target):
                    raise GatewayError("CONFLICT", "Asset conflicts with existing data")
            return
        temporary = directory / (digest + "." + uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
            os.replace(temporary, target)
            created.append(target)
        finally:
            temporary.unlink(missing_ok=True)

    def _insert_asset(self, con: sqlite3.Connection, data: bytes, media_type: str,
                      created: list[Path]) -> AssetRecord:
        digest, uri = _validate_asset(data, media_type)
        existing = con.execute("SELECT media_type, size FROM assets WHERE uri = ?", (uri,)).fetchone()
        if existing is not None and existing["size"] != len(data):
            raise GatewayError("CONFLICT", "Asset conflicts with existing data")
        self._write_blob(digest, data, created)
        con.execute("INSERT OR IGNORE INTO assets VALUES (?, ?, ?, ?)", (uri, digest, media_type, len(data)))
        return AssetRecord(uri, existing["media_type"] if existing is not None else media_type,
                           len(data), digest)

    def register_asset(self, data: bytes, media_type: str,
                       identity: ReportedIdentity | None = None) -> AssetRecord:
        return self._register_asset(data, media_type, identity=identity)

    def register_legacy_asset(self, data: bytes, media_type: str, *,
                              import_batch_id: str, source_ref: str,
                              identity: ReportedIdentity | None = None) -> AssetRecord:
        """Restricted internal image migration; never exposed as an MCP tool.

        A content match retains its original metadata and provenance. The
        reference records a source identity, without asserting an evidence role.
        """
        if type(media_type) is not str or media_type not in {"image/png", "image/jpeg"} \
                or type(import_batch_id) is not str \
                or re.fullmatch(r"migration-[0-9a-f]{32}", import_batch_id) is None \
                or type(source_ref) is not str \
                or re.fullmatch(r"migration-source://[A-Za-z0-9][A-Za-z0-9_-]{0,255}/"
                                r"[A-Za-z0-9][A-Za-z0-9_-]{0,255}", source_ref) is None:
            raise GatewayError("INVALID_ARGUMENT", "Invalid legacy image import")
        return self._register_asset(data, media_type, identity=identity,
                                    legacy_import=(import_batch_id, source_ref))

    def _register_asset(self, data: bytes, media_type: str, *,
                        identity: ReportedIdentity | None,
                        legacy_import: tuple[str, str] | None = None) -> AssetRecord:
        _validate_asset(data, media_type)
        identity = identity or ReportedIdentity()
        created: list[Path] = []
        con = self._connect(write=True)
        try:
            self._schema(con)
            with con:
                existing = con.execute("SELECT 1 FROM assets WHERE uri = ?",
                                        ("asset://sha256/" + hashlib.sha256(data).hexdigest(),)).fetchone()
                result = self._insert_asset(con, data, media_type, created)
                if existing is None:
                    metadata = ({"source_refs": [legacy_import[1]],
                                 "imported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                                 "source_system": legacy_import[1].split("/")[2],
                                 "import_batch_id": legacy_import[0], "legacy_status": "imported"}
                                if legacy_import else {})
                    insert_provenance(con, record_type="asset", record_id=result.uri, version=1,
                                      data_origin="legacy_import" if legacy_import else "source",
                                      actor_type="importer" if legacy_import else "external_client",
                                      identity=identity, **metadata)
                _audit(con, "register_asset", "asset", result.uri)
            return result
        except (sqlite3.Error, OSError) as error:
            for path in created:
                path.unlink(missing_ok=True)
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        finally:
            con.close()

    def _existing_document_for_source(self, asset_uri: str, media_type: str) -> DocumentRecord | None:
        if not self.database_path.is_file():
            return None
        try:
            with closing(self._connect()) as con:
                row = con.execute(
                    "SELECT * FROM documents WHERE asset_uri = ? AND media_type = ?",
                    (asset_uri, media_type),
                ).fetchone()
                return DocumentRecord(*row) if row is not None else None
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None

    def ingest_documents(self, items: list[DocumentInput],
                         identity: ReportedIdentity | None = None) -> tuple[DocumentRecord, ...]:
        identity = identity or ReportedIdentity()
        if type(items) is not list or not items:
            raise GatewayError("INVALID_ARGUMENT", "Invalid document batch")
        if len(items) > MAX_BATCH:
            raise GatewayError("PAYLOAD_TOO_LARGE", "Document batch exceeds limit")
        prepared = []
        batch_bytes = 0
        remaining_ocr_pages = MAX_OCR_PAGES_PER_BATCH
        for item in items:
            if type(item) is not DocumentInput:
                raise GatewayError("INVALID_ARGUMENT", "Invalid document")
            digest, asset_uri = _validate_asset(item.content, item.media_type)
            existing = self._existing_document_for_source(asset_uri, item.media_type)
            if existing is not None:
                if existing.title != item.title:
                    raise GatewayError("CONFLICT", "Document conflicts with existing data")
                prepared.append((item, asset_uri, existing.uri, None, None, existing.text_origin, existing))
                batch_bytes += len(item.content)
                if batch_bytes > MAX_PDF_BYTES + MAX_TOTAL_TEXT:
                    raise GatewayError("PAYLOAD_TOO_LARGE", "Document batch exceeds limit")
                continue
            pages, page_origins, origin = _normalize_pages(
                item, min(MAX_OCR_PAGES, remaining_ocr_pages)
            )
            attempted_ocr_pages = (page_origins.count("pdf_ocr_derived")
                                   + page_origins.count("pdf_ocr_empty")
                                   + page_origins.count("pdf_ocr_failed")
                                   + int("pdf_ocr_unavailable" in page_origins))
            remaining_ocr_pages -= attempted_ocr_pages
            batch_bytes += len(item.content) + sum(len(p.encode("utf-8")) for p in pages)
            if batch_bytes > MAX_PDF_BYTES + MAX_TOTAL_TEXT:
                raise GatewayError("PAYLOAD_TOO_LARGE", "Document batch exceeds limit")
            uri = "document://sha256/" + hashlib.sha256(
                (item.media_type + "\x00" + digest).encode("utf-8")
            ).hexdigest()
            prepared.append((item, asset_uri, uri, pages, page_origins, origin, None))
        created: list[Path] = []
        con = self._connect(write=True)
        try:
            self._schema(con)
            results = []
            with con:
                for item, asset_uri, uri, pages, page_origins, origin, existing in prepared:
                    asset_exists = con.execute("SELECT 1 FROM assets WHERE uri = ?", (asset_uri,)).fetchone()
                    self._insert_asset(con, item.content, item.media_type, created)
                    if asset_exists is None:
                        insert_provenance(con, record_type="asset", record_id=asset_uri, version=1,
                                          data_origin="source", actor_type="external_client",
                                          identity=identity)
                    if existing is not None:
                        results.append(existing)
                        _audit(con, "ingest_document_duplicate", "document", existing.uri)
                        continue
                    existing_row = con.execute("SELECT * FROM documents WHERE uri = ?", (uri,)).fetchone()
                    if existing_row is not None and existing_row["title"] != item.title:
                        raise GatewayError("CONFLICT", "Document conflicts with existing data")
                    if existing_row is None:
                        con.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
                                    (uri, asset_uri, item.title, item.media_type, len(pages), origin))
                        insert_provenance(con, record_type="document", record_id=uri, version=1,
                                          data_origin="deterministic_derived", actor_type="system",
                                          source_refs=[asset_uri], identity=identity)
                        for number, (text, page_origin) in enumerate(zip(pages, page_origins, strict=True), 1):
                            con.execute("INSERT INTO pages VALUES (?, ?, ?, ?)",
                                        (uri, number, text, page_origin))
                            for chunk in _chunks(uri, number, text):
                                con.execute("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                                            (chunk.uri, uri, number, chunk.section, chunk.text))
                            insert_provenance(con, record_type="document_page",
                                              record_id=f"{uri}#page={number}", version=1,
                                              data_origin="deterministic_derived", actor_type="system",
                                              source_refs=[uri, asset_uri], identity=identity)
                    results.append(DocumentRecord(uri, asset_uri, item.title, item.media_type,
                                                  len(pages), origin))
                    _audit(con, "ingest_document", "document", uri)
            return tuple(results)
        except (sqlite3.Error, OSError):
            for path in created:
                path.unlink(missing_ok=True)
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        finally:
            con.close()

    def fetch_asset_record(self, uri: str) -> AssetRecord | None:
        if type(uri) is not str or _ASSET_URI.fullmatch(uri) is None:
            raise GatewayError("INVALID_ARGUMENT", "Invalid asset URI")
        with closing(self._connect()) as con:
            row = con.execute("SELECT * FROM assets WHERE uri = ?", (uri,)).fetchone()
            return AssetRecord(row["uri"], row["media_type"], row["size"], row["sha256"]) if row else None

    def fetch_asset(self, uri: str, offset: int = 0, length: int = MAX_FETCH_BYTES) -> bytes:
        if type(offset) is not int or offset < 0 or type(length) is not int or length < 1:
            raise GatewayError("INVALID_ARGUMENT", "Invalid asset range")
        if length > MAX_FETCH_BYTES:
            raise GatewayError("PAYLOAD_TOO_LARGE", "Asset result exceeds limit")
        record = self.fetch_asset_record(uri)
        if record is None:
            raise GatewayError("RESOURCE_NOT_FOUND", "Asset was not found")
        snapshot = self._verified_asset_snapshot(record)
        return snapshot[offset:offset + length]

    def _verified_asset_snapshot(self, record: AssetRecord) -> bytes:
        if type(record) is not AssetRecord or type(record.sha256) is not str \
                or re.fullmatch(r"[0-9a-f]{64}", record.sha256) is None \
                or record.uri != "asset://sha256/" + record.sha256 \
                or type(record.media_type) is not str or record.media_type not in _MEDIA \
                or type(record.size) is not int:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
        size_limit = (MAX_PDF_BYTES if record.media_type == "application/pdf" else
                      MAX_IMAGE_BYTES if record.media_type in {"image/png", "image/jpeg", "image/webp"} else
                      MAX_ASSET_BYTES)
        if not 1 <= record.size <= size_limit:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
        path = self.asset_root / record.sha256[:2] / record.sha256
        if _path_has_reparse_point(path):
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
        try:
            info = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
            with path.open("rb") as stream:
                before = _stream_signature(stream)
                if os.fstat(stream.fileno()).st_nlink != 1 or before[2] != record.size \
                        or _file_signature(path.stat())[:4] != before[:4]:
                    raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
                snapshot = stream.read(record.size + 1)
                if len(snapshot) != record.size or hashlib.sha256(snapshot).hexdigest() != record.sha256 \
                        or _stream_signature(stream) != before \
                        or os.fstat(stream.fileno()).st_nlink != 1 or path.stat().st_nlink != 1 \
                        or _path_has_reparse_point(path):
                    raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
                return snapshot
        except OSError:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None

    def list_ocr_candidates(self, uri: str, limit: int = MAX_PAGES, offset: int = 0) -> tuple[int, ...]:
        if type(uri) is not str or _DOC_URI.fullmatch(uri) is None \
                or type(limit) is not int or not 1 <= limit <= MAX_PAGES \
                or type(offset) is not int or not 0 <= offset <= MAX_PAGES:
            raise GatewayError("INVALID_ARGUMENT", "Invalid OCR candidate query")
        candidate_origins = ("pdf_text_layer_suspicious", "pdf_ocr_limit_reached",
                             "pdf_ocr_unavailable", "pdf_ocr_failed")
        placeholders = ",".join("?" for _ in candidate_origins)
        with closing(self._connect()) as con:
            rows = con.execute(
                f"SELECT page_number FROM pages WHERE document_uri = ? "
                f"AND text_origin IN ({placeholders}) ORDER BY page_number LIMIT ? OFFSET ?",
                (uri, *candidate_origins, limit, offset),
            ).fetchall()
            if not rows and con.execute("SELECT 1 FROM documents WHERE uri = ?", (uri,)).fetchone() is None:
                raise GatewayError("RESOURCE_NOT_FOUND", "Document was not found")
            return tuple(row["page_number"] for row in rows)

    def process_ocr_pages(self, uri: str, page_numbers: list[int],
                          identity: ReportedIdentity | None = None) -> DocumentRecord:
        identity = identity or ReportedIdentity()
        if type(page_numbers) is list and len(page_numbers) > MAX_OCR_PAGES_PER_BATCH:
            raise GatewayError("PAYLOAD_TOO_LARGE", "OCR page batch exceeds limit")
        if type(uri) is not str or _DOC_URI.fullmatch(uri) is None \
                or type(page_numbers) is not list or not page_numbers \
                or any(type(number) is not int or not 1 <= number <= MAX_PAGES for number in page_numbers) \
                or len(set(page_numbers)) != len(page_numbers):
            raise GatewayError("INVALID_ARGUMENT", "Invalid OCR page batch")
        document = self.fetch_document(uri)
        if document is None:
            raise GatewayError("RESOURCE_NOT_FOUND", "Document was not found")
        if document.media_type != "application/pdf":
            raise GatewayError("UNSUPPORTED_MEDIA_TYPE", "OCR is available only for PDF documents")
        candidate_origins = {"pdf_text_layer_suspicious", "pdf_ocr_limit_reached",
                             "pdf_ocr_unavailable", "pdf_ocr_failed"}
        pages = [self.fetch_page(uri, number) for number in page_numbers]
        if any(page is None for page in pages):
            raise GatewayError("RESOURCE_NOT_FOUND", "Document page was not found")
        if any(page.text_origin not in candidate_origins for page in pages):
            raise GatewayError("CONFLICT", "Page is not pending OCR")
        if not _ocr_runtime_available():
            raise GatewayError("UNSUPPORTED_MEDIA_TYPE", "PDF OCR support is unavailable")
        asset = self.fetch_asset_record(document.asset_uri)
        if asset is None or asset.media_type != "application/pdf":
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
        source = self._verified_asset_snapshot(asset)
        derived = _ocr_pdf_pages(source, tuple(page_numbers))
        normalized = {}
        for number in page_numbers:
            text = unicodedata.normalize("NFC", (derived[number] or "").replace("\r\n", "\n").replace("\r", "\n")).strip()
            if "\x00" in text or len(text.encode("utf-8")) > MAX_PAGE_TEXT:
                raise GatewayError("PAYLOAD_TOO_LARGE", "OCR page exceeds size limit")
            normalized[number] = text

        con = self._connect(write=True)
        try:
            self._schema(con)
            with con:
                current = con.execute("SELECT * FROM documents WHERE uri = ?", (uri,)).fetchone()
                if current is None or current["asset_uri"] != document.asset_uri:
                    raise GatewayError("CONFLICT", "Document source changed during OCR")
                for number in page_numbers:
                    current_page = con.execute(
                        "SELECT text_origin FROM pages WHERE document_uri = ? AND page_number = ?",
                        (uri, number),
                    ).fetchone()
                    if current_page is None or current_page["text_origin"] not in candidate_origins:
                        raise GatewayError("CONFLICT", "Page changed during OCR")
                total = con.execute(
                    "SELECT COALESCE(SUM(length(CAST(text AS BLOB))), 0) AS bytes "
                    "FROM pages WHERE document_uri = ?", (uri,),
                ).fetchone()["bytes"]
                total += sum(len(text.encode("utf-8")) for text in normalized.values())
                total -= sum(len(page.text.encode("utf-8")) for page in pages if page.text)
                if total > MAX_TOTAL_TEXT:
                    raise GatewayError("PAYLOAD_TOO_LARGE", "Document text exceeds size limit")
                for number, text in normalized.items():
                    origin = "pdf_ocr_derived" if text else "pdf_ocr_empty"
                    record_id = f"{uri}#page={number}"
                    previous = get_provenance(con, "document_page", record_id)
                    next_version = previous["version"] + 1 if previous else 1
                    con.execute("UPDATE pages SET text = ?, text_origin = ? "
                                "WHERE document_uri = ? AND page_number = ?",
                                (text, origin, uri, number))
                    con.execute("DELETE FROM chunks WHERE document_uri = ? AND page_number = ?",
                                (uri, number))
                    for chunk in _chunks(uri, number, text):
                        con.execute("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                                    (chunk.uri, uri, number, chunk.section, chunk.text))
                    insert_provenance(
                        con, record_type="document_page", record_id=record_id, version=next_version,
                        data_origin="deterministic_derived", actor_type="system",
                        source_refs=[uri, document.asset_uri],
                        identity=identity,
                        supersedes_provenance_id=previous["provenance_id"] if previous else None,
                    )
                origins = tuple(row["text_origin"] for row in con.execute(
                    "SELECT text_origin FROM pages WHERE document_uri = ? ORDER BY page_number", (uri,)
                ).fetchall())
                aggregate = _document_origin(origins)
                con.execute("UPDATE documents SET text_origin = ? WHERE uri = ?", (aggregate, uri))
                _audit(con, "process_document_ocr", "document", uri)
            return DocumentRecord(uri, document.asset_uri, document.title, document.media_type,
                                  document.page_count, aggregate)
        except sqlite3.Error:
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable") from None
        finally:
            con.close()

    def render_page_image(self, uri: str, page_number: int) -> tuple[bytes, int, int, str]:
        if type(uri) is not str or _DOC_URI.fullmatch(uri) is None \
                or type(page_number) is not int or not 1 <= page_number <= MAX_PAGES:
            raise GatewayError("INVALID_ARGUMENT", "Invalid document page")
        document = self.fetch_document(uri)
        if document is None:
            raise GatewayError("RESOURCE_NOT_FOUND", "Document was not found")
        if document.media_type != "application/pdf" or page_number > document.page_count:
            raise GatewayError("RESOURCE_NOT_FOUND", "PDF page was not found")
        asset = self.fetch_asset_record(document.asset_uri)
        if asset is None or asset.media_type != "application/pdf":
            raise GatewayError("STORAGE_UNAVAILABLE", "Local storage is unavailable")
        source = self._verified_asset_snapshot(asset)
        try:
            import pymupdf
        except ImportError:
            raise GatewayError("UNSUPPORTED_MEDIA_TYPE", "PDF page rendering support is not installed") from None
        try:
            with pymupdf.open(stream=source, filetype="pdf") as pdf:
                if pdf.needs_pass and not pdf.authenticate(""):
                    raise GatewayError("UNSUPPORTED_MEDIA_TYPE", "PDF requires an unavailable password")
                page = pdf[page_number - 1]
                pixels = page.rect.width * page.rect.height * (PAGE_RENDER_DPI / 72) ** 2
                if pixels > MAX_RENDER_PIXELS:
                    raise GatewayError("PAYLOAD_TOO_LARGE", "PDF page render exceeds pixel limit")
                pixmap = page.get_pixmap(dpi=PAGE_RENDER_DPI, alpha=False, colorspace=pymupdf.csRGB)
                content = pixmap.tobytes("png")
                if len(content) > MAX_RENDER_BYTES:
                    raise GatewayError("PAYLOAD_TOO_LARGE", "PDF page image exceeds result limit")
                return content, pixmap.width, pixmap.height, document.asset_uri
        except GatewayError:
            raise
        except Exception:
            raise GatewayError("INVALID_ARGUMENT", "PDF page could not be rendered") from None

    def fetch_document(self, uri: str) -> DocumentRecord | None:
        if type(uri) is not str or _DOC_URI.fullmatch(uri) is None:
            raise GatewayError("INVALID_ARGUMENT", "Invalid document URI")
        with closing(self._connect()) as con:
            row = con.execute("SELECT * FROM documents WHERE uri = ?", (uri,)).fetchone()
            return DocumentRecord(*row) if row else None

    def pages(self, uri: str) -> tuple[PageRecord, ...]:
        if self.fetch_document(uri) is None:
            raise GatewayError("RESOURCE_NOT_FOUND", "Document was not found")
        with closing(self._connect()) as con:
            rows = con.execute("SELECT * FROM pages WHERE document_uri = ? ORDER BY page_number", (uri,)).fetchall()
            return tuple(PageRecord(*row) for row in rows)

    def fetch_page(self, uri: str, page_number: int) -> PageRecord | None:
        if type(page_number) is not int or not 1 <= page_number <= MAX_PAGES:
            raise GatewayError("INVALID_ARGUMENT", "Invalid page number")
        if type(uri) is not str or _DOC_URI.fullmatch(uri) is None:
            raise GatewayError("INVALID_ARGUMENT", "Invalid document URI")
        with closing(self._connect()) as con:
            row = con.execute("SELECT * FROM pages WHERE document_uri = ? AND page_number = ?",
                              (uri, page_number)).fetchone()
            return PageRecord(*row) if row else None

    def search(self, query: str, limit: int = 5, offset: int = 0, *,
               source_ids: list[str] | None = None, page_range: list[int] | None = None) -> SearchPage:
        """Intersect canonical sources and inclusive physical pages before pagination.

        Up to 64 source IDs, duplicates accepted and deduplicated. The existing
        global candidate ceiling applies even when a narrower scope is requested.
        """
        if type(query) is not str or not 1 <= len(query.strip()) <= 500 or "\x00" in query \
                or type(limit) is not int or not 1 <= limit <= 20 \
                or type(offset) is not int or not 0 <= offset <= 1000:
            raise GatewayError("INVALID_ARGUMENT", "Invalid document search")
        if source_ids is not None and (type(source_ids) is not list or not 1 <= len(source_ids) <= 64
                or any(type(uri) is not str or _DOC_URI.fullmatch(uri) is None for uri in source_ids)):
            raise GatewayError("INVALID_ARGUMENT", "Invalid document source scope")
        if page_range is not None and (type(page_range) is not list or len(page_range) != 2
                or any(type(page) is not int or not 1 <= page <= MAX_PAGES for page in page_range)
                or page_range[0] > page_range[1]):
            raise GatewayError("INVALID_ARGUMENT", "Invalid document page scope")
        sources = set(source_ids) if source_ids is not None else None
        words = query.casefold().split()
        with closing(self._connect()) as con:
            rows = con.execute(
                "SELECT c.uri, c.document_uri, c.page_number, c.section, c.text, "
                "d.asset_uri, p.text_origin, d.media_type "
                "FROM chunks c JOIN documents d ON d.uri = c.document_uri "
                "JOIN pages p ON p.document_uri = c.document_uri AND p.page_number = c.page_number "
                "ORDER BY c.document_uri, c.page_number, c.uri LIMIT 2049"
            ).fetchall()
            if len(rows) > 2048:
                raise GatewayError("PAYLOAD_TOO_LARGE", "Document search has too many candidates")
            matches = [ChunkRecord(*row) for row in rows
                       if (sources is None or row["document_uri"] in sources)
                       and (page_range is None or page_range[0] <= row["page_number"] <= page_range[1])
                       and all(word in row["text"].casefold() for word in words)]
            return SearchPage(tuple(matches[offset:offset + limit]), len(matches), offset + limit < len(matches))
