"""
Backend de armazenamento: Microsoft Graph API.

Este é o backend "definitivo" — não depende de nenhum computador
específico estar ligado, funciona de qualquer lugar (inclusive
Streamlit Community Cloud). Requer:

  1. Um app registrado no Azure AD da organização (Portal Azure).
  2. Permissão de aplicação Files.Read.All (ou Sites.Read.All, dependendo
     de como a pasta está compartilhada) com "admin consent" concedido
     por um administrador do Microsoft 365 da empresa.
  3. As credenciais (tenant_id, client_id, client_secret) e o ID do
     drive/site do OneDrive configuradas em st.secrets.

Enquanto a aprovação do TI não sai, use LocalFolderStorage (local.py) em
paralelo — a interface (base.py) é a mesma, então trocar de backend é
só mudar a configuração em config.py / st.secrets, sem tocar no resto
do código.

Autenticação: client credentials flow (a aplicação se autentica como ela
mesma, sem um usuário logado interativamente) — adequado porque, nesta
fase do projeto, todos os consultores compartilham o mesmo acesso, sem
login individual.
"""

from __future__ import annotations

import requests

from .base import ItemPasta, OneDriveStorage, StorageError

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
AUTH_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


class GraphAPIStorage(OneDriveStorage):
    def __init__(self, tenant_id: str, client_id: str, client_secret: str, drive_id: str):
        """
        tenant_id, client_id, client_secret: credenciais do app registrado
            no Azure AD.
        drive_id: ID do drive do OneDrive/SharePoint onde a pasta
            "Mapa da Farmácia" vive. Obtido via Graph API
            (/sites/{site-id}/drive ou /users/{user-id}/drive) depois que
            o app tiver permissão — não é algo que se "adivinha" antes
            de ter acesso liberado.
        """
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.drive_id = drive_id
        self._token = None

    # -- Autenticação ---------------------------------------------------

    def _obter_token(self) -> str:
        if self._token is not None:
            return self._token

        url = AUTH_URL_TEMPLATE.format(tenant_id=self.tenant_id)
        dados = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
        }
        resposta = requests.post(url, data=dados, timeout=30)
        if resposta.status_code != 200:
            raise StorageError(
                f"Falha ao autenticar no Microsoft Graph API: "
                f"{resposta.status_code} — {resposta.text}"
            )
        self._token = resposta.json()["access_token"]
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._obter_token()}"}

    # -- Operações --------------------------------------------------------

    def listar_pasta(self, caminho_relativo: str) -> list[ItemPasta]:
        caminho_encoded = caminho_relativo.strip("/")
        if caminho_encoded:
            url = f"{GRAPH_BASE_URL}/drives/{self.drive_id}/root:/{caminho_encoded}:/children"
        else:
            url = f"{GRAPH_BASE_URL}/drives/{self.drive_id}/root/children"

        resposta = requests.get(url, headers=self._headers(), timeout=30)
        if resposta.status_code == 404:
            raise StorageError(f"Pasta não encontrada no OneDrive: {caminho_relativo}")
        if resposta.status_code != 200:
            raise StorageError(
                f"Falha ao listar pasta '{caminho_relativo}': "
                f"{resposta.status_code} — {resposta.text}"
            )

        itens = []
        for item in resposta.json().get("value", []):
            caminho_rel_item = f"{caminho_relativo}/{item['name']}" if caminho_relativo else item["name"]
            itens.append(ItemPasta(
                nome=item["name"],
                e_pasta="folder" in item,
                caminho_completo=caminho_rel_item,
            ))
        return itens

    def ler_arquivo_bytes(self, caminho_relativo: str) -> bytes:
        caminho_encoded = caminho_relativo.strip("/")
        url = f"{GRAPH_BASE_URL}/drives/{self.drive_id}/root:/{caminho_encoded}:/content"

        resposta = requests.get(url, headers=self._headers(), timeout=60)
        if resposta.status_code == 404:
            raise StorageError(f"Arquivo não encontrado no OneDrive: {caminho_relativo}")
        if resposta.status_code != 200:
            raise StorageError(
                f"Falha ao ler arquivo '{caminho_relativo}': "
                f"{resposta.status_code} — {resposta.text}"
            )
        return resposta.content

    def escrever_arquivo_bytes(self, caminho_relativo: str, conteudo: bytes) -> None:
        caminho_encoded = caminho_relativo.strip("/")
        url = f"{GRAPH_BASE_URL}/drives/{self.drive_id}/root:/{caminho_encoded}:/content"

        resposta = requests.put(
            url, headers=self._headers(), data=conteudo, timeout=60
        )
        if resposta.status_code not in (200, 201):
            raise StorageError(
                f"Falha ao salvar arquivo '{caminho_relativo}': "
                f"{resposta.status_code} — {resposta.text}"
            )

    def existe(self, caminho_relativo: str) -> bool:
        caminho_encoded = caminho_relativo.strip("/")
        url = f"{GRAPH_BASE_URL}/drives/{self.drive_id}/root:/{caminho_encoded}"
        resposta = requests.get(url, headers=self._headers(), timeout=30)
        return resposta.status_code == 200

    def listar_todos_arquivos(self, prefixo: str = "") -> list[str]:
        # Implementação RECURSIVA (1 chamada de rede por subpasta, via
        # listar_pasta) — aceitável aqui porque este backend não é mais o
        # principal do projeto (ver README): a otimização "1 chamada só"
        # feita em spaces.py (list_objects_v2 sem Delimiter) não tem
        # equivalente direto e simples na Graph API sem usar recursos mais
        # avançados (ex.: "delta query" ou "$expand"/busca recursiva
        # nativa). Se o Graph API voltar a ser o backend principal, vale
        # revisitar isso.
        resultado: list[str] = []
        try:
            itens = self.listar_pasta(prefixo)
        except StorageError:
            return resultado

        for item in itens:
            if item.e_pasta:
                resultado.extend(self.listar_todos_arquivos(item.caminho_completo))
            else:
                resultado.append(item.caminho_completo)
        return resultado
