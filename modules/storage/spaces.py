"""
Backend de armazenamento: DigitalOcean Spaces (compatível com S3).

Este é o backend "definitivo" a partir desta fase do projeto — substitui o
OneDrive como destino de armazenamento. DigitalOcean Spaces implementa a
mesma API do Amazon S3, então usamos o cliente genérico do boto3 apontado
para o endpoint da DigitalOcean (em vez do endpoint padrão da AWS).

Diferente do OneDrive, um bucket S3/Spaces não tem pastas de verdade — o
que existe são "chaves" (keys) de objeto, que podem conter "/" e por isso
PARECEM uma estrutura de pastas quando listadas com um prefixo + delimitador.
listar_pasta() usa exatamente esse truque (Prefix + Delimiter="/") para expor
uma interface idêntica à dos outros dois backends (que têm pastas reais).

Requer (configurado em st.secrets, seção [digitalocean]):
  - access_key, secret_key: credenciais da API Spaces (geradas no painel da
    DigitalOcean, não são a senha da conta).
  - bucket: nome do Space (bucket).
  - endpoint_url: endereço regional do Spaces, ex.:
    "https://nyc3.digitaloceanspaces.com".
  - prefixo (opcional): "subpasta" lógica dentro do bucket, caso um dia seja
    necessário compartilhar o mesmo bucket com outros projetos.
"""

from __future__ import annotations

import re

import boto3
from botocore.exceptions import ClientError

from .base import ItemPasta, OneDriveStorage, StorageError


def _regiao_do_endpoint(endpoint_url: str) -> str:
    """Extrai a região a partir do endpoint (ex.: "nyc3" de
    "https://nyc3.digitaloceanspaces.com"). boto3 exige um region_name
    válido mesmo apontando para um endpoint que não é da AWS — a
    DigitalOcean usa o nome da região como primeiro subdomínio. Cai em
    "us-east-1" como valor genérico se o endpoint não seguir esse padrão
    (ex.: em testes, contra um endpoint simulado)."""
    m = re.match(r"https?://([a-z0-9-]+)\.digitaloceanspaces\.com", endpoint_url)
    return m.group(1) if m else "us-east-1"


class DigitalOceanSpacesStorage(OneDriveStorage):
    def __init__(
        self,
        access_key: str,
        secret_key: str,
        bucket: str,
        endpoint_url: str,
        prefixo: str = "",
    ):
        self.bucket = bucket
        self.prefixo = prefixo.strip("/")
        self._cliente = boto3.client(
            "s3",
            region_name=_regiao_do_endpoint(endpoint_url),
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    # -- Chaves -----------------------------------------------------------

    def _chave(self, caminho_relativo: str) -> str:
        """Compõe a chave real do objeto no bucket, prefixando com
        `self.prefixo` quando configurado."""
        caminho_relativo = caminho_relativo.strip("/")
        if self.prefixo:
            return f"{self.prefixo}/{caminho_relativo}" if caminho_relativo else self.prefixo
        return caminho_relativo

    # -- Operações ----------------------------------------------------------

    def listar_pasta(self, caminho_relativo: str) -> list[ItemPasta]:
        prefixo_busca = self._chave(caminho_relativo)
        if prefixo_busca:
            prefixo_busca = prefixo_busca.rstrip("/") + "/"

        try:
            paginador = self._cliente.get_paginator("list_objects_v2")
            paginas = paginador.paginate(
                Bucket=self.bucket, Prefix=prefixo_busca, Delimiter="/"
            )

            itens: list[ItemPasta] = []
            encontrou_algo = False
            for pagina in paginas:
                for subpasta in pagina.get("CommonPrefixes", []):
                    encontrou_algo = True
                    chave_completa = subpasta["Prefix"].rstrip("/")
                    nome = chave_completa.rsplit("/", 1)[-1]
                    itens.append(ItemPasta(
                        nome=nome,
                        e_pasta=True,
                        caminho_completo=self._para_relativo(chave_completa),
                    ))
                for obj in pagina.get("Contents", []):
                    chave_completa = obj["Key"]
                    # O próprio prefixo pode aparecer como um objeto "placeholder"
                    # vazio (algumas ferramentas criam isso pra simular uma
                    # pasta vazia) — ignora, não é um arquivo de verdade.
                    if chave_completa == prefixo_busca:
                        continue
                    encontrou_algo = True
                    nome = chave_completa.rsplit("/", 1)[-1]
                    if not nome:
                        continue
                    itens.append(ItemPasta(
                        nome=nome,
                        e_pasta=False,
                        caminho_completo=self._para_relativo(chave_completa),
                    ))
        except ClientError as e:
            raise StorageError(f"Falha ao listar pasta '{caminho_relativo}': {e}") from e

        if not encontrou_algo:
            raise StorageError(f"Pasta não encontrada: {caminho_relativo}")

        return itens

    def _para_relativo(self, chave_completa: str) -> str:
        if self.prefixo and chave_completa.startswith(self.prefixo + "/"):
            return chave_completa[len(self.prefixo) + 1:]
        return chave_completa

    def ler_arquivo_bytes(self, caminho_relativo: str) -> bytes:
        chave = self._chave(caminho_relativo)
        try:
            resposta = self._cliente.get_object(Bucket=self.bucket, Key=chave)
        except ClientError as e:
            codigo = e.response.get("Error", {}).get("Code", "")
            if codigo in ("NoSuchKey", "404"):
                raise StorageError(f"Arquivo não encontrado: {caminho_relativo}") from e
            raise StorageError(f"Falha ao ler arquivo '{caminho_relativo}': {e}") from e
        return resposta["Body"].read()

    def escrever_arquivo_bytes(self, caminho_relativo: str, conteudo: bytes) -> None:
        chave = self._chave(caminho_relativo)
        try:
            self._cliente.put_object(Bucket=self.bucket, Key=chave, Body=conteudo)
        except ClientError as e:
            raise StorageError(f"Falha ao escrever arquivo '{caminho_relativo}': {e}") from e

    def existe(self, caminho_relativo: str) -> bool:
        chave = self._chave(caminho_relativo)
        try:
            self._cliente.head_object(Bucket=self.bucket, Key=chave)
            return True
        except ClientError as e:
            codigo = e.response.get("Error", {}).get("Code", "")
            if codigo in ("404", "NoSuchKey"):
                return False
            raise StorageError(f"Falha ao verificar existência de '{caminho_relativo}': {e}") from e

    def listar_todos_arquivos(self, prefixo: str = "") -> list[str]:
        # SEM Delimiter -- list_objects_v2 retorna TODOS os objetos sob o
        # prefixo de uma vez (achatado, sem estrutura de pastas), em vez de
        # 1 chamada por subpasta. O paginador cuida automaticamente de
        # buckets com mais de 1000 objetos (limite por página da API S3),
        # mas o número de PÁGINAS depende do volume total de objetos, não
        # da quantidade de "lojas" -- é isso que torna esta função O(1) em
        # relação ao número de lojas (e não O(n), como seria fazer 1
        # listar_pasta() recursivo por loja).
        prefixo_busca = self._chave(prefixo)
        if prefixo_busca:
            prefixo_busca = prefixo_busca.rstrip("/") + "/"

        try:
            paginador = self._cliente.get_paginator("list_objects_v2")
            paginas = paginador.paginate(Bucket=self.bucket, Prefix=prefixo_busca)

            resultado: list[str] = []
            for pagina in paginas:
                for obj in pagina.get("Contents", []):
                    chave_completa = obj["Key"]
                    # Objetos "placeholder" de pasta (a própria chave do
                    # prefixo, ou chaves terminadas em "/") não são
                    # arquivos de verdade.
                    if chave_completa == prefixo_busca or chave_completa.endswith("/"):
                        continue
                    resultado.append(self._para_relativo(chave_completa))
            return resultado
        except ClientError as e:
            raise StorageError(f"Falha ao listar arquivos sob '{prefixo}': {e}") from e
