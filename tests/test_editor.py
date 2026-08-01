"""Test editor behavior.

Módulo de testes unitários para verificar o comportamento do sistema de geração e edição de
documentação 'doc_gub', cobrindo diversos cenários como formatação Python, aninhamento assíncrono, e
substituição/preservação de JSDoc.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from doc_gub import editor
from doc_gub.config import load
from doc_gub.editor import apply, prepare
from doc_gub.errors import DocGubError
from doc_gub.symbols import Documentation, discover, source_for_symbol


@pytest.mark.parametrize(
    ("fmt", "marker"),
    [("google", "Args:"), ("numpy", "Parameters"), ("sphinx", ":param value:")],
)
def test_python_formats_and_stale_file_protection(tmp_path: Path, fmt: str, marker: str) -> None:
    """Test python formats and stale file protection.

    Testa o comportamento do sistema ao detectar alterações em arquivos após a fase de preparação
    (preview), garantindo que a aplicação falhe com um erro específico ('changed after preview') se
    o conteúdo for modificado concorrentemente.

    Args:
        tmp_path: Description of tmp_path.
        fmt: Description of fmt.
        marker: Description of marker.
    """
    path = tmp_path / "sample.py"
    source = "def calculate(value):\n    return value * 2\n"
    path.write_text(source, encoding="utf-8")
    settings = load(tmp_path, python_format=fmt, selection="repository")

    prepared = prepare(
        path,
        discover(source, ".py"),
        {
            "module": Documentation("Module docs."),
            "calculate": Documentation("Calculate twice.", {"value": "Value to be doubled."}),
        },
        settings,
    )

    assert marker in prepared.after
    assert 'Value to be doubled.\n\n    """\n' in prepared.after
    assert "Description of value." not in prepared.after
    assert not any(line.isspace() for line in prepared.after.splitlines())
    path.write_text("# changed concurrently\n" + source, encoding="utf-8")
    with pytest.raises(DocGubError, match="changed after preview"):
        apply(prepared)


def test_python_nested_async_and_jsdoc(tmp_path: Path) -> None:
    """Test python nested async and jsdoc.

    Verifica a descoberta de símbolos em código Python assíncrono aninhado e JavaScript com
    JSDoc, garantindo que os métodos e módulos sejam corretamente identificados.

    Args:
        tmp_path: Description of tmp_path.
    """
    python = "class Service:\n    async def fetch(self, item):\n        return item\n"
    symbols = discover(python, ".py")
    assert {item.name for item in symbols} >= {"module", "Service", "Service.fetch"}

    javascript = "export const add = (left, right) => left + right;\n"
    path = tmp_path / "sample.js"
    path.write_text(javascript, encoding="utf-8")
    result = prepare(
        path,
        discover(javascript, ".js"),
        {
            "add": Documentation(
                "Add two values.",
                {"left": "First addend.", "right": "Second addend."},
            )
        },
        load(tmp_path),
    )

    assert "@param {any} left First addend." in result.after
    assert "@param {any} right Second addend." in result.after


def test_python_discovery_includes_every_parameter_kind() -> None:
    """Request documentation for positional-only, variadic, and keyword-only parameters."""
    source = (
        "class Service:\n"
        "    def work(self, first, /, second, *items, option, **extra):\n"
        "        return None\n"
    )

    method = next(symbol for symbol in discover(source, ".py") if symbol.name == "Service.work")

    assert method.args == ("first", "second", "items", "option", "extra")


def test_javascript_class_methods_are_discovered_with_unique_names() -> None:
    """Discover class methods and keep same-named methods unambiguous."""
    source = (
        "class First {\n"
        "  run(value) { return value; }\n"
        "}\n"
        "class Second {\n"
        "  run(value) { return value; }\n"
        "}\n"
    )

    symbols = discover(source, ".js")

    assert [symbol.name for symbol in symbols] == ["First", "First.run", "Second", "Second.run"]
    assert [symbol.args for symbol in symbols if symbol.name.endswith(".run")] == [
        ("value",),
        ("value",),
    ]


def test_javascript_discovery_and_symbol_scope_ignore_braces_in_strings() -> None:
    """Braces in JavaScript literals do not close a class or function scope."""
    source = (
        "class Service {\n"
        '  label = "}";\n'
        "  first() {}\n"
        "  second() {}\n"
        "}\n\n"
        "function describe() {\n"
        '  const brace = "}";\n'
        "  return brace;\n"
        "}\n\n"
        "function next() {}\n"
    )

    symbols = discover(source, ".js")
    describe = next(symbol for symbol in symbols if symbol.name == "describe")

    assert [symbol.name for symbol in symbols] == [
        "Service",
        "Service.first",
        "Service.second",
        "describe",
        "next",
    ]
    assert source_for_symbol(source, describe, ".js") == (
        'function describe() {\n  const brace = "}";\n  return brace;\n}\n'
    )


def test_typescript_and_tsx_discovery_use_syntax_trees() -> None:
    """Discover generic methods and destructured JSX arrow-function parameters."""
    typescript = (
        "export default class Repository<T> {\n"
        "  async save(value: T, ...items: T[]): Promise<T> { return value; }\n"
        "}\n"
    )
    tsx = "export const App = ({ name }: Props) => <main>{name}</main>;\n"

    ts_symbols = discover(typescript, ".ts")
    tsx_symbols = discover(tsx, ".tsx")

    assert [symbol.name for symbol in ts_symbols] == ["Repository", "Repository.save"]
    assert ts_symbols[1].args == ("value", "items")
    assert [(symbol.name, symbol.args) for symbol in tsx_symbols] == [("App", ("name",))]


def test_javascript_syntax_errors_include_file_and_location() -> None:
    """Reject malformed JavaScript before documentation generation begins."""
    with pytest.raises(SyntaxError) as raised:
        discover("export function broken( {\n", ".js", "broken.js")

    assert raised.value.filename == "broken.js"
    assert raised.value.lineno == 1
    assert raised.value.offset is not None


def test_typescript_discovers_class_arrow_fields_and_nested_classes() -> None:
    """Qualify callable fields and nested classes with their containing class."""
    source = (
        "class Outer {\n"
        "  callback = (value: string) => value;\n"
        "  method() {\n"
        "    class Inner { run() {} }\n"
        "  }\n"
        "}\n"
    )

    symbols = discover(source, ".ts")

    assert [symbol.name for symbol in symbols] == [
        "Outer",
        "Outer.callback",
        "Outer.method",
        "Outer.Inner",
        "Outer.Inner.run",
    ]
    assert next(symbol for symbol in symbols if symbol.name == "Outer.callback").args == ("value",)


def test_python_docstrings_follow_the_nearest_ruff_line_length(tmp_path: Path) -> None:
    """Verifica o limite de linha definido pelo Ruff.

    As docstrings de funções devem seguir o limite de 52
    caracteres, mesmo quando há descrições longas.

    Args:
        tmp_path: Description of tmp_path.
    """
    project = tmp_path / "python-project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[tool.ruff]\nline-length = 52\n", encoding="utf-8")
    path = project / "sample.py"
    source = "def calculate(value):\n    return value\n"
    path.write_text(source, encoding="utf-8")

    prepared = prepare(
        path,
        discover(source, ".py"),
        {
            "calculate": (
                "Calculate a value using a deliberately long description that must wrap safely"
            )
        },
        load(project),
        selected_symbols=[
            next(item for item in discover(source, ".py") if item.name == "calculate")
        ],
    )

    assert all(len(line) <= 52 for line in prepared.after.splitlines())
    ast.parse(prepared.after)


def test_javascript_docstrings_follow_the_nearest_eslint_max_len(tmp_path: Path) -> None:
    """Testa se as docstrings de JavaScript seguem o limite máximo de caracteres do ESLint.

    Args:
        tmp_path: Caminho temporário para arquivos e diretórios.
    """
    project = tmp_path / "javascript-project"
    project.mkdir()
    (project / "eslint.config.js").write_text(
        'export default [{ rules: { "max-len": ["error", { code: 48 }] } }];\n',
        encoding="utf-8",
    )
    path = project / "sample.js"
    source = "export function calculate(value) {\n  return value;\n}\n"
    path.write_text(source, encoding="utf-8")

    prepared = prepare(
        path,
        discover(source, ".js"),
        {
            "calculate": (
                "Calculate a value using a deliberately long description that must wrap safely"
            )
        },
        load(project),
    )

    assert all(len(line) <= 48 for line in prepared.after.splitlines())
    assert " * Calculate a value" in prepared.after


def test_only_documentation_is_changed_and_existing_jsdoc_can_be_replaced(tmp_path: Path) -> None:
    """Test only documentation is changed and existing jsdoc can be replaced.

    Testa que a documentação de módulos e funções pode ser atualizada ou substituída, tanto em
    arquivos Python quanto JavaScript.

    Args:
        tmp_path: Description of tmp_path.
    """
    python_path = tmp_path / "safe.py"
    python = (
        "#!/usr/bin/env python3\n# coding: utf-8\ndef calculate(value):\n    return value * 2\n"
    )
    python_path.write_text(python, encoding="utf-8")
    prepared = prepare(
        python_path,
        discover(python, ".py"),
        {"module": "Module docs.", "calculate": "Double."},
        load(tmp_path),
    )

    assert prepared.after.startswith(
        '#!/usr/bin/env python3\n# coding: utf-8\n"""Module docs."""\n\n'
    )
    assert 'def calculate(value):\n    """Double.' in prepared.after
    assert "    return value * 2\n" in prepared.after
    ast.parse(prepared.after)

    js_path = tmp_path / "safe.js"
    javascript = "/**\n * Old docs.\n */\nexport const add = (left, right) => left + right;\n"
    js_path.write_text(javascript, encoding="utf-8")
    replaced = prepare(
        js_path,
        discover(javascript, ".js"),
        {"add": "Add two values."},
        load(tmp_path, coverage="all", existing_docs="replace", selection="repository"),
    )

    assert "Old docs." not in replaced.after
    assert "export const add = (left, right) => left + right;" in replaced.after


def test_typescript_validation_uses_tsc_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test typescript validation uses tsc when available.

    Verifica se a validação de TypeScript utiliza o compilador `tsc` quando ele está disponível
    no ambiente, simulando o comportamento do sistema operacional e da biblioteca subjacente.

    Args:
        tmp_path: Description of tmp_path.
        monkeypatch: Description of monkeypatch.
    """
    captured: list[str] = []
    monkeypatch.setattr(
        editor.shutil, "which", lambda name: "/usr/local/bin/tsc" if name == "tsc" else None
    )

    def fake_run(command: list[str], **_: object) -> object:
        """Simula a execução de um comando e registra os argumentos em 'captured'.

        Retorna um objeto simulado de resultado com código de retorno 0.

        Args:
                    command: Description of command.
        """
        captured.extend(command)
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(editor.subprocess, "run", fake_run)
    editor._validate_javascript("const value: number = 1;\n", ".ts", tmp_path / "sample.ts")

    assert captured[:5] == ["/usr/local/bin/tsc", "--noEmit", "--noCheck", "--pretty", "false"]


def test_jsx_validation_uses_typescript_compiler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validate JSX with a JSX-aware compiler instead of Node's JS-only checker."""
    captured: list[str] = []
    monkeypatch.setattr(editor.shutil, "which", lambda _: "/usr/local/bin/tsc")

    def fake_run(command: list[str], **_kwargs: object) -> object:
        """Executa um comando simulado e captura os argumentos.

        Args:
            command: A lista de strings que representa o comando a ser executado.
        """
        captured.extend(command)
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(
        editor.subprocess,
        "run",
        fake_run,
    )

    editor._validate_javascript("export const App = () => <div />;\n", ".jsx", tmp_path / "App.jsx")

    assert captured[0] == "/usr/local/bin/tsc"
    assert ["--jsx", "preserve"] == captured[6:8]


def test_inline_python_suites_are_left_untouched(tmp_path: Path) -> None:
    """Test inline python suites are left untouched.

    Verifica que o conteúdo de um arquivo Python criado temporariamente não é alterado durante o
    processo de preparação, mesmo quando funções são descobertas e preparadas.

    Args:
        tmp_path: Description of tmp_path.
    """
    path = tmp_path / "inline.py"
    source = "def ready(): return True\n"
    path.write_text(source, encoding="utf-8")
    function = next(item for item in discover(source, ".py") if item.name == "ready")

    prepared = prepare(path, [function], {"ready": "Return readiness."}, load(tmp_path))

    assert prepared.after == source
    assert prepared.changed == ()
    assert prepared.ignored == (function,)


def test_python_docstring_is_inserted_after_a_multiline_signature(tmp_path: Path) -> None:
    """Test python docstring is inserted after a multiline signature.

    Verifica se o docstring é corretamente inserido após uma assinatura de função que ocupa
    múltiplas linhas, simulando um cenário de análise de código Python.

    Args:
        tmp_path: Description of tmp_path.
    """
    path = tmp_path / "test_cli.py"
    source = (
        "def test_loading_reports_progress_when_stderr_is_not_a_terminal(\n"
        "    capsys: pytest.CaptureFixture[str],\n"
        ") -> None:\n"
        "    pass\n"
    )
    path.write_text(source, encoding="utf-8")
    function = next(item for item in discover(source, ".py") if item.kind == "function")

    prepared = prepare(
        path,
        [function],
        {function.name: "Report loading progress."},
        load(tmp_path),
    )

    assert (
        '    capsys: pytest.CaptureFixture[str],\n) -> None:\n    """Report loading progress.'
    ) in prepared.after
    ast.parse(prepared.after)


def test_class_docstring_is_inserted_before_decorated_first_method(tmp_path: Path) -> None:
    """Test class docstring is inserted before decorated first method.

    Verifica se a docstring da classe é inserida antes do primeiro método decorado na saída do
    código.

    Args:
        tmp_path: Description of tmp_path.
    """
    path = tmp_path / "finance.py"
    source = (
        "class FinancialCalculator:\n"
        "    @staticmethod\n"
        "    def compound_interest(capital: float) -> float:\n"
        "        return capital * 1.1\n"
    )
    path.write_text(source, encoding="utf-8")
    symbols = discover(source, ".py")
    target = next(item for item in symbols if item.name == "FinancialCalculator")

    prepared = prepare(
        path,
        symbols,
        {target.name: "Perform financial calculations."},
        load(tmp_path),
        selected_symbols=[target],
    )

    assert (
        "class FinancialCalculator:\n"
        '    """Perform financial calculations."""\n'
        "\n"
        "    @staticmethod\n"
        "    def compound_interest(capital: float) -> float:\n"
    ) in prepared.after
    ast.parse(prepared.after)


def test_generated_python_docstrings_escape_quotes_and_backslashes(tmp_path: Path) -> None:
    """Test generated python docstrings escape quotes and backslashes.

    Verifica se o docstring gerado é corretamente escapado, lidando com aspas e barras
    invertidas.

    Args:
        tmp_path: Description of tmp_path.
    """
    path = tmp_path / "safe_description.py"
    source = "def explain():\n    return True\n"
    path.write_text(source, encoding="utf-8")

    prepared = prepare(
        path,
        discover(source, ".py"),
        {"module": 'Explain a \\\\path containing """quotes""".'},
        load(tmp_path),
        selected_symbols=[next(item for item in discover(source, ".py") if item.kind == "module")],
    )

    parsed = ast.parse(prepared.after)
    assert ast.get_docstring(parsed) == 'Explain a \\\\path containing """quotes""".'


def test_generated_python_syntax_errors_are_identified_as_validation_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test generated python syntax errors are identified as validation failures.

    Testa se erros de sintaxe Python gerados são identificados como falhas de validação, mesmo
    que o código fonte original não tenha sido alterado.

    Args:
        tmp_path: Description of tmp_path.
        monkeypatch: Description of monkeypatch.
    """
    path = tmp_path / "valid_source.py"
    source = "def ready():\n    return True\n"
    path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(editor, "render", lambda *_args: '"""unterminated')

    with pytest.raises(
        DocGubError,
        match="generated documentation failed Python validation.*source file was not changed",
    ):
        prepare(path, discover(source, ".py"), {"module": "Docs."}, load(tmp_path))

    assert path.read_text(encoding="utf-8") == source


def test_replaced_class_docstring_gets_pep257_spacing(tmp_path: Path) -> None:
    """Ensure replaced class docstrings retain the required surrounding spacing."""
    path = tmp_path / "service.py"
    source = 'class Service:\n    """Old docs."""\n    def run(self):\n        return True\n'
    path.write_text(source, encoding="utf-8")

    prepared = prepare(
        path,
        discover(source, ".py"),
        {"Service": "Provide service operations"},
        load(tmp_path, coverage="all", existing_docs="replace"),
    )

    assert '    """Provide service operations."""\n\n    def run' in prepared.after
    ast.parse(prepared.after)


def test_preserve_existing_documentation(tmp_path: Path) -> None:
    """Test preserve existing documentation.

    Verifica que a documentação existente seja preservada ao preparar o código, mesmo quando
    novas descrições são fornecidas para funções específicas.

    Args:
        tmp_path: Description of tmp_path.
    """
    path = tmp_path / "documented.py"
    source = 'def ready():\n    """Human-written description."""\n    return True\n'
    path.write_text(source, encoding="utf-8")
    settings = load(tmp_path, coverage="all", existing_docs="preserve", selection="repository")

    result = prepare(path, discover(source, ".py"), {"ready": "New description."}, settings)

    assert '"""Human-written description."""' in result.after
    assert "New description." not in result.after
