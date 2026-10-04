import hashlib
from pathlib import Path
import subprocess

import pytest

from cognivault.adapters.history import NotConfiguredHistoryBackend
from cognivault.config import AppConfig
from cognivault.contracts import BackendHealth, GatewayError
from cognivault.gateway import Gateway


def _snapshot(root: Path) -> tuple[tuple[str, bool, int, str], ...]:
    items = []
    for path in sorted(root.rglob("*")):
        data_hash = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
        items.append((str(path.relative_to(root)), path.is_dir(), path.stat().st_mtime_ns, data_hash))
    return tuple(items)


def test_health_report_is_side_effect_free_and_path_safe(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "invented-study"
    root.mkdir()
    (root / "lesson.md").write_text("Synthetic algebra notes.", encoding="utf-8")
    before = _snapshot(tmp_path)

    def forbidden_process(*args, **kwargs):
        raise AssertionError("health_report launched a process")

    monkeypatch.setattr(subprocess, "Popen", forbidden_process)
    monkeypatch.setattr(subprocess, "run", forbidden_process)
    gateway = Gateway(
        AppConfig(gateway_version="0.1.0", study_root=root),
        study_backend=None,
        history_backend=NotConfiguredHistoryBackend(),
        qmd_discoverable=lambda: True,
    )
    report = gateway.health_report()

    assert report == {
        "gateway_version": "0.1.0",
        "study": {"configured": True, "root_exists": True, "readable": True},
        "history": {"backend": "not_configured", "status": "not_configured"},
        "qmd": {"discoverable": True},
    }
    assert _snapshot(tmp_path) == before
    assert str(tmp_path) not in str(report)
    assert not list(tmp_path.rglob("*.db"))
    assert not list(tmp_path.rglob("*.log"))
    assert not list(tmp_path.rglob("cache"))


def test_health_report_handles_unconfigured_study_and_missing_qmd(tmp_path: Path) -> None:
    gateway = Gateway(
        AppConfig(gateway_version="0.1.0", study_root=None),
        study_backend=None,
        qmd_discoverable=lambda: False,
    )

    report = gateway.health_report()

    assert report["study"] == {"configured": False, "root_exists": False, "readable": False}
    assert report["qmd"] == {"discoverable": False}


def test_health_report_sanitizes_unexpected_history_probe_failure(tmp_path: Path) -> None:
    class FailingHistory:
        def probe(self):
            raise RuntimeError(r"C:\private\history.db: token=secret")

    gateway = Gateway(
        AppConfig(gateway_version="0.1.0", study_root=tmp_path),
        study_backend=None,
        history_backend=FailingHistory(),
        qmd_discoverable=lambda: True,
    )

    with pytest.raises(GatewayError) as exc:
        gateway.health_report()

    assert exc.value.code == "INTERNAL_ERROR"
    assert exc.value.correlation_id
    assert "C:\\private" not in str(exc.value)
    assert "secret" not in str(exc.value)


def test_health_report_treats_unexpected_qmd_discovery_failure_as_not_discoverable(tmp_path: Path) -> None:
    def failing_discovery() -> bool:
        raise RuntimeError(r"C:\private\qmd.exe: token=secret")

    gateway = Gateway(
        AppConfig(gateway_version="0.1.0", study_root=tmp_path),
        study_backend=None,
        qmd_discoverable=failing_discovery,
    )

    report = gateway.health_report()

    assert report["qmd"] == {"discoverable": False}
    assert "C:\\private" not in str(report)
    assert "secret" not in str(report)


def test_health_report_projects_hostile_history_metadata_to_safe_values(tmp_path: Path) -> None:
    class HostileHistory:
        def probe(self) -> BackendHealth:
            return BackendHealth(backend=r"C:\private\memory.db", status="token=secret")

    gateway = Gateway(
        AppConfig(gateway_version="0.1.0", study_root=tmp_path),
        study_backend=None,
        history_backend=HostileHistory(),
        qmd_discoverable=lambda: False,
    )

    report = gateway.health_report()

    assert report["history"] == {"backend": "unavailable", "status": "unavailable"}
    assert "C:\\private" not in str(report)
    assert "secret" not in str(report)
