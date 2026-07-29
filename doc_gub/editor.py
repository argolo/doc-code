"""Textual edits with content fingerprints to prevent stale previews from overwriting work."""
from __future__ import annotations

import ast
import difflib
import hashlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .errors import DocGubError
from .symbols import Symbol, eligible, render


@dataclass(frozen=True)
class PreparedFile:
    """Uma estrutura de dados imutável que armazena as informações antes e depois das edições propostas em um arquivo, incluindo um fingerprint e os símbolos afetados."""
    path: Path
    before: str
    after: str
    fingerprint: str
    symbols: tuple[Symbol, ...]
    changed: tuple[Symbol, ...]
    ignored: tuple[Symbol, ...]

    @property
    def diff(self) -> str:
        """Gera uma string no formato unified diff comparando o conteúdo original (before) com o conteúdo editado (after)."""
        return "".join(difflib.unified_diff(self.before.splitlines(keepends=True), self.after.splitlines(keepends=True), fromfile=f"a/{self.path}", tofile=f"b/{self.path}"))


def fingerprint(content: str) -> str:
    """Calcula um hash SHA256 de uma string de conteúdo para criar uma impressão digital única do arquivo.
    
    Args:
        content: Description of content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _python_code_shape(content: str) -> str:
    """Return a Python AST representation with only documentation expressions removed."""
    tree = ast.parse(content)

    class RemoveDocstrings(ast.NodeTransformer):
        """Um NodeTransformer AST usado para percorrer e modificar nós de código Python, removendo docstrings em módulos, classes e funções."""
        def visit_Module(self, node: ast.Module) -> ast.Module:
            """Visita um nó de módulo (ast.Module), garantindo que as docstrings iniciais sejam removidas do corpo do módulo.
            
            Args:
                node: Description of node."""
            self.generic_visit(node)
            _remove_leading_docstring(node.body)
            return node

        def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
            """Visita um nó de definição de classe (ast.ClassDef), garantindo que as docstrings iniciais sejam removidas do corpo da classe.
            
            Args:
                node: Description of node."""
            self.generic_visit(node)
            _remove_leading_docstring(node.body)
            return node

        def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
            """Visita um nó de definição de função (ast.FunctionDef), garantindo que as docstrings iniciais sejam removidas do corpo da função.
            
            Args:
                node: Description of node."""
            self.generic_visit(node)
            _remove_leading_docstring(node.body)
            return node

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
            """Visita um nó de definição de função assíncrona (ast.AsyncFunctionDef), garantindo que as docstrings iniciais sejam removidas do corpo da função.
            
            Args:
                node: Description of node."""
            self.generic_visit(node)
            _remove_leading_docstring(node.body)
            return node

    return ast.dump(RemoveDocstrings().visit(tree), include_attributes=False)


def _remove_leading_docstring(body: list[ast.stmt]) -> None:
    """Função utilitária para remover a primeira declaração de string (docstring) de uma lista de nós de instrução (body).
    
    Args:
        body: Description of body."""
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
    """A docstring cannot be inserted into a one-line Python function or class safely."""
    header = lines[symbol.line - 1].split("#", maxsplit=1)[0]
    return ":" in header and bool(header.rsplit(":", maxsplit=1)[1].strip())


def _documentation_indent(lines: list[str], symbol: Symbol, suffix: str) -> str:
    """Use the body's existing indentation for Python docstrings."""
    if suffix != ".py" or symbol.kind == "module":
        return symbol.indent
    if symbol.has_doc and symbol.doc_start:
        return lines[symbol.doc_start - 1][: len(lines[symbol.doc_start - 1]) - len(lines[symbol.doc_start - 1].lstrip())]
    for line in lines[symbol.line:symbol.end_line]:
        if line.strip():
            indentation = line[: len(line) - len(line.lstrip())]
            if len(indentation) > len(symbol.indent):
                return indentation
    return symbol.indent + "    "


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
        result = subprocess.run([*command, str(candidate)], text=True, capture_output=True, check=False)
    finally:
        candidate.unlink(missing_ok=True)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise DocGubError(f"{path}: generated documentation failed {suffix} validation: {detail}")


def prepare(
    path: Path,
    symbols: list[Symbol],
    descriptions: dict[str, str],
    settings: Settings,
    selected_symbols: list[Symbol] | None = None,
) -> PreparedFile:
    """Prepara um objeto PreparedFile, calculando as diferenças e validando a edição.

    `selected_symbols` limita a alteração a símbolos já gerados, permitindo gravar
    resultados incrementais no modo de escopo por símbolo.
    """
    before = path.read_text(encoding="utf-8")
    if len(before.encode("utf-8")) > settings.max_file_bytes:
        raise DocGubError(f"{path}: exceeds max_file_bytes.")
    selected = (
        selected_symbols
        if selected_symbols is not None
        else [item for item in symbols if eligible(item, settings.coverage)]
    )
    ignored = [item for item in symbols if item not in selected]
    lines = before.splitlines(keepends=True)
    for symbol in reversed(selected):
        if path.suffix == ".py" and not symbol.has_doc and symbol.kind != "module":
            if _is_inline_python_suite(lines, symbol):
                ignored.append(symbol)
                continue
        documentation = render(symbol, descriptions.get(symbol.name, ""), path.suffix, settings.python_format)
        indentation = _documentation_indent(lines, symbol, path.suffix)
        rendered = [f"{indentation}{row}\n" for row in documentation.splitlines()]
        if symbol.has_doc and settings.existing_docs == "replace" and symbol.doc_start and symbol.doc_end:
            lines[symbol.doc_start - 1:symbol.doc_end] = rendered
        elif not symbol.has_doc:
            insertion = _module_insertion(lines) if path.suffix == ".py" and symbol.kind == "module" else symbol.line if path.suffix == ".py" else symbol.line - 1
            lines[insertion:insertion] = rendered
        else:
            ignored.append(symbol)
    after = "".join(lines)
    if path.suffix == ".py" and _python_code_shape(before) != _python_code_shape(after):
        raise DocGubError(f"{path}: refusing an edit that changes Python code.")
    if after != before and path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
        _validate_javascript(after, path.suffix, path)
    changed = tuple(item for item in selected if item not in ignored)
    return PreparedFile(path, before, after, fingerprint(before), tuple(symbols), changed, tuple(ignored))


def apply(prepared: PreparedFile) -> None:
    """Aplica as edições contidas em um objeto PreparedFile ao sistema de arquivos, mas somente se o fingerprint do arquivo atual corresponder ao esperado.
    
    Args:
        prepared: Description of prepared."""
    current = prepared.path.read_text(encoding="utf-8")
    if fingerprint(current) != prepared.fingerprint:
        raise DocGubError(f"{prepared.path}: changed after preview; file was not written.")
    prepared.path.write_text(prepared.after, encoding="utf-8", newline="")
