"""Módulo de teste para a ferramenta doc_gub, que inclui funções para verificar o carregamento, gerenciamento de símbolos e aplicação de configurações."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from doc_gub import cli
from doc_gub.cli import _loading, _model_for_attempt, _undocumented_symbols
from doc_gub.config import Settings
from doc_gub.errors import AIProviderError
from doc_gub.symbols import Symbol, discover, source_for_symbol


def test_loading_reports_progress_when_stderr_is_not_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verifica que o progresso de carregamento dos relatórios seja exibido corretamente quando a saída de erro (stderr) não é um terminal, garantindo que nenhuma mensagem residual permaneça em `stderr`.

    Args:
        capsys: Description of capsys.

    """
    with _loading("Generating documentation for [sample.py]"):
        pass

    assert capsys.readouterr().err == ""


def test_loading_draws_a_single_stable_line_in_a_terminal(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifica se o processo de carregamento exibe exatamente uma linha estável e única no terminal, mesmo que haja múltiplas chamadas internas.

    Args:
        capsys: Description of capsys.
        monkeypatch: Description of monkeypatch.

    """
    monkeypatch.setattr(cli.sys.stderr, "isatty", lambda: True)

    with _loading("Generating documentation for [1/2 sample.py:first]"):
        pass

    assert capsys.readouterr().err.count("Generating documentation") == 1


def test_retries_cycle_through_model_candidates() -> None:
    """Verifica se o sistema de retentativas cicla corretamente através dos candidatos de modelo definidos, garantindo que a sequência seja repetitiva (ex: model-one, model-two, model-one, model-two)."""
    candidates = ("model-one", "model-two")

    assert [_model_for_attempt(candidates, attempt) for attempt in range(1, 5)] == [
        "model-one",
        "model-two",
        "model-one",
        "model-two",
    ]


def test_check_finds_undocumented_symbols_without_calling_ai() -> None:
    """Verifica se a função consegue identificar símbolos não documentados em um conteúdo de módulo sem chamar IA."""
    content = (
        '"""Module docs."""\n\ndef documented():\n    """Docs."""\n\ndef missing():\n    pass\n'
    )

    assert _undocumented_symbols(content, ".py") == ["missing"]


def test_reports_python_syntax_errors_with_file_line_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Testa se o relatório de execução do CLI captura erros de sintaxe Python, incluindo nome do arquivo, número da linha e trecho do código fonte.

    Args:
        tmp_path: Description of tmp_path.
        monkeypatch: Description of monkeypatch.

    """
    path = tmp_path / "test_cli.py"
    path.write_text("\n" * 16 + "def broken(:\n", encoding="utf-8")

    class Repo:
        """Uma classe que representa um repositório, inicializada com um caminho raiz."""

        def __init__(self) -> None:
            """Inicializa o objeto Repo, definindo o atributo 'root' para o caminho temporário fornecido."""
            self.root = tmp_path

    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["test_cli.py"])

    result = CliRunner().invoke(cli.app, ["--check"])

    assert result.exit_code == 1
    assert "Error: test_cli.py:17: invalid syntax" in result.output
    assert "def broken(:" in result.output
    assert "^" in result.output


def test_apply_writes_each_file_before_later_generation_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifica que a documentação é aplicada em cada arquivo antes de falhas subsequentes na geração, garantindo que arquivos anteriores sejam processados corretamente mesmo se o processo falhar mais tarde.

    Args:
        tmp_path: Description of tmp_path.
        monkeypatch: Description of monkeypatch.

    """
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("def first():\n    return True\n", encoding="utf-8")
    second_source = "def second():\n    return True\n"
    second.write_text(second_source, encoding="utf-8")

    class Repo:
        """Classe que representa um repositório, inicializando o atributo 'root' com o caminho temporário (tmp_path)."""

        def __init__(self) -> None:
            """Inicializa uma nova instância de Repo, definindo o diretório raiz para um caminho temporário."""
            self.root = tmp_path

    settings = Settings(output="apply", confirm=False, selection="repository", models=("test",))
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: settings)

    def fake_resolve(_: object, paths: list[Path], __: Settings) -> list[str]:
        """Simula um resolvedor que retorna os caminhos de arquivos em ordem, simulando a escrita sequencial antes de falhas subsequentes.

        Args:
            _: Description of _.
            paths: Description of paths.
            __: Description of __.

        """
        assert paths == [Path("first.py"), Path("second.py")]
        return ["first.py", "second.py"]

    monkeypatch.setattr(cli, "resolve", fake_resolve)
    monkeypatch.setattr(cli, "MAX_AI_ATTEMPTS", 1)

    def fake_documentation(content: str, symbols: list[Symbol], _: Settings) -> dict[str, str]:
        """Gera um dicionário de documentações para os símbolos fornecidos, desde que o conteúdo não contenha a palavra 'second'.

        Args:
            content: Description of content.
            symbols: Description of symbols.
            _: Description of _.

        """
        if "second" in content:
            raise AIProviderError("provider unavailable")
        return {symbol.name: "Generated docs." for symbol in symbols}

    monkeypatch.setattr(cli, "documentation_for", fake_documentation)

    result = CliRunner().invoke(cli.app, ["first.py", "second.py"])

    assert result.exit_code == 0, result.output
    assert '"""Generated docs."""' in first.read_text(encoding="utf-8")
    assert second.read_text(encoding="utf-8") == second_source
    assert "Applied documentation: first.py" in result.output
    assert "Skipped documentation: second.py" in result.output


def test_symbol_scope_contains_only_the_requested_python_function() -> None:
    """Verifica se o escopo do símbolo contém apenas a função Python solicitada."""
    content = "def first():\n    return 1\n\ndef second():\n    return 2\n"
    second = next(symbol for symbol in discover(content, ".py") if symbol.name == "second")

    assert source_for_symbol(content, second, ".py") == "def second():\n    return 2\n"


def test_module_symbol_scope_uses_a_python_outline_without_function_bodies() -> None:
    """Verifica se o escopo de um símbolo em um módulo Python contém apenas a estrutura básica (definições e imports) sem implementar corpos de funções ou variáveis internas."""
    content = '''"""Utilities for values."""
import math

DEFAULT_SCALE = 2

def calculate(value):
    internal_detail = value * math.pi
    return internal_detail * DEFAULT_SCALE
'''
    module = next(symbol for symbol in discover(content, ".py") if symbol.kind == "module")

    scope = source_for_symbol(content, module, ".py")

    assert '"""Utilities for values."""' in scope
    assert "import math" in scope
    assert "CONSTANTS: DEFAULT_SCALE" in scope
    assert "def calculate(value):" in scope
    assert "internal_detail" not in scope


def test_symbol_request_scope_calls_the_model_once_per_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Testa que o escopo de solicitação de símbolos chama o modelo uma vez por alvo, verificando a chamada para documentar funções específicas em um módulo simulado.

    Args:
        tmp_path: Description of tmp_path.
        monkeypatch: Description of monkeypatch.

    """
    source = '"""Module docs."""\n\ndef first():\n    return 1\n\ndef second():\n    return 2\n'
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")

    class Repo:
        """Uma classe que inicializa um atributo root utilizando o valor de tmp_path."""

        def __init__(self) -> None:
            """Inicializa uma nova instância de Repo, definindo o atributo 'root' para o caminho temporário atual."""
            self.root = tmp_path

    settings = Settings(confirm=False, request_scope="symbol", models=("test",))
    received: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: settings)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["sample.py"])
    monkeypatch.setattr(cli, "MAX_AI_ATTEMPTS", 1)

    def fake_documentation(content: str, symbols: list[Symbol], _: Settings) -> dict[str, str]:
        """Função que simula a geração de documentação para um conteúdo e uma lista de símbolos, registrando as chamadas recebidas em um histórico interno.

        Args:
            content: Description of content.
            symbols: Description of symbols.
            _: Description of _.

        """
        received.append((content, [symbol.name for symbol in symbols]))
        return {symbol.name: "Generated docs." for symbol in symbols}

    monkeypatch.setattr(cli, "documentation_for", fake_documentation)

    result = CliRunner().invoke(cli.app, ["sample.py"])

    assert result.exit_code == 0, result.output
    assert received == [
        ("def first():\n    return 1\n", ["first"]),
        ("def second():\n    return 2\n", ["second"]),
    ]


@pytest.mark.parametrize("request_scope", ["file", "symbol"])
def test_preserve_skips_documented_symbols_for_every_request_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request_scope: str
) -> None:
    """Ensure preserve excludes documented modules, classes, methods, and functions."""
    source = (
        '"""Module docs."""\n\n'
        "class DocumentedClass:\n"
        '    """Class docs."""\n\n'
        "    def documented_method(self):\n"
        '        """Method docs."""\n'
        "        return True\n\n"
        "def documented_function():\n"
        '    """Function docs."""\n'
        "    return True\n\n"
        "def missing_function():\n"
        "    return False\n"
    )
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")

    class Repo:
        """Representa um repositório de código."""

        def __init__(self) -> None:
            """Inicializa uma nova instância de Repo."""
            self.root = tmp_path

    received: list[list[str]] = []
    settings = Settings(
        confirm=False,
        coverage="all",
        existing_docs="preserve",
        request_scope=request_scope,
        models=("test",),
    )
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: settings)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["sample.py"])
    monkeypatch.setattr(cli, "MAX_AI_ATTEMPTS", 1)

    def fake_documentation(_: str, symbols: list[Symbol], __: Settings) -> dict[str, str]:
        """Uma função que recebe uma lista de símbolos e retorna um dicionário com as descrições
        geradas para cada símbolo.

        Args:
            _: Um argumento ignorado, tipicamente usado para parâmetros que não são utilizados na
            função.
            symbols: Uma lista de objetos do tipo Symbol, representando os símbolos a serem
            documentados.
            __: Outro argumento ignorado, geralmente usado para configurações ou contextos
            adicionais.

        """
        received.append([symbol.name for symbol in symbols])
        return {symbol.name: "Generated docs." for symbol in symbols}

    monkeypatch.setattr(cli, "documentation_for", fake_documentation)

    result = CliRunner().invoke(cli.app, ["sample.py"])

    assert result.exit_code == 0, result.output
    assert received == [["missing_function"]]


def test_symbol_scope_applies_completed_symbols_before_a_later_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Testa que o escopo do símbolo aplica os símbolos concluídos antes de uma falha posterior, garantindo que a documentação dos símbolos processados com sucesso seja aplicada mesmo se um símbolo subsequente falhar.

    Args:
        tmp_path: Description of tmp_path.
        monkeypatch: Description of monkeypatch.

    """
    source = '"""Module docs."""\n\ndef first():\n    return 1\n\ndef second():\n    return 2\n'
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")

    class Repo:
        """Uma classe que representa um repositório, inicializando o atributo root com um caminho temporário."""

        def __init__(self) -> None:
            """Inicializa uma nova instância de Repo, definindo o atributo 'root' para o caminho temporário atual."""
            self.root = tmp_path

    settings = Settings(output="apply", confirm=False, request_scope="symbol", models=("test",))
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: settings)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["sample.py"])
    monkeypatch.setattr(cli, "MAX_AI_ATTEMPTS", 1)

    def fake_documentation(_: str, symbols: list[Symbol], __: Settings) -> dict[str, str]:
        """Gera documentação para símbolos, verificando se o escopo aplicado é baseado nos símbolos concluídos antes de uma falha posterior.

        Args:
            _: Description of _.
            symbols: Description of symbols.
            __: Description of __.

        """
        if symbols[0].name == "second":
            raise AIProviderError("provider unavailable")
        return {"first": "First generated documentation."}

    monkeypatch.setattr(cli, "documentation_for", fake_documentation)

    result = CliRunner().invoke(cli.app, ["sample.py"])

    assert result.exit_code == 0, result.output
    assert '"""First generated documentation."""' in path.read_text(encoding="utf-8")
    assert "Applied documentation: sample.py:first" not in result.output
    assert "Skipped documentation: sample.py" in result.output


def test_symbol_scope_applies_class_docs_before_a_decorated_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Testa se a documentação do símbolo (docstring) é aplicada ao nível da classe, mesmo que haja métodos decorados nela.

    Args:
        tmp_path: Description of tmp_path.
        monkeypatch: Description of monkeypatch.

    """
    source = (
        '"""Module docs."""\n\n'
        "class FinancialCalculator:\n"
        "    @staticmethod\n"
        "    def compound_interest(capital: float) -> float:\n"
        '        """Method docs."""\n'
        "        return capital * 1.1\n"
    )
    path = tmp_path / "finance.py"
    path.write_text(source, encoding="utf-8")

    class Repo:
        """Uma classe que representa um repositório e inicializa um diretório raiz."""

        def __init__(self) -> None:
            """Inicializa uma nova instância de Repo, definindo o atributo 'root' para o caminho temporário atual."""
            self.root = tmp_path

    settings = Settings(output="apply", confirm=False, request_scope="symbol", models=("test",))
    monkeypatch.setattr(cli, "GitRepo", Repo)
    monkeypatch.setattr(cli, "load", lambda *_args, **_kwargs: settings)
    monkeypatch.setattr(cli, "resolve", lambda *_args: ["finance.py"])
    monkeypatch.setattr(cli, "MAX_AI_ATTEMPTS", 1)
    monkeypatch.setattr(
        cli,
        "documentation_for",
        lambda _content, symbols, _settings: {symbols[0].name: "Perform financial calculations."},
    )

    result = CliRunner().invoke(cli.app, ["finance.py"])

    assert result.exit_code == 0, result.output
    updated = path.read_text(encoding="utf-8")
    assert (
        "class FinancialCalculator:\n"
        '    """Perform financial calculations."""\n'
        "\n"
        "    @staticmethod\n"
    ) in updated
    assert "invalid syntax" not in result.output
