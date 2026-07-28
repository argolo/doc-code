"""Textual edits with content fingerprints to prevent stale previews from overwriting work."""
from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .errors import DocGubError
from .symbols import Symbol, eligible, render


@dataclass(frozen=True)
class PreparedFile:
    path: Path
    before: str
    after: str
    fingerprint: str
    symbols: tuple[Symbol, ...]
    changed: tuple[Symbol, ...]
    ignored: tuple[Symbol, ...]

    @property
    def diff(self) -> str:
        return "".join(difflib.unified_diff(self.before.splitlines(keepends=True), self.after.splitlines(keepends=True), fromfile=f"a/{self.path}", tofile=f"b/{self.path}"))


def fingerprint(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def prepare(path: Path, symbols: list[Symbol], descriptions: dict[str, str], settings: Settings) -> PreparedFile:
    before = path.read_text(encoding="utf-8")
    if len(before.encode("utf-8")) > settings.max_file_bytes:
        raise DocGubError(f"{path}: exceeds max_file_bytes.")
    selected = [item for item in symbols if eligible(item, settings.coverage)]
    ignored = [item for item in symbols if item not in selected]
    lines = before.splitlines(keepends=True)
    for symbol in reversed(selected):
        documentation = render(symbol, descriptions.get(symbol.name, ""), path.suffix, settings.python_format)
        rendered = [f"{symbol.indent}{row}\n" for row in documentation.splitlines()]
        if symbol.has_doc and settings.existing_docs == "replace" and symbol.doc_start and symbol.doc_end:
            lines[symbol.doc_start - 1:symbol.doc_end] = rendered
        elif not symbol.has_doc:
            insertion = symbol.line if path.suffix == ".py" else symbol.line - 1
            lines[insertion:insertion] = rendered
        else:
            ignored.append(symbol)
    after = "".join(lines)
    changed = tuple(item for item in selected if not item.has_doc or settings.existing_docs == "replace")
    return PreparedFile(path, before, after, fingerprint(before), tuple(symbols), changed, tuple(ignored))


def apply(prepared: PreparedFile) -> None:
    current = prepared.path.read_text(encoding="utf-8")
    if fingerprint(current) != prepared.fingerprint:
        raise DocGubError(f"{prepared.path}: changed after preview; file was not written.")
    prepared.path.write_text(prepared.after, encoding="utf-8", newline="")
