"""Test editor behavior."""

from __future__ import annotations

import ast
import os
import stat
import subprocess
import sys
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
    """Verify python formats and stale file protection."""
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


def test_apply_rejects_a_file_replaced_by_a_symlink(tmp_path: Path) -> None:
    """Verify apply rejects a file replaced by a symlink."""
    path = tmp_path / "source.py"
    external = tmp_path / "external.py"
    source = "def work():\n    return True\n"
    path.write_text(source, encoding="utf-8")
    external.write_text(source, encoding="utf-8")
    symbols = discover(source, ".py")
    prepared = prepare(
        path,
        symbols,
        {symbol.name: "Generated documentation." for symbol in symbols},
        load(tmp_path),
    )
    path.unlink()
    path.symlink_to(external)

    with pytest.raises(DocGubError, match="became a symbolic link"):
        apply(prepared)

    assert external.read_text(encoding="utf-8") == source


def test_apply_preserves_crlf_and_file_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify apply preserves crlf and file permissions."""
    path = tmp_path / "source.py"
    source = b"def work():\r\n    return True\r\n"
    path.write_bytes(source)
    path.chmod(0o640)
    content = source.decode("utf-8")
    symbols = discover(content, ".py")
    prepared = prepare(
        path,
        symbols,
        {symbol.name: "Generated documentation." for symbol in symbols},
        load(tmp_path),
    )
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def tracked_replace(source_path: str | Path, destination: str | Path) -> None:
        """Tracked replace."""
        replacements.append((Path(source_path), Path(destination)))
        real_replace(source_path, destination)

    monkeypatch.setattr(editor.os, "replace", tracked_replace)

    apply(prepared)

    result = path.read_bytes()
    assert b"\r\n" in result
    assert b"\n" not in result.replace(b"\r\n", b"")
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert replacements and replacements[0][0].parent == path.parent


def test_atomic_replace_failures_leave_the_original_source_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify atomic replacement failures become actionable domain errors."""
    path = tmp_path / "sample.py"
    source = "def calculate():\n    return 1\n"
    path.write_text(source, encoding="utf-8")
    symbols = discover(source, ".py")
    target = next(item for item in symbols if item.name == "calculate")
    prepared = prepare(
        path,
        symbols,
        {"calculate": Documentation("Calculate a value.")},
        load(tmp_path),
        selected_symbols=[target],
    )

    def fail_replace(_source: str | Path, _destination: str | Path) -> None:
        """Simulate a busy destination file."""
        raise OSError("busy")

    monkeypatch.setattr(editor.os, "replace", fail_replace)

    with pytest.raises(DocGubError, match="unable to apply the atomic file update: busy"):
        apply(prepared)

    assert path.read_text(encoding="utf-8") == source


def test_python_nested_async_and_jsdoc(tmp_path: Path) -> None:
    """Verify python nested async and jsdoc."""
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
    """Verify python discovery includes every parameter kind."""
    source = (
        "class Service:\n"
        "    def work(self, first, /, second, *items, option, **extra):\n"
        "        return None\n"
    )

    method = next(symbol for symbol in discover(source, ".py") if symbol.name == "Service.work")

    assert method.args == ("first", "second", "items", "option", "extra")


def test_javascript_class_methods_are_discovered_with_unique_names() -> None:
    """Verify javascript class methods are discovered with unique names."""
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
    """Verify javascript discovery and symbol scope ignore braces in strings."""
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
    """Verify typescript and tsx discovery use syntax trees."""
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
    """Verify javascript syntax errors include file and location."""
    with pytest.raises(SyntaxError) as raised:
        discover("export function broken( {\n", ".js", "broken.js")

    assert raised.value.filename == "broken.js"
    assert raised.value.lineno == 1
    assert raised.value.offset is not None


def test_typescript_discovers_class_arrow_fields_and_nested_classes() -> None:
    """Verify typescript discovers class arrow fields and nested classes."""
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
        "Outer.method.Inner",
        "Outer.method.Inner.run",
    ]
    assert next(symbol for symbol in symbols if symbol.name == "Outer.callback").args == ("value",)


def test_javascript_nested_and_redefined_symbols_have_unique_names() -> None:
    """Verify javascript nested and redefined symbols have unique names."""
    content = (
        "class Service {\n"
        "  first() { function helper() {} }\n"
        "  second() { function helper() {} }\n"
        "}\n"
        "function repeated() {}\n"
        "function repeated() {}\n"
    )

    symbols = discover(content, ".js")
    names = [symbol.name for symbol in symbols]

    assert "Service.first.helper" in names
    assert "Service.second.helper" in names
    assert "repeated@L5:1" in names
    assert "repeated@L6:1" in names
    assert len(names) == len(set(names))


def test_python_docstrings_follow_the_nearest_ruff_line_length(tmp_path: Path) -> None:
    """Verify python docstrings follow the nearest ruff line length."""
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


def test_prepared_diff_uses_the_relative_display_path(tmp_path: Path) -> None:
    """Verify previews avoid machine-specific absolute paths when one is available."""
    path = tmp_path / "sample.py"
    source = "def calculate():\n    return 1\n"
    path.write_text(source, encoding="utf-8")
    symbols = discover(source, ".py")
    prepared = prepare(
        path,
        symbols,
        {"calculate": Documentation("Calculate a value.")},
        load(tmp_path),
        display_path=Path("package/sample.py"),
    )

    assert "--- a/package/sample.py" in prepared.diff
    assert "+++ b/package/sample.py" in prepared.diff
    assert str(tmp_path) not in prepared.diff


def test_python_rendering_options_support_extended_ruff_toml(tmp_path: Path) -> None:
    """Verify Ruff TOML files and inherited pydocstyle configuration are respected."""
    project = tmp_path / "python-project"
    package = project / "package"
    package.mkdir(parents=True)
    (project / "base.toml").write_text(
        "[lint.pydocstyle]\nconvention = \"numpy\"\n", encoding="utf-8"
    )
    (project / "ruff.toml").write_text(
        'extend = "base.toml"\nline-length = 52\n', encoding="utf-8"
    )
    (package / "pyproject.toml").write_text("[project]\nname = \"package\"\n", encoding="utf-8")

    assert editor._python_rendering_options(package / "sample.py", "google") == (52, "numpy")


def test_long_python_summary_passes_the_project_d205_rule(tmp_path: Path) -> None:
    """Verify long summaries have a blank line before their detailed description."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\n"
        "line-length = 100\n"
        "[tool.ruff.lint]\n"
        'select = ["D", "E501"]\n'
        "[tool.ruff.lint.pydocstyle]\n"
        'convention = "google"\n',
        encoding="utf-8",
    )
    path = tmp_path / "sample.py"
    source = (
        '"""Document the sample module."""\n\n'
        "def fake_documentation(_source, _symbols, settings):\n"
        "    return {}\n"
    )
    path.write_text(source, encoding="utf-8")
    symbols = discover(source, ".py")
    target = next(item for item in symbols if item.name == "fake_documentation")
    prepared = prepare(
        path,
        symbols,
        {
            target.name: Documentation(
                "Simula o processo de documentação, adicionando um modelo ao histórico e "
                "retornando uma estrutura de dicionário com informações sobre os símbolos.",
                {
                    "_source": "O conteúdo da fonte a ser processada.",
                    "_symbols": "Os símbolos encontrados no código.",
                    "settings": "As configurações operacionais.",
                },
            )
        },
        load(tmp_path),
        selected_symbols=[target],
    )
    path.write_text(prepared.after, encoding="utf-8")

    assert (
        '    """Simula o processo de documentação.\n\n    Adicionando um modelo ao histórico'
    ) in prepared.after
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", str(path)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_python_docstrings_capitalize_generated_summaries_for_ruff(tmp_path: Path) -> None:
    """Verify generated summaries meet Ruff's D403 capitalization requirement."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff.lint]\n"
        'select = ["D"]\n'
        "[tool.ruff.lint.pydocstyle]\n"
        'convention = "google"\n',
        encoding="utf-8",
    )
    path = tmp_path / "sample.py"
    source = '\"\"\"Calculate sample values.\"\"\"\n\n\ndef calculate():\n    return 1\n'
    path.write_text(source, encoding="utf-8")
    symbols = discover(source, ".py")
    target = next(item for item in symbols if item.name == "calculate")
    prepared = prepare(
        path,
        symbols,
        {target.name: Documentation("retorna o valor calculado.")},
        load(tmp_path),
        selected_symbols=[target],
    )
    path.write_text(prepared.after, encoding="utf-8")

    assert '"""Retorna o valor calculado."""' in prepared.after
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", str(path)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_python_docstrings_follow_the_project_pydocstyle_convention(tmp_path: Path) -> None:
    """Verify the nearest Ruff convention selects the generated parameter section style."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.ruff.lint.pydocstyle]\nconvention = "numpy"\n', encoding="utf-8"
    )
    path = tmp_path / "sample.py"
    source = "def calculate(value):\n    return value\n"
    path.write_text(source, encoding="utf-8")
    symbols = discover(source, ".py")
    target = next(item for item in symbols if item.name == "calculate")

    prepared = prepare(
        path,
        symbols,
        {target.name: Documentation("Calculate a value.", {"value": "Input value."})},
        load(tmp_path, python_format="google"),
        selected_symbols=[target],
    )

    assert "Parameters\n    ----------" in prepared.after
    assert "Args:" not in prepared.after


def test_prepare_reads_rendering_options_once_per_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify prepare reads project rendering options once per file."""
    path = tmp_path / "source.py"
    source = "def first():\n    pass\n\ndef second():\n    pass\n"
    path.write_text(source, encoding="utf-8")
    calls: list[Path] = []

    def rendering_options(candidate: Path, _settings: object) -> tuple[int, str]:
        """Record rendering option lookups."""
        calls.append(candidate)
        return 88, "google"

    monkeypatch.setattr(editor, "_rendering_options", rendering_options)
    symbols = discover(source, ".py")
    prepare(
        path,
        symbols,
        {symbol.name: "Generated documentation." for symbol in symbols},
        load(tmp_path),
    )

    assert calls == [path]


def test_javascript_docstrings_follow_the_nearest_eslint_max_len(tmp_path: Path) -> None:
    """Verify javascript docstrings follow the nearest eslint max len."""
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
    """Verify only documentation is changed and existing jsdoc can be replaced."""
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
    """Verify typescript validation uses tsc when available."""
    captured: list[str] = []
    monkeypatch.setattr(
        editor.shutil, "which", lambda name: "/usr/local/bin/tsc" if name == "tsc" else None
    )

    def fake_run(command: list[str], **_: object) -> object:
        """Fake run."""
        captured.extend(command)
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(editor.subprocess, "run", fake_run)
    editor._validate_javascript("const value: number = 1;\n", ".ts", tmp_path / "sample.ts")

    assert captured[:5] == ["/usr/local/bin/tsc", "--noEmit", "--noCheck", "--pretty", "false"]


def test_javascript_validation_reports_missing_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify javascript validation reports missing node."""
    path = tmp_path / "source.js"
    source = "function work() { return true; }\n"
    path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(editor.shutil, "which", lambda _name: None)

    with pytest.raises(DocGubError, match="requires `node` on PATH"):
        prepare(
            path,
            discover(source, ".js"),
            {"work": "Perform work."},
            load(tmp_path),
        )


def test_jsx_validation_uses_typescript_compiler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify jsx validation uses typescript compiler."""
    captured: list[str] = []
    monkeypatch.setattr(editor.shutil, "which", lambda _: "/usr/local/bin/tsc")

    def fake_run(command: list[str], **_kwargs: object) -> object:
        """Fake run."""
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
    """Verify inline python suites are left untouched."""
    path = tmp_path / "inline.py"
    source = "def ready(): return True\n"
    path.write_text(source, encoding="utf-8")
    function = next(item for item in discover(source, ".py") if item.name == "ready")

    prepared = prepare(path, [function], {"ready": "Return readiness."}, load(tmp_path))

    assert prepared.after == source
    assert prepared.changed == ()
    assert prepared.ignored == (function,)


def test_python_docstring_is_inserted_after_a_multiline_signature(tmp_path: Path) -> None:
    """Verify python docstring is inserted after a multiline signature."""
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
    """Verify class docstring is inserted before decorated first method."""
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
    """Verify generated python docstrings escape quotes and backslashes."""
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
    """Verify generated python syntax errors are identified as validation failures."""
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
    """Verify replaced class docstring gets pep257 spacing."""
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
    """Verify preserve existing documentation."""
    path = tmp_path / "documented.py"
    source = 'def ready():\n    """Human-written description."""\n    return True\n'
    path.write_text(source, encoding="utf-8")
    settings = load(tmp_path, coverage="all", existing_docs="preserve", selection="repository")

    result = prepare(path, discover(source, ".py"), {"ready": "New description."}, settings)

    assert '"""Human-written description."""' in result.after
    assert "New description." not in result.after
