"""Model-neutral contracts. Grants are trusted application policy, not authentication."""
from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from typing import Protocol


def fingerprint(value) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    if len(encoded) > 1_000_000:
        raise ValueError("PAYLOAD_TOO_LARGE")
    return hashlib.sha256(encoded).hexdigest()


def identifier(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}", value) is not None


def finite(value, minimum, maximum):
    return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum


class AdapterError(Exception):
    """Stable executor failure; arbitrary backend text never becomes audit text."""
    CODES = frozenset({'AUTH_REQUIRED', 'EXECUTOR_BLOCKED', 'MODEL_REFUSED', 'NETWORK_ERROR',
                       'RATE_LIMITED', 'INVALID_RESPONSE', 'EXECUTOR_TIMEOUT'})
    BLOCKING = frozenset({'AUTH_REQUIRED', 'EXECUTOR_BLOCKED'})

    def __init__(self, code, *, retryable=False):
        if code not in self.CODES or type(retryable) is not bool:
            raise ValueError('INVALID_ADAPTER_ERROR')
        self.code, self.retryable = code, retryable
        super().__init__(code)


@dataclass(frozen=True)
class Task:
    task_id: str
    correlation_id: str
    kind: str
    inputs: dict = field(repr=False)
    grants: frozenset[str]
    external_allowed: bool = False
    min_confidence: float = .8
    timeout_s: float = 30
    max_attempts: int = 2
    required_capabilities: frozenset[str] = frozenset({'structured'})

    def validate(self):
        if (not all(identifier(v) for v in (self.task_id, self.correlation_id, self.kind))
                or type(self.inputs) is not dict or type(self.grants) is not frozenset
                or not self.grants <= {"read", "write", "ingest"}
                or type(self.external_allowed) is not bool
                or type(self.required_capabilities) is not frozenset
                or not self.required_capabilities <= {'text', 'image', 'structured'}
                or not finite(self.min_confidence, 0, 1) or not finite(self.timeout_s, .001, 3600)
                or type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 5):
            raise ValueError("INVALID_TASK")
        fingerprint(self.inputs)

    def digest(self):
        self.validate()
        return fingerprint({**self.__dict__, "grants": sorted(self.grants),
                            "required_capabilities": sorted(self.required_capabilities)})


@dataclass(frozen=True)
class Descriptor:
    agent_id: str
    version: str
    kinds: frozenset[str]
    capabilities: frozenset[str]
    data_scope: str  # synthetic, local, external
    relative_cost: float = 0
    latency_ms: float = 0

    def validate(self):
        if (not identifier(self.agent_id) or not identifier(self.version)
                or self.data_scope not in {"synthetic", "local", "external"}
                or type(self.kinds) is not frozenset or not self.kinds
                or not all(identifier(k) for k in self.kinds)
                or type(self.capabilities) is not frozenset
                or not self.capabilities <= {"text", "image", "structured"}
                or not finite(self.relative_cost, 0, 1_000_000)
                or not finite(self.latency_ms, 0, 3_600_000)):
            raise ValueError("INVALID_DESCRIPTOR")


@dataclass(frozen=True)
class Result:
    task_id: str
    correlation_id: str
    adapter_id: str
    adapter_version: str
    output: dict = field(repr=False)
    confidence: float
    evidence: tuple[str, ...]
    uncertainties: tuple[str, ...] = ()

    def validate(self, task: Task, descriptor: Descriptor):
        if (self.task_id != task.task_id or self.correlation_id != task.correlation_id
                or self.adapter_id != descriptor.agent_id or self.adapter_version != descriptor.version
                or type(self.output) is not dict or not finite(self.confidence, 0, 1)
                or type(self.evidence) is not tuple or len(self.evidence) > 32
                or any(type(e) is not str or not 1 <= len(e) <= 500 for e in self.evidence)
                or len(set(self.evidence)) != len(self.evidence)
                or type(self.uncertainties) is not tuple or len(self.uncertainties) > 32
                or any(type(e) is not str or not 1 <= len(e) <= 500 for e in self.uncertainties)):
            raise ValueError("INVALID_RESULT")
        fingerprint(self.__dict__)

    def digest(self):
        return fingerprint(self.__dict__)


class Adapter(Protocol):
    descriptor: Descriptor
    async def healthy(self) -> bool: ...
    async def execute(self, task: Task, prepared: dict) -> Result: ...


class DomainHandler(Protocol):
    required_grants: frozenset[str]
    async def prepare(self, task: Task) -> dict: ...
    def validate(self, task: Task, result: Result, prepared: dict) -> None: ...
    async def commit(self, task: Task, result: Result, key: str) -> dict: ...
    async def verify(self, task: Task, result: Result, receipt: dict) -> bool: ...


@dataclass(frozen=True)
class Receipt:
    task_id: str
    correlation_id: str
    state: str
    attempts: int
    error_code: str | None
    adapter_id: str
    execution_scope: str
    verified: bool
    events: tuple[str, ...]
    artifacts: tuple[dict, ...] = ()
    output: dict | None = field(default=None, repr=False)
    gateway_receipt: dict | None = field(default=None, repr=False)
    execution_evidence: dict | None = None
    review: dict | None = None


TRANSITIONS = {
    "RECEIVED": {"PLANNED", "BLOCKED", "FAILED", "CANCELLED"},
    "PLANNED": {"AUTHORIZED", "BLOCKED", "FAILED", "CANCELLED"},
    "AUTHORIZED": {"RUNNING", "RETRYABLE_FAILURE", "BLOCKED", "FAILED", "CANCELLED"},
    "RUNNING": {"VALIDATING", "RETRYABLE_FAILURE", "BLOCKED", "FAILED", "CANCELLED"},
    "RETRYABLE_FAILURE": {"RUNNING", "BLOCKED", "FAILED", "CANCELLED"},
    "VALIDATING": {"COMMITTING", "WAITING_FOR_REVIEW", "FAILED", "CANCELLED"},
    "COMMITTING": {"VERIFYING", "FAILED", "PARTIAL", "WAITING_FOR_REVIEW"},
    "VERIFYING": {"COMMITTING", "COMPLETED", "PARTIAL", "WAITING_FOR_REVIEW"},
    "PARTIAL": {"COMMITTING", "WAITING_FOR_REVIEW"},
    "WAITING_FOR_REVIEW": {"COMMITTING", "PARTIAL"},
    "COMPLETED": set(), "CANCELLED": set(), "FAILED": set(),
    "BLOCKED": {'PLANNED', 'FAILED', 'CANCELLED'},
}
