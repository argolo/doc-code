"""Language-aware source symbol discovery and deterministic documentation rendering."""

from __future__ import annotations

import ast
import textwrap
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from tree_sitter import Language, Node, Parser
from tree_sitter_javascript import language as javascript_language
from tree_sitter_typescript import language_tsx, language_typescript


@dataclass(frozen=True)
class Symbol:
    """Store immutable metadata for a source symbol."""

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


def _unique_symbol_names(symbols: list[Symbol]) -> list[Symbol]:
    """Disambiguate valid redefinitions without changing already unique public names."""
    totals = Counter(symbol.name for symbol in symbols)
    occurrences: defaultdict[tuple[str, int], int] = defaultdict(int)
    normalized: list[Symbol] = []
    for symbol in symbols:
        if totals[symbol.name] == 1:
            normalized.append(symbol)
            continue
        location = (symbol.name, symbol.line)
        occurrences[location] += 1
        normalized.append(
            replace(symbol, name=f"{symbol.name}@L{symbol.line}:{occurrences[location]}")
        )
    return normalized


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
    """Discover module, class, and function symbols in Python source."""
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
        """Visit nested Python declarations recursively."""
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
    return _unique_symbol_names(found)


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


def _raise_javascript_syntax_error(root: Node, content: str, filename: str) -> None:
    """Raise a Python-style syntax error for the first malformed AST node."""
    if not root.has_error:
        return
    malformed: list[Node] = []
    pending = [root]
    while pending:
        node = pending.pop()
        if node.is_error or node.is_missing:
            malformed.append(node)
        pending.extend(child for child in node.children if child.has_error)
    target = min(malformed or [root], key=lambda node: node.start_byte)
    row, column = target.start_point
    lines = content.splitlines()
    source_line = lines[row] if row < len(lines) else ""
    raise SyntaxError(
        "invalid JavaScript/TypeScript syntax",
        (filename, row + 1, column + 1, source_line + "\n"),
    )


class _JavaScriptSymbolVisitor:
    """Collect documentable declarations from a tree-sitter syntax tree."""

    def __init__(self, source: bytes, lines: list[str]) -> None:
        """Initialize the visitor with shared source context."""
        self.source = source
        self.lines = lines
        self.found: list[Symbol] = []

    @staticmethod
    def _qualified(prefix: str, name: str) -> str:
        """Return a name qualified by its enclosing declaration."""
        return f"{prefix}.{name}" if prefix else name

    def _append(self, node: Node, name: str, kind: str, parameters: Node | None = None) -> None:
        """Append one symbol using this visitor's shared source context."""
        self.found.append(_javascript_symbol(node, name, kind, parameters, self.source, self.lines))

    def visit(self, node: Node, prefix: str = "") -> None:
        """Visit a syntax node and its relevant descendants."""
        declaration = node
        if node.type == "export_statement":
            declaration = node.child_by_field_name("declaration") or node
        if declaration.type == "class_declaration":
            self._visit_class(declaration, prefix)
            return
        if declaration.type in {"function_declaration", "generator_function_declaration"}:
            self._visit_function(declaration, prefix)
            return
        if declaration.type == "lexical_declaration":
            self._visit_lexical(declaration, prefix)
            return
        for child in node.named_children:
            self.visit(child, prefix)

    def _visit_class(self, node: Node, prefix: str) -> None:
        """Record a class and visit its members."""
        raw_name = _node_text(node.child_by_field_name("name"), self.source)
        name = self._qualified(prefix, raw_name)
        self._append(node, name, "class")
        body = node.child_by_field_name("body")
        if not body:
            return
        for child in body.named_children:
            self._visit_class_member(child, name)

    def _visit_class_member(self, node: Node, class_name: str) -> None:
        """Record methods and arrow fields, or recurse into other members."""
        if node.type == "method_definition":
            self._visit_method(node, class_name)
        elif node.type == "public_field_definition":
            self._visit_public_field(node, class_name)
        else:
            self.visit(node, class_name)

    def _visit_method(self, node: Node, class_name: str) -> None:
        """Record a class method and visit declarations in its body."""
        method_name = _node_text(node.child_by_field_name("name"), self.source)
        name = f"{class_name}.{method_name}"
        self._append(node, name, "function", node.child_by_field_name("parameters"))
        for descendant in node.named_children:
            self.visit(descendant, name)

    def _visit_public_field(self, node: Node, class_name: str) -> None:
        """Record a public class field when its value is an arrow function."""
        value = node.child_by_field_name("value")
        if not value or value.type != "arrow_function":
            return
        field_name = _node_text(node.child_by_field_name("name"), self.source)
        name = f"{class_name}.{field_name}"
        parameters = value.child_by_field_name("parameters") or value.child_by_field_name(
            "parameter"
        )
        self._append(node, name, "function", parameters)
        for descendant in value.named_children:
            self.visit(descendant, name)

    def _visit_function(self, node: Node, prefix: str) -> None:
        """Record a function declaration and visit its body."""
        raw_name = _node_text(node.child_by_field_name("name"), self.source)
        name = self._qualified(prefix, raw_name)
        self._append(node, name, "function", node.child_by_field_name("parameters"))
        body = node.child_by_field_name("body")
        if body:
            self.visit(body, name)

    def _visit_lexical(self, node: Node, prefix: str) -> None:
        """Record arrow functions declared with ``let`` or ``const``."""
        for declarator in node.named_children:
            self._visit_declarator(declarator, prefix)

    def _visit_declarator(self, node: Node, prefix: str) -> None:
        """Record one arrow-function variable declarator."""
        value = node.child_by_field_name("value")
        if node.type != "variable_declarator" or not value or value.type != "arrow_function":
            return
        raw_name = _node_text(node.child_by_field_name("name"), self.source)
        name = self._qualified(prefix, raw_name)
        parameters = value.child_by_field_name("parameters") or value.child_by_field_name(
            "parameter"
        )
        self._append(node, name, "function", parameters)
        for descendant in value.named_children:
            self.visit(descendant, name)


def javascript_symbols(
    content: str, suffix: str = ".js", filename: str = "<unknown>"
) -> list[Symbol]:
    """Discover JavaScript and TypeScript declarations through their concrete syntax tree."""
    source = content.encode()
    language = _TSX if suffix == ".tsx" else _TYPESCRIPT if suffix == ".ts" else _JAVASCRIPT
    tree = Parser(language).parse(source)
    _raise_javascript_syntax_error(tree.root_node, content, filename)
    visitor = _JavaScriptSymbolVisitor(source, content.splitlines())
    visitor.visit(tree.root_node)
    return _unique_symbol_names(visitor.found)


def discover(content: str, suffix: str, filename: str = "<unknown>") -> list[Symbol]:
    """Select Python or JavaScript-family symbol discovery by suffix."""
    return (
        python_symbols(content, filename)
        if suffix == ".py"
        else javascript_symbols(content, suffix, filename)
    )


def eligible(symbol: Symbol, coverage: str) -> bool:
    """Return whether a symbol is eligible under the coverage policy."""
    if coverage == "all":
        return True
    if coverage == "minimal":
        return (
            not symbol.has_doc
            and (
                symbol.kind == "module"
                or ("." not in symbol.name and not symbol.name.startswith("_"))
            )
        )
    return not symbol.has_doc


def needs_documentation(symbol: Symbol, coverage: str) -> bool:
    """Return whether a symbol is eligible under the selected coverage policy."""
    return eligible(symbol, coverage)


def source_for_symbol(
    content: str, symbol: Symbol, suffix: str, filename: str = "<unknown>"
) -> str:
    """Return the smallest self-contained source region available for one symbol.

    Python and JavaScript-family symbols use parser-derived ranges. Decorated Python declarations
    include their decorators. Module documentation remains file-scoped.
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
    """Render a PEP 257 docstring or JSDoc block for a source symbol."""
    if isinstance(documentation, Documentation):
        description = documentation.description
        argument_docs = documentation.arguments
    else:
        description = documentation
        argument_docs = {}
    description = " ".join(description.split()).strip() or f"Describe {symbol.name}."
    description = description[:1].upper() + description[1:]
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

    source_rows = escaped.splitlines()
    first_line_width = max(line_length - len(indentation) - 3, 20)
    summary, detail = _split_docstring_summary(source_rows[0], first_line_width)
    logical_rows = [summary]
    if detail:
        logical_rows.extend(("", detail))
    logical_rows.extend(source_rows[1:])

    rows: list[str] = []
    for index, row in enumerate(logical_rows):
        if not row:
            rows.append("")
            continue
        available = line_length - len(indentation) - (3 if index == 0 else 0)
        rows.extend(_wrap_documentation_line(row, max(available, 20)))
    return '"""' + "\n".join(rows) + '\n\n"""'


def _split_docstring_summary(content: str, width: int) -> tuple[str, str | None]:
    """Split an overlong summary into a PEP 257 summary and detailed description."""
    if len(content) <= width:
        return content, None
    split_at = _summary_split_index(content, width)
    summary = content[:split_at].rstrip(" ,;:")
    detail = content[split_at:].lstrip(" ,;:")
    if not summary.endswith((".", "?", "!")):
        summary = summary.rstrip("?!") + "."
    if detail:
        detail = detail[:1].upper() + detail[1:]
    return summary, detail or None


def _summary_split_index(content: str, width: int) -> int:
    """Choose a readable boundary that leaves room for summary punctuation."""
    usable_width = max(width - 1, 10)
    minimum = min(max(usable_width // 3, 12), usable_width)
    for index, character in enumerate(content[:usable_width]):
        if index >= minimum and character in ".!?" and content[index + 1 : index + 2] == " ":
            return index + 1
    clause = max(content.rfind(mark, minimum, usable_width) for mark in (",", ";", ":"))
    if clause >= minimum:
        return clause
    for conjunction in (" and ", " or ", " that ", " which ", " while ", " e ", " que "):
        boundary = content.rfind(conjunction, minimum, usable_width)
        if boundary >= minimum:
            return boundary + 1
    boundary = content.rfind(" ", minimum, usable_width)
    return boundary if boundary >= minimum else usable_width


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
