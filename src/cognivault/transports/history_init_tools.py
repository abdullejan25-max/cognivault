"""Admin-only first-deployment entry point; no paths or migration arguments."""
from mcp import types

from ..adapters.history import SQLiteHistoryBackend
from ..contracts import GatewayError


def history_init_tools(gateway, write_only):
    if ('admin' not in getattr(gateway, 'capabilities', frozenset())
            or not isinstance(getattr(gateway, 'history_backend', None), SQLiteHistoryBackend)):
        return []
    return [types.Tool(name='initialize_history',
        description='Admin-only first deployment into an absent SQLite History namespace. Refuses existing database or auxiliary entries; never adopts, migrates, overwrites or deletes partial storage.',
        inputSchema={'type':'object', 'properties':{}, 'additionalProperties':False},
        annotations=write_only)]


def call_history_init_tool(gateway, name, arguments):
    if name != 'initialize_history':
        return None
    if type(arguments) is not dict or arguments:
        raise GatewayError('INVALID_ARGUMENT', 'Invalid History initialization request')
    return gateway.initialize_history()
