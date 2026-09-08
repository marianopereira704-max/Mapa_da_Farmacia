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

import io
import json
import time
from datetime import date, datetime
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
import modules.styles as styles
from modules.file_resolver import ArquivoObrigatorioAusenteError, localizar_arquivo
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
def _descobrir_inventario_cache(_storage, versao_cache: int) -> dict:
    return inventario.descobrir_inventario(_storage)


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


# ---------------------------------------------------------------------------
# Carregamento de dados da loja/ciclo selecionado (usado só na página
# Análise)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=600, show_spinner="Carregando dados da loja...")
def _carregar_dados_loja(_storage, caminho_ciclo: str, versao_cache: int):
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
def _carregar_base_nacional(_storage, versao_cache: int):
    arq_base = localizar_arquivo(_storage, config.BASE_NACIONAL_FOLDER_NAME, config.BASE_NACIONAL_SPEC, "base_mercado")
    return dl.carregar_base_nacional(io.BytesIO(arq_base.conteudo), extensao=arq_base.extensao)


@st.cache_data(
    ttl=600,
    show_spinner="Carregando imagem (arquivos grandes podem levar alguns segundos)...",
)
def _carregar_imagem_conferencia(_storage, caminho_ciclo: str, chave: str, versao_cache: int):
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
def _carregar_preview_modelo(_storage, caminho_ciclo: str, variante: str, versao_cache: int):
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
def _carregar_dados_resultado_acao(_storage, caminho_ciclo: str, versao_cache: int) -> dict:
    """Carrega os dados necessários para a feature Resultado da ação (Aba
    3 — Conferência). Só monta os DataFrames quando os DOIS arquivos
    "depois" (Retrato de Vendas + Estoque atualizado) já foram enviados —
    sem o Estoque atualizado não dá pra diferenciar "deixou de vender" de
    "sem venda no período" com confiança (ver data_loader.montar_tabela_
    resultado_acao), então a feature não é exibida parcialmente.

    Retorna um dict:
      "retrato_presente" / "estoque_atualizado_presente": bool — pra UI
        avisar especificamente qual arquivo falta, se algum faltar (não é
        erro, é um estado esperado até o consultor enviar).
      "retrato_df" / "estoque_resultado_df" / "estoque_atualizado_df":
        None enquanto algum dos dois não foi enviado.
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
    }
    if arq_retrato is not None and arq_estoque_atualizado is not None:
        arq_estoque = localizar_arquivo(_storage, caminho_ciclo, config.FILE_SPECS["estoque"], "estoque")
        dados["retrato_df"] = dl.carregar_retrato_vendas(io.BytesIO(arq_retrato.conteudo))
        dados["estoque_resultado_df"] = dl.carregar_estoque_resultado(io.BytesIO(arq_estoque.conteudo))
        dados["estoque_atualizado_df"] = dl.carregar_estoque(io.BytesIO(arq_estoque_atualizado.conteudo))
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

def _par_texto(antes, depois, status: str, formatador) -> str:
    """Formata o par "antes → depois" de uma linha da lista produto a
    produto. Casos especiais: "sem_dado" (EAN não localizado, não há
    nenhum dos dois) e "novo" (não existia "antes") mostram travessão do
    lado que não se aplica, em vez de um "0" que pareceria dado real."""
    texto_antes = "—" if status in ("sem_dado", "novo") else formatador(antes)
    texto_depois = "—" if status == "sem_dado" else formatador(depois)
    return f'<span class="antes">{texto_antes}</span><span class="seta">→</span>{texto_depois}'


def _renderizar_resultado_acao(
    tabela: pd.DataFrame, mes_antes_legivel: str, mes_depois_legivel: str, ciclo_selecionado: str
) -> None:
    """Renderiza o dashboard completo do Resultado da ação — chamado só
    depois que a UI (aba_conf) já confirmou que há ajuste de mix salvo e
    que os dois arquivos "depois" (Retrato de Vendas + Estoque
    atualizado) foram enviados. `ciclo_selecionado` só é usado para
    escopar a página atual da lista "Produto a produto" (ver Paginação de
    listas longas) por loja/ciclo — sem filtro de busca nesta lista, então
    não há nada a resetar além da própria troca de loja/ciclo."""
    st.markdown(
        f'<p class="mdf-comparativo-legenda">Comparativo: '
        f'<b>{mes_antes_legivel}</b> vs <b>{mes_depois_legivel}</b>.</p>',
        unsafe_allow_html=True,
    )

    total_antes = float(tabela["faturamento_antes"].sum())
    total_depois = float(tabela["faturamento_depois"].sum())
    crescimento_total_rs = total_depois - total_antes
    crescimento_total_pct = (crescimento_total_rs / total_antes * 100) if total_antes > 0 else None

    c_stat1, c_stat2, c_stat3 = st.columns(3, gap="medium")
    with c_stat1:
        classe_valor = "mdf-stat-valor mdf-stat-valor-positivo" if crescimento_total_rs >= 0 else "mdf-stat-valor"
        sinal = "+" if crescimento_total_rs >= 0 else ""
        styles.cartao_stat(
            "Crescimento em R$",
            f'<p class="{classe_valor}">{sinal}{styles.formatar_rs(crescimento_total_rs)}</p>',
            f"{styles.formatar_rs(total_antes)} → {styles.formatar_rs(total_depois)} (mensal)",
        )
    with c_stat2:
        if crescimento_total_pct is None:
            valor_pct_html = '<p class="mdf-stat-valor">—</p>'
            sub_pct = "sem base de faturamento \"antes\" para calcular %"
        else:
            classe_valor = "mdf-stat-valor mdf-stat-valor-positivo" if crescimento_total_pct >= 0 else "mdf-stat-valor"
            sinal = "+" if crescimento_total_pct >= 0 else ""
            valor_pct_html = f'<p class="{classe_valor}">{sinal}{crescimento_total_pct:.1f}%</p>'
            sub_pct = "sobre o faturamento mensal do mix acompanhado"
        styles.cartao_stat("Crescimento em %", valor_pct_html, sub_pct)
    with c_stat3:
        styles.cartao_stat(
            "Faturamento mensal",
            f'<p class="mdf-stat-valor">{styles.formatar_rs(total_depois)}</p>',
            f"Antes: {styles.formatar_rs(total_antes)}",
        )

    st.markdown("&nbsp;", unsafe_allow_html=True)

    # ---- Top 3 produtos que mais cresceram (em R$) ----
    top3 = tabela[tabela["crescimento_rs"] > 0].sort_values("crescimento_rs", ascending=False).head(3)
    if len(top3) > 0:
        with st.container():
            st.markdown('<span class="mdf-top3-card-marker"></span>', unsafe_allow_html=True)
            st.markdown('<p class="mdf-foto-titulo">Top 3 produtos que mais cresceram (em R$)</p>', unsafe_allow_html=True)
            for posicao, (_, row) in enumerate(top3.iterrows(), start=1):
                badge_html = styles.badge_inline_status_resultado(row["status"])
                meta = f'{int(row["unidades_antes"])} → {int(row["unidades_depois"])} un./mês'
                if row["crescimento_pct"] is not None and pd.notna(row["crescimento_pct"]):
                    meta += f' · +{row["crescimento_pct"]:.0f}%'
                c1, c2, c3 = st.columns([0.08, 0.72, 0.20])
                with c1:
                    st.markdown(f'<div class="mdf-rank-badge">{posicao}</div>', unsafe_allow_html=True)
                with c2:
                    st.markdown(f'<p class="mdf-produto-nome">{row["produto"]}{badge_html}</p>', unsafe_allow_html=True)
                    st.markdown(f'<p class="mdf-produto-meta">{meta}</p>', unsafe_allow_html=True)
                with c3:
                    st.markdown(styles.chip_crescimento_rs(row["crescimento_rs"]), unsafe_allow_html=True)
        st.markdown("&nbsp;", unsafe_allow_html=True)

    # ---- Produtos que deixaram de vender ----
    deixaram = tabela[tabela["status"] == "deixou_de_vender"].sort_values("faturamento_antes", ascending=False)
    if len(deixaram) > 0:
        with st.container():
            st.markdown('<span class="mdf-alerta-card-marker"></span>', unsafe_allow_html=True)
            st.markdown(
                '<p class="mdf-foto-titulo"><span class="mdf-icone-alerta">⚠</span> Produtos que deixaram de vender</p>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<p class="mdf-stat-sub">Estavam no estoque em {mes_antes_legivel}, não tiveram venda em '
                f'{mes_depois_legivel} e o Estoque atualizado confirma que saíram do mix '
                '(estoque zerado ou item não encontrado).</p>',
                unsafe_allow_html=True,
            )
            st.markdown('<div style="height: 14px;"></div>', unsafe_allow_html=True)
            for _, row in deixaram.iterrows():
                c1, c2 = st.columns([0.78, 0.22])
                with c1:
                    st.markdown(f'<p class="mdf-produto-nome">{row["produto"]}</p>', unsafe_allow_html=True)
                    st.markdown(
                        f'<p class="mdf-produto-meta">EAN {row["ean_original"]} · vendia '
                        f'{int(row["unidades_antes"])} un./mês · {styles.formatar_rs(row["faturamento_antes"])}/mês</p>',
                        unsafe_allow_html=True,
                    )
                with c2:
                    st.markdown(styles.chip_variacao_produto("deixou_de_vender", None, None), unsafe_allow_html=True)
        st.markdown("&nbsp;", unsafe_allow_html=True)

    # ---- Produto a produto ----
    st.markdown('<p class="mdf-lista-titulo">Produto a produto</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="mdf-stat-sub">Produtos do ajuste de mix da loja, na mesma ordem de gôndola — '
        'mesmo padrão já usado na Sugestão de GC.</p>',
        unsafe_allow_html=True,
    )
    produtos_nao_encontrados = tabela.attrs.get("produtos_nao_encontrados", [])
    if produtos_nao_encontrados:
        with st.expander(f"⚠️ {len(produtos_nao_encontrados)} produto(s) do ajuste de mix não encontrado(s) no Mapa da Farmácia atual"):
            st.caption("Esses produtos foram salvos no ajuste de mix, mas o Mapa da Farmácia atual não os contém mais.")
            for item in produtos_nao_encontrados:
                st.markdown(f"- EAN cadastrado: `{item['ean_original']}` — quantidade salva: {item['quantidade']}")

    # ---- Paginação da lista (ver bloco "Paginação de listas longas" no
    # topo do arquivo) — sem filtro de busca nesta seção, então
    # chave_reset fica None (só reseta a página quando a própria
    # loja/ciclo muda, já que isso muda a chave_pagina inteira). ----
    chave_pagina_resultado = f"pagina_resultado_acao::{ciclo_selecionado}"
    pagina_atual_resultado, total_paginas_resultado = _pagina_atual_paginacao(
        chave_pagina_resultado, len(tabela), _TAMANHO_PAGINA_LISTA,
    )
    tabela_pagina_resultado = _fatia_pagina(tabela, pagina_atual_resultado, _TAMANHO_PAGINA_LISTA)

    with st.container(border=True):
        st.markdown('<span class="mdf-painel-marker"></span>', unsafe_allow_html=True)
        for _, row in tabela_pagina_resultado.iterrows():
            with st.container():
                st.markdown('<span class="mdf-row-marker"></span>', unsafe_allow_html=True)
                c1, c2, c3 = st.columns([0.4, 0.22, 0.22])
                badge_html = styles.badge_inline_status_resultado(row["status"])
                with c1:
                    st.markdown(f'<p class="mdf-produto-nome">{row["produto"]}{badge_html}</p>', unsafe_allow_html=True)
                    ean_label = "EAN não localizado" if row["status"] == "sem_dado" else row["ean_original"]
                    st.markdown(f'<p class="mdf-produto-meta">{ean_label}</p>', unsafe_allow_html=True)
                with c2:
                    texto_unidades = _par_texto(row["unidades_antes"], row["unidades_depois"], row["status"], lambda v: f"{int(v)}")
                    st.markdown(f'<p class="mdf-par-compacto">{texto_unidades}</p>', unsafe_allow_html=True)
                with c3:
                    texto_valor = _par_texto(row["faturamento_antes"], row["faturamento_depois"], row["status"], styles.formatar_rs)
                    st.markdown(f'<p class="mdf-par-compacto">{texto_valor}</p>', unsafe_allow_html=True)
                    st.markdown(
                        styles.chip_variacao_produto(row["status"], row["crescimento_rs"], row["crescimento_pct"]),
                        unsafe_allow_html=True,
                    )
    _controles_paginacao(chave_pagina_resultado, pagina_atual_resultado, total_paginas_resultado, len(tabela), sufixo_key="rodape")


# ---------------------------------------------------------------------------
# metadata.json de um ciclo (loja/mês) — quem enviou por último, e quando.
# ---------------------------------------------------------------------------

def _atualizar_metadata_ciclo(
    storage, loja: str, ano_mes: str, consultor: str, retrato_vendas_enviado: bool = False
) -> None:
    """Cria ou atualiza (mesclando com o que já existir) o metadata.json
    do ciclo {loja}/{ano_mes}. Só sobrescreve o campo 'consultor' se um
    valor não-vazio foi informado neste envio — um envio sem consultor
    preenchido não apaga o consultor registrado por um envio anterior.

    Quando `retrato_vendas_enviado` é True, carimba também o mês/ano
    ATUAL (não o do ciclo) em 'retrato_vendas_ano_mes' — é esse carimbo
    que a feature Resultado da ação (Aba 3) usa para saber automaticamente
    qual foi o mês "depois" do comparativo, sem o consultor precisar
    informar nada (ver aba_conf)."""
    caminho = f"{loja}/{ano_mes}/{inventario.NOME_ARQUIVO_METADATA}"
    metadata_atual = {}
    try:
        metadata_atual = json.loads(storage.ler_arquivo_bytes(caminho))
    except (StorageError, ValueError):
        pass

    if consultor:
        metadata_atual["consultor"] = consultor
    metadata_atual["enviado_em"] = datetime.now().isoformat(timespec="seconds")
    if retrato_vendas_enviado:
        metadata_atual["retrato_vendas_ano_mes"] = date.today().strftime("%Y-%m")

    conteudo = json.dumps(metadata_atual, ensure_ascii=False, indent=2).encode("utf-8")
    storage.escrever_arquivo_bytes(caminho, conteudo)


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
            # Mesma lógica de pré-preenchimento usada no botão "Upload" de
            # um cartão de ciclo (ver página Selecionar Loja).
            st.session_state["_upload_loja_prefill"] = loja
            st.session_state["_upload_ano_mes_contexto"] = ano_mes
            st.session_state["_upload_ano_mes_contexto_loja"] = loja
            st.switch_page(PAGINA_UPLOAD)
    with c2:
        if st.button("Fechar", key="gc_modelo_fechar", width="stretch"):
            st.rerun()


# Rótulos de exibição dos tipos de arquivo por loja, na mesma ordem em que
# aparecem tanto no formulário de Upload quanto no checklist dos cartões
# de ciclo (página Selecionar Loja, Nível 2). "retrato_vendas" e
# "estoque_atualizado" só aparecem no formulário de Upload quando o ciclo
# já tem ajuste_mix.json salvo (ver grid dinâmico mais abaixo) — mas
# continuam SEMPRE no checklist do cartão de ciclo, igual aos demais.
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

        # Pré-preenchimento vindo de "Enviar novo mês" ou do botão "Upload"
        # de um cartão de ciclo (página Selecionar Loja) — lido UMA VEZ aqui
        # em cima (fora do if/else de qual componente de Loja é usado),
        # porque tanto o campo Loja (dentro de c_loja) quanto o
        # pré-preenchimento do Consultor (dentro de c_consultor, mais
        # abaixo) precisam dele, e o ramo "erro_lojas_api" (fallback sem a
        # API do TI) nunca define essa variável sozinho.
        loja_prefill = st.session_state.pop("_upload_loja_prefill", None)

        c_loja, c_consultor = st.columns(2)
        with c_loja:
            if erro_lojas_api:
                upload_loja = st.text_input("Loja", key="upload_loja")
            else:
                # Componente externo streamlit-searchbox: visualmente é uma
                # seleção normal (um campo só, com dropdown), mas mantém a
                # busca por prefixo definida em _filtrar_lojas_por_prefixo
                # (exatamente o comportamento pedido: "1", "10", "11",
                # "111", ... e não "22" aparecendo ao digitar "1").
                if loja_prefill:
                    # Força o componente a recriar do zero com o valor
                    # pré-preenchido (ver "Ir para Upload" na página
                    # Selecionar Loja) — descartar o session_state antigo
                    # do widget é necessário porque st_searchbox só lê
                    # default_searchterm/default_options na primeira vez
                    # que a key aparece no session_state.
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
                    default="",
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
            #   1. loja_prefill (vindo de "Enviar novo mês" ou do botão
            #      "Upload" de um cartão de ciclo): preenche direto pelo
            #      CÓDIGO da loja, sem esperar upload_loja. Necessário
            #      porque o st_searchbox só devolve um valor não-vazio em
            #      upload_loja depois que o usuário CLICA na sugestão — o
            #      campo já aparece preenchido visualmente (default_searchterm/
            #      default_options, ver c_loja acima), mas upload_loja
            #      continua "" até essa confirmação. Sem tratar esse caso à
            #      parte, o Consultor ficaria em branco mesmo com a Loja já
            #      visível na tela.
            #
            #   2. upload_loja mudou (usuário digitou/selecionou outra loja
            #      manualmente, ou confirmou a sugestão pré-preenchida): só
            #      reage quando upload_loja é CONFIRMADO (truthy) — nunca
            #      quando ele está vazio. Esse guard é essencial: nos reruns
            #      entre o pré-preenchimento (caminho 1) e o usuário
            #      efetivamente clicar na sugestão, upload_loja continua ""
            #      por vários reruns — sem o "and upload_loja" aqui, cada um
            #      desses reruns re-disparava esta condição (upload_loja=""
            #      diferente do que foi salvo no caminho 1) e apagava de
            #      volta o Consultor que acabou de ser preenchido.
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
                # botão "Upload" de um cartão de ciclo foi clicado — se o
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

        # Contexto de "completar um ciclo específico" — definido pelo botão
        # "Upload" de um cartão de mês já existente (página Selecionar
        # Loja). Enquanto ativo: cada campo de arquivo mostra ✅ (já tem
        # dado) ou ⚠️ (falta), usando o mesmo inventário que já monta o
        # checklist daquele cartão, e o envio grava NESSE mês em vez do
        # atual (ver ano_mes mais abaixo). Fora desse fluxo (Upload pela
        # barra lateral, ou "Enviar novo mês"), os dois ficam None e tudo
        # se comporta exatamente como antes.
        ano_mes_contexto = st.session_state.get("_upload_ano_mes_contexto")
        loja_contexto = st.session_state.get("_upload_ano_mes_contexto_loja")
        arquivos_existentes_contexto = None
        ajuste_mix_existe_contexto = False
        if ano_mes_contexto and loja_contexto:
            inventario_contexto = _descobrir_inventario_cache(storage, st.session_state["versao_cache"])
            ciclo_contexto = inventario_contexto.get(loja_contexto, {}).get(ano_mes_contexto)
            if ciclo_contexto:
                arquivos_existentes_contexto = ciclo_contexto.get("arquivos", {})
                # "Retrato de Vendas" e "Estoque atualizado" (Resultado da
                # ação) só fazem sentido depois que o consultor já salvou
                # o ajuste de mix daquele ciclo — sem isso não há lista de
                # produtos pra comparar. ajuste_mix não entra no
                # inventário rápido (ver inventario.py), então checamos
                # direto aqui, só nesse contexto específico (1 chamada
                # extra, não em toda renderização da página).
                ajuste_mix_existe_contexto = (
                    dl.carregar_ajuste_mix_salvo(storage, f"{loja_contexto}/{ano_mes_contexto}") is not None
                )

        chaves_arquivos = [
            chave for chave in _ROTULOS_ARQUIVOS_UPLOAD
            if chave not in ("retrato_vendas", "estoque_atualizado") or ajuste_mix_existe_contexto
        ]
        col_esq, col_dir = st.columns(2)
        colunas_alternadas = [col_esq if i % 2 == 0 else col_dir for i in range(len(chaves_arquivos))]
        arquivos_selecionados = {}
        for chave, coluna in zip(chaves_arquivos, colunas_alternadas):
            with coluna:
                extensoes_aceitas = [ext.lstrip(".") for ext in config.FILE_SPECS[chave]["extensions"]]
                rotulo_arquivo = _ROTULOS_ARQUIVOS_UPLOAD[chave]
                if arquivos_existentes_contexto is not None:
                    rotulo_arquivo = (
                        f"✅ {rotulo_arquivo}" if chave in arquivos_existentes_contexto
                        else f"⚠️ {rotulo_arquivo}"
                    )
                arquivos_selecionados[chave] = st.file_uploader(
                    rotulo_arquivo,
                    type=extensoes_aceitas,
                    key=f"upload_arquivo_{chave}",
                )

        if st.button("Enviar arquivos", type="primary", key="upload_botao_enviar"):
            arquivos_preenchidos = {
                chave: arquivo for chave, arquivo in arquivos_selecionados.items() if arquivo is not None
            }
            if not upload_loja.strip():
                st.warning("Preencha o código da loja antes de enviar.")
            elif not arquivos_preenchidos:
                st.warning("Selecione ao menos um arquivo antes de enviar.")
            else:
                # Ciclo (mês) normalmente é sempre o mês/ano atual no
                # momento do envio (ver item 4 do pedido de mudança
                # original: "organizado já de maneira automática
                # internamente, não deve aparecer para o usuário") — a
                # ÚNICA exceção é o contexto acima (botão "Upload" de um
                # cartão de ciclo já existente), que direciona o envio
                # pro mês daquele cartão em vez do atual, pra completar
                # exatamente aquele ciclo.
                ano_mes = ano_mes_contexto or date.today().strftime("%Y-%m")
                loja_limpa = upload_loja.strip()

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
                for chave, arquivo in arquivos_preenchidos.items():
                    nome_base = config.FILE_SPECS[chave]["basenames"][0]
                    extensao = Path(arquivo.name).suffix.lower()
                    caminho_destino = f"{loja_limpa}/{ano_mes}/{nome_base}{extensao}"
                    conteudo = arquivo.getvalue()
                    if storage.existe(caminho_destino):
                        conflitos[chave] = {"caminho": caminho_destino, "conteudo": conteudo}
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
                            retrato_vendas_enviado=retrato_enviado_agora,
                        )
                    except StorageError as e:
                        falhas.append(f"metadata.json: {e}")

                st.session_state["_upload_enviados_ok"] = enviados_ok
                st.session_state["_upload_falhas"] = falhas
                st.session_state["_upload_conflitos"] = conflitos
                st.session_state["_upload_ciclo_pendente"] = (loja_limpa, ano_mes)
                st.session_state["_upload_consultor_pendente"] = upload_consultor.strip()
                if colisoes_pendentes:
                    st.session_state["_upload_colisoes_pendentes"] = colisoes_pendentes

    if st.session_state.get("_upload_enviados_ok"):
        lista_html = "\n".join(f"- `{c}`" for c in st.session_state["_upload_enviados_ok"])
        st.success(f"Arquivo(s) enviado(s) com sucesso:\n{lista_html}")
    if st.session_state.get("_upload_falhas"):
        lista_falhas = "\n".join(f"- {f}" for f in st.session_state["_upload_falhas"])
        st.error(f"Falha ao enviar:\n{lista_falhas}")

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
                    _atualizar_metadata_ciclo(
                        storage, loja_pend, ano_mes_pend,
                        st.session_state.get("_upload_consultor_pendente", ""),
                        retrato_vendas_enviado=(chave == "retrato_vendas"),
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

def pagina_selecionar_loja() -> None:
    inventario_atual = _descobrir_inventario_cache(storage, st.session_state["versao_cache"])
    loja_em_foco = st.session_state.get("loja_em_foco")

    if loja_em_foco is None:
        # ---- Nível 1: lista de lojas ----
        styles.cabecalho("Selecionar loja")

        if not inventario_atual:
            st.info("Nenhuma loja encontrada ainda. Use a página **Upload** para enviar os primeiros arquivos.")
        else:
            # Consultor e Supervisora vêm da API do TI — dado OFICIAL de
            # cada loja (setores "Consultoria Interna" e "Supervisão" — ver
            # modules/lojas_api.py), em vez do metadata.json de cada envio
            # (que registra quem efetivamente fez o upload, podendo ser
            # outra pessoa: cobertura de férias, upload feito pela
            # supervisora etc.). Mesma lista cacheada já usada na página
            # Upload — não gera uma chamada extra à API.
            try:
                lojas_api_disponiveis = _carregar_lojas_api()
                erro_lojas_api_filtro = None
            except lojas_api.LojasAPIError as e:
                lojas_api_disponiveis = []
                erro_lojas_api_filtro = str(e)

            mapa_lojas_api_por_codigo = {loja.codigo: loja for loja in lojas_api_disponiveis}

            if erro_lojas_api_filtro:
                st.warning(
                    f"Não foi possível carregar a lista oficial de lojas (API do TI) — "
                    f"os filtros de Consultor e Supervisora ficam indisponíveis nesta "
                    f"sessão: {erro_lojas_api_filtro}."
                )

            # As 3 listas de opção são compostas só a partir de lojas que
            # JÁ TÊM algum envio (inventario_atual) — não faz sentido
            # oferecer, como filtro, um consultor cujas lojas ainda não
            # mandaram nada pra cá.
            codigos_com_dados = sorted(inventario_atual.keys())
            consultores_distintos = sorted({
                mapa_lojas_api_por_codigo[codigo].consultor
                for codigo in codigos_com_dados
                if codigo in mapa_lojas_api_por_codigo and mapa_lojas_api_por_codigo[codigo].consultor
            })
            supervisoras_distintas = sorted({
                mapa_lojas_api_por_codigo[codigo].supervisora
                for codigo in codigos_com_dados
                if codigo in mapa_lojas_api_por_codigo and mapa_lojas_api_por_codigo[codigo].supervisora
            })

            c_filtro_codigo, c_filtro_consultor, c_filtro_supervisora = st.columns(3)
            with c_filtro_codigo:
                filtro_codigo = st.selectbox("Código da loja", ["Todos"] + codigos_com_dados)
            with c_filtro_consultor:
                filtro_consultor = st.selectbox("Consultor", ["Todos"] + consultores_distintos)
            with c_filtro_supervisora:
                filtro_supervisora = st.selectbox("Supervisora", ["Todos"] + supervisoras_distintas)

            def _loja_bate_filtros(loja_codigo: str) -> bool:
                """Uma loja só aparece se bater com TODOS os filtros
                preenchidos (Todos = filtro não aplicado). Consultor e
                Supervisora exigem que a loja tenha correspondência na API
                — sem ela (erro_lojas_api_filtro, ou loja ausente na
                resposta), esses dois filtros simplesmente não encontram
                nada, o que é o comportamento correto (dado indisponível
                não deveria "passar" um filtro que o usuário pediu)."""
                if filtro_codigo != "Todos" and loja_codigo != filtro_codigo:
                    return False
                loja_api = mapa_lojas_api_por_codigo.get(loja_codigo)
                if filtro_consultor != "Todos" and (loja_api is None or loja_api.consultor != filtro_consultor):
                    return False
                if filtro_supervisora != "Todos" and (loja_api is None or loja_api.supervisora != filtro_supervisora):
                    return False
                return True

            lojas_visiveis = sorted(
                loja_codigo for loja_codigo in inventario_atual if _loja_bate_filtros(loja_codigo)
            )

            if not lojas_visiveis:
                st.info("Nenhuma loja encontrada para os filtros selecionados.")
            else:
                with st.container(border=True):
                    st.markdown('<span class="mdf-painel-marker"></span>', unsafe_allow_html=True)
                    for loja_codigo in lojas_visiveis:
                        n_ciclos = len(inventario_atual[loja_codigo])
                        with st.container():
                            st.markdown('<span class="mdf-row-marker"></span>', unsafe_allow_html=True)
                            c_nome, c_botao = st.columns([0.75, 0.25])
                            with c_nome:
                                st.markdown(f'<p class="mdf-produto-nome">Loja {loja_codigo}</p>', unsafe_allow_html=True)
                                rotulo_meses = "mês" if n_ciclos == 1 else "meses"
                                st.markdown(
                                    f'<p class="mdf-produto-meta">{n_ciclos} {rotulo_meses} disponível(is)</p>',
                                    unsafe_allow_html=True,
                                )
                            with c_botao:
                                if st.button("Ver", key=f"ver_loja_{loja_codigo}", width="stretch"):
                                    st.session_state["loja_em_foco"] = loja_codigo
                                    st.rerun()
    else:
        # ---- Nível 2: cartões de ciclo (mês) da loja escolhida ----
        styles.cabecalho(f"Loja {loja_em_foco}")

        # Botão único "← Voltar", discreto, no canto superior direito da
        # área de conteúdo — substitui os dois elementos redundantes que
        # existiam antes (um st.button() solto + um breadcrumb em
        # markdown). Posicionado via st.columns (coluna estreita à
        # direita), sem position:sticky/fixed — mesma cautela já adotada
        # em ajustes visuais anteriores do projeto para não repetir o
        # bug de clique que sticky já causou.
        c_espaco, c_voltar = st.columns([0.85, 0.15])
        with c_voltar:
            with st.container():
                st.markdown('<span class="mdf-botao-discreto-marker"></span>', unsafe_allow_html=True)
                if st.button("← Voltar", key="voltar_nivel1", width="stretch"):
                    st.session_state["loja_em_foco"] = None
                    st.rerun()
        st.markdown("&nbsp;", unsafe_allow_html=True)

        ciclos_da_loja = inventario_atual.get(loja_em_foco, {})
        meses_ordenados = sorted(ciclos_da_loja.keys(), reverse=True)

        n_colunas = 3
        # Grid de cartões: cada mês + 1 cartão extra ("Enviar novo mês"),
        # em fileiras de `n_colunas` — criar um st.columns() novo por
        # fileira (em vez de um único st.columns() reciclado por índice
        # % n_colunas) é o que garante o "quebra de linha" correto do
        # grid quando há mais itens do que colunas.
        itens_grid = list(meses_ordenados) + [None]  # None = cartão "Enviar novo mês"
        for inicio in range(0, len(itens_grid), n_colunas):
            grupo = itens_grid[inicio:inicio + n_colunas]
            cols = st.columns(n_colunas)
            for col, item in zip(cols, grupo):
                with col:
                    if item is None:
                        with st.container(border=True):
                            st.markdown('<span class="mdf-ciclo-novo-marker"></span>', unsafe_allow_html=True)
                            st.markdown(
                                f'<p class="mdf-ciclo-titulo">{styles.icone_mais_svg()} Enviar novo mês</p>',
                                unsafe_allow_html=True,
                            )
                            st.caption("para esta loja")
                            with st.container():
                                st.markdown('<span class="mdf-botao-discreto-marker"></span>', unsafe_allow_html=True)
                                if st.button("Ir para Upload", key="ir_upload_novo_mes", width="stretch"):
                                    # Pré-preenche o campo Loja (searchbox)
                                    # da página Upload — ver
                                    # "_upload_loja_prefill" ali, que lê e
                                    # descarta este valor para inicializar
                                    # o st_searchbox já com esta loja
                                    # selecionada. Mês NÃO é pré-definido
                                    # aqui de propósito — é sempre um mês
                                    # novo, então o envio usa o mês/ano
                                    # atual (comportamento padrão da
                                    # página Upload). Limpa qualquer
                                    # contexto de mês que tenha sobrado de
                                    # uma visita anterior via o botão
                                    # "Upload" de um cartão existente.
                                    st.session_state["_upload_loja_prefill"] = loja_em_foco
                                    st.session_state["_upload_ano_mes_contexto"] = None
                                    st.session_state["_upload_ano_mes_contexto_loja"] = None
                                    st.switch_page(PAGINA_UPLOAD)
                    else:
                        mes = item
                        info_ciclo = ciclos_da_loja[mes]
                        with st.container(border=True):
                            st.markdown('<span class="mdf-ciclo-card-marker"></span>', unsafe_allow_html=True)
                            st.markdown(f'<p class="mdf-ciclo-titulo">{styles.mes_legivel(mes)}</p>', unsafe_allow_html=True)
                            for chave_arquivo, rotulo in _ROTULOS_ARQUIVOS_UPLOAD.items():
                                presente = chave_arquivo in info_ciclo["arquivos"]
                                if presente:
                                    st.markdown(f'<p class="mdf-check-ok">{styles.icone_check_svg()} {rotulo}</p>', unsafe_allow_html=True)
                                else:
                                    st.markdown(f'<p class="mdf-check-falta">{styles.icone_x_svg()} {rotulo}</p>', unsafe_allow_html=True)
                            if st.button("Ir para análise", key=f"analise_{loja_em_foco}_{mes}", type="primary", width="stretch"):
                                st.session_state["loja_atual_analise"] = loja_em_foco
                                st.session_state["ciclo_atual_analise"] = f"{loja_em_foco}/{mes}"
                                st.session_state["metadata_atual_analise"] = info_ciclo["metadata"]
                                # Marca esta entrada na Análise como uma
                                # "nova visita" pro aviso de Modelo ausente
                                # (aba_gc) — ver comentário lá: sem isso o
                                # aviso, uma vez fechado, nunca mais
                                # reapareceria pra este ciclo mesmo que o
                                # consultor saia e volte.
                                st.session_state.pop("_gc_modelo_aviso_ciclo_visto", None)
                                st.switch_page(_pagina_analise_para_retomar())
                            with st.container():
                                st.markdown('<span class="mdf-botao-discreto-marker"></span>', unsafe_allow_html=True)
                                if st.button("Upload", key=f"upload_ciclo_{loja_em_foco}_{mes}", width="stretch"):
                                    # Reaproveita a página Upload de
                                    # sempre — pré-preenche Loja E define
                                    # um CONTEXTO de mês fixo (o deste
                                    # cartão, não necessariamente o atual),
                                    # pra completar exatamente este ciclo.
                                    # Ver _upload_ano_mes_contexto no
                                    # início da página Upload: enquanto
                                    # ele estiver definido, o envio grava
                                    # nesse mês (em vez do atual) e cada
                                    # campo de arquivo mostra se já tem
                                    # dado ou está faltando — mesma
                                    # informação do checklist acima.
                                    st.session_state["_upload_loja_prefill"] = loja_em_foco
                                    st.session_state["_upload_ano_mes_contexto"] = mes
                                    st.session_state["_upload_ano_mes_contexto_loja"] = loja_em_foco
                                    st.switch_page(PAGINA_UPLOAD)


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
    subtitulo_cabecalho = styles.montar_subtitulo(loja, ciclo_selecionado, metadata_ciclo)
    styles.cabecalho(subtitulo_cabecalho)
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
        mapa_df, estoque_df = _carregar_dados_loja(storage, ciclo_selecionado, st.session_state["versao_cache"])
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
            base_df = _carregar_base_nacional(storage, st.session_state["versao_cache"])
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
    análise" num cartão de ciclo) — retoma a última sub-página visitada
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
            # Inicializa o estado editável a partir do cálculo automático (1a vez
            # que esta loja/ciclo é aberta nesta sessão).
            st.session_state[chave_estado] = {
                row["chave_produto"]: row["quantidade"]
                for _, row in tabela_base.iterrows()
            }

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
                storage, ciclo_selecionado, "tela", st.session_state["versao_cache"]
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
                        storage, ciclo_selecionado, "impressao", st.session_state["versao_cache"]
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
        storage, ciclo_selecionado, "foto_antes", st.session_state["versao_cache"]
    )
    imagem_modelo, mime_modelo, msg_modelo = _carregar_preview_modelo(
        storage, ciclo_selecionado, "tela", st.session_state["versao_cache"]
    )
    imagem_depois, mime_depois, msg_depois = _carregar_imagem_conferencia(
        storage, ciclo_selecionado, "foto_depois", st.session_state["versao_cache"]
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
    # Só existe a partir do momento em que há um ajuste de mix
    # salvo (é a lista base dela — ver montar_tabela_resultado_
    # acao) — carregado de novo aqui, independente da aba_gc, pra
    # esta seção funcionar mesmo que a Sugestão de GC não tenha
    # sido aberta nesta mesma execução (abas do Streamlit não
    # compartilham escopo de execução entre si de forma confiável
    # quando há erro em outra aba).
    ajuste_salvo_conf = dl.carregar_ajuste_mix_salvo(storage, ciclo_selecionado)
    if ajuste_salvo_conf and ajuste_salvo_conf.get("produtos"):
        st.markdown("&nbsp;", unsafe_allow_html=True)
        st.markdown('<p class="mdf-secao-titulo">Resultado da ação</p>', unsafe_allow_html=True)

        if mapa_df is None:
            st.error(erro_dados or "Não foi possível carregar o Mapa da Farmácia para calcular o resultado da ação.")
        else:
            erro_resultado = None
            dados_resultado = None
            try:
                dados_resultado = _carregar_dados_resultado_acao(
                    storage, ciclo_selecionado, st.session_state["versao_cache"]
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
                st.info(
                    f"Envie {' e '.join(faltando)} na página Upload (pelo botão \"Upload\" "
                    f"deste ciclo, em Selecionar Loja) para ver o resultado da ação aqui."
                )
            else:
                tabela_resultado = dl.montar_tabela_resultado_acao(
                    mapa_df,
                    ajuste_salvo_conf,
                    dados_resultado["estoque_resultado_df"],
                    dados_resultado["retrato_df"],
                    dados_resultado["estoque_atualizado_df"],
                )
                mes_antes_legivel = styles.mes_legivel(ciclo_selecionado.split("/")[-1])
                # "retrato_vendas_ano_mes" é carimbado automaticamente no
                # metadata.json no momento do upload (ver
                # _atualizar_metadata_ciclo) — fallback pro mês atual só
                # cobre o caso raro de um ciclo antigo, enviado antes
                # dessa feature existir, sem o carimbo.
                ano_mes_depois = (metadata_ciclo or {}).get("retrato_vendas_ano_mes") or date.today().strftime("%Y-%m")
                mes_depois_legivel = styles.mes_legivel(ano_mes_depois)
                _renderizar_resultado_acao(tabela_resultado, mes_antes_legivel, mes_depois_legivel, ciclo_selecionado)


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
# estar fresco. Não chama st.cache_data.clear() (isso limparia também o
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
                        # Navegação "normal" pra Upload (não veio do botão
                        # "Upload" de um cartão de ciclo específico) —
                        # garante que não sobra contexto de mês de uma
                        # visita anterior (ver _upload_ano_mes_contexto na
                        # página Upload).
                        st.session_state["_upload_ano_mes_contexto"] = None
                        st.session_state["_upload_ano_mes_contexto_loja"] = None
                        st.switch_page(PAGINA_UPLOAD)
                    elif grupo_item == "Selecionar Loja":
                        st.switch_page(PAGINA_SELECIONAR_LOJA)
                    else:
                        st.switch_page(_pagina_analise_para_retomar())

pg.run()
