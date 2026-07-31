"""
Ponto único de decisão: qual backend de armazenamento usar.

O resto do app nunca importa LocalFolderStorage nem GraphAPIStorage
diretamente — sempre chama get_storage_client(), que devolve o backend
certo com base na configuração em st.secrets.

Trocar de backend (ex.: quando o TI aprovar o Graph API) é uma mudança
de configuração, não de código.
"""

from __future__ import annotations

from .base import OneDriveStorage, StorageError
from .local import LocalFolderStorage
from .graph import GraphAPIStorage
from .spaces import DigitalOceanSpacesStorage

__all__ = ["OneDriveStorage", "StorageError", "get_storage_client"]


def get_storage_client(secrets: dict) -> OneDriveStorage:
    """Constrói o backend de armazenamento configurado.

    `secrets` é o dicionário de configuração (em produção, st.secrets;
    em testes/scripts locais, um dict comum). Espera uma chave
    "storage_mode" com valor "local", "graph_api" ou "digitalocean_spaces",
    e as chaves de configuração específicas de cada modo.

    Exemplo de secrets.toml para modo local:

        storage_mode = "local"
        [local]
        raiz = "C:/Users/Mariano/OneDrive - Rede Melhor Compra/14 - CONSULTOR INTERNO/01 - Dedicado/Mapa da Farmácia"

    Exemplo de secrets.toml para modo Graph API:

        storage_mode = "graph_api"
        [graph_api]
        tenant_id = "..."
        client_id = "..."
        client_secret = "..."
        drive_id = "..."

    Exemplo de secrets.toml para modo DigitalOcean Spaces:

        storage_mode = "digitalocean_spaces"
        [digitalocean]
        access_key = "..."
        secret_key = "..."
        bucket = "..."
        endpoint_url = "https://nyc3.digitaloceanspaces.com"
    """
    modo = secrets.get("storage_mode")

    if modo == "local":
        config_local = secrets.get("local", {})
        raiz = config_local.get("raiz")
        if not raiz:
            raise StorageError(
                "storage_mode = 'local' requer 'raiz' configurada em "
                "[local] no secrets.toml (caminho da pasta OneDrive sincronizada)."
            )
        return LocalFolderStorage(raiz_local=raiz)

    if modo == "graph_api":
        config_graph = secrets.get("graph_api", {})
        obrigatorios = ["tenant_id", "client_id", "client_secret", "drive_id"]
        faltando = [k for k in obrigatorios if not config_graph.get(k)]
        if faltando:
            raise StorageError(
                f"storage_mode = 'graph_api' requer {faltando} configurados "
                f"em [graph_api] no secrets.toml."
            )
        return GraphAPIStorage(
            tenant_id=config_graph["tenant_id"],
            client_id=config_graph["client_id"],
            client_secret=config_graph["client_secret"],
            drive_id=config_graph["drive_id"],
        )

    if modo == "digitalocean_spaces":
        config_do = secrets.get("digitalocean", {})
        obrigatorios = ["access_key", "secret_key", "bucket", "endpoint_url"]
        faltando = [k for k in obrigatorios if not config_do.get(k)]
        if faltando:
            raise StorageError(
                f"storage_mode = 'digitalocean_spaces' requer {faltando} configurados "
                f"em [digitalocean] no secrets.toml."
            )
        return DigitalOceanSpacesStorage(
            access_key=config_do["access_key"],
            secret_key=config_do["secret_key"],
            bucket=config_do["bucket"],
            endpoint_url=config_do["endpoint_url"],
            prefixo=config_do.get("prefixo", ""),
        )

    raise StorageError(
        f"storage_mode inválido ou não configurado: {modo!r}. "
        f"Use 'local', 'graph_api' ou 'digitalocean_spaces' no secrets.toml."
    )
