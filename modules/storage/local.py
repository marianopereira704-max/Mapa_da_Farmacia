"""
Backend de armazenamento: pasta local sincronizada do OneDrive.

Este backend funciona lendo diretamente do sistema de arquivos local,
assumindo que o cliente do OneDrive (aplicativo do Windows/Mac) já está
instalado, logado, e sincronizando a pasta configurada.

Vantagem: funciona imediatamente, sem precisar de aprovação de TI nem
registro de app no Azure AD.
Limitação: só funciona enquanto o computador estiver ligado, logado no
OneDrive, e com a pasta sincronizada (não é acessível de um servidor
remoto). Serve como plano B enquanto a aprovação do Graph API (backend
"oficial", ver graph.py) não sai.
"""

from __future__ import annotations

from pathlib import Path

from .base import ItemPasta, OneDriveStorage, StorageError


class LocalFolderStorage(OneDriveStorage):
    def __init__(self, raiz_local: str):
        """
        raiz_local: caminho absoluto, no computador do usuário, até a
        pasta "Mapa da Farmácia" já sincronizada localmente pelo OneDrive.
        Ex. (Windows): "C:\\Users\\Mariano\\OneDrive - Rede Melhor Compra\\
                          14 - CONSULTOR INTERNO\\01 - Dedicado\\Mapa da Farmácia"
        """
        self.raiz = Path(raiz_local)
        if not self.raiz.exists():
            raise StorageError(
                f"Pasta local do OneDrive não encontrada: {raiz_local}\n"
                f"Verifique se o OneDrive está sincronizado e se o caminho "
                f"configurado está correto."
            )

    def _caminho_absoluto(self, caminho_relativo: str) -> Path:
        return self.raiz / caminho_relativo

    def listar_pasta(self, caminho_relativo: str) -> list[ItemPasta]:
        caminho = self._caminho_absoluto(caminho_relativo)
        if not caminho.exists():
            raise StorageError(f"Pasta não encontrada: {caminho_relativo}")
        if not caminho.is_dir():
            raise StorageError(f"Caminho não é uma pasta: {caminho_relativo}")

        itens = []
        for entrada in sorted(caminho.iterdir()):
            # Ignora arquivos temporários/ocultos que o próprio SO ou o
            # cliente do OneDrive podem criar (ex.: ~$arquivo.xlsx do
            # Excel quando um arquivo está aberto, .DS_Store no Mac).
            if entrada.name.startswith("~$") or entrada.name.startswith("."):
                continue
            caminho_rel_item = f"{caminho_relativo}/{entrada.name}" if caminho_relativo else entrada.name
            itens.append(ItemPasta(
                nome=entrada.name,
                e_pasta=entrada.is_dir(),
                caminho_completo=caminho_rel_item,
            ))
        return itens

    def ler_arquivo_bytes(self, caminho_relativo: str) -> bytes:
        caminho = self._caminho_absoluto(caminho_relativo)
        if not caminho.exists() or not caminho.is_file():
            raise StorageError(f"Arquivo não encontrado: {caminho_relativo}")
        try:
            return caminho.read_bytes()
        except OSError as e:
            raise StorageError(f"Falha ao ler arquivo {caminho_relativo}: {e}") from e

    def escrever_arquivo_bytes(self, caminho_relativo: str, conteudo: bytes) -> None:
        caminho = self._caminho_absoluto(caminho_relativo)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        try:
            caminho.write_bytes(conteudo)
        except OSError as e:
            raise StorageError(f"Falha ao escrever arquivo {caminho_relativo}: {e}") from e

    def existe(self, caminho_relativo: str) -> bool:
        return self._caminho_absoluto(caminho_relativo).exists()

    def listar_todos_arquivos(self, prefixo: str = "") -> list[str]:
        # Listagem recursiva de sistema de arquivos é barata — não precisa
        # de nenhuma otimização especial aqui (diferente do backend Spaces,
        # onde isso evita 1 chamada de rede por subpasta).
        caminho_base = self._caminho_absoluto(prefixo)
        if not caminho_base.exists():
            return []

        resultado = []
        for entrada in caminho_base.rglob("*"):
            if not entrada.is_file():
                continue
            if entrada.name.startswith("~$") or entrada.name.startswith("."):
                continue
            caminho_rel = entrada.relative_to(self.raiz).as_posix()
            resultado.append(caminho_rel)
        return resultado
