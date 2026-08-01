"""Test cli behavior."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from doc_code import cli
from doc_code.cli import _loading, _model_for_attempt, _undocumented_symbols
from doc_code.config import Settings
from doc_code.errors import AIProviderError, AITimeoutError, DocGubError
from doc_code.symbols import Documentation, Symbol, discover, source_for_symbol


def test_loading_reports_progress_when_stderr_is_not_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify loading reports progress when stderr is not a terminal."""
    with _loading("Generating documentation for [sample.py]"):
        pass

    assert capsys.readouterr().err == ""


def test_loading_draws_a_single_stable_line_in_a_terminal(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify loading draws a single stable line in a terminal."""
    monkeypatch.setattr(cli.sys.stderr, "isatty", lambda: True)

    with _loading("Generating documentation for [1/2 sample.py:first]"):
        pass

    assert capsys.readouterr().err.count("Generating documentation") == 1


def test_retries_cycle_through_model_candidates() -> None:
    """Verify retries cycle through model candidates."""
    candidates = ("model-one", "model-two")

    assert [_model_for_attempt(candidates, attempt) for attempt in range(1, 5)] == [
        "model-one",
        "model-two",
        "model-one",
        "model-two",
    ]


def test_check_finds_undocumented_symbols_without_calling_ai() -> None:
    """Verify check finds undocumented symbols without calling ai."""
    content = (
        '"""Module docs."""\n\ndef documented():\n    """Docs."""\n\ndef missing():\n    pass\n'
    )

    assert _undocumented_symbols(content, ".py") == ["missing"]


def test_reports_python_syntax_errors_with_file_line_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify reports python syntax errors with file line and source."""
    path = tmp_path / "test_cli.py"
    path.write_text("\n" * 16 + "def broken(:\n", encoding="utf-8")

    class Repo:
        """Provide a repository test double."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = tmp_path

    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["test_cli.py"])

    result = CliRunner().invoke(cli.app, ["--check"])

    assert result.exit_code == 1
    assert "Skipped documentation: test_cli.py" in result.output
    assert "Reason: test_cli.py:17: invalid syntax" in result.output
    assert "def broken(:" in result.output
    assert "^" in result.output


def test_check_reports_invalid_files_without_skipping_other_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify check reports invalid files without skipping other files."""
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    (tmp_path / "missing.py").write_text("def missing():\n    pass\n", encoding="utf-8")

    class Repo:
        """Provide a repository test double."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = tmp_path

    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["broken.py", "missing.py"])

    result = CliRunner().invoke(cli.app, ["--check"])

    assert result.exit_code == 1
    assert "Skipped documentation: broken.py" in result.output
    assert "missing.py: module, missing" in result.output


def test_generation_skips_invalid_files_and_continues_the_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify generation skips invalid files and continues the scope."""
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    valid = tmp_path / "valid.py"
    valid.write_text("def valid():\n    return True\n", encoding="utf-8")

    class Repo:
        """Provide a repository test double."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = tmp_path

    settings = Settings(confirm=False, models=("test",))
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: settings)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["broken.py", "valid.py"])
    monkeypatch.setattr(
        cli,
        "documentation_for",
        lambda _content, symbols, _settings: {symbol.name: "Generated docs." for symbol in symbols},
    )

    result = CliRunner().invoke(cli.app, ["broken.py", "valid.py"])

    assert result.exit_code == 1, result.output
    assert "Skipped documentation: broken.py" in result.output
    assert "valid.py" in result.output

    continued = CliRunner().invoke(cli.app, ["--continue-on-error", "broken.py", "valid.py"])
    assert continued.exit_code == 0, continued.output


def test_apply_writes_each_file_before_later_generation_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify apply writes each file before later generation failures."""
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("def first():\n    return True\n", encoding="utf-8")
    second_source = "def second():\n    return True\n"
    second.write_text(second_source, encoding="utf-8")

    class Repo:
        """Provide a repository test double."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = tmp_path

    settings = Settings(output="apply", confirm=False, selection="repository", models=("test",))
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: settings)

    def fake_resolve(_: object, paths: list[Path], __: Settings) -> list[str]:
        """Fake resolve."""
        assert paths == [Path("first.py"), Path("second.py")]
        return ["first.py", "second.py"]

    monkeypatch.setattr(cli, "resolve", fake_resolve)
    monkeypatch.setattr(cli, "MAX_AI_ATTEMPTS", 1)

    def fake_documentation(content: str, symbols: list[Symbol], _: Settings) -> dict[str, str]:
        """Fake documentation."""
        if "second" in content:
            raise AIProviderError("provider unavailable")
        return {symbol.name: "Generated docs." for symbol in symbols}

    monkeypatch.setattr(cli, "documentation_for", fake_documentation)

    result = CliRunner().invoke(cli.app, ["first.py", "second.py"])

    assert result.exit_code == 1, result.output
    assert '"""Generated docs."""' in first.read_text(encoding="utf-8")
    assert second.read_text(encoding="utf-8") == second_source
    assert "Applied documentation: first.py" in result.output
    assert "Skipped documentation: second.py" in result.output


def test_symbol_scope_contains_only_the_requested_python_function() -> None:
    """Verify symbol scope contains only the requested python function."""
    content = "def first():\n    return 1\n\ndef second():\n    return 2\n"
    second = next(symbol for symbol in discover(content, ".py") if symbol.name == "second")

    assert source_for_symbol(content, second, ".py") == "def second():\n    return 2\n"


def test_module_symbol_scope_uses_a_python_outline_without_function_bodies() -> None:
    """Verify module symbol scope uses a python outline without function bodies."""
    content = '''"""Utilities for values."""
import math

DEFAULT_SCALE = 2

def calculate(value):
    internal_detail = value * math.pi
    return internal_detail * DEFAULT_SCALE
'''
    module = next(symbol for symbol in discover(content, ".py") if symbol.kind == "module")

    scope = source_for_symbol(content, module, ".py")

    assert '"""Utilities for values."""' in scope
    assert "import math" in scope
    assert "CONSTANTS: DEFAULT_SCALE" in scope
    assert "def calculate(value):" in scope
    assert "internal_detail" not in scope


def test_symbol_request_scope_calls_the_model_once_per_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify symbol request scope calls the model once per target."""
    source = '"""Module docs."""\n\ndef first():\n    return 1\n\ndef second():\n    return 2\n'
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")

    class Repo:
        """Provide a repository test double."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = tmp_path

    settings = Settings(confirm=False, request_scope="symbol", models=("test",))
    received: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: settings)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["sample.py"])
    monkeypatch.setattr(cli, "MAX_AI_ATTEMPTS", 1)

    def fake_documentation(content: str, symbols: list[Symbol], _: Settings) -> dict[str, str]:
        """Fake documentation."""
        received.append((content, [symbol.name for symbol in symbols]))
        return {symbol.name: "Generated docs." for symbol in symbols}

    monkeypatch.setattr(cli, "documentation_for", fake_documentation)

    result = CliRunner().invoke(cli.app, ["sample.py"])

    assert result.exit_code == 0, result.output
    assert received == [
        ("def first():\n    return 1\n", ["first"]),
        ("def second():\n    return 2\n", ["second"]),
    ]
    assert "+++ b/" in result.output
    assert '+    """Generated docs."""' in result.output

    compact = CliRunner().invoke(cli.app, ["--no-show-diff", "sample.py"])
    assert compact.exit_code == 0, compact.output
    assert "+++ b/" not in compact.output


@pytest.mark.parametrize("request_scope", ["file", "symbol"])
def test_preserve_skips_documented_symbols_for_every_request_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request_scope: str
) -> None:
    """Verify preserve skips documented symbols for every request scope."""
    source = (
        '"""Module docs."""\n\n'
        "class DocumentedClass:\n"
        '    """Class docs."""\n\n'
        "    def documented_method(self):\n"
        '        """Method docs."""\n'
        "        return True\n\n"
        "def documented_function():\n"
        '    """Function docs."""\n'
        "    return True\n\n"
        "def missing_function():\n"
        "    return False\n"
    )
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")

    class Repo:
        """Provide a repository test double."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = tmp_path

    received: list[list[str]] = []
    settings = Settings(
        confirm=False,
        coverage="all",
        existing_docs="preserve",
        request_scope=request_scope,
        models=("test",),
    )
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: settings)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["sample.py"])
    monkeypatch.setattr(cli, "MAX_AI_ATTEMPTS", 1)

    def fake_documentation(_: str, symbols: list[Symbol], __: Settings) -> dict[str, str]:
        """Fake documentation."""
        received.append([symbol.name for symbol in symbols])
        return {symbol.name: "Generated docs." for symbol in symbols}

    monkeypatch.setattr(cli, "documentation_for", fake_documentation)

    result = CliRunner().invoke(cli.app, ["sample.py"])

    assert result.exit_code == 0, result.output
    assert received == [["missing_function"]]


def test_symbol_scope_applies_completed_symbols_before_a_later_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify symbol scope applies completed symbols before a later failure."""
    source = '"""Module docs."""\n\ndef first():\n    return 1\n\ndef second():\n    return 2\n'
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")

    class Repo:
        """Provide a repository test double."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = tmp_path

    settings = Settings(output="apply", confirm=False, request_scope="symbol", models=("test",))
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: settings)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["sample.py"])
    monkeypatch.setattr(cli, "MAX_AI_ATTEMPTS", 1)

    def fake_documentation(_: str, symbols: list[Symbol], __: Settings) -> dict[str, str]:
        """Fake documentation."""
        if symbols[0].name == "second":
            raise AIProviderError("provider unavailable")
        return {"first": "First generated documentation."}

    monkeypatch.setattr(cli, "documentation_for", fake_documentation)

    result = CliRunner().invoke(cli.app, ["sample.py"])

    assert result.exit_code == 1, result.output
    assert '"""First generated documentation."""' in path.read_text(encoding="utf-8")
    assert "Applied documentation: sample.py:first" not in result.output
    assert "Skipped documentation: sample.py" in result.output


def test_symbol_scope_applies_every_duplicate_symbol_incrementally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify duplicate names remain addressable after earlier insertions shift their lines."""
    source = (
        '\"\"\"Module docs.\"\"\"\n\n'
        "def repeated():\n"
        "    return 1\n\n"
        "def repeated():\n"
        "    return 2\n"
    )
    path = tmp_path / "duplicate.py"
    path.write_text(source, encoding="utf-8")

    class Repo:
        """Provide a repository test double."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = tmp_path

    settings = Settings(output="apply", confirm=False, request_scope="symbol", models=("test",))
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: settings)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["duplicate.py"])
    monkeypatch.setattr(cli, "MAX_AI_ATTEMPTS", 1)
    monkeypatch.setattr(
        cli,
        "documentation_for",
        lambda _source, symbols, _settings: {
            symbols[0].name: Documentation(f"Document {symbols[0].name}.")
        },
    )

    result = CliRunner().invoke(cli.app, ["duplicate.py"])

    assert result.exit_code == 0, result.output
    updated = path.read_text(encoding="utf-8")
    assert '"""Document repeated@L3:1."""' in updated
    assert '"""Document repeated@L6:1."""' in updated


def test_inline_python_suites_do_not_request_ai_documentation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify inline Python suites are excluded before documentation generation."""
    path = tmp_path / "inline.py"
    source = '\"\"\"Inline examples.\"\"\"\n\n\ndef compact(): return 1\n'
    path.write_text(source, encoding="utf-8")

    class Repo:
        """Provide a repository test double."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = tmp_path

    calls: list[str] = []
    settings = Settings(confirm=False, request_scope="symbol", models=("test",))
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: settings)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["inline.py"])

    def unexpected_documentation(*_args: object) -> dict[str, Documentation]:
        """Fail the test if an inline suite reaches the provider."""
        calls.append("called")
        return {}

    monkeypatch.setattr(
        cli,
        "documentation_for",
        unexpected_documentation,
    )

    result = CliRunner().invoke(cli.app, ["inline.py"])

    assert result.exit_code == 0, result.output
    assert calls == []
    assert path.read_text(encoding="utf-8") == source


def test_missing_javascript_runtime_skips_before_requesting_ai(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify JavaScript validation prerequisites are checked before provider usage."""
    path = tmp_path / "sample.js"
    path.write_text("function work() { return true; }\n", encoding="utf-8")

    class Repo:
        """Provide a repository test double."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = tmp_path

    calls: list[str] = []
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: Settings(models=("test",)))
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["sample.js"])
    monkeypatch.setattr(
        cli,
        "validation_command",
        lambda *_args: (_ for _ in ()).throw(DocGubError("node is unavailable")),
    )

    def unexpected_documentation(*_args: object) -> dict[str, Documentation]:
        """Fail the test if a missing runtime reaches the provider."""
        calls.append("called")
        return {}

    monkeypatch.setattr(
        cli,
        "documentation_for",
        unexpected_documentation,
    )

    result = CliRunner().invoke(cli.app, ["sample.js"])

    assert result.exit_code == 1, result.output
    assert calls == []
    assert "node is unavailable" in result.output


def test_symbol_scope_applies_class_docs_before_a_decorated_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify symbol scope applies class docs before a decorated method."""
    source = (
        '"""Module docs."""\n\n'
        "class FinancialCalculator:\n"
        "    @staticmethod\n"
        "    def compound_interest(capital: float) -> float:\n"
        '        """Method docs."""\n'
        "        return capital * 1.1\n"
    )
    path = tmp_path / "finance.py"
    path.write_text(source, encoding="utf-8")

    class Repo:
        """Provide a repository test double."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = tmp_path

    settings = Settings(output="apply", confirm=False, request_scope="symbol", models=("test",))
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: settings)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["finance.py"])
    monkeypatch.setattr(cli, "MAX_AI_ATTEMPTS", 1)
    monkeypatch.setattr(
        cli,
        "documentation_for",
        lambda _content, symbols, _settings: {symbols[0].name: "Perform financial calculations."},
    )

    result = CliRunner().invoke(cli.app, ["finance.py"])

    assert result.exit_code == 0, result.output
    updated = path.read_text(encoding="utf-8")
    assert (
        "class FinancialCalculator:\n"
        '    """Perform financial calculations."""\n'
        "\n"
        "    @staticmethod\n"
    ) in updated
    assert "invalid syntax" not in result.output


def test_noninteractive_apply_requires_explicit_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify noninteractive apply requires explicit confirmation."""
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    with pytest.raises(DocGubError, match="use --yes"):
        cli._confirm_application(Settings(output="apply"), ["sample.py"], yes=False)


def test_request_retries_with_the_next_model_after_a_timeout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify request retries with the next model after a timeout."""
    symbol = next(
        item for item in discover("def work():\n    pass\n", ".py") if item.name == "work"
    )
    attempted: list[str] = []

    def fake_documentation(
        _source: str, _symbols: list[Symbol], settings: Settings
    ) -> dict[str, Documentation]:
        """Simula o processo de documentação.

        Adicionando um modelo ao histórico e retornando uma estrutura de dicionário com informações
        sobre os símbolos.

        Args:
            _source: O conteúdo da fonte a ser processada.
            _symbols: Uma lista de objetos Symbol que representam os símbolos encontrados na fonte.
            settings: Configurações operacionais, incluindo o modelo do sistema.

        """
        attempted.append(settings.model)
        if len(attempted) == 1:
            raise AITimeoutError("timed out")
        return {symbol.name: Documentation("Perform work.")}

    monkeypatch.setattr(cli, "documentation_for", fake_documentation)
    monkeypatch.setattr(cli, "MAX_AI_ATTEMPTS", 2)

    generated, model = cli._request_documentation(
        "def work():\n    pass\n",
        [symbol],
        Settings(models=("first", "second")),
        "sample.py",
    )

    assert attempted == ["first", "second"]
    assert model == "second"
    assert generated[symbol.name].description == "Perform work."
    assert "AI request timed out" in capsys.readouterr().out


def test_symbol_apply_rejects_a_declaration_changed_during_generation(tmp_path: Path) -> None:
    """Verify symbol apply rejects a declaration changed during generation."""
    path = tmp_path / "sample.py"
    original = "def before():\n    pass\n"
    target = next(item for item in discover(original, ".py") if item.name == "before")
    path.write_text("def after():\n    pass\n", encoding="utf-8")

    with pytest.raises(DocGubError, match="changed during generation"):
        cli._apply_generated_symbol(
            path,
            "sample.py",
            target,
            {target.name: Documentation("Describe the original function.")},
            Settings(output="apply", confirm=False),
        )


def test_config_init_reports_parent_directory_errors(tmp_path: Path) -> None:
    """Verify config creation failures are actionable and traceback-free."""
    target = tmp_path / "missing" / "config.toml"

    result = CliRunner().invoke(cli.config_app, ["init", "--path", str(target)])

    assert result.exit_code == 1
    assert f"Error: unable to create {target}" in result.output


def test_config_init_uses_the_doc_code_default_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify config init creates the configuration file for the renamed project."""
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli.config_app, ["init"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".doc-code.toml").is_file()


def test_generation_reports_source_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify source filesystem failures are reported without a traceback."""
    (tmp_path / "sample.py").mkdir()

    class Repo:
        """Provide a repository test double."""

        def __init__(self) -> None:
            """Initialize the test double."""
            self.root = tmp_path

    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["sample.py"])

    result = CliRunner().invoke(cli.app, ["--continue-on-error"])

    assert result.exit_code == 0
    assert "Skipped documentation: sample.py" in result.output
    assert "Unable to read source file" in result.output
