"""Provider calls and strict JSON response validation for documentation descriptions."""
from __future__ import annotations

import json
import os
from math import ceil
from urllib.error import URLError
from urllib.request import Request, urlopen

from .config import Settings
from .errors import AIProviderError, AITimeoutError, DocGubError, InvalidAIResponseError
from .symbols import Symbol


def estimate_tokens(text: str) -> int:
    return ceil(len(text.encode("utf-8")) / 3)


def prompt(content: str, symbols: list[Symbol], language: str = "English") -> str:
    names = [{"symbol": item.name, "kind": item.kind, "arguments": item.args} for item in symbols]
    return (
        "Return only a JSON object mapping each requested symbol to a concise factual "
        "documentation description. Do not include Markdown or code fences. Write all "
        f"documentation text in {json.dumps(language, ensure_ascii=False)}. Requested symbols: "
        + json.dumps(names)
        + "\n\nSOURCE:\n"
        + content
    )


def _post(url: str, payload: dict, headers: dict[str, str], timeout: int) -> dict:
    try:
        with urlopen(Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST"), timeout=timeout) as response:  # nosec B310 - user-configured endpoint
            return json.loads(response.read())
    except TimeoutError as exc:
        raise AITimeoutError(f"Unable to contact the AI provider: {exc}") from exc
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise AITimeoutError(f"Unable to contact the AI provider: {exc.reason}") from exc
        raise AIProviderError(f"Unable to contact the AI provider: {exc}") from exc
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise AIProviderError("The AI provider returned an invalid response.") from exc


def documentation_for(content: str, symbols: list[Symbol], settings: Settings) -> dict[str, str]:
    body = prompt(content, symbols, settings.language)
    tokens = estimate_tokens(body)
    if tokens > settings.max_input_tokens or tokens + settings.max_output_tokens > settings.context_window_tokens:
        raise DocGubError("AI input exceeds configured token limits; narrow the scope or increase limits.")
    if settings.provider == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise DocGubError("OPENAI_API_KEY is not configured.")
        data = _post(settings.endpoint or "https://api.openai.com/v1/chat/completions", {"model": settings.model, "messages": [{"role": "user", "content": body}], "max_tokens": settings.max_output_tokens, "temperature": settings.temperature}, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, settings.timeout_seconds)
        answer = data["choices"][0]["message"]["content"]
    elif settings.provider == "gemini":
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise DocGubError("GEMINI_API_KEY is not configured.")
        endpoint = settings.endpoint or f"https://generativelanguage.googleapis.com/v1beta/models/{settings.model}:generateContent?key={key}"
        data = _post(endpoint, {"contents": [{"parts": [{"text": body}]}]}, {"Content-Type": "application/json"}, settings.timeout_seconds)
        answer = data["candidates"][0]["content"]["parts"][0]["text"]
    else:
        data = _post(settings.endpoint or "http://localhost:11434/api/generate", {"model": settings.model, "prompt": body, "stream": False, "options": {"temperature": settings.temperature, "num_ctx": settings.context_window_tokens, "num_predict": settings.max_output_tokens}}, {"Content-Type": "application/json"}, settings.timeout_seconds)
        answer = data["response"]
    try:
        parsed = json.loads(answer)
    except json.JSONDecodeError as exc:
        raise InvalidAIResponseError("The AI returned invalid documentation JSON.") from exc
    if not isinstance(parsed, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in parsed.items()):
        raise InvalidAIResponseError("The AI response must be a JSON object of symbol descriptions.")
    return parsed
