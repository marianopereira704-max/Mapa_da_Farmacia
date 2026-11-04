"""Regras do Resultado da ação (Aba 3 — Conferência), sem Streamlit.

Compara os produtos do Mapa da Farmácia antes e depois do planograma,
olhando o que aconteceu na loja. O ajuste de mix não entra na conta: a
maioria dos consultores não pede a compra ao lojista, então um produto novo
é o que o lojista comprou por conta própria.

  Antes:  média mensal do Estoque do mês do planograma (3 meses ÷ 3)
  Depois: vendas do Retrato de Vendas do mês seguinte

O número principal compara a gôndola com ela mesma: faturamento depois menos
faturamento antes. Não desconta o movimento do resto da loja.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

import config

GRUPO_NA_LOJA = "na_loja"
GRUPO_NOVO = "novo"
GRUPO_NAO_ENTROU = "nao_entrou"
GRUPO_SEM_DADO = "sem_dado"

SITUACAO_CRESCEU = "cresceu"
SITUACAO_CAIU = "caiu"
SITUACAO_ESTAVEL = "estavel"
SITUACAO_NOVO_COM_VENDA = "novo_com_venda"
SITUACAO_NOVO_SEM_VENDA = "novo_sem_venda"
SITUACAO_ZEROU = "zerou"
SITUACAO_SAIU = "saiu"
SITUACAO_NAO_ENTROU = "nao_entrou"
SITUACAO_SEM_DADO = "sem_dado"


@dataclass(frozen=True)
class Comparativo:
    """Unidades e R$ de um conjunto de produtos, por mês."""
    unidades_antes: float
    unidades_depois: float
    faturamento_antes: float = 0.0
    faturamento_depois: float = 0.0

    @property
    def crescimento_unidades(self) -> float | None:
        """Crescimento em unidades (0,05 = +5%). None sem base "antes"."""
        if self.unidades_antes <= 0:
            return None
        return self.unidades_depois / self.unidades_antes - 1

    @property
    def crescimento_faturamento(self) -> float | None:
        """Crescimento em R$ (0,05 = +5%). None sem base "antes"."""
        if self.faturamento_antes <= 0:
            return None
        return self.faturamento_depois / self.faturamento_antes - 1


@dataclass(frozen=True)
class ResultadoAcao:
    tabela: pd.DataFrame
    gondola: Comparativo

    @property
    def ganho_faturamento(self) -> float | None:
        """R$/mês. None quando falta base para comparar — a tela avisa, em
        vez de mostrar um número estimado."""
        if self.gondola.crescimento_faturamento is None:
            return None
        return self.gondola.faturamento_depois - self.gondola.faturamento_antes

    def do_grupo(self, grupo: str) -> pd.DataFrame:
        return self.tabela[self.tabela["grupo"] == grupo]

    def comparativo_do_grupo(self, grupo: str) -> Comparativo:
        linhas = self.do_grupo(grupo)
        return Comparativo(
            float(linhas["unidades_antes"].sum()),
            float(linhas["unidades_depois"].sum()),
            float(linhas["faturamento_antes"].sum()),
            float(linhas["faturamento_depois"].sum()),
        )


def _grupo(row) -> str:
    if pd.isna(row["ean"]):
        return GRUPO_SEM_DADO
    if row["estoque"] > 0 or row["unidades_antes"] > 0:
        return GRUPO_NA_LOJA
    if row["unidades_depois"] > 0 or row["estoque_fim"] > 0:
        return GRUPO_NOVO
    return GRUPO_NAO_ENTROU


def _situacao(row, fim_periodo: date) -> str:
    grupo = row["grupo"]
    if grupo == GRUPO_SEM_DADO:
        return SITUACAO_SEM_DADO
    if grupo == GRUPO_NAO_ENTROU:
        return SITUACAO_NAO_ENTROU
    if grupo == GRUPO_NOVO:
        return SITUACAO_NOVO_COM_VENDA if row["unidades_depois"] > 0 else SITUACAO_NOVO_SEM_VENDA

    antes, depois = row["unidades_antes"], row["unidades_depois"]
    if row["estoque_fim"] <= 0:
        compra = row["ultima_compra"]
        compra_recente = (
            pd.notna(compra)
            and (fim_periodo - compra.date()).days <= config.RESULTADO_DIAS_COMPRA_RECENTE
        )
        return SITUACAO_ZEROU if depois > 0 or compra_recente else SITUACAO_SAIU

    diferenca = depois - antes
    if abs(diferenca) < config.RESULTADO_VARIACAO_MIN_UNIDADES:
        return SITUACAO_ESTAVEL
    if antes > 0 and abs(diferenca) / antes * 100 < config.RESULTADO_VARIACAO_MIN_PCT:
        return SITUACAO_ESTAVEL
    return SITUACAO_CRESCEU if diferenca > 0 else SITUACAO_CAIU


def montar_resultado_acao(
    mapa_df: pd.DataFrame,
    estoque_df: pd.DataFrame,
    retrato_df: pd.DataFrame,
    estoque_atualizado_df: pd.DataFrame,
    fim_periodo: date,
) -> ResultadoAcao:
    """Monta o resultado de uma loja/ciclo.

    `estoque_df` vem de data_loader.carregar_estoque_resultado, `retrato_df`
    de carregar_retrato_vendas e `estoque_atualizado_df` de
    carregar_estoque_atualizado_resultado. `fim_periodo` é o último dia do
    período do Retrato, base da regra de compra recente.

    A tabela traz uma linha por produto do Mapa, da maior para a menor
    mudança em R$. Produtos sem EAN válido ficam como "sem_dado" e não entram
    em nenhuma conta. O preço vem do Estoque e, quando o estoque era 0, do
    próprio Retrato; sem nenhum dos dois, o faturamento "antes" fica em 0.
    """
    mapa = mapa_df.drop_duplicates(subset="chave_produto")
    mapa = mapa[mapa["ean"].isna() | ~mapa["ean"].duplicated()]
    # Sem EAN válido, um merge pandas casaria NaN com NaN: estes produtos
    # não podem cruzar com nada.
    com_ean = mapa[["chave_produto", "produto", "ean"]].reset_index(drop=True)

    base = (
        com_ean
        .merge(estoque_df.dropna(subset=["ean"]), on="ean", how="left")
        .merge(retrato_df.dropna(subset=["ean"]), on="ean", how="left")
        .merge(
            estoque_atualizado_df.rename(columns={"estoque": "estoque_fim"}).dropna(subset=["ean"]),
            on="ean", how="left",
        )
    )
    for coluna in ("estoque", "estoque_venda_rs", "unidades_vendidas_3m", "quantidade_vendida", "valor_vendido", "estoque_fim"):
        base[coluna] = pd.to_numeric(base[coluna], errors="coerce").fillna(0.0)
    if "ultima_compra" not in base:
        base["ultima_compra"] = pd.NaT
    base["ultima_compra"] = pd.to_datetime(base["ultima_compra"])

    base["unidades_antes"] = base["unidades_vendidas_3m"] / 3
    base["unidades_depois"] = base["quantidade_vendida"]
    base["faturamento_depois"] = base["valor_vendido"]
    preco_estoque = base["estoque_venda_rs"] / base["estoque"].where(base["estoque"] > 0)
    preco_retrato = base["valor_vendido"] / base["quantidade_vendida"].where(base["quantidade_vendida"] > 0)
    base["faturamento_antes"] = base["unidades_antes"] * preco_estoque.fillna(preco_retrato).fillna(0.0)

    base["grupo"] = base.apply(_grupo, axis=1)
    base["situacao"] = base.apply(_situacao, axis=1, fim_periodo=fim_periodo)

    base["variacao_unidades"] = base["unidades_depois"] - base["unidades_antes"]
    base["variacao_pct"] = (
        (base["variacao_unidades"] / base["unidades_antes"] * 100)
        .where(base["unidades_antes"] >= config.RESULTADO_UNIDADES_MIN_PARA_PCT)
    )
    base["impacto"] = (base["faturamento_depois"] - base["faturamento_antes"]).abs()

    tabela = (
        base[[
            "ean", "produto", "grupo", "situacao",
            "unidades_antes", "unidades_depois", "faturamento_antes", "faturamento_depois",
            "variacao_unidades", "variacao_pct", "impacto",
        ]]
        .sort_values(["impacto", "unidades_depois", "produto"], ascending=[False, False, True])
        .reset_index(drop=True)
    )

    return ResultadoAcao(
        tabela=tabela,
        gondola=Comparativo(
            float(tabela["unidades_antes"].sum()),
            float(tabela["unidades_depois"].sum()),
            float(tabela["faturamento_antes"].sum()),
            float(tabela["faturamento_depois"].sum()),
        ),
    )
