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
import modules.lojas_api as lojas_api
import modules.pdf_export as pdf_export
import modules.styles as styles
from modules.file_resolver import ArquivoObrigatorioAusenteError, localizar_arquivo
from modules.storage import StorageError, get_storage_client
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
# Navegação: 3 páginas na sidebar (Upload / Selecionar Loja / Análise).
# Substitui por completo a antiga navegação por Consultor > Loja > Ciclo — o
# inventário agora é descoberto de uma vez (ver acima), então a navegação
# virou uma tela de seleção visual (página Selecionar Loja), não mais um
# funil de selectboxes dependentes na sidebar.
# ---------------------------------------------------------------------------

PAGINAS_SIDEBAR = ["Upload", "Selecionar Loja", "Análise"]
_SLUGS_PAGINAS = {"Upload": "upload", "Selecionar Loja": "selecionar_loja", "Análise": "analise"}

# Páginas que trabalham em cima do inventário/API (ao invés de só formulário
# de envio) — entrar nelas dispara uma atualização automática dos dados (ver
# abaixo), pra tirar do usuário o esforço manual de lembrar de apertar um
# botão "Atualizar" antes de analisar uma loja.
_PAGINAS_COM_ATUALIZACAO_AUTOMATICA = ("Selecionar Loja", "Análise")

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
#
# Atualização automática: antes existia um botão manual "Atualizar" na
# sidebar (com confirmação) que só bumpava versao_cache quando o usuário
# lembrava de clicar — removido porque o usuário pode esquecer e analisar
# dados desatualizados sem perceber. Em vez disso, entrar em "Selecionar
# Loja" ou "Análise" (vindo de QUALQUER outra página, incluindo a primeira
# vez que a página é resolvida) bumpa versao_cache sozinho — essas duas
# páginas são as que realmente dependem do inventário estar fresco. Não
# chama st.cache_data.clear() (isso limparia também o cache da API do TI,
# que tem TTL próprio de 1h e não precisa ser forçado a cada navegação).
st.session_state.setdefault("pagina_atual", "Selecionar Loja")
st.session_state.setdefault("versao_cache", 0)

if "_pagina_solicitada" in st.session_state:
    _pagina_anterior = st.session_state["pagina_atual"]
    st.session_state["pagina_atual"] = st.session_state.pop("_pagina_solicitada")
    if (
        st.session_state["pagina_atual"] != _pagina_anterior
        and st.session_state["pagina_atual"] in _PAGINAS_COM_ATUALIZACAO_AUTOMATICA
    ):
        st.session_state["versao_cache"] += 1

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
                    # Navegação "normal" pra Upload (não veio do botão
                    # "Upload" de um cartão de ciclo específico) — garante
                    # que não sobra contexto de mês de uma visita anterior
                    # (ver _upload_ano_mes_contexto na página Upload).
                    st.session_state["_upload_ano_mes_contexto"] = None
                    st.session_state["_upload_ano_mes_contexto_loja"] = None
                    st.rerun()

pagina_atual = st.session_state["pagina_atual"]


# ---------------------------------------------------------------------------
# Página: Upload
# ---------------------------------------------------------------------------

if pagina_atual == "Upload":
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
        if ano_mes_contexto and loja_contexto:
            inventario_contexto = _descobrir_inventario_cache(storage, st.session_state["versao_cache"])
            ciclo_contexto = inventario_contexto.get(loja_contexto, {}).get(ano_mes_contexto)
            if ciclo_contexto:
                arquivos_existentes_contexto = ciclo_contexto.get("arquivos", {})

        chaves_arquivos = list(_ROTULOS_ARQUIVOS_UPLOAD)
        col_esq, col_dir = st.columns(2)
        colunas_alternadas = [col_esq, col_dir, col_esq, col_dir, col_esq]
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

    # Seção recolhida por padrão (só um item discreto, sem título em
    # destaque nem divider) — o conteúdo por dentro é o mesmo de sempre
    # (explicação + formulário de envio), só o ponto de entrada mudou pra
    # não competir visualmente com o envio por loja, que é o uso do dia a
    # dia desta página.
    with st.expander("Base nacional de demanda", expanded=False):
        st.caption(
            "Base compartilhada de demanda de mercado, usada por todas as lojas "
            "(diferente da seção acima, que é por loja). Normalmente passa por "
            "um pré-processamento local antes do envio (ver "
            "scripts/preparar_base_nacional.py no README), gerando a versão "
            "'.parquet' — mais rápida de carregar. Enviar a planilha '.xlsx' "
            "bruta também funciona, como alternativa mais lenta."
        )
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
                            if st.button("Ir para análise", key=f"analise_{loja_em_foco}_{mes}", type="primary", width="stretch"):
                                st.session_state["loja_atual_analise"] = loja_em_foco
                                st.session_state["ciclo_atual_analise"] = f"{loja_em_foco}/{mes}"
                                st.session_state["metadata_atual_analise"] = info_ciclo["metadata"]
                                st.session_state["_pagina_solicitada"] = "Análise"
                                st.rerun()
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
                                    st.session_state["_pagina_solicitada"] = "Upload"
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
