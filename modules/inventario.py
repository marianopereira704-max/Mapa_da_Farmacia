"""
Descoberta eficiente de todo o inventário de lojas/ciclos/arquivos do
armazenamento, numa única consulta (via
storage.listar_todos_arquivos_com_versao()), em vez de uma consulta por
loja — motivo de performance detalhado no README (seção "Descoberta do
inventário"). O conteúdo dos metadata.json vem de um índice validado por
versão (ver NOME_ARQUIVO_INDICE).

Sem dependência de Streamlit — testável isoladamente. O cache (via
st.cache_data) fica por conta de quem chama (app.py), não deste módulo.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import config
from modules.file_resolver import _normalizar_nome
from modules.storage.base import OneDriveStorage, StorageError

# Nome do arquivo de metadados por ciclo (loja/mês) -- guarda quem foi o
# último consultor a enviar algo para aquele ciclo, e quando. Não é um
# "tipo de arquivo" de config.FILE_SPECS (não aparece no checklist de
# arquivos presentes/ausentes de cada ciclo).
NOME_ARQUIVO_METADATA = "metadata.json"

# Índice na raiz do armazenamento (fora de qualquer pasta de loja, então a
# própria descoberta abaixo nunca o confunde com um arquivo de ciclo).
# Guarda SÓ uma cópia do conteúdo de cada metadata.json, junto com a versão
# (ETag) do arquivo de onde ela foi lida — evita reler centenas de
# metadata.json a cada atualização da tela de lojas (300 leituras em
# nov/2026, crescendo a cada mês).
#
# Segurança do índice (por que ele nunca mostra dado errado nem faz perder
# nada):
#   - Quais lojas/ciclos/arquivos EXISTEM continua vindo sempre da listagem
#     real do armazenamento, nunca do índice.
#   - Uma entrada do índice só é usada se a versão gravada nela for igual à
#     versão atual do metadata.json na listagem; qualquer diferença (ou
#     índice ausente/corrompido/de outro formato) = relê o arquivo real.
#   - O índice é só cache: apagá-lo não perde nada (é reconstruído
#     sozinho na próxima descoberta), e duas sessões gravando ao mesmo
#     tempo, no pior caso, deixam de fora uma entrada — que é relida na
#     vez seguinte, nunca usada errada.
NOME_ARQUIVO_INDICE = "_indice_inventario.json"
_FORMATO_INDICE = 1


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


def _assinatura(itens: list[tuple[str, str | None]]) -> str:
    """Resume (caminho, versão) de um grupo de arquivos numa string curta
    que muda se QUALQUER arquivo do grupo for criado, alterado ou apagado
    — usada pelo app como chave de cache dos dados daquele ciclo (ver
    app.py::_assinatura_ciclo), no lugar de invalidar tudo a cada troca
    de página. Se algum arquivo não tiver versão (backend sem ETag), não
    dá pra garantir nada: devolve um valor único a cada chamada, o que
    força recarregar — mesmo comportamento de antes do índice."""
    if any(versao is None for _, versao in itens):
        return uuid.uuid4().hex
    texto = "\n".join(f"{caminho}\t{versao}" for caminho, versao in sorted(itens))
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()


def _ler_indice(storage: OneDriveStorage) -> dict:
    """Devolve {caminho_metadata: {"versao": ..., "conteudo": ...}} do
    índice, ou {} se ele não existir, estiver corrompido ou for de outro
    formato — em todos esses casos o efeito é só reler os metadata.json
    reais, nunca usar algo duvidoso."""
    try:
        bruto = json.loads(storage.ler_arquivo_bytes(NOME_ARQUIVO_INDICE))
    except (StorageError, ValueError):
        return {}
    if not isinstance(bruto, dict) or bruto.get("formato") != _FORMATO_INDICE:
        return {}
    entradas = bruto.get("metadata")
    if not isinstance(entradas, dict):
        return {}
    return {
        caminho: entrada for caminho, entrada in entradas.items()
        if isinstance(entrada, dict)
        and isinstance(entrada.get("versao"), str)
        and (entrada.get("conteudo") is None or isinstance(entrada.get("conteudo"), dict))
    }


def descobrir_inventario(storage: OneDriveStorage) -> dict:
    """Mesmo resultado de descobrir_inventario_e_assinaturas(), sem a
    assinatura da base nacional — mantido para os scripts
    (relatorio_mensal, migrar_previews_modelo) que só precisam disso."""
    return descobrir_inventario_e_assinaturas(storage)[0]


def descobrir_inventario_e_assinaturas(storage: OneDriveStorage) -> tuple[dict, str]:
    """Lista o armazenamento UMA vez para descobrir toda a estrutura
    Loja -> Ciclo -> {arquivos presentes, metadata, assinatura}.

    Retorna (inventario, assinatura_base_nacional):
        {
          "2043": {
            "2026-07": {"arquivos": {"mapa_farmacia", "estoque", ...},
                        "metadata": {...} | None,
                        "assinatura": "<muda quando qualquer arquivo do ciclo muda>"},
            "2026-06": {...},
          },
          "556": {...},
        }

    Ignora tudo sob config.BASE_NACIONAL_FOLDER_NAME (não é uma loja — só
    entra na assinatura da base nacional) e qualquer caminho com menos de
    3 níveis (Loja/Ciclo/arquivo é o mínimo esperado na estrutura nova —
    sem consultor no caminho; o índice fica na raiz e cai aqui também).

    Um arquivo "presente" é identificado comparando o nome-base (sem
    extensão) contra config.FILE_SPECS, com a mesma tolerância de nome
    (maiúscula/minúscula/acento) usada em file_resolver.py.
    """
    versoes = storage.listar_todos_arquivos_com_versao("")
    mapa_basename = _mapa_basename_para_chave()

    inventario: dict[str, dict[str, dict]] = {}
    itens_por_ciclo: dict[tuple[str, str], list[tuple[str, str | None]]] = {}
    itens_base: list[tuple[str, str | None]] = []
    alvos = []

    for caminho, versao in versoes.items():
        partes = caminho.split("/")
        if partes[0] == config.BASE_NACIONAL_FOLDER_NAME:
            itens_base.append((caminho, versao))
            continue
        if len(partes) < 3:
            continue

        loja, ciclo, nome_arquivo = partes[0], partes[1], partes[-1]
        ciclo_info = inventario.setdefault(loja, {}).setdefault(
            ciclo, {"arquivos": set(), "metadata": None}
        )
        itens_por_ciclo.setdefault((loja, ciclo), []).append((caminho, versao))

        if nome_arquivo.lower() == NOME_ARQUIVO_METADATA:
            alvos.append((loja, ciclo, caminho, versao))
            continue

        chave_arquivo = _identificar_chave_arquivo(nome_arquivo, mapa_basename)
        if chave_arquivo:
            ciclo_info["arquivos"].add(chave_arquivo)

    for (loja, ciclo), itens in itens_por_ciclo.items():
        inventario[loja][ciclo]["assinatura"] = _assinatura(itens)

    # Metadata de cada ciclo: reaproveita do índice o que estiver com a
    # MESMA versão do arquivo real; só o resto (novos/alterados, ou tudo,
    # se o índice não existir) é lido de verdade.
    indice = _ler_indice(storage)
    novo_indice: dict[str, dict] = {}
    a_ler = []
    for loja, ciclo, caminho, versao in alvos:
        entrada = indice.get(caminho)
        if versao is not None and entrada is not None and entrada["versao"] == versao:
            inventario[loja][ciclo]["metadata"] = entrada["conteudo"]
            novo_indice[caminho] = entrada
        else:
            a_ler.append((loja, ciclo, caminho, versao))

    # Leitura dos que faltam em PARALELO (ThreadPoolExecutor) — cada
    # leitura é uma chamada de rede independente (I/O-bound, libera o GIL),
    # e sequencialmente isso já chegou a medir 3,2s com 22 metadata.json no
    # bucket real, crescendo linearmente com o número de ciclos.
    # Paralelizado, o tempo total fica perto do da chamada mais lenta, não
    # da SOMA de todas. Com o índice em dia, normalmente sobram só os
    # poucos ciclos que receberam upload desde a última descoberta.
    def _ler_metadata(alvo):
        loja, ciclo, caminho, versao = alvo
        try:
            conteudo = storage.ler_arquivo_bytes(caminho)
        except StorageError:
            # Falha de leitura (rede): o ciclo fica sem metadata NESTA
            # descoberta, mas NÃO vai pro índice — tenta de novo na
            # próxima, em vez de gravar "sem metadata" como se fosse verdade.
            #
            # Engolir falha de rede aqui é DELIBERADO, diferente dos outros
            # pontos do app (ver file_resolver/carregar_ajuste_mix_salvo, que
            # só engolem ArquivoNaoEncontradoError): o metadata é decorativo
            # — nome do consultor no cabeçalho e rótulo de mês do "depois",
            # ambos com fallback. Nenhuma decisão do app depende dele, então
            # degradar em silêncio aqui não mente pro usuário sobre dado que
            # importa; derrubar a tela inteira de seleção de loja, sim, seria
            # desproporcional.
            return loja, ciclo, caminho, versao, None, False
        try:
            metadata = json.loads(conteudo)
            if not isinstance(metadata, dict):
                raise ValueError("metadata.json não é um objeto JSON")
        except ValueError:
            # Arquivo corrompido: pode ir pro índice como None — enquanto
            # a versão (ETag) não mudar, o conteúdo continua o mesmo.
            return loja, ciclo, caminho, versao, None, True
        return loja, ciclo, caminho, versao, metadata, True

    if a_ler:
        with ThreadPoolExecutor(max_workers=min(20, len(a_ler))) as executor:
            for loja, ciclo, caminho, versao, metadata, guardar in executor.map(_ler_metadata, a_ler):
                inventario[loja][ciclo]["metadata"] = metadata
                if guardar and versao is not None:
                    novo_indice[caminho] = {"versao": versao, "conteudo": metadata}

    # Regrava o índice só se algo mudou (ciclos novos, alterados ou
    # removidos). Falhar aqui não é erro: o inventário já está correto,
    # só a próxima descoberta é que relê mais arquivos.
    if novo_indice != indice:
        try:
            storage.escrever_arquivo_bytes(
                NOME_ARQUIVO_INDICE,
                json.dumps(
                    {"formato": _FORMATO_INDICE, "metadata": novo_indice}, ensure_ascii=False
                ).encode("utf-8"),
            )
        except StorageError:
            pass

    return inventario, _assinatura(itens_base)
