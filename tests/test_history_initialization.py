"""First deployment controls against invented temporary namespaces only."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from threading import Barrier

import anyio
from mcp.shared.memory import create_connected_server_and_client_session
import pytest

from cognivault.adapters.history import SQLiteHistoryBackend
from cognivault.config import AppConfig
from cognivault.contracts import GatewayError
from cognivault.gateway import Gateway
from cognivault.transports.mcp_stdio import create_mcp_server


def configured(tmp_path, caps=frozenset({'admin'})):
    database = tmp_path/'history.db'
    inbox = tmp_path/'inbox'
    inbox.mkdir(exist_ok=True)
    return Gateway(AppConfig('0.9.0', history_database=database, history_migration_inbox=inbox),
                   None, SQLiteHistoryBackend(database), capabilities=frozenset(caps))


def test_first_deployment_reentry_refuses_and_preserves_database(tmp_path):
    gateway = configured(tmp_path)
    assert gateway.initialize_history() == {
        'initialized':True, 'deployment_kind':'first_deployment', 'sources':0, 'items':0}
    database = gateway.history_backend.database_path
    before = database.read_bytes()
    with pytest.raises(GatewayError) as error:
        gateway.initialize_history()
    assert error.value.code == 'CONFLICT'
    assert database.read_bytes() == before
    assert gateway.list_history_sources() == {'sources':[]}
    assert gateway.history_backend.probe().status == 'ready'


@pytest.mark.parametrize('suffix', ['', '-wal', '-shm', '-journal'])
def test_existing_namespace_entry_never_modified(tmp_path, suffix):
    gateway = configured(tmp_path)
    occupied = Path(str(gateway.history_backend.database_path)+suffix)
    occupied.write_bytes(b'foreign existing bytes')
    before_entries = set(tmp_path.iterdir())
    with pytest.raises(GatewayError):
        gateway.initialize_history()
    assert occupied.read_bytes() == b'foreign existing bytes'
    assert set(tmp_path.iterdir()) == before_entries


@pytest.mark.parametrize('suffix', ['', '-wal', '-shm', '-journal'])
def test_dangling_namespace_link_never_followed(tmp_path, suffix):
    gateway = configured(tmp_path)
    occupied = Path(str(gateway.history_backend.database_path)+suffix)
    target = tmp_path/'nonexistent-target'
    try:
        occupied.symlink_to(target)
    except OSError:
        if os.name != 'nt':
            pytest.skip('Symlink creation unavailable')
        # Broken directory junctions are reparse entries too and do not need
        # Windows symlink privilege. Both endpoints are fresh test paths.
        import subprocess
        created = subprocess.run([str(Path(os.environ['SYSTEMROOT'])/'System32/cmd.exe'),
            '/d', '/c', 'mklink', '/J', str(occupied), str(target)],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if created.returncode:
            pytest.skip('Reparse-point creation unavailable')
    with pytest.raises(GatewayError):
        gateway.initialize_history()
    from cognivault.adapters.documents import _path_has_reparse_point
    assert _path_has_reparse_point(occupied) and os.path.lexists(occupied)
    assert not target.exists()


def test_concurrent_first_deployment_only_one_winner(tmp_path):
    gateway = configured(tmp_path)
    barrier = Barrier(2)
    def run():
        barrier.wait()
        try:
            return gateway.initialize_history()['initialized']
        except GatewayError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _:run(), range(2)))
    assert sorted(results, key=str) == sorted([True, 'CONFLICT'], key=str)
    assert gateway.list_history_sources() == {'sources':[]}


def test_schema_failure_retains_partial_and_retry_refuses(tmp_path, monkeypatch):
    gateway = configured(tmp_path)
    def fail(connection):
        raise sqlite3.OperationalError('synthetic schema failure')
    monkeypatch.setattr(gateway.history_backend, '_create_schema', fail)
    with pytest.raises(GatewayError) as error:
        gateway.initialize_history()
    assert error.value.code == 'STORAGE_UNAVAILABLE'
    database = gateway.history_backend.database_path
    assert database.exists()
    before = database.read_bytes()
    with pytest.raises(GatewayError) as retry:
        gateway.initialize_history()
    assert retry.value.code == 'CONFLICT'
    assert database.read_bytes() == before


def test_sidecar_reservation_race_preserves_foreign_and_owned_partial(tmp_path, monkeypatch):
    gateway = configured(tmp_path)
    database = gateway.history_backend.database_path
    foreign = Path(str(database)+'-wal')
    original = os.open
    def race(path, flags, *args, **kwargs):
        if Path(path) == foreign:
            foreign.write_bytes(b'foreign raced bytes')
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', race)
    with pytest.raises(GatewayError):
        gateway.initialize_history()
    assert foreign.read_bytes() == b'foreign raced bytes'
    assert database.read_bytes() == b''


def test_journal_changed_during_schema_build_is_preserved_and_refused(tmp_path, monkeypatch):
    gateway = configured(tmp_path)
    journal = Path(str(gateway.history_backend.database_path)+'-journal')
    original = gateway.history_backend._create_schema
    def schema_with_foreign_journal(connection):
        journal.write_bytes(b'foreign journal must never be consumed by SQLite')
        original(connection)
    monkeypatch.setattr(gateway.history_backend, '_create_schema', schema_with_foreign_journal)
    with pytest.raises(GatewayError) as error:
        gateway.initialize_history()
    assert error.value.code == 'STORAGE_UNAVAILABLE'
    assert journal.read_bytes() == b'foreign journal must never be consumed by SQLite'


def test_partial_writes_complete_valid_database_using_owned_fd(tmp_path, monkeypatch):
    gateway = configured(tmp_path)
    original, writes = os.write, []
    def short_write(fd, data):
        count = original(fd, data[:4096])
        writes.append(count)
        return count
    monkeypatch.setattr(os, 'write', short_write)
    assert gateway.initialize_history()['initialized'] is True
    assert len(writes) > 1
    assert sum(writes) == gateway.history_backend.database_path.stat().st_size
    assert gateway.list_history_sources() == {'sources':[]}


def test_failed_partial_write_is_retained_and_not_adopted(tmp_path, monkeypatch):
    gateway = configured(tmp_path)
    original, calls = os.write, []
    def fail_after_partial(fd, data):
        if calls:
            raise OSError('synthetic write failure')
        calls.append(True)
        return original(fd, data[:64])
    monkeypatch.setattr(os, 'write', fail_after_partial)
    with pytest.raises(GatewayError) as error:
        gateway.initialize_history()
    assert error.value.code == 'STORAGE_UNAVAILABLE'
    database = gateway.history_backend.database_path
    before = database.read_bytes()
    assert len(before) == 64 and before.startswith(b'SQLite format 3\x00')
    with pytest.raises(GatewayError) as retry:
        gateway.initialize_history()
    assert retry.value.code == 'CONFLICT'
    assert database.read_bytes() == before


def test_serialization_unavailable_fails_closed_without_final_sqlite_open(tmp_path, monkeypatch):
    gateway = configured(tmp_path)
    original = sqlite3.connect
    class WithoutSerialize:
        serialize = None
        def __init__(self, connection):
            self.connection = connection
        def __getattr__(self, name):
            return getattr(self.connection, name)
    def connect_in_memory_only(database, *args, **kwargs):
        assert database == ':memory:'
        return WithoutSerialize(original(database, *args, **kwargs))
    monkeypatch.setattr(sqlite3, 'connect', connect_in_memory_only)
    with pytest.raises(GatewayError) as error:
        gateway.initialize_history()
    assert error.value.code == 'STORAGE_UNAVAILABLE'
    database = gateway.history_backend.database_path
    assert all(Path(str(database)+suffix).read_bytes() == b'' for suffix in ('', '-wal', '-shm', '-journal'))


def test_present_but_unsupported_serializer_keeps_history_reservations(tmp_path, monkeypatch):
    gateway = configured(tmp_path)
    original = sqlite3.connect
    class UnsupportedSerialize:
        def __init__(self, connection):
            self.connection = connection
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def serialize(self):
            raise NotImplementedError('synthetic unsupported SQLite serialization')
    def connect(database, *args, **kwargs):
        assert database == ':memory:'
        return UnsupportedSerialize(original(database, *args, **kwargs))
    monkeypatch.setattr(sqlite3, 'connect', connect)
    with pytest.raises(GatewayError) as error:
        gateway.initialize_history()
    assert error.value.code == 'STORAGE_UNAVAILABLE'
    database = gateway.history_backend.database_path
    assert all(Path(str(database)+suffix).read_bytes() == b'' for suffix in ('', '-wal', '-shm', '-journal'))


def test_journal_changed_in_write_window_is_preserved(tmp_path, monkeypatch):
    gateway = configured(tmp_path)
    database = gateway.history_backend.database_path
    journal = Path(str(database)+'-journal')
    original = os.write
    def write_with_foreign_journal(fd, data):
        journal.write_bytes(b'foreign race during publication')
        return original(fd, data)
    monkeypatch.setattr(os, 'write', write_with_foreign_journal)
    with pytest.raises(GatewayError) as error:
        gateway.initialize_history()
    assert error.value.code == 'STORAGE_UNAVAILABLE'
    assert journal.read_bytes() == b'foreign race during publication'


def test_owned_descriptor_rebound_to_foreign_file_is_rejected_before_write(tmp_path, monkeypatch):
    gateway = configured(tmp_path)
    database = gateway.history_backend.database_path
    foreign = tmp_path/'foreign.bin'
    foreign.write_bytes(b'foreign bytes must remain unchanged')
    original_open, original_schema, reserved = os.open, gateway.history_backend._create_schema, []
    def capture_database_fd(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if Path(path) == database:
            reserved.append(fd)
        return fd
    def rebind_during_schema(connection):
        original_schema(connection)
        with foreign.open('r+b') as stream:
            os.dup2(stream.fileno(), reserved[0])
    monkeypatch.setattr(os, 'open', capture_database_fd)
    monkeypatch.setattr(gateway.history_backend, '_create_schema', rebind_during_schema)
    with pytest.raises(GatewayError) as error:
        gateway.initialize_history()
    assert error.value.code == 'STORAGE_UNAVAILABLE'
    assert foreign.read_bytes() == b'foreign bytes must remain unchanged'
    assert database.read_bytes() == b''


def test_added_hardlink_to_reservation_is_rejected_before_write(tmp_path, monkeypatch):
    gateway = configured(tmp_path)
    database = gateway.history_backend.database_path
    alias = tmp_path/'unexpected-alias.db'
    original = gateway.history_backend._create_schema
    def linked_during_schema(connection):
        original(connection)
        os.link(database, alias)
    monkeypatch.setattr(gateway.history_backend, '_create_schema', linked_during_schema)
    with pytest.raises(GatewayError) as error:
        gateway.initialize_history()
    assert error.value.code == 'STORAGE_UNAVAILABLE'
    assert database.read_bytes() == alias.read_bytes() == b''


def test_missing_parent_is_not_created(tmp_path):
    parent = tmp_path/'absent'
    backend = SQLiteHistoryBackend(parent/'history.db')
    with pytest.raises(GatewayError):
        backend.initialize_empty()
    assert not parent.exists()


def test_legacy_initialize_remains_compatible(tmp_path):
    backend = SQLiteHistoryBackend(tmp_path/'legacy.db')
    assert backend.initialize() is None
    assert backend.initialize() is None


@pytest.mark.parametrize('caps', [set(), {'read'}, {'write','ingest'}, {'read','write','ingest'}])
def test_non_admin_cannot_discover_or_directly_initialize(tmp_path, caps):
    gateway = configured(tmp_path, caps)
    with pytest.raises(GatewayError) as error:
        gateway.initialize_history()
    assert error.value.code == 'PERMISSION_DENIED'
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            assert 'initialize_history' not in {tool.name for tool in (await client.list_tools()).tools}
            denied = await client.call_tool('initialize_history', {})
            assert denied.isError and denied.structuredContent['error']['code'] == 'PERMISSION_DENIED'
    anyio.run(check)
    assert not gateway.history_backend.database_path.exists()


def test_unconfigured_history_is_hidden_and_directed_call_fails():
    gateway = Gateway(AppConfig('0.9.0'), None, capabilities=frozenset({'admin'}))
    with pytest.raises(GatewayError) as error:
        gateway.initialize_history()
    assert error.value.code == 'HISTORY_UNAVAILABLE'
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            assert 'initialize_history' not in {tool.name for tool in (await client.list_tools()).tools}
            response = await client.call_tool('initialize_history', {})
            assert response.isError and response.structuredContent['error']['code'] == 'HISTORY_UNAVAILABLE'
    anyio.run(check)


def test_non_sqlite_history_cannot_initialize_or_discover(tmp_path):
    gateway = configured(tmp_path)
    gateway.history_backend = object()
    with pytest.raises(GatewayError) as error:
        gateway.initialize_history()
    assert error.value.code == 'HISTORY_UNAVAILABLE'
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            assert 'initialize_history' not in {tool.name for tool in (await client.list_tools()).tools}
            response = await client.call_tool('initialize_history', {})
            assert response.isError and response.structuredContent['error']['code'] == 'HISTORY_UNAVAILABLE'
    anyio.run(check)


def test_admin_mcp_first_deployment_then_source_ingest_and_normalize(tmp_path):
    gateway = configured(tmp_path)
    inbox = gateway.config.history_migration_inbox
    rows = [{'type':'session_meta','payload':{'id':'synthetic-init'}},
            {'type':'response_item','timestamp':'2026-01-01T00:00:00Z','payload':{
                'type':'message','role':'user','content':[{'type':'input_text','text':'Synthetic physics goal.'}]}}]
    raw = ('\n'.join(json.dumps(row) for row in rows)+'\n').encode()
    (inbox/'original.jsonl').write_bytes(raw)
    manifest = json.dumps({'schema_version':1,'entries':[{'source_system':'codex','source_format':'jsonl',
        'record_kind':'raw_session','relative_path':'original.jsonl','sha256':hashlib.sha256(raw).hexdigest(),
        'byte_count':len(raw)}]}).encode()
    (inbox/'manifest.json').write_bytes(manifest)
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(gateway)) as client:
            tools = {tool.name:tool for tool in (await client.list_tools()).tools}
            assert tools['initialize_history'].inputSchema == {
                'type':'object','properties':{},'additionalProperties':False}
            bad = await client.call_tool('initialize_history', {'path':'untrusted.db'})
            assert bad.isError and bad.structuredContent['error']['code'] == 'INVALID_ARGUMENT'
            assert not gateway.history_backend.database_path.exists()
            initialized = await client.call_tool('initialize_history', {})
            assert not initialized.isError and initialized.structuredContent['initialized'] is True
            repeated = await client.call_tool('initialize_history', {})
            assert repeated.isError and repeated.structuredContent['error']['code'] == 'CONFLICT'
            imported = await client.call_tool('ingest_history_sources', {
                'relative_manifest':'manifest.json','expected_manifest_sha256':hashlib.sha256(manifest).hexdigest()})
            assert not imported.isError and imported.structuredContent['imported'] == 1
            assert imported.structuredContent['errors'] == 0
            snapshot = (await client.call_tool('history_normalization_snapshot', {})).structuredContent
            normalized = await client.call_tool('normalize_history_sources', {
                'source_set_sha256':snapshot['source_set_sha256']})
            assert not normalized.isError
            messages = (await client.call_tool('search_canonical_messages', {'query':'Synthetic physics'})).structuredContent
            assert messages['total'] == 1
    anyio.run(check)
