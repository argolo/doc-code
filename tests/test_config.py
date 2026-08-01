"""Módulo que contém testes de integração e utilitários para o sistema doc-gub."""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_gub.ai import _documentation_response, prompt
from doc_gub.config import load
from doc_gub.errors import InvalidAIResponseError
from doc_gub.symbols import discover


def test_config_precedence_and_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Testa a precedência e validação da configuração do projeto (ex: cobertura), verificando se as variáveis de ambiente ou argumentos passados superam os valores configurados no arquivo TOML.

    Args:
        tmp_path: Description of tmp_path.
        monkeypatch: Description of monkeypatch.

    """
    (tmp_path / ".doc-gub.toml").write_text("[documentation]\ncoverage = 'all'\n", encoding="utf-8")
    monkeypatch.setenv("DOC_GUB_COVERAGE", "minimal")

    assert load(tmp_path, coverage="missing").coverage == "missing"
    assert load(tmp_path).coverage == "minimal"


def test_language_is_loaded_and_included_in_the_ai_prompt(tmp_path: Path) -> None:
    """Verifica que o idioma definido na configuração é corretamente carregado e incluído no prompt enviado para a API de IA.

    Args:
        tmp_path: Description of tmp_path.

    """
    (tmp_path / ".doc-gub.toml").write_text(
        "[documentation]\nlanguage = 'Portuguese'\n", encoding="utf-8"
    )
    settings = load(tmp_path)
    symbols = discover("def calculate(value):\n    return value * 2\n", ".py")

    assert settings.language == "Portuguese"
    assert 'documentation text in "Portuguese"' in prompt("", symbols, settings.language)


def test_request_scope_can_be_configured(tmp_path: Path) -> None:
    """Verifica se o escopo da requisição pode ser configurado em um arquivo TOML temporário.

    Args:
        tmp_path: Description of tmp_path.

    """
    (tmp_path / ".doc-gub.toml").write_text(
        "[documentation]\nrequest_scope = 'symbol'\n", encoding="utf-8"
    )

    assert load(tmp_path).request_scope == "symbol"


def test_structured_ai_response_requires_argument_documentation() -> None:
    """Testa que a documentação estruturada deve incluir argumentos para cada argumento solicitado
    da função.

    """
    symbols = discover("def calculate(value):\n    return value\n", ".py")
    function = next(symbol for symbol in symbols if symbol.name == "calculate")

    documentation = _documentation_response(
        '{"calculate": {"description": "Return the input value.", '
        '"arguments": {"value": "Value returned unchanged."}}}',
        [function],
    )

    assert documentation["calculate"].arguments["value"] == "Value returned unchanged."
    with pytest.raises(InvalidAIResponseError, match="must describe every requested argument"):
        _documentation_response(
            '{"calculate": {"description": "Return the input value.", "arguments": {}}}',
            [function],
        )


def test_structured_ai_response_requires_every_requested_symbol() -> None:
    """Reject incomplete responses instead of inserting empty documentation."""
    symbols = discover("def first():\n    pass\n\ndef second():\n    pass\n", ".py")
    functions = [symbol for symbol in symbols if symbol.kind == "function"]

    with pytest.raises(InvalidAIResponseError, match="must document every requested symbol"):
        _documentation_response(
            '{"first": {"description": "Do work.", "arguments": {}}}', functions
        )
