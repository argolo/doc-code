"""Provider calls and strict JSON response validation for documentation descriptions."""

from __future__ import annotations

import json
import os
from math import ceil
from typing import Any, cast
from urllib.error import URLError
from urllib.request import Request, urlopen

from .config import Settings
from .errors import AIProviderError, AITimeoutError, DocGubError, InvalidAIResponseError
from .symbols import Documentation, Symbol


def estimate_tokens(text: str) -> int:
    """Estima o número aproximado de tokens necessários para uma determinada string, utilizando um cálculo baseado na codificação UTF-8. Essencial para verificar limites de contexto da API.

    Args:
        text: Description of text.

    """
    return ceil(len(text.encode("utf-8")) / 3)


def prompt(content: str, symbols: list[Symbol], language: str = "English") -> str:
    """Constrói um prompt formatado e detalhado, incluindo instruções específicas (JSON output), símbolos solicitados e o conteúdo fonte, otimizado para ser enviado a modelos de linguagem de IA.

    Args:
        content: Description of content.
        symbols: Description of symbols.
        language: Description of language.

    """
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
    """Função utilitária que realiza requisições HTTP POST para endpoints externos (APIs). É responsável por enviar payloads JSON, gerenciar cabeçalhos e tratar exceções como timeouts ou erros de resposta da API.

    Args:
        url: Description of url.
        payload: Description of payload.
        headers: Description of headers.
        timeout: Description of timeout.

    """
    try:
        with urlopen(
            Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST"),
            timeout=timeout,
        ) as response:  # nosec B310 - user-configured endpoint
            decoded = json.loads(response.read())
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
    """Função principal que orquestra a geração completa da documentação. Recebe o conteúdo fonte, os símbolos e as configurações do provedor de IA (OpenAI, Gemini, etc.), validando limites de tokens e retornando um dicionário JSON com descrições factuais para cada símbolo.

    Args:
        content: Description of content.
        symbols: Description of symbols.
        settings: Description of settings.

    """
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
                "max_tokens": settings.max_output_tokens,
                "temperature": settings.temperature,
            },
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            settings.timeout_seconds,
        )
        answer = data["choices"][0]["message"]["content"]
    elif settings.provider == "gemini":
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise DocGubError("GEMINI_API_KEY is not configured.")
        endpoint = (
            settings.endpoint
            or f"https://generativelanguage.googleapis.com/v1beta/models/{settings.model}:generateContent?key={key}"
        )
        data = _post(
            endpoint,
            {"contents": [{"parts": [{"text": body}]}]},
            {"Content-Type": "application/json"},
            settings.timeout_seconds,
        )
        answer = data["candidates"][0]["content"]["parts"][0]["text"]
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
        answer = data["response"]
    return _documentation_response(answer, symbols)


def _documentation_response(answer: str, symbols: list[Symbol]) -> dict[str, Documentation]:
    """Validate and normalize the structured documentation returned by a provider."""
    try:
        parsed = json.loads(answer)
    except json.JSONDecodeError as exc:
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
        if isinstance(value, str):
            normalized[name] = Documentation(value)
            continue
        if not isinstance(value, dict):
            raise InvalidAIResponseError("Each symbol must contain documentation details.")
        description = value.get("description")
        arguments = value.get("arguments", {})
        if (
            not isinstance(description, str)
            or not isinstance(arguments, dict)
            or not all(
                isinstance(argument, str) and isinstance(argument_description, str)
                for argument, argument_description in arguments.items()
            )
        ):
            raise InvalidAIResponseError(
                "Each symbol must contain a string description and string argument descriptions."
            )
        expected_arguments = set(requested[name].args)
        if set(arguments) != expected_arguments:
            raise InvalidAIResponseError(
                f"Documentation for `{name}` must describe every requested argument."
            )
        normalized[name] = Documentation(description, arguments)
    return normalized
