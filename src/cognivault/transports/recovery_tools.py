"""Opt-in admin-only recovery tools; paths come from private configuration."""
from mcp import types
from ..contracts import GatewayError

SHAPES={'recovery_snapshot_plan':(set(),set()),
        'recovery_snapshot_status':({'snapshot_key','expected_manifest_sha256'},{'snapshot_key'}),
        'create_recovery_snapshot':({'snapshot_key'},{'snapshot_key'}),
        'verify_recovery_snapshot':({'snapshot_key','restore'},{'snapshot_key'})}


def recovery_tools(gateway,read_only,write_only):
    config=getattr(gateway,'config',None)
    if config is None or getattr(config,'recovery_root',None) is None \
            or 'admin' not in getattr(gateway,'capabilities',()): return []
    key={'type':'string','pattern':'^[a-z0-9][a-z0-9-]{0,63}$'}
    definitions=[('recovery_snapshot_plan','Aggregate configured private full snapshot size/capacity; no paths or content returned.',{}),
                 ('recovery_snapshot_status','Read-only bounded metadata observation. Integrity is not verified, owner is unknown, and automatic resume is unsupported. Use explicit verify for payload proof.',{'snapshot_key':key,'expected_manifest_sha256':{'type':'string','pattern':'^[0-9a-f]{64}$'}}),
                 ('create_recovery_snapshot','Create an immutable verified full V2 snapshot under the configured private root. Includes committed WAL, Study, Assets, sources, canonical provenance and migration sidecar. Reuse only after verification.',{'snapshot_key':key}),
                 ('verify_recovery_snapshot','Reverify snapshot bytes, schema and row digests. Optional restore uses a new isolated private target; never overwrites production. Original source/canonical identities and provenance are verified inside the Gateway.',{'snapshot_key':key,'restore':{'type':'boolean'}})]
    return [types.Tool(name=n,description=d,inputSchema={'type':'object','properties':p,'required':list(SHAPES[n][1]),'additionalProperties':False},
                       annotations=read_only if n in {'recovery_snapshot_plan','recovery_snapshot_status'} else write_only) for n,d,p in definitions]


def call_recovery_tool(gateway,name,arguments):
    if name not in SHAPES: return None
    allowed,required=SHAPES[name]
    if type(arguments) is not dict or set(arguments)-allowed or not required<=set(arguments) \
            or any(v is None for v in arguments.values()):
        raise GatewayError('INVALID_ARGUMENT','Invalid recovery arguments')
    return getattr(gateway,name)(**arguments)
