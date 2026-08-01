"""Test scope behavior."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest

from doc_code import scope
from doc_code.config import Settings, load
from doc_code.errors import DocGubError
from doc_code.git import GitRepo
from doc_code.scope import resolve


class ScopeRepo(GitRepo):
    """Provide a Git repository double with no ignored paths by default."""

    def ignored_paths(self, _paths: Iterable[str]) -> set[str]:
        """Return no ignored paths for isolated scope tests."""
        return set()


def test_resolve_combines_multiple_files_and_directories_without_duplicates(tmp_path: Path) -> None:
    """Verify resolve combines multiple files and directories without duplicates."""
    first = tmp_path / "first.py"
    directory = tmp_path / "src"
    second = directory / "second.py"
    directory.mkdir()
    first.write_text("pass\n", encoding="utf-8")
    second.write_text("pass\n", encoding="utf-8")

    class Repo(ScopeRepo):
        """Provide a repository test double."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = tmp_path

        def relative_path(self, requested: Path) -> str:
            """Relative path."""
            return requested.resolve().relative_to(self.root).as_posix()

    assert resolve(Repo(), [first, directory, first], load(tmp_path, selection="repository")) == [
        "first.py",
        "src/second.py",
    ]


def test_resolve_rejects_oversized_files_before_they_are_read(tmp_path: Path) -> None:
    """Verify resolve rejects oversized files before they are read."""
    source = tmp_path / "large.py"
    source.write_text("x" * 20, encoding="utf-8")

    class Repo(ScopeRepo):
        """Provide a repository test double."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = tmp_path

        def relative_path(self, requested: Path) -> str:
            """Relative path."""
            return requested.resolve().relative_to(self.root).as_posix()

    with pytest.raises(DocGubError, match="exceeds max_file_bytes"):
        resolve(Repo(), [source], load(tmp_path, max_file_bytes=10))


def test_default_exclusions_do_not_use_directories_above_the_repository(tmp_path: Path) -> None:
    """Verify default exclusions do not use directories above the repository."""
    root = tmp_path / "build" / "project"
    root.mkdir(parents=True)
    source = root / "module.py"
    source.write_text("pass\n", encoding="utf-8")

    class Repo(ScopeRepo):
        """Provide a repository test double."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = root

        def relative_path(self, requested: Path) -> str:
            """Relative path."""
            return requested.resolve().relative_to(self.root).as_posix()

    assert resolve(Repo(), [source], load(root)) == ["module.py"]


def test_resolve_handles_a_changed_file_removed_before_selection(tmp_path: Path) -> None:
    """Verify resolve handles a changed file removed before selection."""

    class Repo(ScopeRepo):
        """Provide a repository test double."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = tmp_path

        def changed_files(self) -> list[str]:
            """Changed files."""
            return ["removed.py"]

    settings = load(tmp_path, selection="changes")

    with pytest.raises(DocGubError, match="No eligible Python"):
        resolve(Repo(), None, settings)


def test_resolve_rejects_source_symlinks(tmp_path: Path) -> None:
    """Verify resolve rejects source symlinks."""
    root = tmp_path / "repository"
    root.mkdir()
    external = tmp_path / "external.py"
    external.write_text("def external():\n    pass\n", encoding="utf-8")
    (root / "linked.py").symlink_to(external)

    repo = ScopeRepo.__new__(ScopeRepo)
    repo.root = root

    with pytest.raises(DocGubError, match="No eligible Python"):
        resolve(repo, [root], load(root, selection="repository"))


def test_include_double_star_matches_root_and_nested_files(tmp_path: Path) -> None:
    """Verify include double star matches root and nested files."""
    root_source = tmp_path / "root.py"
    nested_source = tmp_path / "src" / "nested.py"
    ignored = tmp_path / "source.js"
    nested_source.parent.mkdir()
    root_source.write_text("pass\n", encoding="utf-8")
    nested_source.write_text("pass\n", encoding="utf-8")
    ignored.write_text("const value = true;\n", encoding="utf-8")
    repo = ScopeRepo.__new__(ScopeRepo)
    repo.root = tmp_path

    assert resolve(repo, [tmp_path], load(tmp_path, include=("**/*.py",))) == [
        "root.py",
        "src/nested.py",
    ]


def test_repository_walk_prunes_default_excluded_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify repository walk prunes default excluded directories."""
    source = tmp_path / "source.py"
    dependency = tmp_path / "node_modules" / "dependency.py"
    dependency.parent.mkdir()
    source.write_text("pass\n", encoding="utf-8")
    dependency.write_text("pass\n", encoding="utf-8")
    repo = ScopeRepo.__new__(ScopeRepo)
    repo.root = tmp_path
    inspected: list[str] = []
    original = scope._eligible

    def tracked_eligible(repository: GitRepo, relative: str, settings: Settings) -> bool:
        """Tracked eligible."""
        inspected.append(relative)
        return original(repository, relative, settings)

    monkeypatch.setattr(scope, "_eligible", tracked_eligible)

    assert resolve(repo, None, load(tmp_path, selection="repository")) == ["source.py"]
    assert not any("node_modules" in candidate for candidate in inspected)


def test_resolve_excludes_paths_ignored_by_git(tmp_path: Path) -> None:
    """Verify Git-ignored paths are excluded from an explicit directory scope."""
    source = tmp_path / "source.py"
    ignored = tmp_path / "generated.py"
    source.write_text("pass\n", encoding="utf-8")
    ignored.write_text("pass\n", encoding="utf-8")

    class Repo(ScopeRepo):
        """Provide a repository double with one ignored source file."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = tmp_path

        def ignored_paths(self, paths: Iterable[str]) -> set[str]:
            """Return generated Python files as ignored."""
            return {path for path in paths if path == "generated.py"}

    assert resolve(Repo(), [tmp_path], load(tmp_path, selection="repository")) == ["source.py"]
