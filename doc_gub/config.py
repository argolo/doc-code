"""Configuration loading with explicit, auditable precedence."""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass
from ipaddress import ip_address
from math import isfinite
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import DocGubError

_OLLAMA_MODEL = "qwen2.5-coder:14b"
_OLLAMA_MODELS = (_OLLAMA_MODEL, "gemma4:e4b")
_PROVIDER_MODELS = {
    "ollama": _OLLAMA_MODEL,
    "openai": "gpt-5.6-sol",
    "gemini": "gemini-3.6-flash",
}


@dataclass(frozen=True)
class Settings:
    """Store immutable operational settings."""

    provider: str = "ollama"
    model: str = _OLLAMA_MODEL
    models: tuple[str, ...] = _OLLAMA_MODELS
    endpoint: str | None = None
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

    def __post_init__(self) -> None:
        """Replace Ollama-only defaults when another provider is selected directly."""
        if self.provider == "ollama":
            return
        if self.model == _OLLAMA_MODEL:
            object.__setattr__(self, "model", _PROVIDER_MODELS.get(self.provider, self.model))
        if self.models == _OLLAMA_MODELS:
            object.__setattr__(self, "models", ())

    @property
    def model_candidates(self) -> tuple[str, ...]:
        """Return fallback models, or the primary model when no fallbacks exist."""
        return self.models or (self.model,)


TEMPLATE = """[ai]
provider = "ollama"
models = ["qwen2.5-coder:14b", "gemma4:e4b"]
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

_SEQUENCE_OPTIONS = ("models", "exclude", "include")
_POSITIVE_INTEGER_OPTIONS = (
    "max_input_tokens",
    "context_window_tokens",
    "max_output_tokens",
    "timeout_seconds",
    "max_files_per_request",
    "max_file_bytes",
)
_CHOICE_OPTIONS = {
    "provider": {"openai", "gemini", "ollama"},
    "selection": {"changes", "repository"},
    "coverage": {"missing", "minimal", "all"},
    "existing_docs": {"preserve", "replace"},
    "request_scope": {"file", "symbol"},
    "python_format": {"google", "numpy", "sphinx"},
    "output": {"preview", "apply"},
}


def _configuration_exists(path: Path, required: bool) -> bool:
    """Validate a configuration path and return whether an optional file exists."""
    try:
        exists = path.exists()
        is_file = path.is_file()
    except OSError as exc:
        raise DocGubError(f"Unable to inspect configuration {path}: {exc}") from exc
    if not exists and not required:
        return False
    if not exists:
        raise DocGubError(f"Configuration file does not exist: {path}.")
    if not is_file:
        raise DocGubError(f"Configuration path is not a regular file: {path}.")
    return True


def _read_toml(path: Path) -> dict[str, Any]:
    """Decode a TOML object while preserving its path in domain errors."""
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise DocGubError(f"Invalid TOML configuration in {path}: {exc}") from exc
    except OSError as exc:
        raise DocGubError(f"Unable to read configuration {path}: {exc}") from exc


def _read(path: Path, *, required: bool = False) -> dict[str, Any]:
    """Read and flatten supported sections from a TOML configuration file."""
    if not _configuration_exists(path, required):
        return {}
    raw = _read_toml(path)
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
    """Read supported ``DOC_GUB_*`` environment variables."""
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


def _endpoint(value: Any, provider: str) -> str | None:
    """Validate an optional HTTP(S) endpoint."""
    if value is None:
        return None
    endpoint = _non_empty_string(value, "endpoint")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DocGubError("`endpoint` must be an absolute HTTP(S) URL.")
    if provider in {"openai", "gemini"} and parsed.scheme != "https":
        hostname = parsed.hostname or ""
        try:
            loopback = ip_address(hostname).is_loopback
        except ValueError:
            loopback = hostname.casefold() == "localhost"
        if not loopback:
            raise DocGubError(
                "Authenticated provider endpoints must use HTTPS unless they are loopback URLs."
            )
    return endpoint


def load(repo_root: Path, config_path: Path | None = None, **overrides: Any) -> Settings:
    """Load global, repository, environment, explicit-file, and CLI settings in order."""
    values: dict[str, Any] = asdict(Settings())
    for layer in (
        _read(Path.home() / ".config/doc-gub/config.toml"),
        _read(repo_root / ".doc-gub.toml"),
        _env(),
    ):
        _apply_layer(values, layer)
    if config_path:
        _apply_layer(values, _read(config_path, required=True))
    _apply_layer(values, {key: value for key, value in overrides.items() if value is not None})
    return _validated_settings(values)


def _normalize_sequences(values: dict[str, Any]) -> None:
    """Validate sequence settings and store immutable tuples."""
    for name in _SEQUENCE_OPTIONS:
        if not isinstance(values[name], (list, tuple)) or not all(
            isinstance(item, str) and item for item in values[name]
        ):
            raise DocGubError(f"`{name}` must be a list of non-empty strings.")
        values[name] = tuple(values[name])
    if len(values["models"]) > 3:
        raise DocGubError("`models` accepts at most three candidates.")


def _normalize_temperature(value: Any, provider: str) -> float:
    """Validate and return a sampling temperature supported by the selected provider."""
    if isinstance(value, bool):
        raise DocGubError("`temperature` must be a finite number.")
    try:
        temperature = float(value)
    except (TypeError, ValueError) as exc:
        raise DocGubError("`temperature` must be a finite number.") from exc
    if not isfinite(temperature):
        raise DocGubError("`temperature` must be a finite number.")
    if temperature < 0:
        raise DocGubError("`temperature` must be non-negative.")
    if provider in {"openai", "gemini"} and temperature > 2:
        raise DocGubError(f"`temperature` must be between 0 and 2 for {provider}.")
    return temperature


def _validate_token_budget(values: dict[str, Any]) -> None:
    """Ensure the configured request fits in the model context window."""
    if values["max_input_tokens"] + values["max_output_tokens"] > values["context_window_tokens"]:
        raise DocGubError(
            "max_input_tokens + max_output_tokens must not exceed context_window_tokens."
        )


def _validated_settings(values: dict[str, Any]) -> Settings:
    """Normalize merged configuration values and build immutable settings."""
    _normalize_sequences(values)
    for name in _POSITIVE_INTEGER_OPTIONS:
        values[name] = _positive_int(values[name], name)
    for name, options in _CHOICE_OPTIONS.items():
        values[name] = _choice(values[name], name, options)
    values["temperature"] = _normalize_temperature(values["temperature"], values["provider"])
    values["language"] = _non_empty_string(values["language"], "language")
    values["model"] = _non_empty_string(values["model"], "model")
    values["endpoint"] = _endpoint(values["endpoint"], values["provider"])
    if values["javascript_format"] != "jsdoc":
        raise DocGubError("`javascript_format` must be `jsdoc`.")
    values["confirm"] = _boolean(values["confirm"], "confirm")
    _validate_token_budget(values)
    return Settings(**values)


def _apply_layer(values: dict[str, Any], layer: dict[str, Any]) -> None:
    """Apply one precedence layer while resetting provider-specific lower-layer defaults."""
    if not layer:
        return
    provider = layer.get("provider")
    if provider is not None and provider != values["provider"]:
        values["model"] = _PROVIDER_MODELS.get(provider, values["model"])
        values["models"] = ()
        values["endpoint"] = None
    if "model" in layer and "models" not in layer:
        values["models"] = ()
    values.update(layer)
