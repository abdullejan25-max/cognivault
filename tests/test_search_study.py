from pathlib import Path

import pytest

from cognivault.config import AppConfig
from cognivault.contracts import BackendStudyHit, GatewayError
from cognivault.gateway import Gateway


class FakeStudyBackend:
    name = "synthetic"

    def __init__(self, hits=(), error=None):
        self.hits = hits
        self.error = error
        self.received = None

    def search(self, query: str, limit: int):
        self.received = (query, limit)
        if self.error:
            raise self.error
        return self.hits


def _gateway(tmp_path: Path, backend: FakeStudyBackend) -> Gateway:
    root = tmp_path / "study"
    root.mkdir(exist_ok=True)
    (root / "lesson.md").write_text("Synthetic lesson", encoding="utf-8")
    return Gateway(AppConfig(gateway_version="0.1.0", study_root=root), backend)


def test_search_returns_bounded_safe_result_and_treats_metacharacters_as_text(tmp_path: Path) -> None:
    backend = FakeStudyBackend([
        BackendStudyHit("Imaginary lesson", "A\x00B\n" + "x" * 600, "raw-private-id", "lesson.md", 0.75)
    ])
    gateway = _gateway(tmp_path, backend)

    result = gateway.search_study("  algebra ; $(whoami)  ", 5)

    assert backend.received == ("algebra ; $(whoami)", 5)
    assert result["results"][0]["title"] == "Imaginary lesson"
    assert result["results"][0]["source_path"] == "lesson.md"
    assert result["results"][0]["source_id"] == "study:lesson.md"
    assert result["results"][0]["retrieval_backend"] == "synthetic"
    assert result["results"][0]["score"] == 0.75
    assert result["backend"] == "synthetic"
    assert result["truncated"] is True
    assert "\x00" not in result["results"][0]["snippet"]
    assert "\n" not in result["results"][0]["snippet"]
    assert len(result["results"][0]["snippet"]) <= 500
    assert result["results"][0]["truncated"] is True
    assert str(tmp_path) not in str(result)


@pytest.mark.parametrize("query", ["", " \t ", "x" * 501, "a\x00b", None, 23])
def test_rejects_invalid_query_before_backend_call(tmp_path: Path, query) -> None:
    backend = FakeStudyBackend()
    gateway = _gateway(tmp_path, backend)

    with pytest.raises(GatewayError) as exc:
        gateway.search_study(query)

    assert exc.value.code == "INVALID_ARGUMENT"
    assert backend.received is None


@pytest.mark.parametrize("limit", [0, 21, True, 1.5, "5"])
def test_rejects_invalid_limit_before_backend_call(tmp_path: Path, limit) -> None:
    backend = FakeStudyBackend()
    gateway = _gateway(tmp_path, backend)

    with pytest.raises(GatewayError) as exc:
        gateway.search_study("valid", limit)

    assert exc.value.code == "INVALID_ARGUMENT"
    assert backend.received is None


@pytest.mark.parametrize(
    ("error", "code"),
    [(TimeoutError("secret path"), "BACKEND_TIMEOUT"), (FileNotFoundError("secret path"), "QMD_NOT_FOUND")],
)
def test_maps_backend_failures_without_leaking_detail(tmp_path: Path, error, code: str) -> None:
    backend = FakeStudyBackend(error=error)
    gateway = _gateway(tmp_path, backend)

    with pytest.raises(GatewayError) as exc:
        gateway.search_study("private query")

    assert exc.value.code == code
    assert "secret path" not in str(exc.value)
    assert "private query" not in str(exc.value)


def test_rejects_malformed_backend_output(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path, FakeStudyBackend([{"raw": "untrusted"}]))

    with pytest.raises(GatewayError) as exc:
        gateway.search_study("valid")

    assert exc.value.code == "BACKEND_BAD_OUTPUT"


def test_rejects_backend_result_outside_allowlist(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("Invented external note", encoding="utf-8")
    hit = BackendStudyHit("Outside", "Invented", "private", outside, None)
    gateway = _gateway(tmp_path, FakeStudyBackend([hit]))

    with pytest.raises(GatewayError) as exc:
        gateway.search_study("valid")

    assert exc.value.code == "OUTSIDE_ALLOWLIST"
    assert str(outside) not in str(exc.value)


def test_unexpected_backend_failure_has_correlation_id_without_detail(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path, FakeStudyBackend(error=RuntimeError("secret diagnostic")))

    with pytest.raises(GatewayError) as exc:
        gateway.search_study("private query")

    assert exc.value.code == "INTERNAL_ERROR"
    assert exc.value.correlation_id
    assert "secret diagnostic" not in str(exc.value)
    assert "private query" not in str(exc.value)


def test_sanitizes_gateway_error_raised_by_backend(tmp_path: Path) -> None:
    backend = FakeStudyBackend(error=GatewayError("QMD_VERSION_UNSUPPORTED", r"C:\private\qmd.exe"))
    gateway = _gateway(tmp_path, backend)

    with pytest.raises(GatewayError) as exc:
        gateway.search_study("private query")

    assert exc.value.code == "QMD_VERSION_UNSUPPORTED"
    assert "C:\\private" not in str(exc.value)
    assert "private query" not in str(exc.value)


def test_unsafe_qmd_runtime_error_keeps_its_public_code_without_private_detail(tmp_path: Path) -> None:
    backend = FakeStudyBackend(error=GatewayError("QMD_RUNTIME_UNSAFE", r"C:\private\qmd-index"))
    gateway = _gateway(tmp_path, backend)

    with pytest.raises(GatewayError) as exc:
        gateway.search_study("valid")

    assert exc.value.code == "QMD_RUNTIME_UNSAFE"
    assert exc.value.message == "Study runtime is unsafe"
    assert "C:\\private" not in str(exc.value)


def test_rejects_backend_name_that_could_leak_a_local_path(tmp_path: Path) -> None:
    backend = FakeStudyBackend([BackendStudyHit("Lesson", "Content", "raw", "lesson.md")])
    backend.name = r"C:\private\tool"
    gateway = _gateway(tmp_path, backend)

    with pytest.raises(GatewayError) as exc:
        gateway.search_study("valid")

    assert exc.value.code == "BACKEND_BAD_OUTPUT"
    assert "C:\\private" not in str(exc.value)


def test_redacts_absolute_local_path_from_backend_result_text(tmp_path: Path) -> None:
    backend = FakeStudyBackend(
        [BackendStudyHit("Lesson", r"See C:\private\lesson.md", "raw", "lesson.md")]
    )
    gateway = _gateway(tmp_path, backend)

    result = gateway.search_study("valid")

    assert result["results"][0]["snippet"] == "See [local path redacted]"


@pytest.mark.parametrize(
    "unsafe_text",
    [
        r"prefixC:\Users\nora\secret.txt",
        r"\Users\nora\secret.txt",
        r"\\host\share\secret.txt",
        "See C:/Users/nora/secret.txt",
        "See /Users/nora/secret.txt",
    ],
)
@pytest.mark.parametrize("field", ["title", "snippet"])
def test_redacts_local_path_hidden_in_backend_text(tmp_path: Path, unsafe_text: str, field: str) -> None:
    title = unsafe_text if field == "title" else "Synthetic lesson"
    snippet = unsafe_text if field == "snippet" else "Synthetic content"
    gateway = _gateway(tmp_path, FakeStudyBackend([BackendStudyHit(title, snippet, "raw", "lesson.md")]))

    result = gateway.search_study("valid")["results"][0]

    assert "[local path redacted]" in result[field]
    assert unsafe_text not in result[field]
    assert result["snippet" if field == "title" else "title"] == (
        "Synthetic content" if field == "title" else "Synthetic lesson"
    )


def test_preserves_benign_text_with_a_single_relative_backslash(tmp_path: Path) -> None:
    snippet = r"LaTeX \frac{x}{2} and A/B are examples."
    gateway = _gateway(tmp_path, FakeStudyBackend([BackendStudyHit("Lesson", snippet, "raw", "lesson.md")]))

    assert gateway.search_study("valid")["results"][0]["snippet"] == snippet


def test_preserves_benign_https_source_in_snippet(tmp_path: Path) -> None:
    snippet = "Reference: https://example.invalid/algebra/lesson"
    gateway = _gateway(tmp_path, FakeStudyBackend([BackendStudyHit("Lesson", snippet, "raw", "lesson.md")]))

    assert gateway.search_study("valid")["results"][0]["snippet"] == snippet


@pytest.mark.parametrize("score", [10**400, float("nan"), float("inf"), "invalid"])
def test_rejects_invalid_or_unrepresentable_backend_score(tmp_path: Path, score) -> None:
    gateway = _gateway(tmp_path, FakeStudyBackend([BackendStudyHit("Lesson", "Content", "raw", "lesson.md", score)]))

    with pytest.raises(GatewayError) as exc:
        gateway.search_study("valid")

    assert exc.value.code == "BACKEND_BAD_OUTPUT"
    assert not exc.value.correlation_id


def test_empty_search_keeps_top_level_backend_and_truncation_contract(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path, FakeStudyBackend())

    assert gateway.search_study("valid") == {"backend": "synthetic", "truncated": False, "results": []}
