from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from doc_gub import cli
from doc_gub.cli import _loading, _model_for_attempt, _undocumented_symbols
from doc_gub.config import Settings
from doc_gub.errors import AIProviderError
from doc_gub.symbols import Symbol, discover, source_for_symbol


def test_loading_reports_progress_when_stderr_is_not_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _loading("Generating documentation for [sample.py]"):
        pass

    assert "Generating documentation for [sample.py]" in capsys.readouterr().err


def test_retries_cycle_through_model_candidates() -> None:
    candidates = ("model-one", "model-two")

    assert [_model_for_attempt(candidates, attempt) for attempt in range(1, 5)] == [
        "model-one",
        "model-two",
        "model-one",
        "model-two",
    ]


def test_check_finds_undocumented_symbols_without_calling_ai() -> None:
    content = '"""Module docs."""\n\ndef documented():\n    """Docs."""\n\ndef missing():\n    pass\n'

    assert _undocumented_symbols(content, ".py") == ["missing"]


def test_apply_writes_each_file_before_later_generation_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("def first():\n    return True\n", encoding="utf-8")
    second_source = "def second():\n    return True\n"
    second.write_text(second_source, encoding="utf-8")

    class Repo:
        def __init__(self) -> None:
            self.root = tmp_path

    settings = Settings(output="apply", confirm=False, selection="repository", models=("test",))
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: settings)
    def fake_resolve(_: object, paths: list[Path], __: Settings) -> list[str]:
        assert paths == [Path("first.py"), Path("second.py")]
        return ["first.py", "second.py"]

    monkeypatch.setattr(cli, "resolve", fake_resolve)
    monkeypatch.setattr(cli, "MAX_AI_ATTEMPTS", 1)

    def fake_documentation(content: str, symbols: list[Symbol], _: Settings) -> dict[str, str]:
        if "second" in content:
            raise AIProviderError("provider unavailable")
        return {symbol.name: "Generated docs." for symbol in symbols}

    monkeypatch.setattr(cli, "documentation_for", fake_documentation)

    result = CliRunner().invoke(cli.app, ["first.py", "second.py"])

    assert result.exit_code == 0, result.output
    assert '"""Generated docs."""' in first.read_text(encoding="utf-8")
    assert second.read_text(encoding="utf-8") == second_source
    assert "Applied documentation: first.py" in result.output
    assert "Skipped documentation: second.py" in result.output


def test_symbol_scope_contains_only_the_requested_python_function() -> None:
    content = "def first():\n    return 1\n\ndef second():\n    return 2\n"
    second = next(symbol for symbol in discover(content, ".py") if symbol.name == "second")

    assert source_for_symbol(content, second, ".py") == "def second():\n    return 2\n"


def test_module_symbol_scope_uses_a_python_outline_without_function_bodies() -> None:
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
    source = '"""Module docs."""\n\ndef first():\n    return 1\n\ndef second():\n    return 2\n'
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")

    class Repo:
        def __init__(self) -> None:
            self.root = tmp_path

    settings = Settings(confirm=False, request_scope="symbol", models=("test",))
    received: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: settings)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["sample.py"])
    monkeypatch.setattr(cli, "MAX_AI_ATTEMPTS", 1)

    def fake_documentation(content: str, symbols: list[Symbol], _: Settings) -> dict[str, str]:
        received.append((content, [symbol.name for symbol in symbols]))
        return {symbol.name: "Generated docs." for symbol in symbols}

    monkeypatch.setattr(cli, "documentation_for", fake_documentation)

    result = CliRunner().invoke(cli.app, ["sample.py"])

    assert result.exit_code == 0, result.output
    assert received == [
        ("def first():\n    return 1\n", ["first"]),
        ("def second():\n    return 2\n", ["second"]),
    ]


def test_symbol_scope_applies_completed_symbols_before_a_later_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = '"""Module docs."""\n\ndef first():\n    return 1\n\ndef second():\n    return 2\n'
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")

    class Repo:
        def __init__(self) -> None:
            self.root = tmp_path

    settings = Settings(
        output="apply", confirm=False, request_scope="symbol", models=("test",)
    )
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: settings)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["sample.py"])
    monkeypatch.setattr(cli, "MAX_AI_ATTEMPTS", 1)

    def fake_documentation(_: str, symbols: list[Symbol], __: Settings) -> dict[str, str]:
        if symbols[0].name == "second":
            raise AIProviderError("provider unavailable")
        return {"first": "First generated documentation."}

    monkeypatch.setattr(cli, "documentation_for", fake_documentation)

    result = CliRunner().invoke(cli.app, ["sample.py"])

    assert result.exit_code == 0, result.output
    assert '"""First generated documentation."""' in path.read_text(encoding="utf-8")
    assert "Applied documentation: sample.py:first" in result.output
    assert "Skipped documentation: sample.py" in result.output
