"""Configuration loading with explicit, auditable precedence."""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass
from math import isfinite
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import DocGubError


@dataclass(frozen=True)
class Settings:
    """Classe imutável que armazena todas as configurações operacionais do sistema, incluindo parâmetros de modelo, limites de arquivo e formatos de documentação."""

    provider: str = "ollama"
    model: str = "qwen2.5-coder:14b"
    models: tuple[str, ...] = ("qwen2.5-coder:14b", "gemma4:e4b")
    endpoint: str | None = "http://localhost:11434/api/generate"
    max_input_tokens: int = 12000
    context_window_tokens: int = 32768
    max_output_tokens: int = 800
    temperature: float = 0.2
    timeout_seconds: int = 60
    selection: str = "changes"
    coverage: str = "missing"
    existing_docs: str = "preserve"
    request_scope: str = "file"
    language: str = "English"
    python_format: str = "google"
    javascript_format: str = "jsdoc"
    output: str = "preview"
    confirm: bool = True
    max_files_per_request: int = 50
    max_file_bytes: int = 100000
    exclude: tuple[str, ...] = (
        "**/node_modules/**",
        "**/dist/**",
        "**/build/**",
        "**/*.min.js",
        "**/package-lock.json",
    )
    include: tuple[str, ...] = ()

    @property
    def model_candidates(self) -> tuple[str, ...]:
        """Propriedade getter que retorna uma tupla contendo os modelos candidatos configurados para uso (padrão: `self.models` ou apenas `self.model`)."""
        return self.models or (self.model,)


TEMPLATE = """[ai]
provider = "ollama"
models = ["qwen2.5-coder:14b", "gemma4:e4b"]
endpoint = "http://localhost:11434/api/generate"
max_input_tokens = 12000
context_window_tokens = 32768
max_output_tokens = 800
temperature = 0.2
timeout_seconds = 60

[documentation]
selection = "changes"
coverage = "missing"
existing_docs = "preserve"
request_scope = "file"
language = "English"
python_format = "google"
javascript_format = "jsdoc"
output = "preview"
confirm = true

[limits]
max_files_per_request = 50
max_file_bytes = 100000
exclude = ["**/node_modules/**", "**/dist/**", "**/build/**", "**/*.min.js", "**/package-lock.json"]
"""

_SECTION_OPTIONS = {
    "ai": {
        "provider",
        "model",
        "models",
        "endpoint",
        "max_input_tokens",
        "context_window_tokens",
        "max_output_tokens",
        "temperature",
        "timeout_seconds",
    },
    "documentation": {
        "selection",
        "coverage",
        "existing_docs",
        "request_scope",
        "language",
        "python_format",
        "javascript_format",
        "output",
        "confirm",
    },
    "limits": {"max_files_per_request", "max_file_bytes", "exclude", "include"},
}


def _read(path: Path) -> dict[str, Any]:
    """Função auxiliar que carrega configurações de um caminho TOML especificado, combinando seções 'ai', 'documentation' e 'limits'.

    Args:
        path: Description of path.

    """
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise DocGubError(f"Invalid TOML configuration in {path}: {exc}") from exc
    unknown_sections = set(raw).difference(_SECTION_OPTIONS)
    if unknown_sections:
        raise DocGubError(
            f"Unknown configuration section(s) in {path}: {', '.join(sorted(unknown_sections))}."
        )
    values: dict[str, Any] = {}
    for section, options in _SECTION_OPTIONS.items():
        configured = raw.get(section, {})
        if not isinstance(configured, dict):
            raise DocGubError(f"Configuration section [{section}] in {path} must be a table.")
        unknown_options = set(configured).difference(options)
        if unknown_options:
            raise DocGubError(
                f"Unknown option(s) in {path} [{section}]: {', '.join(sorted(unknown_options))}."
            )
        values.update(configured)
    return values


def _env() -> dict[str, Any]:
    """Função auxiliar que coleta valores de variáveis de ambiente prefixadas com 'DOC_GUB_' para configurar o objeto Settings."""
    names = (
        "provider",
        "model",
        "endpoint",
        "max_input_tokens",
        "context_window_tokens",
        "max_output_tokens",
        "temperature",
        "timeout_seconds",
        "selection",
        "coverage",
        "existing_docs",
        "request_scope",
        "language",
        "python_format",
        "javascript_format",
        "output",
        "confirm",
        "max_files_per_request",
        "max_file_bytes",
    )
    return {
        name: os.environ[f"DOC_GUB_{name.upper()}"]
        for name in names
        if f"DOC_GUB_{name.upper()}" in os.environ
    }


def _positive_int(value: Any, name: str) -> int:
    """Return a positive integer configuration value."""
    if isinstance(value, bool):
        raise DocGubError(f"`{name}` must be a positive integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DocGubError(f"`{name}` must be a positive integer.") from exc
    if parsed <= 0:
        raise DocGubError(f"`{name}` must be positive.")
    return parsed


def _boolean(value: Any, name: str) -> bool:
    """Normalize a TOML or environment boolean value."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.casefold() in {"true", "false"}:
        return value.casefold() == "true"
    raise DocGubError(f"`{name}` must be true or false.")


def _choice(value: Any, name: str, options: set[str]) -> str:
    """Validate a string setting against its supported values."""
    if not isinstance(value, str) or value not in options:
        raise DocGubError(f"`{name}` must be one of: {', '.join(sorted(options))}.")
    return value


def _non_empty_string(value: Any, name: str) -> str:
    """Validate a required string setting and normalize surrounding whitespace."""
    if not isinstance(value, str) or not value.strip():
        raise DocGubError(f"`{name}` must be a non-empty string.")
    return value.strip()


def _endpoint(value: Any) -> str | None:
    """Validate an optional HTTP(S) endpoint."""
    if value is None:
        return None
    endpoint = _non_empty_string(value, "endpoint")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DocGubError("`endpoint` must be an absolute HTTP(S) URL.")
    return endpoint


def load(repo_root: Path, config_path: Path | None = None, **overrides: Any) -> Settings:
    """Carrega as configurações do sistema seguindo uma ordem estrita de precedência: (1) Configuração global em `~/.config/doc-gub/config.toml`, (2) Configuração local em `./.doc-gub.toml` (relativo ao repositório), (3) Variáveis de ambiente, e finalmente (4) Argumentos de sobrescrita passados diretamente.

    Args:
        repo_root: Description of repo_root.
        config_path: Description of config_path.
        **overrides: Explicit setting values with the highest precedence.

    """
    values: dict[str, Any] = asdict(Settings())
    for layer in (
        _read(Path.home() / ".config/doc-gub/config.toml"),
        _read(repo_root / ".doc-gub.toml"),
        _env(),
    ):
        values.update(layer)
    if config_path:
        values.update(_read(config_path))
    values.update({key: value for key, value in overrides.items() if value is not None})
    for name in ("models", "exclude", "include"):
        if not isinstance(values[name], (list, tuple)) or not all(
            isinstance(item, str) and item for item in values[name]
        ):
            raise DocGubError(f"`{name}` must be a list of non-empty strings.")
        values[name] = tuple(values[name])
    if len(values["models"]) > 3:
        raise DocGubError("`models` accepts at most three candidates.")
    for name in (
        "max_input_tokens",
        "context_window_tokens",
        "max_output_tokens",
        "timeout_seconds",
        "max_files_per_request",
        "max_file_bytes",
    ):
        values[name] = _positive_int(values[name], name)
    if isinstance(values["temperature"], bool):
        raise DocGubError("`temperature` must be a finite number.")
    try:
        values["temperature"] = float(values["temperature"])
    except (TypeError, ValueError) as exc:
        raise DocGubError("`temperature` must be a finite number.") from exc
    if not isfinite(values["temperature"]):
        raise DocGubError("`temperature` must be a finite number.")
    if not isinstance(values["language"], str) or not values["language"].strip():
        raise DocGubError("`language` must be a non-empty string.")
    values["language"] = values["language"].strip()
    values["model"] = _non_empty_string(values["model"], "model")
    values["endpoint"] = _endpoint(values["endpoint"])
    allowed = {
        "provider": {"openai", "gemini", "ollama"},
        "selection": {"changes", "repository"},
        "coverage": {"missing", "minimal", "all"},
        "existing_docs": {"preserve", "replace"},
        "request_scope": {"file", "symbol"},
        "python_format": {"google", "numpy", "sphinx"},
        "output": {"preview", "apply"},
    }
    for name, options in allowed.items():
        values[name] = _choice(values[name], name, options)
    if values["javascript_format"] != "jsdoc":
        raise DocGubError("`javascript_format` must be `jsdoc`.")
    values["confirm"] = _boolean(values["confirm"], "confirm")
    if values["max_input_tokens"] + values["max_output_tokens"] > values["context_window_tokens"]:
        raise DocGubError(
            "max_input_tokens + max_output_tokens must not exceed context_window_tokens."
        )
    return Settings(**values)
