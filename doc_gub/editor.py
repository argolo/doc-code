"""Textual edits with content fingerprints to prevent stale previews from overwriting work."""

from __future__ import annotations

import ast
import difflib
import hashlib
import re
import shutil
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
    """Store a prepared file edit.

    Uma estrutura de dados imutável que armazena as informações antes e depois das edições
    propostas em um arquivo, incluindo um fingerprint e os símbolos afetados.
    """

    path: Path
    before: str
    after: str
    fingerprint: str
    symbols: tuple[Symbol, ...]
    changed: tuple[Symbol, ...]
    ignored: tuple[Symbol, ...]

    @property
    def diff(self) -> str:
        """Return the unified diff for the prepared edit.

        Gera uma string no formato unified diff comparando o conteúdo original (before) com o
        conteúdo editado (after).
        """
        return "".join(
            difflib.unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                fromfile=f"a/{self.path}",
                tofile=f"b/{self.path}",
            )
        )


def fingerprint(content: str) -> str:
    """Return a SHA-256 fingerprint for content.

    Calcula um hash SHA256 de uma string de conteúdo para criar uma impressão digital única do
    arquivo.

    Args:
        content: Description of content.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _python_code_shape(content: str, filename: str = "<unknown>") -> str:
    """Return a Python AST representation with only documentation expressions removed."""
    tree = ast.parse(content, filename=filename)

    class RemoveDocstrings(ast.NodeTransformer):
        """Remove Python docstrings from an AST.

        Um NodeTransformer AST usado para percorrer e modificar nós de código Python, removendo
        docstrings em módulos, classes e funções.
        """

        def visit_Module(self, node: ast.Module) -> ast.Module:
            """Remove a module docstring.

            Visita um nó de módulo (ast.Module), garantindo que as docstrings iniciais sejam
            removidas do corpo do módulo.

            Args:
                node: Description of node.
            """
            self.generic_visit(node)
            _remove_leading_docstring(node.body)
            return node

        def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
            """Remove a class docstring.

            Visita um nó de definição de classe (ast.ClassDef), garantindo que as docstrings
            iniciais sejam removidas do corpo da classe.

            Args:
                node: Description of node.
            """
            self.generic_visit(node)
            _remove_leading_docstring(node.body)
            return node

        def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
            """Remove a function docstring.

            Visita um nó de definição de função (ast.FunctionDef), garantindo que as docstrings
            iniciais sejam removidas do corpo da função.

            Args:
                node: Description of node.
            """
            self.generic_visit(node)
            _remove_leading_docstring(node.body)
            return node

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
            """Remove an async function docstring.

            Visita um nó de definição de função assíncrona (ast.AsyncFunctionDef), garantindo que
            as docstrings iniciais sejam removidas do corpo da função.

            Args:
                node: Description of node.
            """
            self.generic_visit(node)
            _remove_leading_docstring(node.body)
            return node

    return ast.dump(RemoveDocstrings().visit(tree), include_attributes=False)


def _remove_leading_docstring(body: list[ast.stmt]) -> None:
    """Remove the leading docstring from a statement body.

    Função utilitária para remover a primeira declaração de string (docstring) de uma lista de
    nós de instrução (body).

    Args:
        body: Description of body.
    """
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


def _line_length(path: Path, suffix: str) -> int:
    """Read the nearest project line-length rule for generated documentation."""
    return _python_line_length(path) if suffix == ".py" else _javascript_line_length(path)


def _python_line_length(path: Path) -> int:
    """Read Ruff's configured line length from the nearest Python project."""
    for directory in (path.parent, *path.parents):
        config = directory / "pyproject.toml"
        if not config.is_file():
            continue
        try:
            with config.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return _DEFAULT_PYTHON_LINE_LENGTH
        ruff = data.get("tool", {}).get("ruff", {})
        line_length = ruff.get("line-length")
        return (
            line_length
            if isinstance(line_length, int) and line_length > 0
            else _DEFAULT_PYTHON_LINE_LENGTH
        )
    return _DEFAULT_PYTHON_LINE_LENGTH


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
    rendered: list[str], lines: list[str], following_index: int, symbol: Symbol, suffix: str
) -> None:
    """Keep one blank line between module/class docstrings and the following statement."""
    if suffix != ".py" or symbol.kind not in {"module", "class"}:
        return
    if following_index < len(lines) and lines[following_index].strip():
        rendered.append("\n")


def _validate_javascript(content: str, suffix: str, path: Path) -> None:
    """Parse generated JS/TS before it can be applied to the working tree."""
    if suffix == ".js":
        command = ["node", "--check"]
    else:
        compiler = shutil.which("tsc")
        if not compiler:
            raise DocGubError(
                f"{path}: TypeScript validation requires `tsc` on PATH; no file was changed."
            )
        command = [compiler, "--noEmit", "--noCheck", "--pretty", "false", "--allowJs"]
        if suffix in {".jsx", ".tsx"}:
            command.extend(["--jsx", "preserve"])

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=suffix, prefix="doc-gub-", delete=False
    ) as temporary:
        temporary.write(content)
        candidate = Path(temporary.name)
    try:
        result = subprocess.run(
            [*command, str(candidate)], text=True, capture_output=True, check=False
        )
    finally:
        candidate.unlink(missing_ok=True)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise DocGubError(f"{path}: generated documentation failed {suffix} validation: {detail}")


def prepare(
    path: Path,
    symbols: list[Symbol],
    descriptions: Mapping[str, str | Documentation],
    settings: Settings,
    selected_symbols: list[Symbol] | None = None,
) -> PreparedFile:
    """Prepara um objeto PreparedFile, calculando as diferenças e validando a edição.

    `selected_symbols` limita a alteração a símbolos já gerados, permitindo gravar resultados
    incrementais no modo de escopo por símbolo.
    """
    before = path.read_text(encoding="utf-8")
    if len(before.encode("utf-8")) > settings.max_file_bytes:
        raise DocGubError(f"{path}: exceeds max_file_bytes.")
    selected = (
        selected_symbols
        if selected_symbols is not None
        else [
            item
            for item in symbols
            if needs_documentation(item, settings.coverage, settings.existing_docs)
            and item.name in descriptions
        ]
    )
    ignored = [item for item in symbols if item not in selected]
    lines = before.splitlines(keepends=True)
    for symbol in reversed(selected):
        if path.suffix == ".py" and not symbol.has_doc and symbol.kind != "module":
            if _is_inline_python_suite(lines, symbol):
                ignored.append(symbol)
                continue
        indentation = _documentation_indent(lines, symbol, path.suffix)
        documentation = render(
            symbol,
            descriptions.get(symbol.name, ""),
            path.suffix,
            settings.python_format,
            _line_length(path, path.suffix),
            indentation,
        )
        rendered = [f"{indentation}{row}\n" if row else "\n" for row in documentation.splitlines()]
        if (
            symbol.has_doc
            and settings.existing_docs == "replace"
            and symbol.doc_start
            and symbol.doc_end
        ):
            _pep257_separator(rendered, lines, symbol.doc_end, symbol, path.suffix)
            lines[symbol.doc_start - 1 : symbol.doc_end] = rendered
        elif not symbol.has_doc:
            if path.suffix == ".py" and symbol.kind == "module":
                insertion = _module_insertion(lines)
            elif path.suffix == ".py":
                insertion = (symbol.body_line or symbol.line + 1) - 1
            else:
                insertion = symbol.line - 1
            _pep257_separator(rendered, lines, insertion, symbol, path.suffix)
            lines[insertion:insertion] = rendered
        else:
            ignored.append(symbol)
    after = "".join(lines)
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
    if after != before and path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
        _validate_javascript(after, path.suffix, path)
    changed = tuple(item for item in selected if item not in ignored)
    return PreparedFile(
        path, before, after, fingerprint(before), tuple(symbols), changed, tuple(ignored)
    )


def apply(prepared: PreparedFile) -> None:
    """Apply a prepared edit when its fingerprint is current.

    Aplica as edições contidas em um objeto PreparedFile ao sistema de arquivos, mas somente se o
    fingerprint do arquivo atual corresponder ao esperado.

    Args:
        prepared: Description of prepared.
    """
    current = prepared.path.read_text(encoding="utf-8")
    if fingerprint(current) != prepared.fingerprint:
        raise DocGubError(f"{prepared.path}: changed after preview; file was not written.")
    prepared.path.write_text(prepared.after, encoding="utf-8", newline="")
