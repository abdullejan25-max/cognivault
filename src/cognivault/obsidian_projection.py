"""Deterministic, in-memory Obsidian projection from Gateway read DTOs.

This module intentionally has no filesystem, database, network, or Gateway
dependency. The companion collector obtains and reconciles a complete, bounded
Gateway snapshot before rendering.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re

from .contracts import HistoryItem, HistorySource
from .adapters.documents import MAX_PAGES


_LOGICAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_HISTORY_ID = re.compile(r"history:[0-9a-f]{64}\Z")
_WRONG_SOURCE_ID = re.compile(r"wrong-answer://sha256/[0-9a-f]{64}\Z")
_WRONG_ANALYSIS_ID = re.compile(r"wrong-analysis://sha256/[0-9a-f]{64}\Z")
_ASSET_URI = re.compile(r"asset://sha256/[0-9a-f]{64}\Z")
_DOCUMENT_URI = re.compile(r"document://sha256/[0-9a-f]{64}\Z")
_UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PROVENANCE_ORIGINS = frozenset({
    "source", "deterministic_derived", "agent_generated", "imported", "system_generated",
})
_PROVENANCE_ACTORS = frozenset({"external_client", "importer", "system", "unknown"})
_PROVENANCE_TRUST = frozenset({"reported", "unavailable"})
_PROVENANCE_LEGACY = frozenset({"native", "imported", "pre_provenance"})
_ROLES = frozenset({"user", "assistant", "system", "tool"})
_MAX_RECORDS = 10_000
_MAX_ITEM_TEXT = 100_000
_MAX_ANSWER_TEXT = 10_000
_MAX_INPUT_BYTES = 32 * 1024 * 1024
_MAX_RENDERED_BYTES = 64 * 1024 * 1024
_MAX_SOURCE_ITEMS = 1_000_000_000


class ProjectionError(ValueError):
    """A fixed-message validation error that never echoes private input."""

    def __init__(self) -> None:
        super().__init__("Invalid projection snapshot")


@dataclass(frozen=True)
class ProjectionSnapshot:
    """Caller-supplied Gateway snapshot; completeness is not implied."""

    history_sources: tuple[HistorySource, ...] = ()
    history_items: tuple[HistoryItem, ...] = ()
    wrong_answer_bundles: tuple[dict, ...] = ()
    legacy_sources: tuple[dict, ...] = ()


def _timestamp_value(value: object) -> datetime | None:
    if type(value) is not str or not _UTC_TIMESTAMP.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None


def _timestamp(value: object) -> bool:
    return _timestamp_value(value) is not None


def _valid_text(value: object, maximum: int, *, allow_empty: bool = False) -> bool:
    if type(value) is not str or len(value) > maximum or (not allow_empty and not value.strip()) \
            or "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _valid_inline_text(value: object, maximum: int) -> bool:
    return _valid_text(value, maximum) and not any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)


def _valid_study_relation(value: object) -> bool:
    if type(value) is not str or not value.startswith("study:") or len(value) > 2_048:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        return False
    relative = value[6:]
    if not relative or "\\" in relative or relative.startswith("/") \
            or re.match(r"[A-Za-z]:", relative):
        return False
    parts = relative.split("/")
    return all(part not in {"", ".", ".."} and ":" not in part for part in parts)


def _summary_provenance(value: object) -> str:
    if type(value) is not dict:
        return "unavailable"
    origin = value.get("data_origin")
    actor = value.get("actor_type")
    trust = value.get("identity_trust")
    legacy = value.get("legacy_status")
    if type(origin) is not str or type(actor) is not str or type(trust) is not str \
            or type(legacy) is not str \
            or origin not in _PROVENANCE_ORIGINS or actor not in _PROVENANCE_ACTORS \
            or trust not in _PROVENANCE_TRUST or legacy not in _PROVENANCE_LEGACY:
        return "unverified"
    identity = "reported (unverified)" if trust == "reported" else "unavailable"
    return f"origin={origin}; actor={actor}; identity={identity}; legacy={legacy}"


def _frontmatter(values: dict[str, object]) -> str:
    lines = ["---"]
    for key in sorted(values):
        lines.append(f"{key}: {json.dumps(values[key], ensure_ascii=False, sort_keys=True)}")
    lines.append("---")
    return "\n".join(lines)


def _literal_block(value: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", value)), default=0)
    fence = "`" * max(3, longest + 1)
    trailing_newline = "" if not value or value.endswith("\n") else "\n"
    return f"{fence}text\n{value}{trailing_newline}{fence}"


def _code_span(value: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", value)), default=0)
    fence = "`" * (longest + 1)
    return f"{fence} {value} {fence}"


def _hash_path(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _history_item_path(item: HistoryItem) -> str:
    """Hash all logical identity parts once to keep generated paths Windows-safe."""
    logical_key = "\0".join((item.source_id, item.conversation_id, item.item_id))
    return f"History/items/{_hash_path(logical_key)}.md"


def _validate_history(
    snapshot: ProjectionSnapshot,
) -> tuple[dict[str, HistorySource], tuple[HistoryItem, ...], int]:
    if type(snapshot.history_sources) is not tuple or len(snapshot.history_sources) > _MAX_RECORDS \
            or type(snapshot.history_items) is not tuple or len(snapshot.history_items) > _MAX_RECORDS:
        raise ProjectionError
    sources: dict[str, HistorySource] = {}
    for source in snapshot.history_sources:
        if type(source) is not HistorySource or type(source.source_id) is not str \
                or not _LOGICAL_ID.fullmatch(source.source_id) \
                or type(source.item_count) is not int or not 0 <= source.item_count <= _MAX_SOURCE_ITEMS \
                or source.source_id in sources:
            raise ProjectionError
        sources[source.source_id] = source

    seen_items: set[str] = set()
    seen_source_items: set[tuple[str, str]] = set()
    included_by_source: defaultdict[str, int] = defaultdict(int)
    items = []
    input_bytes = 0
    for item in snapshot.history_items:
        if type(item) is not HistoryItem or type(item.item_id) is not str \
                or not _HISTORY_ID.fullmatch(item.item_id) \
                or type(item.source_id) is not str or item.source_id not in sources \
                or type(item.source_item_id) is not str or not _LOGICAL_ID.fullmatch(item.source_item_id) \
                or type(item.conversation_id) is not str or not _LOGICAL_ID.fullmatch(item.conversation_id) \
                or type(item.role) is not str or item.role not in _ROLES \
                or not _timestamp(item.created_at) \
                or type(item.content_sha256) is not str or not _SHA256.fullmatch(item.content_sha256) \
                or not _valid_text(item.content, _MAX_ITEM_TEXT, allow_empty=True) \
                or (item.imported_at is not None and not _timestamp(item.imported_at)) \
                or item.item_id in seen_items or (item.source_id, item.source_item_id) in seen_source_items:
            raise ProjectionError
        seen_items.add(item.item_id)
        seen_source_items.add((item.source_id, item.source_item_id))
        input_bytes += len(item.content.encode("utf-8"))
        if input_bytes > _MAX_INPUT_BYTES:
            raise ProjectionError
        included_by_source[item.source_id] += 1
        items.append(item)
    if any(included_by_source[source_id] > source.item_count for source_id, source in sources.items()):
        raise ProjectionError
    return sources, tuple(items), input_bytes


def _validate_wrong_bundle(bundle: object) -> tuple[dict, tuple[dict, ...], int]:
    if type(bundle) is not dict or not {"source", "analyses", "total", "has_more"} <= set(bundle) \
            or bundle.get("has_more") is not False:
        raise ProjectionError
    source = bundle["source"]
    analyses = bundle["analyses"]
    total = bundle["total"]
    if type(source) is not dict or "page_number" not in source \
            or type(analyses) is not list or len(analyses) > _MAX_RECORDS \
            or type(total) is not int or total != len(analyses):
        raise ProjectionError
    source_id = source.get("source_id")
    if type(source_id) is not str or not _WRONG_SOURCE_ID.fullmatch(source_id) \
            or type(source.get("source_uri")) is not str \
            or not (_ASSET_URI.fullmatch(source["source_uri"]) or _DOCUMENT_URI.fullmatch(source["source_uri"])) \
            or (source.get("page_number") is not None and
                (type(source["page_number"]) is not int or not 1 <= source["page_number"] <= MAX_PAGES
                 or source["source_uri"].startswith("asset://"))) \
            or not _valid_text(source.get("question_text"), _MAX_ANSWER_TEXT) \
            or not _valid_text(source.get("student_answer"), _MAX_ANSWER_TEXT) \
            or not _timestamp(source.get("created_at")):
        raise ProjectionError

    seen_versions: set[int] = set()
    seen_analysis_ids: set[str] = set()
    checked = []
    input_bytes = (len(source["question_text"].encode("utf-8"))
                   + len(source["student_answer"].encode("utf-8")))
    for analysis in analyses:
        if type(analysis) is not dict or analysis.get("source_id") != source_id \
                or "supersedes_analysis_id" not in analysis \
                or type(analysis.get("analysis_id")) is not str \
                or not _WRONG_ANALYSIS_ID.fullmatch(analysis["analysis_id"]) \
                or analysis["analysis_id"] in seen_analysis_ids \
                or type(analysis.get("version")) is not int or not 1 <= analysis["version"] <= _MAX_RECORDS \
                or analysis["version"] in seen_versions \
                or not _timestamp(analysis.get("created_at")) \
                or type(analysis.get("generated_by_agent")) is not bool \
                or analysis.get("provenance") != "agent_supplied" \
                or (analysis.get("supersedes_analysis_id") is not None and
                    (type(analysis["supersedes_analysis_id"]) is not str or
                     not _WRONG_ANALYSIS_ID.fullmatch(analysis["supersedes_analysis_id"]))):
            raise ProjectionError
        if not _valid_inline_text(analysis.get("error_type"), 200) \
                or type(analysis.get("knowledge_points")) is not list \
                or not 1 <= len(analysis["knowledge_points"]) <= 20 \
                or any(not _valid_inline_text(point, 200) for point in analysis["knowledge_points"]):
            raise ProjectionError
        for field in ("reasoning", "correct_solution", "review_advice"):
            if not _valid_text(analysis.get(field), _MAX_ANSWER_TEXT):
                raise ProjectionError
        refs = analysis.get("source_refs")
        relations = analysis.get("study_relations")
        if type(refs) is not list or not 1 <= len(refs) <= 16 \
                or any(type(ref) is not str or not (_ASSET_URI.fullmatch(ref) or _DOCUMENT_URI.fullmatch(ref))
                       for ref in refs) or source["source_uri"] not in refs or len(set(refs)) != len(refs) \
                or type(relations) is not list or len(relations) > 16 \
                or any(not _valid_study_relation(ref) for ref in relations) \
                or len(set(relations)) != len(relations):
            raise ProjectionError
        seen_versions.add(analysis["version"])
        seen_analysis_ids.add(analysis["analysis_id"])
        input_bytes += sum(len(analysis[field].encode("utf-8"))
                           for field in ("error_type", "reasoning", "correct_solution", "review_advice"))
        input_bytes += sum(len(point.encode("utf-8")) for point in analysis["knowledge_points"])
        input_bytes += sum(len(ref.encode("utf-8")) for ref in refs)
        input_bytes += sum(len(ref.encode("utf-8")) for ref in relations)
        if input_bytes > _MAX_INPUT_BYTES:
            raise ProjectionError
        checked.append(analysis)
    versions = sorted(seen_versions)
    if versions and versions != list(range(1, versions[-1] + 1)):
        raise ProjectionError
    ordered_analyses = tuple(sorted(checked, key=lambda analysis: analysis["version"]))
    for index, analysis in enumerate(ordered_analyses):
        expected_previous = ordered_analyses[index - 1]["analysis_id"] if index else None
        if analysis["supersedes_analysis_id"] != expected_previous:
            raise ProjectionError
    return source, ordered_analyses, input_bytes


def _render_history_item(item: HistoryItem) -> tuple[str, str]:
    path = _history_item_path(item)
    values = {
        "type": "history_item",
        "logical_id": item.item_id,
        "source_id": item.source_id,
        "source_item_id": item.source_item_id,
        "conversation_id": item.conversation_id,
        "event_time": item.created_at,
        "imported_at": item.imported_at,
        "source_type": "history_item",
        "role": item.role,
        "provenance_summary": _summary_provenance(item.write_provenance),
        "generated": True,
    }
    body = (f"# {item.role.title()} message\n\n"
            f"Event time: `{item.created_at}`\n\n"
            f"{_literal_block(item.content)}\n")
    return path, f"{_frontmatter(values)}\n\n{body}"


def _render_wrong_bundle(source: dict, analyses: tuple[dict, ...], max_bytes: int) -> tuple[str, str, int]:
    source_id = source["source_id"]
    path = f"WrongAnswers/items/{_hash_path(source_id)}.md"
    latest = analyses[-1] if analyses else None
    values = {
        "type": "wrong_answer",
        "logical_id": source_id,
        "source_id": source_id,
        "version": latest["version"] if latest else 0,
        "event_time": source["created_at"],
        "subject": None,
        "source_refs": [source["source_uri"]],
        "study_relations": latest["study_relations"] if latest else [],
        "provenance_summary": _summary_provenance(source.get("write_provenance")),
        "generated": True,
    }
    chunks = [f"{_frontmatter(values)}\n\n"]
    rendered_bytes = len(chunks[0].encode("utf-8"))
    if rendered_bytes > max_bytes:
        raise ProjectionError

    def append(text: str) -> None:
        nonlocal rendered_bytes
        text_bytes = len(text.encode("utf-8"))
        if rendered_bytes + text_bytes > max_bytes:
            raise ProjectionError
        rendered_bytes += text_bytes
        chunks.append(text)

    append("# Wrong Answer\n\n")
    append(f"Source: `{source['source_uri']}`\n\n")
    append(f"Page: {_code_span(str(source['page_number']))}\n\n"
           if source["page_number"] is not None else "Page: unknown\n\n")
    append("Subject: unavailable (subject is not a field in the current Gateway DTO)\n\n")
    append("## Question\n\n")
    append(_literal_block(source["question_text"]))
    append("\n\n## Student answer\n\n")
    append(_literal_block(source["student_answer"]))
    if not analyses:
        append("\n\nNo analysis version is included in the supplied snapshot.")
    for analysis in analyses:
        append(f"\n\n## Version {analysis['version']}\n\n")
        append(f"Analysis ID: {_code_span(analysis['analysis_id'])}\n\n")
        append(f"Generated by agent: {str(analysis['generated_by_agent']).lower()}\n\n")
        append(f"Agent provenance: {_code_span(analysis['provenance'])}\n\n")
        if analysis["supersedes_analysis_id"] is not None:
            append(f"Supersedes: {_code_span(analysis['supersedes_analysis_id'])}\n\n")
        append(f"Created: {_code_span(analysis['created_at'])}\n\n")
        append(f"Error type: {_code_span(analysis['error_type'])}\n\n")
        append("Knowledge points: " + ", ".join(
            _code_span(point) for point in analysis["knowledge_points"]) + "\n\n")
        append("Source refs: " + ", ".join(
            _code_span(ref) for ref in analysis["source_refs"]) + "\n\n")
        append("Study relations: " + (", ".join(
            _code_span(ref) for ref in analysis["study_relations"])
            if analysis["study_relations"] else "none") + "\n\n")
        append(f"Provenance: {_summary_provenance(analysis.get('write_provenance'))}\n\n")
        append("### Reasoning\n\n")
        append(_literal_block(analysis["reasoning"]))
        append("\n\n### Correct solution\n\n")
        append(_literal_block(analysis["correct_solution"]))
        append("\n\n### Review advice\n\n")
        append(_literal_block(analysis["review_advice"]))
    append("\n")
    return path, "".join(chunks), rendered_bytes


def render_projection(snapshot: ProjectionSnapshot) -> dict[str, str]:
    """Render bounded Gateway DTOs without writing or claiming full coverage."""
    if type(snapshot) is not ProjectionSnapshot \
            or type(snapshot.history_sources) is not tuple \
            or type(snapshot.history_items) is not tuple \
            or type(snapshot.wrong_answer_bundles) is not tuple \
            or type(snapshot.legacy_sources) is not tuple \
            or len(snapshot.wrong_answer_bundles) > _MAX_RECORDS \
            or (len(snapshot.history_sources) + len(snapshot.history_items) +
                len(snapshot.wrong_answer_bundles) + len(snapshot.legacy_sources) > _MAX_RECORDS):
        raise ProjectionError
    sources, history_items, input_bytes = _validate_history(snapshot)
    wrong_bundles = []
    analysis_count = 0
    for bundle in snapshot.wrong_answer_bundles:
        source, analyses, bundle_bytes = _validate_wrong_bundle(bundle)
        input_bytes += bundle_bytes
        analysis_count += len(analyses)
        if input_bytes > _MAX_INPUT_BYTES or analysis_count > _MAX_RECORDS:
            raise ProjectionError
        wrong_bundles.append((source, analyses))

    files: dict[str, str] = {}
    rendered_size = 0

    def add_file(path: str, content: str, *, size: int | None = None) -> None:
        nonlocal rendered_size
        if size is None:
            size = len(content.encode("utf-8"))
        previous_size = len(files[path].encode("utf-8")) if path in files else 0
        if rendered_size - previous_size + size > _MAX_RENDERED_BYTES:
            raise ProjectionError
        rendered_size += size - previous_size
        files[path] = content

    items_by_conversation: defaultdict[tuple[str, str], list[HistoryItem]] = defaultdict(list)
    included_by_source: defaultdict[str, int] = defaultdict(int)
    for item in history_items:
        path, content = _render_history_item(item)
        add_file(path, content)
        items_by_conversation[(item.source_id, item.conversation_id)].append(item)
        included_by_source[item.source_id] += 1

    history_lines = [
        "# History",
        "",
        "> Scope: `provided_input_only`; this index does not assert a complete V2 inventory.",
        "> Items with equal event times are listed by logical ID for deterministic display; that tie-break does not establish original event order.",
    ]
    for source_id, conversation_id in sorted(items_by_conversation):
        history_lines.extend(["", f"## `{source_id}` / `{conversation_id}`"])
        group = sorted(items_by_conversation[(source_id, conversation_id)],
                       key=lambda item: (_timestamp_value(item.created_at), item.item_id))
        for item in group:
            page = _history_item_path(item).removeprefix("History/")
            history_lines.append(f"- [{item.role} — {item.created_at}]({page})")
    add_file("History/index.md", "\n".join(history_lines) + "\n")

    timeline_lines = [
        "# History timeline",
        "",
        "> Scope: `provided_input_only`; this timeline contains only supplied History items.",
        "> Equal event times are listed by logical ID; that tie-break does not establish original event order.",
    ]
    for item in sorted(history_items, key=lambda row: (_timestamp_value(row.created_at), row.item_id)):
        page = _history_item_path(item).removeprefix("History/")
        timeline_lines.append(f"- [{item.role} — {item.created_at}]({page})")
    if not history_items:
        timeline_lines.append("- No History items were supplied.")
    add_file("History/timeline.md", "\n".join(timeline_lines) + "\n")

    source_lines = ["# History sources", "", "> Counts are reported by the supplied Gateway snapshot."]
    for source_id in sorted(sources):
        source = sources[source_id]
        path = f"history/{_hash_path(source_id)}.md"
        source_lines.append(f"- [`{source_id}`]({path}): {included_by_source[source_id]} included / "
                            f"{source.item_count} reported items")
        source_path = f"Sources/{path}"
        add_file(source_path, (f"{_frontmatter({'type': 'history_source', 'logical_id': source_id, 'source_id': source_id, 'reported_item_count': source.item_count, 'included_item_count': included_by_source[source_id], 'generated': True})}\n\n"
                               f"# History source\n\nSource ID: `{source_id}`\n\n"
                               f"Included items: {included_by_source[source_id]}\n\n"
                               f"Gateway-reported item count: {source.item_count}\n"))
    add_file("Sources/index.md", "\n".join(source_lines) + "\n")

    seen_wrong_sources: set[str] = set()
    wrong_index = [
        "# Wrong Answers",
        "",
        "> Scope: `provided_input_only`; this index does not assert a complete V2 inventory.",
    ]
    rendered_wrong = []
    knowledge_point_sources: defaultdict[str, set[str]] = defaultdict(set)
    error_type_sources: defaultdict[str, set[str]] = defaultdict(set)
    for source, analyses in wrong_bundles:
        if source["source_id"] in seen_wrong_sources:
            raise ProjectionError
        seen_wrong_sources.add(source["source_id"])
        path, content, content_size = _render_wrong_bundle(
            source, analyses, _MAX_RENDERED_BYTES - rendered_size,
        )
        files[path] = content
        rendered_size += content_size
        rendered_wrong.append((source, path))
        for analysis in analyses:
            error_type_sources[analysis["error_type"]].add(source["source_id"])
            for point in analysis["knowledge_points"]:
                knowledge_point_sources[point].add(source["source_id"])
    rendered_wrong.sort(key=lambda row: (_timestamp_value(row[0]["created_at"]), row[0]["source_id"]),
                        reverse=True)
    for source, path in rendered_wrong:
        relative = path.removeprefix("WrongAnswers/")
        wrong_index.append(f"- [{source['created_at']} — {source['source_id']}]({relative})")
    add_file("WrongAnswers/index.md", "\n".join(wrong_index) + "\n")

    def render_term_index(title: str, description: str,
                          terms: dict[str, set[str]], max_bytes: int) -> tuple[str, int]:
        chunks: list[str] = []
        content_bytes = 0

        def append(text: str) -> None:
            nonlocal content_bytes
            text_bytes = len(text.encode("utf-8"))
            if content_bytes + text_bytes > max_bytes:
                raise ProjectionError
            chunks.append(text)
            content_bytes += text_bytes

        append(f"# {title}\n\n> Scope: `provided_input_only`; {description}.")
        for term in sorted(terms):
            append(f"\n- {_code_span(term)}: ")
            first_link = True
            for source_id in sorted(terms[term]):
                source_path = f"../WrongAnswers/items/{_hash_path(source_id)}.md"
                append("" if first_link else ", ")
                append(f"[Wrong Answer {source_id}]({source_path})")
                first_link = False
        if not terms:
            append("\n- No terms are present in the supplied analyses.")
        append("\n")
        return "".join(chunks), content_bytes

    for path, title, description, terms in (
        ("KnowledgePoints/index.md", "Knowledge Points",
         "Knowledge points reported by supplied Agent analyses", knowledge_point_sources),
        ("ErrorTypes/index.md", "Error Types",
         "Error types reported by supplied Agent analyses", error_type_sources),
    ):
        content, content_size = render_term_index(
            title, description, terms, _MAX_RENDERED_BYTES - rendered_size,
        )
        add_file(path, content, size=content_size)

    add_file("Dashboard.md", (
        "# V2 projection\n\n"
        "> Scope: `provided_input_only`; these counts describe only the Gateway DTOs supplied to this render.\n\n"
        f"- History messages supplied: {len(history_items)}\n"
        f"- History sources supplied: {len(sources)}\n"
        f"- Wrong-answer sources supplied: {len(rendered_wrong)}\n\n"
        "## Views\n\n"
        "- [History](History/index.md)\n"
        "- [History timeline](History/timeline.md)\n"
        "- [Wrong Answers](WrongAnswers/index.md)\n"
        "- [Sources](Sources/index.md)\n\n"
        "- [Knowledge Points](KnowledgePoints/index.md)\n"
        "- [Error Types](ErrorTypes/index.md)\n\n"
        "## Recent Wrong Answers (up to 5 supplied sources)\n\n"
        + ("\n".join(
            f"- [{source['created_at']} — {source['source_id']}]({path})"
            for source, path in rendered_wrong[:5]
        ) or "- No Wrong Answer sources were supplied.\n")
        + "\n## Unavailable facets\n\n"
        "- Subject index unavailable: subject is not a field in the current Wrong Answer Gateway DTO.\n"
        "- V2 status and unresolved Legacy summary are not supplied to this renderer.\n\n"
        "Study remains in its authoritative Markdown location and is not copied by this renderer.\n"
    ))

    from .source_projection import render_sources
    for path, content in render_sources(snapshot.legacy_sources).items():
        if path == "Sources/index.md":
            content += "\n## Canonical History sources\n\n" + "\n".join(source_lines[3:]) + "\n"
        add_file(path, content)
    add_file("Dashboard.md", files["Dashboard.md"] + (f"\n## Source documents\n\n- Imported source documents: {len(snapshot.legacy_sources)}\n"
                              "- Source documents are not normalized conversations or messages.\n"
                              "- [Study references](Study/index.md)\n- [Logical references](References/index.md)\n"))
    add_file("Study/index.md", "# Study references\n\nStudy remains authoritative in its existing location.\n"
             "Projection does not copy or rewrite Study content.\n")
    refs = set()
    for record in snapshot.legacy_sources:
        refs.update(record["source_refs"])
    for source, analyses in wrong_bundles:
        refs.add(source["source_uri"])
        for analysis in analyses:
            refs.update(analysis["source_refs"])
    add_file("References/index.md", "# Assets / Documents logical references\n\n"
             "Original bytes remain in the Gateway. These logical URIs are not filesystem paths.\n\n"
             + "\n".join(f"- `{ref}`" for ref in sorted(refs)) + "\n")
    ordered_files = dict(sorted(files.items()))
    if len(ordered_files) > _MAX_RECORDS + 13:
        raise ProjectionError
    return ordered_files
