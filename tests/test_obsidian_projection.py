from __future__ import annotations

import builtins
from dataclasses import replace
import io
import os
import posixpath
from pathlib import PurePosixPath
import re

import pytest

from cognivault.contracts import HistoryItem, HistorySource
import cognivault.obsidian_projection as projection_module
from cognivault.obsidian_projection import (
    ProjectionError,
    ProjectionSnapshot,
    render_projection,
)


def _history_item(
    item_id: str,
    *,
    role: str,
    created_at: str,
    content: str,
    source_item_id: str = "message-1",
    provenance: dict | None = None,
) -> HistoryItem:
    return HistoryItem(
        item_id="history:" + item_id * 64,
        source_id="history-source-a",
        source_item_id=source_item_id,
        conversation_id="conversation-a",
        role=role,
        created_at=created_at,
        content_sha256="b" * 64,
        content=content,
        imported_at="2026-09-01T00:00:00Z",
        source_system="gemini",
        import_batch_id="batch-a",
        write_provenance=provenance,
    )


def _wrong_answer_bundle(*, version_order: tuple[int, ...] = (2, 1)) -> dict:
    source_id = "wrong-answer://sha256/" + "a" * 64
    provenance = {
        "data_origin": "source",
        "actor_type": "external_client",
        "identity_trust": "reported",
        "reported_agent": "synthetic-agent",
        "reported_client": "synthetic-host",
        "legacy_status": "native",
    }
    analyses = []
    for version in version_order:
        analyses.append({
            "analysis_id": "wrong-analysis://sha256/" + str(version) * 64,
            "source_id": source_id,
            "version": version,
            "error_type": f"error-{version}",
            "knowledge_points": [f"knowledge-{version}"],
            "reasoning": f"reasoning-{version}",
            "correct_solution": f"solution-{version}",
            "review_advice": f"review-{version}",
            "source_refs": ["asset://sha256/" + "d" * 64],
            "study_relations": ["study:math/algebra.md"],
            "generated_by_agent": True,
            "provenance": "agent_supplied",
            "created_at": f"2026-09-0{version}T00:00:00Z",
            "supersedes_analysis_id": (
                "wrong-analysis://sha256/" + "1" * 64 if version > 1 else None
            ),
            "write_provenance": provenance,
        })
    return {
        "source": {
            "source_id": source_id,
            "source_uri": "asset://sha256/" + "d" * 64,
            "page_number": None,
            "question_text": "Synthetic question",
            "student_answer": "Synthetic answer",
            "text_origin": "asset_only",
            "created_at": "2026-08-31T00:00:00Z",
            "write_provenance": provenance,
        },
        "analyses": analyses,
        "total": len(analyses),
        "has_more": False,
    }


def _snapshot() -> ProjectionSnapshot:
    return ProjectionSnapshot(
        history_sources=(HistorySource("history-source-a", "Private synthetic label", 3),),
        history_items=(
            _history_item("c", role="assistant", created_at="2026-09-01T00:00:02Z",
                          source_item_id="message-3", content="Second event"),
            _history_item("a", role="user", created_at="2026-09-01T00:00:01Z",
                          content="First event with `fence` and <img src=x>"),
            _history_item("b", role="tool", created_at="2026-09-01T00:00:01Z",
                          source_item_id="message-2", content="Equal-time event"),
        ),
        wrong_answer_bundles=(_wrong_answer_bundle(),),
    )


def test_render_is_deterministic_and_does_not_write_files(tmp_path, monkeypatch) -> None:
    snapshot = _snapshot()

    def reject_file_open(*args, **kwargs):
        raise AssertionError("renderer attempted filesystem access")

    monkeypatch.setattr(builtins, "open", reject_file_open)
    monkeypatch.setattr(io, "open", reject_file_open)
    monkeypatch.setattr(os, "open", reject_file_open)
    first = render_projection(snapshot)
    second = render_projection(ProjectionSnapshot(
        history_sources=tuple(reversed(snapshot.history_sources)),
        history_items=tuple(reversed(snapshot.history_items)),
        wrong_answer_bundles=tuple(reversed(snapshot.wrong_answer_bundles)),
    ))

    assert first == second
    assert tmp_path.is_dir()
    assert list(tmp_path.iterdir()) == []
    assert list(first) == sorted(first)
    assert all(path.startswith(("Dashboard.md", "History/", "Sources/", "WrongAnswers/",
                               "KnowledgePoints/", "ErrorTypes/", "Study/", "References/"))
               for path in first)


def test_history_projection_preserves_gateway_content_and_marks_scope_and_ties() -> None:
    files = render_projection(_snapshot())
    dashboard = files["Dashboard.md"]
    history_index = files["History/index.md"]
    history_pages = [body for path, body in files.items() if path.startswith("History/items/")]

    assert "provided_input_only" in dashboard
    assert "equal event times" in history_index
    assert "First event with `fence` and <img src=x>" in "\n".join(history_pages)
    assert "Private synthetic label" not in "\n".join(files.values())
    assert all("generated: true" in body for body in history_pages)


def test_wrong_answer_projection_keeps_versions_and_only_true_references() -> None:
    files = render_projection(_snapshot())
    wrong_page = next(body for path, body in files.items()
                      if path.startswith("WrongAnswers/items/"))

    assert wrong_page.index("Version 1") < wrong_page.index("Version 2")
    assert "asset://sha256/" + "d" * 64 in wrong_page
    assert "study:math/algebra.md" in wrong_page
    assert "identity=reported (unverified)" in wrong_page
    assert "Subject: unavailable" in wrong_page
    assert "Analysis ID: ` wrong-analysis://sha256/" in wrong_page
    assert "Generated by agent: true" in wrong_page
    assert "Agent provenance: ` agent_supplied `" in wrong_page
    assert "Supersedes: ` wrong-analysis://sha256/" + "1" * 64 in wrong_page
    assert "synthetic-agent" not in wrong_page
    assert "synthetic-host" not in wrong_page
    assert "![[asset://" not in wrong_page


def test_dashboard_and_analytical_indexes_stay_within_supplied_input() -> None:
    files = render_projection(_snapshot())
    dashboard = files["Dashboard.md"]

    assert "Recent Wrong Answers (up to 5 supplied sources)" in dashboard
    assert "[Knowledge Points](KnowledgePoints/index.md)" in dashboard
    assert "[Error Types](ErrorTypes/index.md)" in dashboard
    assert "Subject index unavailable" in dashboard
    assert "unresolved Legacy summary are not supplied" in dashboard
    assert "Knowledge points reported by supplied Agent analyses" in files["KnowledgePoints/index.md"]
    assert "Error types reported by supplied Agent analyses" in files["ErrorTypes/index.md"]
    assert "study:math/algebra.md" not in files["KnowledgePoints/index.md"]
    assert "Study relation index" not in files["KnowledgePoints/index.md"]
    assert "provided_input_only" in files["ErrorTypes/index.md"]
    for index_path in ("KnowledgePoints/index.md", "ErrorTypes/index.md"):
        for target in re.findall(r"\]\(([^)]+)\)", files[index_path]):
            resolved = posixpath.normpath(str(PurePosixPath(index_path).parent.joinpath(target)))
            assert resolved in files
    assert all(target in files for target in re.findall(r"\]\(([^)]+)\)", dashboard))


def test_projection_paths_are_relative_and_do_not_embed_logical_ids() -> None:
    files = render_projection(_snapshot())

    assert all(not path.startswith(("/", "\\")) and ".." not in path.split("/") for path in files)
    assert all(re.fullmatch(r"[A-Za-z0-9_./-]+", path) for path in files)
    assert all("history-source-a" not in path and "wrong-answer://" not in path for path in files)


def test_projection_escapes_markdown_fences_and_rejects_unsafe_study_relations() -> None:
    snapshot = _snapshot()
    item = replace(snapshot.history_items[0], content="```\n# not a heading\n```")
    files = render_projection(replace(snapshot, history_items=(item,)))
    page = next(body for path, body in files.items() if path.startswith("History/items/"))
    assert "````text\n```\n# not a heading\n```\n````" in page

    bundle = snapshot.wrong_answer_bundles[0]
    analysis = {**bundle["analyses"][0], "study_relations": ["study:../../outside.md"]}
    unsafe = {**bundle, "analyses": [analysis], "total": 1}
    with pytest.raises(ProjectionError, match="Invalid projection snapshot"):
        render_projection(replace(snapshot, wrong_answer_bundles=(unsafe,)))

    invalid_unicode = {**bundle["analyses"][0], "study_relations": ["study:\ud800"]}
    invalid_relation = {**bundle, "analyses": [invalid_unicode], "total": 1}
    with pytest.raises(ProjectionError, match="Invalid projection snapshot"):
        render_projection(replace(snapshot, wrong_answer_bundles=(invalid_relation,)))

    analysis = {**bundle["analyses"][0], "error_type": "Mistake\n# injected heading"}
    unsafe_inline = {**bundle, "analyses": [analysis], "total": 1}
    with pytest.raises(ProjectionError, match="Invalid projection snapshot"):
        render_projection(replace(snapshot, wrong_answer_bundles=(unsafe_inline,)))


def test_projection_rejects_count_mismatch_and_oversized_content() -> None:
    snapshot = _snapshot()
    too_few_reported = replace(snapshot.history_sources[0], item_count=2)
    with pytest.raises(ProjectionError, match="Invalid projection snapshot"):
        render_projection(replace(snapshot, history_sources=(too_few_reported,)))

    oversized = replace(snapshot.history_items[0], content="x" * 100_001)
    with pytest.raises(ProjectionError, match="Invalid projection snapshot"):
        render_projection(replace(snapshot, history_items=(oversized,)))


def test_history_orders_fractional_timestamps_by_instant() -> None:
    snapshot = _snapshot()
    earlier = replace(snapshot.history_items[0], created_at="2026-09-01T00:00:00.1Z")
    later = replace(snapshot.history_items[1], created_at="2026-09-01T00:00:00.11Z")
    files = render_projection(replace(snapshot, history_items=(later, earlier), wrong_answer_bundles=()))
    index = files["History/index.md"]
    assert index.index("00:00:00.1Z") < index.index("00:00:00.11Z")


def test_dashboard_has_cross_conversation_history_timeline() -> None:
    files = render_projection(_snapshot())
    timeline = files["History/timeline.md"]

    assert "provided_input_only" in timeline
    assert "does not establish original event order" in timeline
    assert timeline.index("user — 2026-09-01T00:00:01Z") \
        < timeline.index("tool — 2026-09-01T00:00:01Z") \
        < timeline.index("assistant — 2026-09-01T00:00:02Z")
    assert "[History timeline](History/timeline.md)" in files["Dashboard.md"]
    assert all(
        posixpath.normpath(str(PurePosixPath("History/timeline.md").parent.joinpath(target))) in files
        for target in re.findall(r"\]\(([^)]+)\)", timeline)
    )


def test_input_byte_limit_applies_across_wrong_answer_bundles(monkeypatch) -> None:
    monkeypatch.setattr(projection_module, "_MAX_INPUT_BYTES", 10_000)
    first = _wrong_answer_bundle()
    second = _wrong_answer_bundle()
    for bundle, source_char, analysis_char in ((first, "a", "1"), (second, "b", "2")):
        bundle["source"]["source_id"] = "wrong-answer://sha256/" + source_char * 64
        bundle["source"]["question_text"] = "q" * 3_000
        bundle["source"]["student_answer"] = "a" * 3_000
        for analysis in bundle["analyses"]:
            analysis["source_id"] = bundle["source"]["source_id"]
            analysis["analysis_id"] = (
                "wrong-analysis://sha256/" + analysis_char * 62 + f"{analysis['version']:02d}"
            )

    with pytest.raises(ProjectionError, match="Invalid projection snapshot"):
        render_projection(ProjectionSnapshot(wrong_answer_bundles=(first, second)))


def test_wrong_answer_analysis_count_is_bounded_across_bundles(monkeypatch) -> None:
    monkeypatch.setattr(projection_module, "_MAX_RECORDS", 3)
    first = _wrong_answer_bundle()
    second = _wrong_answer_bundle()
    second["source"]["source_id"] = "wrong-answer://sha256/" + "b" * 64
    for analysis in second["analyses"]:
        analysis["source_id"] = second["source"]["source_id"]
        analysis["analysis_id"] = "wrong-analysis://sha256/" + "e" * 62 + f"{analysis['version']:02d}"

    with pytest.raises(ProjectionError, match="Invalid projection snapshot"):
        render_projection(ProjectionSnapshot(wrong_answer_bundles=(first, second)))


@pytest.mark.parametrize(
    ("source_uri", "page_number"),
    [
        ("asset://sha256/" + "d" * 64, 1),
        ("document://sha256/" + "d" * 64, 65),
    ],
)
def test_wrong_answer_page_number_matches_gateway_contract(source_uri: str, page_number: int) -> None:
    bundle = _wrong_answer_bundle()
    bundle["source"]["source_uri"] = source_uri
    bundle["source"]["page_number"] = page_number
    with pytest.raises(ProjectionError, match="Invalid projection snapshot"):
        render_projection(ProjectionSnapshot(wrong_answer_bundles=(bundle,)))


def test_wrong_answer_source_requires_explicit_page_number_field() -> None:
    bundle = _wrong_answer_bundle()
    del bundle["source"]["page_number"]
    with pytest.raises(ProjectionError, match="Invalid projection snapshot"):
        render_projection(ProjectionSnapshot(wrong_answer_bundles=(bundle,)))


def test_file_count_bound_accounts_for_all_four_fixed_outputs(monkeypatch) -> None:
    monkeypatch.setattr(projection_module, "_MAX_RECORDS", 2)
    snapshot = _snapshot()
    files = render_projection(ProjectionSnapshot(
        history_sources=snapshot.history_sources,
        history_items=(snapshot.history_items[0],),
    ))
    assert len(files) == 15


def test_writer_accepts_renderer_file_count_limit():
    import json
    from cognivault import obsidian_writer
    names = [f'items/{number:05d}.md' for number in range(projection_module._MAX_RECORDS + 13)]
    manifest = json.dumps({'schema_version': 1, 'files': names}).encode()
    assert obsidian_writer._manifest_files(manifest) == tuple(names)
    names.append('items/99999.md')
    with pytest.raises(obsidian_writer.ProjectionWriteError):
        obsidian_writer._manifest_files(json.dumps({'schema_version': 1, 'files': names}).encode())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda snapshot: replace(snapshot, history_items=(
            snapshot.history_items[0], snapshot.history_items[0],
        )),
        lambda snapshot: replace(snapshot, wrong_answer_bundles=(
            {**snapshot.wrong_answer_bundles[0], "has_more": True},
        )),
        lambda snapshot: replace(snapshot, history_items=(
            replace(snapshot.history_items[0], role="unknown"),
        )),
    ],
)
def test_invalid_or_incomplete_gateway_snapshot_fails_closed(mutate) -> None:
    with pytest.raises(ProjectionError, match="Invalid projection snapshot"):
        render_projection(mutate(_snapshot()))


def test_wrong_answer_supersession_must_match_previous_analysis() -> None:
    bundle = _wrong_answer_bundle()
    latest = next(analysis for analysis in bundle["analyses"] if analysis["version"] == 2)
    latest["supersedes_analysis_id"] = "wrong-analysis://sha256/" + "f" * 64
    with pytest.raises(ProjectionError, match="Invalid projection snapshot"):
        render_projection(ProjectionSnapshot(wrong_answer_bundles=(bundle,)))


def test_term_index_stops_rendering_when_remaining_output_budget_is_exceeded(monkeypatch) -> None:
    bundle = _wrong_answer_bundle(version_order=(1,))
    analysis = bundle["analyses"][0]
    analysis["knowledge_points"] = ["`" * 200 + chr(65 + index) for index in range(20)]
    monkeypatch.setattr(projection_module, "_MAX_RENDERED_BYTES", 5_000)

    original_code_span = projection_module._code_span
    long_terms_rendered = 0

    def tracked_code_span(value: str) -> str:
        nonlocal long_terms_rendered
        if len(value) == 201:
            long_terms_rendered += 1
        return original_code_span(value)

    monkeypatch.setattr(projection_module, "_code_span", tracked_code_span)
    with pytest.raises(ProjectionError, match="Invalid projection snapshot"):
        render_projection(ProjectionSnapshot(wrong_answer_bundles=(bundle,)))
    assert long_terms_rendered < 20
