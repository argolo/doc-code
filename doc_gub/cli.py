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
from .symbols import discover, eligible, source_for_symbol

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
        """Função interna usada por _loading para atualizar o estado visual do spinner em cada quadro."""
        for frame in "|/-\\":
            if stop.is_set():
                break
            typer.secho(f"\r{message} {frame}", fg=typer.colors.CYAN, nl=False, err=True)
            sleep(0.12)

    def loop() -> None:
        """Função interna usada por _loading para manter o loop de atualização do spinner até que seja interrompido."""
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
    """Exibe um resumo factual sobre os resultados da geração de documentação (arquivos, símbolos, tempo).
    
    Args:
        item: Description of item.
        model: Description of model.
        elapsed: Description of elapsed."""
    relative = item.path.as_posix()
    typer.echo()
    typer.secho(relative, fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Symbols: {len(item.symbols)} | changed: {len(item.changed)} | ignored: {len(item.ignored)}")
    typer.echo(f"Model: {model} | Generated in {elapsed:.2f}s")
    if item.diff:
        typer.secho("Status: documentation changes generated.", fg=typer.colors.GREEN)
    else:
        typer.secho("No documentation changes needed.", fg=typer.colors.BRIGHT_BLACK)


def _show_skipped(relative: str, reason: str) -> None:
    """Report a file-level generation failure without stopping the remaining scope."""
    typer.echo()
    typer.secho(f"Skipped documentation: {relative}", fg=typer.colors.YELLOW, bold=True)
    typer.secho(f"Reason: {reason}")


def _show_symbol_completed(relative: str, symbol_name: str, applied: bool) -> None:
    """Show a durable per-symbol progress event in symbol request mode."""
    action = "Applied documentation" if applied else "Generated documentation"
    typer.secho(f"{action}: {relative}:{symbol_name}", fg=typer.colors.GREEN)


def _show_check(missing: dict[str, list[str]]) -> None:
    """Print the actionable output used by CI and local check runs."""
    if not missing:
        typer.secho("Documentation check passed.", fg=typer.colors.GREEN, bold=True)
        return
    typer.secho("Documentation is missing:", fg=typer.colors.YELLOW, bold=True)
    for relative, symbols in missing.items():
        typer.echo(f"  {relative}: {', '.join(symbols)}")


def _model_for_attempt(candidates: tuple[str, ...], attempt: int) -> str:
    """Cycle configured fallback models instead of pinning later retries to the last one."""
    return candidates[(attempt - 1) % len(candidates)]


def _undocumented_symbols(content: str, suffix: str) -> list[str]:
    """Return symbols that make `--check` fail without requesting AI output."""
    return [symbol.name for symbol in discover(content, suffix) if not symbol.has_doc]


@app.command()
def doc_gub(
    paths: list[Path] = typer.Argument(
        None,
        metavar="[PATH]...",
        help="One or more files or directories inside the Git worktree.",
    ),
    output: Optional[str] = typer.Option(None, "--output", help="preview (default) or apply."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Alias for --output preview."),
    check: bool = typer.Option(
        False, "--check", help="Exit with status 1 when eligible symbols are undocumented; never calls AI."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip global confirmation when applying."),
    coverage: Optional[str] = typer.Option(None, "--coverage", help="missing, minimal, or all."),
    existing_docs: Optional[str] = typer.Option(None, "--existing-docs", help="preserve or replace."),
    request_scope: Optional[str] = typer.Option(
        None, "--request-scope", help="file (default) or symbol; symbol sends one source scope per request."
    ),
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
        settings = load(repo.root, config, output="preview" if dry_run or check else output, coverage=coverage, existing_docs=existing_docs, request_scope=request_scope, language=language, selection=selection, python_format=python_format, provider=provider, model=model, timeout_seconds=timeout_seconds, max_input_tokens=max_input_tokens, context_window_tokens=context_window_tokens)
        files = resolve(repo, paths, settings)
        if check:
            missing: dict[str, list[str]] = {}
            for relative in files:
                content = (repo.root / relative).read_text(encoding="utf-8")
                undocumented = _undocumented_symbols(content, Path(relative).suffix)
                if undocumented:
                    missing[relative] = undocumented
            _show_check(missing)
            if missing:
                raise typer.Exit(1)
            return
        if settings.output == "apply" and settings.confirm and not yes:
            if not sys.stdin.isatty():
                raise DocGubError("Confirmation requires an interactive terminal; use --yes for automation.")
            if not typer.confirm(
                f"Apply documentation as each of {len(files)} file(s) completes?", default=False
            ):
                typer.secho("Cancelled.", fg=typer.colors.YELLOW)
                return

        completed: list[str] = []
        skipped: list[str] = []
        for relative in files:
            file_path = repo.root / relative
            content = file_path.read_text(encoding="utf-8")
            symbols = discover(content, file_path.suffix)
            targets = [
                symbol
                for symbol in symbols
                if eligible(symbol, settings.coverage)
                and (not symbol.has_doc or settings.existing_docs == "replace")
            ]
            if not targets:
                item = prepare(file_path, symbols, {}, settings)
                _show(item, "not used", 0)
                continue
            started = perf_counter()
            last_error: Exception | None = None
            requests = [(content, targets)] if settings.request_scope == "file" else [
                (source_for_symbol(content, symbol, file_path.suffix), [symbol]) for symbol in targets
            ]
            descriptions: dict[str, str] = {}
            generation_failed = False
            for source, requested_symbols in requests:
                for attempt in range(1, MAX_AI_ATTEMPTS + 1):
                    candidates = settings.model_candidates
                    candidate = _model_for_attempt(candidates, attempt)
                    label = relative if settings.request_scope == "file" else f"{relative}:{requested_symbols[0].name}"
                    try:
                        with _loading(
                            f"Generating documentation for [{label}] with model [{candidate}] "
                            f"({attempt}/{MAX_AI_ATTEMPTS})..."
                        ):
                            generated = documentation_for(
                                source, requested_symbols, replace(settings, model=candidate, models=())
                            )
                            descriptions.update(generated)
                        if settings.request_scope == "symbol":
                            target = requested_symbols[0]
                            if settings.output == "apply":
                                current_symbols = discover(
                                    file_path.read_text(encoding="utf-8"), file_path.suffix
                                )
                                current_target = next(
                                    (
                                        symbol
                                        for symbol in current_symbols
                                        if symbol.name == target.name and symbol.kind == target.kind
                                    ),
                                    None,
                                )
                                if current_target is None:
                                    raise DocGubError(
                                        f"{relative}: symbol `{target.name}` changed during generation."
                                    )
                                item = prepare(
                                    file_path,
                                    current_symbols,
                                    generated,
                                    settings,
                                    selected_symbols=[current_target],
                                )
                                if item.diff:
                                    apply(item)
                                    if relative not in completed:
                                        completed.append(relative)
                                _show_symbol_completed(relative, target.name, bool(item.diff))
                            else:
                                _show_symbol_completed(relative, target.name, False)
                        break
                    except (AIProviderError, InvalidAIResponseError) as exc:
                        last_error = exc
                        if attempt < MAX_AI_ATTEMPTS:
                            next_model = _model_for_attempt(candidates, attempt + 1)
                            reason = "AI request timed out" if isinstance(exc, AITimeoutError) else str(exc)
                            typer.secho(
                                f"{reason} with model [{candidate}]. Retrying with model "
                                f"[{next_model}] ({attempt + 1}/{MAX_AI_ATTEMPTS})...",
                                fg=typer.colors.YELLOW,
                            )
                else:
                    generation_failed = True
                    break
            if generation_failed:
                skipped.append(relative)
                _show_skipped(
                    relative, f"generation failed after {MAX_AI_ATTEMPTS} attempts: {last_error}"
                )
                continue
            if settings.request_scope == "symbol" and settings.output == "apply":
                continue
            try:
                item = prepare(file_path, symbols, descriptions, settings)
            except DocGubError as exc:
                skipped.append(relative)
                _show_skipped(relative, str(exc))
                continue
            _show(item, candidate, perf_counter() - started)
            if settings.output == "apply" and item.diff:
                try:
                    apply(item)
                except DocGubError as exc:
                    skipped.append(relative)
                    _show_skipped(relative, str(exc))
                    continue
                completed.append(relative)
                typer.secho(f"Applied documentation: {relative}", fg=typer.colors.GREEN)

        if settings.output == "preview":
            if skipped:
                typer.secho(f"Skipped files: {len(skipped)}", fg=typer.colors.YELLOW, bold=True)
            return
        typer.secho(f"Documentation applied to {len(completed)} file(s).", fg=typer.colors.GREEN, bold=True)
        if skipped:
            typer.secho(f"Skipped files: {len(skipped)}", fg=typer.colors.YELLOW, bold=True)
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
