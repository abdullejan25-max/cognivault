"""Private restored-Gateway Memory checks; return only bounded aggregate proof."""
from contextlib import closing
import hashlib

from .io import encoded, fail


LIMIT = 20


def readback(isolated, manifest):
    item = next((entry for entry in manifest['files'] if entry['relative'] == 'memory.sqlite3'), None)
    proof = dict(state='not_configured', integrity_verified=False, facts=0, versions=0,
                 candidates=0, approved_candidates=0, pending_candidates=0, rejected_candidates=0,
                 versions_read_back=0, candidates_read_back=0, requests_read_back=0,
                 source_refs_preserved=False, provenance_preserved=False,
                 scope='bounded_gateway_payloads; whole_tables_verified_by_snapshot_sqlite_proof; '
                       'stored_reference_and_provenance_preservation; source availability, semantic truth and currency not established')
    if item is None:
        if isolated.memory_store is not None:
            fail()
        return proof
    integrity = isolated.verify_memory_integrity()
    if not integrity.get('verified') or not integrity.get('complete'):
        fail()
    tables = item['sqlite_proof']['tables']
    count = lambda name: tables.get(name, {}).get('rows', 0)
    store = isolated.memory_store
    if store is None:
        fail()
    expected_records, expected_pages, expected_candidates, expected_requests = [], [], [], []
    with closing(store._connect()) as con:
        con.execute('BEGIN')
        names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name in ('memory_facts', 'memory_versions', 'memory_assessments', 'memory_requests',
                     'write_provenance', 'memory_candidates', 'memory_candidate_reviews'):
            actual_count = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] if name in names else 0
            if actual_count != count(name):
                fail()
        if (integrity['facts'], integrity['versions']) != (count('memory_facts'), count('memory_versions')):
            fail()
        mids = [row[0] for row in con.execute('SELECT memory_id FROM memory_facts ORDER BY memory_id LIMIT ?', (LIMIT,))]
        for mid in mids:
            expected_records.append(store._record(con, mid))
            rows = con.execute('SELECT version FROM memory_versions WHERE memory_id=? ORDER BY version DESC LIMIT ?', (mid, LIMIT)).fetchall()
            total = con.execute('SELECT count(*) FROM memory_versions WHERE memory_id=?', (mid,)).fetchone()[0]
            expected_pages.append((mid, total, [store._record(con, mid, row[0]) for row in rows]))
        for row in con.execute('SELECT idempotency_key,request_sha256,memory_id,version FROM memory_requests ORDER BY idempotency_key LIMIT ?', (LIMIT,)):
            expected_requests.append((dict(row), store._record(con, row['memory_id'], row['version'])))
        if 'memory_candidates' in names:
            from ..memory_candidates import read
            for row in con.execute('SELECT candidate_id FROM memory_candidates ORDER BY proposed_at DESC,candidate_id LIMIT ?', (LIMIT,)):
                expected_candidates.append(read(con, row[0]))
            reviews = dict(con.execute('SELECT decision,count(*) FROM memory_candidate_reviews GROUP BY decision'))
            proof.update(approved_candidates=reviews.get('approve', 0), rejected_candidates=reviews.get('reject', 0),
                         pending_candidates=count('memory_candidates') - count('memory_candidate_reviews'))
    expected, actual = [], []

    def compare(kind, wanted, returned):
        # Gateway decoration adds live cross-domain availability metadata. The
        # persisted immutable fields, including refs and provenance, must match.
        projected = {key: returned[key] for key in wanted}
        expected.append([kind, wanted])
        actual.append([kind, projected])
        if projected != wanted:
            fail()

    for record in expected_records:
        compare('latest', record, isolated.fetch_memory(record['memory_id'])['memory'])
    for mid, total, records in expected_pages:
        page = isolated.memory_versions(mid, limit=LIMIT)
        if page['total'] != total or len(page['versions']) != len(records):
            fail()
        for wanted, returned in zip(records, page['versions']):
            compare('version', wanted, returned)
        proof['versions_read_back'] += len(records)
    for request, record in expected_requests:
        returned = isolated.fetch_memory_request(request['idempotency_key'])
        compare('request', request, returned['request'])
        compare('request_memory', record, returned['memory'])
    if 'memory_candidates' in tables:
        listing = isolated.list_memory_candidates(limit=LIMIT)
        if listing['total'] != count('memory_candidates') or len(listing['candidates']) != len(expected_candidates):
            fail()
        for wanted, returned in zip(expected_candidates, listing['candidates']):
            compare('candidate_list', wanted, returned)
            compare('candidate_fetch', wanted, isolated.fetch_memory_candidate(wanted['candidate_id'])['candidate'])
    expected_hash = hashlib.sha256(encoded(expected)).hexdigest()
    actual_hash = hashlib.sha256(encoded(actual)).hexdigest()
    if expected_hash != actual_hash:
        fail()
    proof.update(state='verified' if count('memory_facts') or count('memory_candidates') else 'empty',
                 integrity_verified=True, facts=count('memory_facts'), versions=count('memory_versions'),
                 candidates=count('memory_candidates'), candidates_read_back=len(expected_candidates),
                 requests_read_back=len(expected_requests), source_refs_preserved=bool(expected_records),
                 provenance_preserved=bool(expected_records), expected_readback_sha256=expected_hash,
                 actual_readback_sha256=actual_hash)
    return proof
