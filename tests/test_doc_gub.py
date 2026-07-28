from __future__ import annotations

from pathlib import Path

import pytest

from doc_gub.config import load
from doc_gub.editor import apply, prepare
from doc_gub.errors import DocGubError
from doc_gub.symbols import discover


def test_config_precedence_and_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".doc-gub.toml").write_text("[documentation]\ncoverage = 'all'\n", encoding="utf-8")
    monkeypatch.setenv("DOC_GUB_COVERAGE", "minimal")
    assert load(tmp_path, coverage="missing").coverage == "missing"
    assert load(tmp_path).coverage == "minimal"


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


def test_preserve_existing_documentation(tmp_path: Path) -> None:
    path = tmp_path / "documented.py"
    source = 'def ready():\n    """Human-written description."""\n    return True\n'
    path.write_text(source, encoding="utf-8")
    settings = load(tmp_path, coverage="all", existing_docs="preserve", selection="repository")
    result = prepare(path, discover(source, ".py"), {"ready": "New description."}, settings)
    assert '"""Human-written description."""' in result.after
    assert "New description." not in result.after
