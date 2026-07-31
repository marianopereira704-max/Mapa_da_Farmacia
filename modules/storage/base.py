"""
Interface comum de armazenamento (abstração de acesso ao OneDrive).

Existem dois backends possíveis, intercambiáveis:

  - LocalFolderStorage: lê a pasta do OneDrive já sincronizada localmente
    no computador do usuário (funciona sem nenhuma aprovação de TI, mas
    exige o computador ligado e logado no OneDrive).
  - GraphAPIStorage: lê via Microsoft Graph API (independe de qualquer
    computador específico, mas exige registro do app no Azure AD e
    aprovação de TI).

O resto do código (data_loader, telas do Streamlit) nunca deve chamar um
backend diretamente — sempre through esta interface, obtida via
storage.get_storage_client(). Isso permite trocar de backend mudando
apenas uma configuração, sem tocar em nenhuma outra parte do sistema.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ItemPasta:
    """Representa um item (arquivo ou subpasta) dentro de uma pasta do
    OneDrive, independente do backend usado para obtê-lo."""
    nome: str
    e_pasta: bool
    caminho_completo: str  # caminho relativo à raiz do armazenamento configurado


class StorageError(Exception):
    """Erro genérico de acesso ao armazenamento (arquivo/pasta não
    encontrado, falha de autenticação, falha de rede, etc.)."""


class OneDriveStorage(ABC):
    """Contrato que qualquer backend de acesso ao OneDrive deve implementar."""

    @abstractmethod
    def listar_pasta(self, caminho_relativo: str) -> list[ItemPasta]:
        """Lista o conteúdo (arquivos e subpastas) de uma pasta.

        `caminho_relativo` é relativo à raiz do armazenamento configurado.
        Retorna lista vazia se a pasta existir mas estiver vazia.
        Levanta StorageError se a pasta não existir ou não puder ser acessada.
        """

    @abstractmethod
    def ler_arquivo_bytes(self, caminho_relativo: str) -> bytes:
        """Lê o conteúdo bruto (bytes) de um arquivo.

        `caminho_relativo` é relativo à raiz do armazenamento configurado.
        Levanta StorageError se o arquivo não existir ou não puder ser lido.
        """

    @abstractmethod
    def escrever_arquivo_bytes(self, caminho_relativo: str, conteudo: bytes) -> None:
        """Escreve/sobrescreve um arquivo. Usado apenas para salvar o
        ajuste_mix.json (Aba 1) — nenhuma outra escrita acontece no OneDrive.
        """

    @abstractmethod
    def existe(self, caminho_relativo: str) -> bool:
        """Verifica se um arquivo ou pasta existe, sem levantar exceção."""

    @abstractmethod
    def listar_todos_arquivos(self, prefixo: str = "") -> list[str]:
        """Retorna os caminhos relativos de TODOS os arquivos existentes
        sob `prefixo` (recursivo, "achatado" — sem estrutura de pastas),
        numa única operação (ou o mínimo de operações possível), em vez
        de uma consulta por subpasta. Implementação eficiente é
        obrigatória — este método existe especificamente para evitar 1
        chamada de rede por loja ao montar a tela de seleção.

        Retorna lista vazia se `prefixo` não existir ou estiver vazio —
        nunca levanta StorageError por ausência (o caso "armazenamento
        vazio" é um estado válido, não um erro).
        """
