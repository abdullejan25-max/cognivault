"""Explicit offline legacy-input inventory. No Gateway store/client access.

Run with a private descriptor manifest and ledger outside every repository.
The stdout contract contains only allowlisted categories, counts and states.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
import sys

from .history_inventory import (
    ACQUISITION_METHODS, EVIDENCE_KINDS, SOURCE_TYPES, HistoryInventoryError,
    inspect_source, inventory_files,
)
from .history_ledger import HistoryLedgerError, HistoryMigrationLedger
from .inventory import InventoryError, _fingerprint_file
from .manifest import validate_private_journal_path

_SCOPE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_SUFFIX = re.compile(r"\.[a-z0-9]{1,8}\Z")
_UNAVAILABLE = {"waiting_for_export", "acquisition_pending", "gateway_unavailable", "not_found", "retained_legacy", "review_required"}


def _load_manifest(path: Path) -> dict:
    path = validate_private_journal_path(path)
    if any((parent / ".git").exists() for parent in path.parents):
        raise HistoryInventoryError("invalid_manifest")
    _digest, _size, raw = _fingerprint_file(path, source_root=path.parent,
                                           capture_limit=1024*1024, max_bytes=1024*1024)
    data = json.loads(raw)
    if type(data) is not dict or set(data) != {"schema_version", "sources", "protected_paths"} \
            or type(data["schema_version"]) is not int or data["schema_version"] != 1 \
            or type(data["sources"]) is not list or not 1 <= len(data["sources"]) <= 128 \
            or type(data["protected_paths"]) is not list or len(data["protected_paths"]) > 64 \
            or any(type(p) is not str or not Path(p).is_absolute() for p in data["protected_paths"]):
        raise HistoryInventoryError("invalid_manifest")
    scopes = set()
    for source in data["sources"]:
        if type(source) is not dict or type(source.get("scope")) is not str \
                or not _SCOPE.fullmatch(source["scope"]) or source["scope"] in scopes \
                or source.get("source_type") not in SOURCE_TYPES:
            raise HistoryInventoryError("invalid_manifest")
        scopes.add(source["scope"])
        if "root" not in source:
            if set(source) != {"scope", "source_type", "state"} or source["state"] not in _UNAVAILABLE:
                raise HistoryInventoryError("invalid_manifest")
        elif set(source) - {"evidence_kind"} != {"scope", "source_type", "root", "suffixes", "acquisition_method"} \
                or source.get("evidence_kind", "unknown") not in EVIDENCE_KINDS \
                or type(source["root"]) is not str or not Path(source["root"]).is_absolute() \
                or source["acquisition_method"] not in ACQUISITION_METHODS \
                or type(source["suffixes"]) is not list or not 1 <= len(source["suffixes"]) <= 16 \
                or any(type(s) is not str or not _SUFFIX.fullmatch(s) for s in source["suffixes"]):
            raise HistoryInventoryError("invalid_manifest")
    return data


def acquire(manifest_path: Path, ledger_path: Path) -> dict:
    data = _load_manifest(manifest_path)
    protected = tuple(Path(p) for p in data["protected_paths"])
    input_roots = tuple(Path(s["root"]) for s in data["sources"] if "root" in s)
    ledger = HistoryMigrationLedger(ledger_path, protected_paths=(*protected, *input_roots, manifest_path))
    errors = Counter()
    for source in data["sources"]:
        scope, category = source["scope"], source["source_type"]
        if "root" not in source:
            ledger.record_catalog(scope, source_type=category, state=source["state"])
            continue
        root = Path(source["root"])
        family_failed = False
        try:
            files = inventory_files(root, suffixes=tuple(source["suffixes"]), protected_paths=protected)
        except HistoryInventoryError as error:
            errors[str(error)] += 1
            ledger.record_catalog(scope, source_type=category, state="review_required", private_location=str(root))
            ledger.record_failure(scope, str(root), str(error))
            continue
        for file in files:
            try:
                record = inspect_source(file, source_root=root, source_type=category,
                                        acquisition_method=source["acquisition_method"], protected_paths=protected,
                                        evidence_kind=source.get("evidence_kind", "unknown"))
                state = "malformed" if record.malformed_records else \
                        "unsupported" if record.parser_availability == "unsupported" else "parsed"
                ledger.discover(record, initial_state=state)
                ledger.resolve_failure(scope, str(file))
            except HistoryInventoryError as error:
                errors[str(error)] += 1
                family_failed = True
                ledger.record_failure(scope, str(file), str(error))
        ledger.record_catalog(scope, source_type=category,
                              state="review_required" if family_failed else "available", private_location=str(root))
        if not family_failed:
            ledger.resolve_failure(scope, str(root))
    return {**ledger.public_summary(), "run_errors": dict(sorted(errors.items())), "run_state": "INVENTORY_ONLY"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inventory explicit legacy inputs into a Git-external private ledger")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = acquire(args.manifest, args.ledger)
        print(json.dumps(result, sort_keys=True))
        return 1 if result["run_errors"] else 0
    except (ValueError, TypeError, OSError, sqlite3.Error, InventoryError, HistoryLedgerError):
        print("history_inventory_unavailable", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
