"""
Mapa da Farmácia — aplicação principal (Streamlit).

Navegação: 3 páginas na barra lateral (Upload / Selecionar Loja /
Análise), escolhidas via st.session_state["pagina_atual"] — st.tabs() só é
usado DENTRO da página Análise, para as 3 sub-abas (Ajuste de Mix /
Sugestão de GC / Conferência).

O consultor não faz mais parte do caminho físico dos arquivos (estrutura
{Loja}/{AAAA-MM}/arquivo, sem consultor no caminho — não faz mais sentido
com o DigitalOcean Spaces, que não separa permissão por pasta como o
OneDrive fazia). Ele agora é só um metadado (metadata.json de cada
ciclo), usado como filtro na página Selecionar Loja.
"""

from __future__ import annotations

import io
import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import config
import modules.auth as auth
import modules.data_loader as dl
import modules.image_utils as image_utils
import modules.inventario as inventario
import modules.pdf_export as pdf_export
import modules.styles as styles
from modules.file_resolver import ArquivoObrigatorioAusenteError, localizar_arquivo
from modules.storage import StorageError, get_storage_client

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
# Carregamento de dados da loja/ciclo selecionado (usado só na página
# Análise)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=600, show_spinner="Carregando dados da loja...")
def _carregar_dados_loja(_storage, caminho_ciclo: str, versao_cache: int):
    arq_mapa = localizar_arquivo(_storage, caminho_ciclo, config.FILE_SPECS["mapa_farmacia"], "mapa_farmacia")
    mapa = dl.carregar_mapa_farmacia(io.BytesIO(arq_mapa.conteudo))

    arq_estoque = localizar_arquivo(_storage, caminho_ciclo, config.FILE_SPECS["estoque"], "estoque")
    estoque = dl.carregar_estoque(io.BytesIO(arq_estoque.conteudo))

    return mapa, estoque


@st.cache_data(ttl=3600, show_spinner="Carregando base nacional de demanda...")
def _carregar_base_nacional(_storage, versao_cache: int):
    arq_base = localizar_arquivo(_storage, config.BASE_NACIONAL_FOLDER_NAME, config.BASE_NACIONAL_SPEC, "base_mercado")
    return dl.carregar_base_nacional(io.BytesIO(arq_base.conteudo), extensao=arq_base.extensao)


@st.cache_data(ttl=600, show_spinner=False)
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


@st.cache_data(show_spinner="Gerando PDF...")
def _gerar_pdf_gc_cache(tabela_gc: pd.DataFrame, consultor: str, loja: str, subtitulo: str) -> bytes:
    """Cacheado pelo conteúdo da tabela — evita regerar o PDF (custa
    processamento de imagem + desenho vetorial) a cada rerun da Aba 2
    quando nada relevante mudou (ex.: só a busca em texto foi digitada,
    que nem afeta o conteúdo do PDF, gerado a partir da tabela completa)."""
    return pdf_export.gerar_pdf_sugestao_gc(tabela_gc, consultor, loja, subtitulo)


# ---------------------------------------------------------------------------
# metadata.json de um ciclo (loja/mês) — quem enviou por último, e quando.
# ---------------------------------------------------------------------------

def _atualizar_metadata_ciclo(storage, loja: str, ano_mes: str, consultor: str) -> None:
    """Cria ou atualiza (mesclando com o que já existir) o metadata.json
    do ciclo {loja}/{ano_mes}. Só sobrescreve o campo 'consultor' se um
    valor não-vazio foi informado neste envio — um envio sem consultor
    preenchido não apaga o consultor registrado por um envio anterior."""
    caminho = f"{loja}/{ano_mes}/{inventario.NOME_ARQUIVO_METADATA}"
    metadata_atual = {}
    try:
        metadata_atual = json.loads(storage.ler_arquivo_bytes(caminho))
    except (StorageError, ValueError):
        pass

    if consultor:
        metadata_atual["consultor"] = consultor
    metadata_atual["enviado_em"] = datetime.now().isoformat(timespec="seconds")

    conteudo = json.dumps(metadata_atual, ensure_ascii=False, indent=2).encode("utf-8")
    storage.escrever_arquivo_bytes(caminho, conteudo)


# Rótulos de exibição dos 5 tipos de arquivo por loja, na mesma ordem em
# que aparecem tanto no formulário de Upload quanto no checklist dos
# cartões de ciclo (página Selecionar Loja, Nível 2).
_ROTULOS_ARQUIVOS_UPLOAD = {
    "mapa_farmacia": "Mapa da Farmácia",
    "estoque": "Estoque",
    "modelo": "Modelo (planograma)",
    "foto_antes": "Foto Antes",
    "foto_depois": "Foto Depois",
}


# ---------------------------------------------------------------------------
# Navegação: 3 páginas na sidebar (Upload / Selecionar Loja / Análise) +
# botão Atualizar (com confirmação, inalterado). Substitui por completo a
# antiga navegação por Consultor > Loja > Ciclo — o inventário agora é
# descoberto de uma vez (ver acima), então a navegação virou uma tela de
# seleção visual (página Selecionar Loja), não mais um funil de
# selectboxes dependentes na sidebar.
# ---------------------------------------------------------------------------

PAGINAS_SIDEBAR = ["Upload", "Selecionar Loja", "Análise"]
_SLUGS_PAGINAS = {"Upload": "upload", "Selecionar Loja": "selecionar_loja", "Análise": "analise"}

# Botões espalhados pela página (ex.: "Abrir" num cartão de ciclo, "Enviar
# novo mês", os próprios itens do menu lateral abaixo) precisam poder
# mudar de página e dar rerun. Em vez de espalhar escritas diretas em
# st.session_state["pagina_atual"] pelo código (arriscado: um widget com
# key="pagina_atual" instanciado antes no mesmo ciclo bloquearia a
# escrita), todos eles escrevem numa chave "pendente" separada
# (_pagina_solicitada), aplicada de fato bem aqui, ANTES de qualquer
# widget de página ser instanciado — o mesmo padrão já usado para
# pré-preencher o campo "Loja" da página Upload a partir do cartão
# "Enviar novo mês".
if "_pagina_solicitada" in st.session_state:
    st.session_state["pagina_atual"] = st.session_state.pop("_pagina_solicitada")

st.session_state.setdefault("pagina_atual", "Selecionar Loja")
st.session_state.setdefault("versao_cache", 0)

with st.sidebar:
    # Menu de páginas como 3 botões empilhados (não st.radio nativo) —
    # cada um envolto num st.container() com um marcador CSS que indica
    # se é a página ATIVA (preenchido navy) ou INATIVA (neutro claro),
    # seguindo o mesmo padrão de marcador+:has() já usado no resto do
    # projeto (ver modules/styles.py). Clicar num item que já é o ativo
    # não faz nada (evita reescrever session_state/rerun à toa).
    for pagina_item in PAGINAS_SIDEBAR:
        pagina_ativa = pagina_item == st.session_state["pagina_atual"]
        marcador_nav = "mdf-navitem-ativo-marker" if pagina_ativa else "mdf-botao-discreto-marker"
        with st.container():
            st.markdown(f'<span class="{marcador_nav}"></span>', unsafe_allow_html=True)
            if st.button(pagina_item, key=f"nav_{_SLUGS_PAGINAS[pagina_item]}", width="stretch"):
                if not pagina_ativa:
                    st.session_state["_pagina_solicitada"] = pagina_item
                    st.rerun()

    st.divider()
    if st.button("🔄 Atualizar", use_container_width=True):
        st.session_state["_confirmar_atualizar"] = True

    if st.session_state.get("_confirmar_atualizar"):
        st.warning("Tem certeza? Isso recarrega os dados do armazenamento e pode sobrescrever edições não salvas.")
        c1, c2 = st.columns(2)
        if c1.button("Sim", use_container_width=True):
            st.session_state["versao_cache"] += 1
            st.session_state["_confirmar_atualizar"] = False
            st.cache_data.clear()
            st.rerun()
        if c2.button("Não", use_container_width=True):
            st.session_state["_confirmar_atualizar"] = False
            st.rerun()

pagina_atual = st.session_state["pagina_atual"]


# ---------------------------------------------------------------------------
# Página: Upload
# ---------------------------------------------------------------------------

if pagina_atual == "Upload":
    styles.cabecalho("Envio de arquivos")

    st.markdown("#### Envio de arquivos por loja")
    # PENDÊNCIA CONHECIDA: o código da loja é digitado livremente (sem
    # validação contra uma lista) porque ainda não existe uma fonte de
    # verdade para "quais lojas existem" — isso deve vir, no futuro, de
    # uma API do TI que mantém a lista real de lojas ativas na rede
    # (ainda sem documentação técnica disponível). Ver README.md.
    st.caption(
        "O código da loja ainda é digitado livremente — a lista oficial de "
        "lojas ativas ainda não está disponível via API do TI (pendência "
        "conhecida, ver README). O consultor não faz mais parte do caminho "
        "do arquivo — é salvo junto do envio (metadata.json do ciclo) e "
        "serve de filtro na página **Selecionar Loja**. Depois de enviar, "
        "clique em **Atualizar** na barra lateral para que os arquivos "
        "apareçam nas outras páginas."
    )

    with st.container(border=True):
        st.markdown('<span class="mdf-painel-form-marker"></span>', unsafe_allow_html=True)

        c_consultor, c_loja, c_mes = st.columns(3)
        with c_consultor:
            upload_consultor = st.text_input("Consultor", key="upload_consultor")
        with c_loja:
            upload_loja = st.text_input("Loja (código)", key="upload_loja")
        with c_mes:
            # Só o mês/ano importa aqui — o dia escolhido é descartado (ver
            # abaixo, upload_mes_ano.strftime("%Y-%m")). Formato "AAAA-MM"
            # para a subpasta de mês (ver config.py / README).
            upload_mes_ano = st.date_input(
                "Mês/ano",
                value=date.today(),
                format="YYYY/MM/DD",
                help="Só o mês e o ano são usados — o dia escolhido é ignorado.",
                key="upload_mes_ano",
            )

        st.markdown("&nbsp;", unsafe_allow_html=True)

        chaves_arquivos = list(_ROTULOS_ARQUIVOS_UPLOAD)
        col_esq, col_dir = st.columns(2)
        colunas_alternadas = [col_esq, col_dir, col_esq, col_dir, col_esq]
        arquivos_selecionados = {}
        for chave, coluna in zip(chaves_arquivos, colunas_alternadas):
            with coluna:
                extensoes_aceitas = [ext.lstrip(".") for ext in config.FILE_SPECS[chave]["extensions"]]
                arquivos_selecionados[chave] = st.file_uploader(
                    _ROTULOS_ARQUIVOS_UPLOAD[chave],
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
                ano_mes = upload_mes_ano.strftime("%Y-%m")
                loja_limpa = upload_loja.strip()

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
                falhas = []
                for chave, info in sem_conflito.items():
                    try:
                        storage.escrever_arquivo_bytes(info["caminho"], info["conteudo"])
                        enviados_ok.append(info["caminho"])
                    except StorageError as e:
                        falhas.append(f"{_ROTULOS_ARQUIVOS_UPLOAD[chave]}: {e}")

                if enviados_ok:
                    try:
                        _atualizar_metadata_ciclo(storage, loja_limpa, ano_mes, upload_consultor.strip())
                    except StorageError as e:
                        falhas.append(f"metadata.json: {e}")

                st.session_state["_upload_enviados_ok"] = enviados_ok
                st.session_state["_upload_falhas"] = falhas
                st.session_state["_upload_conflitos"] = conflitos
                st.session_state["_upload_ciclo_pendente"] = (loja_limpa, ano_mes)
                st.session_state["_upload_consultor_pendente"] = upload_consultor.strip()

    if st.session_state.get("_upload_enviados_ok"):
        lista_html = "\n".join(f"- `{c}`" for c in st.session_state["_upload_enviados_ok"])
        st.success(f"Arquivo(s) enviado(s) com sucesso:\n{lista_html}")
    if st.session_state.get("_upload_falhas"):
        lista_falhas = "\n".join(f"- {f}" for f in st.session_state["_upload_falhas"])
        st.error(f"Falha ao enviar:\n{lista_falhas}")

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
                    )
                    st.toast(f"{_ROTULOS_ARQUIVOS_UPLOAD[chave]} substituído.", icon="✅")
                except StorageError as e:
                    st.error(f"Falha ao substituir {_ROTULOS_ARQUIVOS_UPLOAD[chave]}: {e}")
                del st.session_state["_upload_conflitos"][chave]
                st.rerun()
            if c2.button("Não", key=f"upload_conflito_nao_{chave}"):
                del st.session_state["_upload_conflitos"][chave]
                st.rerun()

    st.divider()
    st.markdown("#### Base nacional de demanda (atualização mensal)")
    with st.expander("O que é isso?"):
        st.caption(
            "Base compartilhada de demanda de mercado, usada por todas as lojas "
            "(diferente da seção acima, que é por loja). Normalmente passa por "
            "um pré-processamento local antes do envio (ver "
            "scripts/preparar_base_nacional.py no README), gerando a versão "
            "'.parquet' — mais rápida de carregar. Enviar a planilha '.xlsx' "
            "bruta também funciona, como alternativa mais lenta."
        )

    with st.container(border=True):
        st.markdown('<span class="mdf-painel-form-marker"></span>', unsafe_allow_html=True)
        arquivo_base_nacional = st.file_uploader(
            "Arquivo da base nacional (.xlsx ou .parquet)",
            type=["xlsx", "parquet"],
            key="upload_base_nacional",
        )
        if st.button("Enviar base nacional", type="primary", key="upload_botao_base"):
            if arquivo_base_nacional is None:
                st.warning("Selecione um arquivo antes de enviar.")
            else:
                extensao = Path(arquivo_base_nacional.name).suffix.lower()
                nome_base = config.BASE_NACIONAL_SPEC["basenames"][0]
                caminho_destino = f"{config.BASE_NACIONAL_FOLDER_NAME}/{nome_base}{extensao}"
                try:
                    storage.escrever_arquivo_bytes(caminho_destino, arquivo_base_nacional.getvalue())
                    st.success(f"Base nacional enviada com sucesso: `{caminho_destino}`")
                except StorageError as e:
                    st.error(f"Falha ao enviar: {e}")


# ---------------------------------------------------------------------------
# Página: Selecionar Loja
# ---------------------------------------------------------------------------

elif pagina_atual == "Selecionar Loja":
    inventario_atual = _descobrir_inventario_cache(storage, st.session_state["versao_cache"])
    loja_em_foco = st.session_state.get("loja_em_foco")

    if loja_em_foco is None:
        # ---- Nível 1: lista de lojas ----
        styles.cabecalho("Selecionar loja")

        if not inventario_atual:
            st.info("Nenhuma loja encontrada ainda. Use a página **Upload** para enviar os primeiros arquivos.")
        else:
            consultores_distintos = sorted({
                ciclo_info["metadata"]["consultor"]
                for ciclos in inventario_atual.values()
                for ciclo_info in ciclos.values()
                if ciclo_info.get("metadata") and ciclo_info["metadata"].get("consultor")
            })
            filtro_consultor = st.selectbox("Consultor", ["Todos"] + consultores_distintos)

            lojas_visiveis = sorted(
                loja_codigo
                for loja_codigo, ciclos in inventario_atual.items()
                if filtro_consultor == "Todos"
                or any((c.get("metadata") or {}).get("consultor") == filtro_consultor for c in ciclos.values())
            )

            if not lojas_visiveis:
                st.info(f"Nenhuma loja encontrada para o consultor {filtro_consultor}.")
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
                                    st.session_state["upload_loja"] = loja_em_foco
                                    st.session_state["_pagina_solicitada"] = "Upload"
                                    st.rerun()
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
                            if st.button("Abrir", key=f"abrir_{loja_em_foco}_{mes}", type="primary", width="stretch"):
                                st.session_state["loja_atual_analise"] = loja_em_foco
                                st.session_state["ciclo_atual_analise"] = f"{loja_em_foco}/{mes}"
                                st.session_state["metadata_atual_analise"] = info_ciclo["metadata"]
                                st.session_state["_pagina_solicitada"] = "Análise"
                                st.rerun()


# ---------------------------------------------------------------------------
# Página: Análise (Ajuste de Mix / Sugestão de GC / Conferência)
# ---------------------------------------------------------------------------

elif pagina_atual == "Análise":
    loja = st.session_state.get("loja_atual_analise")
    ciclo_selecionado = st.session_state.get("ciclo_atual_analise")
    metadata_ciclo = st.session_state.get("metadata_atual_analise")

    if ciclo_selecionado is None:
        styles.cabecalho("Nenhuma loja selecionada")
        st.info("Nenhuma loja selecionada.")
        if st.button("Ir para Selecionar Loja"):
            st.session_state["_pagina_solicitada"] = "Selecionar Loja"
            st.rerun()
    else:
        subtitulo_cabecalho = styles.montar_subtitulo(loja, ciclo_selecionado, metadata_ciclo)
        styles.cabecalho(subtitulo_cabecalho)
        consultor = (metadata_ciclo or {}).get("consultor") or ""

        # ---- Carregamento de dados do ciclo selecionado — lógica interna
        # idêntica à de antes; erros aparecem dentro das sub-abas que
        # precisam desses dados (Ajuste de mix / Sugestão de GC), não
        # travam a página inteira (Conferência não depende deles). ----
        tabela_base = None
        mapa_df = None
        erro_dados = None

        try:
            mapa_df, estoque_df = _carregar_dados_loja(storage, ciclo_selecionado, st.session_state["versao_cache"])
        except ArquivoObrigatorioAusenteError as e:
            erro_dados = (
                f"Arquivo obrigatório **{e.chave}** não encontrado em `{e.pasta}`. "
                f"Sem esse arquivo, esta loja não pode ser processada — envie o arquivo "
                f"correspondente na página Upload e clique em Atualizar."
            )
        except dl.MultiplasLojasEstoqueError as e:
            erro_dados = (
                f"O arquivo de estoque contém mais de uma loja: **{', '.join(e.ids_encontrados)}**. "
                f"Verifique o arquivo enviado — ele deve conter dados de uma única loja."
            )
        except dl.PlanilhaInvalidaError as e:
            erro_dados = f"Não foi possível interpretar uma das planilhas: {e}"

        if erro_dados is None:
            try:
                base_df = _carregar_base_nacional(storage, st.session_state["versao_cache"])
            except ArquivoObrigatorioAusenteError:
                erro_dados = (
                    f"A base nacional de demanda não foi encontrada na pasta "
                    f"`{config.BASE_NACIONAL_FOLDER_NAME}`. Sem ela, não é possível calcular "
                    f"a demanda de mercado dos produtos."
                )

        if erro_dados is None:
            tabela_base = dl.montar_tabela_ajuste_mix(mapa_df, estoque_df, base_df)

        aba_mix, aba_gc, aba_conf = st.tabs(["Ajuste de mix", "Sugestão de GC", "Conferência"])

        with aba_mix:
            if erro_dados:
                st.error(erro_dados)
            else:
                chave_estado = f"ajuste_mix::{loja}::{ciclo_selecionado}"

                if chave_estado not in st.session_state:
                    # Inicializa o estado editável a partir do cálculo automático (1a vez
                    # que esta loja/ciclo é aberta nesta sessão).
                    st.session_state[chave_estado] = {
                        row["ean_original"] if not row["ean_valido"] else row["ean"]: row["quantidade"]
                        for _, row in tabela_base.iterrows()
                    }

                quantidades_editadas = st.session_state[chave_estado]

                # Os widgets de quantidade já editados nesta interação têm seu valor
                # atualizado em st.session_state antes deste script rodar — sincroniza
                # o dict aqui para que a contagem/confirmação e o status "alterado" de
                # cada produto reflitam a edição mais recente, mesmo antes do loop de
                # renderização (mais abaixo) rodar.
                for _, row in tabela_base.iterrows():
                    chave_produto = row["ean_original"] if not row["ean_valido"] else row["ean"]
                    chave_widget = f"qtd::{chave_estado}::{chave_produto}"
                    if chave_widget in st.session_state:
                        quantidades_editadas[chave_produto] = st.session_state[chave_widget]

                qtd_preenchidos = sum(1 for v in quantidades_editadas.values() if v and v > 0)

                def _status_atual_linha(row):
                    chave_produto = row["ean_original"] if not row["ean_valido"] else row["ean"]
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
                            st.markdown(f"- **{row['produto']}** — posição {row['posicao']}, EAN cadastrado: `{row['ean_original']}`")

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

                with st.container(border=True):
                    st.markdown('<span class="mdf-painel-marker"></span>', unsafe_allow_html=True)

                    if len(tabela_exibida) == 0:
                        st.caption("Nenhum produto encontrado para esta busca.")

                    for _, row in tabela_exibida.iterrows():
                        chave_produto = row["ean_original"] if not row["ean_valido"] else row["ean"]
                        with st.container():
                            st.markdown('<span class="mdf-row-marker"></span>', unsafe_allow_html=True)
                            c1, c2, c3 = st.columns([0.08, 0.66, 0.26])
                            with c1:
                                posicao_label = row["posicao"] if pd.notna(row["posicao"]) else "–"
                                st.markdown(f'<div class="mdf-tag">{posicao_label}</div>', unsafe_allow_html=True)
                            with c2:
                                badge_html = styles.badge_status(row["status_atual"])
                                st.markdown(f'<p class="mdf-produto-nome">{row["produto"]}</p>', unsafe_allow_html=True)
                                st.markdown(f'<p class="mdf-produto-meta">{badge_html}</p>', unsafe_allow_html=True)
                            with c3:
                                valor_atual = quantidades_editadas.get(chave_produto, row["quantidade"])
                                nova_qtd = st.number_input(
                                    "Quantidade",
                                    min_value=0,
                                    value=int(valor_atual) if pd.notna(valor_atual) else 0,
                                    step=1,
                                    key=f"qtd::{chave_estado}::{chave_produto}",
                                    label_visibility="collapsed",
                                )
                                quantidades_editadas[chave_produto] = nova_qtd

        with aba_gc:
            if erro_dados:
                st.error(erro_dados)
            else:
                ajuste_salvo = dl.carregar_ajuste_mix_salvo(storage, ciclo_selecionado)

                if ajuste_salvo is None:
                    st.info(
                        "Nenhum ajuste de mix salvo ainda para esta loja. Vá para a aba "
                        "'Ajuste de mix' e salve primeiro."
                    )
                else:
                    tabela_gc = dl.montar_tabela_sugestao_gc(mapa_df, ajuste_salvo)

                    produtos_nao_localizados_gc = tabela_gc[~tabela_gc["ean_valido"]]
                    if len(produtos_nao_localizados_gc) > 0:
                        with st.expander(f"⚠️ {len(produtos_nao_localizados_gc)} produto(s) com EAN não localizado — ver detalhes"):
                            st.caption(
                                "Estes produtos continuam na lista abaixo e podem ser preenchidos manualmente. "
                                "O EAN cadastrado na origem não pôde ser cruzado com a base de demanda/estoque."
                            )
                            for _, row in produtos_nao_localizados_gc.iterrows():
                                st.markdown(f"- **{row['produto']}** — posição {row['posicao']}, EAN cadastrado: `{row['ean_original']}`")

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
                        pdf_bytes = _gerar_pdf_gc_cache(tabela_gc, consultor, loja, subtitulo_cabecalho)
                        st.download_button(
                            "📄 Exportar PDF",
                            data=pdf_bytes,
                            file_name=f"Ajuste_Mix_RMC_{loja}.pdf",
                            mime="application/pdf",
                            type="primary",
                            width="stretch",
                        )

                    mask_busca_gc = tabela_gc.apply(
                        lambda row: styles.corresponde_busca(termo_busca_gc, row["produto"], row["ean_original"], row["posicao"]),
                        axis=1,
                    )
                    tabela_gc_exibida = tabela_gc[mask_busca_gc]

                    with st.container(border=True):
                        st.markdown('<span class="mdf-painel-marker"></span>', unsafe_allow_html=True)

                        if len(tabela_gc_exibida) == 0:
                            st.caption("Nenhum produto encontrado para esta busca.")

                        for _, row in tabela_gc_exibida.iterrows():
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

        with aba_conf:
            imagem_antes, mime_antes, msg_antes = _carregar_imagem_conferencia(
                storage, ciclo_selecionado, "foto_antes", st.session_state["versao_cache"]
            )
            imagem_modelo, mime_modelo, msg_modelo = _carregar_imagem_conferencia(
                storage, ciclo_selecionado, "modelo", st.session_state["versao_cache"]
            )
            imagem_depois, mime_depois, msg_depois = _carregar_imagem_conferencia(
                storage, ciclo_selecionado, "foto_depois", st.session_state["versao_cache"]
            )

            c_antes, c_modelo, c_depois = st.columns(3, gap="medium")
            with c_antes:
                styles.cartao_foto("Antes", imagem_antes, mime_antes, msg_antes)
            with c_modelo:
                styles.cartao_foto("Modelo", imagem_modelo, mime_modelo, msg_modelo)
            with c_depois:
                styles.cartao_foto("Depois", imagem_depois, mime_depois, msg_depois)
