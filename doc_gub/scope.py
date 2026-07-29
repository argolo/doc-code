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
    """Verifica se um caminho relativo é elegível para inclusão, considerando extensões suportadas, exclusões padrão, regras personalizadas de exclusão/inclusão e a estrutura do repositório.
    
    Args:
        repo: Description of repo.
        relative: Description of relative.
        settings: Description of settings."""
    path = repo.root / relative
    if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return False
    if any(part in DEFAULT_EXCLUDED_PARTS for part in path.parts):
        return False
    normalized = relative.replace("\\", "/")
    if any(fnmatch(normalized, pattern) or fnmatch("/" + normalized, pattern) for pattern in settings.exclude):
        return False
    return not settings.include or any(fnmatch(normalized, pattern) for pattern in settings.include)


def resolve(repo: GitRepo, requested: list[Path] | None, settings: Settings) -> list[str]:
    """Resolve paths, Git changes, or the repository into one deduplicated file scope.
    
    Args:
        repo: Description of repo.
        requested: Description of requested.
        settings: Description of settings."""
    if requested:
        candidates: list[str] = []
        for requested_path in requested:
            relative = repo.relative_path(requested_path)
            source = repo.root / relative
            if source.is_file():
                candidates.append(relative)
            else:
                candidates.extend(item.relative_to(repo.root).as_posix() for item in source.rglob("*"))
    elif settings.selection == "changes":
        candidates = repo.changed_files()
    else:
        candidates = [item.relative_to(repo.root).as_posix() for item in repo.root.rglob("*")]
    files = sorted({item for item in candidates if _eligible(repo, item, settings)})
    if not files:
        raise DocGubError("No eligible Python, JavaScript, or TypeScript files found.")
    if len(files) > settings.max_files_per_request:
        raise DocGubError("The scope exceeds max_files_per_request; narrow the path or increase the limit.")
    return files
