from __future__ import annotations

import pytest

from doc_gub.cli import _loading, _model_for_attempt, _undocumented_symbols


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
