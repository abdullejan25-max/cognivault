"""Application contracts shared by adapters and transports."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


ERROR_CODES = frozenset(
    {
        "INVALID_ARGUMENT",
        "STUDY_UNAVAILABLE",
        "QMD_NOT_FOUND",
        "QMD_RUNTIME_UNSAFE",
        "QMD_VERSION_UNSUPPORTED",
        "BACKEND_TIMEOUT",
        "BACKEND_BAD_OUTPUT",
        "OUTSIDE_ALLOWLIST",
        "INTERNAL_ERROR",
        "HISTORY_UNAVAILABLE",
        "RESOURCE_NOT_FOUND",
        "CONFLICT",
        "PERMISSION_DENIED",
        "PAYLOAD_TOO_LARGE",
        "STORAGE_UNAVAILABLE",
        "UNSUPPORTED_MEDIA_TYPE",
    }
)


class GatewayError(Exception):
    """A stable public error without backend diagnostics or private input."""

    def __init__(self, code: str, message: str, correlation_id: str | None = None) -> None:
        if code not in ERROR_CODES:
            raise ValueError("Unknown gateway error code")
        self.code = code
        self.message = message
        self.correlation_id = correlation_id
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, str]:
        result = {"code": self.code, "message": self.message}
        if self.correlation_id:
            result["correlation_id"] = self.correlation_id
        return result


@dataclass(frozen=True)
class BackendHealth:
    backend: str
    status: str


@dataclass(frozen=True)
class BackendStudyHit:
    title: str
    snippet: str
    source_id: str
    source_path: str | Path
    score: float | None = None


@dataclass(frozen=True)
class SafeStudyResult:
    title: str
    snippet: str
    source_id: str
    source_path: str
    retrieval_backend: str
    truncated: bool
    score: float | None = None

    def to_dict(self) -> dict[str, str | float | bool]:
        result: dict[str, str | float | bool] = {
            "title": self.title,
            "snippet": self.snippet,
            "source_id": self.source_id,
            "source_path": self.source_path,
            "retrieval_backend": self.retrieval_backend,
            "truncated": self.truncated,
        }
        if self.score is not None:
            result["score"] = self.score
        return result


class StudyBackend(Protocol):
    @property
    def name(self) -> str: ...

    def search(self, query: str, limit: int) -> Sequence[BackendStudyHit]: ...


class HistoryBackend(Protocol):
    def probe(self) -> BackendHealth: ...

    def list_sources(self) -> Sequence["HistorySource"]: ...

    def search(self, query: str, *, source_id: str | None = None,
               conversation_id: str | None = None, limit: int = 5,
               offset: int = 0) -> "HistoryPage": ...

    def fetch(self, item_id: str) -> "HistoryItem | None": ...


@dataclass(frozen=True)
class HistoryImportItem:
    source_item_id: str
    conversation_id: str
    role: str
    content: str
    created_at: str


@dataclass(frozen=True)
class HistorySource:
    source_id: str
    label: str
    item_count: int


@dataclass(frozen=True)
class HistoryItem:
    item_id: str
    source_id: str
    source_item_id: str
    conversation_id: str
    role: str
    created_at: str
    content_sha256: str
    content: str
    imported_at: str | None = None
    source_system: str | None = None
    import_batch_id: str | None = None
    write_provenance: dict | None = None


@dataclass(frozen=True)
class HistoryPage:
    items: tuple[HistoryItem, ...]
    total: int
    has_more: bool
