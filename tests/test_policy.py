from pathlib import Path

import pytest

from cognivault.contracts import GatewayError
from cognivault.policy import safe_relative_source_path


def test_returns_slash_relative_path_for_file_inside_study_root(tmp_path: Path) -> None:
    root = tmp_path / "study"
    source = root / "数学" / "synthetic.md"
    source.parent.mkdir(parents=True)
    source.write_text("Invented lesson", encoding="utf-8")

    assert safe_relative_source_path(root, source) == "数学/synthetic.md"
    assert safe_relative_source_path(root, "数学/synthetic.md") == "数学/synthetic.md"


@pytest.mark.parametrize("raw", ["../outside.md", "数学/../synthetic.md", "..\\outside.md", ""])
def test_rejects_traversal_or_empty_path(tmp_path: Path, raw: str) -> None:
    root = tmp_path / "study"
    root.mkdir()

    with pytest.raises(GatewayError, match="OUTSIDE_ALLOWLIST"):
        safe_relative_source_path(root, raw)


def test_rejects_absolute_path_outside_study_root(tmp_path: Path) -> None:
    root = tmp_path / "study"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("Invented outside content", encoding="utf-8")

    with pytest.raises(GatewayError, match="OUTSIDE_ALLOWLIST"):
        safe_relative_source_path(root, outside)


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "study"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("Invented outside content", encoding="utf-8")
    link = root / "link.md"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("This host does not permit creating symlinks")

    with pytest.raises(GatewayError, match="OUTSIDE_ALLOWLIST"):
        safe_relative_source_path(root, link)


def test_rejects_resolved_symlink_or_junction_escape_without_host_privileges(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "study"
    root.mkdir()
    raw_link = root / "linked.md"
    outside = tmp_path / "outside.md"
    outside.write_text("Synthetic external note", encoding="utf-8")
    real_resolve = Path.resolve

    def resolve(path: Path, *args, **kwargs) -> Path:
        if path == raw_link:
            return outside
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(GatewayError, match="OUTSIDE_ALLOWLIST"):
        safe_relative_source_path(root, raw_link)
