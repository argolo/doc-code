"""Small, safe Git interface used for path and ignore decisions."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import DocGubError


class GitRepo:
    """Classe que representa um repositório Git e fornece métodos para interagir com ele."""

    def __init__(self, start: Path | None = None) -> None:
        """Inicializa a instância do GitRepo.

        Localiza a raiz do repositório a partir do diretório informado.

        Args:
            start: Diretório inicial ou o diretório de trabalho atual.
        """
        result = self._run("rev-parse", "--show-toplevel", cwd=str(start or Path.cwd()))
        self.root = Path(result.stdout.strip()).resolve()

    @staticmethod
    def _run(
        *args: str, cwd: str | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Executa um comando Git usando subprocess.run.

        Este método estático pode ser usado para executar comandos Git fora do contexto de uma
        instância específica de GitRepo.
        """
        result = subprocess.run(
            ["git", *args], cwd=cwd, text=True, capture_output=True, check=False
        )
        if check and result.returncode:
            raise DocGubError(
                f"Git command failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Executa um comando Git dentro do diretório raiz do repositório atual."""
        return self._run(*args, cwd=str(self.root))

    def relative_path(self, requested: Path) -> str:
        """Return a worktree-relative path.

        Retorna o caminho relativo de um arquivo ou diretório solicitado em relação à raiz do
        repositório Git.

        Levanta uma exceção se o caminho não existir dentro da árvore de trabalho do Git.

        Args:
                    requested: Description of requested.
        """
        try:
            return requested.resolve(strict=True).relative_to(self.root).as_posix()
        except (OSError, ValueError) as exc:
            raise DocGubError("The path must exist inside the Git worktree.") from exc

    def changed_files(self) -> list[str]:
        """Return staged or modified files together with untracked non-ignored files."""
        staged = self.run("diff", "--cached", "--name-only", "-z").stdout.split("\0")
        tracked = [name for name in staged if name] or [
            name for name in self.run("diff", "--name-only", "-z").stdout.split("\0") if name
        ]
        untracked = self.run("ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0")
        return list(dict.fromkeys([*tracked, *(name for name in untracked if name)]))
