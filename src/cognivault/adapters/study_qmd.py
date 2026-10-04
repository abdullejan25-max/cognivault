"""QMD 2.8.3 BM25 search over the fixed Study collection."""

import json
import math
import os
from contextlib import nullcontext
from pathlib import Path
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, ContextManager
from urllib.parse import unquote, urlsplit

from ..config import AppConfig
from ..contracts import BackendStudyHit, GatewayError
from ..policy import safe_relative_source_path


_APPROVED_VERSION = "2.8.3"
_COLLECTION = "studyvault"
_ENV_KEYS = frozenset({
    "PATH", "SYSTEMROOT", "WINDIR", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME",
    "XDG_CONFIG_HOME", "TEMP", "TMP",
})


@dataclass(frozen=True)
class QmdRuntime:
    """Fixed native QMD process and disposable runtime paths."""

    runtime_root: Path
    node_executable: Path
    cli_entrypoint: Path
    package_json: Path
    cwd: Path
    index_path: Path
    config_dir: Path
    cache_dir: Path
    home_dir: Path
    userprofile_dir: Path

    def command_and_environment(self, study_root: Path) -> tuple[list[str], dict[str, str]]:
        root = _resolve_directory(self.runtime_root)
        study = _resolve_directory(study_root)
        if _within(root, study) or _within(study, root):
            raise GatewayError("QMD_RUNTIME_UNSAFE", "Study runtime is unsafe")

        node = _resolve_file(self.node_executable)
        cli = _resolve_file(self.cli_entrypoint)
        package = _resolve_file(self.package_json)
        if node.name.casefold() != "node.exe" or node.suffix.casefold() in {".cmd", ".bat", ".ps1"}:
            raise GatewayError("QMD_RUNTIME_UNSAFE", "Study runtime is unsafe")
        if cli.suffix.casefold() != ".js" or not _within(cli, root) and not cli.exists():
            raise GatewayError("QMD_RUNTIME_UNSAFE", "Study runtime is unsafe")
        package_root = package.parent
        if cli != (package_root / "dist" / "cli" / "qmd.js"):
            raise GatewayError("QMD_RUNTIME_UNSAFE", "Study runtime is unsafe")
        try:
            metadata = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            raise GatewayError("QMD_RUNTIME_UNSAFE", "Study runtime is unsafe") from None
        if metadata.get("name") != "@tobilu/qmd" or metadata.get("version") != _APPROVED_VERSION:
            raise GatewayError("QMD_VERSION_UNSUPPORTED", "Study search version is unsupported")

        paths = (self.cwd, self.index_path, self.config_dir, self.cache_dir, self.home_dir, self.userprofile_dir)
        resolved = tuple(_resolve_path(path) for path in paths)
        if any(not _within(path, root) for path in resolved):
            raise GatewayError("QMD_RUNTIME_UNSAFE", "Study runtime is unsafe")
        if not resolved[0].is_dir():
            raise GatewayError("QMD_RUNTIME_UNSAFE", "Study runtime is unsafe")
        system_root = os.environ.get("SYSTEMROOT")
        if os.name == "nt" and (not system_root or not Path(system_root).is_dir()):
            raise GatewayError("QMD_RUNTIME_UNSAFE", "Study runtime is unsafe")
        env = {
            "PATH": str(node.parent),
            "INDEX_PATH": str(resolved[1]),
            "QMD_CONFIG_DIR": str(resolved[2]),
            "XDG_CACHE_HOME": str(resolved[3]),
            "HOME": str(resolved[4]),
            "USERPROFILE": str(resolved[5]),
        }
        if system_root:
            # Windows native Node needs the OS bootstrap root even when all QMD
            # state paths are contained by the disposable runtime.
            env["SYSTEMROOT"] = system_root
        return [str(node), str(cli)], env


def _resolve_path(path: Path) -> Path:
    try:
        return Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        raise GatewayError("QMD_RUNTIME_UNSAFE", "Study runtime is unsafe") from None


def _resolve_directory(path: Path) -> Path:
    resolved = _resolve_path(path)
    if not resolved.is_dir():
        raise GatewayError("QMD_RUNTIME_UNSAFE", "Study runtime is unsafe")
    return resolved


def _resolve_file(path: Path) -> Path:
    resolved = _resolve_path(path)
    if not resolved.is_file():
        raise GatewayError("QMD_RUNTIME_UNSAFE", "Study runtime is unsafe")
    return resolved


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


class QmdStudyBackend:
    """Run one fixed search and validate sources.

    ``approved_qmd_version`` is a local configuration assertion, not binary
    verification. Real-data use still needs an audited launcher/version binding
    and a review of QMD index side effects.
    """

    name = "qmd_bm25"

    def __init__(
        self,
        config: AppConfig,
        *,
        qmd_executable: str = "qmd",
        approved_qmd_version: str | None = None,
        runtime: QmdRuntime | None = None,
        runtime_provider: Callable[[], ContextManager[QmdRuntime]] | None = None,
        runner: Callable = subprocess.run,
        resolver: Callable[[str], str | None] = shutil.which,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.config = config
        self.qmd_executable = qmd_executable
        self.approved_qmd_version = approved_qmd_version
        self.runtime = runtime
        self.runtime_provider = runtime_provider
        self.runner = runner
        self.resolver = resolver
        self.timeout_seconds = timeout_seconds

    def search(self, query: str, limit: int) -> list[BackendStudyHit]:
        if (self.config.expected_qmd_version != _APPROVED_VERSION
                or (self.runtime is None and self.runtime_provider is None
                    and self.approved_qmd_version != _APPROVED_VERSION)):
            raise GatewayError("QMD_VERSION_UNSUPPORTED", "Study search version is unsupported")
        if self.config.collection != _COLLECTION:
            raise GatewayError("STUDY_UNAVAILABLE", "Study search is unavailable")
        root = self.config.study_root
        if root is None or not root.is_dir():
            raise GatewayError("STUDY_UNAVAILABLE", "Study search is unavailable")
        if self.runtime is not None or self.runtime_provider is not None:
            runtime_context = (
                nullcontext(self.runtime)
                if self.runtime is not None
                else self.runtime_provider()
            )
            try:
                with runtime_context as active_runtime:
                    executable_argv, environment = active_runtime.command_and_environment(root)
                    executable = executable_argv[0]
                    cwd = str(active_runtime.cwd)
                    argv = executable_argv + ["search", "--format", "json", "--collection", _COLLECTION, "-n", str(limit), "--", query]
                    completed = self.runner(
                        argv,
                        shell=False,
                        timeout=self.timeout_seconds,
                        cwd=cwd,
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="strict",
                    )
            except subprocess.TimeoutExpired:
                raise GatewayError("BACKEND_TIMEOUT", "Study search timed out") from None
            except OSError:
                raise GatewayError("QMD_NOT_FOUND", "Study search executable is unavailable") from None
        else:
            try:
                cwd = str(root.resolve(strict=True))
            except (OSError, RuntimeError):
                raise GatewayError("STUDY_UNAVAILABLE", "Study search is unavailable") from None

            try:
                executable = self.resolver(self.qmd_executable)
            except OSError:
                executable = None
            if (not executable or not Path(executable).is_absolute()
                    or Path(executable).suffix.lower() in {".cmd", ".bat", ".ps1"}):
                raise GatewayError("QMD_NOT_FOUND", "Study search executable is unavailable")

            argv = [executable, "search", "--format", "json", "--collection", _COLLECTION, "-n", str(limit), "--", query]
            environment = {key: value for key, value in os.environ.items() if key.upper() in _ENV_KEYS}
            try:
                completed = self.runner(
                argv,
                shell=False,
                timeout=self.timeout_seconds,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                )
            except subprocess.TimeoutExpired:
                raise GatewayError("BACKEND_TIMEOUT", "Study search timed out") from None
            except OSError:
                raise GatewayError("QMD_NOT_FOUND", "Study search executable is unavailable") from None

        if completed.returncode != 0:
            raise GatewayError("STUDY_UNAVAILABLE", "Study search is unavailable")
        try:
            records = json.loads(completed.stdout, parse_constant=_reject_nonfinite)
        except (TypeError, ValueError):
            raise GatewayError("BACKEND_BAD_OUTPUT", "Study backend returned invalid results") from None
        if not isinstance(records, list) or len(records) > limit:
            raise GatewayError("BACKEND_BAD_OUTPUT", "Study backend returned invalid results")
        return [self._parse_hit(root, record) for record in records]

    @staticmethod
    def _parse_hit(root: Path, record: object) -> BackendStudyHit:
        if not isinstance(record, dict):
            raise GatewayError("BACKEND_BAD_OUTPUT", "Study backend returned invalid results")
        uri, title, snippet = (record.get(key) for key in ("file", "title", "snippet"))
        if snippet is None:
            snippet = ""
        if not isinstance(uri, str) or not isinstance(title, str) or not isinstance(snippet, str):
            raise GatewayError("BACKEND_BAD_OUTPUT", "Study backend returned invalid results")
        score = record.get("score")
        if score is not None:
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise GatewayError("BACKEND_BAD_OUTPUT", "Study backend returned invalid results")
            try:
                score = float(score)
            except (OverflowError, TypeError, ValueError):
                raise GatewayError("BACKEND_BAD_OUTPUT", "Study backend returned invalid results") from None
            if not math.isfinite(score):
                raise GatewayError("BACKEND_BAD_OUTPUT", "Study backend returned invalid results")
        docid = record.get("docid")
        if docid is not None and (not isinstance(docid, str) or not docid):
            raise GatewayError("BACKEND_BAD_OUTPUT", "Study backend returned invalid results")
        source_path = _safe_uri_path(root, uri)
        return BackendStudyHit(title, snippet, f"study:{source_path}", source_path, score)


def _reject_nonfinite(_: str) -> None:
    raise ValueError("Nonfinite JSON value")


def _safe_uri_path(root: Path, uri: str) -> str:
    try:
        parsed = urlsplit(uri)
        if (parsed.scheme != "qmd" or parsed.netloc != _COLLECTION or parsed.query or parsed.fragment
                or not parsed.path.startswith("/")):
            raise ValueError("Invalid Study URI")
        path = unquote(parsed.path[1:], encoding="utf-8", errors="strict")
        parts = path.split("/")
        if (not path or path.startswith("/") or "\\" in path or ":" in path
                or any(part in ("", ".", "..") for part in parts)
                or any(ord(char) < 32 or ord(char) == 127 for char in path)):
            raise ValueError("Invalid Study URI path")
        return safe_relative_source_path(root, path)
    except (UnicodeError, ValueError, GatewayError):
        raise GatewayError("OUTSIDE_ALLOWLIST", "Study source is outside the allowed root") from None
