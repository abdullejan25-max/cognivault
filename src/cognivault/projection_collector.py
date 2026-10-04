"""Complete bounded Gateway pages into an explicitly reconciled renderer snapshot."""

from dataclasses import dataclass, fields
from typing import Protocol

from .contracts import HistoryItem, HistorySource
from .obsidian_projection import ProjectionSnapshot


_PAGE_SIZE = 20
_MAX_RECORDS = 10_000
_MAX_BYTES = 32 * 1024 * 1024


class ProjectionCollectionError(ValueError):
    """A fixed-message error for an incomplete or inconsistent Gateway snapshot."""

    def __init__(self) -> None:
        super().__init__("Projection snapshot is incomplete or inconsistent")


class ProjectionGateway(Protocol):
    def projection_snapshot(self, domain: str, operation: str, *,
                            snapshot_token: str | None = None, cursor: int = 0,
                            source_id: str | None = None,
                            limit: int = _PAGE_SIZE) -> dict: ...


@dataclass(frozen=True)
class ProjectionCollection:
    snapshot: ProjectionSnapshot
    history_snapshot_token: str
    wrong_answer_snapshot_token: str
    history_source_count: int
    history_item_count: int
    wrong_answer_source_count: int
    wrong_answer_analysis_count: int
    legacy_snapshot_token: str = ""


def _count(value: object, maximum: int = _MAX_RECORDS) -> bool:
    return type(value) is int and 0 <= value <= maximum


def _begin(gateway: ProjectionGateway, domain: str) -> tuple[str, int, int]:
    page = gateway.projection_snapshot(domain, "begin")
    if type(page) is not dict or type(page.get("snapshot_token")) is not str \
            or not page["snapshot_token"] or len(page["snapshot_token"]) > 64 \
            or not _count(page.get("total_sources")) \
            or not _count(page.get("total_records")) \
            or type(page.get("stored_payload_bytes")) is not int \
            or not 0 <= page["stored_payload_bytes"] <= _MAX_BYTES:
        raise ProjectionCollectionError
    source_count = page["total_sources"]
    record_count = page["total_records"]
    if domain == "history" and source_count + record_count > _MAX_RECORDS:
        raise ProjectionCollectionError
    return page["snapshot_token"], source_count, record_count


def _pages(gateway: ProjectionGateway, domain: str, operation: str, token: str,
           key: str, *, expected_count: int | None = None,
           source_id: str | None = None):
    cursor = 0
    while True:
        page = gateway.projection_snapshot(
            domain, operation, snapshot_token=token, cursor=cursor,
            source_id=source_id, limit=_PAGE_SIZE,
        )
        if type(page) is not dict or page.get("snapshot_token") != token \
                or type(page.get(key)) is not list or len(page[key]) > _PAGE_SIZE \
                or type(page.get("has_more")) is not bool:
            raise ProjectionCollectionError
        if expected_count is not None:
            reported_total = page.get("total_records")
            if type(reported_total) is not int or reported_total != expected_count:
                raise ProjectionCollectionError
        has_more = page["has_more"]
        next_cursor = page.get("next_cursor")
        if has_more:
            if not page[key] or type(next_cursor) is not int or next_cursor <= cursor:
                raise ProjectionCollectionError
        elif next_cursor is not None:
            raise ProjectionCollectionError
        yield page[key]
        if not has_more:
            break
        cursor = next_cursor


def _history(gateway: ProjectionGateway, token: str, source_count: int,
             record_count: int) -> tuple[tuple[HistorySource, ...], tuple[HistoryItem, ...]]:
    sources: list[HistorySource] = []
    seen_sources: set[str] = set()
    for page in _pages(gateway, "history", "sources", token, "sources"):
        for source in page:
            if type(source) is not dict or type(source.get("source_id")) is not str \
                    or source["source_id"] in seen_sources \
                    or type(source.get("label")) is not str \
                    or not _count(source.get("item_count")):
                raise ProjectionCollectionError
            seen_sources.add(source["source_id"])
            sources.append(HistorySource(source["source_id"], source["label"], source["item_count"]))
    if len(sources) != source_count or sum(source.item_count for source in sources) != record_count:
        raise ProjectionCollectionError

    items: list[HistoryItem] = []
    seen_items: set[str] = set()
    allowed_fields = {field.name for field in fields(HistoryItem)}
    for source in sources:
        source_items: list[HistoryItem] = []
        for page in _pages(gateway, "history", "records", token, "items",
                           expected_count=source.item_count, source_id=source.source_id):
            for item in page:
                if type(item) is not dict or set(item) != allowed_fields \
                        or item.get("source_id") != source.source_id \
                        or type(item.get("item_id")) is not str or item["item_id"] in seen_items:
                    raise ProjectionCollectionError
                try:
                    record = HistoryItem(**item)
                except (TypeError, ValueError):
                    raise ProjectionCollectionError from None
                seen_items.add(record.item_id)
                source_items.append(record)
        if len(source_items) != source.item_count:
            raise ProjectionCollectionError
        items.extend(source_items)
    if len(items) != record_count:
        raise ProjectionCollectionError
    return tuple(sources), tuple(items)


def _wrong_answers(gateway: ProjectionGateway, token: str, source_count: int,
                   record_count: int) -> tuple[dict, ...]:
    sources: list[dict] = []
    seen_sources: set[str] = set()
    for page in _pages(gateway, "wrong_answers", "sources", token, "sources"):
        for source in page:
            if type(source) is not dict or type(source.get("source_id")) is not str \
                    or source["source_id"] in seen_sources \
                    or not _count(source.get("analysis_count")):
                raise ProjectionCollectionError
            seen_sources.add(source["source_id"])
            sources.append(source)
    if len(sources) != source_count \
            or sum(source["analysis_count"] for source in sources) != record_count:
        raise ProjectionCollectionError

    bundles = []
    seen_analyses: set[str] = set()
    for source in sources:
        source_id = source["source_id"]
        expected = source["analysis_count"]
        analyses = []
        for page in _pages(gateway, "wrong_answers", "records", token, "analyses",
                           expected_count=expected, source_id=source_id):
            for analysis in page:
                if type(analysis) is not dict or analysis.get("source_id") != source_id \
                        or type(analysis.get("analysis_id")) is not str \
                        or analysis["analysis_id"] in seen_analyses:
                    raise ProjectionCollectionError
                seen_analyses.add(analysis["analysis_id"])
                analyses.append(analysis)
        if len(analyses) != expected:
            raise ProjectionCollectionError
        safe_source = {key: value for key, value in source.items() if key != "analysis_count"}
        bundles.append({"source": safe_source, "analyses": analyses,
                        "total": expected, "has_more": False})
    if len(seen_analyses) != record_count:
        raise ProjectionCollectionError
    return tuple(bundles)


def collect_wrong_answer_projection(gateway: ProjectionGateway) -> ProjectionCollection:
    """Explicit Wrong Answer scope; omitted domains are not asserted empty.

    Uses the same permission-controlled snapshot and completeness validation as
    the full collector. Does not catch unavailable-backend errors or access stores.
    """
    token, source_count, record_count = _begin(gateway, "wrong_answers")
    bundles = _wrong_answers(gateway, token, source_count, record_count)
    return ProjectionCollection(
        ProjectionSnapshot(wrong_answer_bundles=bundles),
        "", token, 0, 0, source_count, record_count,
    )


def collect_projection(gateway: ProjectionGateway) -> ProjectionCollection:
    """Collect both independent store watermarks and reconcile all advertised counts."""
    history_token, history_sources_count, history_records_count = _begin(gateway, "history")
    wrong_token, wrong_sources_count, wrong_records_count = _begin(gateway, "wrong_answers")
    history_sources, history_items = _history(
        gateway, history_token, history_sources_count, history_records_count,
    )
    wrong_bundles = _wrong_answers(
        gateway, wrong_token, wrong_sources_count, wrong_records_count,
    )
    legacy_token, legacy_count, legacy_records = _begin(gateway, "legacy_sources")
    legacy = []
    seen = set()
    for page in _pages(gateway, "legacy_sources", "records", legacy_token, "records",
                       expected_count=legacy_records):
        for record in page:
            if type(record) is not dict or type(record.get("source_record_id")) is not str \
                    or record["source_record_id"] in seen:
                raise ProjectionCollectionError
            seen.add(record["source_record_id"])
            legacy.append(record)
    if len(legacy) != legacy_count or legacy_count != legacy_records:
        raise ProjectionCollectionError
    snapshot = ProjectionSnapshot(history_sources, history_items, wrong_bundles, tuple(legacy))
    return ProjectionCollection(
        snapshot, history_token, wrong_token, history_sources_count,
        history_records_count, wrong_sources_count, wrong_records_count, legacy_token,
    )
