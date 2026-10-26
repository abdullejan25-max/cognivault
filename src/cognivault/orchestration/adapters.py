"""Explicit registration; configured is not connected and a probe is not execution."""
import inspect

from .models import Descriptor


class Registry:
    def __init__(self):
        self._adapters = {}
        self._descriptors = {}
        self._connected = set()
        self._verified = {}
        self._disabled = set()

    def register(self, adapter):
        adapter.descriptor.validate()
        name = adapter.descriptor.agent_id
        if name == 'unassigned':
            raise ValueError('RESERVED_AGENT_ID')
        if name in self._adapters:
            raise ValueError("DUPLICATE_AGENT")
        self._adapters[name] = adapter
        self._descriptors[name] = adapter.descriptor

    def get(self, name):
        try:
            adapter = self._adapters[name]
        except KeyError:
            raise ValueError("AGENT_UNAVAILABLE") from None
        if adapter.descriptor != self._descriptors[name]:
            raise ValueError('AGENT_DESCRIPTOR_CHANGED')
        return adapter

    def set_enabled(self, name, enabled):
        self.get(name)
        if type(enabled) is not bool:
            raise ValueError('INVALID_ENABLED_FLAG')
        self._disabled.discard(name) if enabled else self._disabled.add(name)

    def enabled(self, name):
        return name not in self._disabled

    def select(self, task):
        """Deterministic routing; declarations are probed before any execution."""
        task.validate()
        candidates = [a.descriptor for a in (self.get(name) for name in self._adapters)
                      if task.kind in a.descriptor.kinds
                      and self.enabled(a.descriptor.agent_id)
                      and task.required_capabilities <= a.descriptor.capabilities
                      and (a.descriptor.data_scope != 'external' or task.external_allowed)]
        if not candidates:
            raise ValueError('AGENT_UNAVAILABLE')
        return min(candidates, key=lambda d: (d.data_scope == 'external',
                   task.kind not in self._verified.get(d.agent_id, ()),
                   d.relative_cost, d.latency_ms, d.agent_id)).agent_id

    def connected(self, name):
        self._connected.add(name)

    def verified(self, name, kind):
        self._verified.setdefault(name, set()).add(kind)

    def describe(self, name):
        desc = self.get(name).descriptor
        return {"agent_id": desc.agent_id, "version": desc.version,
                "data_scope": desc.data_scope, "declared_kinds": sorted(desc.kinds),
                "enabled": self.enabled(name), "relative_cost": desc.relative_cost,
                "latency_ms": desc.latency_ms,
                "declared_capabilities": sorted(desc.capabilities),
                "verified_kinds": sorted(self._verified.get(name, ())),
                "status": "VERIFIED" if self._verified.get(name) else
                          "CONNECTED" if name in self._connected else "CONFIGURED"}


class SyntheticAdapter:
    """Test executor; its scope always stays visible in the task receipt."""
    def __init__(self, descriptor: Descriptor, callback):
        if descriptor.data_scope != 'synthetic':
            raise ValueError('SYNTHETIC_SCOPE_REQUIRED')
        self.descriptor = descriptor
        self.callback = callback
        self.calls = 0

    async def healthy(self):
        return True

    async def execute(self, task, prepared):
        self.calls += 1
        result = self.callback(task, prepared)
        return await result if inspect.isawaitable(result) else result
