from __future__ import annotations

import ast
from pathlib import Path

import pytest

from doc_gub import editor
from doc_gub.config import load
from doc_gub.editor import apply, prepare
from doc_gub.errors import DocGubError
from doc_gub.symbols import discover


@pytest.mark.parametrize(
    ("fmt", "marker"),
    [("google", "Args:"), ("numpy", "Parameters"), ("sphinx", ":param value:")],
)
def test_python_formats_and_stale_file_protection(tmp_path: Path, fmt: str, marker: str) -> None:
    path = tmp_path / "sample.py"
    source = "def calculate(value):\n    return value * 2\n"
    path.write_text(source, encoding="utf-8")
    settings = load(tmp_path, python_format=fmt, selection="repository")

    prepared = prepare(
        path,
        discover(source, ".py"),
        {"module": "Module docs.", "calculate": "Calculate twice."},
        settings,
    )

    assert marker in prepared.after
    path.write_text("# changed concurrently\n" + source, encoding="utf-8")
    with pytest.raises(DocGubError, match="changed after preview"):
        apply(prepared)


def test_python_nested_async_and_jsdoc(tmp_path: Path) -> None:
    python = "class Service:\n    async def fetch(self, item):\n        return item\n"
    symbols = discover(python, ".py")
    assert {item.name for item in symbols} >= {"module", "Service", "Service.fetch"}

    javascript = "export const add = (left, right) => left + right;\n"
    path = tmp_path / "sample.js"
    path.write_text(javascript, encoding="utf-8")
    result = prepare(path, discover(javascript, ".js"), {"add": "Add two values."}, load(tmp_path))

    assert "@param {any} left" in result.after


def test_only_documentation_is_changed_and_existing_jsdoc_can_be_replaced(tmp_path: Path) -> None:
    python_path = tmp_path / "safe.py"
    python = "#!/usr/bin/env python3\n# coding: utf-8\ndef calculate(value):\n    return value * 2\n"
    python_path.write_text(python, encoding="utf-8")
    prepared = prepare(
        python_path,
        discover(python, ".py"),
        {"module": "Module docs.", "calculate": "Double."},
        load(tmp_path),
    )

    assert prepared.after.startswith("#!/usr/bin/env python3\n# coding: utf-8\n\"\"\"Module docs.\"\"\"\n")
    assert "def calculate(value):\n    \"\"\"Double." in prepared.after
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
    captured: list[str] = []
    monkeypatch.setattr(
        editor.shutil, "which", lambda name: "/usr/local/bin/tsc" if name == "tsc" else None
    )

    def fake_run(command: list[str], **_: object) -> object:
        captured.extend(command)
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(editor.subprocess, "run", fake_run)
    editor._validate_javascript("const value: number = 1;\n", ".ts", tmp_path / "sample.ts")

    assert captured[:5] == ["/usr/local/bin/tsc", "--noEmit", "--noCheck", "--pretty", "false"]


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
