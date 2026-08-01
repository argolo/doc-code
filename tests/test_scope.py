"""Módulo principal para testes de resolução e combinação de arquivos/diretórios."""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_gub.config import load
from doc_gub.errors import DocGubError
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


def test_resolve_rejects_oversized_files_before_they_are_read(tmp_path: Path) -> None:
    """Exclude files over the configured byte limit from the generation scope."""
    source = tmp_path / "large.py"
    source.write_text("x" * 20, encoding="utf-8")

    class Repo(GitRepo):
        """Minimal repository double rooted at the temporary directory."""

        def __init__(self) -> None:
            """Inicializa uma instância de Repo, definindo o diretório raiz para um caminho
            temporário.

            """
            self.root = tmp_path

        def relative_path(self, requested: Path) -> str:
            """Retorna o caminho relativo de um arquivo, resolvendo-o e comparando-o com o diretório
            raiz do repositório.

            Args:
                requested: O caminho do arquivo a ser processado.

            """
            return requested.resolve().relative_to(self.root).as_posix()

    with pytest.raises(DocGubError, match="exceeds max_file_bytes"):
        resolve(Repo(), [source], load(tmp_path, max_file_bytes=10))


def test_default_exclusions_do_not_use_directories_above_the_repository(tmp_path: Path) -> None:
    """A repository located below a directory named build remains eligible."""
    root = tmp_path / "build" / "project"
    root.mkdir(parents=True)
    source = root / "module.py"
    source.write_text("pass\n", encoding="utf-8")

    class Repo(GitRepo):
        """Minimal repository double rooted below a directory named build."""

        def __init__(self) -> None:
            """Inicializa uma instância de Repo."""
            self.root = root

        def relative_path(self, requested: Path) -> str:
            """Retorna o caminho relativo de um arquivo ou diretório em relação ao diretório raiz do
            repositório.

            Args:
                requested: O caminho completo (Path) para o recurso cujo caminho relativo deve ser
                determinado.

            """
            return requested.resolve().relative_to(self.root).as_posix()

    assert resolve(Repo(), [source], load(root)) == ["module.py"]
