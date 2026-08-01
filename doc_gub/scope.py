"""File selection, including Git-aware filtering and repository scanning."""

from __future__ import annotations

import os
from collections.abc import Iterator
from fnmatch import fnmatch
from pathlib import Path

from .config import Settings
from .errors import DocGubError
from .git import GitRepo

SUPPORTED_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx"}
DEFAULT_EXCLUDED_PARTS = {".git", "node_modules", "dist", "build", ".venv", "venv", "__pycache__"}


def _matches(relative: str, patterns: tuple[str, ...]) -> bool:
    """Match root and nested paths consistently for include and exclude patterns."""
    normalized = relative.replace("\\", "/")
    return any(
        fnmatch(candidate, pattern)
        for pattern in patterns
        for candidate in (normalized, f"/{normalized}")
    )


def _walk_candidates(root: Path, source: Path) -> Iterator[str]:
    """Yield files while pruning excluded and symbolic-link directories early."""
    for directory, subdirectories, filenames in os.walk(source, followlinks=False):
        current = Path(directory)
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if name not in DEFAULT_EXCLUDED_PARTS and not (current / name).is_symlink()
        )
        for filename in sorted(filenames):
            yield (current / filename).relative_to(root).as_posix()


def _is_oversized(repo: GitRepo, relative: str, settings: Settings) -> bool:
    """Return whether an existing supported file exceeds the byte limit."""
    path = repo.root / relative
    try:
        return (
            path.is_file()
            and path.suffix.lower() in SUPPORTED_SUFFIXES
            and path.stat().st_size > settings.max_file_bytes
        )
    except OSError:
        return False


def _eligible(repo: GitRepo, relative: str, settings: Settings) -> bool:
    """Return whether a worktree-relative path satisfies every scope rule."""
    path = repo.root / relative
    if path.is_symlink() or not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return False
    try:
        path.resolve(strict=True).relative_to(repo.root.resolve(strict=True))
    except (OSError, ValueError):
        return False
    if _is_oversized(repo, relative, settings):
        return False
    if any(part in DEFAULT_EXCLUDED_PARTS for part in Path(relative).parts):
        return False
    if _matches(relative, settings.exclude):
        return False
    return not settings.include or _matches(relative, settings.include)


def resolve(repo: GitRepo, requested: list[Path] | None, settings: Settings) -> list[str]:
    """Resolve paths, Git changes, or a repository into a deduplicated file scope."""
    if requested:
        candidates: list[str] = []
        for requested_path in requested:
            relative = repo.relative_path(requested_path)
            source = repo.root / relative
            if source.is_file():
                candidates.append(relative)
            else:
                candidates.extend(_walk_candidates(repo.root, source))
    elif settings.selection == "changes":
        candidates = repo.changed_files()
    else:
        candidates = list(_walk_candidates(repo.root, repo.root))
    unique_candidates = set(candidates)
    files = sorted(item for item in unique_candidates if _eligible(repo, item, settings))
    if not files:
        oversized = sorted(
            item for item in unique_candidates if _is_oversized(repo, item, settings)
        )
        if oversized:
            raise DocGubError(f"No eligible files: exceeds max_file_bytes: {', '.join(oversized)}.")
        raise DocGubError("No eligible Python, JavaScript, or TypeScript files found.")
    if len(files) > settings.max_files_per_request:
        raise DocGubError(
            "The scope exceeds max_files_per_request; narrow the path or increase the limit."
        )
    return files
