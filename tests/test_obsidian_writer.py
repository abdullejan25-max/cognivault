from __future__ import annotations

import json
from pathlib import Path

import pytest

import cognivault.obsidian_writer as writer_module
from cognivault.obsidian_writer import ProjectionWriteError, write_projection


def _read_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_first_write_and_rebuild_are_byte_identical(tmp_path: Path) -> None:
    root = tmp_path / "V2Projection"
    files = {
        "Dashboard.md": "# Synthetic dashboard\n",
        "History/index.md": "# Synthetic history\n",
    }

    write_projection(files, root)
    first = _read_tree(root)
    write_projection(files, root)

    assert _read_tree(root) == first
    manifest = json.loads(first[".projection-manifest.json"])
    assert manifest == {"files": sorted(files), "schema_version": 1}


def test_rebuild_removes_only_stale_owned_files_and_preserves_unknown_files(tmp_path: Path) -> None:
    root = tmp_path / "V2Projection"
    write_projection({"old.md": "generated old\n", "keep.md": "generated keep\n"}, root)
    (root / "notes.txt").write_text("user file\n", encoding="utf-8")

    write_projection({"keep.md": "generated keep v2\n"}, root)

    assert not (root / "old.md").exists()
    assert (root / "keep.md").read_text(encoding="utf-8") == "generated keep v2\n"
    assert (root / "notes.txt").read_text(encoding="utf-8") == "user file\n"


@pytest.mark.parametrize("path", ["../outside.md", "/absolute.md", "C:/drive.md", "a\\b.md", ".projection-manifest.json"])
def test_unsafe_or_reserved_output_paths_fail_before_creating_directory(tmp_path: Path, path: str) -> None:
    root = tmp_path / "V2Projection"

    with pytest.raises(ProjectionWriteError, match="Invalid projection output"):
        write_projection({path: "synthetic\n"}, root)

    assert not root.exists()


def test_projection_inside_repository_is_rejected_before_writing(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    root = repository / "V2Projection"
    monkeypatch.setattr(writer_module, "_repository_root", lambda: repository, raising=False)

    with pytest.raises(ProjectionWriteError, match="Invalid projection output"):
        write_projection({"History/index.md": "synthetic private history\n"}, root)

    assert not root.exists()


def test_repository_root_discovery_uses_active_checkout(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "active-checkout"
    (repository / ".git").mkdir(parents=True)
    monkeypatch.chdir(repository)

    assert writer_module._repository_root() == repository.resolve()


def test_nonempty_directory_requires_an_existing_valid_manifest(tmp_path: Path) -> None:
    root = tmp_path / "V2Projection"
    root.mkdir()
    (root / "notes.md").write_text("user content\n", encoding="utf-8")

    with pytest.raises(ProjectionWriteError, match="Invalid projection output"):
        write_projection({"Dashboard.md": "synthetic\n"}, root)

    (root / ".projection-manifest.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ProjectionWriteError, match="Invalid projection output"):
        write_projection({"Dashboard.md": "synthetic\n"}, root)
    assert (root / "notes.md").read_text(encoding="utf-8") == "user content\n"


def test_symlinked_output_file_is_rejected_without_following_it(tmp_path: Path) -> None:
    root = tmp_path / "V2Projection"
    outside = tmp_path / "outside.md"
    outside.write_text("untouched\n", encoding="utf-8")
    write_projection({"Dashboard.md": "initial\n"}, root)
    (root / "Dashboard.md").unlink()
    try:
        (root / "Dashboard.md").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available")

    with pytest.raises(ProjectionWriteError, match="Invalid projection output"):
        write_projection({"Dashboard.md": "replacement\n"}, root)
    assert outside.read_text(encoding="utf-8") == "untouched\n"


def test_windows_reparse_point_ancestor_is_rejected(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "V2Projection"
    root.mkdir()
    reparse_path = root
    original_check = writer_module._is_reparse_point
    monkeypatch.setattr(
        writer_module, "_is_reparse_point",
        lambda path: Path(path) == reparse_path or original_check(Path(path)),
    )

    with pytest.raises(ProjectionWriteError, match="Invalid projection output"):
        write_projection({"Dashboard.md": "synthetic\n"}, root)

    assert list(root.iterdir()) == []


def test_failed_first_write_cleans_new_files_and_directories(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "V2Projection"

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated failure")

    monkeypatch.setattr(writer_module, "_replace_file", fail_replace)
    with pytest.raises(ProjectionWriteError, match="Invalid projection output"):
        write_projection({"History/index.md": "synthetic\n"}, root)

    assert not root.exists()


def test_manifest_is_published_last_and_old_manifest_survives_failure(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "V2Projection"
    old_files = {"Dashboard.md": "old content\n", "Legacy/old.md": "retired\n"}
    write_projection(old_files, root)
    old_tree = _read_tree(root)
    original_replace = writer_module._replace_file

    def fail_manifest(source: Path, destination: Path) -> None:
        if destination.name == ".projection-manifest.json":
            raise OSError("simulated failure")
        original_replace(source, destination)

    monkeypatch.setattr(writer_module, "_replace_file", fail_manifest)
    with pytest.raises(ProjectionWriteError, match="Invalid projection output"):
        write_projection({"Dashboard.md": "new content\n", "History/index.md": "new\n"}, root)

    assert _read_tree(root) == old_tree

    monkeypatch.setattr(writer_module, "_replace_file", original_replace)
    write_projection({"Dashboard.md": "new content\n", "History/index.md": "new\n"}, root)
    assert _read_tree(root)[".projection-manifest.json"] != old_tree[".projection-manifest.json"]


def test_failed_rebuild_removes_file_missing_from_prior_manifest(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "V2Projection"
    write_projection({"Dashboard.md": "old content\n"}, root)
    (root / "Dashboard.md").unlink()
    previous_tree = _read_tree(root)
    original_replace = writer_module._replace_file

    def fail_manifest(source: Path, destination: Path) -> None:
        if destination.name == ".projection-manifest.json":
            raise OSError("simulated manifest publication failure")
        original_replace(source, destination)

    monkeypatch.setattr(writer_module, "_replace_file", fail_manifest)

    with pytest.raises(ProjectionWriteError, match="Invalid projection output"):
        write_projection({"Dashboard.md": "new content\n"}, root)

    assert _read_tree(root) == previous_tree


def test_failed_rollback_retains_backup_and_marks_recovery_required(
        tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "V2Projection"
    write_projection({"Dashboard.md": "old content\n", "History/old.md": "old history\n"}, root)
    original_replace_file = writer_module._replace_file
    original_os_replace = writer_module.os.replace

    def fail_manifest(source: Path, destination: Path) -> None:
        if destination.name == ".projection-manifest.json":
            raise OSError("simulated manifest publication failure")
        original_replace_file(source, destination)

    def fail_dashboard_restore(source, destination) -> None:
        if source.name.startswith(".Dashboard.md.") and Path(destination).name == "Dashboard.md":
            raise OSError("simulated rollback failure")
        original_os_replace(source, destination)

    monkeypatch.setattr(writer_module, "_replace_file", fail_manifest)
    monkeypatch.setattr(writer_module.os, "replace", fail_dashboard_restore)

    with pytest.raises(ProjectionWriteError, match="Invalid projection output") as error:
        write_projection({"Dashboard.md": "new content\n", "History/old.md": "new history\n"}, root)

    assert error.value.recovery_required is True
    retained = list(tmp_path.glob(".v2projection-rollback-*"))
    assert len(retained) == 1
    recovery = json.loads((retained[0] / "recovery.json").read_bytes())
    assert recovery["files"] == {"owned-0": "Dashboard.md", "owned-1": "History/old.md"}
    assert recovery["manifest"] == "manifest"
    assert (retained[0] / "owned-0").read_bytes() == b"old content\n"
    assert (retained[0] / "owned-1").read_bytes() == b"old history\n"


def test_backup_setup_failure_removes_new_projection_directory(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "V2Projection"

    def fail_backup_directory(*args, **kwargs):
        raise OSError("simulated backup setup failure")

    monkeypatch.setattr(writer_module.tempfile, "mkdtemp", fail_backup_directory)

    with pytest.raises(ProjectionWriteError, match="Invalid projection output"):
        write_projection({"Dashboard.md": "synthetic\n"}, root)

    assert not root.exists()


def test_backup_cleanup_failure_is_reported_as_cleanup_not_recovery(
    tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "V2Projection"
    write_projection({"Dashboard.md": "old content\n"}, root)
    original_unlink = Path.unlink

    def fail_backup_unlink(path: Path, missing_ok: bool = False) -> None:
        if path.name == "owned-0":
            raise OSError("simulated backup cleanup failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_backup_unlink)

    with pytest.raises(ProjectionWriteError, match="Invalid projection output") as error:
        write_projection({"Dashboard.md": "new content\n"}, root)

    assert error.value.cleanup_required is True
    assert error.value.recovery_required is False
    assert (root / "Dashboard.md").read_text(encoding="utf-8") == "new content\n"
    retained = list(tmp_path.glob(".v2projection-rollback-*"))
    assert len(retained) == 1
    assert json.loads((retained[0] / "recovery.json").read_bytes())["action"] == "cleanup_only"


def test_failed_empty_root_cleanup_is_reported(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "V2Projection"

    def fail_backup_directory(*args, **kwargs):
        raise OSError("simulated backup setup failure")

    original_rmdir = Path.rmdir

    def fail_root_rmdir(path: Path) -> None:
        if path == root:
            raise OSError("simulated root cleanup failure")
        original_rmdir(path)

    monkeypatch.setattr(writer_module.tempfile, "mkdtemp", fail_backup_directory)
    monkeypatch.setattr(Path, "rmdir", fail_root_rmdir)

    with pytest.raises(ProjectionWriteError, match="Invalid projection output") as error:
        write_projection({"Dashboard.md": "synthetic\n"}, root)

    assert error.value.cleanup_required is True
    assert root.is_dir()


def test_backup_permission_setup_failure_cleans_staging_directory(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "V2Projection"
    original_mkdtemp = writer_module.tempfile.mkdtemp
    staging_dirs = []

    def track_mkdtemp(*args, **kwargs):
        path = original_mkdtemp(*args, **kwargs)
        staging_dirs.append(Path(path))
        return path

    def fail_private_mode_setup(path: Path) -> None:
        raise OSError("simulated private-mode setup failure")

    monkeypatch.setattr(writer_module.tempfile, "mkdtemp", track_mkdtemp)
    monkeypatch.setattr(writer_module, "_set_private_directory_mode", fail_private_mode_setup,
                        raising=False)

    with pytest.raises(ProjectionWriteError, match="Invalid projection output") as error:
        write_projection({"Dashboard.md": "synthetic\n"}, root)

    assert error.value.cleanup_required is False
    assert not root.exists()
    assert len(staging_dirs) == 1
    assert not staging_dirs[0].exists()
