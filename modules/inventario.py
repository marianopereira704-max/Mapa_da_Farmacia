"""
Descoberta eficiente de todo o inventário de lojas/ciclos/arquivos do
armazenamento, numa única consulta (via storage.listar_todos_arquivos()),
em vez de uma consulta por loja — motivo de performance detalhado no
README (seção "Descoberta do inventário").

Sem dependência de Streamlit — testável isoladamente. O cache (via
st.cache_data) fica por conta de quem chama (app.py), não deste módulo.
"""

from __future__ import annotations

import json

import config
from modules.file_resolver import _normalizar_nome
from modules.storage.base import OneDriveStorage, StorageError

# Nome do arquivo de metadados por ciclo (loja/mês) -- guarda quem foi o
# último consultor a enviar algo para aquele ciclo, e quando. Não é um
# "tipo de arquivo" de config.FILE_SPECS (não aparece no checklist de
# arquivos presentes/ausentes de cada ciclo).
NOME_ARQUIVO_METADATA = "metadata.json"


def _mapa_basename_para_chave() -> dict[str, str]:
    """Constrói {nome_base_normalizado: chave_do_file_specs}, ignorando
    'ajuste_mix' (não é um arquivo de dados enviado pela aba Upload, é
    gerado pelo próprio app ao salvar a Aba 1 — não faz sentido aparecer
    no checklist de arquivos presentes de um ciclo)."""
    mapa = {}
    for chave, spec in config.FILE_SPECS.items():
        if chave == "ajuste_mix":
            continue
        for basename in spec["basenames"]:
            mapa[_normalizar_nome(basename)] = chave
    return mapa


def _identificar_chave_arquivo(nome_arquivo: str, mapa_basename_exato: dict[str, str]) -> str | None:
    """Identifica a qual chave de config.FILE_SPECS um nome de arquivo
    corresponde, com a MESMA tolerância de duas passadas usada em
    file_resolver.localizar_arquivo():
      1a passada: nome (sem extensão, normalizado) bate EXATAMENTE com
         algum basename esperado.
      2a passada: o basename esperado aparece DENTRO do nome do arquivo
         (cobre variações como "estoque da loja.xlsx", onde o consultor
         acrescentou palavras extras ao nome padrão).
    Sem essa 2a passada, o checklist da página Selecionar Loja poderia
    discordar do que localizar_arquivo() realmente encontra ao processar
    a Análise — a mesma tolerância dos dois lugares evita essa
    inconsistência."""
    base = nome_arquivo.rsplit(".", 1)[0] if "." in nome_arquivo else nome_arquivo
    base_norm = _normalizar_nome(base)

    chave_exata = mapa_basename_exato.get(base_norm)
    if chave_exata:
        return chave_exata

    for basename_norm, chave in mapa_basename_exato.items():
        if basename_norm in base_norm:
            return chave
    return None


def descobrir_inventario(storage: OneDriveStorage) -> dict:
    """Usa storage.listar_todos_arquivos() UMA vez para descobrir toda a
    estrutura Loja -> Ciclo -> {arquivos presentes, metadata}.

    Retorna:
        {
          "2043": {
            "2026-07": {"arquivos": {"mapa_farmacia", "estoque", ...}, "metadata": {...} | None},
            "2026-06": {...},
          },
          "556": {...},
        }

    Ignora tudo sob config.BASE_NACIONAL_FOLDER_NAME (não é uma loja) e
    qualquer caminho com menos de 3 níveis (Loja/Ciclo/arquivo é o
    mínimo esperado na estrutura nova — sem consultor no caminho).

    Um arquivo "presente" é identificado comparando o nome-base (sem
    extensão) contra config.FILE_SPECS, com a mesma tolerância de nome
    (maiúscula/minúscula/acento) usada em file_resolver.py.
    """
    caminhos = storage.listar_todos_arquivos("")
    mapa_basename = _mapa_basename_para_chave()

    inventario: dict[str, dict[str, dict]] = {}

    for caminho in caminhos:
        partes = caminho.split("/")
        if len(partes) < 3:
            continue

        loja, ciclo, nome_arquivo = partes[0], partes[1], partes[-1]
        if loja == config.BASE_NACIONAL_FOLDER_NAME:
            continue

        ciclo_info = inventario.setdefault(loja, {}).setdefault(
            ciclo, {"arquivos": set(), "metadata": None}
        )

        if nome_arquivo.lower() == NOME_ARQUIVO_METADATA:
            continue  # lido na segunda passada, abaixo

        chave_arquivo = _identificar_chave_arquivo(nome_arquivo, mapa_basename)
        if chave_arquivo:
            ciclo_info["arquivos"].add(chave_arquivo)

    # Segunda passada: lê o metadata.json de cada ciclo que tiver um --
    # feita depois, pra já ter certeza de que o ciclo existe no dict
    # (independente da ordem em que os caminhos vieram de
    # listar_todos_arquivos, que não é garantida).
    for caminho in caminhos:
        partes = caminho.split("/")
        if len(partes) < 3:
            continue
        loja, ciclo, nome_arquivo = partes[0], partes[1], partes[-1]
        if loja == config.BASE_NACIONAL_FOLDER_NAME:
            continue
        if nome_arquivo.lower() != NOME_ARQUIVO_METADATA:
            continue

        try:
            conteudo = storage.ler_arquivo_bytes(caminho)
            inventario[loja][ciclo]["metadata"] = json.loads(conteudo)
        except (StorageError, ValueError):
            # metadata.json corrompido ou ilegível -- não é motivo pra
            # derrubar a descoberta do inventário inteiro, só fica sem
            # metadata pra esse ciclo (mesmo tratamento de ciclo antigo
            # pré-migração, que nunca teve metadata.json).
            pass

    return inventario
