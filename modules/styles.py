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
from pathlib import Path

import streamlit as st

import config


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
            max-width: 1000px;
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
        .mdf-logo-badge {{
            width: 48px; height: 48px; border-radius: 50%;
            background: {config.COR_VERDE};
            display: flex; align-items: center; justify-content: center;
            font-weight: 700; color: {config.COR_NAVY}; font-size: 15px;
            flex-shrink: 0;
        }}
        .mdf-logo-img {{
            width: 48px; height: 48px; border-radius: 50%;
            object-fit: cover; flex-shrink: 0;
            background: #FFFFFF;
        }}
        .mdf-header-title {{
            font-size: 28px; font-weight: 800; letter-spacing: -0.01em;
            color: #FFFFFF; margin: 0; line-height: 1.15;
        }}
        .mdf-header-subtitle {{ font-size: 13px; color: #B9CBDC; margin: 2px 0 0 0; }}

        /* ---- Abas (st.tabs) ---- */
        .stTabs [data-baseweb="tab-list"] {{
            gap: 4px;
            border-bottom: 1px solid #E4E7EB;
        }}
        .stTabs [data-baseweb="tab"] {{
            height: auto;
            padding: 10px 16px;
            font-size: 14px;
            font-weight: 500;
            color: #7A8699;
        }}
        .stTabs [aria-selected="true"] {{
            color: {config.COR_NAVY} !important;
            border-bottom-color: {config.COR_VERDE} !important;
        }}

        /* Os seletores abaixo localizam o container-alvo pela distância
        exata (em nível de DOM) até o marcador que colocamos como primeiro
        elemento dentro dele — st.markdown envolve um <span> solto em
        vários wrappers internos do Streamlit (stElementContainer >
        stMarkdown > div > stMarkdownContainer > <p>) antes do próprio
        marcador, e essa é a única forma de mirar o container "pai"
        certo (e não um ancestral mais externo) sem um seletor CSS nativo
        de "container mais próximo". Depende da estrutura interna do
        Streamlit 1.60 — se uma atualização futura mudar esse HTML, os
        2 blocos abaixo precisam ser reconferidos no navegador.  */

        /* ---- Quadro único que agrupa toda a lista de produtos ----
        Altura fixa com rolagem própria (max-height + overflow-y), para que
        o botão Salvar e as barras de busca — que ficam FORA deste quadro,
        acima dele — permaneçam sempre visíveis sem precisar de position:
        sticky/fixed. Só a lista de produtos rola; o resto da página não. */
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-painel-marker) {{
            border-radius: 14px !important;
            border: 1px solid #E4E7EB !important;
            background: #FFFFFF;
            padding: 18px !important;
            max-height: {config.ALTURA_PAINEL_PRODUTOS_PX}px;
            overflow-y: auto;
        }}
        /* Barra de rolagem própria do quadro, sempre visível e discreta
        (o scrollbar padrão do Chrome é um traço quase imperceptível) —
        WebKit-only, mas cobre o navegador usado no projeto (Chrome/Edge). */
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-painel-marker)::-webkit-scrollbar {{
            width: 8px;
        }}
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-painel-marker)::-webkit-scrollbar-track {{
            background: transparent;
        }}
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-painel-marker)::-webkit-scrollbar-thumb {{
            background: #C9CFD8;
            border-radius: 4px;
        }}
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-painel-marker)::-webkit-scrollbar-thumb:hover {{
            background: #A9B2BF;
        }}

        /* ---- Navegação da sidebar (Upload / Selecionar Loja / Análise) ----
        Substitui o st.radio nativo por 3 st.button() empilhados — cada um
        envolvido por um st.container() com um marcador (mdf-navitem-ativo-
        marker OU mdf-botao-discreto-marker, escolhido em Python conforme
        a página atual). Escopado a [data-testid="stSidebar"] DE PROPÓSITO:
        o objetivo é diferenciar só os 3 botões do menu — um seletor
        :has() sem esse escopo poderia, no futuro, pegar qualquer outro
        botão dentro de um st.container() em outro lugar do app. */
        [data-testid="stSidebar"] div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-navitem-ativo-marker) button {{
            background: {config.COR_NAVY} !important;
            border-color: {config.COR_NAVY} !important;
            color: #FFFFFF !important;
            font-weight: 600 !important;
        }}
        [data-testid="stSidebar"] div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-navitem-ativo-marker) button:hover,
        [data-testid="stSidebar"] div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-navitem-ativo-marker) button:focus-visible {{
            background: {config.COR_NAVY_CLARO} !important;
            border-color: {config.COR_NAVY_CLARO} !important;
            color: #FFFFFF !important;
        }}

        /* ---- Botão discreto reaproveitável ----
        Usado tanto pelos itens INATIVOS do menu lateral quanto pelo botão
        "← Voltar" (página Selecionar Loja, Nível 2) e "Ir para Upload"
        (cartão "Enviar novo mês") — fundo neutro claro, texto navy, sem
        preenchimento forte, pra não competir visualmente com as ações
        primárias (Abrir, Enviar, Salvar). Ao contrário do marcador
        "ativo" acima, este NÃO é escopado à sidebar — é a mesma
        linguagem visual "secundária" reaproveitada em qualquer lugar do
        app que precise dela. */
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-botao-discreto-marker) button {{
            background: #F7F8FA !important;
            border-color: #F7F8FA !important;
            color: {config.COR_NAVY} !important;
            font-weight: 500 !important;
        }}
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-botao-discreto-marker) button:hover,
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-botao-discreto-marker) button:focus-visible {{
            background: #E4E7EB !important;
            border-color: #E4E7EB !important;
            color: {config.COR_NAVY} !important;
        }}

        /* ---- Cartão de painel SEM altura fixa (Aba Upload) ----
        Mesmo visual do quadro acima (cantos arredondados, borda sutil,
        padding) mas sem o max-height + overflow-y — aqui o conteúdo é um
        FORMULÁRIO (campos + botão de envio), não uma lista longa de itens
        que precisa rolar por dentro; com o max-height do outro marcador,
        os campos de baixo (inclusive o botão "Enviar") ficavam cortados,
        só acessíveis por uma barra de rolagem interna pouco óbvia. */
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-painel-form-marker) {{
            border-radius: 14px !important;
            border: 1px solid #E4E7EB !important;
            background: #FFFFFF;
            padding: 18px !important;
        }}

        /* Cards de produto (linhas dentro do quadro) — sem borda própria,
        só um leve background para diferenciar cada linha. */
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-row-marker) {{
            border-radius: 10px;
            background: #F7F8FA;
            padding: 8px 10px 4px 10px;
            margin-bottom: 6px;
        }}

        /* ---- Cartões de ciclo (mês) — página "Selecionar Loja", Nível 2 ----
        Card secundário independente (grid, não lista) — mesmas cores já
        usadas em outros cards secundários do projeto (fundo #F7F8FA,
        borda #E4E7EB), com border-radius um pouco maior (12px) por ser
        um cartão "cheio" (título + checklist + botão), não uma linha fina
        de lista. */
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-ciclo-card-marker) {{
            border-radius: 12px !important;
            border: 1px solid #E4E7EB !important;
            background: #F7F8FA;
            padding: 14px !important;
        }}
        /* Cartão "Enviar novo mês" — mesma família visual, mas com borda
        tracejada e fundo branco (não é um mês existente, é uma ação). */
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-ciclo-novo-marker) {{
            border-radius: 12px !important;
            border: 1px dashed #C9CFD8 !important;
            background: #FFFFFF;
            padding: 14px !important;
            display: flex !important;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
        }}
        .mdf-ciclo-titulo {{
            font-size: 15px;
            font-weight: 700;
            color: {config.COR_NAVY};
            margin: 0 0 8px 0;
        }}
        .mdf-check-ok {{ font-size: 13px; color: {config.COR_VERDE_TEXTO}; margin: 2px 0; }}
        .mdf-check-falta {{ font-size: 13px; color: {config.COR_AMBAR_TEXTO}; margin: 2px 0; }}

        /* Etiqueta de posição estilo "tag de prateleira" */
        .mdf-tag {{
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

        .mdf-produto-nome {{ font-size: 14px; color: #1A1A1A; margin: 4px 0 0 0; }}
        .mdf-produto-meta {{ font-size: 12px; color: #8A93A3; margin: 0; }}

        /* Quantidade em destaque (Aba 2 — Sugestão de GC): é o dado
        acionável da linha, precisa ser o elemento visualmente mais forte,
        maior contraste que o nome do produto. */
        .mdf-produto-qtd-destaque {{
            font-size: 18px;
            font-weight: 700;
            color: {config.COR_NAVY};
            margin: 4px 0 0 0;
            text-align: right;
        }}

        /* Badges de status (sugestão automática / confirmado / não encontrado) */
        .mdf-badge {{
            font-size: 12px; font-weight: 600; padding: 3px 8px;
            border-radius: 6px; display: inline-block;
        }}
        .mdf-badge-auto {{ background: {config.COR_AMBAR_BG}; color: {config.COR_AMBAR_TEXTO}; }}
        .mdf-badge-ok {{ background: {config.COR_VERDE_BG}; color: {config.COR_VERDE_TEXTO}; }}
        .mdf-badge-alterado {{ background: {config.COR_AZUL_BG}; color: {config.COR_AZUL_TEXTO}; }}
        .mdf-badge-nao-encontrado {{ background: #EEF0F3; color: #7A8699; }}
        .mdf-badge-frentes {{ background: {config.COR_ROXO_BG}; color: {config.COR_ROXO_TEXTO}; }}

        /* ---- Cartões de foto (Aba 3 — Conferência) ----
        Mesma linguagem visual do quadro de produtos (.mdf-painel-marker):
        cantos arredondados, borda sutil — com uma sombra leve por cima,
        já que aqui cada cartão é uma peça isolada (não um quadro único
        agrupando várias linhas) e se beneficia de uma elevação discreta.

        A imagem em si é um st.image() NATIVO (não <img> em HTML solto),
        pra ganhar o ícone de tela cheia do próprio Streamlit — a moldura
        é aplicada por FORA dele, no st.container() que o envolve, via o
        mesmo padrão de marcador invisível + :has() usado em
        .mdf-painel-marker/.mdf-row-marker. Altura FIXA (não min-height)
        pra os 3 cartões ficarem sempre alinhados entre si, tenham ou não
        imagem — a foto usa object-fit: contain dentro dessa altura. */
        .mdf-foto-titulo {{
            font-size: 15px;
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
        /* O marcador em si some completamente (display:none não o remove
        do DOM, então o seletor :has() continua funcionando — só evita que
        ele seja desenhado). Sem isso, o <p> que o envolve fica com a
        margem padrão do navegador e, empilhado em cima da imagem via
        flex-direction: column, acaba sobrepondo (e capturando o clique
        de) o botão nativo de tela cheia do Streamlit no canto da foto. */
        .mdf-foto-marker {{ display: none; }}
        p:has(> .mdf-foto-marker) {{ margin: 0; padding: 0; height: 0; line-height: 0; }}
        /* Ainda que o <p> acima colapse pra altura zero, o wrapper
        stElementContainer que o envolve (gerado pelo Streamlit) pode
        manter alguma caixa própria e ficar por cima do botão de tela
        cheia no canto da foto — pointer-events: none garante que ele
        nunca intercepta clique, não importa a altura residual. */
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-foto-marker) > div[data-testid="stElementContainer"]:has(.mdf-foto-marker) {{
            pointer-events: none;
            height: 0 !important;
            min-height: 0 !important;
            overflow: hidden;
        }}
        /* De propósito SEM overflow: hidden aqui — não precisa (object-fit:
        contain já garante que a imagem nunca ultrapassa a caixa) e, pior,
        causava um bug real: com overflow: hidden neste container, o botão
        nativo de tela cheia do Streamlit (que flutua ligeiramente pra fora
        do topo do cartão no hover) parava de receber cliques — o próprio
        cartão passava a interceptar o clique no lugar dele. */
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-foto-marker) {{
            border-radius: 14px !important;
            border: 1px solid #E4E7EB !important;
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
        fim object-fit: contain no <img>) realmente valerem. O marcador em
        si (span vazio dentro de um <p>) não precisa dessa regra — é ínfimo
        o bastante pra não atrapalhar o layout mesmo sem altura forçada. */
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-foto-marker) > div[data-testid="stElementContainer"]:has(img),
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-foto-marker) [data-testid="stFullScreenFrame"],
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-foto-marker) [data-testid="stFullScreenFrame"] > div,
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-foto-marker) [data-testid="stImage"],
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-foto-marker) [data-testid="stImageContainer"] {{
            height: 100% !important;
            width: 100% !important;
            flex: none !important;
        }}
        div[data-testid="stVerticalBlock"]:has(> div > div > div > div > p > .mdf-foto-marker) img {{
            width: 100% !important;
            height: 100% !important;
            object-fit: contain;
            border-radius: 8px;
        }}
        .mdf-foto-vazio {{
            font-size: 13px;
            color: #8A93A3;
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
    consultor = (metadata or {}).get("consultor")

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


def icone_mais_svg() -> str:
    """Ícone de "+" (cartão "Enviar novo mês", página Selecionar Loja)."""
    return (
        '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" '
        'style="vertical-align:-3px;" aria-hidden="true">'
        f'<path d="M8 3V13M3 8H13" stroke="{config.COR_NAVY}" '
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

    Retorna um dos códigos: "nao_localizado" | "alterado" | "auto" | "confirmado".
    """
    if not ean_valido:
        return "nao_localizado"
    if quantidade_atual != quantidade_original:
        return "alterado"
    if origem == "auto":
        return "auto"
    return "confirmado"


# Mapeia os rótulos de config.OPCOES_FILTRO_STATUS para os códigos
# retornados por status_produto(). "Todos" não entra aqui — a ausência de
# entrada no dicionário é o próprio sinal de "sem filtro" (ver
# codigo_status_do_filtro()).
_MAPA_FILTRO_STATUS = {
    "Estoque confirmado": "confirmado",
    "Sugestão automática": "auto",
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
    por status_produto() ("nao_localizado" | "alterado" | "auto" | "confirmado")."""
    if status == "nao_localizado":
        return '<span class="mdf-badge mdf-badge-nao-encontrado">EAN não localizado</span>'
    if status == "alterado":
        return '<span class="mdf-badge mdf-badge-alterado">alterado manualmente</span>'
    if status == "auto":
        return '<span class="mdf-badge mdf-badge-auto">sugestão automática</span>'
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
    aplicada por FORA do widget via CSS, mirando o container pelo mesmo
    padrão de marcador invisível + :has() já usado em .mdf-painel-marker/
    .mdf-row-marker — não há como estilizar o container nativo do
    st.container() de outra forma sem introduzir CSS solto fora do fluxo.

    Sem imagem, mostra `mensagem_vazio` (ex.: "Ainda não enviada", "Não foi
    possível processar o arquivo") DENTRO do mesmo tipo de moldura — nunca
    como texto solto fora dela.
    """
    st.markdown(f'<p class="mdf-foto-titulo">{titulo}</p>', unsafe_allow_html=True)
    with st.container():
        st.markdown('<span class="mdf-foto-marker"></span>', unsafe_allow_html=True)
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
