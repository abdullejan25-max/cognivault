from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cognivault_distribution_preserves_release_and_domain_cli() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == "cognivault"
    assert project["version"] == "0.7.0"
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
