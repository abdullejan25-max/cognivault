"""Registration compatibility with additive columns in invented stores only."""
from contextlib import closing
import sqlite3

import pytest

from test_wrong_answers import gateway_with_sources
from cognivault.contracts import GatewayError


@pytest.mark.parametrize('extra_definition,expected_extra', [
    ('invented_extension TEXT', None),
    ("invented_extension TEXT NOT NULL DEFAULT 'invented-default'", 'invented-default'),
])
def test_source_registration_preserves_additive_columns_old_rows_and_provenance(
        tmp_path, extra_definition, expected_extra):
    g, _, asset_uri, _ = gateway_with_sources(tmp_path)
    old = g.register_wrong_answer_source(asset_uri, 'Invented old question', 'Invented old answer')['source']
    database = g.document_store.database_path
    with closing(sqlite3.connect(database)) as con:
        con.execute('ALTER TABLE wrong_sources ADD COLUMN ' + extra_definition)
        old_row = con.execute('SELECT * FROM wrong_sources').fetchone()
        old_provenance = con.execute('SELECT * FROM write_provenance WHERE record_type=?',
                                     ('wrong_answer_source',)).fetchall()
        schema = con.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        migrations = con.execute('SELECT * FROM schema_migrations ORDER BY scope,version').fetchall()
        con.commit()

    new = g.register_wrong_answer_source(asset_uri, 'Invented new question', 'Invented new answer')['source']
    assert new['source_id'] != old['source_id']
    assert new['question_text'] == 'Invented new question'
    assert new['student_answer'] == 'Invented new answer'
    assert new['text_origin'] == 'asset_only'
    assert new['write_provenance']['record_type'] == 'wrong_answer_source'
    assert new['write_provenance']['source_refs'] == [asset_uri]
    assert g.register_wrong_answer_source(asset_uri, 'Invented new question', 'Invented new answer')['source'] == new
    assert g.register_wrong_answer_source(asset_uri, 'Invented old question', 'Invented old answer')['source'] == old
    with closing(sqlite3.connect(database)) as con:
        assert con.execute('SELECT * FROM wrong_sources WHERE source_id=?', (old['source_id'],)).fetchone() == old_row
        assert con.execute('SELECT invented_extension FROM wrong_sources WHERE source_id=?',
                           (new['source_id'],)).fetchone() == (expected_extra,)
        assert con.execute('SELECT * FROM write_provenance WHERE record_type=? AND record_id=?',
                           ('wrong_answer_source', old['source_id'])).fetchall() == old_provenance
        assert con.execute('SELECT COUNT(*) FROM write_provenance WHERE record_type=? AND record_id=?',
                           ('wrong_answer_source', new['source_id'])).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM operation_audit WHERE operation='register_wrong_answer_source'").fetchone()[0] == 2
        assert con.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall() == schema
        assert con.execute('SELECT * FROM schema_migrations ORDER BY scope,version').fetchall() == migrations


def test_source_registration_does_not_invent_required_extension_values(tmp_path):
    g, _, asset_uri, _ = gateway_with_sources(tmp_path)
    # Initialize the normal schema before adding a required column to an empty table.
    with closing(g.document_store._connect(write=True)) as con:
        g._wrong_answers()._schema(con)
        con.execute('ALTER TABLE wrong_sources ADD COLUMN invented_required TEXT NOT NULL')
        con.commit()
        before_provenance = con.execute('SELECT COUNT(*) FROM write_provenance').fetchone()[0]
    with pytest.raises(GatewayError) as raised:
        g.register_wrong_answer_source(asset_uri, 'Invented question', 'Invented answer')
    assert raised.value.code == 'STORAGE_UNAVAILABLE'
    with closing(sqlite3.connect(g.document_store.database_path)) as con:
        assert con.execute('SELECT COUNT(*) FROM wrong_sources').fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM write_provenance').fetchone()[0] == before_provenance
        assert con.execute("SELECT COUNT(*) FROM operation_audit WHERE operation='register_wrong_answer_source'").fetchone()[0] == 0
