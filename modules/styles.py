"""
Identidade visual do Mapa da Farmácia, aplicada sobre os componentes
nativos do Streamlit via CSS injetado.

Importante: tudo aqui é CSS por cima de widgets NATIVOS do Streamlit
(st.container, st.columns, st.number_input, st.tabs, st.button) — não
há HTML solto fora do fluxo do app, conforme combinado. Isso garante que
o app funcione normalmente quando publicado no Streamlit Community
Cloud, sem depender de nenhum componente externo.
"""

import base64
import re
import unicodedata
from pathlib import Path

import streamlit as st

import config
from modules.lojas_api import formatar_consultor


@st.cache_resource(show_spinner=False)
def _logo_base64() -> str | None:
    """Lê o logo do disco e devolve em base64, para embutir direto no HTML
    do cabeçalho (evita depender de servir o arquivo como URL estática).
    Cacheado como recurso — só é lido uma vez por processo do servidor,
    não a cada rerun."""
    caminho = Path(config.LOGO_PATH)
    if not caminho.exists():
        return None
    return base64.b64encode(caminho.read_bytes()).decode("utf-8")


def aplicar_estilo() -> None:
    st.markdown(
        f"""
        <style>
        /* ---- Reset geral: tira a "cara de ferramenta genérica" ----
        Importante: NÃO esconder o <header> inteiro (visibility: hidden
        nele escondia também o botão de recolher/expandir a sidebar, que
        vive dentro do mesmo header — sem ele, quem recolhesse a sidebar
        ficava sem jeito de abri-la de novo). Escondemos só as partes que
        não queremos (menu hambúrguer, rodapé, botão de deploy, status de
        execução) e deixamos o header em si transparente, sem sumir. */
        #MainMenu, footer,
        [data-testid="stAppDeployButton"],
        [data-testid="stStatusWidget"] {{ visibility: hidden; }}
        header[data-testid="stHeader"] {{
            background: transparent;
            box-shadow: none;
        }}
        .block-container {{
            padding-top: 1.5rem;
            /* Largura padrão das telas (antes 1000px, que cortava os botões
            da lista de lojas). */
            max-width: 1280px;
        }}

        /* ---- Cabeçalho institucional ---- */
        .mdf-header {{
            background: {config.COR_NAVY};
            border-radius: 12px;
            padding: 18px 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 8px;
        }}
        .mdf-header-left {{ display: flex; align-items: center; gap: 14px; }}
        [data-testid="stMarkdownContainer"] .mdf-logo-badge {{
            width: 48px; height: 48px; border-radius: 50%;
            background: {config.COR_VERDE};
            display: flex; align-items: center; justify-content: center;
            font-weight: 700; color: {config.COR_NAVY}; font-size: 16px;
            flex-shrink: 0;
        }}
        .mdf-logo-img {{
            width: 48px; height: 48px; border-radius: 50%;
            object-fit: cover; flex-shrink: 0;
            background: #FFFFFF;
        }}
        [data-testid="stMarkdownContainer"] .mdf-header-title {{
            font-size: 28px; font-weight: 700; letter-spacing: -0.01em;
            color: #FFFFFF; margin: 0; line-height: 1.15;
        }}
        [data-testid="stMarkdownContainer"] .mdf-header-subtitle {{ font-size: 14px; color: {config.COR_TEXTO_SOBRE_NAVY}; margin: 2px 0 0 0; }}

        /* ---- Abas (st.tabs) — NÃO usado mais hoje (a página Análise
        virou 3 st.Page separadas por st.navigation/st.switch_page, com
        barra de sub-abas própria em CSS — ver bloco abaixo e app.py) —
        mantido só porque não custa nada deixar e algum outro st.tabs()
        pode voltar a existir no futuro. */
        .stTabs [data-baseweb="tab-list"] {{
            gap: 4px;
            border-bottom: 1px solid {config.COR_BORDA};
        }}
        .stTabs [data-baseweb="tab"] {{
            height: auto;
            padding: 10px 16px;
            font-size: 14px;
            font-weight: 500;
            color: {config.COR_TEXTO_MUTED};
        }}
        .stTabs [aria-selected="true"] {{
            color: {config.COR_NAVY} !important;
            border-bottom-color: {config.COR_VERDE} !important;
        }}

        /* ---- Barra de sub-abas da página Análise (Ajuste de mix /
        Sugestão de GC / Conferência) — desde a migração pra st.navigation/
        st.Page (ver app.py), essas 3 deixaram de ser um único
        st.segmented_control dentro do mesmo script e viraram 3 st.Page
        genuinamente separadas: cada "aba" agora é um st.button() próprio
        (dentro do seu st.container(key="mdf_aba_ativa_..." ou
        "mdf_aba_inativa_...") — mesmo padrão do nav da sidebar) que
        chama st.switch_page(). Visual buscando repetir o .stTabs do topo
        deste arquivo o mais próximo possível (linha inferior colorida na
        aba ativa), não precisa ser pixel-perfect. */
        .st-key-mdf_abas_analise {{
            border-bottom: 1px solid {config.COR_BORDA};
            margin-bottom: 8px;
        }}
        [class*="st-key-mdf_aba_ativa_"] button,
        [class*="st-key-mdf_aba_inativa_"] button {{
            height: auto;
            padding: 10px 16px;
            font-size: 14px;
            background: transparent !important;
            border: none !important;
            border-radius: 0 !important;
            border-bottom: 2px solid transparent !important;
            box-shadow: none !important;
        }}
        [class*="st-key-mdf_aba_inativa_"] button {{
            color: {config.COR_TEXTO_MUTED} !important;
            font-weight: 500 !important;
        }}
        [class*="st-key-mdf_aba_ativa_"] button {{
            color: {config.COR_NAVY} !important;
            font-weight: 600 !important;
            border-bottom-color: {config.COR_VERDE} !important;
        }}
        [class*="st-key-mdf_aba_ativa_"] button:hover,
        [class*="st-key-mdf_aba_ativa_"] button:focus-visible,
        [class*="st-key-mdf_aba_inativa_"] button:hover,
        [class*="st-key-mdf_aba_inativa_"] button:focus-visible {{
            color: {config.COR_NAVY} !important;
        }}

        /* PADRÃO DO PROJETO para estilizar um container: dar `key` a ele
        (st.container(key="mdf_...")) e mirar a classe .st-key-mdf_... que o
        Streamlit põe no próprio container. Não usar marcador invisível com
        :has(> div > div > ...): esse seletor conta níveis internos do
        Streamlit e quebrou (fundo dos cards menor que o conteúdo). */

        /* ---- Quadro único que agrupa toda a lista de produtos ----
        Altura fixa com rolagem própria (max-height + overflow-y), para que
        o botão Salvar e as barras de busca — que ficam FORA deste quadro,
        acima dele — permaneçam sempre visíveis sem precisar de position:
        sticky/fixed. Só a lista de produtos rola; o resto da página não. */
        [class*="st-key-mdf_painel_"] {{
            border-radius: 14px !important;
            border: 1px solid {config.COR_BORDA} !important;
            background: #FFFFFF;
            padding: 18px !important;
            max-height: {config.ALTURA_PAINEL_PRODUTOS_PX}px;
            overflow-y: auto;
        }}
        /* Barra de rolagem própria do quadro, sempre visível e discreta
        (o scrollbar padrão do Chrome é um traço quase imperceptível) —
        WebKit-only, mas cobre o navegador usado no projeto (Chrome/Edge). */
        [class*="st-key-mdf_painel_"]::-webkit-scrollbar {{
            width: 8px;
        }}
        [class*="st-key-mdf_painel_"]::-webkit-scrollbar-track {{
            background: transparent;
        }}
        [class*="st-key-mdf_painel_"]::-webkit-scrollbar-thumb {{
            background: #C9CFD8;
            border-radius: 4px;
        }}
        [class*="st-key-mdf_painel_"]::-webkit-scrollbar-thumb:hover {{
            background: #A9B2BF;
        }}

        /* ---- Navegação da sidebar (Upload / Selecionar Loja / Análise) ----
        Substitui o st.radio nativo por 3 st.button() empilhados — cada um
        envolvido por um st.container() com key mdf_nav_ativo_* OU
        mdf_discreto_nav_*, escolhida em Python conforme
        a página atual). Escopado a [data-testid="stSidebar"] DE PROPÓSITO:
        o objetivo é diferenciar só os 3 botões do menu — um seletor
        :has() sem esse escopo poderia, no futuro, pegar qualquer outro
        botão dentro de um st.container() em outro lugar do app. */
        [data-testid="stSidebar"] [class*="st-key-mdf_nav_ativo_"] button {{
            background: {config.COR_NAVY} !important;
            border-color: {config.COR_NAVY} !important;
            color: #FFFFFF !important;
            font-weight: 600 !important;
        }}
        [data-testid="stSidebar"] [class*="st-key-mdf_nav_ativo_"] button:hover,
        [data-testid="stSidebar"] [class*="st-key-mdf_nav_ativo_"] button:focus-visible {{
            background: {config.COR_NAVY_CLARO} !important;
            border-color: {config.COR_NAVY_CLARO} !important;
            color: #FFFFFF !important;
        }}

        /* ---- Botão discreto reaproveitável ----
        Usado pelos itens INATIVOS do menu lateral — fundo neutro claro, texto navy, sem
        preenchimento forte, pra não competir visualmente com as ações
        primárias (Abrir, Enviar, Salvar). Ao contrário da key
        "ativo" acima, este NÃO é escopado à sidebar — é a mesma
        linguagem visual "secundária" reaproveitada em qualquer lugar do
        app que precise dela. */
        [class*="st-key-mdf_discreto_"] button {{
            background: {config.COR_FUNDO_CARD} !important;
            border-color: {config.COR_FUNDO_CARD} !important;
            color: {config.COR_NAVY} !important;
            font-weight: 500 !important;
        }}
        [class*="st-key-mdf_discreto_"] button:hover,
        [class*="st-key-mdf_discreto_"] button:focus-visible {{
            background: {config.COR_BORDA} !important;
            border-color: {config.COR_BORDA} !important;
            color: {config.COR_NAVY} !important;
        }}

        /* ---- Cartão de painel SEM altura fixa (Aba Upload) ----
        Mesmo visual do quadro acima (cantos arredondados, borda sutil,
        padding) mas sem o max-height + overflow-y — aqui o conteúdo é um
        FORMULÁRIO (campos + botão de envio), não uma lista longa de itens
        que precisa rolar por dentro; com o max-height do outro painel,
        os campos de baixo (inclusive o botão "Enviar") ficavam cortados,
        só acessíveis por uma barra de rolagem interna pouco óbvia. */
        .st-key-mdf_form_upload {{
            border-radius: 14px !important;
            border: 1px solid {config.COR_BORDA} !important;
            background: #FFFFFF;
            padding: 18px !important;
        }}

        /* ---- Rótulo "Loja" desenhado por fora do streamlit-searchbox ----
        Complementa o bloco acima: o componente teria seu PRÓPRIO rótulo
        embutido (se recebesse label="Loja"), com espaçamento fixo até a
        caixa (não configurável via style_overrides) — diferente do
        espaçamento nativo do Streamlit entre rótulo e widget. Por isso o
        app.py chama st_searchbox(label=None) e desenha este rótulo à
        parte (ver o st.markdown logo antes da chamada). Os valores abaixo
        (font-size, cor, min-height, margin-bottom) foram extraídos do
        bundle do próprio Streamlit — não são uma estimativa — pra
        reproduzir o rótulo nativo com exatidão: fonte 0.875rem, cor
        #31333F (token bodyText do tema claro), min-height 1.5rem (24px,
        igual ao tema.fontSizes.xl) e margin-bottom 0.25rem (4px, igual a
        theme.spacing.twoXS). */
        [data-testid="stMarkdownContainer"] .mdf-campo-loja-label {{
            font-size: 14px;
            color: {config.COR_TEXTO};
            display: flex;
            flex-direction: row;
            align-items: center;
            min-height: 1.5rem;
            height: auto;
            margin-bottom: 0.25rem;
        }}
        /* O Streamlit insere um espaçamento padrão (1rem) entre elementos
        vizinhos dentro da mesma coluna — como o rótulo acima e o
        st_searchbox agora são DOIS elementos separados (e não um só
        widget nativo, que já embute rótulo + caixa como uma unidade),
        esse espaçamento se somaria ao margin-bottom do rótulo, deixando
        a caixa da Loja mais baixa que a do Consultor. Este seletor mira
        o elemento que envolve especificamente o iframe do searchbox (via
        o atributo title que o próprio componente usa internamente — ver
        main.js do streamlit-searchbox) e cancela esse espaçamento padrão
        com uma margem negativa do mesmo tamanho, deixando o
        margin-bottom do rótulo acima como a ÚNICA fonte de espaço entre
        rótulo e caixa — igual ao widget nativo.

        Na prática, o navegador colapsa a margin-bottom do rótulo (4px)
        junto com essa margem negativa em vez de somar as duas — cancelar
        o 1rem inteiro (-16px) zerava o espaço por completo, colando a
        caixa direto embaixo do texto "Loja" (medido: 0px de vão, contra
        4px do lado do Consultor). -12px é o valor que sobra exatamente os
        4px do margin-bottom do rótulo, medido lado a lado com o Consultor
        via automação de navegador (ver conversa) — não é um chute. */
        div[data-testid="stElementContainer"]:has(iframe[title="streamlit_searchbox.searchbox"]) {{
            margin-top: -12px !important;
        }}

        /* Cards de produto (linhas dentro do quadro) — sem borda própria,
        só um leve background para diferenciar cada linha. */
        [class*="st-key-mdf_produto_"] {{
            border-radius: 10px;
            background: {config.COR_FUNDO_CARD};
            padding: 8px 12px;
        }}

        /* Situação de cada arquivo no painel "Arquivos do mês" (Análise) */
        [data-testid="stMarkdownContainer"] .mdf-check-ok {{ font-size: 14px; color: {config.COR_VERDE_TEXTO}; margin: 2px 0; }}
        [data-testid="stMarkdownContainer"] .mdf-check-falta {{ font-size: 14px; color: {config.COR_AMBAR_TEXTO}; margin: 2px 0; }}

        /* Etiqueta de posição estilo "tag de prateleira" */
        [data-testid="stMarkdownContainer"] .mdf-tag {{
            background: {config.COR_NAVY};
            color: #FFFFFF;
            font-size: 12px;
            font-weight: 600;
            width: 30px;
            height: 34px;
            display: flex;
            align-items: center;
            justify-content: center;
            clip-path: polygon(0 0, 100% 0, 100% 70%, 50% 100%, 0 70%);
            margin-top: 6px;
        }}

        /* ---- Ajuste de mix: faixa do último salvamento ---- */
        .st-key-mdf_faixa_ajuste, .st-key-mdf_faixa_ajuste_pendente {{
            border-radius: 10px;
            padding: 8px 12px 8px 16px;
            background: {config.COR_FUNDO_CARD};
        }}
        .st-key-mdf_faixa_ajuste_pendente {{ background: #FAEEDA; }}
        .mdf-faixa-ajuste-texto {{ margin: 0; color: {config.COR_TEXTO}; font-weight: 600; }}
        .st-key-mdf_faixa_ajuste_pendente .mdf-faixa-ajuste-texto {{ color: {config.COR_AMBAR_TEXTO}; }}

        /* ---- Selecionar Loja: barra de meses e lista de lojas ---- */
        .st-key-mdf_barra_meses {{
            background: {config.COR_FUNDO_CAMPO};
            border-radius: 12px;
            padding: 8px;
        }}
        .mdf-barra-mes-rotulo {{ font-weight: 700; color: {config.COR_NAVY}; margin: 0; padding: 0 8px; }}
        .st-key-mdf_barra_meses button[kind="secondary"] {{
            background: transparent !important;
            border-color: transparent !important;
            color: {config.COR_TEXTO} !important;
        }}
        .st-key-mdf_barra_meses button[kind="secondary"]:hover {{ background: #FFFFFF !important; }}
        .st-key-mdf_barra_meses button[kind="primary"] {{
            background: {config.COR_NAVY} !important;
            border-color: {config.COR_NAVY} !important;
            color: #FFFFFF !important;
        }}
        .st-key-mdf_lista_lojas {{ gap: 0 !important; }}
        [class*="st-key-mdf_linha_loja_"] {{
            border-bottom: 1px solid {config.COR_BORDA_SUAVE};
            border-radius: 6px;
            padding: 6px 8px;
        }}
        .st-key-mdf_lista_lojas [data-testid="stLayoutWrapper"]:last-child > [class*="st-key-mdf_linha_loja_"] {{ border-bottom: 0; }}
        [class*="st-key-mdf_linha_loja_"]:hover {{ background: {config.COR_FUNDO_CARD}; }}
        /* O Markdown do Streamlit tem margem negativa embaixo (-1rem): o texto
        sai do centro em relação aos botões da mesma linha e a última linha
        vaza para fora do card que a envolve. Vale para todo HTML do app. */
        [data-testid="stMarkdownContainer"]:has(> [class*="mdf-"]) {{
            margin-bottom: 0 !important;
        }}
        .mdf-loja-cod {{ font-weight: 700; color: {config.COR_NAVY}; margin: 0; white-space: nowrap; }}
        .mdf-loja-razao {{ font-weight: 600; color: {config.COR_TEXTO}; margin: 0; overflow-wrap: anywhere; }}
        .mdf-loja-consultor {{ color: {config.COR_TEXTO_SECUNDARIO}; margin: 0; overflow-wrap: anywhere; }}
        .mdf-lista-apoio {{ color: {config.COR_TEXTO_SECUNDARIO}; margin: 0; font-size: 14px; }}
        .mdf-lista-vazia {{ color: {config.COR_TEXTO_SECUNDARIO}; margin: 0; padding: 32px 16px; text-align: center; }}
        [data-testid="stMarkdownContainer"] .mdf-loja-ultimo {{ margin: 0; color: {config.COR_TEXTO}; font-weight: 600; font-size: 14px; white-space: nowrap; }}
        [data-testid="stMarkdownContainer"] .mdf-loja-ultimo-rotulo {{
            display: block; color: {config.COR_TEXTO_MUTED}; font-weight: 500; font-size: 12px;
            text-transform: uppercase; letter-spacing: .04em; line-height: 1.2;
        }}

        /* Upload: a linha "200MB per file • XLSX" vem em inglês do Streamlit e
        não tem tradução; os formatos aceitos ficam na ajuda (?) do rótulo. */
        [data-testid="stFileUploaderDropzoneInstructions"] {{ display: none !important; }}

        /* ---- Selecionar Loja: pop-up "Selecione o mês" ---- */
        [data-testid="stMarkdownContainer"] .mdf-dialog-mes-loja {{ margin: -8px 0 4px; color: {config.COR_TEXTO_MUTED}; font-size: 14px; overflow-wrap: anywhere; }}
        [data-testid="stMarkdownContainer"] .mdf-dialog-mes-ano {{
            margin: 10px 0 2px; color: {config.COR_NAVY}; font-weight: 700; font-size: 14px;
            letter-spacing: .08em; border-bottom: 1px solid {config.COR_BORDA}; padding-bottom: 4px;
        }}
        [class*="st-key-mdf_card_mes_"] {{
            position: relative;
            background: {config.COR_FUNDO_CARD};
            border: 1px solid {config.COR_BORDA};
            border-radius: 12px;
            padding: 12px 14px;
            min-height: 96px;
            justify-content: center;
            transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease;
        }}
        [class*="st-key-mdf_card_mes_"]:hover {{
            border-color: {config.COR_NAVY};
            box-shadow: 0 4px 14px rgba(23, 55, 94, .12);
            transform: translateY(-1px);
        }}
        [class*="st-key-mdf_card_mes_"]:has(button:focus-visible) {{
            outline: 2px solid #4A6E90; outline-offset: 2px;
        }}
        [class*="st-key-mdf_card_mes_atual_"] {{
            background: {config.COR_NAVY};
            border-color: {config.COR_NAVY};
        }}
        [class*="st-key-mdf_card_mes_atual_"]:hover {{ box-shadow: 0 4px 16px rgba(141, 198, 63, .35); }}
        /* Botão invisível cobrindo o card inteiro: o card é o alvo do clique. */
        [class*="st-key-mdf_card_btn_"] {{ position: absolute; inset: 0; z-index: 2; margin: 0 !important; }}
        [class*="st-key-mdf_card_btn_"] button {{
            width: 100% !important; height: 100% !important; opacity: 0; cursor: pointer;
        }}
        .mdf-card-mes {{ display: flex; flex-direction: column; gap: 2px; }}
        .mdf-card-mes-topo {{ display: flex; align-items: center; justify-content: space-between; gap: 6px; }}
        [data-testid="stMarkdownContainer"] .mdf-card-mes-nome {{ font-size: 16px; font-weight: 700; color: {config.COR_NAVY}; line-height: 1.2; }}
        [data-testid="stMarkdownContainer"] .mdf-card-mes-ano {{ font-size: 12px; color: {config.COR_TEXTO_MUTED}; }}
        [data-testid="stMarkdownContainer"] .mdf-card-mes-selo {{
            background: #8DC63F; color: #FFFFFF; border-radius: 999px;
            padding: 1px 8px; font-size: 12px; font-weight: 700;
        }}
        [data-testid="stMarkdownContainer"] .mdf-card-mes-arquivos {{
            display: inline-flex; align-items: center; gap: 4px; margin-top: 6px;
            font-size: 12px; font-weight: 600; color: #27500A; align-self: flex-start;
        }}
        .mdf-card-mes-arquivos svg {{ color: #639922; }}
        .mdf-card-mes-arquivos.incompleto {{
            background: #FAEEDA; color: #854F0B; border-radius: 999px; padding: 1px 8px;
        }}
        .mdf-card-mes-arquivos.incompleto svg {{ color: #854F0B; }}
        [class*="st-key-mdf_card_mes_atual_"] .mdf-card-mes-nome {{ color: #FFFFFF; }}
        [class*="st-key-mdf_card_mes_atual_"] .mdf-card-mes-ano {{ color: {config.COR_TEXTO_SOBRE_NAVY}; }}
        [class*="st-key-mdf_card_mes_atual_"] .mdf-card-mes-arquivos {{ color: #FFFFFF; }}
        [class*="st-key-mdf_card_mes_atual_"] .mdf-card-mes-arquivos svg {{ color: #8DC63F; }}
        [class*="st-key-mdf_card_mes_atual_"] .mdf-card-mes-arquivos.incompleto {{ color: #854F0B; }}
        [class*="st-key-mdf_card_mes_atual_"] .mdf-card-mes-arquivos.incompleto svg {{ color: #854F0B; }}
        /* Ações da linha: os botões nunca encolhem nem cortam o texto; em tela
        estreita (menu lateral aberto) empilham em vez de invadir a coluna ao lado. */
        [class*="st-key-mdf_acoes_loja_"] {{ flex-wrap: wrap !important; justify-content: flex-end; row-gap: 6px; }}
        [class*="st-key-mdf_acoes_loja_"] > div {{ flex-shrink: 0 !important; }}
        [class*="st-key-analise_loja_"] button,
        [class*="st-key-upload_loja_"] button {{
            min-height: 32px !important;
            height: 32px !important;
            padding: 0 10px !important;
            border-radius: 6px !important;
            color: {config.COR_NAVY} !important;
            white-space: nowrap;
        }}
        [class*="st-key-analise_loja_"] button p,
        [class*="st-key-upload_loja_"] button p {{ font-size: 14px !important; font-weight: 600 !important; }}
        [class*="st-key-analise_loja_"] button {{
            background: #EAF0F7 !important;
            border-color: #EAF0F7 !important;
        }}
        [class*="st-key-analise_loja_"] button:hover {{
            background: #DCE6F2 !important;
            border-color: #DCE6F2 !important;
        }}
        [class*="st-key-upload_loja_"] button {{
            background: #FFFFFF !important;
            border: 1px solid #D5DBE3 !important;
        }}
        [class*="st-key-upload_loja_"] button:hover {{
            background: {config.COR_FUNDO_CAMPO} !important;
            border-color: #4A6E90 !important;
        }}

        [data-testid="stMarkdownContainer"] .mdf-produto-nome {{ font-size: 16px; color: {config.COR_TEXTO}; margin: 4px 0 0 0; }}
        .mdf-produto-info {{ display: flex; flex-direction: column; gap: 6px; align-items: flex-start; }}
        [data-testid="stMarkdownContainer"] .mdf-produto-info p {{ margin: 0; }}
        [data-testid="stMarkdownContainer"] .mdf-produto-meta {{ font-size: 12px; color: {config.COR_TEXTO_SECUNDARIO}; margin: 0; }}

        /* ---- Controles de paginação (Ajuste de mix / Sugestão de GC /
        Produto a produto) — texto central "Página X de Y". Os botões
        "← Anterior"/"Próxima →" ao lado são st.button() nativos, sem
        marcador especial (visual padrão do Streamlit já basta aqui). */
        [data-testid="stMarkdownContainer"] .mdf-paginacao-info {{
            text-align: center;
            font-size: 14px;
            color: {config.COR_TEXTO_MUTED};
            margin: 6px 0;
            line-height: 2.2;
        }}

        /* Quantidade em destaque (Aba 2 — Sugestão de GC): é o dado
        acionável da linha, precisa ser o elemento visualmente mais forte,
        maior contraste que o nome do produto. */
        [data-testid="stMarkdownContainer"] .mdf-produto-qtd-destaque {{
            font-size: 18px;
            font-weight: 700;
            color: {config.COR_NAVY};
            margin: 4px 0 0 0;
            text-align: right;
        }}

        /* Badges de status (sugestão automática / confirmado / sem estoque / não encontrado) */
        [data-testid="stMarkdownContainer"] .mdf-badge {{
            font-size: 12px; font-weight: 600; padding: 3px 8px;
            border-radius: 6px; display: inline-block;
        }}
        .mdf-badge-auto {{ background: {config.COR_AMBAR_BG}; color: {config.COR_AMBAR_TEXTO}; }}
        .mdf-badge-ok {{ background: {config.COR_VERDE_BG}; color: {config.COR_VERDE_TEXTO}; }}
        .mdf-badge-alterado {{ background: {config.COR_AZUL_BG}; color: {config.COR_AZUL_TEXTO}; }}
        .mdf-badge-sem-estoque {{ background: {config.COR_CINZA_BG}; color: {config.COR_CINZA_TEXTO}; }}
        .mdf-badge-nao-encontrado {{ background: {config.COR_BORDA_SUAVE}; color: {config.COR_TEXTO_MUTED}; }}
        .mdf-badge-frentes {{ background: {config.COR_ROXO_BG}; color: {config.COR_ROXO_TEXTO}; }}

        /* ---- Chips de grupo e situação (Resultado da ação). Vermelho =
        queda de venda (única exceção à convenção "nunca vermelho" do resto
        do projeto — pedido explícito pra dar mais destaque à queda). */
        [data-testid="stMarkdownContainer"] .mdf-chip {{
            display: inline-flex; align-items: center; gap: 3px;
            font-size: 12px; font-weight: 700; padding: 2px 9px;
            border-radius: 10px; white-space: nowrap;
        }}
        .mdf-chip-positivo {{ background: {config.COR_VERDE_BG}; color: {config.COR_VERDE_TEXTO}; }}
        .mdf-chip-neutro {{ background: {config.COR_CINZA_BG}; color: {config.COR_CINZA_TEXTO}; }}
        .mdf-chip-alerta {{ background: {config.COR_AMBAR_BG}; color: {config.COR_AMBAR_TEXTO}; }}
        .mdf-chip-negativo {{ background: {config.COR_VERMELHO_BG}; color: {config.COR_VERMELHO_TEXTO}; }}
        .mdf-chip-azul {{ background: {config.COR_AZUL_BG}; color: {config.COR_AZUL_TEXTO}; }}
        .mdf-chip-roxo {{ background: {config.COR_ROXO_BG}; color: {config.COR_ROXO_TEXTO}; }}
        .mdf-chip-laranja {{ background: {config.COR_LARANJA_BG}; color: {config.COR_LARANJA_TEXTO}; }}


        /* ---- Alerta de quantidade negativa (Ajuste de mix) — estoque veio
        negativo na planilha de origem (erro de digitação/exportação na loja).
        Junto com .mdf-chip-negativo (queda de venda), é a segunda exceção
        deliberada à convenção "nunca vermelho" do projeto — aqui o vermelho
        foi escolhido de propósito pra chamar mais atenção que o âmbar padrão,
        já que é sinal de dado inválido, não só de atenção normal. */
        [class*="st-key-mdf_produto_neg_"] [data-testid="stNumberInput"] button {{
            border-color: {config.COR_VERMELHO_TEXTO} !important;
            color: {config.COR_VERMELHO_TEXTO} !important;
        }}
        [class*="st-key-mdf_produto_neg_"] [data-testid="stNumberInput"] input {{
            color: {config.COR_VERMELHO_TEXTO} !important;
            font-weight: 700;
        }}
        [data-testid="stMarkdownContainer"] .mdf-qtd-alerta-icone {{
            color: {config.COR_VERMELHO_TEXTO};
            font-size: 16px;
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100%;
        }}

        /* ---- Resultado da ação (Aba 3 — Conferência). Mesma escala em toda
        a seção: espaço 4/8/12/16/24, fonte 12/14/16/20/28/44, cantos 12/999. Os
        textos em <p> precisam do seletor longo: o st.markdown define a fonte
        do <p> com prioridade maior que a de uma classe sozinha. */
        div[data-testid="stMarkdownContainer"] p.mdf-secao-titulo {{ font-size: 28px; font-weight: 700; color: {config.COR_NAVY}; line-height: 1.2; margin: 0; }}
        .mdf-ra-icone {{ width: 20px; height: 20px; flex: none; }}
        .mdf-ra-topo {{ display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 16px; margin: 0 0 16px 0; }}
        div[data-testid="stMarkdownContainer"] p.mdf-ra-subtitulo {{ margin: 0; font-size: 16px; color: {config.COR_TEXTO_SECUNDARIO}; }}
        .mdf-ra-periodo {{
            display: flex; gap: 12px; align-items: flex-start; padding: 12px 16px; background: #FFFFFF; border-radius: 12px;
            box-shadow: 0 1px 2px rgba(23,55,94,.06), 0 2px 8px rgba(23,55,94,.04); color: {config.COR_NAVY};
        }}
        [data-testid="stMarkdownContainer"] .mdf-ra-periodo dl {{ margin: 0; display: grid; grid-template-columns: auto auto; gap: 4px 12px; font-size: 14px; line-height: 1.4; }}
        [data-testid="stMarkdownContainer"] .mdf-ra-periodo dt {{ font-size: 12px; color: {config.COR_TEXTO_SECUNDARIO}; align-self: center; }}
        .mdf-ra-periodo dd {{ margin: 0; font-weight: 600; color: {config.COR_TEXTO}; }}
        .mdf-ra-cartao {{
            display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 32px; align-items: center;
            background: #FFFFFF; border-radius: 12px; padding: 24px; margin: 0 0 16px 0;
            box-shadow: 0 1px 2px rgba(23,55,94,.06), 0 2px 8px rgba(23,55,94,.04);
        }}
        .mdf-ra-principal {{ display: flex; flex-direction: column; gap: 8px; }}
        div[data-testid="stMarkdownContainer"] p.mdf-ra-rotulo {{ margin: 0; font-size: 12px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: {config.COR_TEXTO_SECUNDARIO}; }}
        div[data-testid="stMarkdownContainer"] p.mdf-ra-valor {{ margin: 0; font-size: 44px; font-weight: 700; line-height: 1.2; }}
        [data-testid="stMarkdownContainer"] .mdf-ra-valor small {{ font-size: 16px; font-weight: 600; color: {config.COR_TEXTO_SECUNDARIO}; }}
        div[data-testid="stMarkdownContainer"] p.mdf-ra-sem-base {{ font-size: 28px; color: {config.COR_NAVY}; }}
        div[data-testid="stMarkdownContainer"] p.mdf-ra-frase {{ margin: 0; font-size: 16px; color: {config.COR_TEXTO}; }}
        div[data-testid="stMarkdownContainer"] p.mdf-ra-apoio {{ margin: 0; font-size: 14px; color: {config.COR_TEXTO_SECUNDARIO}; }}
        div[data-testid="stMarkdownContainer"] p.mdf-ra-h2 {{ display: flex; align-items: center; gap: 8px; margin: 16px 0 4px 0; font-size: 20px; font-weight: 700; color: {config.COR_NAVY}; line-height: 1.3; }}
        .mdf-ra-positivo {{ color: {config.COR_VERDE_TEXTO}; }}
        .mdf-ra-negativo {{ color: {config.COR_VERMELHO_TEXTO}; }}
        .mdf-ra-suave {{ color: {config.COR_TEXTO_SECUNDARIO}; }}
        .mdf-ra-selos {{ display: flex; flex-wrap: wrap; gap: 8px; }}
        .mdf-chip.mdf-ra-selo {{ gap: 4px; padding: 4px 12px; border-radius: 999px; font-weight: 600; }}
        [data-testid="stMarkdownContainer"] .mdf-ra-selo b {{ font-size: 14px; }}
        .mdf-ra-comparacao {{ display: flex; flex-direction: column; gap: 16px; min-width: 0; font-variant-numeric: tabular-nums; }}
        [data-testid="stMarkdownContainer"] .mdf-ra-linha {{ display: grid; grid-template-columns: 56px 88px minmax(0, 1fr) 64px; align-items: center; gap: 12px; font-size: 14px; }}
        .mdf-ra-quando {{ font-weight: 600; color: {config.COR_TEXTO_SECUNDARIO}; }}
        .mdf-ra-rs {{ font-weight: 700; color: {config.COR_TEXTO}; text-align: right; }}
        .mdf-ra-un {{ color: {config.COR_TEXTO_SECUNDARIO}; text-align: right; }}
        .mdf-ra-trilho {{ display: block; height: 10px; background: {config.COR_CINZA_BG}; border-radius: 999px; overflow: hidden; }}
        .mdf-ra-trilho span {{ display: block; height: 100%; border-radius: 999px; }}
        div[data-testid="stMarkdownContainer"] p.mdf-ra-composicao {{ margin: 0; padding-top: 12px; border-top: 1px solid {config.COR_BORDA_SUAVE}; font-size: 14px; color: {config.COR_TEXTO_SECUNDARIO}; }}
        .mdf-ra-composicao b {{ color: {config.COR_TEXTO}; font-weight: 600; }}
        .mdf-ra-tabela-caixa {{ overflow-x: auto; background: #FFFFFF; border-radius: 12px; padding: 8px; margin: 8px 0; }}
        [data-testid="stMarkdownContainer"] .mdf-ra-tabela {{ width: 100%; min-width: 760px; border-collapse: collapse; font-size: 14px; font-variant-numeric: tabular-nums; }}
        [data-testid="stMarkdownContainer"] .mdf-ra-tabela th {{
            padding: 12px 16px; text-align: left; font-size: 12px; font-weight: 600; letter-spacing: .04em;
            text-transform: uppercase; color: {config.COR_TEXTO_SECUNDARIO}; border: 0; border-bottom: 1px solid {config.COR_BORDA}; white-space: nowrap;
        }}
        .mdf-ra-tabela td {{ padding: 12px 16px; border: 0; border-bottom: 1px solid {config.COR_BORDA_SUAVE}; vertical-align: middle; color: {config.COR_TEXTO}; }}
        .mdf-ra-tabela tr:last-child td {{ border-bottom: 0; }}
        .mdf-ra-tabela tbody tr:hover td {{ background: {config.COR_FUNDO_CARD}; }}
        .mdf-ra-tabela th.mdf-ra-num, .mdf-ra-tabela td.mdf-ra-num {{ text-align: right; white-space: nowrap; }}
        .mdf-ra-tabela td.mdf-ra-positivo {{ color: {config.COR_VERDE_TEXTO}; }}
        .mdf-ra-tabela td.mdf-ra-negativo {{ color: {config.COR_VERMELHO_TEXTO}; }}
        .mdf-ra-tabela td.mdf-ra-suave {{ color: {config.COR_TEXTO_SECUNDARIO}; }}
        .mdf-ra-produto {{ font-weight: 600; }}
        .mdf-ra-tabela td.mdf-ra-forte {{ font-weight: 700; }}
        div[data-testid="stMarkdownContainer"] p.mdf-ra-vazio {{ margin: 8px 0; padding: 32px 16px; text-align: center; font-size: 14px; color: {config.COR_TEXTO_SECUNDARIO}; background: #FFFFFF; border-radius: 12px; }}
        /* Dica ao passar o mouse (ou focar pelo teclado) */
        [data-testid="stMarkdownContainer"] .mdf-dica {{ position: relative; display: inline-block; cursor: help; font-size: 14px; font-weight: 400; color: {config.COR_TEXTO_SECUNDARIO}; letter-spacing: 0; }}
        .mdf-dica:focus-visible {{ outline: 2px solid {config.COR_AZUL_TEXTO}; outline-offset: 2px; border-radius: 999px; }}
        [data-testid="stMarkdownContainer"] .mdf-dica-caixa {{
            position: absolute; z-index: 20; top: calc(100% + 8px); left: 50%; transform: translateX(-50%); width: 240px;
            padding: 12px; border-radius: 12px; background: {config.COR_NAVY}; color: #FFFFFF;
            font-size: 12px; font-weight: 400; line-height: 1.5; text-transform: none; text-align: left; white-space: normal;
            box-shadow: 0 4px 16px rgba(23,55,94,.2); display: none;
        }}
        .mdf-dica-esquerda .mdf-dica-caixa {{ left: auto; right: 0; transform: none; }}
        .mdf-dica:hover .mdf-dica-caixa, .mdf-dica:focus .mdf-dica-caixa {{ display: block; }}

        /* ---- Cartões de foto (Aba 3 — Conferência) ----

        /* ---- Cartões de foto (Aba 3 — Conferência) ----
        Mesma linguagem visual do quadro de produtos (mdf_painel_*):
        cantos arredondados, borda sutil — com uma sombra leve por cima,
        já que aqui cada cartão é uma peça isolada (não um quadro único
        agrupando várias linhas) e se beneficia de uma elevação discreta.

        A imagem em si é um st.image() NATIVO (não <img> em HTML solto),
        pra ganhar o ícone de tela cheia do próprio Streamlit — a moldura
        é aplicada por FORA dele, no st.container() que o envolve, via o
        key do container (mdf_foto_*), como os demais. Altura FIXA (não min-height)
        pra os 3 cartões ficarem sempre alinhados entre si, tenham ou não
        imagem — a foto usa object-fit: contain dentro dessa altura. */
        [data-testid="stMarkdownContainer"] .mdf-foto-titulo {{
            font-size: 16px;
            font-weight: 700;
            color: {config.COR_NAVY};
            margin: 0 0 8px 0;
        }}
        /* O botão nativo de tela cheia do Streamlit (revelado no hover)
        flutua com um deslocamento fixo ACIMA do topo do cartão — não
        importa quanto espaço se dê entre o título e o cartão (isso só
        empurra os dois pra baixo juntos, sem criar folga onde o botão
        realmente precisa). O rótulo é só decorativo (sem nenhum clique
        esperado nele), então a solução real é esse texto nunca capturar
        clique — pointer-events é herdado, então desativar no container
        já cobre todo mundo dentro dele (o <p>, o texto). */
        div[data-testid="stElementContainer"]:has(.mdf-foto-titulo) {{
            pointer-events: none;
        }}
        /* De propósito SEM overflow: hidden aqui — não precisa (object-fit:
        contain já garante que a imagem nunca ultrapassa a caixa) e, pior,
        causava um bug real: com overflow: hidden neste container, o botão
        nativo de tela cheia do Streamlit (que flutua ligeiramente pra fora
        do topo do cartão no hover) parava de receber cliques — o próprio
        cartão passava a interceptar o clique no lugar dele. */
        [class*="st-key-mdf_foto_"] {{
            border-radius: 14px !important;
            border: 1px solid {config.COR_BORDA} !important;
            background: #FFFFFF;
            box-shadow: 0 2px 10px rgba(23, 55, 94, 0.08);
            padding: 10px !important;
            height: {config.ALTURA_CARTAO_FOTO_PX}px !important;
            flex: none !important;
            display: flex !important;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }}
        /* st.image() embrulha a <img> em vários níveis (stElementContainer >
        stFullScreenFrame > ... > stImage > stImageContainer > img) — todos
        esses níveis são, por padrão, itens flex com "flex: 1 1 0%" do
        próprio Streamlit, o que faz o `height` declarado neles ser
        IGNORADO pelo algoritmo de flexbox (quando flex-basis não é "auto",
        é ele — não o height — que decide o tamanho no eixo principal).
        "flex: none" reseta isso em cada nível, pra "height: 100%" (e por
        fim object-fit: contain no <img>) realmente valerem. */
        [class*="st-key-mdf_foto_"] > div[data-testid="stElementContainer"],
        [class*="st-key-mdf_foto_"] [data-testid="stFullScreenFrame"],
        [class*="st-key-mdf_foto_"] [data-testid="stFullScreenFrame"] > div,
        [class*="st-key-mdf_foto_"] [data-testid="stImage"],
        [class*="st-key-mdf_foto_"] [data-testid="stImageContainer"] {{
            height: 100% !important;
            width: 100% !important;
            flex: none !important;
        }}
        [class*="st-key-mdf_foto_"] img {{
            width: 100% !important;
            height: 100% !important;
            object-fit: contain;
            border-radius: 8px;
        }}
        [data-testid="stMarkdownContainer"] .mdf-foto-vazio {{
            font-size: 14px;
            color: {config.COR_TEXTO_SECUNDARIO};
            text-align: center;
            margin: 0;
            padding: 0 12px;
        }}

        /* ---- Botões ----
        stDownloadButton (st.download_button) e o botão de dentro de um
        st.form (st.form_submit_button) são componentes separados de
        stButton (st.button) — cada um com seu próprio testid de "kind"
        primário (stBaseButton-primary / -primaryFormSubmit), então a
        regra de cor precisa mirar os três; senão o botão de formulário
        (ex.: "Entrar" na tela de login) fica com a cor padrão do
        Streamlit (vermelha) em vez do navy da identidade visual. */
        .stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {{
            border-radius: 8px;
            font-weight: 500;
        }}
        button[data-testid="stBaseButton-primary"],
        button[data-testid="stBaseButton-primaryFormSubmit"] {{
            background: {config.COR_NAVY} !important;
            border-color: {config.COR_NAVY} !important;
        }}
        /* O tema padrão do Streamlit tem sua PRÓPRIA regra de :hover/
        :focus-visible pintando o botão de vermelho (a cor "primária"
        padrão dele), com especificidade maior que uma regra simples por
        atributo — sem isso, passar o mouse (ou terminar de clicar, já
        que o cursor "fica parado" ali) faz o botão piscar vermelho antes
        de voltar ao navy. !important garante que o navy sempre vence. */
        button[data-testid="stBaseButton-primary"]:hover,
        button[data-testid="stBaseButton-primary"]:focus-visible,
        button[data-testid="stBaseButton-primaryFormSubmit"]:hover,
        button[data-testid="stBaseButton-primaryFormSubmit"]:focus-visible {{
            background: {config.COR_NAVY_CLARO} !important;
            border-color: {config.COR_NAVY_CLARO} !important;
            color: #FFFFFF !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _slug(texto: str) -> str:
    """Texto -> pedaço de key de container (só letras, números e _)."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", sem_acento.lower()).strip("_")


def cabecalho(subtitulo: str) -> None:
    """Renderiza o cabeçalho institucional fixo no topo de todas as abas."""
    logo_b64 = _logo_base64()
    if logo_b64:
        logo_html = f'<img class="mdf-logo-img" src="data:image/webp;base64,{logo_b64}" alt="RMC" />'
    else:
        logo_html = '<div class="mdf-logo-badge">RMC</div>'

    st.markdown(
        f"""
        <div class="mdf-header">
            <div class="mdf-header-left">
                {logo_html}
                <div>
                    <p class="mdf-header-title">{config.APP_TITLE}</p>
                    <p class="mdf-header-subtitle">{subtitulo}</p>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def montar_subtitulo(loja: str, ciclo_selecionado: str, metadata: dict | None = None) -> str:
    """Monta o subtítulo do cabeçalho a partir da loja, do ciclo
    selecionado e (se existir) do metadata.json daquele ciclo — de onde
    vem o nome do consultor que fez o último envio (o consultor não é
    mais escolhido antecipadamente numa tela de navegação, então não dá
    mais pra simplesmente recebê-lo como parâmetro).

    Ciclos antigos, de antes da migração para o Spaces (ou qualquer ciclo
    sem metadata.json por qualquer motivo), não têm essa informação —
    nesse caso o consultor é omitido do texto (nunca aparece "None").

    Quando a loja não tem subpasta de mês, o último trecho do caminho do
    ciclo é igual ao próprio código da loja — nesse caso o nome do mês
    também é omitido (em vez de repetir a loja duas vezes)."""
    ultimo_trecho = ciclo_selecionado.split("/")[-1]
    consultor = formatar_consultor((metadata or {}).get("consultor"))

    partes = [f"Loja {loja}"]
    if ultimo_trecho != loja:
        partes.append(ultimo_trecho)
    subtitulo = " · ".join(partes)

    if consultor:
        subtitulo = f"{consultor} · {subtitulo}"
    return subtitulo


_MESES_PT = {
    "01": "Janeiro", "02": "Fevereiro", "03": "Março", "04": "Abril",
    "05": "Maio", "06": "Junho", "07": "Julho", "08": "Agosto",
    "09": "Setembro", "10": "Outubro", "11": "Novembro", "12": "Dezembro",
}


def mes_legivel(ciclo_mes: str) -> str:
    """Converte o nome de uma subpasta de mês no formato 'AAAA-MM' (ex.:
    '2026-07') para um rótulo legível em português (ex.: 'Julho 2026').

    Se o valor não seguir esse formato (ex.: subpasta antiga, de antes da
    convenção AAAA-MM ter sido adotada), devolve o valor original sem
    tentar adivinhar."""
    partes = ciclo_mes.split("-")
    if len(partes) != 2 or partes[1] not in _MESES_PT:
        return ciclo_mes
    ano, mes = partes
    return f"{_MESES_PT[mes]} {ano}"


def icone_check_svg() -> str:
    """Ícone de "check" (checklist de arquivos presentes num cartão de
    ciclo, página Selecionar Loja) — SVG inline em vez de emoji (✅):
    emoji tem cor fixa da fonte do sistema operacional, não aceita ser
    pintado da cor verde do projeto via CSS mesmo dentro de um elemento
    com classe de cor já definida (.mdf-check-ok)."""
    return (
        '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" '
        'style="vertical-align:-2px;" aria-hidden="true">'
        f'<path d="M3 8.5L6.5 12L13 4.5" stroke="{config.COR_VERDE_TEXTO}" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
        '</svg>'
    )


def icone_x_svg() -> str:
    """Ícone de "x" (arquivo ausente no checklist de um cartão de ciclo)
    — mesma paleta de "ausente/pendente" já usada em outros badges do
    projeto (âmbar, ver .mdf-badge-auto), nunca vermelho."""
    return (
        '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" '
        'style="vertical-align:-2px;" aria-hidden="true">'
        f'<path d="M4 4L12 12M12 4L4 12" stroke="{config.COR_AMBAR_TEXTO}" '
        'stroke-width="2" stroke-linecap="round"/>'
        '</svg>'
    )


def corresponde_busca(termo: str, produto: str, ean_original: str, posicao) -> bool:
    """Indica se um produto corresponde a um termo de busca livre, checando
    ao mesmo tempo (sem o usuário escolher o tipo de busca):
      - substring case-insensitive no nome do produto;
      - substring case-insensitive no EAN original cadastrado;
      - correspondência exata (string) na posição.
    """
    termo = termo.strip()
    if not termo:
        return True

    termo_lower = termo.lower()
    if termo_lower in str(produto).lower():
        return True
    if termo_lower in str(ean_original).lower():
        return True
    if str(posicao).strip() == termo:
        return True
    return False


def status_produto(quantidade_original, quantidade_atual, origem: str | None, ean_valido: bool) -> str:
    """Calcula o status atual de um produto para exibição (badge) e para o
    filtro de status da Aba 1. Não é uma coluna fixa da tabela — é derivado
    a cada rerun a partir da quantidade original (calculada) comparada com
    a quantidade atualmente editada pelo consultor.

    Retorna um dos códigos: "nao_localizado" | "alterado" | "auto" |
    "sem_estoque" | "confirmado".

    Nota sobre "sem_estoque" vs. "confirmado": quando origem não é "auto",
    quantidade_original É o próprio valor de estoque atual da loja (ver
    montar_tabela_ajuste_mix — quantidade = estoque quando não há sugestão
    automática), então basta checar quantidade_original == 0 aqui, sem
    precisar receber o estoque bruto como parâmetro separado. "confirmado"
    passa a significar só "a loja tem estoque desse produto" — antes desta
    distinção, um produto zerado que ficou fora do top N de sugestão
    automática também aparecia como "confirmado", o que confundia o
    consultor sobre o que a loja realmente tem.
    """
    if not ean_valido:
        return "nao_localizado"
    if quantidade_atual != quantidade_original:
        return "alterado"
    if origem == "auto":
        return "auto"
    if quantidade_original == 0:
        return "sem_estoque"
    return "confirmado"


# Mapeia os rótulos de config.OPCOES_FILTRO_STATUS para os códigos
# retornados por status_produto(). "Todos" não entra aqui — a ausência de
# entrada no dicionário é o próprio sinal de "sem filtro" (ver
# codigo_status_do_filtro()).
_MAPA_FILTRO_STATUS = {
    "Estoque confirmado": "confirmado",
    "Sugestão automática": "auto",
    "Sem estoque": "sem_estoque",
    "Alterado manualmente": "alterado",
}


def codigo_status_do_filtro(opcao_selecionada: str) -> str | None:
    """Converte o rótulo escolhido no selectbox de filtro de status para o
    código interno de status_produto(). Retorna None para "Todos" (ou
    qualquer rótulo não mapeado), indicando que nenhum filtro de status
    deve ser aplicado."""
    return _MAPA_FILTRO_STATUS.get(opcao_selecionada)


def badge_status(status: str) -> str:
    """Retorna o HTML de um badge de status, a partir do código calculado
    por status_produto() ("nao_localizado" | "alterado" | "auto" |
    "sem_estoque" | "confirmado")."""
    if status == "nao_localizado":
        return '<span class="mdf-badge mdf-badge-nao-encontrado">EAN não localizado</span>'
    if status == "alterado":
        return '<span class="mdf-badge mdf-badge-alterado">alterado manualmente</span>'
    if status == "auto":
        return '<span class="mdf-badge mdf-badge-auto">sugestão automática</span>'
    if status == "sem_estoque":
        return '<span class="mdf-badge mdf-badge-sem-estoque">sem estoque</span>'
    return '<span class="mdf-badge mdf-badge-ok">estoque confirmado</span>'


def cartao_foto(
    titulo: str,
    imagem_bytes: bytes | None,
    mime: str | None = None,
    mensagem_vazio: str | None = None,
) -> None:
    """Renderiza o rótulo + cartão de uma foto da Aba 3 (Conferência).

    Com `imagem_bytes` preenchido, usa st.image() NATIVO (não <img> em HTML
    solto) — é o que dá ao consultor o ícone de tela cheia do próprio
    Streamlit ao passar o mouse sobre a imagem, sem precisar reimplementar
    zoom na mão. A "moldura" (cantos arredondados, borda sutil, sombra) é
    aplicada por FORA do widget via CSS, mirando o container pela key
    (mdf_foto_*), o padrão do projeto para estilizar containers.

    Sem imagem, mostra `mensagem_vazio` (ex.: "Ainda não enviada", "Não foi
    possível processar o arquivo") DENTRO do mesmo tipo de moldura — nunca
    como texto solto fora dela.
    """
    # gap=None: o espaço entre título e cartão é só a margem do título (8px).
    with st.container(gap=None):
        st.markdown(f'<p class="mdf-foto-titulo">{titulo}</p>', unsafe_allow_html=True)
        _cartao_foto(titulo, imagem_bytes, mensagem_vazio)


def _cartao_foto(titulo: str, imagem_bytes, mensagem_vazio) -> None:
    with st.container(key=f"mdf_foto_{_slug(titulo)}"):
        if imagem_bytes:
            st.image(imagem_bytes, width="stretch")
        else:
            st.markdown(
                f'<p class="mdf-foto-vazio">{mensagem_vazio or "Ainda não enviada"}</p>',
                unsafe_allow_html=True,
            )


def badge_frentes(frentes) -> str:
    """Retorna o HTML do campo "Frentes" de um produto (usado na Aba 2).

    Com 1 frente (ou valor ausente/não numérico) continua como texto
    simples cinza, igual já era. Com 2 ou mais frentes vira um badge roxo
    destacado — é informação de merchandising relevante o bastante pra
    chamar atenção visualmente em vez de se perder no texto cinza comum.
    """
    texto = str(frentes).strip() if frentes is not None else ""
    if texto.lower() in ("", "nan", "none"):
        return "Frentes: –"

    try:
        valor = int(float(texto))
    except (TypeError, ValueError):
        return f"Frentes: {texto}"

    if valor >= 2:
        return f'<span class="mdf-badge mdf-badge-frentes">Frentes: {valor}</span>'
    return f"Frentes: {valor}"
