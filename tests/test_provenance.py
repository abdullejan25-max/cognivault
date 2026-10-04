"""Common write provenance ledger behavior on disposable SQLite databases."""

import sqlite3
from datetime import datetime

import pytest

from cognivault.contracts import GatewayError
from cognivault.provenance import (
    ReportedIdentity, ensure_provenance_schema, insert_provenance,
)


def test_ledger_records_gateway_time_sources_and_reported_identity() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    ensure_provenance_schema(connection)

    provenance_id = insert_provenance(
        connection, record_type="wrong_answer_analysis", record_id="analysis:1", version=1,
        data_origin="agent_generated", actor_type="external_client",
        source_refs=["asset://sha256/" + "a" * 64],
        identity=ReportedIdentity(reported_agent="ChatGPT", reported_client="mcp-client", run_id="run-1"),
    )

    row = connection.execute("SELECT * FROM write_provenance WHERE provenance_id = ?",
                             (provenance_id,)).fetchone()
    assert row["recorded_at"].endswith("Z")
    datetime.fromisoformat(row["recorded_at"].replace("Z", "+00:00"))
    assert row["data_origin"] == "agent_generated"
    assert row["actor_type"] == "external_client"
    assert row["identity_trust"] == "reported"
    assert row["reported_agent"] == "ChatGPT"
    assert row["source_refs"] == '["asset://sha256/' + "a" * 64 + '"]'


@pytest.mark.parametrize("value", ["C:/private/file.png", "..\\secret", "bad\x00value", "  "])
def test_reported_identity_rejects_paths_and_invalid_values(value: str) -> None:
    with pytest.raises(GatewayError):
        ReportedIdentity.from_value({"reported_agent": value})


def test_direct_identity_values_are_validated_too() -> None:
    with pytest.raises(GatewayError):
        ReportedIdentity(reported_client="C:/private/client")


def test_schema_upgrade_is_idempotent_and_preserves_preexisting_records() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE assets(uri TEXT PRIMARY KEY, sha256 TEXT NOT NULL, media_type TEXT NOT NULL, size INTEGER NOT NULL)")
    asset_uri = "asset://sha256/" + "b" * 64
    connection.execute("INSERT INTO assets VALUES (?, ?, ?, ?)", (asset_uri, "b" * 64, "image/png", 4))

    ensure_provenance_schema(connection)
    ensure_provenance_schema(connection)

    rows = connection.execute("SELECT * FROM write_provenance WHERE record_id = ?", (asset_uri,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["data_origin"] == "source"
    assert rows[0]["legacy_status"] == "pre_provenance"
    assert rows[0]["actor_type"] == "unknown"
    assert rows[0]["identity_trust"] == "unavailable"
    assert rows[0]["reported_agent"] is None


def test_provenance_rows_are_append_only() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    ensure_provenance_schema(connection)
    provenance_id = insert_provenance(
        connection, record_type="asset", record_id="asset://sha256/" + "c" * 64,
        version=1, data_origin="source", actor_type="external_client",
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE write_provenance SET data_origin='agent_generated' WHERE provenance_id=?",
                           (provenance_id,))
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM write_provenance WHERE provenance_id=?", (provenance_id,))
