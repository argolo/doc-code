"""Módulo que contém testes de integração e utilitários para o sistema doc-gub."""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_gub import ai
from doc_gub.ai import _documentation_response, documentation_for, prompt
from doc_gub.config import Settings, load
from doc_gub.errors import AIProviderError, DocGubError, InvalidAIResponseError
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


def test_environment_values_are_converted_and_invalid_values_are_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Normalize environment configuration without leaking conversion tracebacks."""
    monkeypatch.setenv("DOC_GUB_CONFIRM", "false")

    assert load(tmp_path).confirm is False

    monkeypatch.setenv("DOC_GUB_MAX_INPUT_TOKENS", "not-a-number")
    with pytest.raises(DocGubError, match="max_input_tokens.*positive integer"):
        load(tmp_path)


def test_unknown_toml_options_are_reported_as_configuration_errors(tmp_path: Path) -> None:
    """Reject misspelled options without exposing a dataclass traceback."""
    (tmp_path / ".doc-gub.toml").write_text("[ai]\nunknown = true\n", encoding="utf-8")

    with pytest.raises(DocGubError, match=r"Unknown option\(s\).*\[ai\].*unknown"):
        load(tmp_path)


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("model", "", "model.*non-empty"),
        ("endpoint", "localhost:11434", "endpoint.*HTTP"),
    ],
)
def test_model_and_endpoint_are_validated(
    tmp_path: Path, option: str, value: str, message: str
) -> None:
    """Fail locally when a provider setting has an invalid type or shape."""
    (tmp_path / ".doc-gub.toml").write_text(f"[ai]\n{option} = {value!r}\n", encoding="utf-8")

    with pytest.raises(DocGubError, match=message):
        load(tmp_path)


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
    """Testa argumentos em uma resposta estruturada.

    A resposta deve incluir argumentos para cada argumento solicitado
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


def test_structured_ai_response_rejects_non_string_content() -> None:
    """Keep malformed provider payloads inside the documented error boundary."""
    symbol = next(
        symbol for symbol in discover("def work():\n    pass\n", ".py") if symbol.name == "work"
    )

    with pytest.raises(InvalidAIResponseError, match="invalid format"):
        _documentation_response(None, [symbol])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("provider", "response", "key_name"),
    [
        (
            "openai",
            {
                "choices": [
                    {"message": {"content": '{"work": {"description": "Work.", "arguments": {}}}'}}
                ]
            },
            "OPENAI_API_KEY",
        ),
        (
            "gemini",
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": '{"work": {"description": "Work.", "arguments": {}}}'}
                            ]
                        }
                    }
                ]
            },
            "GEMINI_API_KEY",
        ),
        ("ollama", {"response": '{"work": {"description": "Work.", "arguments": {}}}'}, None),
    ],
)
def test_documentation_for_normalizes_supported_provider_responses(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    response: dict[str, object],
    key_name: str | None,
) -> None:
    """Exercise the provider-specific response paths without making network calls."""
    if key_name:
        monkeypatch.setenv(key_name, "test-key")
    received: list[dict[str, object]] = []

    def fake_post(
        _url: str, payload: dict[str, object], _headers: dict[str, str], _timeout: int
    ) -> dict[str, object]:
        """Simula uma requisição POST para um URL específico e retorna uma resposta simulada.

        Args:
            _url: O URL de destino da requisição.
            payload: Os dados a serem enviados no corpo (body) da requisição.
            _headers: Um dicionário contendo os cabeçalhos HTTP personalizados para a requisição.
            _timeout: O tempo limite em segundos para a requisição.

        """
        received.append(payload)
        return response

    monkeypatch.setattr(
        ai,
        "_post",
        fake_post,
    )
    symbol = next(
        symbol for symbol in discover("def work():\n    pass\n", ".py") if symbol.name == "work"
    )

    result = documentation_for("def work():\n    pass\n", [symbol], Settings(provider=provider))

    assert result["work"].description == "Work."
    assert received


def test_post_converts_invalid_transport_response_to_domain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not leak JSON shape errors from the HTTP transport helper."""

    class Response:
        """Minimal response context manager with a JSON array body."""

        def __enter__(self) -> Response:
            """Entra a um contexto, retornando uma instância de Response."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Finaliza o contexto da resposta.

            É chamado quando o bloco ``with`` é encerrado para limpar
            recursos ou executar lógica de finalização.

            """
            return None

        def read(self) -> bytes:
            """Lê o conteúdo da resposta como um objeto de bytes."""
            return b"[]"

    monkeypatch.setattr(ai, "urlopen", lambda *_args, **_kwargs: Response())

    with pytest.raises(AIProviderError, match="invalid response"):
        ai._post("https://example.test", {}, {}, 1)
