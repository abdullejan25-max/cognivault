"""Package auditing follows the source version without weakening privacy checks."""

from pathlib import Path
import io
import subprocess
import sys
import tarfile
import tomllib
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


def prepare_archives(root: Path, version: str, *, private: str | None = None,
                     include_runtime: bool = True) -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nname="cognivault"\nversion="{version}"\n', encoding="utf-8"
    )
    dist = root / "dist"
    dist.mkdir()
    with zipfile.ZipFile(dist / f"cognivault-{version}-py3-none-any.whl", "w") as archive:
        for name in ["cognivault/__init__.py", "chatgpt_study_system/transports/mcp_stdio.py",
                     f"cognivault-{version}.dist-info/METADATA"]:
            archive.writestr(name, "synthetic fixture")
        if private:
            archive.writestr(private, "invented private marker")
        if include_runtime:
            archive.writestr('cognivault/orchestration/harness_runtime.mjs', '// synthetic runtime')
    with tarfile.open(dist / f"cognivault-{version}.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo(f"cognivault-{version}/src/cognivault/__init__.py")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"#"))
        if include_runtime:
            info = tarfile.TarInfo(f'cognivault-{version}/src/cognivault/orchestration/harness_runtime.mjs')
            info.size = 1
            archive.addfile(info, io.BytesIO(b'#'))


def audit(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify_package_contents.py")],
        cwd=root, capture_output=True, text=True, timeout=20,
    )


@pytest.mark.parametrize("version", ["0.9.0", "0.9.1", "0.10.0"])
def test_package_audit_uses_declared_version(tmp_path: Path, version: str) -> None:
    prepare_archives(tmp_path, version)
    result = audit(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("privacy audit passed") == 2


def test_package_audit_requires_harness_runtime(tmp_path):
    prepare_archives(tmp_path, '0.9.0', include_runtime=False)
    result = audit(tmp_path)
    assert result.returncode != 0


@pytest.mark.parametrize("missing", ["*.whl", "*.tar.gz"])
def test_package_audit_requires_both_current_archives(tmp_path: Path, missing: str) -> None:
    prepare_archives(tmp_path, "0.9.0")
    next((tmp_path / "dist").glob(missing)).unlink()
    result = audit(tmp_path)
    assert result.returncode != 0
    assert "expected one CogniVault wheel and one source archive" in result.stderr


def test_package_audit_does_not_substitute_stale_archives(tmp_path: Path) -> None:
    prepare_archives(tmp_path, "0.8.0")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="cognivault"\nversion="0.9.0"\n', encoding="utf-8"
    )
    result = audit(tmp_path)
    assert result.returncode != 0
    assert "expected one CogniVault wheel and one source archive" in result.stderr


@pytest.mark.parametrize("private", [".codex/config.toml", "StudyVault/private.md", "config.local.toml"])
def test_current_archive_privacy_audit_is_still_enforced(tmp_path: Path, private: str) -> None:
    prepare_archives(tmp_path, "0.9.0", private=private)
    result = audit(tmp_path)
    assert result.returncode != 0
    assert "private checkout file in package" in result.stderr


def test_lockfile_matches_source_candidate_version() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    package = next(item for item in lock["package"] if item["name"] == project["name"])
    assert project["version"] == "0.9.1"
    assert package["version"] == project["version"]
