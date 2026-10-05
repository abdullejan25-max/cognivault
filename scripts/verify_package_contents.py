"""Check built archives for required package files and private checkout data."""

from pathlib import Path, PurePosixPath
import re
import tarfile
import zipfile


def archive_names(path: Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    with tarfile.open(path, "r:gz") as archive:
        return archive.getnames()


def main() -> None:
    archives = sorted(Path("dist").glob("cognivault-0.7.0*"))
    if {path.suffix for path in archives} != {".whl", ".gz"} or len(archives) != 2:
        raise SystemExit("expected one CogniVault wheel and one source archive")
    forbidden_parts = {".hermes", ".workbuddy", ".superpowers", "StudyVault", "History", "var"}
    forbidden_names = {"config.local.toml", "config.chatgpt-readonly.local.toml", ".env"}
    forbidden_paths = {(".codex", "config.toml")}
    for path in archives:
        names = archive_names(path)
        for name in names:
            normalized = name.replace("\\", "/")
            parts = PurePosixPath(normalized).parts
            if PurePosixPath(normalized).is_absolute() or re.match(r"^[A-Za-z]:", normalized):
                raise SystemExit(f"absolute path in package: {path.name}")
            has_forbidden_path = any(
                tuple(parts[index:index + len(path)]) == path
                for path in forbidden_paths
                for index in range(len(parts) - len(path) + 1)
            )
            if forbidden_names.intersection(parts) or forbidden_parts.intersection(parts) or has_forbidden_path:
                raise SystemExit(f"private checkout file in package: {path.name}")
        if path.suffix == ".whl":
            required = {"cognivault/__init__.py", "chatgpt_study_system/transports/mcp_stdio.py"}
            if not required.issubset(names) or not any(name.endswith(".dist-info/METADATA") for name in names):
                raise SystemExit("wheel is missing canonical package, compatibility shim, or metadata")
        elif not any(name.endswith("/src/cognivault/__init__.py") for name in names):
            raise SystemExit("source archive is missing the canonical package")
        print(f"{path.name}: {len(names)} entries, privacy audit passed")


if __name__ == "__main__":
    main()
