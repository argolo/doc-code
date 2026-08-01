"""Small, safe Git interface used for path and ignore decisions."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

from .errors import DocGubError


class GitRepo:
    """Provide safe Git operations rooted in one worktree."""

    def __init__(self, start: Path | None = None) -> None:
        """Find the worktree root from ``start`` or the current directory."""
        result = self._run("rev-parse", "--show-toplevel", cwd=str(start or Path.cwd()))
        self.root = Path(result.stdout.strip()).resolve()

    @staticmethod
    def _run(
        *args: str, cwd: str | None = None, check: bool = True, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Execute Git and convert expected process failures to domain errors."""
        executable = shutil.which("git")
        if not executable:
            raise DocGubError("Git is required but was not found on PATH.")
        try:
            result = subprocess.run(
                [executable, *args],
                cwd=cwd,
                input=input_text,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise DocGubError(f"Unable to run Git: {exc}") from exc
        if check and result.returncode:
            raise DocGubError(
                f"Git command failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    def run(
        self, *args: str, check: bool = True, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Execute Git inside this worktree."""
        return self._run(*args, cwd=str(self.root), check=check, input_text=input_text)

    def relative_path(self, requested: Path) -> str:
        """Return a resolved worktree-relative path or reject the request."""
        try:
            return requested.resolve(strict=True).relative_to(self.root).as_posix()
        except (OSError, ValueError) as exc:
            raise DocGubError("The path must exist inside the Git worktree.") from exc

    def changed_files(self) -> list[str]:
        """Return every staged, unstaged, and untracked non-ignored file once."""
        staged = self.run("diff", "--cached", "--name-only", "-z").stdout.split("\0")
        unstaged = self.run("diff", "--name-only", "-z").stdout.split("\0")
        untracked = self.run("ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0")
        return list(dict.fromkeys(name for name in [*staged, *unstaged, *untracked] if name))

    def ignored_paths(self, paths: Iterable[str]) -> set[str]:
        """Return paths ignored by the repository's Git exclude rules."""
        candidates = tuple(paths)
        if not candidates:
            return set()
        result = self.run(
            "check-ignore", "-z", "--stdin", check=False, input_text="\0".join(candidates) + "\0"
        )
        return {path for path in result.stdout.split("\0") if path}
