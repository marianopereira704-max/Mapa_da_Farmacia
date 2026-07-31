"""
Ponte entre o storage (que só sabe listar pastas e ler bytes) e o
data_loader (que sabe interpretar o conteúdo de cada tipo de planilha).

Resolve três coisas:
  1. Encontrar, dentro de uma pasta, o arquivo que corresponde a um dos
     nomes/extensões aceitos (ex.: "mapa_farmacia.xlsx"), com
     correspondência TOLERANTE a maiúsculas/minúsculas, acentos, espaços
     e traços — um consultor pode salvar como "Mapa_Farmacia.XLSX",
     "mapa da farmácia.xlsx" ou "MAPA-FARMACIA.xlsx" e o arquivo
     precisa ser encontrado do mesmo jeito.
  2. Diferenciar arquivo OBRIGATÓRIO ausente (deve interromper com
     mensagem clara) de OPCIONAL ausente (deve seguir em frente).
  3. Descobrir os "ciclos de análise" de uma loja — a subpasta de mês é
     OPCIONAL: se o consultor só analisou aquela loja uma vez, os
     arquivos podem estar direto dentro da pasta da loja, sem subpasta
     de mês. Se analisou mais de uma vez, cada mês vira uma subpasta.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import config
from modules.storage.base import ItemPasta, OneDriveStorage, StorageError


class ArquivoObrigatorioAusenteError(Exception):
    """Levantado quando um arquivo marcado como obrigatório (em
    config.FILE_SPECS) não é encontrado na pasta. A UI deve capturar isso
    e mostrar uma mensagem clara indicando o que falta — nunca deixar o
    app quebrar sem explicação."""

    def __init__(self, chave: str, pasta: str):
        self.chave = chave
        self.pasta = pasta
        super().__init__(f"Arquivo obrigatório '{chave}' não encontrado em: {pasta}")


@dataclass
class ArquivoEncontrado:
    caminho_relativo: str
    extensao: str  # inclui o ponto, ex.: ".xlsx"
    conteudo: bytes


# ---------------------------------------------------------------------------
# Normalização de nomes (tolerante a variações de digitação)
# ---------------------------------------------------------------------------

def _normalizar_nome(texto: str) -> str:
    """Remove acentuação, baixa a caixa, e reduz espaços/traços/underscores
    a um único separador — pra comparar "Mapa da Farmácia", "mapa_farmacia"
    e "MAPA-FARMACIA" como equivalentes."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower().strip()
    texto = re.sub(r"[\s\-_]+", "_", texto)
    texto = re.sub(r"[^a-z0-9_]", "", texto)
    return texto


def _dividir_nome_extensao(nome_arquivo: str) -> tuple[str, str]:
    if "." not in nome_arquivo:
        return nome_arquivo, ""
    base, ext = nome_arquivo.rsplit(".", 1)
    return base, f".{ext.lower()}"


# ---------------------------------------------------------------------------
# Localização de arquivo dentro de uma pasta
# ---------------------------------------------------------------------------

def localizar_arquivo(
    storage: OneDriveStorage,
    pasta_relativa: str,
    spec: dict,
    chave: str = "",
) -> ArquivoEncontrado | None:
    """Procura, dentro de `pasta_relativa`, um arquivo cujo nome (sem
    extensão, normalizado) bata com algum de spec['basenames'] e cuja
    extensão esteja em spec['extensions'] — testando as extensões na
    ordem de preferência definida em config.py (ex.: .parquet antes de
    .xlsx). A comparação de nome ignora maiúsculas/minúsculas, acentos,
    espaços e traços.

    Retorna None se não encontrar e spec['obrigatorio'] for False.
    Levanta ArquivoObrigatorioAusenteError se não encontrar e
    spec['obrigatorio'] for True.
    """
    try:
        itens = storage.listar_pasta(pasta_relativa)
    except StorageError:
        itens = []

    basenames_normalizados = [_normalizar_nome(b) for b in spec["basenames"]]
    extensoes_aceitas = [e.lower() for e in spec["extensions"]]

    # Mapa: nome_base_normalizado -> {extensao: ItemPasta}
    candidatos: dict[str, dict[str, ItemPasta]] = {}
    for item in itens:
        if item.e_pasta:
            continue
        base, ext = _dividir_nome_extensao(item.nome)
        base_norm = _normalizar_nome(base)
        candidatos.setdefault(base_norm, {})[ext] = item

    # 1a passada: correspondência EXATA do nome normalizado (mais segura —
    # evita, por exemplo, confundir "estoque_pmf_backup" com "estoque").
    for extensao in extensoes_aceitas:
        for base_norm in basenames_normalizados:
            item = candidatos.get(base_norm, {}).get(extensao)
            if item is not None:
                conteudo = storage.ler_arquivo_bytes(item.caminho_completo)
                return ArquivoEncontrado(
                    caminho_relativo=item.caminho_completo,
                    extensao=extensao,
                    conteudo=conteudo,
                )

    # 2a passada: o nome esperado aparece DENTRO do nome do arquivo — cobre
    # variações como "estoque da loja.xlsx" ou "mapa_farmacia_loja1188.xlsx",
    # onde o consultor acrescentou palavras extras ao nome padrão.
    for extensao in extensoes_aceitas:
        for nome_base_arquivo, exts in candidatos.items():
            item = exts.get(extensao)
            if item is None:
                continue
            if any(base_norm in nome_base_arquivo for base_norm in basenames_normalizados):
                conteudo = storage.ler_arquivo_bytes(item.caminho_completo)
                return ArquivoEncontrado(
                    caminho_relativo=item.caminho_completo,
                    extensao=extensao,
                    conteudo=conteudo,
                )

    if spec.get("obrigatorio"):
        raise ArquivoObrigatorioAusenteError(chave, pasta_relativa)

    return None


# ---------------------------------------------------------------------------
# Descoberta de ciclos de análise (com ou sem subpasta de mês)
# ---------------------------------------------------------------------------

def _pasta_contem_dados_de_loja(storage: OneDriveStorage, caminho: str) -> bool:
    """Verifica se uma pasta contém, diretamente dentro dela, os arquivos
    de dados de uma análise (mapa_farmacia e/ou estoque) — usado para
    decidir se essa pasta em si já é um "ciclo de análise" (caso não
    exista subpasta de mês)."""
    try:
        itens = storage.listar_pasta(caminho)
    except StorageError:
        return False

    nomes_base_normalizados = {
        _normalizar_nome(base)
        for chave in ("mapa_farmacia", "estoque")
        for base in config.FILE_SPECS[chave]["basenames"]
    }

    for item in itens:
        if item.e_pasta:
            continue
        base, _ext = _dividir_nome_extensao(item.nome)
        base_norm = _normalizar_nome(base)
        if any(nb == base_norm or nb in base_norm for nb in nomes_base_normalizados):
            return True
    return False


def listar_ciclos_analise(storage: OneDriveStorage, caminho_loja: str) -> list[str]:
    """Retorna a lista de caminhos (relativos) de cada ciclo de análise
    de uma loja.

    A subpasta de mês é opcional:
      - Se os arquivos estiverem direto dentro da pasta da loja (loja
        analisada uma única vez, sem organização por mês), a própria
        pasta da loja é retornada como o único ciclo.
      - Se existirem subpastas (um ou mais meses), cada subpasta é
        retornada como um ciclo — MESMO que a pasta da loja também
        contenha arquivos soltos (cobre o caso de a primeira análise
        não ter sido organizada em subpasta e as seguintes sim).

    Cada item retornado é o caminho pronto para ser passado como
    `pasta_relativa` em localizar_arquivo().
    """
    ciclos = []

    if _pasta_contem_dados_de_loja(storage, caminho_loja):
        ciclos.append(caminho_loja)

    try:
        itens = storage.listar_pasta(caminho_loja)
    except StorageError:
        itens = []

    for item in itens:
        if item.e_pasta:
            ciclos.append(item.caminho_completo)

    return ciclos
