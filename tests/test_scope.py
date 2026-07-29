from __future__ import annotations

from pathlib import Path

from doc_gub.config import load
from doc_gub.scope import resolve


def test_resolve_combines_multiple_files_and_directories_without_duplicates(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    directory = tmp_path / "src"
    second = directory / "second.py"
    directory.mkdir()
    first.write_text("pass\n", encoding="utf-8")
    second.write_text("pass\n", encoding="utf-8")

    class Repo:
        root = tmp_path

        def relative_path(self, requested: Path) -> str:
            return requested.resolve().relative_to(self.root).as_posix()

    assert resolve(Repo(), [first, directory, first], load(tmp_path, selection="repository")) == [
        "first.py",
        "src/second.py",
    ]
