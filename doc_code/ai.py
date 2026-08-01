"""Provider calls and strict JSON response validation for documentation descriptions."""

from __future__ import annotations

import json
import os
from math import ceil
from typing import Any, cast
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .config import Settings
from .errors import AIProviderError, AITimeoutError, DocGubError, InvalidAIResponseError
from .symbols import Documentation, Symbol

_MAX_PROVIDER_RESPONSE_BYTES = 1_000_000


def estimate_tokens(text: str) -> int:
    """Estimate tokens conservatively from the UTF-8 byte length."""
    return ceil(len(text.encode("utf-8")) / 3)


def prompt(content: str, symbols: list[Symbol], language: str = "English") -> str:
    """Build the strict JSON prompt sent to a provider."""
    names = [{"symbol": item.name, "kind": item.kind, "arguments": item.args} for item in symbols]
    return (
        "Return only a JSON object mapping each requested symbol to an object with "
        "`description` (a concise factual description) and `arguments` (an object mapping "
        "every requested argument name to its specific description). Use an empty `arguments` "
        "object when a symbol has no arguments. Do not include Markdown or code fences. Write "
        f"all documentation text in {json.dumps(language, ensure_ascii=False)}. Requested symbols: "
        + json.dumps(names)
        + "\n\nSOURCE:\n"
        + content
    )


def _post(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int
) -> dict[str, Any]:
    """Post JSON and convert transport or decoding failures to domain errors."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AIProviderError("The AI provider endpoint must be an absolute HTTP(S) URL.")
    try:
        with urlopen(
            Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST"),
            timeout=timeout,
        ) as response:  # nosec B310 - user-configured endpoint
            body = response.read(_MAX_PROVIDER_RESPONSE_BYTES + 1)
            if len(body) > _MAX_PROVIDER_RESPONSE_BYTES:
                raise AIProviderError("The AI provider response exceeds the supported size.")
            decoded = json.loads(body)
            if not isinstance(decoded, dict):
                raise TypeError("The AI provider response must be a JSON object.")
            return cast(dict[str, Any], decoded)
    except TimeoutError as exc:
        raise AITimeoutError(f"Unable to contact the AI provider: {exc}") from exc
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise AITimeoutError(f"Unable to contact the AI provider: {exc.reason}") from exc
        raise AIProviderError(f"Unable to contact the AI provider: {exc}") from exc
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise AIProviderError("The AI provider returned an invalid response.") from exc


def documentation_for(
    content: str, symbols: list[Symbol], settings: Settings
) -> dict[str, Documentation]:
    """Generate validated documentation for the requested symbols."""
    body = prompt(content, symbols, settings.language)
    tokens = estimate_tokens(body)
    if (
        tokens > settings.max_input_tokens
        or tokens + settings.max_output_tokens > settings.context_window_tokens
    ):
        raise DocGubError(
            "AI input exceeds configured token limits; narrow the scope or increase limits."
        )
    if settings.provider == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise DocGubError("OPENAI_API_KEY is not configured.")
        data = _post(
            settings.endpoint or "https://api.openai.com/v1/chat/completions",
            {
                "model": settings.model,
                "messages": [{"role": "user", "content": body}],
                "max_completion_tokens": settings.max_output_tokens,
                "temperature": settings.temperature,
            },
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            settings.timeout_seconds,
        )
        answer = _provider_answer(data, "openai")
    elif settings.provider == "gemini":
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise DocGubError("GEMINI_API_KEY is not configured.")
        endpoint = settings.endpoint or (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.model}:generateContent"
        )
        data = _post(
            endpoint,
            {"contents": [{"parts": [{"text": body}]}]},
            {"Content-Type": "application/json", "x-goog-api-key": key},
            settings.timeout_seconds,
        )
        answer = _provider_answer(data, "gemini")
    else:
        data = _post(
            settings.endpoint or "http://localhost:11434/api/generate",
            {
                "model": settings.model,
                "prompt": body,
                "stream": False,
                "options": {
                    "temperature": settings.temperature,
                    "num_ctx": settings.context_window_tokens,
                    "num_predict": settings.max_output_tokens,
                },
            },
            {"Content-Type": "application/json"},
            settings.timeout_seconds,
        )
        answer = _provider_answer(data, "ollama")
    return _documentation_response(answer, symbols)


def _provider_answer(data: dict[str, Any], provider: str) -> str:
    """Extract provider text while keeping malformed envelopes inside the domain boundary."""
    try:
        if provider == "openai":
            answer = data["choices"][0]["message"]["content"]
        elif provider == "gemini":
            answer = data["candidates"][0]["content"]["parts"][0]["text"]
        else:
            answer = data["response"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIProviderError(f"The {provider} provider returned an invalid response.") from exc
    if not isinstance(answer, str):
        raise AIProviderError(f"The {provider} provider returned an invalid response.")
    return answer


def _documentation_response(answer: str, symbols: list[Symbol]) -> dict[str, Documentation]:
    """Validate and normalize the structured documentation returned by a provider."""
    if not isinstance(answer, str):
        raise InvalidAIResponseError("The AI returned documentation in an invalid format.")
    try:
        parsed = json.loads(answer)
    except (json.JSONDecodeError, TypeError) as exc:
        raise InvalidAIResponseError("The AI returned invalid documentation JSON.") from exc
    if not isinstance(parsed, dict):
        raise InvalidAIResponseError("The AI response must be a JSON object of documentation.")

    requested = {symbol.name: symbol for symbol in symbols}
    if set(parsed) != set(requested):
        raise InvalidAIResponseError("The AI response must document every requested symbol.")
    normalized: dict[str, Documentation] = {}
    for name, value in parsed.items():
        if not isinstance(name, str) or name not in requested:
            raise InvalidAIResponseError("The AI response contains an unexpected symbol.")
        normalized[name] = _normalize_documentation(name, value, requested[name])
    return normalized


def _normalize_documentation(name: str, value: Any, symbol: Symbol) -> Documentation:
    """Validate one symbol's generated description and argument mapping."""
    if isinstance(value, str):
        legacy_description = value.strip()
        if not legacy_description:
            raise InvalidAIResponseError("Each symbol must contain a non-empty description.")
        return Documentation(legacy_description)
    if not isinstance(value, dict):
        raise InvalidAIResponseError("Each symbol must contain documentation details.")
    structured_description = value.get("description")
    arguments = value.get("arguments", {})
    valid_arguments = isinstance(arguments, dict) and all(
        isinstance(argument, str)
        and isinstance(argument_description, str)
        and bool(argument_description.strip())
        for argument, argument_description in arguments.items()
    )
    if (
        not isinstance(structured_description, str)
        or not structured_description.strip()
        or not valid_arguments
    ):
        raise InvalidAIResponseError(
            "Each symbol must contain a non-empty description and argument descriptions."
        )
    if set(arguments) != set(symbol.args):
        raise InvalidAIResponseError(
            f"Documentation for `{name}` must describe every requested argument."
        )
    return Documentation(
        structured_description.strip(),
        {
            argument: argument_description.strip()
            for argument, argument_description in arguments.items()
        },
    )
