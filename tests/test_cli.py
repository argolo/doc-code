from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from doc_gub import cli
from doc_gub.cli import _loading, _model_for_attempt, _undocumented_symbols
from doc_gub.config import Settings
from doc_gub.errors import AIProviderError
from doc_gub.symbols import Symbol


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
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["first.py", "second.py"])
    monkeypatch.setattr(cli, "MAX_AI_ATTEMPTS", 1)

    def fake_documentation(content: str, symbols: list[Symbol], _: Settings) -> dict[str, str]:
        if "second" in content:
            raise AIProviderError("provider unavailable")
        return {symbol.name: "Generated docs." for symbol in symbols}

    monkeypatch.setattr(cli, "documentation_for", fake_documentation)

    result = CliRunner().invoke(cli.app, [])

    assert result.exit_code == 0, result.output
    assert '"""Generated docs."""' in first.read_text(encoding="utf-8")
    assert second.read_text(encoding="utf-8") == second_source
    assert "Applied documentation: first.py" in result.output
    assert "Skipped documentation: second.py" in result.output
