"""Typer command-line interface for doc-gub."""

from __future__ import annotations

import json
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter

import typer

from .ai import documentation_for
from .config import TEMPLATE, Settings, load
from .editor import PreparedFile, apply, can_insert_documentation, prepare, validation_command
from .errors import AIProviderError, AITimeoutError, DocGubError, InvalidAIResponseError
from .git import GitRepo
from .scope import resolve
from .symbols import Documentation, Symbol, discover, needs_documentation, source_for_symbol

app = typer.Typer(add_completion=False, no_args_is_help=False)
config_app = typer.Typer(help="Create and inspect doc-gub configuration.", no_args_is_help=True)
MAX_AI_ATTEMPTS = 3
_DUPLICATE_SYMBOL_SUFFIX = re.compile(r"^(?P<name>.+)@L\d+:\d+$")


@contextmanager
def _loading(message: str):
    """Show one stable progress line while the AI responds interactively."""
    if not sys.stderr.isatty():
        yield
        return
    lines = message.splitlines()
    typer.secho(f"\r{message}", fg=typer.colors.CYAN, nl=False, err=True)
    try:
        yield
    finally:
        if len(lines) > 1:
            # Move up for each extra line and clear from cursor to end of screen
            typer.echo(f"\033[{len(lines) - 1}A\033[J", nl=False, err=True)
        else:
            typer.echo("\r" + " " * len(message) + "\r", nl=False, err=True)


def _show(item: PreparedFile, model: str, elapsed: float, show_diff: bool = False) -> None:
    """Display a generation summary and optionally its unified diff."""
    relative = (item.display_path or item.path).as_posix()
    typer.echo()
    typer.secho(relative, fg=typer.colors.CYAN, bold=True)
    typer.echo(
        f"Symbols: {len(item.symbols)} | changed: {len(item.changed)} | "
        f"ignored: {len(item.ignored)}"
    )
    typer.echo(f"Model: {model} | Generated in {elapsed:.2f}s")
    if item.diff:
        typer.secho("Status: documentation changes generated.", fg=typer.colors.GREEN)
        if show_diff:
            typer.echo(item.diff, nl=not item.diff.endswith("\n"))
    else:
        typer.secho("No documentation changes needed.", fg=typer.colors.BRIGHT_BLACK)


def _show_skipped(relative: str, reason: str) -> None:
    """Report a file-level generation failure without stopping the remaining scope."""
    typer.echo()
    typer.secho(f"Skipped documentation: {relative}", fg=typer.colors.YELLOW, bold=True)
    typer.secho(f"Reason: {reason}")


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


def _symbol_identity(symbol: Symbol) -> tuple[str, str]:
    """Return a symbol identity that remains stable when inserted lines shift it."""
    match = _DUPLICATE_SYMBOL_SUFFIX.fullmatch(symbol.name)
    name = match.group("name") if match else symbol.name
    return name, symbol.kind


def _symbol_occurrence(symbols: list[Symbol], target: Symbol) -> int | None:
    """Return the ordinal needed to locate a duplicate symbol after an edit."""
    matches = [symbol for symbol in symbols if _symbol_identity(symbol) == _symbol_identity(target)]
    if len(matches) == 1:
        return None
    return matches.index(target)


def _undocumented_symbols(content: str, suffix: str, filename: str = "<unknown>") -> list[str]:
    """Return symbols that make `--check` fail without requesting AI output."""
    return [symbol.name for symbol in discover(content, suffix, filename) if not symbol.has_doc]


def _syntax_error_message(exc: SyntaxError) -> str:
    """Render Python syntax errors with the file, source line, and error column."""
    location = f"{exc.filename or '<unknown>'}:{exc.lineno or '?'}"
    message = f"{location}: {exc.msg}"
    if not exc.text:
        return message
    source = exc.text.rstrip("\n")
    column = max((exc.offset or 1) - 1, 0)
    return f"{message}\n  {source}\n  {' ' * column}^"


def _read_source(path: Path) -> str:
    """Read UTF-8 source and convert filesystem failures to domain errors."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DocGubError(f"Unable to read source file {path}: {exc}") from exc


@dataclass
class _RunState:
    """Track file-level results across an incremental run."""

    completed: list[str]
    skipped: list[str]

    def mark_completed(self, relative: str) -> None:
        """Record an applied file once."""
        if relative not in self.completed:
            self.completed.append(relative)

    def mark_skipped(self, relative: str, reason: str) -> None:
        """Record and display a file-level failure."""
        self.skipped.append(relative)
        _show_skipped(relative, reason)


def _run_check(repo: GitRepo, files: list[str]) -> None:
    """Inspect selected files without calling an AI provider."""
    missing: dict[str, list[str]] = {}
    inspection_failures: list[str] = []
    for relative in files:
        try:
            content = _read_source(repo.root / relative)
            undocumented = _undocumented_symbols(content, Path(relative).suffix, relative)
        except (DocGubError, UnicodeDecodeError, SyntaxError) as exc:
            inspection_failures.append(relative)
            reason = _syntax_error_message(exc) if isinstance(exc, SyntaxError) else str(exc)
            _show_skipped(relative, reason)
            continue
        if undocumented:
            missing[relative] = undocumented
    if not missing and not inspection_failures:
        _show_check({})
        return
    if missing:
        _show_check(missing)
    if inspection_failures:
        typer.secho(
            f"Documentation check could not inspect {len(inspection_failures)} file(s).",
            fg=typer.colors.RED,
        )
    raise typer.Exit(1)


def _confirm_application(settings: Settings, files: list[str], yes: bool) -> bool:
    """Request one global confirmation before incremental application."""
    if settings.output != "apply" or not settings.confirm or yes:
        return True
    if not sys.stdin.isatty():
        raise DocGubError(
            "Confirmation requires an interactive terminal; use --yes for automation."
        )
    confirmed = typer.confirm(
        f"Apply documentation as each of {len(files)} file(s) completes?", default=False
    )
    if not confirmed:
        typer.secho("Cancelled.", fg=typer.colors.YELLOW)
    return confirmed


def _request_batches(
    content: str,
    targets: list[Symbol],
    path: Path,
    relative: str,
    settings: Settings,
) -> list[tuple[str, list[Symbol]]]:
    """Build file- or symbol-scoped provider requests."""
    if settings.request_scope == "file":
        return [(content, targets)]
    return [
        (source_for_symbol(content, symbol, path.suffix, relative), [symbol]) for symbol in targets
    ]


def _request_documentation(
    source: str,
    symbols: list[Symbol],
    settings: Settings,
    label: str,
) -> tuple[dict[str, Documentation], str]:
    """Request documentation with bounded, model-cycling retries."""
    candidates = settings.model_candidates
    last_error: Exception | None = None
    for attempt in range(1, MAX_AI_ATTEMPTS + 1):
        candidate = _model_for_attempt(candidates, attempt)
        try:
            with _loading(
                f"Generating docs [{label}] | model: {candidate} ({attempt}/{MAX_AI_ATTEMPTS})..."
            ):
                generated = documentation_for(
                    source,
                    symbols,
                    replace(settings, model=candidate, models=()),
                )
            return generated, candidate
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
    raise AIProviderError(f"generation failed after {MAX_AI_ATTEMPTS} attempts: {last_error}")


def _apply_generated_symbol(
    file_path: Path,
    relative: str,
    target: Symbol,
    generated: dict[str, Documentation],
    settings: Settings,
    occurrence: int | None = None,
) -> bool:
    """Apply one generated symbol against a freshly discovered source tree."""
    current_symbols = discover(_read_source(file_path), file_path.suffix, relative)
    if occurrence is None:
        current_target = next(
            (
                symbol
                for symbol in current_symbols
                if symbol.name == target.name and symbol.kind == target.kind
            ),
            None,
        )
    else:
        matches = [
            symbol
            for symbol in current_symbols
            if _symbol_identity(symbol) == _symbol_identity(target)
        ]
        current_target = matches[occurrence] if occurrence < len(matches) else None
    if current_target is None:
        raise DocGubError(f"{relative}: symbol `{target.name}` changed during generation.")
    documentation = generated.get(target.name)
    if documentation is None:
        raise DocGubError(f"{relative}: missing generated documentation for `{target.name}`.")
    item = prepare(
        file_path,
        current_symbols,
        {current_target.name: documentation},
        settings,
        selected_symbols=[current_target],
        display_path=Path(relative),
    )
    if not item.diff:
        return False
    apply(item)
    return True


def _generate_for_file(
    file_path: Path,
    relative: str,
    content: str,
    targets: list[Symbol],
    settings: Settings,
    state: _RunState,
) -> tuple[dict[str, Documentation], str]:
    """Generate every request for one file, applying symbol-scoped results incrementally."""
    requests = _request_batches(content, targets, file_path, relative, settings)
    descriptions: dict[str, Documentation] = {}
    candidate = settings.model
    for number, (source, requested_symbols) in enumerate(requests, start=1):
        label = relative
        if settings.request_scope == "symbol":
            label = f"{number}/{len(requests)} {relative}:{requested_symbols[0].name}"
        generated, candidate = _request_documentation(source, requested_symbols, settings, label)
        descriptions.update(generated)
        if settings.request_scope == "symbol" and settings.output == "apply":
            if _apply_generated_symbol(
                file_path,
                relative,
                requested_symbols[0],
                generated,
                settings,
                _symbol_occurrence(targets, requested_symbols[0]),
            ):
                state.mark_completed(relative)
    return descriptions, candidate


def _process_file(
    repo: GitRepo,
    relative: str,
    settings: Settings,
    show_diff: bool,
    state: _RunState,
) -> None:
    """Discover, generate, validate, and optionally apply documentation for one file."""
    file_path = repo.root / relative
    try:
        content = _read_source(file_path)
        symbols = discover(content, file_path.suffix, relative)
    except (DocGubError, UnicodeDecodeError, SyntaxError) as exc:
        reason = _syntax_error_message(exc) if isinstance(exc, SyntaxError) else str(exc)
        state.mark_skipped(relative, reason)
        return
    targets = [
        symbol
        for symbol in symbols
        if needs_documentation(symbol, settings.coverage, settings.existing_docs)
        and can_insert_documentation(content, symbol, file_path.suffix)
    ]
    if not targets:
        item = prepare(file_path, symbols, {}, settings, display_path=Path(relative))
        _show(item, "not used", 0, settings.output == "preview" and show_diff)
        return
    started = perf_counter()
    try:
        if file_path.suffix != ".py":
            validation_command(file_path.suffix, file_path)
        descriptions, candidate = _generate_for_file(
            file_path, relative, content, targets, settings, state
        )
        if settings.request_scope == "symbol" and settings.output == "apply":
            return
        item = prepare(file_path, symbols, descriptions, settings, display_path=Path(relative))
        _show(item, candidate, perf_counter() - started, settings.output == "preview" and show_diff)
        if settings.output == "apply" and item.diff:
            apply(item)
            state.mark_completed(relative)
            typer.secho(f"Applied documentation: {relative}", fg=typer.colors.GREEN)
    except DocGubError as exc:
        state.mark_skipped(relative, str(exc))


def _finish_run(settings: Settings, state: _RunState, continue_on_error: bool) -> None:
    """Display aggregate results and enforce the partial-failure exit policy."""
    if settings.output == "apply":
        typer.secho(
            f"Documentation applied to {len(state.completed)} file(s).",
            fg=typer.colors.GREEN,
            bold=True,
        )
    if state.skipped:
        typer.secho(f"Skipped files: {len(state.skipped)}", fg=typer.colors.YELLOW, bold=True)
        if not continue_on_error:
            raise typer.Exit(1)


@app.command()
def doc_gub(
    paths: list[Path] = typer.Argument(
        None,
        metavar="[PATH]...",
        help="One or more files or directories inside the Git worktree.",
    ),
    output: str | None = typer.Option(None, "--output", help="preview (default) or apply."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Alias for --output preview."),
    check: bool = typer.Option(
        False,
        "--check",
        help="Exit with status 1 when eligible symbols are undocumented; never calls AI.",
    ),
    continue_on_error: bool = typer.Option(
        False,
        "--continue-on-error",
        help="Return status 0 after partial failures when other files can still be processed.",
    ),
    show_diff: bool = typer.Option(
        True,
        "--show-diff/--no-show-diff",
        help="Show generated unified diffs in preview mode.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip global confirmation when applying."),
    coverage: str | None = typer.Option(None, "--coverage", help="missing, minimal, or all."),
    existing_docs: str | None = typer.Option(None, "--existing-docs", help="preserve or replace."),
    request_scope: str | None = typer.Option(
        None,
        "--request-scope",
        help="file (default) or symbol; symbol sends one source scope per request.",
    ),
    language: str | None = typer.Option(
        None, "--language", help="Language used for generated documentation."
    ),
    selection: str | None = typer.Option(None, "--selection", help="changes or repository."),
    python_format: str | None = typer.Option(
        None, "--format", help="google, numpy, or sphinx for Python."
    ),
    provider: str | None = typer.Option(None, "--provider", help="openai, gemini, or ollama."),
    model: str | None = typer.Option(None, "--model", help="Model name."),
    timeout_seconds: int | None = typer.Option(None, "--timeout-seconds", min=1),
    max_input_tokens: int | None = typer.Option(None, "--max-input-tokens", min=1),
    context_window_tokens: int | None = typer.Option(None, "--context-window-tokens", min=1),
    config: Path | None = typer.Option(None, "--config", help="Additional TOML configuration."),
) -> None:
    """Preview or safely apply AI-generated docs to Python, JavaScript and TypeScript."""
    try:
        repo = GitRepo()
        settings = load(
            repo.root,
            config,
            output="preview" if dry_run or check else output,
            coverage=coverage,
            existing_docs=existing_docs,
            request_scope=request_scope,
            language=language,
            selection=selection,
            python_format=python_format,
            provider=provider,
            model=model,
            timeout_seconds=timeout_seconds,
            max_input_tokens=max_input_tokens,
            context_window_tokens=context_window_tokens,
        )
        files = resolve(repo, paths, settings)
        if check:
            _run_check(repo, files)
            return
        if not _confirm_application(settings, files, yes):
            return
        state = _RunState([], [])
        for relative in files:
            _process_file(repo, relative, settings, show_diff, state)
        _finish_run(settings, state, continue_on_error)
    except (DocGubError, UnicodeDecodeError, SyntaxError) as exc:
        message = _syntax_error_message(exc) if isinstance(exc, SyntaxError) else str(exc)
        typer.secho(f"Error: {message}", fg=typer.colors.RED, bold=True, err=True)
        raise typer.Exit(1) from exc


@config_app.command("init")
def config_init(path: Path = typer.Option(Path(".doc-gub.toml"), "--path")) -> None:
    """Create a documentation configuration template without overwriting files."""
    try:
        if path.exists():
            typer.secho(f"Error: {path} already exists.", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        path.write_text(TEMPLATE, encoding="utf-8")
    except OSError as exc:
        typer.secho(f"Error: unable to create {path}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.secho(f"Configuration created at {path}.", fg=typer.colors.GREEN)


@config_app.command("show")
def config_show(config: Path | None = typer.Option(None, "--config")) -> None:
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
