"""Tests for Git command adaptation and worktree path handling."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from doc_gub.errors import DocGubError
from doc_gub.git import GitRepo


def test_git_repo_uses_staged_files_before_unstaged_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prefer staged paths and run subsequent commands at the detected root."""
    calls: list[tuple[str, ...]] = []

    def fake_run(
        *args: str, cwd: str | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Simula a execução de comandos git para testes.

        Args:
            args: Argumentos posicionais que representam os argumentos do comando git (ex:
            'ls-files', 'rev-parse').
            cwd: O diretório de trabalho atual onde o comando deve ser executado.
            check: Se deve levantar uma exceção se o comando retornar um código de erro diferente de
            zero.

        """
        calls.append(args)
        stdout = f"{tmp_path}\n" if args[0] == "rev-parse" else "staged.py\0"
        if args[0] == "ls-files":
            stdout = "staged.py\0untracked.py\0"
        return subprocess.CompletedProcess(["git", *args], 0, stdout=stdout, stderr="")

    monkeypatch.setattr(GitRepo, "_run", staticmethod(fake_run))
    repo = GitRepo(tmp_path)

    assert repo.changed_files() == ["staged.py", "untracked.py"]
    assert calls == [
        ("rev-parse", "--show-toplevel"),
        ("diff", "--cached", "--name-only", "-z"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ]


def test_git_repo_rejects_paths_outside_the_worktree(tmp_path: Path) -> None:
    """Keep explicit path requests within the Git working tree."""
    repo = GitRepo.__new__(GitRepo)
    repo.root = tmp_path
    outside = tmp_path.parent / "outside.py"
    outside.write_text("pass\n", encoding="utf-8")

    with pytest.raises(DocGubError, match="inside the Git worktree"):
        repo.relative_path(outside)
