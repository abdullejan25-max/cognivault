"""Read-only debug guard uses exclusively invented temporary document stores."""
from contextlib import closing
import json
import sqlite3

import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from cognivault.adapters.documents import SQLiteDocumentStore
from cognivault.adapters.wrong_answers import SQLiteWrongAnswerStore
from cognivault.config import AppConfig
from cognivault.contracts import GatewayError
from cognivault.gateway import Gateway
from cognivault.transports.mcp_stdio import create_mcp_server


def setup(tmp_path, *, schema=True):
    objects = tmp_path / 'objects'
    objects.mkdir()
    database = tmp_path / 'invented.sqlite3'
    store = SQLiteDocumentStore(objects, database)
    with closing(sqlite3.connect(database)) as con:
        if schema:
            SQLiteDocumentStore._schema(con)
            SQLiteWrongAnswerStore._schema(con)
        con.commit()
    return Gateway(AppConfig('0.6.0'), None, document_store=store,
                   capabilities=frozenset({'read'})), database


def test_diagnostics_normal_contract_is_content_free_and_does_not_write(tmp_path, monkeypatch):
    g, database = setup(tmp_path)
    secret = 'INVENTED_PRIVATE_BODY_AND_COLUMN_NAME'
    with closing(sqlite3.connect(database)) as con:
        con.execute('INSERT INTO wrong_sources VALUES(?,?,?,?,?,?,?)',
                    ('invented-source', 'invented-uri', None, secret, secret, 'asset_only', 'invented-time'))
        con.execute('INSERT INTO wrong_analyses VALUES(?,?,?,?,?,?,?,?,?)',
                    ('invented-analysis', 'invented-source', 0, secret, secret, secret, 1, secret, 'invented-time'))
        con.execute('DELETE FROM schema_migrations WHERE scope=?', ('wrong_answers',))
        con.commit()
    before = database.read_bytes()
    connections = []
    original = g.document_store._connect
    def guarded_connect(*, write=False):
        assert write is False
        con = original(write=write)
        # The real SQL may only read/inspect metadata; no schema or audit writes.
        con.set_authorizer(lambda action, table, column, *args: sqlite3.SQLITE_OK if action in {
            sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
            sqlite3.SQLITE_PRAGMA, sqlite3.SQLITE_TRANSACTION
        } and not (action == sqlite3.SQLITE_READ and column in {
            'question_text', 'student_answer', 'body', 'source_refs', 'resource_id', 'record_id'
        }) else sqlite3.SQLITE_DENY)
        connections.append(con)
        return con
    monkeypatch.setattr(g.document_store, '_connect', guarded_connect)
    result = g.wrong_answer_storage_diagnostics()
    assert len(connections) == 1
    assert set(result['tables']) == {'wrong_sources', 'wrong_analyses', 'wrong_analysis_requests',
                                     'operation_audit', 'write_provenance', 'schema_migrations'}
    assert all(t['expected_column_contract'] for t in result['tables'].values())
    assert result['tables']['wrong_sources']['column_count'] == 7
    assert result['wrong_analyses_invalid_nonpositive_version_count'] == 1
    assert result['migration_scopes'] == {'documents': True, 'wrong_answers': False}
    assert result['backfill_ranges']['wrong_answer_analysis']['would_scan_if_scope_pending'] is True
    output = json.dumps(result)
    assert secret not in output and str(database) not in output
    assert 'CREATE TABLE' not in output and 'invented-source' not in output
    assert database.read_bytes() == before


def test_diagnostics_reports_extra_required_columns_and_missing_contract(tmp_path):
    g, database = setup(tmp_path)
    with closing(sqlite3.connect(database)) as con:
        con.execute('ALTER TABLE wrong_sources ADD COLUMN INVENTED_SECRET_COLUMN TEXT NOT NULL')
        con.execute('ALTER TABLE wrong_analysis_requests DROP COLUMN payload_sha256')
        con.commit()
    result = g.wrong_answer_storage_diagnostics()
    assert result['tables']['wrong_sources']['extra_column_count'] == 1
    assert result['tables']['wrong_sources']['required_extra_column_count'] == 1
    assert result['tables']['wrong_analysis_requests']['expected_column_contract'] is False
    assert 'INVENTED_SECRET_COLUMN' not in json.dumps(result)


def test_diagnostics_missing_tables_remain_missing(tmp_path):
    g, database = setup(tmp_path, schema=False)
    before = database.read_bytes()
    result = g.wrong_answer_storage_diagnostics()
    assert all(t['exists'] is False and t['column_count'] == 0
               and t['expected_column_contract'] is False for t in result['tables'].values())
    assert result['wrong_analyses_invalid_nonpositive_version_count'] is None
    assert result['migration_scopes'] == {'documents': False, 'wrong_answers': False}
    assert database.read_bytes() == before


def test_nullable_or_defaulted_extras_are_counted_without_exposing_defaults(tmp_path):
    g, database = setup(tmp_path)
    with closing(sqlite3.connect(database)) as con:
        con.execute('ALTER TABLE wrong_sources ADD COLUMN invented_nullable TEXT')
        con.execute("ALTER TABLE wrong_sources ADD COLUMN invented_default TEXT NOT NULL DEFAULT 'INVENTED_SECRET_DEFAULT'")
        con.commit()
    result = g.wrong_answer_storage_diagnostics()
    table = result['tables']['wrong_sources']
    assert table['expected_column_contract'] is True
    assert table['extra_column_count'] == 2 and table['required_extra_column_count'] == 0
    assert 'INVENTED_SECRET_DEFAULT' not in json.dumps(result)


def test_malformed_migration_contract_keeps_dependent_flags_unknown(tmp_path):
    g, database = setup(tmp_path)
    with closing(sqlite3.connect(database)) as con:
        con.execute('DROP TABLE schema_migrations')
        con.execute('CREATE TABLE schema_migrations(scope TEXT, version TEXT, applied_at TEXT)')
        con.commit()
    result = g.wrong_answer_storage_diagnostics()
    assert result['tables']['schema_migrations']['expected_column_contract'] is False
    assert result['migration_scopes'] == {'documents': None, 'wrong_answers': None}
    assert result['backfill_ranges']['wrong_answer_source']['would_scan_if_scope_pending'] is None


@pytest.mark.parametrize('configured,capabilities,visible,code', [
    (True, {'read'}, True, None), (True, {'admin'}, True, None),
    (True, {'write'}, False, 'PERMISSION_DENIED'),
    (False, {'read'}, False, 'STORAGE_UNAVAILABLE'),
])
def test_native_diagnostics_discovery_call_permissions_and_no_arguments(
        tmp_path, configured, capabilities, visible, code):
    g, _ = setup(tmp_path)
    if not configured:
        g.document_store = None
    g.capabilities = frozenset(capabilities)
    async def run():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            name = 'wrong_answer_storage_diagnostics'
            assert (name in tools) is visible
            result = (await client.call_tool(name, {})).structuredContent
            if code:
                assert result['ok'] is False and result['error']['code'] == code
            else:
                assert tools[name].annotations.readOnlyHint is True
                assert tools[name].inputSchema['additionalProperties'] is False
                assert result['ok'] is True and result['tables']['wrong_sources']['column_count'] == 7
                bad = (await client.call_tool(name, {'unexpected': 'INVENTED_PRIVATE'})).structuredContent
                assert bad['ok'] is False and bad['error']['code'] == 'INVALID_ARGUMENT'
                assert 'INVENTED_PRIVATE' not in json.dumps(bad)
    anyio.run(run)


def test_diagnostics_sqlite_errors_are_sanitized(tmp_path, monkeypatch):
    g, _ = setup(tmp_path)
    def broken_connect():
        raise sqlite3.OperationalError('INVENTED_SECRET_PATH_AND_ERROR')
    monkeypatch.setattr(g.document_store, '_connect', broken_connect)
    with pytest.raises(GatewayError) as raised:
        g.wrong_answer_storage_diagnostics()
    assert raised.value.code == 'STORAGE_UNAVAILABLE'
    assert 'INVENTED_SECRET' not in str(raised.value)
