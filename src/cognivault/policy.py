"""Study path containment, independent of the retrieval backend."""

from pathlib import Path

from .contracts import GatewayError


def safe_relative_source_path(root: Path, raw_path: str | Path) -> str:
    """Resolve a result source and return only a safe path under Study root."""

    if not isinstance(raw_path, (str, Path)):
        raise GatewayError("OUTSIDE_ALLOWLIST", "Study source is outside the allowed root")
    raw = str(raw_path)
    if not raw or any(ord(char) < 32 for char in raw):
        raise GatewayError("OUTSIDE_ALLOWLIST", "Study source is outside the allowed root")
    candidate = Path(raw)
    if ".." in candidate.parts or (candidate.drive and not candidate.is_absolute()):
        raise GatewayError("OUTSIDE_ALLOWLIST", "Study source is outside the allowed root")

    try:
        resolved_root = root.resolve(strict=True)
        resolved = (candidate if candidate.is_absolute() else resolved_root / candidate).resolve(strict=True)
        relative = resolved.relative_to(resolved_root)
        if not resolved.is_file():
            raise ValueError("Source is not a file")
    except (OSError, ValueError, RuntimeError):
        raise GatewayError("OUTSIDE_ALLOWLIST", "Study source is outside the allowed root") from None
    return relative.as_posix()
