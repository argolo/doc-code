"""Módulo que contém testes de integração e utilitários para o sistema doc-gub."""
from __future__ import annotations

from pathlib import Path

import pytest

from doc_gub.ai import prompt
from doc_gub.config import load
from doc_gub.symbols import discover


def test_config_precedence_and_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Testa a precedência e validação da configuração do projeto (ex: cobertura), verificando se as variáveis de ambiente ou argumentos passados superam os valores configurados no arquivo TOML.
    
    Args:
        tmp_path: Description of tmp_path.
        monkeypatch: Description of monkeypatch."""
    (tmp_path / ".doc-gub.toml").write_text("[documentation]\ncoverage = 'all'\n", encoding="utf-8")
    monkeypatch.setenv("DOC_GUB_COVERAGE", "minimal")

    assert load(tmp_path, coverage="missing").coverage == "missing"
    assert load(tmp_path).coverage == "minimal"


def test_language_is_loaded_and_included_in_the_ai_prompt(tmp_path: Path) -> None:
    """Verifica que o idioma definido na configuração é corretamente carregado e incluído no prompt enviado para a API de IA.
    
    Args:
        tmp_path: Description of tmp_path."""
    (tmp_path / ".doc-gub.toml").write_text(
        "[documentation]\nlanguage = 'Portuguese'\n", encoding="utf-8"
    )
    settings = load(tmp_path)
    symbols = discover("def calculate(value):\n    return value * 2\n", ".py")

    assert settings.language == "Portuguese"
    assert 'documentation text in "Portuguese"' in prompt("", symbols, settings.language)
