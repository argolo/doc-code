from __future__ import annotations

import ast
from pathlib import Path

import pytest

from doc_gub.ai import prompt
from doc_gub.cli import _loading
from doc_gub.config import load
from doc_gub.editor import apply, prepare
from doc_gub.errors import DocGubError
from doc_gub.symbols import discover


def test_config_precedence_and_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".doc-gub.toml").write_text("[documentation]\ncoverage = 'all'\n", encoding="utf-8")
    monkeypatch.setenv("DOC_GUB_COVERAGE", "minimal")
    assert load(tmp_path, coverage="missing").coverage == "missing"
    assert load(tmp_path).coverage == "minimal"


def test_language_is_loaded_and_included_in_the_ai_prompt(tmp_path: Path) -> None:
    (tmp_path / ".doc-gub.toml").write_text(
        "[documentation]\nlanguage = 'Portuguese'\n", encoding="utf-8"
    )
    settings = load(tmp_path)
    symbols = discover("def calculate(value):\n    return value * 2\n", ".py")

    assert settings.language == "Portuguese"
    assert 'documentation text in "Portuguese"' in prompt("", symbols, settings.language)


def test_loading_reports_progress_when_stderr_is_not_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _loading("Generating documentation for [sample.py]"):
        pass

    assert "Generating documentation for [sample.py]" in capsys.readouterr().err


@pytest.mark.parametrize("fmt, marker", [("google", "Args:"), ("numpy", "Parameters"), ("sphinx", ":param value:")])
def test_python_formats_and_stale_file_protection(tmp_path: Path, fmt: str, marker: str) -> None:
    path = tmp_path / "sample.py"
    source = "def calculate(value):\n    return value * 2\n"
    path.write_text(source, encoding="utf-8")
    settings = load(tmp_path, python_format=fmt, selection="repository")
    prepared = prepare(path, discover(source, ".py"), {"module": "Module docs.", "calculate": "Calculate twice."}, settings)
    assert marker in prepared.after
    path.write_text("# changed concurrently\n" + source, encoding="utf-8")
    with pytest.raises(DocGubError, match="changed after preview"):
        apply(prepared)


def test_python_nested_async_and_jsdoc(tmp_path: Path) -> None:
    python = "class Service:\n    async def fetch(self, item):\n        return item\n"
    symbols = discover(python, ".py")
    assert {item.name for item in symbols} >= {"module", "Service", "Service.fetch"}
    javascript = "export const add = (left, right) => left + right;\n"
    js_path = tmp_path / "sample.ts"
    js_path.write_text(javascript, encoding="utf-8")
    settings = load(tmp_path, selection="repository")
    result = prepare(js_path, discover(javascript, ".ts"), {"add": "Add two values."}, settings)
    assert "@param {any} left" in result.after


def test_only_documentation_is_changed_and_existing_jsdoc_can_be_replaced(tmp_path: Path) -> None:
    python_path = tmp_path / "safe.py"
    python = "#!/usr/bin/env python3\n# coding: utf-8\ndef calculate(value):\n    return value * 2\n"
    python_path.write_text(python, encoding="utf-8")
    settings = load(tmp_path, selection="repository")
    prepared = prepare(
        python_path, discover(python, ".py"), {"module": "Module docs.", "calculate": "Double."}, settings
    )
    assert prepared.after.startswith("#!/usr/bin/env python3\n# coding: utf-8\n\"\"\"Module docs.\"\"\"\n")
    assert "def calculate(value):\n    \"\"\"Double." in prepared.after
    assert "    return value * 2\n" in prepared.after
    ast.parse(prepared.after)

    js_path = tmp_path / "safe.ts"
    javascript = "/**\n * Old docs.\n */\nexport const add = (left, right) => left + right;\n"
    js_path.write_text(javascript, encoding="utf-8")
    replaced = prepare(
        js_path,
        discover(javascript, ".ts"),
        {"add": "Add two values."},
        load(tmp_path, coverage="all", existing_docs="replace", selection="repository"),
    )
    assert "Old docs." not in replaced.after
    assert "export const add = (left, right) => left + right;" in replaced.after


def test_inline_python_suites_are_left_untouched(tmp_path: Path) -> None:
    path = tmp_path / "inline.py"
    source = "def ready(): return True\n"
    path.write_text(source, encoding="utf-8")
    function = next(item for item in discover(source, ".py") if item.name == "ready")

    prepared = prepare(path, [function], {"ready": "Return readiness."}, load(tmp_path))

    assert prepared.after == source
    assert prepared.changed == ()
    assert prepared.ignored == (function,)


def test_preserve_existing_documentation(tmp_path: Path) -> None:
    path = tmp_path / "documented.py"
    source = 'def ready():\n    """Human-written description."""\n    return True\n'
    path.write_text(source, encoding="utf-8")
    settings = load(tmp_path, coverage="all", existing_docs="preserve", selection="repository")
    result = prepare(path, discover(source, ".py"), {"ready": "New description."}, settings)
    assert '"""Human-written description."""' in result.after
    assert "New description." not in result.after
