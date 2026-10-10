"""Synthetic regression: read-only SQLite reads must not create WAL sidecars."""
from contextlib import closing
import sqlite3
import pytest

from cognivault.adapters.memory import SQLiteMemoryStore
from cognivault.config import AppConfig
from cognivault.gateway import Gateway
from cognivault.contracts import GatewayError


@pytest.mark.parametrize("operation", ["request", "fetch", "search", "versions", "create", "revise"])
def test_wal_operations_fail_closed_without_creating_sidecars(tmp_path, monkeypatch, operation):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    ref = "history:" + "a" * 64
    memory = store.create("synthetic", "goal", "physics", [ref], "known")["memory"]
    with closing(sqlite3.connect(store.database_path)) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["memory.db"]
    before = store.database_path.read_bytes(), store.database_path.stat().st_mtime_ns
    gateway = Gateway(AppConfig("0.8.0"), None, memory_store=store,
                      capabilities=frozenset({"read", "write"}))
    calls = {
        "request": lambda: gateway.fetch_memory_request("known"),
        "fetch": lambda: gateway.fetch_memory(memory["memory_id"]),
        "search": lambda: gateway.search_memory("physics"),
        "versions": lambda: gateway.memory_versions(memory["memory_id"]),
        "create": lambda: gateway.create_memory("synthetic", "other", "new", [ref], "new"),
        "revise": lambda: gateway.revise_memory(memory["memory_id"], "new", [ref], 1, "new",
                                               target_guard=memory["target_guard"]),
    }
    # If the preflight fails closed, SQLite never has a chance to create files.
    connect = sqlite3.connect
    opened = []
    def observe_connect(*args, **kwargs):
        opened.append(True)
        return connect(*args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", observe_connect)
    with pytest.raises(GatewayError) as error:
        calls[operation]()
    assert error.value.code == "STORAGE_UNAVAILABLE"
    assert not opened
    assert sorted(p.name for p in tmp_path.iterdir()) == ["memory.db"]
    assert (store.database_path.read_bytes(), store.database_path.stat().st_mtime_ns) == before


def test_delete_mode_reads_keep_database_and_directory_unchanged(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    memory = store.create("synthetic", "goal", "physics", ["history:" + "a" * 64], "known")["memory"]
    with closing(sqlite3.connect(store.database_path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    gateway = Gateway(AppConfig("0.8.0"), None, memory_store=store, capabilities=frozenset({"read"}))
    before = store.database_path.read_bytes(), store.database_path.stat().st_mtime_ns
    assert gateway.fetch_memory_request("known")["request"]["version"] == 1
    gateway.fetch_memory(memory["memory_id"])
    gateway.search_memory("physics")
    gateway.memory_versions(memory["memory_id"])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["memory.db"]
    assert (store.database_path.read_bytes(), store.database_path.stat().st_mtime_ns) == before
