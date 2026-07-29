"""Language-aware source symbol discovery and deterministic documentation rendering."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass


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


def python_symbols(content: str) -> list[Symbol]:
    """Analisa uma string de conteúdo Python e retorna uma lista de objetos Symbol que representam os símbolos definidos.
    
    Args:
        content: Description of content."""
    tree = ast.parse(content)
    lines = content.splitlines()
    found: list[Symbol] = []
    module_first = tree.body[0] if tree.body else None
    module_doc = isinstance(module_first, ast.Expr) and isinstance(getattr(module_first, "value", None), ast.Constant) and isinstance(module_first.value.value, str)
    found.append(Symbol("module", "module", 0, 0, "", (), module_doc, module_first.lineno if module_doc else None, module_first.end_lineno if module_doc else None))

    def visit(nodes: list[ast.stmt], prefix: str = "") -> None:
        """Função auxiliar recursiva usada para percorrer nós AST e identificar símbolos aninhados (como métodos ou classes internas).
        
        Args:
            nodes: Description of nodes.
            prefix: Description of prefix."""
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
    """Analisa uma string de conteúdo JavaScript usando expressões regulares para extrair funções, classes e métodos JS.
    
    Args:
        content: Description of content."""
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


def discover(content: str, suffix: str) -> list[Symbol]:
    """Determina qual função de análise de símbolos deve ser utilizada (`python` ou `javascript`) com base na extensão do arquivo fornecida.
    
    Args:
        content: Description of content.
        suffix: Description of suffix."""
    return python_symbols(content) if suffix == ".py" else javascript_symbols(content)


def eligible(symbol: Symbol, coverage: str) -> bool:
    """Verifica se um símbolo é considerado elegível para documentação, geralmente baseado em critérios de cobertura de testes ('all').
    
    Args:
        symbol: Description of symbol.
        coverage: Description of coverage."""
    return coverage == "all" or not symbol.has_doc


def source_for_symbol(content: str, symbol: Symbol, suffix: str) -> str:
    """Return the smallest self-contained source region available for one symbol.

    Python symbols have AST-derived end lines. JavaScript discovery is intentionally
    lightweight, so its region ends when its braces balance (or at a semicolon for
    expression-bodied arrow functions). Module documentation remains file-scoped.
    """
    if symbol.kind == "module":
        return content
    lines = content.splitlines(keepends=True)
    start = symbol.line - 1
    if suffix == ".py":
        while start > 0 and lines[start - 1].lstrip().startswith("@"):
            start -= 1
        return "".join(lines[start:symbol.end_line])

    depth = 0
    opened = False
    for index in range(start, len(lines)):
        line = lines[index]
        depth += line.count("{") - line.count("}")
        opened = opened or "{" in line
        if (opened and depth <= 0) or (not opened and ";" in line):
            return "".join(lines[start:index + 1])
    return "".join(lines[start:])


def render(symbol: Symbol, description: str, suffix: str, python_format: str) -> str:
    """Formata a descrição textual de um símbolo (docstring) no formato apropriado, seja ele Python docstrings ou JSDoc.
    
    Args:
        symbol: Description of symbol.
        description: Description of description.
        suffix: Description of suffix.
        python_format: Description of python_format."""
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
