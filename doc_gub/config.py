"""Configuration loading with explicit, auditable precedence."""
from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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
    language: str = "English"
    python_format: str = "google"
    javascript_format: str = "jsdoc"
    output: str = "preview"
    confirm: bool = True
    max_files_per_request: int = 50
    max_file_bytes: int = 100000
    exclude: tuple[str, ...] = (
        "**/node_modules/**", "**/dist/**", "**/build/**", "**/*.min.js", "**/package-lock.json",
    )
    include: tuple[str, ...] = ()

    @property
    def model_candidates(self) -> tuple[str, ...]:
        """Propriedade getter que retorna uma tupla contendo os modelos candidatos configurados para uso (padrão: `self.models` ou apenas `self.model`)."""
        return self.models or (self.model,)


TEMPLATE = '''[ai]
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
language = "English"
python_format = "google"
javascript_format = "jsdoc"
output = "preview"
confirm = true

[limits]
max_files_per_request = 50
max_file_bytes = 100000
exclude = ["**/node_modules/**", "**/dist/**", "**/build/**", "**/*.min.js", "**/package-lock.json"]
'''


def _read(path: Path) -> dict[str, Any]:
    """Função auxiliar que carrega configurações de um caminho TOML especificado, combinando seções 'ai', 'documentation' e 'limits'.
    
    Args:
        path: Description of path."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise DocGubError(f"Invalid TOML configuration in {path}: {exc}") from exc
    return {**raw.get("ai", {}), **raw.get("documentation", {}), **raw.get("limits", {})}


def _env() -> dict[str, Any]:
    """Função auxiliar que coleta valores de variáveis de ambiente prefixadas com 'DOC_GUB_' para configurar o objeto Settings."""
    names = (
        "provider", "model", "endpoint", "max_input_tokens", "context_window_tokens",
        "max_output_tokens", "temperature", "timeout_seconds", "selection", "coverage",
        "existing_docs", "language", "python_format", "javascript_format", "output", "confirm",
        "max_files_per_request", "max_file_bytes",
    )
    return {
        name: os.environ[f"DOC_GUB_{name.upper()}"]
        for name in names if f"DOC_GUB_{name.upper()}" in os.environ
    }


def load(repo_root: Path, config_path: Path | None = None, **overrides: Any) -> Settings:
    """Carrega as configurações do sistema seguindo uma ordem estrita de precedência: (1) Configuração global em `~/.config/doc-gub/config.toml`, (2) Configuração local em `./.doc-gub.toml` (relativo ao repositório), (3) Variáveis de ambiente, e finalmente (4) Argumentos de sobrescrita passados diretamente.
    
    Args:
        repo_root: Description of repo_root.
        config_path: Description of config_path."""
    values: dict[str, Any] = asdict(Settings())
    for layer in (_read(Path.home() / ".config/doc-gub/config.toml"), _read(repo_root / ".doc-gub.toml"), _env()):
        values.update(layer)
    if config_path:
        values.update(_read(config_path))
    values.update({key: value for key, value in overrides.items() if value is not None})
    for name in ("models", "exclude", "include"):
        if not isinstance(values[name], (list, tuple)) or not all(isinstance(item, str) and item for item in values[name]):
            raise DocGubError(f"`{name}` must be a list of non-empty strings.")
        values[name] = tuple(values[name])
    if len(values["models"]) > 3:
        raise DocGubError("`models` accepts at most three candidates.")
    for name in ("max_input_tokens", "context_window_tokens", "max_output_tokens", "timeout_seconds", "max_files_per_request", "max_file_bytes"):
        values[name] = int(values[name])
        if values[name] <= 0:
            raise DocGubError(f"`{name}` must be positive.")
    values["temperature"] = float(values["temperature"])
    if not isinstance(values["language"], str) or not values["language"].strip():
        raise DocGubError("`language` must be a non-empty string.")
    values["language"] = values["language"].strip()
    if values["provider"] not in {"openai", "gemini", "ollama"}:
        raise DocGubError("Provider must be openai, gemini, or ollama.")
    allowed = {"selection": {"changes", "repository"}, "coverage": {"missing", "minimal", "all"}, "existing_docs": {"preserve", "replace"}, "python_format": {"google", "numpy", "sphinx"}, "output": {"preview", "apply"}}
    for name, options in allowed.items():
        if values[name] not in options:
            raise DocGubError(f"`{name}` must be one of: {', '.join(sorted(options))}.")
    if values["javascript_format"] != "jsdoc":
        raise DocGubError("`javascript_format` must be `jsdoc`.")
    if values["max_input_tokens"] + values["max_output_tokens"] > values["context_window_tokens"]:
        raise DocGubError("max_input_tokens + max_output_tokens must not exceed context_window_tokens.")
    return Settings(**values)
