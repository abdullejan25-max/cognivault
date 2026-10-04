"""Metadata-only source documents; never infer message or learning semantics."""

import re

from .obsidian_projection import ProjectionError
from .adapters.history import _normalize_timestamp
from .contracts import GatewayError


def _source_timestamp(value):
    try:
        _normalize_timestamp(value)
        return True
    except GatewayError:
        return False

_GROUPS = {
    "legacy_markdown": ("LegacyChat", "Legacy Chat Sources"),
    "codex_jsonl": ("Codex", "Codex Sources"),
    "legacy_wrong_answer_document": ("LegacyWrongAnswers", "Legacy Wrong Answer Sources"),
    "legacy_derived_fact": ("DerivedFacts", "Derived Facts"),
}


def render_sources(records: tuple[dict, ...]) -> dict[str, str]:
    if type(records) is not tuple or len(records) > 10000:
        raise ProjectionError
    groups = {key: [] for key in _GROUPS}
    seen = set()
    for record in records:
        if type(record) is not dict or set(record) != {"source_record_id", "source_id", "source_type",
                "source_order", "source_created_at", "imported_at", "source_refs", "byte_count", "write_provenance"}:
            raise ProjectionError
        logical = record["source_record_id"]
        if type(logical) is not str or not re.fullmatch(r"legacy-source:[0-9a-f]{64}", logical) or logical in seen \
                or type(record["source_id"]) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", record["source_id"]) \
                or type(record["source_type"]) is not str or record["source_type"] not in _GROUPS \
                or not _source_timestamp(record["imported_at"]) \
                or (record["source_created_at"] is not None and not _source_timestamp(record["source_created_at"])) \
                or type(record["source_order"]) is not int or record["source_order"] < 0 \
                or type(record["byte_count"]) is not int or record["byte_count"] < 0 \
                or type(record["source_refs"]) is not list or len(record["source_refs"]) > 70 \
                or any(type(ref) is not str or not re.fullmatch(r"asset://sha256/[0-9a-f]{64}", ref) for ref in record["source_refs"]) \
                or len(set(record["source_refs"])) != len(record["source_refs"]) \
                or type(record["write_provenance"]) is not dict \
                or record["write_provenance"] != {"data_origin": "legacy_import", "actor_type": "importer",
                    "identity_trust": record["write_provenance"].get("identity_trust"), "legacy_status": "imported"} \
                or record["write_provenance"]["identity_trust"] not in {"reported", "unavailable"}:
            raise ProjectionError
        seen.add(logical)
        groups[record["source_type"]].append(record)
    files = {}
    index = ["# Sources", "", "> Generated metadata projection. Source documents are not messages.",
             "> No author, role, conversation boundary or learning relation is inferred.", ""]
    for kind, (folder, title) in _GROUPS.items():
        rows = sorted(groups[kind], key=lambda row: (row["source_id"], row["source_order"], row["source_record_id"]))
        index.append(f"- [{title}]({folder}/index.md): {len(rows)}")
        lines = [f"# {title}", "", f"Source documents: {len(rows)}", ""]
        for row in rows:
            name = row["source_record_id"].split(':')[1] + ".md"
            lines.append(f"- [{row['source_id']} / {row['source_order']}]({name})")
            files[f"Sources/{folder}/{name}"] = (
                '---\ngenerated: true\ntype: source_document\n---\n\n'
                f"# {title} — {row['source_order']}\n\n"
                "> Source-level metadata only. Original content can be read through `fetch_legacy_source`; it is not copied here.\n\n"
                + "\n".join(f"- {key}: `{value if value is not None else 'unknown'}`" for key, value in row.items()
                            if key not in {"source_refs", "write_provenance"})
                + "\n- author: unknown\n- role: unknown\n- conversation boundary: unknown\n\n"
                + "## Provenance\n\n" + "\n".join(f"- {key}: `{value}`" for key, value in sorted(row['write_provenance'].items()))
                + "\n\n## Logical references\n\n" + ("\n".join(f"- `{ref}`" for ref in sorted(row['source_refs'])) or "- None supplied") + "\n")
        files[f"Sources/{folder}/index.md"] = "\n".join(lines) + "\n"
    files['Sources/index.md'] = "\n".join(index) + "\n"
    return files
