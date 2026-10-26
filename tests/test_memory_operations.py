"""Only synthetic temporary databases: first deployment and read-only checks."""
import json
import os
import sqlite3

import pytest

from cognivault.adapters.memory import SQLiteMemoryStore
from cognivault.contracts import GatewayError

REF = "history:" + "a" * 64


def _allow_reservation_replacement(monkeypatch, database):
    """Exercise path identity races even when Windows CRT denies open-file unlink.

    Production keeps its stricter os.open sharing. This test-only equivalent
    exclusive creator grants FILE_SHARE_DELETE so an actual replacement can
    occur; no identity/foreign-byte preservation assertion is relaxed.
    """
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    original = os.open
    reservations = {str(database) + suffix for suffix in ("", "-wal", "-shm", "-journal")}

    def shared_delete(name, flags, *args, **kwargs):
        if str(name) not in reservations or not flags & os.O_EXCL:
            return original(name, flags, *args, **kwargs)
        handle = create(str(name), 0x80000000 | 0x40000000, 0x1 | 0x2 | 0x4,
                        None, 1, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            if error in (80, 183):
                raise FileExistsError()
            raise ctypes.WinError(error)
        return msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)

    monkeypatch.setattr(os, "open", shared_delete)


def test_initialize_absent_only_and_empty_candidate_schema(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    assert store.initialize() == {"initialized": True, "deployment_kind": "first_deployment", "facts": 0, "versions": 0}
    with sqlite3.connect(store.database_path) as con:
        assert con.execute("SELECT count(*) FROM memory_candidates").fetchone()[0] == 0
    before = store.database_path.read_bytes()
    with pytest.raises(GatewayError):
        store.initialize()
    assert store.database_path.read_bytes() == before
    assert store.verify_integrity()["verified"] is True


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_initialize_refuses_every_existing_entry_without_changing_it(tmp_path, suffix):
    path = tmp_path / "memory.db"
    occupied = tmp_path / ("memory.db" + suffix)
    occupied.write_bytes(b"foreign")
    with pytest.raises(GatewayError):
        SQLiteMemoryStore(path).initialize()
    assert occupied.read_bytes() == b"foreign"
    if suffix:
        assert not path.exists()


def test_initialize_missing_parent_never_creates_directories(tmp_path):
    parent = tmp_path / "absent"
    with pytest.raises(GatewayError):
        SQLiteMemoryStore(parent / "memory.db").initialize()
    assert not parent.exists()


def test_initialize_refuses_dangling_symlink(tmp_path):
    path = tmp_path / "memory.db"
    try:
        path.symlink_to(tmp_path / "absent")
    except OSError:
        pytest.skip("Symlink privilege unavailable")
    with pytest.raises(GatewayError):
        SQLiteMemoryStore(path).initialize()
    assert path.is_symlink()
    assert not (tmp_path / "absent").exists()


def test_initialize_auxiliary_creation_race_preserves_foreign_entry(tmp_path, monkeypatch):
    path = tmp_path / "memory.db"
    original_open = os.open

    def race(name, flags, *args, **kwargs):
        if str(name).endswith("-wal"):
            (tmp_path / "memory.db-wal").write_bytes(b"foreign")
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", race)
    with pytest.raises(GatewayError):
        SQLiteMemoryStore(path).initialize()
    assert (tmp_path / "memory.db-wal").read_bytes() == b"foreign"
    assert path.read_bytes() == b""  # Clearly failed reservation; never adopted on retry.


def test_initialize_failure_keeps_owned_partial_and_refuses_retry(tmp_path, monkeypatch):
    store = SQLiteMemoryStore(tmp_path / "memory.db")

    def fail(con):
        raise sqlite3.OperationalError("synthetic failure")

    monkeypatch.setattr(store, "_schema", fail)
    with pytest.raises(GatewayError):
        store.initialize()
    assert store.database_path.exists()
    with pytest.raises(GatewayError) as caught:
        store.initialize()
    assert caught.value.code == "CONFLICT"


def test_initialize_schema_race_preserves_foreign_journal(tmp_path, monkeypatch):
    """SQLite must never adopt/delete a journal replaced after namespace checks."""
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    journal = tmp_path / "memory.db-journal"
    original_schema = store._schema
    _allow_reservation_replacement(monkeypatch, store.database_path)

    def race(con):
        journal.unlink()
        journal.write_bytes(b"foreign raced bytes")
        original_schema(con)

    monkeypatch.setattr(store, "_schema", race)
    with pytest.raises(GatewayError) as caught:
        store.initialize()
    assert caught.value.code == "STORAGE_UNAVAILABLE"
    assert journal.read_bytes() == b"foreign raced bytes"


def test_initialize_sqlite_only_opens_memory_and_retains_every_descriptor(tmp_path, monkeypatch):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    original_open, original_connect, original_schema = os.open, sqlite3.connect, store._schema
    descriptors, opened = [], []

    def reserve(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        descriptors.append(fd)
        return fd

    def connect(name, *args, **kwargs):
        opened.append(name)
        assert name == ":memory:"
        return original_connect(name, *args, **kwargs)

    def schema(con):
        assert len(descriptors) == 4
        assert all(os.fstat(fd).st_size == 0 for fd in descriptors)
        assert con.execute("PRAGMA database_list").fetchone()[2] == ""
        original_schema(con)

    monkeypatch.setattr(os, "open", reserve)
    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(store, "_schema", schema)
    monkeypatch.setattr(store, "_connect", lambda **_: pytest.fail("Final path must never be opened by SQLite"))
    assert store.initialize()["initialized"] is True
    assert opened == [":memory:"]
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)
    for suffix in ("-wal", "-shm", "-journal"):
        assert (tmp_path / ("memory.db" + suffix)).read_bytes() == b""


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_initialize_schema_race_preserves_replaced_database_and_sidecars(tmp_path, monkeypatch, suffix):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    victim = tmp_path / ("memory.db" + suffix)
    original_schema = store._schema
    _allow_reservation_replacement(monkeypatch, store.database_path)

    def race(con):
        victim.unlink()
        victim.write_bytes(b"foreign replacement")
        original_schema(con)

    monkeypatch.setattr(store, "_schema", race)
    with pytest.raises(GatewayError) as caught:
        store.initialize()
    assert caught.value.code == "STORAGE_UNAVAILABLE"
    assert victim.read_bytes() == b"foreign replacement"


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_initialize_write_race_preserves_foreign_database_and_sidecars(tmp_path, monkeypatch, suffix):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    victim = tmp_path / ("memory.db" + suffix)
    _allow_reservation_replacement(monkeypatch, store.database_path)
    original_write = os.write
    raced = False

    def race(fd, data):
        nonlocal raced
        written = original_write(fd, data)
        if not raced:
            raced = True
            victim.unlink()
            victim.write_bytes(b"foreign during publication")
        return written

    monkeypatch.setattr(os, "write", race)
    with pytest.raises(GatewayError) as caught:
        store.initialize()
    assert caught.value.code == "STORAGE_UNAVAILABLE"
    assert raced
    assert victim.read_bytes() == b"foreign during publication"


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_initialize_never_accepts_nonempty_owned_sidecar(tmp_path, monkeypatch, suffix):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    sidecar = tmp_path / ("memory.db" + suffix)
    original_schema = store._schema

    def race(con):
        sidecar.write_bytes(b"foreign same inode")
        original_schema(con)

    monkeypatch.setattr(store, "_schema", race)
    with pytest.raises(GatewayError) as caught:
        store.initialize()
    assert caught.value.code == "STORAGE_UNAVAILABLE"
    assert sidecar.read_bytes() == b"foreign same inode"
    assert store.database_path.read_bytes() == b""


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_initialize_rejects_new_hardlink_to_any_reserved_file(tmp_path, monkeypatch, suffix):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    reserved = tmp_path / ("memory.db" + suffix)
    alias = tmp_path / "synthetic-link"
    original_schema = store._schema

    def race(con):
        os.link(reserved, alias)
        original_schema(con)

    monkeypatch.setattr(store, "_schema", race)
    with pytest.raises(GatewayError) as caught:
        store.initialize()
    assert caught.value.code == "STORAGE_UNAVAILABLE"
    assert alias.exists() and alias.stat().st_nlink == 2
    assert reserved.read_bytes() == b""


@pytest.mark.parametrize("failure", ["absent", "unsupported", "sqlite-error", "nonbytes", "invalid-header"])
def test_initialize_serialization_failure_is_stable_and_keeps_reservations(tmp_path, monkeypatch, failure):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    original_connect = sqlite3.connect

    class Connection:
        def __init__(self, con):
            self.con = con

        def __getattr__(self, name):
            if name == "serialize":
                if failure == "absent":
                    return None
                return self.fail_serialize
            return getattr(self.con, name)

        def fail_serialize(self):
            if failure == "unsupported":
                raise NotImplementedError("synthetic unsupported serializer")
            if failure == "sqlite-error":
                raise sqlite3.OperationalError("synthetic serialize failure")
            return "not bytes" if failure == "nonbytes" else b"not SQLite"

    def connect(name, *args, **kwargs):
        assert name == ":memory:"
        return Connection(original_connect(name, *args, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(GatewayError) as caught:
        store.initialize()
    assert caught.value.code == "STORAGE_UNAVAILABLE"
    for suffix in ("", "-wal", "-shm", "-journal"):
        assert (tmp_path / ("memory.db" + suffix)).read_bytes() == b""
    with pytest.raises(GatewayError) as retry:
        store.initialize()
    assert retry.value.code == "CONFLICT"


@pytest.mark.parametrize("failure", ["write-zero", "write-error", "partial-then-error", "fsync-error"])
def test_initialize_publication_failures_keep_partial_namespace(tmp_path, monkeypatch, failure):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    original_write = os.write
    calls = 0

    def write(fd, data):
        nonlocal calls
        calls += 1
        if failure == "write-zero":
            return 0
        if failure == "write-error" or failure == "partial-then-error" and calls > 1:
            raise OSError("synthetic private error that must not escape")
        return original_write(fd, data[:128] if failure == "partial-then-error" else data)

    def fsync(fd):
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(os, "write", write)
    if failure == "fsync-error":
        monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(GatewayError) as caught:
        store.initialize()
    assert caught.value.code == "STORAGE_UNAVAILABLE"
    assert "synthetic" not in str(caught.value)
    expected = 128 if failure == "partial-then-error" else 0
    assert (store.database_path.stat().st_size > 128 if failure == "fsync-error"
            else store.database_path.stat().st_size == expected)
    for suffix in ("-wal", "-shm", "-journal"):
        assert (tmp_path / ("memory.db" + suffix)).read_bytes() == b""
    with pytest.raises(GatewayError) as retry:
        store.initialize()
    assert retry.value.code == "CONFLICT"


def test_initialize_fsync_race_detects_replacement_without_deleting_it(tmp_path, monkeypatch):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    journal = tmp_path / "memory.db-journal"
    _allow_reservation_replacement(monkeypatch, store.database_path)
    original_fsync = os.fsync

    def fsync(fd):
        original_fsync(fd)
        journal.unlink()
        journal.write_bytes(b"foreign after fsync")

    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(GatewayError) as caught:
        store.initialize()
    assert caught.value.code == "STORAGE_UNAVAILABLE"
    assert journal.read_bytes() == b"foreign after fsync"


def populated(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    first = store.create("synthetic", "choice", "first", [REF], "create-1")["memory"]
    store.revise(first["memory_id"], "second", [REF], 1, "revise-1")
    return store


def test_integrity_checks_all_versions_without_mutating_database(tmp_path):
    store = populated(tmp_path)
    before = store.database_path.read_bytes()
    entries = set(tmp_path.iterdir())
    result = store.verify_integrity()
    assert result["verified"] is True
    assert result["complete"] is True
    assert (result["facts"], result["versions"], result["checked_versions"]) == (1, 2, 2)
    assert result["candidate_schema"] == "absent"
    assert result["diagnostics"] == []
    assert store.database_path.read_bytes() == before
    assert set(tmp_path.iterdir()) == entries


@pytest.mark.parametrize("damage,code", [
    ("DELETE FROM memory_requests", "request_coverage"),
    ("DROP TRIGGER write_provenance_no_delete; DELETE FROM write_provenance", "provenance_coverage"),
    ("DROP TRIGGER memory_versions_no_update; UPDATE memory_versions SET source_refs='[\"bad\"]'", "version_record"),
    ("DROP TRIGGER memory_versions_no_delete; DELETE FROM memory_versions WHERE version=1", "version_sequence"),
    ("INSERT INTO memory_facts VALUES ('memory:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','x','y','t')", "fact_coverage"),
])
def test_integrity_reports_corruption_without_private_details(tmp_path, damage, code):
    store = populated(tmp_path)
    with sqlite3.connect(store.database_path) as con:
        con.executescript(damage)
    before = store.database_path.read_bytes()
    result = store.verify_integrity()
    assert result["verified"] is False
    assert code in result["diagnostics"]
    serialized = json.dumps(result)
    assert str(tmp_path) not in serialized
    assert "synthetic" not in serialized
    assert REF not in serialized
    assert store.database_path.read_bytes() == before


def test_integrity_missing_or_unrecognized_database_is_not_verified(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    with pytest.raises(GatewayError):
        store.verify_integrity()
    assert not store.database_path.exists()
    store.database_path.touch()
    result = store.verify_integrity()
    assert result["verified"] is False
    assert "required_schema" in result["diagnostics"]


@pytest.mark.parametrize("operation", [
    lambda store: store.search("synthetic"),
    lambda store: store.matching_slot("synthetic", "choice"),
    lambda store: store.fetch("memory:" + "b" * 32),
    lambda store: store.versions("memory:" + "b" * 32),
    lambda store: store.request("synthetic-request"),
])
def test_missing_configured_store_is_unavailable_not_a_no_hit(tmp_path, operation):
    store = SQLiteMemoryStore(tmp_path / "absent.db")
    with pytest.raises(GatewayError) as caught:
        operation(store)
    assert caught.value.code == "STORAGE_UNAVAILABLE"
    assert not store.database_path.exists()


def test_integrity_enforces_version_bound(tmp_path):
    store = populated(tmp_path)
    with sqlite3.connect(store.database_path) as con:
        mid = con.execute("SELECT memory_id FROM memory_facts").fetchone()[0]
        con.executemany("INSERT INTO memory_versions VALUES (?,?,'x','active',?,'t')",
                        ((mid, version, json.dumps([REF])) for version in range(3, 10002)))
    result = store.verify_integrity()
    assert result["verified"] is False
    assert result["complete"] is False
    assert result["checked_versions"] <= 10000
    assert "row_budget_exceeded" in result["diagnostics"]


@pytest.mark.parametrize("damage,code", [
    ("UPDATE memory_requests SET request_sha256=printf('%063d',0)||'g'", "request_record"),
    ("UPDATE memory_requests SET idempotency_key='invalid/key'", "request_record"),
    ("DROP TRIGGER write_provenance_no_update; UPDATE write_provenance SET reported_client='C:/private/path'", "provenance_record"),
    ("DROP TRIGGER memory_versions_no_update; UPDATE memory_versions SET state='retired' WHERE version=1", "retirement_sequence"),
])
def test_integrity_validates_requests_identity_and_terminal_retirement(tmp_path, damage, code):
    store = populated(tmp_path)
    with sqlite3.connect(store.database_path) as con:
        # Different request rows need distinct invalid keys for this fixture.
        if "idempotency_key=" in damage:
            damage = "UPDATE memory_requests SET idempotency_key=idempotency_key||'/invalid'"
        con.executescript(damage)
    result = store.verify_integrity()
    assert result["verified"] is False
    assert code in result["diagnostics"]


@pytest.mark.parametrize("damage", [
    "DROP TRIGGER memory_versions_no_update",
    "DROP TRIGGER memory_versions_no_update; CREATE TRIGGER memory_versions_no_update BEFORE UPDATE ON memory_versions BEGIN SELECT 1; END",
    "INSERT INTO memory_candidates VALUES ('bad','bad','not-json','t')",
    "INSERT INTO memory_candidate_reviews VALUES ('missing','approve','note','none','t')",
    "INSERT INTO operation_audit VALUES (1,'bogus','memory','missing','t','success')",
])
def test_integrity_rejects_schema_candidate_review_and_audit_corruption(tmp_path, damage):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.initialize()
    with sqlite3.connect(store.database_path) as con:
        con.executescript(damage)
    result = store.verify_integrity()
    assert result["verified"] is False


@pytest.mark.parametrize("table,insert", [
    ("memory_candidates", "INSERT INTO memory_candidates VALUES (?,?,'{}','t')"),
    ("memory_candidate_reviews", "INSERT INTO memory_candidate_reviews VALUES (?,'reject','note','none','t')"),
    ("operation_audit", "INSERT INTO operation_audit(operation,resource_type,resource_id,occurred_at,outcome) VALUES ('create_memory','memory',?,'t','success')"),
])
def test_integrity_bounds_candidate_review_and_audit_rows(tmp_path, table, insert):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.initialize()
    with sqlite3.connect(store.database_path) as con:
        rows = ((f"row-{n}", f"digest-{n}") if table == "memory_candidates" else (f"row-{n}",) for n in range(10001))
        con.executemany(insert, rows)
    result = store.verify_integrity()
    assert result["verified"] is False
    assert result["complete"] is False
    assert "row_budget_exceeded" in result["diagnostics"]


@pytest.mark.parametrize("damage", ["target", "digest"])
def test_integrity_valid_candidate_review_commit_then_rejects_wrong_binding(tmp_path, damage):
    from test_evidence_bundle import scenario
    from test_memory_candidates import propose
    g, ref, *_ = scenario(tmp_path)
    candidate = propose(g, ref, predicate="long_term_goal")
    g.capabilities |= {"admin"}
    g.review_memory_candidate(candidate["candidate_id"], "approve", "Reviewed original")
    result = g.commit_memory_candidate(candidate["candidate_id"])
    assert g.memory_store.verify_integrity()["verified"] is True
    with sqlite3.connect(g.memory_store.database_path) as con:
        key = "candidate-" + candidate["candidate_id"][10:]
        if damage == "target":
            con.execute("UPDATE memory_requests SET memory_id=?,version=1 WHERE idempotency_key=?", ("memory:" + "f" * 32, key))
        else:
            con.execute("UPDATE memory_requests SET request_sha256=? WHERE idempotency_key=?", ("a" * 64, key))
    assert g.memory_store.verify_integrity()["verified"] is False


def test_initialize_never_unlinks_paths_manually(tmp_path, monkeypatch):
    def forbid_unlink(path, *args, **kwargs):
        raise AssertionError("Path-based cleanup can delete a racing foreign entry")
    from pathlib import Path
    monkeypatch.setattr(Path, "unlink", forbid_unlink)
    assert SQLiteMemoryStore(tmp_path / "memory.db").initialize()["initialized"]


@pytest.mark.parametrize("key", ["candidate-normal", "candidate-" + "a" * 32])
def test_integrity_does_not_infer_candidate_origin_from_an_unreserved_request_key(tmp_path, key):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.create("synthetic", "choice", "ordinary fact", [REF], key)
    assert store.verify_integrity()["verified"] is True


def test_integrity_allows_ordinary_request_key_colliding_with_pending_candidate(tmp_path):
    from test_evidence_bundle import scenario
    from test_memory_candidates import propose
    g, ref, *_ = scenario(tmp_path)
    candidate = propose(g, ref, predicate="long_term_goal")
    key = "candidate-" + candidate["candidate_id"][10:]
    g.memory_store.create("synthetic", "ordinary", "ordinary fact", [REF], key)
    assert g.memory_store.verify_integrity()["verified"] is True
