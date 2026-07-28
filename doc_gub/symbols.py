"""Language-aware source symbol discovery and deterministic documentation rendering."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    line: int
    end_line: int
    indent: str
    args: tuple[str, ...] = ()
    has_doc: bool = False
    doc_start: int | None = None
    doc_end: int | None = None


def python_symbols(content: str) -> list[Symbol]:
    tree = ast.parse(content)
    lines = content.splitlines()
    found: list[Symbol] = []
    module_first = tree.body[0] if tree.body else None
    module_doc = isinstance(module_first, ast.Expr) and isinstance(getattr(module_first, "value", None), ast.Constant) and isinstance(module_first.value.value, str)
    found.append(Symbol("module", "module", 0, 0, "", (), module_doc, module_first.lineno if module_doc else None, module_first.end_lineno if module_doc else None))

    def visit(nodes: list[ast.stmt], prefix: str = "") -> None:
        for node in nodes:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = f"{prefix}.{node.name}" if prefix else node.name
            first = node.body[0] if node.body else None
            has_doc = isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) and isinstance(first.value.value, str)
            args = tuple(arg.arg for arg in node.args.args if arg.arg not in {"self", "cls"}) if hasattr(node, "args") else ()
            found.append(Symbol(name, "class" if isinstance(node, ast.ClassDef) else "function", node.lineno, node.end_lineno or node.lineno, lines[node.lineno - 1][:len(lines[node.lineno - 1]) - len(lines[node.lineno - 1].lstrip())], args, has_doc, first.lineno if has_doc else None, first.end_lineno if has_doc else None))
            visit(node.body, name)
    visit(tree.body)
    return found


_JS = re.compile(r"^(?P<indent>\s*)(?:export\s+)?(?:(?:async\s+)?function\s+(?P<function>[$\w]+)|class\s+(?P<class>[$\w]+)|(?:(?:const|let|var)\s+)?(?P<arrow>[$\w]+)\s*=\s*(?:async\s*)?\((?P<args>[^)]*)\)\s*=>|(?P<method>[$\w]+)\s*\((?P<methodargs>[^)]*)\s*\{)")


def javascript_symbols(content: str) -> list[Symbol]:
    lines = content.splitlines()
    found: list[Symbol] = []
    for index, line in enumerate(lines):
        match = _JS.match(line)
        if not match:
            continue
        name = match.group("function") or match.group("class") or match.group("arrow") or match.group("method")
        kind = "class" if match.group("class") else "function"
        args = match.group("args") or match.group("methodargs") or ""
        arguments = tuple(item.strip().split("=")[0].strip() for item in args.split(",") if item.strip())
        previous = lines[index - 1].strip() if index else ""
        found.append(Symbol(name, kind, index + 1, index + 1, match.group("indent"), arguments, previous.endswith("*/")))
    return found


def discover(content: str, suffix: str) -> list[Symbol]:
    return python_symbols(content) if suffix == ".py" else javascript_symbols(content)


def eligible(symbol: Symbol, coverage: str) -> bool:
    return coverage == "all" or not symbol.has_doc


def render(symbol: Symbol, description: str, suffix: str, python_format: str) -> str:
    description = " ".join(description.split()).strip() or f"Describe {symbol.name}."
    if suffix != ".py":
        rows = ["/**", f" * {description}"]
        rows.extend(f" * @param {{any}} {arg} Description of {arg}." for arg in symbol.args)
        rows.append(" */")
        return "\n".join(rows)
    if symbol.kind in {"module", "class"} or not symbol.args:
        return f'"""{description}"""'
    if python_format == "numpy":
        body = [description, "", "Parameters", "----------"]
        body.extend(f"{arg} : Any\n    Description of {arg}." for arg in symbol.args)
    elif python_format == "sphinx":
        body = [description, ""] + [f":param {arg}: Description of {arg}." for arg in symbol.args]
    else:
        body = [description, "", "Args:"] + [f"    {arg}: Description of {arg}." for arg in symbol.args]
    return '\"\"\"' + "\n".join(body) + '\"\"\"'
