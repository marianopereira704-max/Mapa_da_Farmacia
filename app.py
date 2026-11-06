"""
Mapa da Farmácia — aplicação principal (Streamlit).

Navegação: st.navigation()/st.Page() nativos do Streamlit (>= 1.36),
posicionamento "hidden" (a sidebar automática do Streamlit fica escondida
— a navegação visível continua sendo a nossa, customizada, em CSS). Cada
página é um script GENUINAMENTE separado: o Streamlit só executa o corpo
da página para a qual se navegou, nunca o código das outras juntas — essa
é uma garantia estrutural do próprio framework, diferente do esquema
anterior (se página == "X": ... elif ...), que dependia só de convenção de
código para não rodar as abas não-visíveis.

5 páginas: Upload, Selecionar Loja, e as 3 sub-páginas de Análise (Ajuste
de mix / Sugestão de GC / Conferência) — cada sub-aba de Análise que antes
era só um `elif aba_ativa == "..."` dentro do mesmo script agora é uma
st.Page própria, com sua própria função. Trocar de página usa
st.switch_page() no lugar do antigo padrão
`st.session_state["_pagina_solicitada"] = "X"; st.rerun()`.

O consultor não faz mais parte do caminho físico dos arquivos (estrutura
{Loja}/{AAAA-MM}/arquivo, sem consultor no caminho — não faz mais sentido
com o DigitalOcean Spaces, que não separa permissão por pasta como o
OneDrive fazia). Ele agora é só um metadado (metadata.json de cada
ciclo), usado como filtro na página Selecionar Loja.
"""

from __future__ import annotations

import html
import io
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

import config
import modules.auth as auth
import modules.data_loader as dl
import modules.image_utils as image_utils
import modules.inventario as inventario
import modules.lojas_api as lojas_api
import modules.pdf_export as pdf_export
import modules.resultado_acao as ra
import modules.resultado_tela as resultado_tela
import modules.selecao_loja as selecao_loja
import modules.styles as styles
from modules.file_resolver import ArquivoObrigatorioAusenteError, localizar_arquivo, localizar_item
from modules.storage import ArquivoNaoEncontradoError, StorageError, get_storage_client
from streamlit_searchbox import st_searchbox

st.set_page_config(
    page_title=config.APP_TITLE,
    page_icon="🏪",
    layout="wide",
)
styles.aplicar_estilo()
auth.exigir_autenticacao()


# ---------------------------------------------------------------------------
# Storage: obtido uma vez, reaproveitado durante toda a sessão
# ---------------------------------------------------------------------------

def obter_storage():
    # O objeto guardado na sessão sobrevive a um deploy (o Streamlit
    # recarrega os módulos, mas quem já estava com o app aberto mantém o
    # session_state) — e continua sendo uma instância da classe ANTIGA, sem
    # os métodos novos (ex.: AttributeError em
    # listar_todos_arquivos_com_versao logo após o deploy de nov/2026).
    # Recria quando a classe com esse nome no módulo atual não é mais a do
    # objeto guardado.
    storage_salvo = st.session_state.get("storage")
    if storage_salvo is not None:
        classe_salva = type(storage_salvo)
        classe_atual = getattr(sys.modules.get(classe_salva.__module__), classe_salva.__name__, None)
        if classe_atual is not classe_salva:
            st.session_state["_storage_stale"] = True
    if "storage" not in st.session_state or st.session_state.get("_storage_stale"):
        try:
            st.session_state["storage"] = get_storage_client(dict(st.secrets))
            st.session_state["_storage_stale"] = False
        except StorageError as e:
            st.error(f"Não foi possível conectar ao armazenamento: {e}")
            st.stop()
    return st.session_state["storage"]


storage = obter_storage()


# ---------------------------------------------------------------------------
# Inventário: descoberta de toda a estrutura Loja -> Ciclo -> arquivos numa
# única consulta (ver modules/inventario.py e README — motivo de
# performance) — cacheado pelo mesmo padrão TTL 600s + versao_cache já
# usado nas outras consultas da página Análise.
# ---------------------------------------------------------------------------

@st.cache_data(ttl=600, show_spinner="Carregando lista de lojas...")
def _inventario_e_assinaturas_cache(_storage, versao_cache: int) -> tuple[dict, str]:
    return inventario.descobrir_inventario_e_assinaturas(_storage)


def _descobrir_inventario_cache(_storage, versao_cache: int) -> dict:
    return _inventario_e_assinaturas_cache(_storage, versao_cache)[0]


# Chaves de cache dos dados pesados (planilhas, base nacional, imagens):
# em vez de versao_cache — que muda a cada entrada em Selecionar Loja/
# Análise e forçava baixar e reprocessar TUDO de novo, mesmo sem nenhuma
# mudança —, usam a assinatura do ciclo/base vinda do inventário, que só
# muda quando algum arquivo daquele ciclo (ou da base) é criado, alterado
# ou apagado. Assim um upload novo continua aparecendo na próxima
# atualização do inventário, mas navegar sem mudanças não baixa nada de
# novo. Se o inventário falhar (rede) ou o ciclo não estiver nele, cai no
# comportamento antigo (versao_cache), que nunca mostra dado velho.
def _assinatura_ciclo(caminho_ciclo: str) -> str:
    versao_cache = st.session_state["versao_cache"]
    try:
        inventario_atual, _ = _inventario_e_assinaturas_cache(storage, versao_cache)
    except StorageError:
        return f"versao-{versao_cache}"
    loja, _, mes = caminho_ciclo.partition("/")
    info_ciclo = inventario_atual.get(loja, {}).get(mes)
    if not info_ciclo:
        return f"versao-{versao_cache}"
    return info_ciclo["assinatura"]


def _assinatura_base_nacional() -> str:
    versao_cache = st.session_state["versao_cache"]
    try:
        return _inventario_e_assinaturas_cache(storage, versao_cache)[1]
    except StorageError:
        return f"versao-{versao_cache}"


# ---------------------------------------------------------------------------
# Lista oficial de lojas (API do TI) — usada só na página Upload, para o
# seletor de loja e o pré-preenchimento do consultor. Cacheada por 1h: é
# uma lista que muda raramente (não a cada upload), então não precisa do
# TTL curto nem do padrão versao_cache usado no inventário/dados de loja.
# De propósito NÃO é bumpada pela atualização automática ao entrar em
# "Selecionar Loja"/"Análise" (ver mais abaixo) — só o TTL de 1h força a
# recarga desta lista, pra não bater na API do TI a cada navegação.
@st.cache_data(ttl=3600, show_spinner="Carregando lista de lojas (API do TI)...")
def _carregar_lojas_api() -> list[lojas_api.Loja]:
    config_api = dict(st.secrets.get("api_lojas", {}))
    return lojas_api.obter_lojas(config_api.get("url", ""), config_api.get("api_key", ""))


@st.cache_data(ttl=3600, show_spinner="Carregando lista de lojas (API do TI)...")
def _carregar_cadastro_lojas() -> dict[str, lojas_api.Loja]:
    """Inclui as inativas: loja já desligada continua na lista do mês em que
    enviou arquivos, com a razão social."""
    config_api = dict(st.secrets.get("api_lojas", {}))
    lojas = lojas_api.obter_lojas(config_api.get("url", ""), config_api.get("api_key", ""), apenas_ativas=False)
    return {loja.codigo: loja for loja in lojas}


# ---------------------------------------------------------------------------
# Carregamento de dados da loja/ciclo selecionado (usado só na página
# Análise)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=600, show_spinner="Carregando dados da loja...")
def _carregar_dados_loja(_storage, caminho_ciclo: str, assinatura: str):
    # Lista a pasta do ciclo UMA vez e reaproveita nas duas chamadas de
    # localizar_arquivo abaixo (mapa_farmacia + estoque) — evita listar a
    # mesma pasta duas vezes (cada storage.listar_pasta é uma chamada de
    # rede). Se a listagem falhar aqui, passa lista vazia — localizar_
    # arquivo já sabe lidar com isso (obrigatório ausente levanta erro
    # claro, opcional ausente retorna None).
    try:
        itens_ciclo = _storage.listar_pasta(caminho_ciclo)
    except StorageError:
        itens_ciclo = []

    arq_mapa = localizar_arquivo(_storage, caminho_ciclo, config.FILE_SPECS["mapa_farmacia"], "mapa_farmacia", itens=itens_ciclo)
    mapa = dl.carregar_mapa_farmacia(io.BytesIO(arq_mapa.conteudo))

    arq_estoque = localizar_arquivo(_storage, caminho_ciclo, config.FILE_SPECS["estoque"], "estoque", itens=itens_ciclo)
    estoque = dl.carregar_estoque(io.BytesIO(arq_estoque.conteudo))

    return mapa, estoque


@st.cache_data(ttl=3600, show_spinner="Carregando base nacional de demanda...")
def _carregar_base_nacional(_storage, assinatura: str):
    arq_base = localizar_arquivo(_storage, config.BASE_NACIONAL_FOLDER_NAME, config.BASE_NACIONAL_SPEC, "base_mercado")
    return dl.carregar_base_nacional(io.BytesIO(arq_base.conteudo), extensao=arq_base.extensao)


@st.cache_data(
    ttl=600,
    show_spinner="Carregando imagem (arquivos grandes podem levar alguns segundos)...",
)
def _carregar_imagem_conferencia(_storage, caminho_ciclo: str, chave: str, assinatura: str):
    """Localiza e carrega uma imagem da Aba Conferência (foto_antes/
    modelo/foto_depois). Retorna (bytes_imagem, mime, mensagem_vazio):
      - achou e é jpg/png: (bytes, "image/jpeg"|"image/png", None)
      - achou e é pdf (só ocorre para "modelo"): extrai a página 2 como
        imagem e retorna (bytes_png, "image/png", None)
      - não achou: (None, None, "Ainda não enviada")
      - achou mas não conseguiu processar (ex.: pdf corrompido) ou
        qualquer outra falha inesperada: (None, None, "Não foi possível
        processar o arquivo") — nunca propaga a exceção, pra não derrubar
        a aba inteira por causa de um arquivo problemático.
    """
    try:
        arq = localizar_arquivo(_storage, caminho_ciclo, config.FILE_SPECS[chave], chave)
    except Exception:
        return None, None, "Não foi possível processar o arquivo"

    if arq is None:
        return None, None, "Ainda não enviada"

    if arq.extensao == ".pdf":
        try:
            conteudo_png = image_utils.extrair_pagina_como_imagem(arq.conteudo, numero_pagina=2)
            return conteudo_png, "image/png", None
        except Exception:
            return None, None, "Não foi possível processar o arquivo"

    mime = "image/jpeg" if arq.extensao in (".jpg", ".jpeg") else "image/png"
    return arq.conteudo, mime, None


@st.cache_data(ttl=600, show_spinner=False)
def _carregar_preview_modelo(_storage, caminho_ciclo: str, variante: str, assinatura: str):
    """variante: 'tela' ou 'impressao'. Lê o preview já pronto (gerado no
    upload, ou pela migração única das lojas antigas) — nunca baixa nem
    processa o arquivo Modelo original.

    Distingue ArquivoNaoEncontradoError ("Ainda não enviada" — estado
    normal, e é isso que decide se o pop-up "Modelo ainda não enviado"
    aparece) de qualquer outra falha de storage/rede ("Não foi possível
    processar o arquivo"). Sem essa distinção, uma queda de conexão faria
    o app afirmar que o Modelo não foi enviado e mandar o consultor
    reenviar um arquivo que já está lá.
    """
    caminho = f"{caminho_ciclo}/modelo_{variante}.png"
    print(f"[PREVIEW-TEMPO] INICIO ler_arquivo_bytes({caminho!r})", flush=True)  # >>> DIAGNOSTICO
    _pv_t0 = time.perf_counter()  # >>> DIAGNOSTICO
    try:
        conteudo = _storage.ler_arquivo_bytes(caminho)
        print(f"[PREVIEW-TEMPO] FIM ler_arquivo_bytes({caminho!r}): {time.perf_counter() - _pv_t0:.2f}s, {len(conteudo)/1024:.1f} KB", flush=True)  # >>> DIAGNOSTICO
        return conteudo, "image/png", None
    except ArquivoNaoEncontradoError as e:
        print(f"[PREVIEW-TEMPO] FIM ler_arquivo_bytes({caminho!r}): {time.perf_counter() - _pv_t0:.2f}s, nao encontrado: {e}", flush=True)  # >>> DIAGNOSTICO
        return None, None, "Ainda não enviada"
    except Exception as e:
        print(f"[PREVIEW-TEMPO] FIM ler_arquivo_bytes({caminho!r}): {time.perf_counter() - _pv_t0:.2f}s, {type(e).__name__}: {e}", flush=True)  # >>> DIAGNOSTICO
        return None, None, "Não foi possível processar o arquivo"


@st.cache_data(ttl=600, show_spinner="Carregando resultado da ação...")
def _carregar_dados_resultado_acao(_storage, caminho_ciclo: str, assinatura: str) -> dict:
    """Carrega os dados necessários para a feature Resultado da ação (Aba
    3 — Conferência). Só monta os DataFrames quando os DOIS arquivos
    "depois" (Retrato de Vendas + Estoque atualizado) já foram enviados:
    sem o Estoque atualizado não dá para saber como a loja terminou o mês,
    então a feature não é exibida parcialmente.

    Retorna um dict:
      "retrato_presente" / "estoque_atualizado_presente": bool — pra UI
        avisar especificamente qual arquivo falta, se algum faltar (não é
        erro, é um estado esperado até o consultor enviar).
      "retrato_df" / "estoque_resultado_df" / "estoque_atualizado_df":
        None enquanto algum dos dois não foi enviado.
      "periodo": (início, fim) lido do próprio Retrato, ou None se o arquivo
        não tiver o período.
    """
    arq_retrato = localizar_arquivo(_storage, caminho_ciclo, config.FILE_SPECS["retrato_vendas"], "retrato_vendas")
    arq_estoque_atualizado = localizar_arquivo(
        _storage, caminho_ciclo, config.FILE_SPECS["estoque_atualizado"], "estoque_atualizado"
    )

    dados = {
        "retrato_presente": arq_retrato is not None,
        "estoque_atualizado_presente": arq_estoque_atualizado is not None,
        "retrato_df": None,
        "estoque_resultado_df": None,
        "estoque_atualizado_df": None,
        "periodo": None,
    }
    if arq_retrato is not None and arq_estoque_atualizado is not None:
        arq_estoque = localizar_arquivo(_storage, caminho_ciclo, config.FILE_SPECS["estoque"], "estoque")
        dados["retrato_df"] = dl.carregar_retrato_vendas(io.BytesIO(arq_retrato.conteudo))
        dados["estoque_resultado_df"] = dl.carregar_estoque_resultado(io.BytesIO(arq_estoque.conteudo))
        dados["estoque_atualizado_df"] = dl.carregar_estoque_atualizado_resultado(io.BytesIO(arq_estoque_atualizado.conteudo))
        dados["periodo"] = dl.extrair_periodo_retrato(io.BytesIO(arq_retrato.conteudo))
    return dados


@st.cache_data(show_spinner="Gerando PDF...")
def _gerar_pdf_gc_cache(
    tabela_gc: pd.DataFrame, consultor: str, loja: str, subtitulo: str, imagem_modelo: bytes | None
) -> bytes:
    """Cacheado pelo conteúdo da tabela (e da imagem do modelo) — evita
    regerar o PDF (custa processamento de imagem + desenho vetorial) a cada
    rerun da Aba 2 quando nada relevante mudou (ex.: só a busca em texto foi
    digitada, que nem afeta o conteúdo do PDF, gerado a partir da tabela
    completa). `imagem_modelo` é o PNG/JPEG já extraído (mesmo bytes usados
    na aba Conferência) — None quando a loja não tem Modelo enviado, e
    nesse caso a página "Modelo" simplesmente não entra no PDF."""
    return pdf_export.gerar_pdf_sugestao_gc(tabela_gc, consultor, loja, subtitulo, imagem_modelo)


# ---------------------------------------------------------------------------
# Paginação de listas longas de produtos — usada em Ajuste de mix (até
# ~300+ produtos, o Mapa da Farmácia inteiro da loja), Sugestão de GC e
# Produto a produto (Conferência) quando o ajuste de mix salvo também for
# grande. Cada linha da lista tinha (ou tem, em Ajuste de mix) widgets
# nativos por produto — renderizar todos de uma vez é o candidato
# investigado para o travamento ao trocar de sub-aba com listas grandes.
# 40 produtos por página (dentro da faixa 30-50 pedida): grande o
# suficiente pra não virar um catálogo de dezenas de páginas numa loja de
# ~300 produtos (~8 páginas), pequeno o suficiente pra cortar
# significativamente os widgets renderizados por execução do script.
# ---------------------------------------------------------------------------

_TAMANHO_PAGINA_LISTA = 40


def _pagina_atual_paginacao(chave_pagina: str, total_itens: int, tamanho_pagina: int, chave_reset=None) -> tuple[int, int]:
    """Gerencia (em st.session_state) o número da página atual (1-based)
    de uma lista paginada. `chave_pagina` precisa ser única por
    tabela+tela+loja/ciclo (ex.: "pagina_mix::{chave_estado}") — cada
    combinação tem seu próprio contador de página independente.

    `chave_reset`, se informado, é comparado com o valor da execução
    anterior: mudou (ex.: texto de busca ou filtro de status diferente) ->
    volta pra página 1. Sem isso, trocar o filtro podia deixar o usuário
    "preso" numa página que não existe mais no resultado filtrado.

    Também reancora (clampa) a página dentro do intervalo válido sempre
    que o total de itens encolher (ex.: filtro ficou mais restritivo,
    ou a própria loja/ciclo mudou) — nunca deixa a página apontar pra um
    intervalo vazio.

    Retorna (pagina_atual, total_paginas) — total_paginas nunca é menor
    que 1, mesmo com 0 itens (mostra uma "página 1 de 1" vazia, tratada
    pela mensagem "Nenhum produto encontrado" já existente em cada tela)."""
    total_paginas = max(1, -(-total_itens // tamanho_pagina))  # ceil division

    if chave_reset is not None:
        chave_reset_anterior = f"_paginacao_reset::{chave_pagina}"
        if st.session_state.get(chave_reset_anterior) != chave_reset:
            st.session_state[chave_reset_anterior] = chave_reset
            st.session_state[chave_pagina] = 1

    pagina_atual = max(1, min(st.session_state.get(chave_pagina, 1), total_paginas))
    st.session_state[chave_pagina] = pagina_atual
    return pagina_atual, total_paginas


def _fatia_pagina(tabela: pd.DataFrame, pagina_atual: int, tamanho_pagina: int) -> pd.DataFrame:
    indice_inicio = (pagina_atual - 1) * tamanho_pagina
    return tabela.iloc[indice_inicio: indice_inicio + tamanho_pagina]


def _controles_paginacao(chave_pagina: str, pagina_atual: int, total_paginas: int, total_itens: int, sufixo_key: str) -> None:
    """Desenha "← Anterior · Página X de Y (N produtos) · Próxima →". Não
    desenha nada quando a lista inteira cabe numa página só (não precisa
    de controle de navegação pra 1 página)."""
    if total_paginas <= 1:
        return
    c_ant, c_info, c_prox = st.columns([0.2, 0.6, 0.2])
    with c_ant:
        if st.button("← Anterior", key=f"{chave_pagina}_ant_{sufixo_key}", width="stretch", disabled=(pagina_atual <= 1)):
            st.session_state[chave_pagina] = pagina_atual - 1
            st.rerun()
    with c_info:
        st.markdown(
            f'<p class="mdf-paginacao-info">Página {pagina_atual} de {total_paginas} '
            f'· {total_itens} produto(s)</p>',
            unsafe_allow_html=True,
        )
    with c_prox:
        if st.button("Próxima →", key=f"{chave_pagina}_prox_{sufixo_key}", width="stretch", disabled=(pagina_atual >= total_paginas)):
            st.session_state[chave_pagina] = pagina_atual + 1
            st.rerun()


# ---------------------------------------------------------------------------
# Resultado da ação (Aba 3 — Conferência) — renderização
# ---------------------------------------------------------------------------

def _periodo_depois(periodo_arquivo, metadata_ciclo: dict | None) -> tuple[str, date]:
    """(rótulo, último dia) do período do "depois". O que vem do próprio
    Retrato tem prioridade. Ciclos enviados antes de o período ser lido do
    arquivo só têm "retrato_vendas_ano_mes", que era o mês do ENVIO."""
    metadata_ciclo = metadata_ciclo or {}
    if periodo_arquivo:
        return f"vendas de {periodo_arquivo[0]:%d/%m} a {periodo_arquivo[1]:%d/%m}", periodo_arquivo[1]
    periodo = metadata_ciclo.get("retrato_vendas_periodo")
    rotulo = _rotulo_periodo_retrato(periodo) if periodo else None
    if rotulo:
        return f"vendas de {rotulo}", date.fromisoformat(periodo["fim"])
    ano_mes = metadata_ciclo.get("retrato_vendas_ano_mes") or date.today().strftime("%Y-%m")
    return f"vendas de {styles.mes_legivel(ano_mes)}", date.today()


@st.fragment
def _renderizar_resultado_acao(
    resultado: ra.ResultadoAcao, rotulo_antes: str, rotulo_depois: str, ciclo_selecionado: str
) -> None:
    """Renderiza o dashboard do Resultado da ação. É um fragmento: clicar num
    filtro só refaz esta seção, sem recarregar as fotos acima dela."""
    st.markdown(resultado_tela.periodo_html(rotulo_antes, rotulo_depois), unsafe_allow_html=True)
    st.markdown(resultado_tela.cartao_principal_html(resultado), unsafe_allow_html=True)

    chave_grupo = f"resultado_grupo::{ciclo_selecionado}"
    chave_situacao = f"resultado_situacao::{ciclo_selecionado}"

    st.markdown(
        '<p class="mdf-ra-h2">Por grupo</p>',
        unsafe_allow_html=True,
    )
    resultado_tela.filtro_clicavel("cards", resultado_tela.itens_cards(resultado), chave_grupo)

    grupo = st.session_state.get(chave_grupo)
    situacao = st.session_state.get(chave_situacao)
    tabela = resultado.do_grupo(grupo) if grupo else resultado.tabela

    c_titulo, c_limpar = st.columns([0.8, 0.2], vertical_alignment="bottom")
    with c_titulo:
        st.markdown('<p class="mdf-ra-h2">Produto a produto</p>', unsafe_allow_html=True)
    with c_limpar:
        if grupo or situacao:
            def _limpar_filtros() -> None:
                st.session_state[chave_grupo] = None
                st.session_state[chave_situacao] = None

            st.button("Limpar filtros", key=f"resultado_limpar::{ciclo_selecionado}", width="stretch", on_click=_limpar_filtros)

    resultado_tela.filtro_clicavel("chips", resultado_tela.itens_chips(tabela, situacao), chave_situacao)
    if situacao:
        tabela = tabela[tabela["situacao"] == situacao]

    if tabela.empty:
        st.markdown('<p class="mdf-ra-vazio">Nenhum produto neste filtro.</p>', unsafe_allow_html=True)
    else:
        chave_pagina = f"pagina_resultado_acao::{ciclo_selecionado}"
        pagina_atual, total_paginas = _pagina_atual_paginacao(
            chave_pagina, len(tabela), _TAMANHO_PAGINA_LISTA, chave_reset=(grupo, situacao),
        )
        pagina = _fatia_pagina(tabela, pagina_atual, _TAMANHO_PAGINA_LISTA)
        st.markdown(resultado_tela.tabela_html(pagina), unsafe_allow_html=True)
        _controles_paginacao(chave_pagina, pagina_atual, total_paginas, len(tabela), sufixo_key="rodape")

        c_rodape, c_exportar = st.columns([0.8, 0.2], vertical_alignment="center")
        with c_rodape:
            st.markdown(
                f'<p class="mdf-ra-apoio">Mostrando {len(pagina)} de {len(tabela)}, do maior para o menor impacto.</p>',
                unsafe_allow_html=True,
            )
        with c_exportar:
            st.download_button(
                "Exportar lista",
                resultado_tela.csv_da_lista(tabela),
                file_name=f"resultado_acao_{ciclo_selecionado.replace('/', '_')}.csv",
                mime="text/csv",
                on_click="ignore",
                width="stretch",
                key=f"resultado_exportar::{ciclo_selecionado}",
            )

    with st.expander("Como o resultado é calculado"):
        st.markdown(resultado_tela.EXPLICACAO)


# ---------------------------------------------------------------------------
# metadata.json de um ciclo (loja/mês) — quem enviou por último, e quando.
# ---------------------------------------------------------------------------

def _atualizar_metadata_ciclo(
    storage, loja: str, ano_mes: str, consultor: str, retrato_periodo: dict | None = None
) -> None:
    """Cria ou atualiza (mesclando com o que já existir) o metadata.json
    do ciclo {loja}/{ano_mes}. Só sobrescreve o campo 'consultor' se um
    valor não-vazio foi informado neste envio — um envio sem consultor
    preenchido não apaga o consultor registrado por um envio anterior.

    Quando um Retrato de Vendas foi enviado (`retrato_periodo`, ver
    _montar_periodo_retrato), grava o período de VENDAS do arquivo — lido
    do próprio arquivo ou informado pelo consultor no pop-up — em
    'retrato_vendas_ano_mes' (mês do início) e 'retrato_vendas_periodo'
    (datas exatas + origem). É isso que a feature Resultado da ação (Aba
    3) mostra como o mês "depois" do comparativo (ver aba_conf). Antes
    era carimbado o mês do ENVIO, que costuma ser o mês seguinte ao das
    vendas (Retrato de um mês fechado é enviado no começo do outro)."""
    caminho = f"{loja}/{ano_mes}/{inventario.NOME_ARQUIVO_METADATA}"
    metadata_atual = {}
    try:
        metadata_atual = json.loads(storage.ler_arquivo_bytes(caminho))
    except (StorageError, ValueError):
        pass

    if consultor:
        metadata_atual["consultor"] = consultor
    # UTC com offset explícito (+00:00): o servidor do Streamlit Cloud roda
    # em UTC, e um horário "ingênuo" fica ambíguo pra quem lê depois (ex.:
    # relatório mensal, que converte pra horário de Brasília).
    metadata_atual["enviado_em"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if retrato_periodo:
        metadata_atual["retrato_vendas_ano_mes"] = retrato_periodo["ano_mes"]
        metadata_atual["retrato_vendas_periodo"] = {
            chave: retrato_periodo[chave] for chave in ("inicio", "fim", "origem")
        }

    conteudo = json.dumps(metadata_atual, ensure_ascii=False, indent=2).encode("utf-8")
    storage.escrever_arquivo_bytes(caminho, conteudo)


# ---------------------------------------------------------------------------
# Período de vendas do Retrato de Vendas
# ---------------------------------------------------------------------------

def _montar_periodo_retrato(inicio: date, fim: date, origem: str) -> dict:
    """origem: "arquivo" (lido de "Filtros aplicados") ou "informado"
    (escolhido pelo consultor no pop-up, quando o arquivo não tem)."""
    return {
        "inicio": inicio.isoformat(),
        "fim": fim.isoformat(),
        "origem": origem,
        "ano_mes": inicio.strftime("%Y-%m"),
    }


def _periodo_e_mes_inteiro(inicio: date, fim: date) -> bool:
    proximo_mes = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1)
    return inicio.day == 1 and fim == proximo_mes - timedelta(days=1)


def _rotulo_periodo_retrato(periodo: dict) -> str | None:
    """'Julho 2026' quando o período é um mês inteiro; '01/07/2026 a
    15/07/2026' quando não é. None se o dict estiver malformado."""
    try:
        inicio = date.fromisoformat(periodo["inicio"])
        fim = date.fromisoformat(periodo["fim"])
    except (KeyError, TypeError, ValueError):
        return None
    if _periodo_e_mes_inteiro(inicio, fim):
        return styles.mes_legivel(inicio.strftime("%Y-%m"))
    return f"{inicio:%d/%m/%Y} a {fim:%d/%m/%Y}"


def _periodo_retrato_para_envio(conteudo: bytes) -> dict | None:
    """Período do Retrato que está sendo enviado: o do próprio arquivo;
    se ele não tiver, o mês informado no pop-up (_dialog_mes_retrato); se
    nenhum dos dois, None — e aí quem chama não grava nada e abre o
    pop-up."""
    mes_informado = st.session_state.pop("_upload_mes_retrato_informado", None)
    lido = dl.extrair_periodo_retrato(io.BytesIO(conteudo))
    if lido:
        return _montar_periodo_retrato(lido[0], lido[1], "arquivo")
    if mes_informado:
        ano, mes = (int(p) for p in mes_informado.split("-"))
        inicio = date(ano, mes, 1)
        fim = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return _montar_periodo_retrato(inicio, fim, "informado")
    return None


def _fechar_dialog_mes_retrato() -> None:
    st.session_state.pop("_upload_pedir_mes_retrato", None)


@st.dialog("Qual o mês do Retrato de Vendas?", on_dismiss=_fechar_dialog_mes_retrato)
def _dialog_mes_retrato() -> None:
    """Aberto quando o Retrato de Vendas enviado não traz o período de
    vendas no rodapé ("Filtros aplicados"). Nada foi gravado ainda:
    confirmar reenvia o lote inteiro sozinho, já com o mês escolhido;
    cancelar (ou fechar pelo X, ver on_dismiss) não envia nada. O pedido
    (_upload_pedir_mes_retrato) fica guardado enquanto o pop-up estiver
    aberto e só é limpo por uma dessas três saídas."""
    st.write(
        "Não encontramos o período de vendas no arquivo do **Retrato de Vendas** "
        "(normalmente ele aparece no rodapé, em \"Filtros aplicados\"). "
        "Informe a qual mês as vendas desse arquivo se referem."
    )
    opcoes = []
    ano, mes = map(int, selecao_loja.mes_atual().split("-"))
    for _ in range(6):
        opcoes.append(f"{ano:04d}-{mes:02d}")
        ano, mes = (ano - 1, 12) if mes == 1 else (ano, mes - 1)
    # Padrão = mês anterior: o Retrato costuma ser de um mês já fechado.
    mes_escolhido = st.selectbox("Mês das vendas", opcoes, index=1, format_func=styles.mes_legivel)
    c1, c2 = st.columns(2)
    if c1.button("Confirmar e enviar", type="primary", key="retrato_mes_confirmar", width="stretch"):
        _fechar_dialog_mes_retrato()
        st.session_state["_upload_mes_retrato_informado"] = mes_escolhido
        st.session_state["_upload_reenviar"] = True
        st.rerun()
    if c2.button("Cancelar", key="retrato_mes_cancelar", width="stretch"):
        _fechar_dialog_mes_retrato()
        st.rerun()


def _validar_mapa_farmacia_upload(conteudo: bytes) -> list[dict]:
    """Faz o parse do Mapa da Farmácia recém-selecionado no formulário de
    Upload só para validar — não é usado para carregar dados de fato (isso
    acontece depois, na Análise, com o arquivo já salvo). Deixa
    `dl.PlanilhaInvalidaError` propagar quando falta coluna obrigatória
    (módulo/EAN/produto) — quem chama decide o que fazer (ver botão
    "Enviar arquivos"). Quando o arquivo é válido, retorna a lista de
    colisões de (módulo, posição) encontradas (vazia se não houver
    nenhuma) — ver `dl.carregar_mapa_farmacia`."""
    df = dl.carregar_mapa_farmacia(io.BytesIO(conteudo))
    return df.attrs.get("colisoes_modulo_posicao", [])


@st.dialog("⚠️ Possível erro na planilha do Mapa da Farmácia")
def _dialog_colisoes_modulo_posicao(colisoes: list[dict]) -> None:
    """Pop-up didático mostrado depois de salvar um Mapa da Farmácia em
    que 2+ produtos disputam a mesma posição dentro do mesmo módulo — sinal
    de erro de preenchimento na planilha de origem (ex.: alguém editou
    módulo/posição manualmente). Não bloqueia nada: o arquivo já foi salvo
    antes deste aviso aparecer, isso é só um alerta pro consultor corrigir
    na origem quando puder."""
    st.write(
        "O arquivo foi salvo, mas encontramos mais de um produto ocupando a "
        "mesma posição dentro do mesmo módulo. Isso costuma acontecer quando "
        "a planilha foi editada manualmente e o módulo ou a posição de algum "
        "produto ficou incorreto."
    )
    for item in colisoes:
        produtos_fmt = ", ".join(f"**{p}**" for p in item["produtos"])
        st.markdown(f"- Módulo **{item['modulo']}**, posição **{item['posicao']}**: {produtos_fmt}")
    st.write(
        "**O que fazer:** confira a planilha do Mapa da Farmácia e corrija o "
        "módulo e/ou a posição dos produtos listados acima, de forma que "
        "cada um ocupe um lugar único na gôndola. Depois, envie o arquivo "
        "corrigido novamente nesta página."
    )
    if st.button("Entendi", type="primary", key="upload_colisoes_entendi"):
        st.session_state.pop("_upload_colisoes_pendentes", None)
        st.rerun()


@st.dialog("Modelo ainda não enviado")
def _dialog_modelo_ausente(loja: str, ano_mes: str) -> None:
    """Pop-up mostrado na aba Sugestão de GC quando a loja/ciclo em análise
    ainda não tem o arquivo de Modelo (planograma) enviado — esse arquivo
    agora também vira uma página no PDF exportado ali, além de já ser usado
    como referência na aba Conferência."""
    st.write(
        "Esta loja ainda não tem o arquivo do **Modelo (planograma)** "
        "enviado. Ele é usado como referência na aba Conferência e agora "
        "também entra como uma página no PDF exportado aqui na Sugestão de GC."
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Ir para Upload", type="primary", key="gc_modelo_ir_upload", width="stretch"):
            _abrir_upload(loja, ano_mes)
    with c2:
        if st.button("Fechar", key="gc_modelo_fechar", width="stretch"):
            st.rerun()


def _abrir_upload(loja: str | None = None, mes: str | None = None) -> None:
    """Único caminho para a página Upload. Com `loja`, abre com ela preenchida;
    com `mes`, grava nesse mês em vez do atual. Sem nada (menu lateral), abre em
    branco. Sem limpar o campo de loja, ele voltaria vazio na tela mas ainda
    guardando a loja anterior, e o envio iria para ela. Mensagens e confirmações
    da visita anterior também saem, para não parecerem desta loja."""
    for chave in (
        "upload_loja_searchbox", "_ultima_loja_upload", "_upload_enviados_ok",
        "_upload_falhas", "_upload_avisos", "_upload_conflitos",
    ):
        st.session_state.pop(chave, None)
    st.session_state["_upload_loja_prefill"] = loja
    st.session_state["_upload_ano_mes_contexto"] = mes
    st.session_state["_upload_ano_mes_contexto_loja"] = loja if mes else None
    st.switch_page(PAGINA_UPLOAD)


# Rótulos de exibição dos tipos de arquivo por loja, na ordem do formulário de
# Upload. "retrato_vendas" e "estoque_atualizado" só aparecem nele quando o mês
# de destino já tem arquivos (ver selecao_loja.arquivos_do_upload).
_ROTULOS_ARQUIVOS_UPLOAD = {
    "mapa_farmacia": "Mapa da Farmácia",
    "estoque": "Estoque",
    "modelo": "Modelo (planograma)",
    "foto_antes": "Foto Antes",
    "foto_depois": "Foto Depois",
    "retrato_vendas": "Retrato de Vendas",
    "estoque_atualizado": "Estoque atualizado",
}

# CSS-in-JS aplicado ao componente React do campo Loja (streamlit-
# searchbox), pra ficar visualmente IDÊNTICO ao campo Consultor ao lado
# (st.text_input nativo — CSS dele em styles.py, marcador
# .mdf-campo-consultor-marker). Usa as mesmas constantes de config.py dos
# dois lados, de propósito, pra não haver deriva entre eles. Sem isso, o
# searchbox vem com a aparência padrão do react-select: mais alto, cantos
# menos arredondados, sem borda visível em repouso e borda vermelha
# (cor "primária" padrão do Streamlit) quando focado/aberto — bem
# diferente do text_input nativo ao lado.
_ESTILO_CAMPO_LOJA = {
    "searchbox": {
        "control": {
            "minHeight": f"{config.ALTURA_CAMPO_LOJA_CONSULTOR_PX}px",
            "border": f"1px solid {config.BORDA_CAMPO_LOJA_CONSULTOR}",
            "borderRadius": f"{config.RAIO_CAMPO_LOJA_CONSULTOR_PX}px",
            "backgroundColor": config.FUNDO_CAMPO_LOJA_CONSULTOR,
            "boxShadow": "none",
            "&:hover": {"border": f"1px solid {config.BORDA_CAMPO_LOJA_CONSULTOR}"},
        },
        "singleValue": {
            "color": config.TEXTO_CAMPO_LOJA_CONSULTOR,
            "fontSize": f"{config.FONTE_CAMPO_LOJA_CONSULTOR_PX}px",
        },
        "input": {
            "color": config.TEXTO_CAMPO_LOJA_CONSULTOR,
            "fontSize": f"{config.FONTE_CAMPO_LOJA_CONSULTOR_PX}px",
        },
        "placeholder": {
            "color": config.PLACEHOLDER_CAMPO_LOJA_CONSULTOR,
            "fontSize": f"{config.FONTE_CAMPO_LOJA_CONSULTOR_PX}px",
        },
        "menuList": {"backgroundColor": config.FUNDO_CAMPO_LOJA_CONSULTOR},
    },
    # Ícones (limpar / abrir) na mesma cor cinza discreta usada em outros
    # textos secundários do app, em vez das cores padrão do react-select.
    "clear": {"icon": "cross", "fill": config.PLACEHOLDER_CAMPO_LOJA_CONSULTOR, "stroke": config.PLACEHOLDER_CAMPO_LOJA_CONSULTOR},
    "dropdown": {"fill": config.PLACEHOLDER_CAMPO_LOJA_CONSULTOR},
}


# ---------------------------------------------------------------------------
# Página: Upload
# ---------------------------------------------------------------------------

def pagina_upload() -> None:
    styles.cabecalho("Envio de arquivos")

    st.markdown("#### Envio de arquivos por loja")
    st.caption(
        "Faça o Upload dos arquivos nos campos indicados."
    )

    # ---- Lista oficial de lojas (API do TI) ----
    # Busca cacheada (ver _carregar_lojas_api). Se a API falhar por
    # qualquer motivo (secrets não configurados, rede, chave inválida,
    # formato de resposta inesperado), cai de volta para texto livre em
    # vez de travar a página inteira — o upload continua funcionando,
    # só sem a busca/autopreenchimento.
    try:
        lojas_disponiveis = _carregar_lojas_api()
        erro_lojas_api = None
    except lojas_api.LojasAPIError as e:
        lojas_disponiveis = []
        erro_lojas_api = str(e)

    if erro_lojas_api:
        st.warning(
            f"Não foi possível carregar a lista oficial de lojas (API do TI): "
            f"{erro_lojas_api}. Digite o código da loja manualmente."
        )

    mapa_lojas_por_codigo = {loja.codigo: loja for loja in lojas_disponiveis}

    def _rotulo_loja(codigo):
        loja_obj = mapa_lojas_por_codigo.get(codigo)
        if loja_obj is None:
            return codigo
        if loja_obj.nome:
            return f"{codigo} — {loja_obj.nome}"
        return codigo

    def _filtrar_lojas_por_prefixo(texto_busca: str, limite: int = 30):
        """Filtra códigos de loja que COMEÇAM com o texto digitado (não
        "contém", diferente da busca embutida de um selectbox comum) e
        ordena em ordem alfabética simples — para códigos numéricos em
        string, isso já produz a sequência esperada (prefixo mais curto
        primeiro): "1", "10", "11", "111", "1111", ... Retorna a lista
        limitada a `limite` itens e o total real de correspondências."""
        texto_busca = texto_busca.strip()
        if not texto_busca:
            return [], 0
        correspondentes = sorted(c for c in mapa_lojas_por_codigo if c.startswith(texto_busca))
        return correspondentes[:limite], len(correspondentes)

    def _buscar_lojas_searchbox(texto_busca: str):
        """search_function do st_searchbox — devolve pares (rótulo, valor)
        na ordem de prefixo definida acima. Se nada bater com o texto
        digitado, oferece o próprio texto como opção (mesmo comportamento
        de fallback que a busca antiga: loja recém-inativada ou cadastro
        desatualizado não deve travar o envio)."""
        texto_busca = texto_busca.strip()
        if not texto_busca:
            return []
        codigos_filtrados, _total = _filtrar_lojas_por_prefixo(texto_busca)
        if not codigos_filtrados:
            return [(f"{texto_busca}  (fora da lista oficial — usar mesmo assim)", texto_busca)]
        return [(_rotulo_loja(c), c) for c in codigos_filtrados]

    with st.container(border=True):
        st.markdown('<span class="mdf-painel-form-marker"></span>', unsafe_allow_html=True)

        # Pré-preenchimento vindo do botão "Upload" de uma loja (ver
        # _abrir_upload) — lido UMA VEZ aqui
        # em cima (fora do if/else de qual componente de Loja é usado),
        # porque tanto o campo Loja (dentro de c_loja) quanto o
        # pré-preenchimento do Consultor (dentro de c_consultor, mais
        # abaixo) precisam dele, e o ramo "erro_lojas_api" (fallback sem a
        # API do TI) nunca define essa variável sozinho.
        loja_prefill = st.session_state.pop("_upload_loja_prefill", None)

        # Reservado aqui para a linha de destino ficar no topo do painel; só é
        # preenchido depois do campo Loja, de onde vem a loja.
        area_destino = st.container()

        c_loja, c_consultor = st.columns(2)
        with c_loja:
            if erro_lojas_api:
                if loja_prefill:
                    st.session_state["upload_loja"] = loja_prefill
                upload_loja = st.text_input("Loja", key="upload_loja")
            else:
                # Componente externo streamlit-searchbox: visualmente é uma
                # seleção normal (um campo só, com dropdown), mas mantém a
                # busca por prefixo definida em _filtrar_lojas_por_prefixo
                # (exatamente o comportamento pedido: "1", "10", "11",
                # "111", ... e não "22" aparecendo ao digitar "1").
                if loja_prefill:
                    # Força o componente a recriar do zero com o valor
                    # pré-preenchido — descartar o session_state antigo
                    # do widget é necessário porque st_searchbox só lê
                    # default/default_searchterm/default_options na primeira
                    # vez que a key aparece no session_state.
                    st.session_state.pop("upload_loja_searchbox", None)
                # Rótulo "Loja" desenhado por FORA do componente (em vez de
                # usar o parâmetro label do st_searchbox) — o rótulo interno
                # do componente tem seu próprio espaçamento embutido (fixo,
                # não configurável via style_overrides) até a caixa de
                # busca, diferente do espaçamento que o Streamlit usa entre
                # o rótulo e a caixa do Consultor nativo ao lado. Este
                # <label> replica EXATAMENTE a métrica que o Streamlit usa
                # para o rótulo de qualquer widget nativo (fonte 0.875rem,
                # cor #31333F = tema claro "bodyText", min-height 1.5rem,
                # margin-bottom 0.25rem — valores extraídos do bundle do
                # Streamlit, não estimados), pra a caixa da Loja nascer na
                # mesma altura da caixa do Consultor. A regra CSS que
                # cancela o espaçamento padrão entre elementos (que o
                # Streamlit insere entre este rótulo e o componente
                # seguinte) está em styles.py.
                st.markdown(
                    '<label class="mdf-campo-loja-label"><span>Loja</span></label>',
                    unsafe_allow_html=True,
                )
                upload_loja = st_searchbox(
                    _buscar_lojas_searchbox,
                    key="upload_loja_searchbox",
                    label=None,
                    placeholder="Digite o código da loja...",
                    # `default` é o valor devolvido enquanto nada for clicado:
                    # sem ele, a loja aparecia no campo mas o envio recusava
                    # com "Preencha o código da loja".
                    default=loja_prefill or "",
                    default_searchterm=loja_prefill or "",
                    default_options=(
                        [(_rotulo_loja(loja_prefill), loja_prefill)] if loja_prefill else None
                    ),
                    clear_on_submit=False,
                    style_overrides=_ESTILO_CAMPO_LOJA,
                )
                if not upload_loja:
                    upload_loja = ""
        with c_consultor:
            # Pré-preenche o Consultor a partir da loja escolhida ao lado —
            # escrito em session_state ANTES deste widget ser instanciado
            # (Loja agora vem antes de Consultor na tela, então dá pra
            # calcular isso na hora, sem o truque de ler session_state de
            # antemão que era necessário quando a ordem era invertida).
            #
            # Dois caminhos, tratados separadamente de propósito:
            #
            #   1. loja_prefill (vindo do botão "Upload" de uma loja):
            #      preenche sempre, mesmo
            #      que a loja seja a mesma da última visita, porque o
            #      Streamlit descarta o valor do campo ao sair da página.
            #
            #   2. upload_loja mudou (usuário selecionou outra loja): só
            #      reage quando há loja (truthy). Limpar o campo não apaga
            #      o Consultor.
            #
            # Em ambos os casos, o valor manual que o consultor tiver digitado
            # depois de a loja já estar confirmada continua preservado (ex.:
            # cobertura de férias, upload feito por quem não é a consultora
            # oficial) — só reage a uma MUDANÇA de loja, nunca sobrescreve
            # uma edição livre no mesmo ciclo.
            if loja_prefill:
                loja_obj_pendente = mapa_lojas_por_codigo.get(loja_prefill)
                st.session_state["upload_consultor"] = (
                    (loja_obj_pendente.consultor if loja_obj_pendente else None) or ""
                )
                st.session_state["_ultima_loja_upload"] = loja_prefill
            elif upload_loja and upload_loja != st.session_state.get("_ultima_loja_upload"):
                st.session_state["_ultima_loja_upload"] = upload_loja
                loja_obj_pendente = mapa_lojas_por_codigo.get(upload_loja)
                st.session_state["upload_consultor"] = (
                    (loja_obj_pendente.consultor if loja_obj_pendente else None) or ""
                )
                # Contexto de mês (ver _upload_ano_mes_contexto abaixo) só
                # vale enquanto a loja continuar sendo aquela pra qual o
                # botão "Upload" de uma loja foi clicado — se o
                # usuário trocar pra outra loja aqui na tela (uma seleção
                # CONFIRMADA diferente da que veio pré-preenchida), o
                # contexto não faz mais sentido (era de OUTRA loja) e cai
                # pro comportamento padrão (mês atual, sem ícones).
                if upload_loja != st.session_state.get("_upload_ano_mes_contexto_loja"):
                    st.session_state["_upload_ano_mes_contexto"] = None
                    st.session_state["_upload_ano_mes_contexto_loja"] = None
            st.markdown('<span class="mdf-campo-consultor-marker"></span>', unsafe_allow_html=True)
            upload_consultor = st.text_input("Consultor", key="upload_consultor")

        st.markdown("&nbsp;", unsafe_allow_html=True)

        # Mês de destino: o que veio da tela de origem (barra de meses, cartão
        # de um mês) ou o atual. Se a loja já tem arquivos nesse mês, cada campo
        # mostra ✅ (já tem) ou ⚠️ (falta), e Retrato e Estoque atualizado ficam
        # disponíveis.
        mes_de_hoje = selecao_loja.mes_atual()
        loja_destino = upload_loja.strip()
        ano_mes_destino = selecao_loja.mes_de_destino(
            loja_destino,
            st.session_state.get("_upload_ano_mes_contexto"),
            st.session_state.get("_upload_ano_mes_contexto_loja"),
            mes_de_hoje,
        )
        with area_destino:
            texto_destino = selecao_loja.texto_de_destino(
                loja_destino, ano_mes_destino, mes_de_hoje, styles.mes_legivel
            )
            if texto_destino:
                st.markdown(texto_destino)

        arquivos_existentes_destino = None
        if loja_destino:
            try:
                inventario_destino = _descobrir_inventario_cache(storage, st.session_state["versao_cache"])
            except StorageError:
                # Sem o inventário o envio ainda funciona: só faltam os ícones.
                inventario_destino = {}
            ciclo_destino = inventario_destino.get(loja_destino, {}).get(ano_mes_destino)
            if ciclo_destino:
                arquivos_existentes_destino = ciclo_destino.get("arquivos", {})

        chaves_arquivos = selecao_loja.arquivos_do_upload(
            list(_ROTULOS_ARQUIVOS_UPLOAD), arquivos_existentes_destino is not None
        )
        col_esq, col_dir = st.columns(2)
        colunas_alternadas = [col_esq if i % 2 == 0 else col_dir for i in range(len(chaves_arquivos))]
        arquivos_selecionados = {}
        for chave, coluna in zip(chaves_arquivos, colunas_alternadas):
            with coluna:
                extensoes_aceitas = [ext.lstrip(".") for ext in config.FILE_SPECS[chave]["extensions"]]
                rotulo_arquivo = _ROTULOS_ARQUIVOS_UPLOAD[chave]
                if arquivos_existentes_destino is not None:
                    rotulo_arquivo = (
                        f"✅ {rotulo_arquivo}" if chave in arquivos_existentes_destino
                        else f"⚠️ {rotulo_arquivo}"
                    )
                arquivos_selecionados[chave] = st.file_uploader(
                    rotulo_arquivo,
                    type=extensoes_aceitas,
                    key=f"upload_arquivo_{chave}",
                )

        # "_upload_reenviar": o pop-up do mês do Retrato (ver
        # _dialog_mes_retrato) pede o mesmo envio de novo depois de
        # confirmado — os arquivos continuam selecionados nos campos.
        enviar = st.button("Enviar arquivos", type="primary", key="upload_botao_enviar")
        if st.session_state.pop("_upload_reenviar", False):
            enviar = True
        if enviar:
            arquivos_preenchidos = {
                chave: arquivo for chave, arquivo in arquivos_selecionados.items() if arquivo is not None
            }
            retrato_periodo = None
            if not loja_destino:
                st.warning("Preencha o código da loja antes de enviar.")
            elif not arquivos_preenchidos:
                st.warning("Selecione ao menos um arquivo antes de enviar.")
            elif "retrato_vendas" in arquivos_preenchidos and (
                retrato_periodo := _periodo_retrato_para_envio(arquivos_preenchidos["retrato_vendas"].getvalue())
            ) is None:
                # Retrato sem período no arquivo e sem mês informado: NADA
                # do lote é gravado agora — o pop-up abaixo pergunta o mês
                # e reenvia tudo sozinho.
                st.session_state["_upload_pedir_mes_retrato"] = True
            else:
                ano_mes = ano_mes_destino
                loja_limpa = loja_destino

                falhas = []
                colisoes_pendentes = []

                # Validação específica do Mapa da Farmácia, feita aqui no
                # momento do envio (antes de gravar qualquer coisa). Coluna
                # obrigatória ausente (módulo/EAN/produto) BLOQUEIA só este
                # arquivo — ele sai de `arquivos_preenchidos` e vira uma
                # falha, os demais arquivos do lote seguem normalmente.
                # Colisão de (módulo, posição) NÃO bloqueia o envio: o
                # arquivo é salvo do mesmo jeito, só guardamos a lista pra
                # mostrar o pop-up de aviso mais abaixo.
                if "mapa_farmacia" in arquivos_preenchidos:
                    try:
                        colisoes_pendentes = _validar_mapa_farmacia_upload(
                            arquivos_preenchidos["mapa_farmacia"].getvalue()
                        )
                    except dl.PlanilhaInvalidaError as e:
                        falhas.append(f"{_ROTULOS_ARQUIVOS_UPLOAD['mapa_farmacia']}: {e}")
                        del arquivos_preenchidos["mapa_farmacia"]

                # Verifica CADA arquivo individualmente antes de gravar —
                # os que não têm conflito são salvos direto; os que já
                # existem ficam pendentes de confirmação (ver bloco
                # abaixo, fora deste `if button`, pra sobreviver aos
                # reruns dos cliques em "Sim"/"Não").
                conflitos = {}
                sem_conflito = {}
                caminho_ciclo_upload = f"{loja_limpa}/{ano_mes}"
                try:
                    itens_ciclo_upload = storage.listar_pasta(caminho_ciclo_upload)
                except ArquivoNaoEncontradoError:
                    itens_ciclo_upload = []
                for chave, arquivo in arquivos_preenchidos.items():
                    nome_base = config.FILE_SPECS[chave]["basenames"][0]
                    extensao = Path(arquivo.name).suffix.lower()
                    caminho_destino = f"{caminho_ciclo_upload}/{nome_base}{extensao}"
                    conteudo = arquivo.getvalue()
                    # Conflito = já existe um arquivo desta chave em
                    # QUALQUER extensão aceita (não só na extensão do
                    # arquivo enviado agora) — senão reenviar "foto_antes"
                    # com extensão diferente da anterior (a câmera do
                    # celular decide o formato, não o consultor) não detecta
                    # conflito nenhum: os dois arquivos ficam coexistindo no
                    # storage, e como localizar_arquivo() sempre resolve pra
                    # extensão de maior prioridade, a "substituição" nunca
                    # aparece nas telas seguintes (Conferência etc.).
                    item_existente = localizar_item(
                        storage, caminho_ciclo_upload, config.FILE_SPECS[chave], itens=itens_ciclo_upload
                    )
                    if item_existente is not None:
                        conflitos[chave] = {
                            "caminho": caminho_destino,
                            "conteudo": conteudo,
                            "caminho_antigo": item_existente.caminho_completo,
                        }
                    else:
                        sem_conflito[chave] = {"caminho": caminho_destino, "conteudo": conteudo}

                enviados_ok = []
                for chave, info in sem_conflito.items():
                    try:
                        storage.escrever_arquivo_bytes(info["caminho"], info["conteudo"])
                        enviados_ok.append(info["caminho"])
                    except StorageError as e:
                        falhas.append(f"{_ROTULOS_ARQUIVOS_UPLOAD[chave]}: {e}")
                        continue

                    if chave == "modelo":
                        try:
                            extensao = Path(info["caminho"]).suffix.lower()
                            bytes_tela, bytes_impressao = image_utils.gerar_previews_modelo(info["conteudo"], extensao)
                            storage.escrever_arquivo_bytes(f"{loja_limpa}/{ano_mes}/modelo_tela.png", bytes_tela)
                            storage.escrever_arquivo_bytes(f"{loja_limpa}/{ano_mes}/modelo_impressao.png", bytes_impressao)
                        except Exception as e:
                            falhas.append(
                                f"Pré-visualização do Modelo: não foi possível gerar ({e}) — "
                                "o arquivo original foi salvo normalmente, mas a tela pode ficar lenta até isso ser corrigido."
                            )

                if enviados_ok:
                    retrato_enviado_agora = any(
                        chave == "retrato_vendas" and info["caminho"] in enviados_ok
                        for chave, info in sem_conflito.items()
                    )
                    try:
                        _atualizar_metadata_ciclo(
                            storage, loja_limpa, ano_mes, upload_consultor.strip(),
                            retrato_periodo=retrato_periodo if retrato_enviado_agora else None,
                        )
                    except StorageError as e:
                        falhas.append(f"metadata.json: {e}")

                # Aviso (não bloqueia) quando o Retrato não cobre um mês
                # inteiro: o "antes" do Resultado da ação é uma média
                # MENSAL, então um período menor ou maior distorce a
                # comparação.
                avisos = []
                if retrato_periodo:
                    inicio = date.fromisoformat(retrato_periodo["inicio"])
                    fim = date.fromisoformat(retrato_periodo["fim"])
                    if not _periodo_e_mes_inteiro(inicio, fim):
                        avisos.append(
                            f"O Retrato de Vendas cobre de {inicio:%d/%m/%Y} a {fim:%d/%m/%Y}, que não é "
                            "um mês completo. O Resultado da ação compara com a média MENSAL de "
                            "vendas anterior, então o resultado pode ficar distorcido — o ideal é "
                            "exportar o Retrato de um mês inteiro."
                        )

                st.session_state["_upload_avisos"] = avisos
                # Período guardado pro caso de o Retrato cair na
                # confirmação de sobrescrita ("Sim, substituir") abaixo.
                st.session_state["_upload_retrato_periodo_pendente"] = retrato_periodo
                st.session_state["_upload_enviados_ok"] = enviados_ok
                st.session_state["_upload_falhas"] = falhas
                st.session_state["_upload_conflitos"] = conflitos
                st.session_state["_upload_ciclo_pendente"] = (loja_limpa, ano_mes)
                st.session_state["_upload_consultor_pendente"] = upload_consultor.strip()
                if colisoes_pendentes:
                    st.session_state["_upload_colisoes_pendentes"] = colisoes_pendentes
                if enviados_ok:
                    # Refaz a tela com o inventário novo: atualiza os ícones e,
                    # no primeiro envio do mês, mostra Retrato e Estoque
                    # atualizado. O resultado fica guardado acima.
                    st.session_state["versao_cache"] += 1
                    st.rerun()

    if st.session_state.get("_upload_enviados_ok"):
        lista_html = "\n".join(f"- `{c}`" for c in st.session_state["_upload_enviados_ok"])
        st.success(f"Arquivo(s) enviado(s) com sucesso:\n{lista_html}")
    if st.session_state.get("_upload_falhas"):
        lista_falhas = "\n".join(f"- {f}" for f in st.session_state["_upload_falhas"])
        st.error(f"Falha ao enviar:\n{lista_falhas}")
    for aviso in st.session_state.get("_upload_avisos") or []:
        st.warning(aviso)

    # Retrato sem período no arquivo: pergunta o mês (nada foi gravado).
    if st.session_state.get("_upload_pedir_mes_retrato"):
        _dialog_mes_retrato()

    # Aviso didático (não-bloqueante — o arquivo já foi salvo) sobre
    # colisões de (módulo, posição) no Mapa da Farmácia recém-enviado. Ver
    # `_dialog_colisoes_modulo_posicao`: some sozinho ao clicar "Entendi".
    if st.session_state.get("_upload_colisoes_pendentes"):
        _dialog_colisoes_modulo_posicao(st.session_state["_upload_colisoes_pendentes"])

    # ---- Confirmação de sobrescrita, por arquivo individual ----
    conflitos_pendentes = st.session_state.get("_upload_conflitos") or {}
    if conflitos_pendentes:
        loja_pend, ano_mes_pend = st.session_state["_upload_ciclo_pendente"]
        for chave in list(conflitos_pendentes.keys()):
            info = conflitos_pendentes[chave]
            st.warning(
                f"Já existe um arquivo enviado para **{_ROTULOS_ARQUIVOS_UPLOAD[chave]}** em "
                f"`{loja_pend}/{ano_mes_pend}`. Deseja substituir?"
            )
            c1, c2 = st.columns(2)
            if c1.button("Sim, substituir", key=f"upload_conflito_sim_{chave}"):
                try:
                    storage.escrever_arquivo_bytes(info["caminho"], info["conteudo"])
                    caminho_antigo = info.get("caminho_antigo")
                    if caminho_antigo and caminho_antigo != info["caminho"]:
                        # A extensão mudou entre o arquivo antigo e o novo
                        # (ex.: foto_antes.jpg -> foto_antes.png) — sem
                        # apagar o antigo, os dois ficam coexistindo e
                        # localizar_arquivo() sempre volta pro mais antigo
                        # primeiro (prioridade de extensão), fazendo a
                        # "substituição" nunca aparecer na tela.
                        try:
                            storage.apagar_arquivo(caminho_antigo)
                        except StorageError as e:
                            st.warning(
                                f"{_ROTULOS_ARQUIVOS_UPLOAD[chave]} foi substituído, mas não foi "
                                f"possível remover o arquivo antigo ({caminho_antigo}): {e}"
                            )
                    _atualizar_metadata_ciclo(
                        storage, loja_pend, ano_mes_pend,
                        st.session_state.get("_upload_consultor_pendente", ""),
                        retrato_periodo=(
                            st.session_state.get("_upload_retrato_periodo_pendente")
                            if chave == "retrato_vendas" else None
                        ),
                    )
                    st.toast(f"{_ROTULOS_ARQUIVOS_UPLOAD[chave]} substituído.", icon="✅")

                    if chave == "modelo":
                        try:
                            extensao = Path(info["caminho"]).suffix.lower()
                            bytes_tela, bytes_impressao = image_utils.gerar_previews_modelo(info["conteudo"], extensao)
                            storage.escrever_arquivo_bytes(f"{loja_pend}/{ano_mes_pend}/modelo_tela.png", bytes_tela)
                            storage.escrever_arquivo_bytes(f"{loja_pend}/{ano_mes_pend}/modelo_impressao.png", bytes_impressao)
                        except Exception as e:
                            st.error(
                                f"Pré-visualização do Modelo: não foi possível gerar ({e}) — "
                                "o arquivo original foi salvo normalmente, mas a tela pode ficar lenta até isso ser corrigido."
                            )
                except StorageError as e:
                    st.error(f"Falha ao substituir {_ROTULOS_ARQUIVOS_UPLOAD[chave]}: {e}")
                del st.session_state["_upload_conflitos"][chave]
                st.rerun()
            if c2.button("Não", key=f"upload_conflito_nao_{chave}"):
                del st.session_state["_upload_conflitos"][chave]
                st.rerun()

    # Seção recolhida por padrão (só um item discreto, sem título em
    # destaque nem divider) — o conteúdo por dentro é o mesmo de sempre
    # (explicação + formulário de envio), só o ponto de entrada mudou pra
    # não competir visualmente com o envio por loja, que é o uso do dia a
    # dia desta página.
    with st.expander("Base nacional de demanda", expanded=False):
        st.caption(
            "Base compartilhada de demanda de mercado, usada por todas as lojas "
            "(diferente da seção acima, que é por loja). Enviar a planilha bruta "
            "('.xlsx'/'.xls', como ela sai do sistema de origem) já converte "
            "automaticamente para '.parquet' — formato bem mais rápido de "
            "carregar — aplicando os mesmos filtros de "
            "scripts/preparar_base_nacional.py (remove categorias RX_ e "
            "produtos de baixa demanda, ver README). Enviar um '.parquet' já "
            "pronto (gerado localmente) também funciona, sem reprocessar."
        )
        arquivo_base_nacional = st.file_uploader(
            "Arquivo da base nacional (.xlsx, .xls ou .parquet)",
            type=["xlsx", "xls", "parquet"],
            key="upload_base_nacional",
        )
        if st.button("Enviar base nacional", type="primary", key="upload_botao_base"):
            if arquivo_base_nacional is None:
                st.warning("Selecione um arquivo antes de enviar.")
            else:
                extensao = Path(arquivo_base_nacional.name).suffix.lower()
                nome_base = config.BASE_NACIONAL_SPEC["basenames"][0]
                try:
                    if extensao == ".parquet":
                        caminho_destino = f"{config.BASE_NACIONAL_FOLDER_NAME}/{nome_base}.parquet"
                        storage.escrever_arquivo_bytes(caminho_destino, arquivo_base_nacional.getvalue())
                        st.toast(f"Base nacional enviada: `{caminho_destino}`", icon="✅")
                    else:
                        # Arquivo bruto (.xlsx/.xls) -- converte automaticamente
                        # antes de salvar, em vez de subir do jeito que veio (o
                        # app sempre lê essa versão bruta pelo caminho lento,
                        # ver modules.data_loader.carregar_base_nacional).
                        bruta = dl.carregar_base_nacional_bruta(
                            io.BytesIO(arquivo_base_nacional.getvalue())
                        )
                        tratada, resumo = dl.tratar_base_nacional(bruta)
                        buffer_parquet = io.BytesIO()
                        tratada.to_parquet(buffer_parquet, index=False)
                        caminho_destino = f"{config.BASE_NACIONAL_FOLDER_NAME}/{nome_base}.parquet"
                        storage.escrever_arquivo_bytes(caminho_destino, buffer_parquet.getvalue())
                        st.toast(
                            f"Base nacional convertida e enviada: "
                            f"{resumo.total_original:,} → {resumo.total_final:,} produtos "
                            f"({resumo.removidos_categoria:,} por categoria, "
                            f"{resumo.removidos_demanda:,} por demanda baixa)."
                            .replace(",", "."),
                            icon="✅",
                        )
                except dl.PlanilhaInvalidaError as e:
                    st.error(f"Não foi possível processar o arquivo: {e}")
                except StorageError as e:
                    st.error(f"Falha ao enviar: {e}")


# ---------------------------------------------------------------------------
# Página: Selecionar Loja
# ---------------------------------------------------------------------------

def _selecionar_ciclo(loja: str, mes: str, info_ciclo: dict) -> None:
    st.session_state["loja_atual_analise"] = loja
    st.session_state["ciclo_atual_analise"] = f"{loja}/{mes}"
    st.session_state["metadata_atual_analise"] = info_ciclo["metadata"]
    st.session_state["mes_selecionado"] = mes
    # Marca esta entrada na Análise como uma "nova visita" pro aviso de Modelo
    # ausente (aba_gc): sem isso o aviso, uma vez fechado, nunca mais
    # reapareceria pra este ciclo mesmo que o consultor saia e volte.
    st.session_state.pop("_gc_modelo_aviso_ciclo_visto", None)


def _abrir_analise(loja: str, mes: str, info_ciclo: dict) -> None:
    _selecionar_ciclo(loja, mes, info_ciclo)
    st.session_state.pop("analise_arquivos_abertos", None)
    st.switch_page(_pagina_analise_para_retomar())


def _escolher_mes(mes: str) -> None:
    st.session_state["mes_selecionado"] = mes
    st.session_state["busca_loja"] = ""
    st.session_state.pop("w_busca_loja", None)


def _alternar_filtros() -> None:
    st.session_state["filtros_lojas_abertos"] = not st.session_state.get("filtros_lojas_abertos", False)


def _campo_guardado(chave: str, padrao: str, opcoes: list[str] | None = None) -> str:
    """Prepara um campo cujo valor sobrevive à saída da página ou ao painel
    fechado (o Streamlit apaga o valor de widget que não foi desenhado). O valor
    fica em `chave`, e o widget usa `w_{chave}`. Com `opcoes`, valor que deixou
    de existir (ex.: consultor sem lojas no novo mês) volta ao padrão."""
    valor = st.session_state.get(f"w_{chave}", st.session_state.get(chave, padrao))
    if opcoes is not None and valor not in opcoes:
        valor = padrao
    st.session_state[chave] = valor
    st.session_state[f"w_{chave}"] = valor
    return valor


def _barra_de_meses(barra: list, mes_selecionado: str) -> None:
    with st.container(key="mdf_barra_meses", horizontal=True, vertical_alignment="center", gap="small"):
        st.markdown('<p class="mdf-barra-mes-rotulo">Mês</p>', unsafe_allow_html=True, width="content")
        for item in barra:
            st.button(
                selecao_loja.rotulo_mes_da_barra(item),
                key=f"barra_mes_{item.mes}",
                type="primary" if item.mes == mes_selecionado else "secondary",
                on_click=_escolher_mes,
                args=(item.mes,),
            )


def _linha_da_lista(linha: selecao_loja.LinhaLoja, mes: str, ciclos_do_mes: dict) -> None:
    with st.container(key=f"mdf_linha_loja_{linha.codigo}"):
        c_cod, c_razao, c_consultor, c_analise, c_upload = st.columns(
            [0.8, 2.6, 1.5, 1.4, 1], vertical_alignment="center"
        )
        for coluna, classe, valor in (
            (c_cod, "mdf-loja-cod", linha.codigo),
            (c_razao, "mdf-loja-razao", linha.razao_social),
            (c_consultor, "mdf-loja-consultor", linha.consultor),
        ):
            coluna.markdown(f'<p class="{classe}">{html.escape(valor)}</p>', unsafe_allow_html=True)
        if linha.tem_arquivos and c_analise.button(
            "Ir para análise", key=f"analise_loja_{linha.codigo}", width="stretch",
            icon=":material/arrow_forward:", icon_position="right",
        ):
            _abrir_analise(linha.codigo, mes, ciclos_do_mes[linha.codigo])
        if c_upload.button("Upload", key=f"upload_loja_{linha.codigo}", width="stretch", icon=":material/upload:"):
            _abrir_upload(linha.codigo, mes)


def _paginacao_lojas(pagina: selecao_loja.Pagina) -> None:
    if pagina.total_paginas <= 1:
        return

    def _ir_para(numero: int) -> None:
        st.session_state["pagina_lojas"] = numero

    c_faixa, c_anterior, c_pagina, c_proxima = st.columns([2.4, 1, 1.4, 1], vertical_alignment="center")
    c_faixa.markdown(
        f'<p class="mdf-lista-apoio">{pagina.inicio} a {pagina.fim} de {pagina.total} lojas</p>',
        unsafe_allow_html=True,
    )
    c_anterior.button(
        "Anterior", key="lojas_pagina_anterior", width="stretch", disabled=not pagina.tem_anterior,
        on_click=_ir_para, args=(pagina.pagina - 1,),
    )
    c_pagina.markdown(
        f'<p class="mdf-paginacao-info">Página {pagina.pagina} de {pagina.total_paginas}</p>',
        unsafe_allow_html=True,
    )
    c_proxima.button(
        "Próxima", key="lojas_pagina_proxima", width="stretch", disabled=not pagina.tem_proxima,
        on_click=_ir_para, args=(pagina.pagina + 1,),
    )


def pagina_selecionar_loja() -> None:
    styles.cabecalho("Selecionar loja")

    try:
        inventario_atual = _descobrir_inventario_cache(storage, st.session_state["versao_cache"])
    except StorageError as e:
        st.error(f"Não foi possível carregar a lista de lojas. Tente de novo em instantes. ({e})")
        return

    # Sem a API do TI a lista continua funcionando, só com o código da loja.
    try:
        cadastro = _carregar_cadastro_lojas()
    except lojas_api.LojasAPIError as e:
        cadastro = {}
        st.warning(
            "Não foi possível carregar a lista oficial de lojas (API do TI). A lista mostra só "
            f"o código, e a busca e os filtros ficam limitados: {e}."
        )

    mes_de_hoje = selecao_loja.mes_atual()
    barra = selecao_loja.meses_da_barra(inventario_atual, mes_de_hoje)
    mes = st.session_state.get("mes_selecionado", mes_de_hoje)
    if mes not in {item.mes for item in barra}:
        mes = mes_de_hoje
    st.session_state["mes_selecionado"] = mes

    _barra_de_meses(barra, mes)

    _campo_guardado("busca_loja", "")
    texto_busca = st.text_input("Buscar loja", key="w_busca_loja", placeholder="Código ou razão social")
    st.session_state["busca_loja"] = texto_busca

    consultores, supervisoras = selecao_loja.opcoes_de_filtro(inventario_atual, mes, cadastro)
    opcoes_consultor = ["Todos"] + consultores
    opcoes_supervisora = ["Todos"] + supervisoras
    filtro_consultor = _campo_guardado("filtro_consultor", "Todos", opcoes_consultor)
    filtro_supervisora = _campo_guardado("filtro_supervisora", "Todos", opcoes_supervisora)

    c_contagem, c_filtros = st.columns([0.55, 0.45], vertical_alignment="center")
    if consultores or supervisoras:
        filtros_abertos = st.session_state.get("filtros_lojas_abertos", False)
        filtro_ativo = filtro_consultor != "Todos" or filtro_supervisora != "Todos"
        rotulo_filtros = "Esconder filtros" if filtros_abertos else "Filtrar por consultor ou supervisora"
        with c_filtros, st.container(horizontal=True, horizontal_alignment="right"):
            st.button(
                f"{rotulo_filtros} (ativo)" if filtro_ativo else rotulo_filtros,
                key="lojas_alternar_filtros", type="tertiary", on_click=_alternar_filtros,
            )
        if filtros_abertos:
            c_consultor, c_supervisora = st.columns(2)
            filtro_consultor = c_consultor.selectbox("Consultor", opcoes_consultor, key="w_filtro_consultor")
            filtro_supervisora = c_supervisora.selectbox("Supervisora", opcoes_supervisora, key="w_filtro_supervisora")
            st.session_state["filtro_consultor"] = filtro_consultor
            st.session_state["filtro_supervisora"] = filtro_supervisora

    linhas = selecao_loja.lojas_da_lista(
        inventario_atual, mes, texto_busca, cadastro,
        consultor=None if filtro_consultor == "Todos" else filtro_consultor,
        supervisora=None if filtro_supervisora == "Todos" else filtro_supervisora,
    )
    buscando = bool(texto_busca.strip())
    if buscando:
        contagem = f"{selecao_loja.rotulo_lojas(len(linhas))} encontrada{'' if len(linhas) == 1 else 's'}"
    else:
        contagem = f"{selecao_loja.rotulo_lojas(len(linhas))} com arquivos em {selecao_loja.mes_curto(mes)}"
    tem_arquivos_no_mes = any(item.mes == mes and item.lojas for item in barra)
    if tem_arquivos_no_mes or buscando:
        c_contagem.markdown(f'<p class="mdf-lista-apoio">{contagem}</p>', unsafe_allow_html=True)
    else:
        st.info(f"Nenhuma loja enviou arquivos em {styles.mes_legivel(mes)}.")
        anterior = selecao_loja.mes_anterior_com_arquivos(barra, mes)
        if anterior:
            st.button(
                f"Ver {styles.mes_legivel(anterior)}", key="lojas_mes_anterior", type="primary",
                on_click=_escolher_mes, args=(anterior,),
            )
        else:
            st.markdown("Use a página **Upload** para enviar os primeiros arquivos.")
        return

    numero_pagina, _ = _pagina_atual_paginacao(
        "pagina_lojas", len(linhas), selecao_loja.LOJAS_POR_PAGINA,
        chave_reset=(mes, texto_busca.strip(), filtro_consultor, filtro_supervisora),
    )
    pagina = selecao_loja.paginar(linhas, numero_pagina)

    ciclos_do_mes = {codigo: ciclos[mes] for codigo, ciclos in inventario_atual.items() if mes in ciclos}
    with st.container(border=True, key="mdf_lista_lojas"):
        if not pagina.itens:
            st.markdown('<p class="mdf-lista-vazia">Nenhuma loja encontrada.</p>', unsafe_allow_html=True)
        for linha in pagina.itens:
            _linha_da_lista(linha, mes, ciclos_do_mes)

    _paginacao_lojas(pagina)
    if not buscando and cadastro:
        st.markdown(
            '<p class="mdf-lista-apoio">Loja que ainda não enviou neste mês: busque pelo código para fazer o Upload.</p>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Página: Análise (Ajuste de Mix / Sugestão de GC / Conferência) — 3
# st.Page separadas. _contexto_analise() carrega o que as 3 têm em comum
# (loja/ciclo selecionado, dados da loja, base nacional, tabela base) —
# EXATAMENTE a mesma sequência de chamadas/erros que rodava antes do
# if/elif de sub-abas no script monolítico, só que agora reexecutada
# dentro de QUALQUER UMA das 3 páginas que a chamar (cada chamada cai no
# cache de st.cache_data — não é trabalho refeito de verdade, ver
# decorators acima), em vez de rodar uma única vez só porque as 3 abas
# viviam no mesmo script. Isso preserva o mesmo comportamento de negócio
# (mesmos erros, mesma ordem de checagem, mesmos dados) em cada uma das 3
# páginas, que agora só existem de fato quando o Streamlit navega pra elas.
# ---------------------------------------------------------------------------

def _trocar_loja_analise(inventario_atual: dict, mes: str) -> None:
    loja = st.session_state["w_analise_loja"]
    info_ciclo = inventario_atual.get(loja, {}).get(mes)
    if info_ciclo:
        _selecionar_ciclo(loja, mes, info_ciclo)
        st.session_state.pop("analise_arquivos_abertos", None)


def _trocar_mes_analise(inventario_atual: dict, loja: str) -> None:
    mes = st.session_state["w_analise_mes"]
    info_ciclo = inventario_atual.get(loja, {}).get(mes)
    if info_ciclo:
        _selecionar_ciclo(loja, mes, info_ciclo)


def _alternar_arquivos_do_mes() -> None:
    st.session_state["analise_arquivos_abertos"] = not st.session_state.get("analise_arquivos_abertos", False)


def _painel_arquivos_do_mes(loja: str, mes: str, situacao: list[selecao_loja.ArquivoDoMes]) -> None:
    with st.container(border=True, key="mdf_arquivos_do_mes"):
        colunas = st.columns(3)
        for indice, arquivo in enumerate(situacao):
            classe, icone = (
                ("mdf-check-ok", styles.icone_check_svg()) if arquivo.presente
                else ("mdf-check-falta", styles.icone_x_svg())
            )
            colunas[indice % 3].markdown(f'<p class="{classe}">{icone} {arquivo.rotulo}</p>', unsafe_allow_html=True)
        if st.button("Enviar arquivos deste mês", key="analise_enviar_arquivos", type="primary"):
            _abrir_upload(loja, mes)


def _barra_contexto_analise(inventario_atual: dict, loja: str, mes: str) -> None:
    """Trocar de loja no mês aberto, trocar de mês e ver os arquivos do mês. Os
    campos recebem a loja e o mês abertos a cada execução: a seleção pode ter
    mudado em outra página, e o Streamlit não guarda valor de widget entre elas."""
    try:
        cadastro = _carregar_cadastro_lojas()
    except lojas_api.LojasAPIError:
        cadastro = {}

    lojas = selecao_loja.lojas_do_mes(inventario_atual, mes)
    if loja not in lojas:
        lojas = [loja, *lojas]
    meses = selecao_loja.meses_da_loja(inventario_atual, loja)
    if mes not in meses:
        meses = [mes, *meses]
    st.session_state["w_analise_loja"] = loja
    st.session_state["w_analise_mes"] = mes

    arquivos = inventario_atual.get(loja, {}).get(mes, {}).get("arquivos", {})
    situacao = selecao_loja.arquivos_do_mes(_ROTULOS_ARQUIVOS_UPLOAD, arquivos)
    enviados = sum(arquivo.presente for arquivo in situacao)

    c_loja, c_mes, c_arquivos = st.columns([2.6, 1.2, 1.5], vertical_alignment="bottom")
    c_loja.selectbox(
        f"Trocar de loja em {selecao_loja.mes_curto(mes)}", lojas, key="w_analise_loja",
        format_func=lambda codigo: selecao_loja.rotulo_da_loja(codigo, cadastro),
        on_change=_trocar_loja_analise, args=(inventario_atual, mes),
    )
    c_mes.selectbox(
        "Mês", meses, key="w_analise_mes", format_func=styles.mes_legivel, disabled=len(meses) == 1,
        on_change=_trocar_mes_analise, args=(inventario_atual, loja),
    )
    c_arquivos.button(
        f"Arquivos do mês · {enviados} de {len(situacao)}", key="analise_alternar_arquivos", width="stretch",
        on_click=_alternar_arquivos_do_mes,
    )
    if st.session_state.get("analise_arquivos_abertos"):
        _painel_arquivos_do_mes(loja, mes, situacao)


def _contexto_analise():
    """Retorna None (e já desenha a tela "Nenhuma loja selecionada") se
    não há loja/ciclo escolhido, ou a tupla
    (loja, ciclo_selecionado, metadata_ciclo, consultor, mapa_df,
    estoque_df, tabela_base, erro_dados, subtitulo_cabecalho,
    tempo_inicio_pagina) com tudo que as 3 sub-páginas de Análise
    precisam."""
    loja = st.session_state.get("loja_atual_analise")
    ciclo_selecionado = st.session_state.get("ciclo_atual_analise")
    metadata_ciclo = st.session_state.get("metadata_atual_analise")

    if ciclo_selecionado is None:
        styles.cabecalho("Nenhuma loja selecionada")
        st.info("Nenhuma loja selecionada.")
        if st.button("Ir para Selecionar Loja"):
            st.switch_page(PAGINA_SELECIONAR_LOJA)
        return None

    _pag_t0 = time.perf_counter()  # >>> DIAGNOSTICO
    print(f"[PAGINA-TEMPO] INICIO pagina Analise, loja={loja!r} ciclo={ciclo_selecionado!r}", flush=True)  # >>> DIAGNOSTICO
    # Sem o inventário a análise continua abrindo: só faltam os controles do topo.
    mes_aberto = ciclo_selecionado.partition("/")[2]
    try:
        inventario_atual = _descobrir_inventario_cache(storage, st.session_state["versao_cache"])
    except StorageError:
        inventario_atual = None
    # O inventário tem o dado mais novo: um envio feito depois de abrir a loja
    # muda o consultor e a data do cabeçalho.
    info_ciclo = (inventario_atual or {}).get(loja, {}).get(mes_aberto)
    if info_ciclo:
        metadata_ciclo = info_ciclo["metadata"]
        st.session_state["metadata_atual_analise"] = metadata_ciclo

    subtitulo_cabecalho = styles.montar_subtitulo(loja, ciclo_selecionado, metadata_ciclo)
    styles.cabecalho(subtitulo_cabecalho)
    if inventario_atual is not None:
        _barra_contexto_analise(inventario_atual, loja, mes_aberto)
    consultor = (metadata_ciclo or {}).get("consultor") or ""

    # ---- Carregamento de dados do ciclo selecionado — lógica interna
    # idêntica à de antes; erros aparecem dentro das sub-páginas que
    # precisam desses dados (Ajuste de mix / Sugestão de GC), não
    # travam a página inteira (Conferência não depende deles). ----
    tabela_base = None
    mapa_df = None
    estoque_df = None
    erro_dados = None

    _pag_t1 = time.perf_counter()  # >>> DIAGNOSTICO
    try:
        mapa_df, estoque_df = _carregar_dados_loja(storage, ciclo_selecionado, _assinatura_ciclo(ciclo_selecionado))
    except ArquivoObrigatorioAusenteError as e:
        erro_dados = (
            f"Arquivo obrigatório **{e.chave}** não encontrado em `{e.pasta}`. "
            f"Sem esse arquivo, esta loja não pode ser processada — envie o arquivo "
            f"correspondente na página Upload e volte para Análise (os dados são "
            f"atualizados automaticamente)."
        )
    except dl.MultiplasLojasEstoqueError as e:
        erro_dados = (
            f"O arquivo de estoque contém mais de uma loja: **{', '.join(e.ids_encontrados)}**. "
            f"Verifique o arquivo enviado — ele deve conter dados de uma única loja."
        )
    except dl.PlanilhaInvalidaError as e:
        erro_dados = f"Não foi possível interpretar uma das planilhas: {e}"
    print(f"[PAGINA-TEMPO] carregar_dados_loja: {time.perf_counter() - _pag_t1:.2f}s | len(mapa_df)={len(mapa_df) if mapa_df is not None else None}", flush=True)  # >>> DIAGNOSTICO

    _pag_t2 = time.perf_counter()  # >>> DIAGNOSTICO
    base_df = None
    if erro_dados is None:
        try:
            base_df = _carregar_base_nacional(storage, _assinatura_base_nacional())
        except ArquivoObrigatorioAusenteError:
            erro_dados = (
                f"A base nacional de demanda não foi encontrada na pasta "
                f"`{config.BASE_NACIONAL_FOLDER_NAME}`. Sem ela, não é possível calcular "
                f"a demanda de mercado dos produtos."
            )
    print(f"[PAGINA-TEMPO] carregar_base_nacional: {time.perf_counter() - _pag_t2:.2f}s", flush=True)  # >>> DIAGNOSTICO

    _pag_t3 = time.perf_counter()  # >>> DIAGNOSTICO
    if erro_dados is None:
        tabela_base = dl.montar_tabela_ajuste_mix(mapa_df, estoque_df, base_df)
    print(f"[PAGINA-TEMPO] montar_tabela_ajuste_mix: {time.perf_counter() - _pag_t3:.2f}s | len(tabela_base)={len(tabela_base) if tabela_base is not None else None}", flush=True)  # >>> DIAGNOSTICO

    return (
        loja, ciclo_selecionado, metadata_ciclo, consultor,
        mapa_df, estoque_df, tabela_base, erro_dados,
        subtitulo_cabecalho, _pag_t0,
    )


def _pagina_analise_para_retomar():
    """Qual das 3 sub-páginas de Análise abrir quando a navegação não
    especifica uma sub-aba em particular (nav lateral "Análise", "Ir para
    análise" na lista de lojas) — retoma a última sub-página visitada
    nesta sessão (ver _barra_abas_analise), ou "Ajuste de mix" (a
    primeira) se esta é a primeira vez. Substitui o comportamento antigo
    em que a key do st.segmented_control (aba_analise_ativa) persistia
    sozinha em session_state entre navegações."""
    url_path_salvo = st.session_state.get("_analise_ultima_subpagina")
    if url_path_salvo == PAGINA_SUGESTAO_GC.url_path:
        return PAGINA_SUGESTAO_GC
    if url_path_salvo == PAGINA_CONFERENCIA.url_path:
        return PAGINA_CONFERENCIA
    return PAGINA_AJUSTE_MIX


def _barra_abas_analise(sub_pagina_ativa: str) -> None:
    """Reconstrói visualmente a barra de sub-abas da Análise (Ajuste de
    mix / Sugestão de GC / Conferência) em cima do CSS que já existia pro
    st.segmented_control (ver modules/styles.py) — agora são 3 st.Page
    genuinamente separadas, então cada "aba" é um st.button() dentro do
    seu próprio st.container() marcado (mesmo padrão marcador+:has() já
    usado no resto do projeto, ex.: nav da sidebar), que chama
    st.switch_page() em vez de só trocar o valor de um widget. Clicar na
    aba já ativa não faz nada, mesmo comportamento do required=True do
    segmented_control antigo."""
    abas = [
        ("Ajuste de mix", PAGINA_AJUSTE_MIX),
        ("Sugestão de GC", PAGINA_SUGESTAO_GC),
        ("Conferência", PAGINA_CONFERENCIA),
    ]
    with st.container():
        st.markdown('<span class="mdf-abas-analise-marker"></span>', unsafe_allow_html=True)
        cols = st.columns(len(abas))
        for col, (rotulo, pagina_alvo) in zip(cols, abas):
            with col:
                ativa = rotulo == sub_pagina_ativa
                marcador = "mdf-aba-analise-ativa-marker" if ativa else "mdf-aba-analise-inativa-marker"
                with st.container():
                    st.markdown(f'<span class="{marcador}"></span>', unsafe_allow_html=True)
                    if st.button(rotulo, key=f"aba_analise_{pagina_alvo.url_path.replace('/', '_')}", width="stretch"):
                        if not ativa:
                            st.switch_page(pagina_alvo)
    st.session_state["_analise_ultima_subpagina"] = [p for r, p in abas if r == sub_pagina_ativa][0].url_path


def pagina_ajuste_mix() -> None:
    ctx = _contexto_analise()
    if ctx is None:
        return
    (
        loja, ciclo_selecionado, metadata_ciclo, consultor,
        mapa_df, estoque_df, tabela_base, erro_dados,
        subtitulo_cabecalho, _pag_t0,
    ) = ctx

    _barra_abas_analise("Ajuste de mix")

    if erro_dados:
        st.error(erro_dados)
    else:
        chave_estado = f"ajuste_mix::{loja}::{ciclo_selecionado}"

        if chave_estado not in st.session_state:
            # Inicializa o estado editável (1a vez que esta loja/ciclo é aberta
            # nesta sessão) a partir do ajuste salvo ou, sem ele, do cálculo
            # automático. Se a leitura falhar, a tela para aqui: abrir com o
            # cálculo automático deixaria um "Salvar" apagar o ajuste existente.
            try:
                ajuste_salvo = dl.carregar_ajuste_mix_salvo(storage, ciclo_selecionado)
            except StorageError as e:
                st.error(f"Não foi possível ler o ajuste de mix salvo desta loja. Tente de novo em instantes. ({e})")
                return
            except ValueError:
                ajuste_salvo = None
                st.warning(
                    "O ajuste de mix salvo desta loja está corrompido e não pôde ser lido. "
                    "A tela mostra a sugestão automática; ao salvar, ele será substituído."
                )
            st.session_state[chave_estado] = dl.quantidades_iniciais_ajuste(tabela_base, ajuste_salvo)

        quantidades_editadas = st.session_state[chave_estado]

        # Os widgets de quantidade já editados nesta interação têm seu valor
        # atualizado em st.session_state antes deste script rodar — sincroniza
        # o dict aqui para que a contagem/confirmação e o status "alterado" de
        # cada produto reflitam a edição mais recente, mesmo antes do loop de
        # renderização (mais abaixo) rodar.
        for _, row in tabela_base.iterrows():
            chave_produto = row["chave_produto"]
            chave_widget = f"qtd::{chave_estado}::{chave_produto}"
            if chave_widget in st.session_state:
                quantidades_editadas[chave_produto] = st.session_state[chave_widget]

        qtd_preenchidos = sum(1 for v in quantidades_editadas.values() if v and v > 0)

        def _status_atual_linha(row):
            chave_produto = row["chave_produto"]
            quantidade_atual = quantidades_editadas.get(chave_produto, row["quantidade"])
            return styles.status_produto(row["quantidade"], quantidade_atual, row["origem"], row["ean_valido"])

        tabela_base = tabela_base.assign(status_atual=tabela_base.apply(_status_atual_linha, axis=1))

        produtos_nao_localizados = tabela_base[~tabela_base["ean_valido"]]

        if len(produtos_nao_localizados) > 0:
            with st.expander(f"⚠️ {len(produtos_nao_localizados)} produto(s) com EAN não localizado — ver detalhes"):
                st.caption(
                    "Estes produtos continuam na lista abaixo e podem ser preenchidos manualmente. "
                    "O EAN cadastrado na origem não pôde ser cruzado com a base de demanda/estoque."
                )
                for _, row in produtos_nao_localizados.iterrows():
                    st.markdown(f"- **{row['produto']}** — módulo {row['modulo']}, posição {row['posicao']}, EAN cadastrado: `{row['ean_original']}`")

        c_busca_texto, c_busca_status, c_botao_salvar = st.columns([0.42, 0.38, 0.20], gap="small")
        with c_busca_texto:
            termo_busca = st.text_input(
                "Buscar",
                placeholder="Buscar por produto, EAN ou posição...",
                label_visibility="collapsed",
            )
        with c_busca_status:
            opcao_status = st.selectbox(
                "Status",
                config.OPCOES_FILTRO_STATUS,
                label_visibility="collapsed",
            )
        with c_botao_salvar:
            if st.button("💾 Salvar", type="primary", width="stretch"):
                if qtd_preenchidos < config.QTD_MINIMA_AJUSTE_MIX:
                    st.session_state["_confirmar_salvar_poucos"] = True
                else:
                    st.session_state["_confirmar_salvar_poucos"] = False
                    st.session_state["_pronto_para_salvar"] = True

        mask_busca = tabela_base.apply(
            lambda row: styles.corresponde_busca(termo_busca, row["produto"], row["ean_original"], row["posicao"]),
            axis=1,
        )
        codigo_filtro_status = styles.codigo_status_do_filtro(opcao_status)
        if codigo_filtro_status is None:
            mask_status = pd.Series(True, index=tabela_base.index)
        else:
            mask_status = tabela_base["status_atual"] == codigo_filtro_status

        tabela_exibida = tabela_base[mask_busca & mask_status]

        if st.session_state.get("_confirmar_salvar_poucos"):
            st.warning(f"Somente {qtd_preenchidos} produtos no ajuste de mix. Deseja continuar?")
            c1, c2 = st.columns(2)
            if c1.button("Sim, salvar assim mesmo"):
                st.session_state["_pronto_para_salvar"] = True
                st.session_state["_confirmar_salvar_poucos"] = False
            if c2.button("Cancelar"):
                st.session_state["_confirmar_salvar_poucos"] = False

        if st.session_state.get("_pronto_para_salvar"):
            payload = {
                "loja": loja,
                "consultor": consultor or None,
                "ciclo": ciclo_selecionado,
                "produtos": [
                    {
                        "ean": chave if str(chave).isdigit() and len(str(chave)) >= config.EAN_MIN_DIGITOS else None,
                        "ean_original": chave,
                        "quantidade": int(qtd),
                    }
                    for chave, qtd in quantidades_editadas.items()
                    if qtd and qtd > 0
                ],
            }
            conteudo_json = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            caminho_salvar = f"{ciclo_selecionado}/ajuste_mix.json"
            try:
                storage.escrever_arquivo_bytes(caminho_salvar, conteudo_json)
                st.toast("Ajuste de mix salvo.", icon="✅")
            except StorageError as e:
                st.error(f"Falha ao salvar: {e}")
            st.session_state["_pronto_para_salvar"] = False

        # ---- Paginação da lista (ver bloco "Paginação de listas longas"
        # acima) — só afeta QUAIS produtos ganham um st.number_input
        # nesta execução do script; quantidades_editadas (o dict que vira
        # o payload salvo) continua sendo sincronizado a partir de TODA a
        # tabela_base logo acima, então trocar de página nunca perde a
        # quantidade digitada numa página diferente (a key do widget,
        # "qtd::{chave_estado}::{chave_produto}", é estável por produto,
        # não por posição de renderização — o valor fica em
        # st.session_state mesmo quando o widget não é desenhado nesta
        # execução). O aviso de EAN não localizado e o botão Salvar acima
        # já usam tabela_base/quantidades_editadas inteiros, não
        # tabela_exibida/tabela_pagina — continuam cobrindo todas as
        # páginas.
        chave_pagina_mix = f"pagina_mix::{chave_estado}"
        pagina_atual_mix, total_paginas_mix = _pagina_atual_paginacao(
            chave_pagina_mix, len(tabela_exibida), _TAMANHO_PAGINA_LISTA,
            chave_reset=(termo_busca, opcao_status),
        )
        tabela_pagina_mix = _fatia_pagina(tabela_exibida, pagina_atual_mix, _TAMANHO_PAGINA_LISTA)

        with st.container(border=True):
            st.markdown('<span class="mdf-painel-marker"></span>', unsafe_allow_html=True)

            if len(tabela_exibida) == 0:
                st.caption("Nenhum produto encontrado para esta busca.")

            _mix_t0 = time.perf_counter()  # >>> DIAGNOSTICO
            print(f"[MIX-TEMPO] INICIO loop de renderizacao, len(tabela_exibida)={len(tabela_exibida)}, pagina={pagina_atual_mix}/{total_paginas_mix}, len(tabela_pagina)={len(tabela_pagina_mix)}", flush=True)  # >>> DIAGNOSTICO
            for _, row in tabela_pagina_mix.iterrows():
                chave_produto = row["chave_produto"]
                valor_atual = quantidades_editadas.get(chave_produto, row["quantidade"])
                eh_qtd_negativa = pd.notna(valor_atual) and valor_atual < 0
                with st.container():
                    st.markdown('<span class="mdf-row-marker"></span>', unsafe_allow_html=True)
                    if eh_qtd_negativa:
                        st.markdown('<span class="mdf-row-qtd-negativa-marker"></span>', unsafe_allow_html=True)
                    c1, c2, c3 = st.columns([0.08, 0.66, 0.26])
                    with c1:
                        posicao_label = row["posicao"] if pd.notna(row["posicao"]) else "–"
                        st.markdown(f'<div class="mdf-tag">{posicao_label}</div>', unsafe_allow_html=True)
                    with c2:
                        badge_html = styles.badge_status(row["status_atual"])
                        st.markdown(f'<p class="mdf-produto-nome">{row["produto"]}</p>', unsafe_allow_html=True)
                        st.markdown(f'<p class="mdf-produto-meta">{badge_html}</p>', unsafe_allow_html=True)
                    with c3:
                        # Piso normalmente é 0 (não existe "pedir quantidade
                        # negativa"). Mas se o estoque da planilha de origem já
                        # veio negativo, o piso desce até esse valor pra não
                        # travar o widget (StreamlitValueBelowMinError) — o
                        # consultor consegue corrigir na hora clicando em "+".
                        piso = min(0, int(row["quantidade"])) if pd.notna(row["quantidade"]) else 0
                        col_input, col_alerta = st.columns([0.85, 0.15])
                        with col_input:
                            nova_qtd = st.number_input(
                                "Quantidade",
                                min_value=piso,
                                value=int(valor_atual) if pd.notna(valor_atual) else 0,
                                step=1,
                                key=f"qtd::{chave_estado}::{chave_produto}",
                                label_visibility="collapsed",
                            )
                        with col_alerta:
                            if eh_qtd_negativa:
                                st.markdown('<span class="mdf-qtd-alerta-icone">⚠</span>', unsafe_allow_html=True)
                        quantidades_editadas[chave_produto] = nova_qtd
            print(f"[MIX-TEMPO] FIM loop de renderizacao: {time.perf_counter() - _mix_t0:.2f}s", flush=True)  # >>> DIAGNOSTICO

        _controles_paginacao(chave_pagina_mix, pagina_atual_mix, total_paginas_mix, len(tabela_exibida), sufixo_key="rodape")
        print(f"[PAGINA-TEMPO] aba_mix completa (desde inicio da pagina): {time.perf_counter() - _pag_t0:.2f}s", flush=True)  # >>> DIAGNOSTICO


def pagina_sugestao_gc() -> None:
    ctx = _contexto_analise()
    if ctx is None:
        return
    (
        loja, ciclo_selecionado, metadata_ciclo, consultor,
        mapa_df, estoque_df, tabela_base, erro_dados,
        subtitulo_cabecalho, _pag_t0,
    ) = ctx

    _barra_abas_analise("Sugestão de GC")

    if erro_dados:
        st.error(erro_dados)
    else:
        _gc_t0 = time.perf_counter()  # >>> DIAGNOSTICO
        ajuste_salvo = dl.carregar_ajuste_mix_salvo(storage, ciclo_selecionado)
        print(f"[GC-TEMPO] 1 carregar_ajuste_mix_salvo: {time.perf_counter() - _gc_t0:.2f}s", flush=True)  # >>> DIAGNOSTICO
        _qtd_produtos_ajuste = len(ajuste_salvo["produtos"]) if ajuste_salvo else None  # >>> DIAGNOSTICO
        print(f"[GC-TAMANHO] len(mapa_df)={len(mapa_df) if mapa_df is not None else None} | ajuste_salvo is None={ajuste_salvo is None} | len(ajuste_salvo['produtos'])={_qtd_produtos_ajuste}", flush=True)  # >>> DIAGNOSTICO

        if ajuste_salvo is None:
            st.info(
                "Nenhum ajuste de mix salvo ainda para esta loja. Vá para a aba "
                "'Ajuste de mix' e salve primeiro."
            )
        else:
            _gc_t1 = time.perf_counter()  # >>> DIAGNOSTICO
            tabela_gc = dl.montar_tabela_sugestao_gc(mapa_df, ajuste_salvo)
            print(f"[GC-TEMPO] 2 montar_tabela_sugestao_gc: {time.perf_counter() - _gc_t1:.2f}s", flush=True)  # >>> DIAGNOSTICO
            print(f"[GC-TAMANHO] len(tabela_gc)={len(tabela_gc)}", flush=True)  # >>> DIAGNOSTICO

            _gc_t2 = time.perf_counter()  # >>> DIAGNOSTICO
            print(f"[GC-TAMANHO] ANTES de chamar _carregar_preview_modelo variante=tela ciclo={ciclo_selecionado!r}", flush=True)  # >>> DIAGNOSTICO
            imagem_modelo_gc, mime_modelo_gc, msg_modelo_gc = _carregar_preview_modelo(
                storage, ciclo_selecionado, "tela", _assinatura_ciclo(ciclo_selecionado)
            )
            print(f"[GC-TEMPO] 3 carregar_imagem_conferencia: {time.perf_counter() - _gc_t2:.2f}s", flush=True)  # >>> DIAGNOSTICO

            _gc_t3 = time.perf_counter()  # >>> DIAGNOSTICO
            # Aviso "Modelo ainda não enviado" — decisão do produto:
            # aparece toda vez que o consultor ENTRA na Análise
            # deste ciclo (não a cada rerun causado por qualquer
            # interação dentro da aba, ex.: digitar na busca), já
            # que o Streamlit não distingue "trocar de aba" de
            # qualquer outro rerun da página. Quem reseta o
            # controle abaixo é o clique em "Ir para análise" (ver
            # página Selecionar Loja) — é isso que marca uma nova
            # "visita" a este ciclo.
            if imagem_modelo_gc is None and msg_modelo_gc == "Ainda não enviada":
                if st.session_state.get("_gc_modelo_aviso_ciclo_visto") != ciclo_selecionado:
                    st.session_state["_gc_modelo_aviso_ciclo_visto"] = ciclo_selecionado
                    _dialog_modelo_ausente(loja, ciclo_selecionado.split("/", 1)[1])
            print(f"[GC-TEMPO] 4 checagem modelo ausente: {time.perf_counter() - _gc_t3:.2f}s", flush=True)  # >>> DIAGNOSTICO

            _gc_t4 = time.perf_counter()  # >>> DIAGNOSTICO
            produtos_nao_localizados_gc = tabela_gc[~tabela_gc["ean_valido"]]
            if len(produtos_nao_localizados_gc) > 0:
                with st.expander(f"⚠️ {len(produtos_nao_localizados_gc)} produto(s) com EAN não localizado — ver detalhes"):
                    st.caption(
                        "Estes produtos continuam na lista abaixo e podem ser preenchidos manualmente. "
                        "O EAN cadastrado na origem não pôde ser cruzado com a base de demanda/estoque."
                    )
                    for _, row in produtos_nao_localizados_gc.iterrows():
                        st.markdown(f"- **{row['produto']}** — módulo {row['modulo']}, posição {row['posicao']}, EAN cadastrado: `{row['ean_original']}`")
            print(f"[GC-TEMPO] 5 expander EAN nao localizado: {time.perf_counter() - _gc_t4:.2f}s", flush=True)  # >>> DIAGNOSTICO

            c_busca_texto_gc, c_botao_pdf = st.columns([0.75, 0.25], gap="small")
            with c_busca_texto_gc:
                termo_busca_gc = st.text_input(
                    "Buscar",
                    placeholder="Buscar por produto, EAN ou posição...",
                    label_visibility="collapsed",
                    key="busca_gc",
                )
            with c_botao_pdf:
                # A tabela usada no PDF é sempre a lista completa do ajuste
                # salvo (não a filtrada pela busca acima) — a busca é só uma
                # ajuda de navegação em tela, não deve limitar o que é exportado.
                _gc_t5 = time.perf_counter()  # >>> DIAGNOSTICO
                try:  # >>> DIAGNOSTICO
                    imagem_modelo_impressao, _, _ = _carregar_preview_modelo(
                        storage, ciclo_selecionado, "impressao", _assinatura_ciclo(ciclo_selecionado)
                    )
                except BaseException as e:  # >>> DIAGNOSTICO
                    print(f"[GC-EXCEPTION] impressao: tipo={type(e).__module__}.{type(e).__name__} msg={e!r}", flush=True)  # >>> DIAGNOSTICO
                    raise  # >>> DIAGNOSTICO
                try:  # >>> DIAGNOSTICO
                    pdf_bytes = _gerar_pdf_gc_cache(tabela_gc, consultor, loja, subtitulo_cabecalho, imagem_modelo_impressao)
                except BaseException as e:  # >>> DIAGNOSTICO
                    print(f"[GC-EXCEPTION] gerar_pdf_gc_cache: tipo={type(e).__module__}.{type(e).__name__} msg={e!r}", flush=True)  # >>> DIAGNOSTICO
                    raise  # >>> DIAGNOSTICO
                print(f"[GC-TEMPO] 6 gerar_pdf_gc_cache: {time.perf_counter() - _gc_t5:.2f}s", flush=True)  # >>> DIAGNOSTICO
                st.download_button(
                    "📄 Exportar PDF",
                    data=pdf_bytes,
                    file_name=f"Ajuste_Mix_RMC_{loja}.pdf",
                    mime="application/pdf",
                    type="primary",
                    width="stretch",
                )

            _gc_t6 = time.perf_counter()  # >>> DIAGNOSTICO
            mask_busca_gc = tabela_gc.apply(
                lambda row: styles.corresponde_busca(termo_busca_gc, row["produto"], row["ean_original"], row["posicao"]),
                axis=1,
            )
            tabela_gc_exibida = tabela_gc[mask_busca_gc]
            print(f"[GC-TEMPO] 7 filtro de busca (mask): {time.perf_counter() - _gc_t6:.2f}s", flush=True)  # >>> DIAGNOSTICO

            # ---- Paginação da lista (ver bloco "Paginação de listas
            # longas" no topo do arquivo) — a lista aqui só tem display
            # (sem widget de input por produto), então não há estado de
            # edição pra preservar entre páginas; ainda assim reduz o
            # número de elementos desenhados por execução do script numa
            # loja com ajuste de mix grande. O PDF exportado acima já usa
            # tabela_gc inteira (não tabela_gc_exibida/tabela_gc_pagina),
            # então continua cobrindo todos os produtos independente da
            # paginação em tela.
            chave_pagina_gc = f"pagina_gc::{loja}::{ciclo_selecionado}"
            pagina_atual_gc, total_paginas_gc = _pagina_atual_paginacao(
                chave_pagina_gc, len(tabela_gc_exibida), _TAMANHO_PAGINA_LISTA,
                chave_reset=(termo_busca_gc,),
            )
            tabela_gc_pagina = _fatia_pagina(tabela_gc_exibida, pagina_atual_gc, _TAMANHO_PAGINA_LISTA)

            _gc_t7 = time.perf_counter()  # >>> DIAGNOSTICO
            with st.container(border=True):
                st.markdown('<span class="mdf-painel-marker"></span>', unsafe_allow_html=True)

                if len(tabela_gc_exibida) == 0:
                    st.caption("Nenhum produto encontrado para esta busca.")

                for _, row in tabela_gc_pagina.iterrows():
                    with st.container():
                        st.markdown('<span class="mdf-row-marker"></span>', unsafe_allow_html=True)
                        c1, c2, c3 = st.columns([0.08, 0.66, 0.26])
                        with c1:
                            posicao_label = row["posicao"] if pd.notna(row["posicao"]) else "–"
                            st.markdown(f'<div class="mdf-tag">{posicao_label}</div>', unsafe_allow_html=True)
                        with c2:
                            st.markdown(f'<p class="mdf-produto-nome">{row["produto"]}</p>', unsafe_allow_html=True)
                            frentes_html = styles.badge_frentes(row["frentes"])
                            st.markdown(f'<p class="mdf-produto-meta">{frentes_html}</p>', unsafe_allow_html=True)
                        with c3:
                            st.markdown(
                                f'<p class="mdf-produto-qtd-destaque">{int(row["quantidade"])}</p>',
                                unsafe_allow_html=True,
                            )
            _controles_paginacao(chave_pagina_gc, pagina_atual_gc, total_paginas_gc, len(tabela_gc_exibida), sufixo_key="rodape")
            print(f"[GC-TEMPO] 8 loop de renderizacao das linhas: {time.perf_counter() - _gc_t7:.2f}s | pagina={pagina_atual_gc}/{total_paginas_gc}", flush=True)  # >>> DIAGNOSTICO
            print(f"[GC-TEMPO] TOTAL do bloco aba_gc: {time.perf_counter() - _gc_t0:.2f}s", flush=True)  # >>> DIAGNOSTICO


def pagina_conferencia() -> None:
    ctx = _contexto_analise()
    if ctx is None:
        return
    (
        loja, ciclo_selecionado, metadata_ciclo, consultor,
        mapa_df, estoque_df, tabela_base, erro_dados,
        subtitulo_cabecalho, _pag_t0,
    ) = ctx

    _barra_abas_analise("Conferência")

    print(f"[PAGINA-TEMPO] aba_conf INICIO (desde inicio da pagina): {time.perf_counter() - _pag_t0:.2f}s", flush=True)  # >>> DIAGNOSTICO
    _conf_t0 = time.perf_counter()  # >>> DIAGNOSTICO
    imagem_antes, mime_antes, msg_antes = _carregar_imagem_conferencia(
        storage, ciclo_selecionado, "foto_antes", _assinatura_ciclo(ciclo_selecionado)
    )
    imagem_modelo, mime_modelo, msg_modelo = _carregar_preview_modelo(
        storage, ciclo_selecionado, "tela", _assinatura_ciclo(ciclo_selecionado)
    )
    imagem_depois, mime_depois, msg_depois = _carregar_imagem_conferencia(
        storage, ciclo_selecionado, "foto_depois", _assinatura_ciclo(ciclo_selecionado)
    )
    print(f"[PAGINA-TEMPO] aba_conf 3 imagens carregadas: {time.perf_counter() - _conf_t0:.2f}s", flush=True)  # >>> DIAGNOSTICO

    c_antes, c_modelo, c_depois = st.columns(3, gap="medium")
    with c_antes:
        styles.cartao_foto("Antes", imagem_antes, mime_antes, msg_antes)
    with c_modelo:
        styles.cartao_foto("Modelo", imagem_modelo, mime_modelo, msg_modelo)
    with c_depois:
        styles.cartao_foto("Depois", imagem_depois, mime_depois, msg_depois)

    # ---- Resultado da ação ----
    # Não depende do ajuste de mix: compara o que a loja vendeu, com ou sem
    # ajuste salvo neste ciclo.
    st.markdown("&nbsp;", unsafe_allow_html=True)
    st.markdown('<p class="mdf-secao-titulo">Resultado da ação</p>', unsafe_allow_html=True)

    if mapa_df is None:
        st.error(erro_dados or "Não foi possível carregar o Mapa da Farmácia para calcular o resultado da ação.")
        return

    erro_resultado = None
    dados_resultado = None
    try:
        dados_resultado = _carregar_dados_resultado_acao(
            storage, ciclo_selecionado, _assinatura_ciclo(ciclo_selecionado)
        )
    except dl.PlanilhaInvalidaError as e:
        erro_resultado = (
            f"Não foi possível interpretar o Retrato de Vendas ou o Estoque "
            f"atualizado: {e}"
        )
    except dl.MultiplasLojasEstoqueError as e:
        erro_resultado = (
            f"O Estoque atualizado contém mais de uma loja: "
            f"{', '.join(e.ids_encontrados)}."
        )

    if erro_resultado:
        st.error(erro_resultado)
    elif not dados_resultado["retrato_presente"] or not dados_resultado["estoque_atualizado_presente"]:
        faltando = []
        if not dados_resultado["retrato_presente"]:
            faltando.append("**Retrato de Vendas**")
        if not dados_resultado["estoque_atualizado_presente"]:
            faltando.append("**Estoque atualizado**")
        st.info(f"Falta {' e '.join(faltando)} deste mês para ver o resultado da ação.")
        if st.button("Enviar agora", key="conferencia_enviar_agora", type="primary"):
            _abrir_upload(loja, ciclo_selecionado.partition("/")[2])
    else:
        rotulo_depois, fim_periodo = _periodo_depois(dados_resultado["periodo"], metadata_ciclo)
        resultado = ra.montar_resultado_acao(
            mapa_df,
            dados_resultado["estoque_resultado_df"],
            dados_resultado["retrato_df"],
            dados_resultado["estoque_atualizado_df"],
            fim_periodo,
        )
        mes_planograma = styles.mes_legivel(ciclo_selecionado.split("/")[-1])
        rotulo_antes = f"média mensal dos 3 meses do Estoque de {mes_planograma}"
        _renderizar_resultado_acao(resultado, rotulo_antes, rotulo_depois, ciclo_selecionado)


# ---------------------------------------------------------------------------
# Navegação: st.navigation()/st.Page() nativos, position="hidden" (a
# navegação visível continua sendo a nossa, customizada em CSS, na
# sidebar). Substitui por completo o antigo esquema de
# st.session_state["pagina_atual"] + if/elif no corpo do script — cada
# página abaixo agora é um script genuinamente separado aos olhos do
# Streamlit: ao navegar pra "Sugestão de GC", só a função pagina_sugestao_
# gc roda; o código de Ajuste de mix e Conferência simplesmente não
# executa nessa requisição (garantia do framework, não convenção de
# código). Isso elimina de vez a hipótese de código de uma aba "vazando"
# efeitos colaterais/tempo de execução pra outra.
# ---------------------------------------------------------------------------

PAGINA_UPLOAD = st.Page(pagina_upload, title="Upload", url_path="upload")
PAGINA_SELECIONAR_LOJA = st.Page(pagina_selecionar_loja, title="Selecionar Loja", url_path="selecionar-loja", default=True)
# NOTA: st.Page.url_path não aceita "/" (path aninhado) fora de navegação
# por seções com dict — só slugs de um nível. Daí "analise-ajuste-mix" e
# não "analise/ajuste-mix".
PAGINA_AJUSTE_MIX = st.Page(pagina_ajuste_mix, title="Ajuste de mix", url_path="analise-ajuste-mix")
PAGINA_SUGESTAO_GC = st.Page(pagina_sugestao_gc, title="Sugestão de GC", url_path="analise-sugestao-gc")
PAGINA_CONFERENCIA = st.Page(pagina_conferencia, title="Conferência", url_path="analise-conferencia")

# "Grupo" de cada st.Page — as 3 sub-páginas de Análise contam como um
# único grupo "Análise" pra fins de navegação da sidebar customizada e da
# lógica de versao_cache abaixo (ver _PAGINAS_COM_ATUALIZACAO_AUTOMATICA).
_GRUPO_DA_PAGINA = {
    PAGINA_UPLOAD.url_path: "Upload",
    PAGINA_SELECIONAR_LOJA.url_path: "Selecionar Loja",
    PAGINA_AJUSTE_MIX.url_path: "Análise",
    PAGINA_SUGESTAO_GC.url_path: "Análise",
    PAGINA_CONFERENCIA.url_path: "Análise",
}
_GRUPOS_SIDEBAR = ["Upload", "Selecionar Loja", "Análise"]
_SLUGS_GRUPOS = {"Upload": "upload", "Selecionar Loja": "selecionar_loja", "Análise": "analise"}

# Grupos que trabalham em cima do inventário/API (ao invés de só formulário
# de envio) — entrar neles dispara uma atualização automática dos dados (ver
# abaixo), pra tirar do usuário o esforço manual de lembrar de apertar um
# botão "Atualizar" antes de analisar uma loja.
_PAGINAS_COM_ATUALIZACAO_AUTOMATICA = ("Selecionar Loja", "Análise")

pg = st.navigation(
    [PAGINA_SELECIONAR_LOJA, PAGINA_UPLOAD, PAGINA_AJUSTE_MIX, PAGINA_SUGESTAO_GC, PAGINA_CONFERENCIA],
    position="hidden",
)

# Atualização automática: antes existia um botão manual "Atualizar" na
# sidebar (com confirmação) que só bumpava versao_cache quando o usuário
# lembrava de clicar — removido porque o usuário pode esquecer e analisar
# dados desatualizados sem perceber. Em vez disso, entrar em "Selecionar
# Loja" ou "Análise" (vindo de QUALQUER outro grupo) bumpa versao_cache
# sozinho — esses dois grupos são os que realmente dependem do inventário
# estar fresco. versao_cache hoje só refaz o INVENTÁRIO (barato: 1 listagem
# + 1 índice, ver modules/inventario.py); os dados pesados usam a
# assinatura dos arquivos (ver _assinatura_ciclo) e só são baixados de novo
# se algo mudou de fato. Não chama st.cache_data.clear() (isso limparia também o
# cache da API do TI, que tem TTL próprio de 1h e não precisa ser forçado
# a cada navegação). Trocar de sub-página DENTRO de Análise (Ajuste de mix
# <-> Sugestão de GC <-> Conferência) NÃO bumpa — mesmo comportamento do
# esquema antigo, em que só a troca de "página" (não de sub-aba) disparava
# isso.
st.session_state.setdefault("versao_cache", 0)
_grupo_atual = _GRUPO_DA_PAGINA[pg.url_path]
_grupo_anterior = st.session_state.get("_grupo_pagina_anterior")
if _grupo_atual != _grupo_anterior and _grupo_atual in _PAGINAS_COM_ATUALIZACAO_AUTOMATICA:
    st.session_state["versao_cache"] += 1
st.session_state["_grupo_pagina_anterior"] = _grupo_atual

with st.sidebar:
    # Menu de páginas como 3 botões empilhados (não st.radio nativo, nem a
    # sidebar automática do st.navigation — por isso position="hidden"
    # acima) — cada um envolto num st.container() com um marcador CSS que
    # indica se é o grupo ATIVO (preenchido navy) ou INATIVO (neutro
    # claro), seguindo o mesmo padrão de marcador+:has() já usado no resto
    # do projeto (ver modules/styles.py). Clicar num item que já é o ativo
    # não faz nada (evita st.switch_page à toa). "Análise" retoma a última
    # sub-página visitada (ver _pagina_analise_para_retomar).
    for grupo_item in _GRUPOS_SIDEBAR:
        grupo_ativo = grupo_item == _grupo_atual
        marcador_nav = "mdf-navitem-ativo-marker" if grupo_ativo else "mdf-botao-discreto-marker"
        with st.container():
            st.markdown(f'<span class="{marcador_nav}"></span>', unsafe_allow_html=True)
            if st.button(grupo_item, key=f"nav_{_SLUGS_GRUPOS[grupo_item]}", width="stretch"):
                if not grupo_ativo:
                    if grupo_item == "Upload":
                        _abrir_upload()
                    elif grupo_item == "Selecionar Loja":
                        st.switch_page(PAGINA_SELECIONAR_LOJA)
                    else:
                        st.switch_page(_pagina_analise_para_retomar())

pg.run()
