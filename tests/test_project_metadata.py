from __future__ import annotations

import tomllib
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_sdist_excludes_local_agent_work_and_private_runtime(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required for the distribution build check")
    checkout = tmp_path / "synthetic-checkout"
    checkout.mkdir()
    for name in ("pyproject.toml", "LICENSE", ".gitignore"):
        shutil.copy2(ROOT / name, checkout / name)
    package = checkout / "src" / "cognivault"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('"""Synthetic build fixture."""\n', encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(checkout)], check=True)
    local_work = checkout / ".superpowers" / "sdd"
    local_work.mkdir(parents=True)
    (local_work.parent / ".gitignore").write_text("*\n", encoding="utf-8")
    (local_work / ".gitignore").write_text("*\n", encoding="utf-8")
    (local_work / "report.md").write_text("Synthetic local-only audit marker.\n", encoding="utf-8")
    (checkout / "config.local.toml").write_text("# Invented private config marker\n", encoding="utf-8")
    runtime = checkout / "var"
    runtime.mkdir()
    (runtime / "synthetic.db").write_bytes(b"invented runtime marker")
    output = tmp_path / "dist"

    subprocess.run(
        [uv, "build", "--sdist", "--out-dir", str(output)],
        cwd=checkout, check=True, capture_output=True, timeout=60,
    )

    with tarfile.open(output / "cognivault-0.8.0.tar.gz") as archive:
        names = archive.getnames()
    assert "cognivault-0.8.0/src/cognivault/__init__.py" in names
    assert not any("/.superpowers/" in name for name in names)
    assert not any("/var/" in name or name.endswith("/config.local.toml") for name in names)


def test_cognivault_distribution_preserves_release_and_domain_cli() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == "cognivault"
    assert project["version"] == "0.8.0"
    assert project["description"] == "A local-first learning and memory layer for AI agents."
    assert project["scripts"]["study-migrate"] == "cognivault.migration.cli:main"


def test_project_declares_apache_license_and_packages_license_text() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert (ROOT / "LICENSE").is_file()


def test_pymupdf_is_only_in_the_non_default_pdf_ocr_extra() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    default_dependencies = [dependency.lower() for dependency in project["dependencies"]]
    pdf_ocr_dependencies = [
        dependency.lower()
        for dependency in project["optional-dependencies"]["pdf-ocr"]
    ]

    assert not any("pymupdf" in dependency for dependency in default_dependencies)
    assert any("pymupdf" in dependency for dependency in pdf_ocr_dependencies)
