"""Parte visual do Resultado da ação (Aba 3 — Conferência): textos, HTML e o
componente clicável dos filtros. As regras ficam em modules/resultado_acao.py.
"""
from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

import config
import modules.resultado_acao as ra

MENOS = "−"

GRUPOS = {
    ra.GRUPO_NA_LOJA: {"rotulo": "Em estoque", "classe": "mdf-chip-azul"},
    ra.GRUPO_NOVO: {"rotulo": "Novo", "classe": "mdf-chip-roxo"},
    ra.GRUPO_NAO_ENTROU: {"rotulo": "Não entrou", "classe": "mdf-chip-neutro"},
    ra.GRUPO_SEM_DADO: {"rotulo": "Sem dado", "classe": "mdf-chip-neutro"},
}

SITUACOES = {
    ra.SITUACAO_CRESCEU: {
        "rotulo": "Cresceu", "classe": "mdf-chip-positivo",
        "dica": (
            f"Vendeu pelo menos {config.RESULTADO_VARIACAO_MIN_UNIDADES} unidade e "
            f"{config.RESULTADO_VARIACAO_MIN_PCT}% a mais que a média dos 3 meses anteriores."
        ),
    },
    ra.SITUACAO_CAIU: {
        "rotulo": "Caiu", "classe": "mdf-chip-negativo",
        "dica": (
            f"Vendeu pelo menos {config.RESULTADO_VARIACAO_MIN_UNIDADES} unidade e "
            f"{config.RESULTADO_VARIACAO_MIN_PCT}% a menos que a média dos 3 meses anteriores."
        ),
    },
    ra.SITUACAO_ESTAVEL: {
        "rotulo": "Estável", "classe": "mdf-chip-neutro",
        "dica": (
            f"Mudou menos de {config.RESULTADO_VARIACAO_MIN_UNIDADES} unidade ou "
            f"menos de {config.RESULTADO_VARIACAO_MIN_PCT}%."
        ),
    },
    ra.SITUACAO_NOVO_COM_VENDA: {
        "rotulo": "Novo com venda", "classe": "mdf-chip-roxo",
        "dica": "A loja não tinha o produto, comprou e já vendeu.",
    },
    ra.SITUACAO_NOVO_SEM_VENDA: {
        "rotulo": "Novo sem venda", "classe": "mdf-chip-azul",
        "dica": "A loja não tinha o produto, comprou e ainda não vendeu.",
    },
    ra.SITUACAO_ZEROU: {
        "rotulo": "Zerou o estoque", "classe": "mdf-chip-alerta",
        "dica": (
            "Terminou o mês sem estoque, mas vendeu no mês ou foi comprado nos "
            f"últimos {config.RESULTADO_DIAS_COMPRA_RECENTE} dias. Precisa repor."
        ),
    },
    ra.SITUACAO_SAIU: {
        "rotulo": "Saiu do mix", "classe": "mdf-chip-laranja",
        "dica": f"Sem estoque, sem venda no mês e sem compra nos últimos {config.RESULTADO_DIAS_COMPRA_RECENTE} dias.",
    },
    ra.SITUACAO_NAO_ENTROU: {
        "rotulo": "Não entrou", "classe": "mdf-chip-neutro",
        "dica": "Está no Mapa, mas a loja não tinha, não comprou e não vendeu.",
    },
    ra.SITUACAO_SEM_DADO: {
        "rotulo": "Sem dado", "classe": "mdf-chip-neutro",
        "dica": "O código de barras do produto no Mapa não é válido, então não dá para cruzar.",
    },
}

EXPLICACAO = f"""
- **Antes:** vendas dos 3 meses do Estoque do mês do planograma, divididas por 3.
- **Depois:** vendas exatas do Retrato de Vendas, no período lido do próprio arquivo.
- **Preço do Antes:** o do Estoque. Se o produto estava sem estoque no dia do planograma, o do Retrato. Sem preço em nenhum dos dois, ele conta R$ 0 no Antes.
- **Faturamento da gôndola:** faturamento depois menos faturamento antes, só com produtos do Mapa. Compara a gôndola com ela mesma, sem descontar o movimento do resto da loja.
- **Cresceu ou Caiu:** diferença de pelo menos {config.RESULTADO_VARIACAO_MIN_UNIDADES} unidade e {config.RESULTADO_VARIACAO_MIN_PCT}%. Abaixo disso, o produto fica como Estável.
- **Uma situação por produto:** se o produto terminou o mês sem estoque, ele fica como Zerou o estoque ou Saiu do mix, mesmo que também tenha caído ou crescido.
- **Zerou o estoque:** terminou o mês sem estoque, mas vendeu no mês ou foi comprado nos últimos {config.RESULTADO_DIAS_COMPRA_RECENTE} dias.
- **Saiu do mix:** sem estoque, sem venda no mês e sem compra nos últimos {config.RESULTADO_DIAS_COMPRA_RECENTE} dias. Produto sem data de compra entra aqui.
- **Produtos novos:** a loja não tinha estoque nem venda antes e, depois, vendeu ou ficou com estoque.
- **Não entrou:** está no Mapa, mas a loja não tinha, não comprou e não vendeu.
- Só entram produtos da planilha do Mapa da Farmácia.
"""


# ---------------------------------------------------------------------------
# Formatação
# ---------------------------------------------------------------------------

def _numero(valor: float, casas: int = 0) -> str:
    return f"{valor:,.{casas}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def _rs(valor: float) -> str:
    return f"{MENOS if round(valor) < 0 else ''}R$ {_numero(abs(valor))}"


def _unidades(valor: float) -> str:
    return _numero(valor, 0 if abs(valor - round(valor)) < 0.05 else 1)


def _pct_com_sinal(fracao: float) -> str:
    sinal = "+" if fracao >= 0 else MENOS
    return f"{sinal}{_numero(abs(fracao) * 100, 1)}%"


def _dica(texto: str) -> str:
    """Ícone ⓘ que mostra `texto` ao passar o mouse ou focar pelo teclado."""
    return (
        f'<span class="mdf-dica" tabindex="0" aria-label="{escape(texto)}">ⓘ'
        f'<span class="mdf-dica-caixa" role="tooltip">{escape(texto)}</span></span>'
    )


# ---------------------------------------------------------------------------
# Topo da seção
# ---------------------------------------------------------------------------

# Ícones de traço (24×24), desenhados com currentColor. Texto fixo do código,
# nunca dado do usuário.
ICONES = {
    "calendario": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>',
    "grupos": (
        '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/>'
        '<rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>'
    ),
    "lista": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "caixa": '<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
    "novo": '<path d="M12 5v14M5 12h14"/>',
    "fora": '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
}


def _icone(nome: str) -> str:
    return (
        '<svg class="mdf-ra-icone" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{ICONES[nome]}</svg>'
    )


def titulo_secao_html(texto: str, icone: str) -> str:
    return f'<p class="mdf-ra-h2">{_icone(icone)}{escape(texto)}</p>'


def periodo_html(rotulo_antes: str, rotulo_depois: str) -> str:
    """Subtítulo da seção e, ao lado, os dois períodos comparados, escritos
    por extenso para ninguém precisar passar o mouse."""
    return (
        '<div class="mdf-ra-topo">'
        '<p class="mdf-ra-subtitulo">O que mudou nas vendas da gôndola depois do planograma</p>'
        f'<div class="mdf-ra-periodo">{_icone("calendario")}<dl>'
        f'<dt>Antes</dt><dd>{escape(rotulo_antes)}</dd>'
        f'<dt>Depois</dt><dd>{escape(rotulo_depois)}</dd>'
        '</dl></div></div>'
    )


def _frase_comparacao(faturamento: float, unidades: float) -> str:
    def trecho(fracao: float) -> str:
        return f"{_numero(abs(fracao) * 100, 1)}% {'mais' if fracao >= 0 else 'menos'}"

    if (faturamento >= 0) == (unidades >= 0):
        return f"A gôndola faturou {trecho(faturamento)} e vendeu {trecho(unidades)} unidades."
    return f"A gôndola faturou {trecho(faturamento)}, com {trecho(unidades)} unidades."


def _selo(fracao: float | None, rotulo: str) -> str:
    if fracao is None:
        return ""
    if round(fracao * 100, 1) == 0:  # mostraria "+0,0%" ou "−0,0%"
        return f'<span class="mdf-chip mdf-ra-selo mdf-chip-neutro"><b>0,0%</b> {rotulo}</span>'
    if fracao < 0:
        seta, classe = "↘", "mdf-chip-negativo"
    else:
        seta, classe = "↗", "mdf-chip-positivo"
    return f'<span class="mdf-chip mdf-ra-selo {classe}">{seta} <b>{_pct_com_sinal(fracao)}</b> {rotulo}</span>'


def _linha_comparacao(quando: str, faturamento: float, unidades: float, maior: float, cor: str) -> str:
    """Uma linha "Antes" ou "Depois": R$, barra na mesma escala das duas
    linhas (a maior ocupa 100%) e unidades."""
    largura = faturamento / maior * 100 if maior > 0 else 0
    return (
        '<div class="mdf-ra-linha">'
        f'<span class="mdf-ra-quando">{quando}</span>'
        f'<span class="mdf-ra-rs">{_rs(faturamento)}</span>'
        f'<span class="mdf-ra-trilho"><span style="width: {largura:.1f}%; background: {cor};"></span></span>'
        f'<span class="mdf-ra-un">{_numero(unidades)} un</span>'
        '</div>'
    )


def _comparacao_html(resultado: ra.ResultadoAcao) -> str:
    gondola = resultado.gondola
    maior = max(gondola.faturamento_antes, gondola.faturamento_depois)
    linhas = (
        _linha_comparacao("Antes", gondola.faturamento_antes, gondola.unidades_antes, maior, config.COR_NAVY_CLARO)
        + _linha_comparacao("Depois", gondola.faturamento_depois, gondola.unidades_depois, maior, config.COR_NAVY)
    )
    # O total "depois" junta duas origens; sem isto, o card do grupo Em
    # estoque (que não conta os novos) parece contradizer o total.
    unidades_novos = resultado.comparativo_do_grupo(ra.GRUPO_NOVO).unidades_depois if not resultado.tabela.empty else 0
    composicao = ""
    if unidades_novos > 0:
        unidades_na_loja = resultado.comparativo_do_grupo(ra.GRUPO_NA_LOJA).unidades_depois
        composicao = (
            f'<p class="mdf-ra-composicao">Depois: <b>{_numero(unidades_na_loja)} un</b> dos produtos que já estavam '
            f'na loja + <b>{_numero(unidades_novos)} un</b> dos produtos novos.</p>'
        )
    return f'<div class="mdf-ra-comparacao">{linhas}{composicao}</div>'


def cartao_principal_html(resultado: ra.ResultadoAcao) -> str:
    gondola = resultado.gondola
    ganho = resultado.ganho_faturamento
    rotulo = (
        '<p class="mdf-ra-rotulo">Faturamento da gôndola '
        f'{_dica("Faturamento depois menos faturamento antes, só com produtos do Mapa. Compara a gôndola com ela mesma.")}</p>'
    )
    if ganho is None:
        principal = (
            f'{rotulo}<p class="mdf-ra-valor mdf-ra-sem-base">Sem base para comparar</p>'
            '<p class="mdf-ra-frase">Não há venda suficiente antes do planograma para fazer a conta.</p>'
        )
    else:
        classe_valor = "mdf-ra-negativo" if round(ganho) < 0 else "mdf-ra-positivo"
        sinal = "+" if round(ganho) > 0 else ""
        faturamento, unidades = gondola.crescimento_faturamento, gondola.crescimento_unidades
        frase = f'<p class="mdf-ra-frase">{_frase_comparacao(faturamento, unidades)}</p>' if unidades is not None else ""
        principal = (
            f'{rotulo}<p class="mdf-ra-valor {classe_valor}">{sinal}{_rs(ganho)} <small>/mês</small></p>'
            f'<div class="mdf-ra-selos">{_selo(faturamento, "no faturamento")}{_selo(unidades, "em unidades")}</div>'
            f'{frase}'
        )
    return (
        f'<div class="mdf-ra-cartao"><div class="mdf-ra-principal">{principal}</div>'
        f'{_comparacao_html(resultado)}</div>'
    )


# ---------------------------------------------------------------------------
# Busca e ordenação da lista
# ---------------------------------------------------------------------------

ORDENS = {
    "impacto": "Maior impacto",
    "queda": "Maior queda",
    "alta": "Maior alta",
}

DESCRICAO_ORDEM = {
    "impacto": "do maior para o menor impacto em R$",
    "queda": "da maior para a menor queda em unidades",
    "alta": "da maior para a menor alta em unidades",
}


def buscar_e_ordenar(tabela: pd.DataFrame, busca: str, ordem: str) -> pd.DataFrame:
    """Filtra pelo nome ou EAN (sem diferenciar maiúsculas) e ordena. A
    tabela já chega do motor na ordem de impacto."""
    termo = (busca or "").strip().lower()
    if termo:
        no_nome = tabela["produto"].astype(str).str.lower().str.contains(termo, regex=False)
        # Produto sem EAN válido tem NaN, que viraria o texto "nan" e
        # apareceria ao buscar "na".
        no_ean = tabela["ean"].fillna("").astype(str).str.contains(termo, regex=False)
        tabela = tabela[no_nome | no_ean]
    if ordem in ("queda", "alta"):
        tabela = tabela.sort_values(
            ["variacao_unidades", "produto"], ascending=[ordem == "queda", True], kind="stable",
        )
    return tabela


# ---------------------------------------------------------------------------
# Itens dos filtros clicáveis
# ---------------------------------------------------------------------------

def _contagem(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def itens_cards(resultado: ra.ResultadoAcao) -> list[dict]:
    situacoes = resultado.tabela["situacao"].value_counts()
    def plural(n: int) -> str:
        return f"{n} produto" if n == 1 else f"{n} produtos"

    na_loja = resultado.comparativo_do_grupo(ra.GRUPO_NA_LOJA)
    if na_loja.crescimento_unidades is None:
        valor_na_loja, classe_na_loja = "—", "suave"
    else:
        valor_na_loja = _pct_com_sinal(na_loja.crescimento_unidades)
        classe_na_loja = "negativo" if na_loja.crescimento_unidades < 0 else "positivo"
    n_novos = len(resultado.do_grupo(ra.GRUPO_NOVO))
    n_na_loja = len(resultado.do_grupo(ra.GRUPO_NA_LOJA))
    n_nao_entrou = len(resultado.do_grupo(ra.GRUPO_NAO_ENTROU))
    n_novos_sem_venda = int(situacoes.get(ra.SITUACAO_NOVO_SEM_VENDA, 0))
    if not n_novos:
        apoio_novos = "A loja não comprou nenhum produto novo do Mapa"
    elif n_novos_sem_venda:
        apoio_novos = f"{n_novos_sem_venda} ainda sem venda"
    else:
        apoio_novos = "Todos já venderam"

    return [
        {
            "id": ra.GRUPO_NA_LOJA, "titulo": "Produtos em estoque", "qtd": plural(n_na_loja), "selo": "selo-azul",
            "icone": ICONES["caixa"],
            "dica": "A loja já tinha ou já vendia antes do planograma.",
            "valor": valor_na_loja, "valorClasse": classe_na_loja,
            "apoio": "",
            "detalhe": (
                f"Unidades dos que já estavam na loja: {_numero(na_loja.unidades_antes)} → "
                f"{_numero(na_loja.unidades_depois)} por mês. "
                f"{_contagem(situacoes.get(ra.SITUACAO_CRESCEU, 0), 'cresceu', 'cresceram')}, "
                f"{_contagem(situacoes.get(ra.SITUACAO_CAIU, 0), 'caiu', 'caíram')}."
            ),
        },
        {
            "id": ra.GRUPO_NOVO, "titulo": "Produtos novos", "qtd": plural(n_novos), "selo": "selo-roxo",
            "icone": ICONES["novo"],
            "dica": "A loja não tinha e comprou depois do planograma.",
            "valor": f"{situacoes.get(ra.SITUACAO_NOVO_COM_VENDA, 0)} de {n_novos} venderam" if n_novos else "Nenhum",
            "valorClasse": "navy",
            "apoio": apoio_novos, "detalhe": "",
        },
        {
            "id": ra.GRUPO_NAO_ENTROU, "titulo": "Não entraram", "qtd": plural(n_nao_entrou), "selo": "selo-neutro",
            "icone": ICONES["fora"],
            "dica": "A loja não tinha, não comprou e não vendeu.",
            "valor": plural(n_nao_entrou), "valorClasse": "suave",
            "apoio": "Estão no Mapa e a loja não tem", "detalhe": "",
        },
    ]


def itens_chips(tabela: pd.DataFrame, ativo: str | None) -> list[dict]:
    """Filtros de situação, com os totais de `tabela` (a lista já filtrada por
    grupo). Situação sem nenhum produto não aparece, salvo a que está ativa."""
    contagem = tabela["situacao"].value_counts()
    itens = [{"id": "todos", "rotulo": "Todos", "total": len(tabela), "dica": ""}]
    for situacao, info in SITUACOES.items():
        total = int(contagem.get(situacao, 0))
        if total or situacao == ativo:
            itens.append({"id": situacao, "rotulo": info["rotulo"], "total": total, "dica": info["dica"]})
    return itens


# ---------------------------------------------------------------------------
# Lista produto a produto
# ---------------------------------------------------------------------------

def _texto_antes(row) -> str:
    if row["grupo"] != ra.GRUPO_NA_LOJA:
        return "—"
    if row["faturamento_antes"] <= 0:  # sem preço em nenhum dos arquivos
        return f'{_unidades(row["unidades_antes"])} un'
    return f'{_unidades(row["unidades_antes"])} un · {_rs(row["faturamento_antes"])}'


def _texto_depois(row) -> str:
    if row["grupo"] in (ra.GRUPO_NAO_ENTROU, ra.GRUPO_SEM_DADO):
        return "—"
    if row["unidades_depois"] <= 0:
        return "0 un"
    return f'{_unidades(row["unidades_depois"])} un · {_rs(row["faturamento_depois"])}'


def _variacao(row) -> tuple[str, str]:
    """(texto, classe de cor). Sempre em unidades, para concordar com a
    situação; os R$ ficam nas colunas Antes e Depois."""
    if row["grupo"] in (ra.GRUPO_NAO_ENTROU, ra.GRUPO_SEM_DADO):
        return "—", "mdf-ra-suave"
    diferenca = row["variacao_unidades"]
    if abs(diferenca) < 0.05:
        return "0 un", "mdf-ra-suave"
    sinal = "+" if diferenca > 0 else MENOS
    texto = f"{sinal}{_unidades(abs(diferenca))} un"
    if pd.notna(row["variacao_pct"]):
        texto += f' · {sinal}{_numero(abs(row["variacao_pct"]))}%'
    if row["situacao"] in (ra.SITUACAO_CRESCEU, ra.SITUACAO_NOVO_COM_VENDA):
        return texto, "mdf-ra-positivo"
    if row["situacao"] in (ra.SITUACAO_CAIU, ra.SITUACAO_SAIU) or (row["situacao"] == ra.SITUACAO_ZEROU and diferenca < 0):
        return texto, "mdf-ra-negativo"
    return texto, "mdf-ra-suave"


def tabela_html(tabela: pd.DataFrame) -> str:
    dica_variacao = _dica(
        f"Diferença em unidades. A % só aparece quando o produto vendia "
        f"{config.RESULTADO_UNIDADES_MIN_PARA_PCT} ou mais unidades por mês."
    ).replace('class="mdf-dica"', 'class="mdf-dica mdf-dica-esquerda"')
    linhas = []
    for _, row in tabela.iterrows():
        grupo, situacao = GRUPOS[row["grupo"]], SITUACOES[row["situacao"]]
        texto_variacao, classe_variacao = _variacao(row)
        linhas.append(
            "<tr>"
            f'<td class="mdf-ra-produto">{escape(str(row["produto"]))}</td>'
            f'<td><span class="mdf-chip {grupo["classe"]}">{grupo["rotulo"]}</span></td>'
            f'<td class="mdf-ra-num">{_texto_antes(row)}</td>'
            f'<td class="mdf-ra-num">{_texto_depois(row)}</td>'
            f'<td class="mdf-ra-num mdf-ra-forte {classe_variacao}">{texto_variacao}</td>'
            f'<td><span class="mdf-chip {situacao["classe"]}">{situacao["rotulo"]}</span></td>'
            "</tr>"
        )
    return (
        '<div class="mdf-ra-tabela-caixa"><table class="mdf-ra-tabela"><thead><tr>'
        '<th>Produto</th><th>Grupo</th><th class="mdf-ra-num">Antes</th><th class="mdf-ra-num">Depois</th>'
        f'<th class="mdf-ra-num">Variação {dica_variacao}</th><th>Situação</th>'
        f'</tr></thead><tbody>{"".join(linhas)}</tbody></table></div>'
    )


def csv_da_lista(tabela: pd.DataFrame) -> bytes:
    """CSV para abrir direto no Excel em português (ponto e vírgula, vírgula
    decimal e BOM para os acentos)."""
    saida = pd.DataFrame({
        "Produto": tabela["produto"],
        "EAN": tabela["ean"],
        "Grupo": tabela["grupo"].map(lambda g: GRUPOS[g]["rotulo"]),
        "Situação": tabela["situacao"].map(lambda s: SITUACOES[s]["rotulo"]),
        "Unidades antes (por mês)": tabela["unidades_antes"].round(1),
        "Unidades depois": tabela["unidades_depois"],
        "R$ antes (por mês)": tabela["faturamento_antes"].round(2),
        "R$ depois": tabela["faturamento_depois"].round(2),
        "Variação (unidades)": tabela["variacao_unidades"].round(1),
        "Variação (%)": tabela["variacao_pct"].round(0),
    })
    return saida.to_csv(index=False, sep=";", decimal=",", encoding="utf-8-sig").encode("utf-8-sig")


# ---------------------------------------------------------------------------
# Componente clicável (cards de grupo e filtros de situação). O Streamlit não
# tem card clicável nem botão com cor e dica por opção. Os textos entram por
# textContent, nunca como HTML.
# ---------------------------------------------------------------------------

_CSS_FILTRO = f"""
.mdf-raiz {{ font-family: inherit; font-size: 14px; line-height: 1.5; color: #1F2937; }}
.mdf-raiz.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; }}
.mdf-raiz.chips {{ display: flex; flex-wrap: wrap; gap: 4px; padding: 4px; background: #F3F4F6; border-radius: 12px; }}
button {{ font: inherit; color: inherit; cursor: pointer; }}
button:focus-visible {{ outline: 2px solid {config.COR_AZUL_TEXTO}; outline-offset: 2px; }}
.card {{
    display: flex; flex-direction: column; gap: 8px; padding: 24px; text-align: left;
    background: #FFFFFF; border: 0; border-radius: 12px; box-sizing: border-box; min-width: 0;
    box-shadow: 0 1px 2px rgba(23,55,94,.06), 0 2px 8px rgba(23,55,94,.04);
    transition: opacity .2s ease, box-shadow .2s ease, transform .2s ease;
}}
.card:hover {{ box-shadow: 0 0 0 1px {config.COR_NAVY_CLARO}, 0 4px 12px rgba(23,55,94,.08); }}
.card.ativo, .card.ativo:hover {{ box-shadow: 0 0 0 2px {config.COR_NAVY}, 0 8px 24px rgba(23,55,94,.14); transform: translateY(-2px); }}
.card.apagado {{ opacity: .45; }}
.card.apagado:hover {{ opacity: .8; }}
.card-topo {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; }}
.card-titulo {{ display: flex; align-items: center; gap: 8px; font-size: 16px; font-weight: 700; color: {config.COR_NAVY}; }}
.card-icone {{ display: grid; place-items: center; width: 32px; height: 32px; border-radius: 999px; flex: none; }}
.card-icone svg {{ width: 18px; height: 18px; }}
.card-dica {{ font-size: 14px; font-weight: 400; color: #6B7280; cursor: help; vertical-align: middle; }}
.card:focus-visible .card-dica .caixa-dica {{ display: block; }}
.card-texto {{ color: #4B5563; }}
.card-valor {{ font-size: 28px; font-weight: 700; line-height: 1.2; }}
.valor-navy {{ color: {config.COR_NAVY}; }}
.valor-suave {{ color: #4B5563; }}
.valor-positivo {{ color: {config.COR_VERDE_TEXTO}; }}
.valor-negativo {{ color: {config.COR_VERMELHO_TEXTO}; }}
.card-filtrando {{ margin-top: auto; padding-top: 12px; border-top: 1px solid #E5E7EB; font-size: 12px; font-weight: 600; color: {config.COR_NAVY}; }}
.selo {{ display: inline-flex; font-size: 12px; font-weight: 600; padding: 4px 12px; border-radius: 999px; white-space: nowrap; }}
.chip {{
    display: inline-flex; align-items: center; gap: 8px; min-height: 36px; padding: 0 12px;
    font-size: 14px; font-weight: 600; color: #4B5563; background: transparent; border: 0; border-radius: 8px;
    transition: background .15s ease, color .15s ease;
}}
.chip:hover {{ color: {config.COR_NAVY}; background: rgba(255,255,255,.6); }}
.chip.ativo {{ color: {config.COR_NAVY}; background: #FFFFFF; box-shadow: 0 1px 2px rgba(23,55,94,.12), 0 1px 4px rgba(23,55,94,.08); }}
.chip-total {{ font-size: 12px; color: #6B7280; }}
.chip.ativo .chip-total {{ color: {config.COR_NAVY}; }}
.selo-azul {{ color: {config.COR_AZUL_TEXTO}; background: {config.COR_AZUL_BG}; }}
.selo-roxo {{ color: {config.COR_ROXO_TEXTO}; background: {config.COR_ROXO_BG}; }}
.selo-neutro {{ color: {config.COR_CINZA_TEXTO}; background: {config.COR_CINZA_BG}; }}
.com-dica {{ position: relative; display: inline-flex; }}
.caixa-dica {{
    position: absolute; z-index: 10; top: calc(100% + 8px); left: 50%; transform: translateX(-50%);
    width: 240px; padding: 12px; border-radius: 12px; background: {config.COR_NAVY}; color: #FFFFFF;
    font-size: 12px; font-weight: 400; line-height: 1.5; box-shadow: 0 4px 16px rgba(23,55,94,.2); display: none;
}}
.com-dica:hover .caixa-dica, .com-dica:focus-within .caixa-dica {{ display: block; }}
"""

_JS_FILTRO = """
function elemento(tag, classe, texto) {
    const el = document.createElement(tag);
    if (classe) el.className = classe;
    if (texto !== undefined) el.textContent = texto;
    return el;
}

function card(item, ativo, escolher) {
    const botao = elemento("button", "card");
    botao.type = "button";
    botao.setAttribute("aria-pressed", String(item.id === ativo));
    if (item.id === ativo) botao.classList.add("ativo");
    else if (ativo) botao.classList.add("apagado");
    const topo = elemento("span", "card-topo");
    const titulo = elemento("span", "card-titulo");
    if (item.icone) {
        // O ícone é SVG fixo do código Python, não dado da loja.
        const bola = elemento("span", "card-icone " + item.selo);
        bola.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" '
            + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + item.icone + "</svg>";
        titulo.append(bola);
    }
    titulo.append(elemento("span", "", item.titulo));
    topo.append(titulo, elemento("span", "selo " + item.selo, item.qtd));
    const valor = elemento("span", "card-valor valor-" + item.valorClasse, item.valor);
    if (item.detalhe) {
        const dica = elemento("span", "com-dica card-dica", "ⓘ");
        const caixa = elemento("span", "caixa-dica", item.detalhe);
        caixa.setAttribute("role", "tooltip");
        dica.append(caixa);
        valor.append(" ", dica);
        botao.setAttribute("aria-description", item.detalhe);
    }
    botao.append(topo, elemento("span", "card-texto", item.dica), valor);
    if (item.apoio) botao.append(elemento("span", "card-texto", item.apoio));
    if (item.id === ativo) {
        botao.append(elemento("span", "card-filtrando", "✓ Filtrando a lista · clique de novo para ver todos"));
    }
    botao.onclick = () => escolher(item.id);
    return botao;
}

function chip(item, ativo, escolher) {
    const ehAtivo = item.id === ativo || (item.id === "todos" && !ativo);
    const botao = elemento("button", "chip" + (ehAtivo ? " ativo" : ""));
    botao.append(elemento("span", "", item.rotulo), elemento("span", "chip-total", String(item.total)));
    botao.type = "button";
    botao.setAttribute("aria-pressed", String(ehAtivo));
    botao.onclick = () => escolher(item.id);
    const envoltorio = elemento("span", "com-dica");
    envoltorio.append(botao);
    if (item.dica) {
        const caixa = elemento("span", "caixa-dica", item.dica);
        caixa.setAttribute("role", "tooltip");
        envoltorio.append(caixa);
    }
    return envoltorio;
}

export default function (component) {
    const { data, parentElement, setTriggerValue } = component;
    const raiz = parentElement.querySelector(".mdf-raiz");
    const desenhar = data.modo === "cards" ? card : chip;
    const escolher = (id) => setTriggerValue("escolhido", id);
    raiz.className = "mdf-raiz " + data.modo;
    raiz.replaceChildren(...data.itens.map((item) => desenhar(item, data.ativo, escolher)));
}
"""

_filtro_clicavel = st.components.v2.component(
    "mdf_resultado_filtro",
    html='<div class="mdf-raiz"></div>',
    css=_CSS_FILTRO,
    js=_JS_FILTRO,
)


def filtro_clicavel(modo: str, itens: list[dict], chave_estado: str) -> None:
    """Desenha os itens ("cards" ou "chips"). A escolha fica em
    st.session_state[chave_estado]: clicar no item ativo (ou em "Todos") limpa."""
    chave_componente = f"{chave_estado}::componente"

    def _ao_escolher() -> None:
        escolhido = st.session_state[chave_componente]["escolhido"]
        if escolhido:
            limpar = escolhido == "todos" or escolhido == st.session_state.get(chave_estado)
            st.session_state[chave_estado] = None if limpar else escolhido

    _filtro_clicavel(
        data={"modo": modo, "itens": itens, "ativo": st.session_state.get(chave_estado)},
        key=chave_componente,
        on_escolhido_change=_ao_escolher,
    )
