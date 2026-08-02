"""Test config behavior."""

from __future__ import annotations

from pathlib import Path
from urllib.error import URLError

import pytest

from doc_code import ai
from doc_code.ai import _documentation_response, documentation_for, prompt
from doc_code.config import Settings, load
from doc_code.errors import (
    AIProviderError,
    AITimeoutError,
    DocGubError,
    InvalidAIResponseError,
)
from doc_code.symbols import discover, needs_documentation


def test_config_precedence_and_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify config precedence and validation."""
    (tmp_path / ".doc-code.toml").write_text(
        "[documentation]\ncoverage = 'all'\n", encoding="utf-8"
    )
    monkeypatch.setenv("DOC_CODE_COVERAGE", "minimal")

    assert load(tmp_path, coverage="missing").coverage == "missing"
    assert load(tmp_path).coverage == "minimal"


def test_environment_values_are_converted_and_invalid_values_are_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify environment values are converted and invalid values are actionable."""
    monkeypatch.setenv("DOC_CODE_CONFIRM", "false")

    assert load(tmp_path).confirm is False

    monkeypatch.setenv("DOC_CODE_MAX_INPUT_TOKENS", "not-a-number")
    with pytest.raises(DocGubError, match="max_input_tokens.*positive integer"):
        load(tmp_path)


def test_unknown_toml_options_are_reported_as_configuration_errors(tmp_path: Path) -> None:
    """Verify unknown toml options are reported as configuration errors."""
    (tmp_path / ".doc-code.toml").write_text("[ai]\nunknown = true\n", encoding="utf-8")

    with pytest.raises(DocGubError, match=r"Unknown option\(s\).*\[ai\].*unknown"):
        load(tmp_path)


def test_explicit_configuration_must_exist(tmp_path: Path) -> None:
    """Verify a missing explicit configuration is never silently ignored."""
    missing = tmp_path / "missing.toml"

    with pytest.raises(DocGubError, match=f"does not exist: {missing}"):
        load(tmp_path, missing)


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
    """Verify model and endpoint are validated."""
    (tmp_path / ".doc-code.toml").write_text(f"[ai]\n{option} = {value!r}\n", encoding="utf-8")

    with pytest.raises(DocGubError, match=message):
        load(tmp_path)


@pytest.mark.parametrize(
    ("provider", "temperature", "message"),
    [
        ("ollama", -0.1, "non-negative"),
        ("openai", 2.1, "between 0 and 2"),
    ],
)
def test_temperature_respects_provider_ranges(
    tmp_path: Path, provider: str, temperature: float, message: str
) -> None:
    """Verify invalid sampling temperatures fail before a provider request."""
    with pytest.raises(DocGubError, match=message):
        load(tmp_path, provider=provider, temperature=temperature)

    assert load(tmp_path, provider="ollama", temperature=3).temperature == 3


def test_provider_defaults_and_explicit_model_precedence(tmp_path: Path) -> None:
    """Verify provider defaults and explicit model precedence."""
    openai = load(tmp_path, provider="openai")
    explicit = load(tmp_path, provider="openai", model="custom-openai-model")

    assert openai.endpoint is None
    assert openai.model == "gpt-5.6-sol"
    assert openai.model_candidates == ("gpt-5.6-sol",)
    assert explicit.model_candidates == ("custom-openai-model",)


def test_an_empty_model_list_uses_the_primary_model(tmp_path: Path) -> None:
    """Verify an explicit empty fallback list retains the configured primary model."""
    settings = load(tmp_path, model="single-model", models=[])

    assert settings.models == ()
    assert settings.model_candidates == ("single-model",)


def test_authenticated_remote_endpoints_require_https(tmp_path: Path) -> None:
    """Verify authenticated remote endpoints require https."""
    with pytest.raises(DocGubError, match="must use HTTPS"):
        load(tmp_path, provider="openai", endpoint="http://example.test/v1")

    assert (
        load(tmp_path, provider="openai", endpoint="http://localhost:8080/v1").endpoint
        == "http://localhost:8080/v1"
    )


def test_language_is_loaded_and_included_in_the_ai_prompt(tmp_path: Path) -> None:
    """Verify language is loaded and included in the ai prompt."""
    (tmp_path / ".doc-code.toml").write_text(
        "[documentation]\nlanguage = 'Portuguese'\n", encoding="utf-8"
    )
    settings = load(tmp_path)
    symbols = discover("def calculate(value):\n    return value * 2\n", ".py")

    assert settings.language == "Portuguese"
    assert 'documentation text in "Portuguese"' in prompt("", symbols, settings.language)


def test_request_scope_can_be_configured(tmp_path: Path) -> None:
    """Verify request scope can be configured."""
    (tmp_path / ".doc-code.toml").write_text(
        "[documentation]\nrequest_scope = 'symbol'\n", encoding="utf-8"
    )

    assert load(tmp_path).request_scope == "symbol"


def test_minimal_coverage_targets_only_the_public_top_level_api() -> None:
    """Verify minimal coverage differs from missing coverage in a predictable way."""
    symbols = discover(
        "def public():\n    pass\n\n"
        "def _private():\n    pass\n\n"
        "class Service:\n"
        "    def method(self):\n"
        "        pass\n",
        ".py",
    )

    selected = [
        symbol.name
        for symbol in symbols
        if needs_documentation(symbol, "minimal")
    ]

    assert selected == ["module", "public", "Service"]


def test_existing_docs_is_not_a_supported_configuration_option(tmp_path: Path) -> None:
    """Verify the removed option is rejected instead of silently changing behavior."""
    (tmp_path / ".doc-code.toml").write_text(
        "[documentation]\nexisting_docs = 'preserve'\n", encoding="utf-8"
    )

    with pytest.raises(DocGubError, match=r"Unknown option\(s\).*existing_docs"):
        load(tmp_path)


def test_structured_ai_response_requires_argument_documentation() -> None:
    """Verify structured ai response requires argument documentation."""
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
    """Verify structured ai response requires every requested symbol."""
    symbols = discover("def first():\n    pass\n\ndef second():\n    pass\n", ".py")
    functions = [symbol for symbol in symbols if symbol.kind == "function"]

    with pytest.raises(InvalidAIResponseError, match="must document every requested symbol"):
        _documentation_response(
            '{"first": {"description": "Do work.", "arguments": {}}}', functions
        )


def test_structured_ai_response_rejects_non_string_content() -> None:
    """Verify structured ai response rejects non string content."""
    symbol = next(
        symbol for symbol in discover("def work():\n    pass\n", ".py") if symbol.name == "work"
    )

    with pytest.raises(InvalidAIResponseError, match="invalid format"):
        _documentation_response(None, [symbol])  # type: ignore[arg-type]


def test_structured_ai_response_rejects_empty_documentation_text() -> None:
    """Verify whitespace-only descriptions and argument details are rejected."""
    symbol = next(
        symbol
        for symbol in discover("def calculate(value):\n    return value\n", ".py")
        if symbol.name == "calculate"
    )

    with pytest.raises(InvalidAIResponseError, match="non-empty"):
        _documentation_response(
            '{"calculate": {"description": "  ", "arguments": {"value": "Value."}}}',
            [symbol],
        )
    with pytest.raises(InvalidAIResponseError, match="non-empty"):
        _documentation_response(
            '{"calculate": {"description": "Return a value.", "arguments": {"value": " "}}}',
            [symbol],
        )


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
    """Verify documentation for normalizes supported provider responses."""
    if key_name:
        monkeypatch.setenv(key_name, "test-key")
    received: list[tuple[str, dict[str, object], dict[str, str]]] = []

    def fake_post(
        url: str, payload: dict[str, object], headers: dict[str, str], _timeout: int
    ) -> dict[str, object]:
        """Fake post."""
        received.append((url, payload, headers))
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
    url, payload, headers = received[0]
    if provider == "openai":
        assert url == "https://api.openai.com/v1/chat/completions"
        assert payload["model"] == "gpt-5.6-sol"
        assert "max_completion_tokens" in payload
    elif provider == "gemini":
        assert "models/gemini-3.6-flash:generateContent" in url
        assert headers["x-goog-api-key"] == "test-key"
        assert "?key=" not in url


@pytest.mark.parametrize(
    ("provider", "key_name"),
    [("openai", "OPENAI_API_KEY"), ("gemini", "GEMINI_API_KEY"), ("ollama", None)],
)
def test_documentation_for_rejects_malformed_provider_envelopes(
    monkeypatch: pytest.MonkeyPatch, provider: str, key_name: str | None
) -> None:
    """Verify documentation for rejects malformed provider envelopes."""
    if key_name:
        monkeypatch.setenv(key_name, "test-key")
    monkeypatch.setattr(ai, "_post", lambda *_args, **_kwargs: {})
    symbol = next(
        symbol for symbol in discover("def work():\n    pass\n", ".py") if symbol.name == "work"
    )

    with pytest.raises(AIProviderError, match=f"{provider} provider returned an invalid response"):
        documentation_for("def work():\n    pass\n", [symbol], Settings(provider=provider))


def test_post_converts_invalid_transport_response_to_domain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify post converts invalid transport response to domain error."""

    class Response:
        """Provide the Response test double."""

        def __enter__(self) -> Response:
            """Enter the response context."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Exit the response context."""
            return None

        def read(self, _size: int = -1) -> bytes:
            """Read."""
            return b"[]"

    monkeypatch.setattr(ai, "urlopen", lambda *_args, **_kwargs: Response())

    with pytest.raises(AIProviderError, match="invalid response"):
        ai._post("https://example.test", {}, {}, 1)


def test_post_rejects_unsafe_endpoints_before_requesting_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify direct Settings construction cannot make non-HTTP requests."""
    monkeypatch.setattr(ai, "urlopen", lambda *_args, **_kwargs: pytest.fail("unexpected request"))

    with pytest.raises(AIProviderError, match="absolute HTTP"):
        ai._post("file:///tmp/provider-response", {}, {}, 1)


def test_post_rejects_oversized_provider_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify response size is bounded before JSON decoding."""
    class Response:
        """Provide an oversized response test double."""

        def __enter__(self) -> Response:
            """Enter the response context."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Exit the response context."""
            return None

        def read(self, size: int = -1) -> bytes:
            """Return more bytes than the requested safe limit."""
            return b"x" * size

    monkeypatch.setattr(ai, "urlopen", lambda *_args, **_kwargs: Response())

    with pytest.raises(AIProviderError, match="exceeds the supported size"):
        ai._post("https://example.test", {}, {}, 1)


@pytest.mark.parametrize(
    ("transport_error", "expected_error"),
    [
        (TimeoutError("request timed out"), AITimeoutError),
        (URLError(TimeoutError("socket timed out")), AITimeoutError),
        (URLError("network unavailable"), AIProviderError),
    ],
)
def test_post_converts_transport_failures_to_domain_errors(
    monkeypatch: pytest.MonkeyPatch,
    transport_error: OSError,
    expected_error: type[AIProviderError],
) -> None:
    """Verify post converts transport failures to domain errors."""

    def fail_request(*_args: object, **_kwargs: object) -> None:
        """Fail request."""
        raise transport_error

    monkeypatch.setattr(ai, "urlopen", fail_request)

    with pytest.raises(expected_error, match="Unable to contact the AI provider"):
        ai._post("https://example.test", {}, {}, 1)
