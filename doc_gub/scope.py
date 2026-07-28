"""File selection, including Git-aware filtering and repository scanning."""
from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from .config import Settings
from .errors import DocGubError
from .git import GitRepo

SUPPORTED_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx"}
DEFAULT_EXCLUDED_PARTS = {".git", "node_modules", "dist", "build", ".venv", "venv", "__pycache__"}


def _eligible(repo: GitRepo, relative: str, settings: Settings) -> bool:
    path = repo.root / relative
    if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return False
    if any(part in DEFAULT_EXCLUDED_PARTS for part in path.parts):
        return False
    normalized = relative.replace("\\", "/")
    if any(fnmatch(normalized, pattern) or fnmatch("/" + normalized, pattern) for pattern in settings.exclude):
        return False
    return not settings.include or any(fnmatch(normalized, pattern) for pattern in settings.include)


def resolve(repo: GitRepo, requested: Path | None, settings: Settings) -> list[str]:
    if requested:
        relative = repo.relative_path(requested)
        source = repo.root / relative
        candidates = [relative] if source.is_file() else [item.relative_to(repo.root).as_posix() for item in source.rglob("*")]
    elif settings.selection == "changes":
        candidates = repo.changed_files()
    else:
        candidates = [item.relative_to(repo.root).as_posix() for item in repo.root.rglob("*")]
    files = sorted(item for item in candidates if _eligible(repo, item, settings))
    if not files:
        raise DocGubError("No eligible Python, JavaScript, or TypeScript files found.")
    if len(files) > settings.max_files_per_request:
        raise DocGubError("The scope exceeds max_files_per_request; narrow the path or increase the limit.")
    return files
