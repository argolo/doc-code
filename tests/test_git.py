"""Test git behavior."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from doc_code import git as git_module
from doc_code.errors import DocGubError
from doc_code.git import GitRepo


def test_git_missing_from_path_is_reported_as_a_domain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify git missing from path is reported as a domain error."""
    monkeypatch.setattr(git_module.shutil, "which", lambda _name: None)

    with pytest.raises(DocGubError, match="Git is required"):
        GitRepo._run("status")


def test_git_command_failure_is_reported_as_a_domain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify git command failure is reported as a domain error."""

    def failed_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        """Failed run."""
        return subprocess.CompletedProcess(
            ["git", "status"], returncode=128, stdout="", stderr="fatal: not a repository"
        )

    monkeypatch.setattr(subprocess, "run", failed_run)

    with pytest.raises(DocGubError, match="fatal: not a repository"):
        GitRepo._run("status")


def test_git_repo_combines_staged_unstaged_and_untracked_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Git change selection does not omit unstaged files after staging."""
    calls: list[tuple[str, ...]] = []

    def fake_run(
        *args: str, cwd: str | None = None, check: bool = True, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Fake run."""
        del cwd, check, input_text
        calls.append(args)
        stdout = f"{tmp_path}\n" if args[0] == "rev-parse" else ""
        if args == ("diff", "--cached", "--name-only", "-z"):
            stdout = "staged.py\0shared.py\0"
        if args == ("diff", "--name-only", "-z"):
            stdout = "unstaged.py\0shared.py\0"
        if args[0] == "ls-files":
            stdout = "untracked.py\0shared.py\0"
        return subprocess.CompletedProcess(["git", *args], 0, stdout=stdout, stderr="")

    monkeypatch.setattr(GitRepo, "_run", staticmethod(fake_run))
    repo = GitRepo(tmp_path)

    assert repo.changed_files() == ["staged.py", "shared.py", "unstaged.py", "untracked.py"]
    assert calls == [
        ("rev-parse", "--show-toplevel"),
        ("diff", "--cached", "--name-only", "-z"),
        ("diff", "--name-only", "-z"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ]


def test_git_repo_rejects_paths_outside_the_worktree(tmp_path: Path) -> None:
    """Verify git repo rejects paths outside the worktree."""
    repo = GitRepo.__new__(GitRepo)
    repo.root = tmp_path
    outside = tmp_path.parent / "outside.py"
    outside.write_text("pass\n", encoding="utf-8")

    with pytest.raises(DocGubError, match="inside the Git worktree"):
        repo.relative_path(outside)


def test_git_repo_returns_paths_ignored_by_git_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify ignored path detection delegates to Git with NUL-safe input."""
    calls: list[tuple[tuple[str, ...], str | None]] = []

    def fake_run(
        *args: str,
        cwd: str | None = None,
        check: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Return one ignored path from a fake Git command."""
        del cwd, check
        calls.append((args, input_text))
        stdout = f"{tmp_path}\n" if args[0] == "rev-parse" else "generated.py\0"
        return subprocess.CompletedProcess(["git", *args], 0, stdout=stdout, stderr="")

    monkeypatch.setattr(GitRepo, "_run", staticmethod(fake_run))
    repo = GitRepo(tmp_path)

    assert repo.ignored_paths(["generated.py", "source.py"]) == {"generated.py"}
    assert calls[-1] == (("check-ignore", "-z", "--stdin"), "generated.py\0source.py\0")
