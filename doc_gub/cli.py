"""Typer command-line interface for doc-gub."""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread
from time import perf_counter, sleep
from typing import Optional

import typer

from .ai import documentation_for
from .config import TEMPLATE, load
from .editor import PreparedFile, apply, prepare
from .errors import AIProviderError, AITimeoutError, DocGubError, InvalidAIResponseError
from .git import GitRepo
from .scope import resolve
from .symbols import discover, eligible

app = typer.Typer(add_completion=False, no_args_is_help=False)
config_app = typer.Typer(help="Create and inspect doc-gub configuration.", no_args_is_help=True)
MAX_AI_ATTEMPTS = 3


@contextmanager
def _loading(message: str):
    """Show an interactive spinner or one stable log line while the AI responds."""
    typer.echo(err=True)
    if not sys.stderr.isatty():
        typer.secho(message, fg=typer.colors.CYAN, err=True)
        yield
        return

    stop = Event()

    def spin() -> None:
        for frame in "|/-\\":
            if stop.is_set():
                break
            typer.secho(f"\r{message} {frame}", fg=typer.colors.CYAN, nl=False, err=True)
            sleep(0.12)

    def loop() -> None:
        while not stop.is_set():
            spin()

    worker = Thread(target=loop, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=1)
        typer.echo("\r" + " " * (len(message) + 2) + "\r", nl=False, err=True)


def _show(item: PreparedFile, model: str, elapsed: float) -> None:
    relative = item.path.as_posix()
    typer.echo()
    typer.secho(relative, fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Symbols: {len(item.symbols)} | changed: {len(item.changed)} | ignored: {len(item.ignored)}")
    typer.echo(f"Model: {model} | Generated in {elapsed:.2f}s")
    if item.diff:
        typer.secho("Status: documentation changes generated.", fg=typer.colors.GREEN)
    else:
        typer.secho("No documentation changes needed.", fg=typer.colors.BRIGHT_BLACK)


@app.command()
def doc_gub(
    path: Optional[Path] = typer.Argument(None, metavar="[PATH]", help="File or directory inside the Git worktree."),
    output: Optional[str] = typer.Option(None, "--output", help="preview (default) or apply."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Alias for --output preview."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip global confirmation when applying."),
    coverage: Optional[str] = typer.Option(None, "--coverage", help="missing, minimal, or all."),
    existing_docs: Optional[str] = typer.Option(None, "--existing-docs", help="preserve or replace."),
    language: Optional[str] = typer.Option(
        None, "--language", help="Language used for generated documentation."
    ),
    selection: Optional[str] = typer.Option(None, "--selection", help="changes or repository."),
    python_format: Optional[str] = typer.Option(None, "--format", help="google, numpy, or sphinx for Python."),
    provider: Optional[str] = typer.Option(None, "--provider", help="openai, gemini, or ollama."),
    model: Optional[str] = typer.Option(None, "--model", help="Model name."),
    timeout_seconds: Optional[int] = typer.Option(None, "--timeout-seconds", min=1),
    max_input_tokens: Optional[int] = typer.Option(None, "--max-input-tokens", min=1),
    context_window_tokens: Optional[int] = typer.Option(None, "--context-window-tokens", min=1),
    config: Optional[Path] = typer.Option(None, "--config", help="Additional TOML configuration."),
) -> None:
    """Preview or safely apply AI-generated docs to Python, JavaScript and TypeScript."""
    try:
        repo = GitRepo()
        settings = load(repo.root, config, output="preview" if dry_run else output, coverage=coverage, existing_docs=existing_docs, language=language, selection=selection, python_format=python_format, provider=provider, model=model, timeout_seconds=timeout_seconds, max_input_tokens=max_input_tokens, context_window_tokens=context_window_tokens)
        prepared: list[PreparedFile] = []
        for relative in resolve(repo, path, settings):
            file_path = repo.root / relative
            content = file_path.read_text(encoding="utf-8")
            symbols = discover(content, file_path.suffix)
            targets = [symbol for symbol in symbols if eligible(symbol, settings.coverage)]
            if not targets:
                item = prepare(file_path, symbols, {}, settings)
                _show(item, "not used", 0)
                prepared.append(item)
                continue
            started = perf_counter()
            last_error: Exception | None = None
            for attempt in range(1, MAX_AI_ATTEMPTS + 1):
                candidate = settings.model_candidates[
                    min(attempt - 1, len(settings.model_candidates) - 1)
                ]
                try:
                    with _loading(
                        f"Generating documentation for [{relative}] with model [{candidate}] "
                        f"({attempt}/{MAX_AI_ATTEMPTS})..."
                    ):
                        descriptions = documentation_for(
                            content, targets, replace(settings, model=candidate, models=())
                        )
                    break
                except (AITimeoutError, InvalidAIResponseError) as exc:
                    last_error = exc
                    if attempt < MAX_AI_ATTEMPTS:
                        next_model = settings.model_candidates[
                            min(attempt, len(settings.model_candidates) - 1)
                        ]
                        reason = "AI request timed out" if isinstance(exc, AITimeoutError) else "Invalid AI response"
                        typer.secho(
                            f"{reason} with model [{candidate}]. Retrying with model "
                            f"[{next_model}] ({attempt + 1}/{MAX_AI_ATTEMPTS})...",
                            fg=typer.colors.YELLOW,
                        )
            else:
                raise DocGubError(f"{relative}: generation failed after {MAX_AI_ATTEMPTS} attempts: {last_error}")
            item = prepare(file_path, symbols, descriptions, settings)
            _show(item, candidate, perf_counter() - started)
            prepared.append(item)
        changed = [item for item in prepared if item.diff]
        if settings.output == "preview" or not changed:
            return
        if settings.confirm and not yes:
            if not sys.stdin.isatty():
                raise DocGubError("Confirmation requires an interactive terminal; use --yes for automation.")
            if not typer.confirm(f"Apply documentation changes to {len(changed)} file(s)?", default=False):
                typer.secho("Cancelled.", fg=typer.colors.YELLOW)
                return
        for item in changed:
            apply(item)
        typer.secho(f"Documentation applied to {len(changed)} file(s).", fg=typer.colors.GREEN, bold=True)
    except (DocGubError, UnicodeDecodeError, SyntaxError) as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, bold=True, err=True)
        raise typer.Exit(1) from exc
    except AIProviderError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, bold=True, err=True)
        raise typer.Exit(1) from exc


@config_app.command("init")
def config_init(path: Path = typer.Option(Path(".doc-gub.toml"), "--path")) -> None:
    """Create a documentation configuration template without overwriting files."""
    if path.exists():
        typer.secho(f"Error: {path} already exists.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    path.write_text(TEMPLATE, encoding="utf-8")
    typer.secho(f"Configuration created at {path}.", fg=typer.colors.GREEN)


@config_app.command("show")
def config_show(config: Optional[Path] = typer.Option(None, "--config")) -> None:
    """Print effective configuration without credentials."""
    try:
        repo = GitRepo()
        typer.echo(json.dumps(load(repo.root, config).__dict__, ensure_ascii=False, indent=2))
    except DocGubError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


def main() -> None:
    """Dispatch `config` separately so it never looks like a path argument."""
    if len(sys.argv) > 1 and sys.argv[1] == "config":
        config_app(args=sys.argv[2:], prog_name="doc-gub config")
    else:
        app()
