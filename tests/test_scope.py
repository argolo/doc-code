"""Módulo principal para testes de resolução e combinação de arquivos/diretórios."""

from __future__ import annotations

from pathlib import Path

from doc_gub.config import load
from doc_gub.git import GitRepo
from doc_gub.scope import resolve


def test_resolve_combines_multiple_files_and_directories_without_duplicates(tmp_path: Path) -> None:
    """Verifica que a função resolve combine múltiplos arquivos e diretórios em uma lista de caminhos únicos, eliminando duplicatas.

    Args:
        tmp_path: Description of tmp_path.

    """
    first = tmp_path / "first.py"
    directory = tmp_path / "src"
    second = directory / "second.py"
    directory.mkdir()
    first.write_text("pass\n", encoding="utf-8")
    second.write_text("pass\n", encoding="utf-8")

    class Repo(GitRepo):
        """Representa um repositório de arquivos e diretórios, fornecendo métodos para manipulação de caminhos relativos dentro do seu escopo raiz."""

        def __init__(self) -> None:
            """Inicializa uma instância de Repo, definindo o diretório raiz para o caminho temporário atual."""
            self.root = tmp_path

        def relative_path(self, requested: Path) -> str:
            """Retorna o caminho relativo de um arquivo ou diretório solicitado em relação à raiz do repositório, resolvendo quaisquer links simbólicos e combinando múltiplos componentes sem duplicatas.

            Args:
                requested: Description of requested.

            """
            return requested.resolve().relative_to(self.root).as_posix()

    assert resolve(Repo(), [first, directory, first], load(tmp_path, selection="repository")) == [
        "first.py",
        "src/second.py",
    ]
