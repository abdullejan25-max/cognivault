"""Synthetic native MCP deployment controls, never a production database."""
import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from cognivault.adapters.memory import SQLiteMemoryStore
from cognivault.config import AppConfig
from cognivault.contracts import GatewayError
from cognivault.gateway import Gateway
from cognivault.transports.mcp_stdio import create_mcp_server


def gateway(tmp_path, caps):
    return Gateway(AppConfig('0.8.0', memory_database=tmp_path / 'memory.db'), None,
                   memory_store=SQLiteMemoryStore(tmp_path / 'memory.db'), capabilities=frozenset(caps))


@pytest.mark.parametrize('caps', [{'read'}, {'read', 'write'}])
def test_initialize_hidden_and_denied_to_non_admin(tmp_path, caps):
    g = gateway(tmp_path, caps)
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert 'initialize_memory' not in names
            assert 'verify_memory_integrity' in names
            denied = await client.call_tool('initialize_memory', {})
            assert denied.isError and 'PERMISSION_DENIED' in denied.content[0].text
    anyio.run(check)
    assert not g.memory_store.database_path.exists()


def test_native_admin_initializes_once_then_reader_verifies(tmp_path):
    g = gateway(tmp_path, {'admin'})
    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert {'initialize_memory', 'verify_memory_integrity'} <= names
            bad = await client.call_tool('initialize_memory', {'path': 'invented.db'})
            assert bad.isError
            first = await client.call_tool('initialize_memory', {})
            assert not first.isError and first.structuredContent['initialized']
            assert (await client.call_tool('initialize_memory', {})).isError
            result = await client.call_tool('verify_memory_integrity', {})
            assert not result.isError and result.structuredContent['verified']
            assert result.structuredContent['facts'] == 0
    anyio.run(check)
    assert gateway(tmp_path, {'read'}).verify_memory_integrity()['verified']


def test_unconfigured_and_unreadable_are_not_empty_success(tmp_path):
    with pytest.raises(GatewayError) as error:
        Gateway(AppConfig('0.8.0'), None).verify_memory_integrity()
    assert error.value.code == 'STORAGE_UNAVAILABLE'
    with pytest.raises(GatewayError) as error:
        gateway(tmp_path, {'read'}).verify_memory_integrity()
    assert error.value.code == 'STORAGE_UNAVAILABLE'
