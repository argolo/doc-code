"""Textual edits with content fingerprints to prevent stale previews from overwriting work."""

from __future__ import annotations

import ast
import difflib
import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .errors import DocGubError
from .symbols import Documentation, Symbol, needs_documentation, render

_DEFAULT_PYTHON_LINE_LENGTH = 88
_DEFAULT_JAVASCRIPT_LINE_LENGTH = 100
_ESLINT_MAX_LEN = re.compile(
    r"[\"']?max-len[\"']?\s*:\s*\[\s*(?:[\"'](?:error|warn)[\"']|[12])\s*,\s*"
    r"(?:\{\s*code\s*:\s*)?(\d+)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class PreparedFile:
    """Store an immutable, validated file edit and its source fingerprint."""

    path: Path
    before: str
    after: str
    fingerprint: str
    symbols: tuple[Symbol, ...]
    changed: tuple[Symbol, ...]
    ignored: tuple[Symbol, ...]
    display_path: Path | None = None

    @property
    def diff(self) -> str:
        """Return the unified diff for the prepared edit."""
        display_path = self.display_path or self.path
        return "".join(
            difflib.unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                fromfile=f"a/{display_path.as_posix()}",
                tofile=f"b/{display_path.as_posix()}",
            )
        )


def fingerprint(content: str) -> str:
    """Return a SHA-256 fingerprint for content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _read_utf8(path: Path) -> str:
    """Decode UTF-8 without universal-newline conversion."""
    try:
        return path.read_bytes().decode("utf-8")
    except OSError as exc:
        raise DocGubError(f"{path}: unable to read source file: {exc}") from exc


def _line_ending(content: str) -> str:
    """Return the first line-ending convention used by source content."""
    match = re.search(r"\r\n|\r|\n", content)
    return match.group(0) if match else "\n"


def _python_code_shape(content: str, filename: str = "<unknown>") -> str:
    """Return a Python AST representation with only documentation expressions removed."""
    tree = ast.parse(content, filename=filename)

    class RemoveDocstrings(ast.NodeTransformer):
        """Remove Python docstrings from an AST."""

        def visit_Module(self, node: ast.Module) -> ast.Module:
            """Remove a module docstring."""
            self.generic_visit(node)
            _remove_leading_docstring(node.body)
            return node

        def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
            """Remove a class docstring."""
            self.generic_visit(node)
            _remove_leading_docstring(node.body)
            return node

        def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
            """Remove a function docstring."""
            self.generic_visit(node)
            _remove_leading_docstring(node.body)
            return node

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
            """Remove an async function docstring."""
            self.generic_visit(node)
            _remove_leading_docstring(node.body)
            return node

    return ast.dump(RemoveDocstrings().visit(tree), include_attributes=False)


def _remove_leading_docstring(body: list[ast.stmt]) -> None:
    """Remove the leading docstring from a statement body."""
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            body.pop(0)


def _module_insertion(lines: list[str]) -> int:
    """Keep Unix shebang and Python encoding declarations in their required positions."""
    insertion = 1 if lines and lines[0].startswith("#!") else 0
    coding = "coding"
    if insertion < len(lines) and coding in lines[insertion][:80]:
        insertion += 1
    return insertion


def _is_inline_python_suite(lines: list[str], symbol: Symbol) -> bool:
    """Return whether a Python function or class uses an inline suite."""
    header = lines[symbol.line - 1].split("#", maxsplit=1)[0]
    return ":" in header and bool(header.rsplit(":", maxsplit=1)[1].strip())


def can_insert_documentation(content: str, symbol: Symbol, suffix: str) -> bool:
    """Return whether a symbol can receive documentation without rewriting its code."""
    if suffix != ".py" or symbol.kind == "module" or symbol.has_doc:
        return True
    return not _is_inline_python_suite(content.splitlines(keepends=True), symbol)


def _documentation_indent(lines: list[str], symbol: Symbol, suffix: str) -> str:
    """Use the body's existing indentation for Python docstrings."""
    if suffix != ".py" or symbol.kind == "module":
        return symbol.indent
    if symbol.has_doc and symbol.doc_start:
        return lines[symbol.doc_start - 1][
            : len(lines[symbol.doc_start - 1]) - len(lines[symbol.doc_start - 1].lstrip())
        ]
    for line in lines[symbol.line : symbol.end_line]:
        if line.strip():
            indentation = line[: len(line) - len(line.lstrip())]
            if len(indentation) > len(symbol.indent):
                return indentation
    return symbol.indent + "    "


def _python_rendering_options(path: Path, fallback_format: str) -> tuple[int, str]:
    """Read Ruff line length and pydocstyle convention from the target project."""
    for directory in (path.parent, *path.parents):
        for name in (".ruff.toml", "ruff.toml", "pyproject.toml"):
            ruff = _ruff_settings(directory / name)
            if ruff is None:
                continue
            line_length = ruff.get("line-length")
            normalized_line_length = (
                line_length
                if isinstance(line_length, int) and line_length > 0
                else _DEFAULT_PYTHON_LINE_LENGTH
            )
            lint = ruff.get("lint", {})
            pydocstyle = lint.get("pydocstyle", {}) if isinstance(lint, dict) else {}
            convention = pydocstyle.get("convention") if isinstance(pydocstyle, dict) else None
            python_format = convention if convention in {"google", "numpy"} else fallback_format
            return normalized_line_length, python_format
    return _DEFAULT_PYTHON_LINE_LENGTH, fallback_format


def _ruff_settings(config: Path, seen: frozenset[Path] = frozenset()) -> dict[str, object] | None:
    """Load one Ruff configuration, including its optional extended configuration."""
    if not config.is_file():
        return None
    resolved = config.resolve()
    if resolved in seen:
        return None
    try:
        with config.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    if config.name == "pyproject.toml":
        tool = data.get("tool", {})
        ruff = tool.get("ruff", {}) if isinstance(tool, dict) else {}
        if not ruff:
            return None
    else:
        ruff = data
    if not isinstance(ruff, dict):
        return None
    extend = ruff.get("extend")
    inherited: dict[str, object] = {}
    if isinstance(extend, str) and extend:
        inherited = _ruff_settings(config.parent / extend, seen | {resolved}) or {}
    return _merge_ruff_settings(inherited, ruff)


def _merge_ruff_settings(
    inherited: dict[str, object], configured: dict[str, object]
) -> dict[str, object]:
    """Overlay nested Ruff settings while retaining inherited pydocstyle options."""
    merged = dict(inherited)
    for key, value in configured.items():
        previous = merged.get(key)
        if isinstance(previous, dict) and isinstance(value, dict):
            merged[key] = _merge_ruff_settings(previous, value)
        else:
            merged[key] = value
    return merged


def _rendering_options(path: Path, settings: Settings) -> tuple[int, str]:
    """Return target-project line length and Python documentation format."""
    if path.suffix == ".py":
        return _python_rendering_options(path, settings.python_format)
    return _javascript_line_length(path), settings.python_format


def _javascript_line_length(path: Path) -> int:
    """Read ESLint's `max-len` code limit from the nearest flat config."""
    for directory in (path.parent, *path.parents):
        for name in ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs"):
            config = directory / name
            if not config.is_file():
                continue
            try:
                content = config.read_text(encoding="utf-8")
            except OSError:
                return _DEFAULT_JAVASCRIPT_LINE_LENGTH
            match = _ESLINT_MAX_LEN.search(content)
            return int(match.group(1)) if match else _DEFAULT_JAVASCRIPT_LINE_LENGTH
    return _DEFAULT_JAVASCRIPT_LINE_LENGTH


def _pep257_separator(
    rendered: list[str],
    lines: list[str],
    following_index: int,
    symbol: Symbol,
    suffix: str,
    newline: str,
) -> None:
    """Keep one blank line between module/class docstrings and the following statement."""
    if suffix != ".py" or symbol.kind not in {"module", "class"}:
        return
    if following_index < len(lines) and lines[following_index].strip():
        rendered.append(newline)


def _validate_javascript(content: str, suffix: str, path: Path) -> None:
    """Parse generated JS/TS before it can be applied to the working tree."""
    command = validation_command(suffix, path)

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=suffix, prefix="doc-code-", delete=False
    ) as temporary:
        temporary.write(content)
        candidate = Path(temporary.name)
    try:
        try:
            result = subprocess.run(
                [*command, str(candidate)], text=True, capture_output=True, check=False
            )
        except OSError as exc:
            raise DocGubError(
                f"{path}: unable to run {Path(command[0]).name} validation: {exc}; "
                "no file was changed."
            ) from exc
    finally:
        candidate.unlink(missing_ok=True)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise DocGubError(f"{path}: generated documentation failed {suffix} validation: {detail}")


def validation_command(suffix: str, path: Path) -> list[str]:
    """Return the validation command required for a JavaScript-family source file."""
    if suffix == ".js":
        runtime = shutil.which("node")
        if not runtime:
            raise DocGubError(
                f"{path}: JavaScript validation requires `node` on PATH; no file was changed."
            )
        command = [runtime, "--check"]
    else:
        compiler = shutil.which("tsc")
        if not compiler:
            raise DocGubError(
                f"{path}: TypeScript validation requires `tsc` on PATH; no file was changed."
            )
        command = [compiler, "--noEmit", "--noCheck", "--pretty", "false", "--allowJs"]
        if suffix in {".jsx", ".tsx"}:
            command.extend(["--jsx", "preserve"])
    return command


def _selected_symbols(
    symbols: list[Symbol],
    descriptions: Mapping[str, str | Documentation],
    settings: Settings,
    selected_symbols: list[Symbol] | None,
) -> list[Symbol]:
    """Return explicitly selected symbols or eligible generated symbols."""
    if selected_symbols is not None:
        return selected_symbols
    return [
        symbol
        for symbol in symbols
        if needs_documentation(symbol, settings.coverage)
        and symbol.name in descriptions
    ]


def _insert_documentation(
    lines: list[str],
    symbol: Symbol,
    description: str | Documentation,
    path: Path,
    settings: Settings,
    python_format: str,
    newline: str,
    line_length: int,
) -> bool:
    """Render and insert one symbol's documentation, returning whether it changed."""
    if path.suffix == ".py" and not symbol.has_doc and symbol.kind != "module":
        if not can_insert_documentation("".join(lines), symbol, path.suffix):
            return False
    indentation = _documentation_indent(lines, symbol, path.suffix)
    documentation = render(
        symbol,
        description,
        path.suffix,
        python_format,
        line_length,
        indentation,
    )
    rendered = [
        f"{indentation}{row}{newline}" if row else newline for row in documentation.splitlines()
    ]
    if symbol.has_doc and symbol.doc_start and symbol.doc_end:
        _pep257_separator(rendered, lines, symbol.doc_end, symbol, path.suffix, newline)
        lines[symbol.doc_start - 1 : symbol.doc_end] = rendered
        return True
    if symbol.has_doc:
        return False
    if path.suffix == ".py" and symbol.kind == "module":
        insertion = _module_insertion(lines)
    elif path.suffix == ".py":
        insertion = (symbol.body_line or symbol.line + 1) - 1
    else:
        insertion = symbol.line - 1
    _pep257_separator(rendered, lines, insertion, symbol, path.suffix, newline)
    lines[insertion:insertion] = rendered
    return True


def preview_documentation(
    path: Path,
    content: str,
    symbol: Symbol,
    documentation: str | Documentation,
    settings: Settings,
) -> str:
    """Render one docstring exactly as it will appear in the source file."""
    lines = content.splitlines(keepends=True)
    line_length, python_format = _rendering_options(path, settings)
    indentation = _documentation_indent(lines, symbol, path.suffix)
    rendered = render(
        symbol,
        documentation,
        path.suffix,
        python_format,
        line_length,
        indentation,
    )
    return "\n".join(f"{indentation}{row}" if row else "" for row in rendered.splitlines())


def _validate_edit(before: str, after: str, path: Path) -> None:
    """Verify that an edit changes documentation only and remains syntactically valid."""
    if path.suffix == ".py":
        before_shape = _python_code_shape(before, str(path))
        try:
            after_shape = _python_code_shape(after, str(path))
        except SyntaxError as exc:
            line = exc.lineno or "?"
            raise DocGubError(
                f"{path}: generated documentation failed Python validation at line {line}: "
                f"{exc.msg}; the source file was not changed."
            ) from exc
        if before_shape != after_shape:
            raise DocGubError(f"{path}: refusing an edit that changes Python code.")
    elif after != before and path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
        _validate_javascript(after, path.suffix, path)


def prepare(
    path: Path,
    symbols: list[Symbol],
    descriptions: Mapping[str, str | Documentation],
    settings: Settings,
    selected_symbols: list[Symbol] | None = None,
    display_path: Path | None = None,
) -> PreparedFile:
    """Prepare and validate an edit, optionally limited to generated symbols."""
    if path.is_symlink():
        raise DocGubError(f"{path}: symbolic links are not supported; no file was changed.")
    before = _read_utf8(path)
    if len(before.encode("utf-8")) > settings.max_file_bytes:
        raise DocGubError(f"{path}: exceeds max_file_bytes.")
    selected = _selected_symbols(symbols, descriptions, settings, selected_symbols)
    ignored = [item for item in symbols if item not in selected]
    lines = before.splitlines(keepends=True)
    newline = _line_ending(before)
    line_length, python_format = _rendering_options(path, settings)
    for symbol in reversed(selected):
        inserted = _insert_documentation(
            lines,
            symbol,
            descriptions.get(symbol.name, ""),
            path,
            settings,
            python_format,
            newline,
            line_length,
        )
        if not inserted:
            ignored.append(symbol)
    after = "".join(lines)
    _validate_edit(before, after, path)
    changed = tuple(item for item in selected if item not in ignored)
    return PreparedFile(
        path,
        before,
        after,
        fingerprint(before),
        tuple(symbols),
        changed,
        tuple(ignored),
        display_path,
    )


def apply(prepared: PreparedFile) -> None:
    """Atomically apply an edit when its source fingerprint is still current."""
    if prepared.path.is_symlink():
        raise DocGubError(
            f"{prepared.path}: became a symbolic link after preview; file was not written."
        )
    current = _read_utf8(prepared.path)
    if fingerprint(current) != prepared.fingerprint:
        raise DocGubError(f"{prepared.path}: changed after preview; file was not written.")
    mode = stat.S_IMODE(prepared.path.stat().st_mode)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{prepared.path.name}.",
            dir=prepared.path.parent,
            delete=False,
        ) as temporary:
            temporary.write(prepared.after)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.chmod(mode)
        os.replace(temporary_path, prepared.path)
        temporary_path = None
        try:
            directory = os.open(prepared.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass
    except OSError as exc:
        raise DocGubError(
            f"{prepared.path}: unable to apply the atomic file update: {exc}"
        ) from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
