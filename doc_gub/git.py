"""Small, safe Git interface used for path and ignore decisions."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import DocGubError


class GitRepo:
    def __init__(self, start: Path | None = None) -> None:
        result = self._run("rev-parse", "--show-toplevel", cwd=str(start or Path.cwd()))
        self.root = Path(result.stdout.strip()).resolve()

    @staticmethod
    def _run(*args: str, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)
        if check and result.returncode:
            raise DocGubError(f"Git command failed: {result.stderr.strip() or result.stdout.strip()}")
        return result

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self._run(*args, cwd=str(self.root))

    def relative_path(self, requested: Path) -> str:
        try:
            return requested.resolve(strict=True).relative_to(self.root).as_posix()
        except (OSError, ValueError) as exc:
            raise DocGubError("The path must exist inside the Git worktree.") from exc

    def changed_files(self) -> list[str]:
        staged = self.run("diff", "--cached", "--name-only", "-z").stdout.split("\0")
        return [name for name in staged if name] or [name for name in self.run("diff", "--name-only", "-z").stdout.split("\0") if name]
