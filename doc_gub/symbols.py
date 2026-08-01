"""Language-aware source symbol discovery and deterministic documentation rendering."""

from __future__ import annotations

import ast
import re
import textwrap
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Symbol:
    """Uma classe de dados imutável usada para armazenar metadados sobre um símbolo (módulo, classe ou função) encontrado no código fonte."""

    name: str
    kind: str
    line: int
    end_line: int
    indent: str
    args: tuple[str, ...] = ()
    has_doc: bool = False
    doc_start: int | None = None
    doc_end: int | None = None
    body_line: int | None = None


@dataclass(frozen=True)
class Documentation:
    """Generated documentation for one symbol and its named arguments."""

    description: str
    arguments: Mapping[str, str] = field(default_factory=dict)


def _statement_start_line(node: ast.stmt) -> int:
    """Return the first source line belonging to a statement, including decorators."""
    decorators = (
        node.decorator_list
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        else ()
    )
    return min((node.lineno, *(decorator.lineno for decorator in decorators)))


def _docstring_expression(node: ast.stmt | None) -> ast.Expr | None:
    """Return a statement when it is a string literal suitable for a docstring."""
    if (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ):
        return node
    return None


def python_symbols(content: str, filename: str = "<unknown>") -> list[Symbol]:
    """Analisa uma string de conteúdo Python e retorna uma lista de objetos Symbol que representam os símbolos definidos.

    Args:
        content: Description of content.
        filename: Name reported when Python parsing fails.

    """
    tree = ast.parse(content, filename=filename)
    lines = content.splitlines()
    found: list[Symbol] = []
    module_doc = _docstring_expression(tree.body[0] if tree.body else None)
    found.append(
        Symbol(
            "module",
            "module",
            0,
            0,
            "",
            (),
            module_doc is not None,
            module_doc.lineno if module_doc else None,
            module_doc.end_lineno if module_doc else None,
        )
    )

    def visit(nodes: list[ast.stmt], prefix: str = "") -> None:
        """Função auxiliar recursiva usada para percorrer nós AST e identificar símbolos aninhados (como métodos ou classes internas).

        Args:
            nodes: Description of nodes.
            prefix: Description of prefix.

        """
        for node in nodes:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = f"{prefix}.{node.name}" if prefix else node.name
            first = node.body[0] if node.body else None
            docstring = _docstring_expression(first)
            args = (
                tuple(arg.arg for arg in node.args.args if arg.arg not in {"self", "cls"})
                if hasattr(node, "args")
                else ()
            )
            found.append(
                Symbol(
                    name,
                    "class" if isinstance(node, ast.ClassDef) else "function",
                    node.lineno,
                    node.end_lineno or node.lineno,
                    lines[node.lineno - 1][
                        : len(lines[node.lineno - 1]) - len(lines[node.lineno - 1].lstrip())
                    ],
                    args,
                    docstring is not None,
                    docstring.lineno if docstring else None,
                    docstring.end_lineno if docstring else None,
                    _statement_start_line(first) if first else None,
                )
            )
            visit(node.body, name)

    visit(tree.body)
    return found


_JS = re.compile(
    r"^(?P<indent>\s*)(?:export\s+)?(?:(?:async\s+)?function\s+(?P<function>[$\w]+)|class\s+(?P<class>[$\w]+)|(?:(?:const|let|var)\s+)?(?P<arrow>[$\w]+)\s*=\s*(?:async\s*)?\((?P<args>[^)]*)\)\s*=>|(?P<method>[$\w]+)\s*\((?P<methodargs>[^)]*)\s*\{)"
)


def javascript_symbols(content: str) -> list[Symbol]:
    """Analisa uma string de conteúdo JavaScript usando expressões regulares para extrair funções, classes e métodos JS.

    Args:
        content: Description of content.

    """
    lines = content.splitlines()
    found: list[Symbol] = []
    for index, line in enumerate(lines):
        match = _JS.match(line)
        if not match:
            continue
        name = (
            match.group("function")
            or match.group("class")
            or match.group("arrow")
            or match.group("method")
        )
        kind = "class" if match.group("class") else "function"
        args = match.group("args") or match.group("methodargs") or ""
        arguments = tuple(
            item.strip().split("=")[0].strip() for item in args.split(",") if item.strip()
        )
        doc_start: int | None = None
        doc_end: int | None = None
        if index and lines[index - 1].strip().endswith("*/"):
            for doc_index in range(index - 1, -1, -1):
                if lines[doc_index].strip().startswith("/**"):
                    doc_start = doc_index + 1
                    doc_end = index
                    break
        found.append(
            Symbol(
                name,
                kind,
                index + 1,
                index + 1,
                match.group("indent"),
                arguments,
                doc_start is not None,
                doc_start,
                doc_end,
            )
        )
    return found


def discover(content: str, suffix: str, filename: str = "<unknown>") -> list[Symbol]:
    """Determina qual função de análise de símbolos deve ser utilizada (`python` ou `javascript`) com base na extensão do arquivo fornecida.

    Args:
        content: Description of content.
        suffix: Description of suffix.
        filename: Name reported when Python parsing fails.

    """
    return python_symbols(content, filename) if suffix == ".py" else javascript_symbols(content)


def eligible(symbol: Symbol, coverage: str) -> bool:
    """Verifica se um símbolo é considerado elegível para documentação, geralmente baseado em critérios de cobertura de testes ('all').

    Args:
        symbol: Description of symbol.
        coverage: Description of coverage.

    """
    return coverage == "all" or not symbol.has_doc


def needs_documentation(symbol: Symbol, coverage: str, existing_docs: str) -> bool:
    """Return whether a symbol is eligible and may have its docs generated.

    Existing documentation is never sent to a provider when ``existing_docs`` is
    ``"preserve"``. This rule is independent of request scope: a scope only
    changes the source sent with a request, not which symbols are candidates.
    """
    return eligible(symbol, coverage) and (not symbol.has_doc or existing_docs == "replace")


def source_for_symbol(
    content: str, symbol: Symbol, suffix: str, filename: str = "<unknown>"
) -> str:
    """Return the smallest self-contained source region available for one symbol.

    Python symbols have AST-derived end lines. JavaScript discovery is intentionally
    lightweight, so its region ends when its braces balance (or at a semicolon for
    expression-bodied arrow functions). Module documentation remains file-scoped.
    """
    if symbol.kind == "module":
        return _module_outline(content, suffix, filename)
    lines = content.splitlines(keepends=True)
    start = symbol.line - 1
    if suffix == ".py":
        while start > 0 and lines[start - 1].lstrip().startswith("@"):
            start -= 1
        return "".join(lines[start : symbol.end_line])

    depth = 0
    opened = False
    for index in range(start, len(lines)):
        line = lines[index]
        depth += line.count("{") - line.count("}")
        opened = opened or "{" in line
        if (opened and depth <= 0) or (not opened and ";" in line):
            return "".join(lines[start : index + 1])
    return "".join(lines[start:])


def _module_outline(content: str, suffix: str, filename: str = "<unknown>") -> str:
    """Create a compact module overview without including implementation bodies."""
    if suffix == ".py":
        return _python_module_outline(content, filename)
    return _javascript_module_outline(content)


def _python_module_outline(content: str, filename: str = "<unknown>") -> str:
    """Return Python module metadata, imports, constants, and public signatures."""
    tree = ast.parse(content, filename=filename)
    lines = content.splitlines(keepends=True)
    outline = ["MODULE OUTLINE:"]
    first = tree.body[0] if tree.body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        outline.append("MODULE DOCSTRING:")
        outline.append("".join(lines[first.lineno - 1 : first.end_lineno]).rstrip())
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            outline.append("".join(lines[node.lineno - 1 : node.end_lineno]).rstrip())
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = _public_assignment_names(node)
            if names:
                outline.append(f"CONSTANTS: {', '.join(names)}")
        elif isinstance(
            node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and not node.name.startswith("_"):
            outline.append(_python_definition_header(lines, node))
    return "\n\n".join(part for part in outline if part)


def _public_assignment_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    """Return public top-level names without serializing potentially large values."""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [
        target.id
        for target in targets
        if isinstance(target, ast.Name) and not target.id.startswith("_")
    ]


def _python_definition_header(
    lines: list[str], node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
) -> str:
    """Return decorators and the signature, stopping before the first body statement."""
    start = node.lineno - 1
    while start > 0 and lines[start - 1].lstrip().startswith("@"):
        start -= 1
    first_body_line = node.body[0].lineno - 1 if node.body else (node.end_lineno or node.lineno)
    end = max(start + 1, first_body_line)
    return "".join(lines[start:end]).rstrip()


def _javascript_module_outline(content: str) -> str:
    """Return imports and definition lines for JavaScript and TypeScript modules."""
    lines = content.splitlines(keepends=True)
    outline = ["MODULE OUTLINE:"]
    index = 0
    while index < len(lines):
        stripped = lines[index].lstrip()
        if stripped.startswith(("import ", "export {", "export *")):
            statement = [lines[index]]
            while not statement[-1].rstrip().endswith(";") and index + 1 < len(lines):
                index += 1
                statement.append(lines[index])
            outline.append("".join(statement).rstrip())
        index += 1
    for symbol in javascript_symbols(content):
        if not symbol.name.startswith("_"):
            outline.append(lines[symbol.line - 1].rstrip())
    return "\n\n".join(part for part in outline if part)


def render(
    symbol: Symbol,
    documentation: str | Documentation,
    suffix: str,
    python_format: str,
    line_length: int = 100,
    indentation: str = "",
) -> str:
    """Formata a descrição textual de um símbolo (docstring) no formato apropriado, seja ele Python docstrings ou JSDoc.

    Args:
        symbol: Description of symbol.
        documentation: Description of the symbol and, optionally, its arguments.
        suffix: Description of suffix.
        python_format: Description of python_format.
        line_length: Maximum allowed line length for the target project.
        indentation: Indentation that will precede the rendered documentation.

    """
    if isinstance(documentation, Documentation):
        description = documentation.description
        argument_docs = documentation.arguments
    else:
        description = documentation
        argument_docs = {}
    description = " ".join(description.split()).strip() or f"Describe {symbol.name}."
    if not description.endswith("."):
        description = description.rstrip("!?;:") + "."
    if suffix != ".py":
        rows = ["/**"]
        rows.extend(_javascript_doc_lines(description, line_length, indentation))
        for arg in symbol.args:
            rows.extend(
                _javascript_doc_lines(
                    f"@param {{any}} {arg} {_argument_description(arg, argument_docs)}",
                    line_length,
                    indentation,
                )
            )
        rows.append(" */")
        return "\n".join(rows)
    if symbol.kind in {"module", "class"} or not symbol.args:
        return _python_docstring(description, line_length, indentation)
    if python_format == "numpy":
        body = [description, "", "Parameters", "----------"]
        body.extend(
            f"{arg} : Any\n    {_argument_description(arg, argument_docs)}" for arg in symbol.args
        )
    elif python_format == "sphinx":
        body = [description, ""] + [
            f":param {arg}: {_argument_description(arg, argument_docs)}" for arg in symbol.args
        ]
    else:
        body = [description, "", "Args:"] + [
            f"    {arg}: {_argument_description(arg, argument_docs)}" for arg in symbol.args
        ]
    return _python_docstring("\n".join(body), line_length, indentation)


def _python_docstring(content: str, line_length: int, indentation: str) -> str:
    """Render generated text using the one-line and multi-line forms from PEP 257."""
    escaped = content.replace("\\", "\\\\").replace('"""', '\\"""')
    single_line_width = max(line_length - len(indentation) - 6, 20)
    if "\n" not in escaped and len(escaped) <= single_line_width:
        return f'"""{escaped}"""'

    rows: list[str] = []
    for index, row in enumerate(escaped.splitlines()):
        if not row:
            rows.append("")
            continue
        available = line_length - len(indentation) - (3 if index == 0 else 0)
        rows.extend(_wrap_documentation_line(row, max(available, 20)))
    return '"""' + "\n".join(rows) + '\n\n"""'


def _argument_description(argument: str, descriptions: Mapping[str, str]) -> str:
    """Return a normalized argument description with a safe compatibility fallback."""
    description = " ".join(descriptions.get(argument, "").split()).strip()
    if not description:
        return f"Description of {argument}."
    return description if description.endswith(".") else description.rstrip("!?;:") + "."


def _javascript_doc_lines(content: str, line_length: int, indentation: str) -> list[str]:
    """Wrap one JSDoc content line while accounting for its leading ` * ` marker."""
    width = max(line_length - len(indentation) - 3, 20)
    return [f" * {row}" if row else " *" for row in _wrap_documentation_line(content, width)]


def _wrap_documentation_line(content: str, width: int) -> list[str]:
    """Wrap documentation text while retaining its semantic indentation."""
    leading = content[: len(content) - len(content.lstrip())]
    text = content.lstrip()
    return textwrap.wrap(
        text,
        width=width,
        initial_indent=leading,
        subsequent_indent=leading,
        break_long_words=True,
        break_on_hyphens=False,
    ) or [leading]
