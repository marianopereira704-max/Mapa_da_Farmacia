"""Regras do Resultado da ação. Rodar da raiz do projeto: python -m pytest tests"""
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import modules.resultado_acao as ra

FIM = date(2026, 10, 31)


def montar(produtos, resto_estoque=(), resto_retrato=()):
    """Cada produto: dict com ean e, opcionais, est, vend3m, rs_estoque, un,
    rs, fim, compra."""
    mapa = pd.DataFrame([
        {"chave_produto": p.get("chave", str(p["ean"])), "produto": p.get("nome", f"P{p['ean']}"), "ean": p["ean"]}
        for p in produtos
    ])
    # Os leitores reais devolvem cada EAN uma vez só
    com_ean = list({p["ean"]: p for p in reversed(produtos) if pd.notna(p["ean"])}.values())
    estoque = pd.DataFrame(
        [
            {"ean": p["ean"], "estoque": p.get("est", 0), "estoque_venda_rs": p.get("rs_estoque", 0), "unidades_vendidas_3m": p.get("vend3m", 0)}
            for p in com_ean if p.get("no_estoque", True)
        ] + list(resto_estoque),
        columns=["ean", "estoque", "estoque_venda_rs", "unidades_vendidas_3m"],
    )
    retrato = pd.DataFrame(
        [{"ean": p["ean"], "quantidade_vendida": p["un"], "valor_vendido": p.get("rs", 0)} for p in com_ean if p.get("un")]
        + list(resto_retrato),
        columns=["ean", "quantidade_vendida", "valor_vendido"],
    )
    atualizado = pd.DataFrame(
        [{"ean": p["ean"], "estoque": p.get("fim", 0), "ultima_compra": pd.Timestamp(p["compra"]) if p.get("compra") else pd.NaT} for p in com_ean],
        columns=["ean", "estoque", "ultima_compra"],
    )
    return ra.montar_resultado_acao(mapa, estoque, retrato, atualizado, FIM)


def linha(produto: dict) -> pd.Series:
    return montar([{"ean": "1", **produto}]).tabela.iloc[0]


@pytest.mark.parametrize("produto, grupo", [
    ({"est": 5}, ra.GRUPO_NA_LOJA),
    ({"est": 0, "vend3m": 3}, ra.GRUPO_NA_LOJA),  # ruptura: já vendia, não é novo
    ({"est": 0, "un": 2}, ra.GRUPO_NOVO),
    ({"est": 0, "fim": 4}, ra.GRUPO_NOVO),
    ({"no_estoque": False, "un": 1}, ra.GRUPO_NOVO),
    ({"est": 0}, ra.GRUPO_NAO_ENTROU),
    ({"no_estoque": False}, ra.GRUPO_NAO_ENTROU),
])
def test_grupos(produto, grupo):
    assert linha(produto)["grupo"] == grupo


def test_ean_invalido_fica_sem_dado_e_nao_cruza_com_nada():
    resultado = montar([{"ean": None, "chave": "x", "nome": "Sem EAN"}, {"ean": "1", "est": 1, "vend3m": 3, "un": 1, "fim": 1}])
    sem_dado = resultado.tabela[resultado.tabela["produto"] == "Sem EAN"].iloc[0]
    assert sem_dado["grupo"] == ra.GRUPO_SEM_DADO
    assert sem_dado["situacao"] == ra.SITUACAO_SEM_DADO
    assert sem_dado["unidades_antes"] == 0


@pytest.mark.parametrize("produto, situacao", [
    ({"est": 0}, ra.SITUACAO_NAO_ENTROU),
    ({"est": 0, "un": 2, "fim": 1}, ra.SITUACAO_NOVO_COM_VENDA),
    ({"est": 0, "fim": 3}, ra.SITUACAO_NOVO_SEM_VENDA),
    # Zerou x Saiu: limite de 60 dias contados do fim do período
    ({"est": 2, "vend3m": 3, "un": 1, "fim": 0}, ra.SITUACAO_ZEROU),
    ({"est": 2, "vend3m": 3, "fim": 0, "compra": "2026-09-01"}, ra.SITUACAO_ZEROU),  # 60 dias
    ({"est": 2, "vend3m": 3, "fim": 0, "compra": "2026-08-31"}, ra.SITUACAO_SAIU),  # 61 dias
    ({"est": 2, "vend3m": 3, "fim": 0}, ra.SITUACAO_SAIU),  # sem data de compra
    # Cresceu/Caiu: pelo menos 1 unidade E 5%
    ({"est": 5, "vend3m": 60, "un": 21, "fim": 1}, ra.SITUACAO_CRESCEU),  # +1 un., +5,0%
    ({"est": 5, "vend3m": 60, "un": 19, "fim": 1}, ra.SITUACAO_CAIU),
    ({"est": 5, "vend3m": 90, "un": 31, "fim": 1}, ra.SITUACAO_ESTAVEL),  # +1 un., +3,3%
    ({"est": 5, "vend3m": 3, "un": 1, "fim": 1}, ra.SITUACAO_ESTAVEL),  # 0 un.
    ({"est": 5, "vend3m": 4, "un": 2, "fim": 1}, ra.SITUACAO_ESTAVEL),  # +0,67 un.
    ({"est": 5, "un": 1, "fim": 1}, ra.SITUACAO_CRESCEU),  # sem venda antes
    ({"est": 5, "fim": 5}, ra.SITUACAO_ESTAVEL),
])
def test_situacoes(produto, situacao):
    assert linha(produto)["situacao"] == situacao


def test_preco_vem_do_retrato_quando_estoque_era_zero():
    assert linha({"est": 0, "vend3m": 3, "un": 2, "rs": 20, "fim": 1})["faturamento_antes"] == pytest.approx(10)
    assert linha({"est": 2, "rs_estoque": 40, "vend3m": 3, "un": 2, "rs": 20, "fim": 1})["faturamento_antes"] == pytest.approx(20)
    assert linha({"est": 0, "vend3m": 3, "fim": 1})["faturamento_antes"] == 0


def test_porcentagem_so_com_duas_unidades_ou_mais_antes():
    assert linha({"est": 5, "vend3m": 6, "un": 3, "fim": 1})["variacao_pct"] == pytest.approx(50)
    assert pd.isna(linha({"est": 5, "vend3m": 3, "un": 3, "fim": 1})["variacao_pct"])


def test_ganho_acima_da_loja_compara_unidades():
    resultado = montar(
        [{"ean": "1", "est": 10, "rs_estoque": 100, "vend3m": 300, "un": 90, "rs": 900, "fim": 1}],  # 100 → 90 un., −10%
        resto_estoque=[{"ean": "9", "estoque": 1, "estoque_venda_rs": 1, "unidades_vendidas_3m": 150}],  # 50 un.
        resto_retrato=[{"ean": "9", "quantidade_vendida": 55, "valor_vendido": 1}],  # 55 un., +10%
    )
    assert resultado.gondola.crescimento == pytest.approx(-0.10)
    assert resultado.resto_loja.crescimento == pytest.approx(0.10)
    assert resultado.ganho_acima_da_loja == pytest.approx(1000 * -0.20)  # R$ antes = 100 un. × R$ 10


def test_sem_base_nao_inventa_numero():
    assert montar([{"ean": "1", "est": 0, "un": 2, "rs": 10, "fim": 1}]).ganho_acima_da_loja is None
    assert montar([{"ean": "1", "est": 1, "rs_estoque": 5, "vend3m": 3, "un": 1, "fim": 1}]).ganho_acima_da_loja is None


def test_produto_fora_do_mapa_nao_entra_na_lista():
    resultado = montar(
        [{"ean": "1", "est": 1, "fim": 1}],
        resto_retrato=[{"ean": "9", "quantidade_vendida": 5, "valor_vendido": 50}],
    )
    assert list(resultado.tabela["ean"]) == ["1"]
    assert resultado.resto_loja.unidades_depois == 5


def test_mesmo_ean_em_duas_posicoes_conta_uma_vez():
    resultado = montar([
        {"ean": "1", "chave": "a", "est": 1, "vend3m": 3, "un": 2, "fim": 1},
        {"ean": "1", "chave": "b", "est": 1, "vend3m": 3, "un": 2, "fim": 1, "no_estoque": False},
    ])
    assert len(resultado.tabela) == 1
    assert resultado.gondola.unidades_depois == 2
