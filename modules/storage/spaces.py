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
from botocore.config import Config
# ClientError = erro devolvido PELO serviço (403, 404, NoSuchKey...).
# BotoCoreError = falha do lado do cliente/rede (ReadTimeoutError,
# ConnectTimeoutError, EndpointConnectionError, ConnectionClosedError...)
# — NÃO é subclasse de ClientError, então precisa ser capturada à parte.
# Sem isso, timeout e conexão resetada (o modo de falha mais comum aqui)
# subiam crus, sem virar StorageError, e escapavam dos ~14 pontos do app
# que fazem `except StorageError` justamente pra tratar falha de storage.
from botocore.exceptions import BotoCoreError, ClientError

from .base import ArquivoNaoEncontradoError, ItemPasta, OneDriveStorage, StorageError

# Sem isso, o boto3 usa os defaults do botocore (connect_timeout=60s,
# read_timeout=60s, retries em modo "legacy") — e read_timeout é por
# leitura de socket/chunk, não pela operação inteira: uma conexão lenta
# mas viva nunca dispara esse timeout, não importa quanto tempo o
# download total leve. Valores explícitos aqui dão um teto previsível
# em vez de deixar uma conexão degradada travar indefinidamente.
#
# ATENÇÃO com a semântica de "max_attempts" do botocore — ela NÃO é
# "tentativas totais": é "quantos retries além da tentativa inicial"
# (confirmado em botocore/config.py, docstring de `retries`). Ou seja,
# max_attempts=1 aqui = 1 tentativa inicial + 1 retry = 2 tentativas
# totais. Testado e confirmado via client.meta.config.retries depois de
# construir o client — não confiar só na leitura da documentação sem
# checar o valor resolvido de verdade.
#
# read_timeout=20s e max_attempts=1 (não 60s/3) de propósito: os maiores
# arquivos reais que este app lê hoje (planilhas, parquet da base
# nacional, previews de Modelo já reduzidos) levam no máximo ~2-3s numa
# conexão normal — 20s já é generoso. O motivo de reduzir é o PIOR CASO:
# com retry "standard", o boto3 tenta a chamada inteira de novo em erro
# retriável (timeout, conexão resetada) — pior caso é
# (connect_timeout + read_timeout) × tentativas_totais. A config anterior
# (60s/max_attempts=3 = 4 tentativas totais) dava até 280s (~4,7min) de
# espera silenciosa antes de qualquer erro aparecer na tela —
# indistinguível de "travado" pra quem está usando o app. Com 20s/
# max_attempts=1 (2 tentativas totais), o teto cai pra 60s: ainda dá uma
# chance de recuperar de uma falha transitória, mas falha rápido o
# bastante pra mostrar um erro em vez de parecer travado pra sempre.
#
# max_pool_connections=20 (default do botocore é 10): desde que
# modules/inventario.py passou a ler os metadata.json de cada ciclo em
# paralelo (ThreadPoolExecutor), o pool de conexões HTTP do cliente passou
# a ser um limite real — com só 10 conexões simultâneas liberadas, 22
# leituras paralelas viravam ~3 levas em vez de 1, perdendo boa parte do
# ganho da paralelização (handshake TLS novo a cada leva). 20 cobre
# confortavelmente o volume atual de ciclos no bucket, com folga.
_CONFIG_BOTO3 = Config(
    connect_timeout=10,
    read_timeout=20,
    retries={"max_attempts": 1, "mode": "standard"},
    max_pool_connections=20,
)


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
            config=_CONFIG_BOTO3,
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
        except BotoCoreError as e:
            raise StorageError(f"Falha de rede ao listar pasta '{caminho_relativo}': {e}") from e

        if not encontrou_algo:
            raise ArquivoNaoEncontradoError(f"Pasta não encontrada: {caminho_relativo}")

        return itens

    def _para_relativo(self, chave_completa: str) -> str:
        if self.prefixo and chave_completa.startswith(self.prefixo + "/"):
            return chave_completa[len(self.prefixo) + 1:]
        return chave_completa

    def ler_arquivo_bytes(self, caminho_relativo: str) -> bytes:
        chave = self._chave(caminho_relativo)
        try:
            resposta = self._cliente.get_object(Bucket=self.bucket, Key=chave)
            # O .read() fica DENTRO do try de propósito: o corpo da resposta
            # é lido em streaming DEPOIS do get_object retornar, então uma
            # queda de conexão no meio do download estoura aqui, não na
            # linha acima — se ficasse fora, essa falha (justamente a mais
            # provável numa conexão instável) escaparia sem virar StorageError.
            return resposta["Body"].read()
        except ClientError as e:
            codigo = e.response.get("Error", {}).get("Code", "")
            if codigo in ("NoSuchKey", "404"):
                raise ArquivoNaoEncontradoError(f"Arquivo não encontrado: {caminho_relativo}") from e
            raise StorageError(f"Falha ao ler arquivo '{caminho_relativo}': {e}") from e
        except BotoCoreError as e:
            raise StorageError(f"Falha de rede ao ler arquivo '{caminho_relativo}': {e}") from e

    def escrever_arquivo_bytes(self, caminho_relativo: str, conteudo: bytes) -> None:
        chave = self._chave(caminho_relativo)
        try:
            self._cliente.put_object(Bucket=self.bucket, Key=chave, Body=conteudo)
        except ClientError as e:
            raise StorageError(f"Falha ao escrever arquivo '{caminho_relativo}': {e}") from e
        except BotoCoreError as e:
            raise StorageError(f"Falha de rede ao escrever arquivo '{caminho_relativo}': {e}") from e

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
        except BotoCoreError as e:
            # Falha de rede NÃO é "não existe" — precisa virar erro explícito,
            # senão o app trataria uma conexão caída como "arquivo ausente" e
            # sobrescreveria dado bom sem pedir confirmação (ver o fluxo de
            # conflito de upload, que decide sobrescrever com base neste retorno).
            raise StorageError(f"Falha de rede ao verificar existência de '{caminho_relativo}': {e}") from e

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
        except BotoCoreError as e:
            raise StorageError(f"Falha de rede ao listar arquivos sob '{prefixo}': {e}") from e
