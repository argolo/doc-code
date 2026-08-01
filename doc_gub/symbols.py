"""Language-aware source symbol discovery and deterministic documentation rendering."""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Mapping
from dataclasses import dataclass, field

from tree_sitter import Language, Node, Parser
from tree_sitter_javascript import language as javascript_language
from tree_sitter_typescript import language_tsx, language_typescript


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


def _python_arguments(arguments: ast.arguments) -> tuple[str, ...]:
    """Return every named function parameter except conventional instance parameters."""
    positional = (*arguments.posonlyargs, *arguments.args)
    named = [argument.arg for argument in positional if argument.arg not in {"self", "cls"}]
    if arguments.vararg:
        named.append(arguments.vararg.arg)
    named.extend(argument.arg for argument in arguments.kwonlyargs)
    if arguments.kwarg:
        named.append(arguments.kwarg.arg)
    return tuple(named)


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
                _python_arguments(node.args)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
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


_JAVASCRIPT = Language(javascript_language())
_TYPESCRIPT = Language(language_typescript())
_TSX = Language(language_tsx())


def _node_text(node: Node | None, source: bytes) -> str:
    """Return the UTF-8 source slice represented by an AST node."""
    return source[node.start_byte : node.end_byte].decode() if node else ""


def _parameter_names(node: Node | None, source: bytes) -> tuple[str, ...]:
    """Extract named parameter bindings from a JavaScript or TypeScript AST node."""
    if node is None:
        return ()
    if node.type in {"identifier", "shorthand_property_identifier_pattern"}:
        return (_node_text(node, source),)
    if node.type == "assignment_pattern":
        return _parameter_names(node.child_by_field_name("left"), source)
    if node.type in {"required_parameter", "optional_parameter"}:
        return _parameter_names(node.child_by_field_name("pattern"), source)
    if node.type == "rest_pattern":
        return tuple(name for child in node.children for name in _parameter_names(child, source))
    return tuple(name for child in node.named_children for name in _parameter_names(child, source))


def _javascript_symbol(
    node: Node, name: str, kind: str, parameters: Node | None, source: bytes, lines: list[str]
) -> Symbol:
    """Build a symbol from a declaration AST node and its optional JSDoc block."""
    line = node.start_point.row + 1
    doc_start: int | None = None
    doc_end: int | None = None
    if line > 1 and lines[line - 2].strip().endswith("*/"):
        for index in range(line - 2, -1, -1):
            if lines[index].strip().startswith("/**"):
                doc_start, doc_end = index + 1, line - 1
                break
    source_line = lines[line - 1]
    indent = source_line[: len(source_line) - len(source_line.lstrip())]
    return Symbol(
        name,
        kind,
        line,
        node.end_point.row + 1,
        indent,
        _parameter_names(parameters, source),
        doc_start is not None,
        doc_start,
        doc_end,
    )


def javascript_symbols(content: str, suffix: str = ".js") -> list[Symbol]:
    """Discover JavaScript and TypeScript declarations through their concrete syntax tree."""
    source = content.encode()
    language = _TSX if suffix == ".tsx" else _TYPESCRIPT if suffix == ".ts" else _JAVASCRIPT
    tree = Parser(language).parse(source)
    lines = content.splitlines()
    found: list[Symbol] = []

    def visit(node: Node, prefix: str = "") -> None:
        """Visita um nó (node) e registra símbolos JavaScript encontrados nele, anexando o nome do
        prefixo fornecido.

        Args:
            node: O nó AST a ser visitado.
            prefix: O prefixo de escopo para os nomes dos símbolos encontrados.

        """
        declaration = node
        if node.type == "export_statement":
            declaration = node.child_by_field_name("declaration") or node
        if declaration.type == "class_declaration":
            raw_name = _node_text(declaration.child_by_field_name("name"), source)
            name = f"{prefix}.{raw_name}" if prefix else raw_name
            found.append(_javascript_symbol(declaration, name, "class", None, source, lines))
            body = declaration.child_by_field_name("body")
            if body:
                for child in body.named_children:
                    if child.type == "method_definition":
                        method_name = _node_text(child.child_by_field_name("name"), source)
                        found.append(
                            _javascript_symbol(
                                child,
                                f"{name}.{method_name}",
                                "function",
                                child.child_by_field_name("parameters"),
                                source,
                                lines,
                            )
                        )
            return
        if declaration.type in {"function_declaration", "generator_function_declaration"}:
            raw_name = _node_text(declaration.child_by_field_name("name"), source)
            found.append(
                _javascript_symbol(
                    declaration,
                    f"{prefix}.{raw_name}" if prefix else raw_name,
                    "function",
                    declaration.child_by_field_name("parameters"),
                    source,
                    lines,
                )
            )
            return
        if declaration.type == "lexical_declaration":
            for declarator in declaration.named_children:
                value = declarator.child_by_field_name("value")
                if (
                    declarator.type == "variable_declarator"
                    and value
                    and value.type == "arrow_function"
                ):
                    name = _node_text(declarator.child_by_field_name("name"), source)
                    found.append(
                        _javascript_symbol(
                            declarator,
                            name,
                            "function",
                            value.child_by_field_name("parameters")
                            or value.child_by_field_name("parameter"),
                            source,
                            lines,
                        )
                    )
            return
        for child in node.named_children:
            visit(child, prefix)

    visit(tree.root_node)
    return found


def discover(content: str, suffix: str, filename: str = "<unknown>") -> list[Symbol]:
    """Determina qual função de análise de símbolos deve ser utilizada (`python` ou `javascript`) com base na extensão do arquivo fornecida.

    Args:
        content: Description of content.
        suffix: Description of suffix.
        filename: Name reported when Python parsing fails.

    """
    return (
        python_symbols(content, filename)
        if suffix == ".py"
        else javascript_symbols(content, suffix)
    )


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

    return "".join(lines[start : symbol.end_line])


def _module_outline(content: str, suffix: str, filename: str = "<unknown>") -> str:
    """Create a compact module overview without including implementation bodies."""
    if suffix == ".py":
        return _python_module_outline(content, filename)
    return _javascript_module_outline(content, suffix)


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


def _javascript_module_outline(content: str, suffix: str = ".js") -> str:
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
    for symbol in javascript_symbols(content, suffix):
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
