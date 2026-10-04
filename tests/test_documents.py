"""Only invented documents and bytes; no personal material."""

import hashlib
import logging
import os
import sys
from pathlib import Path
import sqlite3
import zlib

import pytest

from cognivault.adapters.documents import DocumentInput, SQLiteDocumentStore
from cognivault.contracts import GatewayError
import cognivault.adapters.documents as documents
from pypdf import apply_configuration
from pypdf.errors import LimitReachedError
from pypdf.generic import DecodedStreamObject, EncodedStreamObject, NameObject


def _store(tmp_path: Path) -> SQLiteDocumentStore:
    root = tmp_path / "assets"
    root.mkdir()
    return SQLiteDocumentStore(root, tmp_path / "private-documents.db")


def _synthetic_pdf(*page_contents: str, compress: bool = False) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (f"<< /Type /Pages /Kids [{ ' '.join(f'{3 + i * 3} 0 R' for i in range(len(page_contents)))}] "
         f"/Count {len(page_contents)} >>").encode(),
    ]
    for index, text in enumerate(page_contents):
        page_id = 3 + index * 3
        stream = (f"BT /F1 12 Tf 20 250 Td ({text}) Tj ET" if text else "").encode("ascii")
        if compress:
            stream = zlib.compress(stream)
        objects.extend([
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
             f"/Resources << /Font << /F1 {page_id + 1} 0 R >> >> /Contents {page_id + 2} 0 R >>").encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(stream)).encode()
            + (b" /Filter /FlateDecode" if compress else b"")
            + b" >>\nstream\n" + stream + b"\nendstream",
        ])
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_id, body in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{object_id} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_offset = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode())
    return bytes(result)


def _synthetic_pdf_with_indirect_font_metrics() -> bytes:
    content = b"BT /F1 12 Tf 20 250 Td (A) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /TrueType /BaseFont /Helvetica /FirstChar 7 0 R "
        b"/LastChar 7 0 R /Widths [6 0 R] /Encoding 8 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"500",
        b"65",
        b"<< /Type /Encoding /Differences [7 0 R 9 0 R] >>",
        b"/A",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_id, body in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{object_id} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_offset = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode())
    return bytes(result)


def _synthetic_suspicious_tounicode_pdf() -> bytes:
    """A real ToUnicode map that deterministically maps every glyph to private-use text."""
    text = "synthetic damaged mapping with enough characters to trigger sanity checks"
    glyphs = "".join(f"<{index:04X}>" for index, _ in enumerate(text, 1))
    cmap = ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /SyntheticBadMap def\n/CMapType 2 def\n"
            "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
            f"{len(text)} beginbfchar\n" + "".join(
                f"<{index:04X}> <E000>\n" for index, _ in enumerate(text, 1)
            ) + "endbfchar\nendcmap\nCMapName currentdict /CMap defineresource pop\n"
            "end\nend\n").encode("ascii")
    stream = f"BT /F1 12 Tf 20 250 Td {glyphs} Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 7 0 R >>",
        b"<< /Type /Font /Subtype /Type0 /BaseFont /Synthetic /Encoding /Identity-H "
        b"/DescendantFonts [5 0 R] /ToUnicode 6 0 R >>",
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Synthetic "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 1000 >>",
        b"<< /Length " + str(len(cmap)).encode()
        + b" >>\nstream\n" + cmap + b"\nendstream",
        b"<< /Length " + str(len(stream)).encode()
        + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_id, body in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{object_id} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_offset = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode())
    return bytes(result)


def _synthetic_form_pdf(form_bytes: int = 0, do_count: int = 1, image: bool = False) -> bytes:
    form_text = b"BT /F1 12 Tf 20 250 Td (Form words) Tj ET" + b" " * form_bytes
    xobject_name = b"/Im1" if image else b"/F1"
    page_content = b"BT /F1 12 Tf 20 280 Td (Page words) Tj ET " + (xobject_name + b" Do ") * do_count
    page_content = page_content.rstrip()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 /Resources << /Font << /F1 5 0 R >> "
        + (b"/XObject << /Im1 6 0 R >> >> >>" if image
           else b"/XObject << /F1 6 0 R >> >> >>"),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents 4 0 R >>",
        b"<< /Length " + str(len(page_content)).encode() + b" >>\nstream\n"
        + page_content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
         b"/BitsPerComponent 8 /Length 1 >>\nstream\nx\nendstream" if image else
         b"<< /Type /XObject /Subtype /Form /BBox [0 0 300 300] "
         b"/Resources << /Font << /F1 5 0 R >> >> /Length " + str(len(form_text)).encode()
         + b" >>\nstream\n" + form_text + b"\nendstream"),
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_id, body in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{object_id} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_offset = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode())
    return bytes(result)


def test_asset_dedupes_raw_bytes_and_returns_logical_uri(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = b"invented image bytes"
    first = store.register_asset(payload, "application/octet-stream")
    second = store.register_asset(payload, "application/octet-stream")
    expected = "asset://sha256/" + hashlib.sha256(payload).hexdigest()
    assert first.uri == second.uri == expected
    assert first.size == len(payload)
    assert store.fetch_asset(expected, 0, 8) == payload[:8]
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1
    assert len(list((tmp_path / "assets").rglob(hashlib.sha256(payload).hexdigest()))) == 1


def test_original_assets_and_derived_document_pages_have_distinct_provenance(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = b"Invented source lesson"
    document = store.ingest_documents([DocumentInput("Synthetic lesson", "text/plain", payload)],
                                      identity=documents.ReportedIdentity(
                                          reported_agent="Codex", reported_client="local-mcp"))[0]

    asset_provenance = store.get_write_provenance("asset", document.asset_uri)
    document_provenance = store.get_write_provenance("document", document.uri)
    page_provenance = store.get_write_provenance("document_page", f"{document.uri}#page=1")
    assert asset_provenance["data_origin"] == "source"
    assert document_provenance["data_origin"] == "deterministic_derived"
    assert document_provenance["source_refs"] == [document.asset_uri]
    assert page_provenance["source_refs"] == [document.uri, document.asset_uri]
    assert page_provenance["reported_agent"] == "Codex"
    assert all(item["recorded_at"].endswith("Z") for item in
               (asset_provenance, document_provenance, page_provenance))


def test_same_bytes_dedupe_across_media_declarations_without_rewriting_first_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = b"Invented lesson text"
    first = store.register_asset(payload, "application/octet-stream")
    second = store.register_asset(payload, "text/plain")
    document = store.ingest_documents([DocumentInput("Invented", "text/plain", payload)])[0]
    assert first.uri == second.uri == document.asset_uri
    assert second.media_type == "application/octet-stream"
    assert store.fetch_asset_record(first.uri).media_type == "application/octet-stream"
    assert store.fetch_document(document.uri).media_type == "text/plain"
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1


def test_first_specific_media_declaration_stays_stable_on_repeat_generic_registration(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = b"Invented source"
    first = store.register_asset(payload, "text/plain")
    second = store.register_asset(payload, "application/octet-stream")
    assert second.uri == first.uri
    assert second.media_type == first.media_type == "text/plain"


def test_text_media_declaration_requires_utf8_text_bytes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(GatewayError) as error:
        store.register_asset(b"\xff", "text/plain")
    assert error.value.code == "INVALID_ARGUMENT"
    assert not store.database_path.exists()


def test_text_pages_chunks_and_search_have_stable_provenance(tmp_path: Path) -> None:
    store = _store(tmp_path)
    source = b"# Algebra\nFirst equation.\n## Examples\nSecond equation.\f# Geometry\nThird theorem."
    entry = DocumentInput("Invented workbook", "text/plain", source)
    first = store.ingest_documents([entry])[0]
    second = store.ingest_documents([entry])[0]
    assert first.uri == second.uri
    source_identity = "text/plain\x00" + hashlib.sha256(source).hexdigest()
    assert first.uri == "document://sha256/" + hashlib.sha256(source_identity.encode()).hexdigest()
    assert first.asset_uri == "asset://sha256/" + hashlib.sha256(source).hexdigest()
    assert first.page_count == 2
    assert [p.page_number for p in store.pages(first.uri)] == [1, 2]
    chunks = store.search("equation", limit=10).items
    assert len(chunks) == 2
    assert {c.page_number for c in chunks} == {1}
    assert all(c.uri.startswith("chunk://sha256/") and c.document_uri == first.uri for c in chunks)
    page = store.fetch_page(first.uri, 2)
    assert page.text == "# Geometry\nThird theorem."
    assert page.text_origin == "source_text"
    assert store.fetch_document(first.uri).title == "Invented workbook"


def test_pdf_extracts_real_text_layer_with_page_provenance_and_stable_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pdf = _synthetic_pdf("Synthetic lesson text", "Second synthetic page")
    entry = DocumentInput("Invented", "application/pdf", pdf)
    doc = store.ingest_documents([entry])[0]
    assert store.ingest_documents([entry])[0].uri == doc.uri
    assert doc.page_count == 2
    assert store.fetch_page(doc.uri, 1).text == "Synthetic lesson text"
    assert store.fetch_page(doc.uri, 1).text_origin == "pdf_text_layer"
    assert store.fetch_page(doc.uri, 1).text_layer_status == "unverified"
    assert store.fetch_page(doc.uri, 2).text == "Second synthetic page"
    chunks = store.search("Synthetic lesson").items
    assert len(chunks) == 1 and chunks[0].document_uri == doc.uri and chunks[0].page_number == 1
    assert chunks[0].uri.startswith("chunk://sha256/")
    assert store.fetch_page(doc.uri, 2).text_origin == "pdf_text_layer"
    assert store.fetch_asset(doc.asset_uri, 0, 8) == pdf[:8]


def test_pdf_supports_realistic_multi_hundred_page_provenance_boundary(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pdf = _synthetic_pdf(*(f"Synthetic page marker {number}" for number in range(1, 270)))
    document = store.ingest_documents([DocumentInput("Synthetic 269-page source", "application/pdf", pdf)])[0]
    assert document.page_count == 269
    page = store.fetch_page(document.uri, 269)
    assert page.page_number == 269
    assert page.text == "Synthetic page marker 269"
    assert page.text_origin == "pdf_text_layer"


def test_pdf_page_tree_node_limit_is_independent_of_page_count_limit() -> None:
    pages, origins = documents._extract_pdf_pages(
        _synthetic_pdf(*(f"Synthetic leaf {number}" for number in range(999))), max_ocr_pages=0,
    )
    assert len(pages) == len(origins) == 999
    assert pages[-1] == "Synthetic leaf 998"


def test_pdf_asset_limit_is_distinct_from_small_generic_asset_limit() -> None:
    pdf = b"%PDF-1.4\n" + b" " * (2_097_152 + 1)
    digest, uri = documents._validate_asset(pdf, "application/pdf")
    assert uri.endswith(digest)
    with pytest.raises(GatewayError) as error:
        documents._validate_asset(pdf, "application/octet-stream")
    assert error.value.code == "PAYLOAD_TOO_LARGE"


def test_pdf_empty_user_password_is_accepted_but_protected_pdf_fails_closed(tmp_path: Path) -> None:
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter

    plain = _synthetic_pdf("Synthetic encrypted text")
    writer = PdfWriter(clone_from=BytesIO(plain))
    writer.encrypt(user_password="", owner_password="synthetic-owner")
    output = BytesIO()
    writer.write(output)
    empty_password_pdf = output.getvalue()
    assert _extract_pdf_pages_for_test(empty_password_pdf) == ("Synthetic encrypted text",)

    writer = PdfWriter(clone_from=BytesIO(plain))
    writer.encrypt(user_password="not-known", owner_password="synthetic-owner")
    output = BytesIO()
    writer.write(output)
    with pytest.raises(GatewayError) as error:
        _extract_pdf_pages_for_test(output.getvalue())
    assert error.value.code == "UNSUPPORTED_MEDIA_TYPE"


def _extract_pdf_pages_for_test(pdf: bytes) -> tuple[str, ...]:
    pages, _origins = documents._extract_pdf_pages(pdf, max_ocr_pages=0)
    return pages


def test_pdf_font_indirect_metrics_are_normalized_before_extraction(tmp_path: Path) -> None:
    # Indirect members nested inside /W and /Encoding /Differences occur in valid PDFs.
    from io import BytesIO
    from pypdf import PdfReader
    from pypdf.generic import IndirectObject

    pdf = _synthetic_pdf_with_indirect_font_metrics()
    reader = PdfReader(BytesIO(pdf), strict=True)
    font = reader.pages[0]["/Resources"]["/Font"]["/F1"].get_object()
    assert isinstance(font["/Widths"][0], IndirectObject)
    assert isinstance(font["/Encoding"].get_object()["/Differences"][0], IndirectObject)
    documents._normalize_pdf_font_objects(reader)
    font = reader.pages[0]["/Resources"]["/Font"]["/F1"].get_object()
    assert not isinstance(font["/Widths"][0], IndirectObject)
    assert not isinstance(font["/Encoding"].get_object()["/Differences"][0], IndirectObject)


def test_pdf_quarantines_suspicious_tounicode_text_and_keeps_original_asset(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pdf = _synthetic_suspicious_tounicode_pdf()
    document = store.ingest_documents([DocumentInput("Synthetic damaged map", "application/pdf", pdf)])[0]
    page = store.fetch_page(document.uri, 1)

    assert page.text == ""
    assert page.text_origin == "pdf_text_layer_suspicious"
    assert page.text_layer_status == "suspicious"
    assert document.text_layer_status == "suspicious"
    assert store.search("synthetic").total == 0
    assert store.fetch_asset(document.asset_uri, 0, len(pdf)) == pdf


def test_pdf_runs_ocr_only_for_pages_without_text_layer(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    pdf = _synthetic_pdf("Synthetic text layer", "")
    calls = []

    def fake_ocr(data: bytes, page_number: int) -> str:
        calls.append((data, page_number))
        return "Synthetic OCR-derived words"

    monkeypatch.setattr(documents, "_ocr_runtime_available", lambda: True)
    monkeypatch.setattr(documents, "_ocr_pdf_page", fake_ocr)
    doc = store.ingest_documents([DocumentInput("Mixed synthetic", "application/pdf", pdf)])[0]
    assert calls == [(pdf, 2)]
    assert store.fetch_page(doc.uri, 1).text_origin == "pdf_text_layer"
    assert store.fetch_page(doc.uri, 2).text == "Synthetic OCR-derived words"
    assert store.fetch_page(doc.uri, 2).text_origin == "pdf_ocr_derived"
    assert doc.text_origin == "mixed_pdf_text_and_ocr"


def test_pdf_ocr_budget_prioritizes_no_text_pages_over_suspicious_text(tmp_path: Path, monkeypatch) -> None:
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for _ in range(documents.MAX_OCR_PAGES + 1):
        writer.add_page(PdfReader(BytesIO(_synthetic_suspicious_tounicode_pdf())).pages[0])
    writer.add_page(PdfReader(BytesIO(_synthetic_pdf(""))).pages[0])
    output = BytesIO()
    writer.write(output)
    pdf = output.getvalue()
    calls = []

    monkeypatch.setattr(documents, "_ocr_runtime_available", lambda: True)
    monkeypatch.setattr(documents, "_ocr_pdf_page",
                        lambda _data, number: calls.append(number) or f"OCR page {number}")
    store = _store(tmp_path)
    document = store.ingest_documents([DocumentInput("Synthetic OCR priority", "application/pdf", pdf)])[0]

    assert len(calls) == documents.MAX_OCR_PAGES
    assert calls == [10, 1, 2, 3, 4, 5, 6, 7]
    assert store.fetch_page(document.uri, 10).text == "OCR page 10"
    assert store.fetch_page(document.uri, 10).text_origin == "pdf_ocr_derived"
    assert store.fetch_page(document.uri, 1).text_origin == "pdf_ocr_derived"


def test_duplicate_pdf_ingestion_does_not_repeat_extraction_or_ocr(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    pdf = _synthetic_pdf("A deterministic lesson")
    entry = DocumentInput("Synthetic lesson", "application/pdf", pdf)
    first = store.ingest_documents([entry])[0]

    def extraction_must_not_run(*_args, **_kwargs):
        raise AssertionError("duplicate source was parsed again")

    monkeypatch.setattr(documents, "_normalize_pages", extraction_must_not_run)
    second = store.ingest_documents([entry])[0]
    assert second == first


def test_suspicious_pdf_pages_can_be_ocr_processed_in_bounded_continuation(
    tmp_path: Path, monkeypatch,
) -> None:
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    source_page = PdfReader(BytesIO(_synthetic_suspicious_tounicode_pdf())).pages[0]
    for _ in range(documents.MAX_OCR_PAGES + 1):
        writer.add_page(source_page)
    output = BytesIO()
    writer.write(output)
    pdf = output.getvalue()
    store = _store(tmp_path)
    monkeypatch.setattr(documents, "_ocr_runtime_available", lambda: True)
    monkeypatch.setattr(documents, "_ocr_pdf_page", lambda *_args: "Initial derived words")
    monkeypatch.setattr(documents, "_ocr_pdf_pages",
                        lambda _data, numbers: {number: "OCR continuation phrase" for number in numbers})
    document = store.ingest_documents([DocumentInput("Synthetic lesson", "application/pdf", pdf)])[0]

    assert store.list_ocr_candidates(document.uri) == (documents.MAX_OCR_PAGES + 1,)
    record_id = f"{document.uri}#page={documents.MAX_OCR_PAGES + 1}"
    prior_provenance = store.get_write_provenance("document_page", record_id)
    updated = store.process_ocr_pages(document.uri, [documents.MAX_OCR_PAGES + 1])
    next_provenance = store.get_write_provenance("document_page", record_id)
    page = store.fetch_page(document.uri, documents.MAX_OCR_PAGES + 1)
    assert updated.uri == document.uri
    assert page.text == "OCR continuation phrase"
    assert page.text_origin == "pdf_ocr_derived"
    assert page.text_layer_status == "ocr_derived"
    assert next_provenance["version"] == prior_provenance["version"] + 1
    assert next_provenance["supersedes_provenance_id"] == prior_provenance["provenance_id"]
    match = store.search("continuation phrase").items[0]
    assert match.document_uri == document.uri
    assert match.page_number == documents.MAX_OCR_PAGES + 1
    assert store.list_ocr_candidates(document.uri) == ()


def test_pdf_ocr_empty_output_is_not_reported_as_successful_text(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(documents, "_ocr_runtime_available", lambda: True)
    monkeypatch.setattr(documents, "_ocr_pdf_page", lambda *_args: " \n ")
    document = store.ingest_documents([
        DocumentInput("Synthetic blank scan", "application/pdf", _synthetic_pdf("")),
    ])[0]
    page = store.fetch_page(document.uri, 1)
    assert page.text == ""
    assert page.text_origin == "pdf_ocr_empty"
    assert page.text_layer_status == "ocr_empty"


def test_pdf_extraction_exception_uses_ocr_fallback(tmp_path: Path, monkeypatch) -> None:
    class DamagedTextPage:
        def extract_text(self, **_kwargs) -> str:
            raise ValueError("synthetic damaged text layer")

    class SyntheticReader:
        is_encrypted = False
        pages = [DamagedTextPage()]

    monkeypatch.setattr(documents, "PdfReader", lambda *_args, **_kwargs: SyntheticReader())
    monkeypatch.setattr(documents, "_preflight_pdf_streams", lambda _reader: (frozenset(),))
    monkeypatch.setattr(documents, "_ocr_runtime_available", lambda: True)
    monkeypatch.setattr(documents, "_ocr_pdf_page", lambda _data, _number: "Recovered synthetic text")
    store = _store(tmp_path)
    document = store.ingest_documents([DocumentInput(
        "Damaged synthetic text layer", "application/pdf", _synthetic_pdf("Placeholder"),
    )])[0]
    assert store.fetch_page(document.uri, 1).text == "Recovered synthetic text"
    assert store.fetch_page(document.uri, 1).text_origin == "pdf_ocr_derived"


def test_xform_extraction_exception_uses_ocr_fallback(tmp_path: Path, monkeypatch) -> None:
    class DamagedFormPage:
        def extract_text(self, **_kwargs) -> str:
            raise ValueError("synthetic XForm decode failure")

    class SyntheticReader:
        is_encrypted = False
        pages = [DamagedFormPage()]

    monkeypatch.setattr(documents, "PdfReader", lambda *_args, **_kwargs: SyntheticReader())
    monkeypatch.setattr(documents, "_preflight_pdf_streams", lambda _reader: (frozenset(),))
    monkeypatch.setattr(documents, "_ocr_runtime_available", lambda: True)
    monkeypatch.setattr(documents, "_ocr_pdf_page", lambda _data, _number: "Recovered complete text")
    store = _store(tmp_path)
    document = store.ingest_documents([DocumentInput(
        "Damaged synthetic form", "application/pdf", _synthetic_pdf("Placeholder"),
    )])[0]
    assert store.fetch_page(document.uri, 1).text == "Recovered complete text"
    assert store.fetch_page(document.uri, 1).text_origin == "pdf_ocr_derived"


def test_suppressed_xform_failure_warning_discards_partial_text(tmp_path: Path, monkeypatch) -> None:
    class DamagedFormPage:
        def extract_text(self) -> str:
            documents.pypdf_page.logger_warning(
                "Impossible to decode XFormObject %(operand)s: %(exception)s",
                source="pypdf._page", operand="/F1", exception="synthetic extraction error",
            )
            return "Partial text must not be persisted"

    class SyntheticReader:
        is_encrypted = False
        pages = [DamagedFormPage()]

    monkeypatch.setattr(documents, "PdfReader", lambda *_args, **_kwargs: SyntheticReader())
    monkeypatch.setattr(documents, "_preflight_pdf_streams", lambda _reader: (False,))
    monkeypatch.setattr(documents, "_ocr_runtime_available", lambda: True)
    monkeypatch.setattr(documents, "_ocr_pdf_page", lambda _data, _number: "Recovered complete text")
    monkeypatch.setattr(logging.getLogger("pypdf._page"), "level", logging.ERROR)
    store = _store(tmp_path)
    document = store.ingest_documents([DocumentInput(
        "Suppressed synthetic XForm", "application/pdf", _synthetic_pdf("Placeholder"),
    )])[0]
    page = store.fetch_page(document.uri, 1)
    assert page.text == "Recovered complete text"
    assert page.text_origin == "pdf_ocr_derived"


def test_pdf_preflight_caps_aggregate_form_stream_bytes() -> None:
    class SyntheticStream:
        def __init__(self, size: int) -> None:
            self.size = size

        def get_data(self) -> bytes:
            return b"x" * self.size

    class SyntheticForm(dict):
        def __init__(self, size: int) -> None:
            super().__init__({"/Subtype": "/Form", "/Resources": {}})
            self.stream = SyntheticStream(size)

        def get_data(self) -> bytes:
            return self.stream.get_data()

    stream_count = documents.MAX_PDF_DECODED_BYTES // documents.MAX_PDF_STREAM_BYTES + 1
    reader = type("SyntheticReader", (), {"pages": [
        {"/Contents": [SyntheticStream(documents.MAX_PDF_STREAM_BYTES) for _ in range(stream_count)],
         "/Resources": {}},
    ]})()
    with documents._pdf_decode_budget(), pytest.raises(GatewayError) as error:
        documents._preflight_pdf_streams(reader)
    assert error.value.code == "PAYLOAD_TOO_LARGE"


def test_pdf_preflight_caps_aggregate_font_cmap_stream_bytes() -> None:
    class SyntheticStream:
        def get_data(self) -> bytes:
            return b"x" * documents.MAX_PDF_FONT_STREAM_BYTES

    class SyntheticReader:
        pages = [{"/Contents": None, "/Resources": {
            "/Font": {f"/F{number}": {"/ToUnicode": SyntheticStream()}
                      for number in range(documents.MAX_PDF_DECODED_BYTES //
                                          documents.MAX_PDF_FONT_STREAM_BYTES + 1)},
        }}]

    with documents._pdf_decode_budget(), pytest.raises(GatewayError) as error:
        documents._preflight_pdf_streams(SyntheticReader())
    assert error.value.code == "PAYLOAD_TOO_LARGE"


def test_pdf_global_decode_budget_includes_object_streams() -> None:
    compressed_object_data = zlib.compress(b"o" * documents.MAX_PDF_FONT_STREAM_BYTES)
    with apply_configuration(zlib_maximum_output_length=documents.MAX_PDF_FONT_STREAM_BYTES), \
            documents._pdf_decode_budget():
        for _ in range(documents.MAX_PDF_DECODED_BYTES // documents.MAX_PDF_FONT_STREAM_BYTES):
            stream = EncodedStreamObject()
            stream[NameObject("/Type")] = NameObject("/ObjStm")
            stream[NameObject("/Filter")] = NameObject("/FlateDecode")
            stream._data = compressed_object_data
            assert len(stream.get_data()) == documents.MAX_PDF_FONT_STREAM_BYTES
        overflow = EncodedStreamObject()
        overflow[NameObject("/Type")] = NameObject("/ObjStm")
        overflow[NameObject("/Filter")] = NameObject("/FlateDecode")
        overflow._data = compressed_object_data
        with pytest.raises(LimitReachedError):
            overflow.get_data()


def test_pdf_global_decode_budget_includes_unfiltered_object_and_xref_streams() -> None:
    with documents._pdf_decode_budget():
        for index in range(documents.MAX_PDF_DECODED_BYTES // documents.MAX_PDF_FONT_STREAM_BYTES):
            stream = DecodedStreamObject()
            stream[NameObject("/Type")] = NameObject("/XRef" if index == 7 else "/ObjStm")
            stream.set_data(b"o" * documents.MAX_PDF_FONT_STREAM_BYTES)
            assert len(stream.get_data()) == documents.MAX_PDF_FONT_STREAM_BYTES
        overflow = DecodedStreamObject()
        overflow[NameObject("/Type")] = NameObject("/ObjStm")
        overflow.set_data(b"o" * documents.MAX_PDF_FONT_STREAM_BYTES)
        with pytest.raises(LimitReachedError):
            overflow.get_data()


def test_pdf_global_decode_budget_counts_nested_unfiltered_streams_separately(monkeypatch) -> None:
    from pypdf import filters
    from pypdf.generic import DictionaryObject

    nested = DecodedStreamObject()
    nested.set_data(b"g" * (documents.MAX_PDF_DECODED_BYTES // 2 + 1))
    outer = EncodedStreamObject()
    outer[NameObject("/Filter")] = NameObject("/JBIG2Decode")
    outer[NameObject("/DecodeParms")] = DictionaryObject({
        NameObject("/JBIG2Globals"): nested,
    })

    def decode_with_nested_global(stream) -> bytes:
        stream["/DecodeParms"]["/JBIG2Globals"].get_data()
        return b"o" * (documents.MAX_PDF_DECODED_BYTES // 2 + 1)

    monkeypatch.setattr(filters, "decode_stream_data", decode_with_nested_global)
    with documents._pdf_decode_budget(), pytest.raises(LimitReachedError):
        outer.get_data()


def test_pdf_preflight_counts_encoded_stream_once_across_decode_cache_clear() -> None:
    stream = EncodedStreamObject()
    stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    content = b"BT ET"
    stream._data = zlib.compress(content)
    reader = type("SyntheticReader", (), {"pages": [{"/Contents": stream, "/Resources": {}}]})()

    with documents._pdf_decode_budget() as budget:
        documents._preflight_pdf_streams(reader)
        assert budget.decoded_bytes == len(content)
        assert stream.get_data() == content
        assert budget.decoded_bytes == len(content)


def test_pdf_preflight_accounts_for_repeated_form_expansion() -> None:
    class SyntheticForm(dict):
        def __init__(self) -> None:
            super().__init__({"/Subtype": "/Form", "/Resources": {}})

        def get_data(self) -> bytes:
            return b"x" * documents.MAX_PDF_STREAM_BYTES

    reader = type("SyntheticReader", (), {"pages": [
        {"/Contents": None, "/Resources": {"/XObject": {"/Repeated": SyntheticForm()}}},
    ]})()
    with pytest.raises(GatewayError) as error:
        documents._preflight_pdf_streams(reader)
    assert error.value.code == "PAYLOAD_TOO_LARGE"


def test_pdf_preflight_rejects_recursive_form_resources() -> None:
    class SyntheticForm(dict):
        def get_data(self) -> bytes:
            return b"BT ET"

    resources = {}
    form = SyntheticForm({"/Subtype": "/Form"})
    form["/Resources"] = resources
    resources["/XObject"] = {"/X1": form}
    reader = type("SyntheticReader", (), {"pages": [
        {"/Contents": None, "/Resources": resources},
    ]})()
    with pytest.raises(GatewayError) as error:
        documents._preflight_pdf_streams(reader)
    assert error.value.code == "INVALID_ARGUMENT"


def test_pdf_preflight_reads_form_resources_from_parent() -> None:
    class SyntheticForm(dict):
        def get_data(self) -> bytes:
            return b"x" * documents.MAX_PDF_STREAM_BYTES

        def get_inherited(self, key: str, default=None):
            current = self
            while current is not None:
                if key in current:
                    return current[key]
                current = current.get("/Parent")
            return default

    parent = {"/Resources": {"/XObject": {}}}
    outer = SyntheticForm({"/Subtype": "/Form", "/Parent": parent})
    parent["/Resources"]["/XObject"]["/Nested"] = SyntheticForm({"/Subtype": "/Form"})
    reader = type("SyntheticReader", (), {"pages": [
        {"/Contents": None, "/Resources": {"/XObject": {"/Outer": outer}}},
    ]})()
    with pytest.raises(GatewayError) as error:
        documents._preflight_pdf_streams(reader)
    assert error.value.code == "PAYLOAD_TOO_LARGE"


def test_pdf_indirect_reference_cycle_is_bounded() -> None:
    class Reference:
        def get_object(self):
            return self.target

    first, second = Reference(), Reference()
    first.target = second
    second.target = first
    with pytest.raises(GatewayError) as error:
        documents._resolve_pdf_object(first)
    assert error.value.code == "INVALID_ARGUMENT"


def test_pdf_preflight_invocation_count_uses_page_and_nested_resource_scope() -> None:
    class SyntheticStream(dict):
        def __init__(self, content: bytes) -> None:
            super().__init__()
            self.content = content

        def get_data(self) -> bytes:
            return self.content

        def get_object(self):
            return self

    class SyntheticForm(SyntheticStream):
        def __init__(self, resources: dict, content: bytes = b"BT ET") -> None:
            super().__init__(content)
            self["/Subtype"] = "/Form"
            self["/Resources"] = resources

    reader = type("SyntheticReader", (), {"pages": [
        {"/Contents": SyntheticStream(b"/X1 Do"), "/Resources": {
            "/XObject": {"/X1": SyntheticForm({"/XObject": {"/X1": {"/Subtype": "/Image"}}},
                                              b"/X1 Do " * 65)}}},
        {"/Contents": SyntheticStream(b"/X1 Do " * 65), "/Resources": {
            "/XObject": {"/X1": {"/Subtype": "/Image"}}}},
    ]})()
    assert documents._preflight_pdf_streams(reader) == (False, False)


def test_inherited_pdf_form_resources_are_preflighted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pdf = _synthetic_form_pdf(form_bytes=documents.MAX_PDF_STREAM_BYTES + 1)
    assert len(pdf) < documents.MAX_ASSET_BYTES
    with pytest.raises(GatewayError) as error:
        store.ingest_documents([DocumentInput("Inherited form", "application/pdf", pdf)])
    assert error.value.code == "PAYLOAD_TOO_LARGE"


def test_repeated_pdf_form_limit_uses_ocr_instead_of_partial_text(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(documents, "_ocr_runtime_available", lambda: True)
    monkeypatch.setattr(documents, "_ocr_pdf_page", lambda _data, _page: "Complete OCR text")
    pdf = _synthetic_form_pdf(do_count=documents.MAX_XFORM_INVOCATIONS + 1)
    document = store.ingest_documents([DocumentInput("Repeated form", "application/pdf", pdf)])[0]
    page = store.fetch_page(document.uri, 1)
    assert page.text == "Complete OCR text"
    assert page.text_origin == "pdf_ocr_derived"


def test_repeated_pdf_images_do_not_count_as_form_invocations(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pdf = _synthetic_form_pdf(do_count=documents.MAX_XFORM_INVOCATIONS + 1, image=True)
    document = store.ingest_documents([DocumentInput("Many images", "application/pdf", pdf)])[0]
    page = store.fetch_page(document.uri, 1)
    assert page.text == "Page words"
    assert page.text_origin == "pdf_text_layer"


def test_malformed_pdf_fails_closed_and_preserves_scanned_blank_page(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    with pytest.raises(GatewayError) as malformed:
        store.ingest_documents([DocumentInput("Malformed", "application/pdf", b"%PDF-not-a-pdf")])
    assert malformed.value.code == "INVALID_ARGUMENT"
    with pytest.raises(GatewayError) as empty_pdf:
        store.ingest_documents([DocumentInput("Empty PDF", "application/pdf", _synthetic_pdf())])
    assert empty_pdf.value.code == "PAYLOAD_TOO_LARGE"
    assert not store.database_path.exists()
    monkeypatch.setattr(documents, "_ocr_runtime_available", lambda: True)
    monkeypatch.setattr(documents, "_ocr_pdf_page", lambda _data, _page: "  ")
    blank = store.ingest_documents([DocumentInput("Blank scan", "application/pdf", _synthetic_pdf(""))])[0]
    assert store.fetch_page(blank.uri, 1).text == ""
    assert store.fetch_page(blank.uri, 1).text_origin == "pdf_ocr_empty"


def test_pdf_supplied_page_origin_requires_page_text(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(GatewayError) as error:
        store.ingest_documents([DocumentInput(
            "Synthetic PDF", "application/pdf", _synthetic_pdf("Synthetic text"),
            supplied_page_origin="ocr",
        )])
    assert error.value.code == "INVALID_ARGUMENT"


def test_scan_without_optional_ocr_support_fails_closed(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)

    def unavailable(_data: bytes, _page_number: int) -> str:
        raise GatewayError("UNSUPPORTED_MEDIA_TYPE", "PDF OCR support is not installed")

    monkeypatch.setattr(documents, "_ocr_runtime_available", lambda: False)
    monkeypatch.setattr(documents, "_ocr_pdf_page", unavailable)
    document = store.ingest_documents([
        DocumentInput("Synthetic scan", "application/pdf", _synthetic_pdf(""))
    ])[0]
    page = store.fetch_page(document.uri, 1)
    assert page.text == ""
    assert page.text_origin == "pdf_ocr_unavailable"
    assert page.text_layer_status == "ocr_unavailable"
    assert store.search("Synthetic scan").total == 0


def test_ocr_runtime_needs_tessdata_not_tesseract_on_path(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    (tessdata / "eng.traineddata").write_bytes(b"synthetic")
    chi = tessdata / "chi_sim.traineddata"
    chi.write_bytes(b"synthetic")
    monkeypatch.setenv("TESSDATA_PREFIX", str(tessdata))
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(
        get_tessdata=lambda: pytest.fail("explicit tessdata must skip executable discovery"),
    ))
    monkeypatch.setattr(documents, "shutil", SimpleNamespace(which=lambda _command: None), raising=False)
    assert documents._ocr_runtime_available() is True
    chi.unlink()
    assert documents._ocr_runtime_available() is False


def test_page_ocr_receives_explicit_tessdata_path(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    calls = []
    text_page = object()

    class SyntheticPage:
        rect = SimpleNamespace(width=100, height=100)

        def get_textpage_ocr(self, **kwargs):
            calls.append(kwargs)
            return text_page

        def get_text(self, _kind: str, *, textpage):
            assert textpage is text_page
            return "Synthetic OCR output"

    class SyntheticDocument:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __getitem__(self, index):
            assert index == 0
            return SyntheticPage()

    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    monkeypatch.setenv("TESSDATA_PREFIX", str(tessdata))
    fake_pymupdf = SimpleNamespace(open=lambda **_kwargs: SyntheticDocument())
    monkeypatch.setitem(sys.modules, "pymupdf", fake_pymupdf)

    assert documents._ocr_pdf_page(b"synthetic PDF bytes", 1) == "Synthetic OCR output"
    assert calls == [{"language": "chi_sim+eng", "dpi": 300, "full": True,
                      "tessdata": str(tessdata)}]


@pytest.mark.parametrize("contents", [("Synthetic readable page", ""), ("", "Synthetic readable page")])
def test_document_origin_discloses_ocr_unavailable_in_mixed_page_order(
    tmp_path: Path, monkeypatch, contents: tuple[str, str],
) -> None:
    def unavailable(_data: bytes, _page_number: int) -> str:
        raise GatewayError("UNSUPPORTED_MEDIA_TYPE", "PDF OCR support is not installed")

    monkeypatch.setattr(documents, "_ocr_runtime_available", lambda: False)
    monkeypatch.setattr(documents, "_ocr_pdf_page", unavailable)
    root = tmp_path / "assets"
    root.mkdir()
    store = SQLiteDocumentStore(root, tmp_path / "private.db")
    document = store.ingest_documents([DocumentInput("Mixed", "application/pdf", _synthetic_pdf(*contents))])[0]
    assert document.text_origin == "mixed_pdf_text_and_ocr_unavailable"
    assert document.text_layer_status == "ocr_unavailable"


def test_missing_ocr_runtime_does_not_reject_many_scanned_pages(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def unavailable(_data: bytes, number: int) -> str:
        calls.append(number)
        raise GatewayError("UNSUPPORTED_MEDIA_TYPE", "PDF OCR support is not installed")

    monkeypatch.setattr(documents, "_ocr_runtime_available", lambda: False)
    monkeypatch.setattr(documents, "_ocr_pdf_page", unavailable)
    store = _store(tmp_path)
    document = store.ingest_documents([DocumentInput(
        "Synthetic scanned pages", "application/pdf", _synthetic_pdf(*("" for _ in range(12))),
    )])[0]
    assert document.page_count == 12
    assert {page.text_origin for page in store.pages(document.uri)} == {"pdf_ocr_unavailable"}
    assert calls == []


def test_pdf_ocr_page_batch_is_bounded(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    calls = []
    monkeypatch.setattr(documents, "_ocr_runtime_available", lambda: True)
    monkeypatch.setattr(documents, "_ocr_pdf_page",
                        lambda _data, number: calls.append(number) or "Synthetic OCR text")
    pdf = _synthetic_pdf(*("" for _ in range(documents.MAX_OCR_PAGES + 1)))
    document = store.ingest_documents([DocumentInput("OCR batch", "application/pdf", pdf)])[0]
    assert calls == list(range(1, documents.MAX_OCR_PAGES + 1))
    assert store.fetch_page(document.uri, documents.MAX_OCR_PAGES + 1).text_origin == "pdf_ocr_limit_reached"


def test_compressed_pdf_content_stream_has_decompression_limit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pdf = _synthetic_pdf(" " * (documents.MAX_PDF_STREAM_BYTES * 3), compress=True)
    assert len(pdf) < documents.MAX_ASSET_BYTES
    with pytest.raises(GatewayError) as error:
        store.ingest_documents([DocumentInput("Compressed synthetic", "application/pdf", pdf)])
    assert error.value.code == "PAYLOAD_TOO_LARGE"


def test_pdf_ocr_total_batch_is_bounded(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    calls = []
    monkeypatch.setattr(documents, "_ocr_runtime_available", lambda: True)
    monkeypatch.setattr(documents, "_ocr_pdf_page",
                        lambda _data, number: calls.append(number) or "Synthetic OCR text")
    pdf = _synthetic_pdf(*("" for _ in range(documents.MAX_OCR_PAGES)))
    items = [DocumentInput(f"Scan {i}", "application/pdf", pdf + f"\n% synthetic-source-{i}".encode())
             for i in range(3)]
    documents_saved = store.ingest_documents(items)
    assert len(documents_saved) == 3
    assert len(calls) == documents.MAX_OCR_PAGES_PER_BATCH
    assert store.fetch_page(documents_saved[2].uri, 1).text_origin == "pdf_ocr_limit_reached"


def test_pdf_ocr_failed_attempts_count_toward_batch_limit(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    calls = []

    def failed(_data: bytes, number: int) -> str:
        calls.append(number)
        raise GatewayError("INVALID_ARGUMENT", "Synthetic page OCR failure")

    monkeypatch.setattr(documents, "_ocr_runtime_available", lambda: True)
    monkeypatch.setattr(documents, "_ocr_pdf_page", failed)
    pdf = _synthetic_pdf(*("" for _ in range(documents.MAX_OCR_PAGES)))
    saved = store.ingest_documents([
        DocumentInput(f"Failed OCR {number}", "application/pdf",
                      pdf + f"\n% synthetic-source-{number}".encode()) for number in range(3)
    ])
    assert len(saved) == 3
    assert len(calls) == documents.MAX_OCR_PAGES_PER_BATCH
    assert store.fetch_page(saved[0].uri, 1).text_origin == "pdf_ocr_failed"
    assert store.fetch_page(saved[2].uri, 1).text_origin == "pdf_ocr_limit_reached"


def test_supplied_ocr_is_labeled_derived_and_retains_original_visual_asset(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pdf = _synthetic_pdf("")
    doc = store.ingest_documents([DocumentInput(
        "Invented scan", "application/pdf", pdf, ("Invented OCR text",), "ocr",
    )])[0]
    source_identity = "application/pdf\x00" + hashlib.sha256(pdf).hexdigest()
    assert doc.uri == "document://sha256/" + hashlib.sha256(source_identity.encode()).hexdigest()
    assert doc.text_origin == "supplied_ocr_derived"
    assert store.fetch_page(doc.uri, 1).text_origin == "supplied_ocr_derived"
    assert store.fetch_asset(doc.asset_uri, 0, len(pdf)) == pdf


def test_batch_validation_is_atomic_for_malformed_and_oversized_entries(tmp_path: Path) -> None:
    store = _store(tmp_path)
    good = DocumentInput("Good", "text/plain", b"Synthetic valid page")
    for bad in (
        DocumentInput("Bad", "text/plain", b"\xff"),
        DocumentInput("Bad", "text/plain", b"x" * (2_097_152 + 1)),
        DocumentInput("Bad", "text/plain", b"x" * (100_000 + 1)),
        DocumentInput("Bad", "application/pdf", b"not a pdf", ("Synthetic",)),
    ):
        with pytest.raises(GatewayError):
            store.ingest_documents([good, bad])
        assert not store.database_path.exists()
        assert list((tmp_path / "assets").rglob("*")) == []
    with pytest.raises(GatewayError) as batch:
        store.ingest_documents([good] * 17)
    assert batch.value.code == "PAYLOAD_TOO_LARGE"


def test_conflict_rolls_back_whole_batch_and_preserves_existing_document(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = DocumentInput("Original", "text/plain", b"Synthetic content")
    saved = store.ingest_documents([original])[0]
    with pytest.raises(GatewayError) as error:
        store.ingest_documents([
            DocumentInput("Fresh", "text/plain", b"Fresh synthetic content"),
            DocumentInput("Different title", "text/plain", b"Synthetic content"),
        ])
    assert error.value.code == "CONFLICT"
    assert store.fetch_document(saved.uri).title == "Original"
    assert store.search("Fresh").total == 0
    fresh_digest = hashlib.sha256(b"Fresh synthetic content").hexdigest()
    assert not list((tmp_path / "assets").rglob(fresh_digest))
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM operation_audit").fetchone()[0] == 1


def test_fetch_and_search_bounds_reject_malformed_ids_and_sizes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = store.ingest_documents([DocumentInput("Invented", "text/plain", b"Invented text")])[0]
    with pytest.raises(GatewayError) as bad:
        store.fetch_page(doc.uri, 0)
    assert bad.value.code == "INVALID_ARGUMENT"
    with pytest.raises(GatewayError) as bad:
        store.fetch_asset(doc.asset_uri, 0, 65_537)
    assert bad.value.code == "PAYLOAD_TOO_LARGE"
    with pytest.raises(GatewayError) as bad:
        store.search("text", limit=21)
    assert bad.value.code == "INVALID_ARGUMENT"
    with pytest.raises(GatewayError) as bad:
        store.fetch_document("document://../private")
    assert bad.value.code == "INVALID_ARGUMENT"


def test_page_limit_applies_to_utf8_result_bytes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(GatewayError) as error:
        store.ingest_documents([DocumentInput("Invented", "text/plain", ("中" * 40_000).encode())])
    assert error.value.code == "PAYLOAD_TOO_LARGE"
    assert not store.database_path.exists()


def test_asset_fetch_rejects_blob_tampering(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.register_asset(b"original invented bytes", "application/octet-stream")
    blob = tmp_path / "assets" / record.sha256[:2] / record.sha256
    blob.write_bytes(b"tampered invented bytes")
    with pytest.raises(GatewayError) as error:
        store.fetch_asset(record.uri)
    assert error.value.code == "STORAGE_UNAVAILABLE"


def test_asset_range_reads_verify_full_content_on_every_fetch(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    payload = b"%PDF-1.4\n" + b"a" * (8 * 1024 * 1024)
    record = store.register_asset(payload, "application/pdf")
    hashes = []
    original = documents.hashlib.sha256

    def observed(content=b""):
        hashes.append(len(content))
        return original(content)

    monkeypatch.setattr(documents.hashlib, "sha256", observed)
    assert store.fetch_asset(record.uri, 0, 32_768) == payload[:32_768]
    assert store.fetch_asset(record.uri, 65_536, 32_768) == payload[65_536:98_304]
    assert hashes == [len(payload), len(payload)]
    blob = tmp_path / "assets" / record.sha256[:2] / record.sha256
    original_stat = blob.stat()
    blob.write_bytes(b"%PDF-1.4\n" + b"b" * (len(payload) - len(b"%PDF-1.4\n")))
    os.utime(blob, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    with pytest.raises(GatewayError) as tampered:
        store.fetch_asset(record.uri, 0, 32_768)
    assert tampered.value.code == "STORAGE_UNAVAILABLE"
    assert hashes == [len(payload), len(payload), len(payload)]
