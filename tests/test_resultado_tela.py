"""Tela do Resultado da ação, com arquivos pequenos em pasta temporária.
Rodar da raiz do projeto: python -m pytest tests"""
import json
import shutil
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import modules.resultado_acao as ra
import modules.resultado_tela as tela

CICLO = "0000/2026-09"
GRUPO = f"resultado_grupo::{CICLO}"
SITUACAO = f"resultado_situacao::{CICLO}"


def _planilha(caminho: Path, cabecalho: list, linhas: list) -> None:
    pd.DataFrame([cabecalho, *linhas]).to_excel(caminho, header=False, index=False)


@pytest.fixture
def dados(tmp_path) -> Path:
    pasta = tmp_path / "0000" / "2026-09"
    pasta.mkdir(parents=True)
    shutil.copytree(RAIZ / "dados_demo" / "_Base", tmp_path / "_Base")
    _planilha(pasta / "mapa_farmacia.xlsx", ["Módulo", "Posição", "EAN", "Produto", "Frentes"], [
        [1, 1, "7891000000011", "<b>Cresce</b> & Cia", 1],
        [1, 2, "7891000000028", "Novo na loja", 1],
        [1, 3, "7891000000035", "Nunca entrou", 1],
    ])
    _planilha(pasta / "estoque.xlsx", ["Id lojas", "Cód. barras", "Unidades em estoque", "Estoque venda (R$)", "Unidades vendidas"], [
        ["0000", "7891000000011", 5, 50, 30],
        ["0000", "7890000000099", 2, 20, 30],
    ])
    _planilha(pasta / "retrato_vendas.xlsx", ["CodigoBarras", "Quantidade", "Variável de Análise"], [
        ["7891000000011", 20, 200],
        ["7891000000028", 3, 30],
        ["7890000000099", 10, 100],
    ])
    _planilha(pasta / "estoque_atualizado.xlsx", ["Id lojas", "Cód. barras", "Unidades em estoque", "Última compra"], [
        ["0000", "7891000000011", 4, "15/10/2026"],
        ["0000", "7891000000028", 1, "20/10/2026"],
    ])
    (pasta / "metadata.json").write_text(json.dumps({"consultor": "Teste"}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def abrir(dados, monkeypatch):
    """O AppTest sempre volta para a página padrão: torna a Conferência a
    padrão só no teste."""
    pagina_original = st.Page

    def pagina_conferencia_padrao(pagina, **kwargs):
        kwargs["default"] = kwargs.get("url_path") == "analise-conferencia"
        return pagina_original(pagina, **kwargs)

    monkeypatch.setattr(st, "Page", pagina_conferencia_padrao)
    # O AppTest não registra nem clica em componentes: aqui o filtro é escolhido
    # pelo session_state. O clique real é conferido em navegador.
    monkeypatch.setattr(tela, "_filtro_clicavel", lambda **kwargs: None)
    st.cache_data.clear()

    def _abrir(**estado) -> AppTest:
        at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=120)
        at.secrets["storage_mode"] = "local"
        at.secrets["local"] = {"raiz": str(dados)}
        at.secrets["api_lojas"] = {"url": "", "api_key": ""}
        at.secrets["auth"] = {"senha": "x"}
        at.session_state["autenticado"] = True
        at.session_state["loja_atual_analise"] = "0000"
        at.session_state["ciclo_atual_analise"] = CICLO
        at.session_state["metadata_atual_analise"] = {"consultor": "Teste"}
        for chave, valor in estado.items():
            at.session_state[chave] = valor
        at.run()
        assert not at.exception, at.exception
        return at

    return _abrir


def _textos(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_mostra_resultado_sem_ajuste_de_mix_salvo(abrir, dados):
    assert not list(dados.glob("0000/2026-09/ajuste_mix*"))
    texto = _textos(abrir())
    assert "Faturamento da gôndola" in texto
    assert "Mostrando 3 de 3" in texto
    assert "vendas de" in texto


def test_nome_do_produto_nao_vira_html(abrir):
    assert "&lt;b&gt;Cresce&lt;/b&gt; &amp; Cia" in _textos(abrir())


def test_filtros_reduzem_a_lista(abrir):
    assert "Mostrando 1 de 1" in _textos(abrir(**{GRUPO: ra.GRUPO_NOVO}))
    assert "Mostrando 1 de 1" in _textos(abrir(**{SITUACAO: ra.SITUACAO_NAO_ENTROU}))


def test_filtros_sem_produto_avisam(abrir):
    assert "Nenhum produto neste filtro" in _textos(abrir(**{GRUPO: ra.GRUPO_NOVO, SITUACAO: ra.SITUACAO_CAIU}))


def test_limpar_filtros_volta_para_a_lista_inteira(abrir):
    at = abrir(**{GRUPO: ra.GRUPO_NOVO})
    next(b for b in at.button if b.label == "Limpar filtros").click().run()
    assert "Mostrando 3 de 3" in _textos(at)


@pytest.mark.parametrize("arquivo, aviso", [
    ("retrato_vendas.xlsx", "Retrato de Vendas"),
    ("estoque_atualizado.xlsx", "Estoque atualizado"),
])
def test_arquivo_faltando_avisa_qual(abrir, dados, arquivo, aviso):
    (dados / "0000" / "2026-09" / arquivo).unlink()
    at = abrir()
    assert any(aviso in i.value for i in at.info)
    assert "Faturamento da gôndola" not in _textos(at)


# ---- Peças de texto ----

def _resultado(unidades_depois, faturamento_depois) -> ra.ResultadoAcao:
    return ra.ResultadoAcao(
        tabela=pd.DataFrame(columns=["grupo", "situacao"]),
        gondola=ra.Comparativo(100, unidades_depois, 1000, faturamento_depois),
    )


def test_cartao_principal_mostra_queda_em_vermelho():
    html = tela.cartao_principal_html(_resultado(90, 800))
    assert "−R$ 200" in html and "mdf-ra-negativo" in html
    assert "A gôndola faturou 20,0% menos e vendeu 10,0% menos unidades." in html


def test_cartao_principal_explica_faturamento_subindo_com_menos_unidades():
    html = tela.cartao_principal_html(_resultado(90, 1100))
    assert "+R$ 100" in html and "mdf-ra-positivo" in html
    assert "A gôndola faturou 10,0% mais, com 10,0% menos unidades." in html
    assert "R$ 1.000 → R$ 1.100 por mês" in html and "100 → 90 unidades" in html


def test_cartao_principal_nao_fala_do_resto_da_loja():
    assert "Resto da loja" not in tela.cartao_principal_html(_resultado(90, 1100))


def test_cartao_principal_sem_base_nao_mostra_numero():
    resultado = ra.ResultadoAcao(pd.DataFrame(), ra.Comparativo(0, 5, 0, 50))
    html = tela.cartao_principal_html(resultado)
    assert "Sem base para comparar" in html and "<small>" not in html


@pytest.mark.parametrize("situacao, grupo, antes, depois, pct, esperado", [
    (ra.SITUACAO_CRESCEU, ra.GRUPO_NA_LOJA, 12, 21, 75.0, "+9 un · +75%"),
    (ra.SITUACAO_CRESCEU, ra.GRUPO_NA_LOJA, 1, 2, None, "+1 un"),
    (ra.SITUACAO_ESTAVEL, ra.GRUPO_NA_LOJA, 0.7, 1, None, "+0,3 un"),
    (ra.SITUACAO_NAO_ENTROU, ra.GRUPO_NAO_ENTROU, 0, 0, None, "—"),
])
def test_texto_da_variacao(situacao, grupo, antes, depois, pct, esperado):
    row = {"situacao": situacao, "grupo": grupo, "variacao_unidades": depois - antes, "variacao_pct": pct}
    assert tela._variacao(row)[0] == esperado


def test_unidades_do_cartao_principal_sem_casa_decimal():
    resultado = ra.ResultadoAcao(pd.DataFrame(), ra.Comparativo(156.7, 152, 1000, 900))
    assert "157 → 152 unidades" in tela.cartao_principal_html(resultado)


def test_textos_das_colunas_usam_un_sem_ponto():
    row = {"grupo": ra.GRUPO_NA_LOJA, "unidades_antes": 2.0, "faturamento_antes": 90.0, "unidades_depois": 1.0, "faturamento_depois": 45.0}
    assert tela._texto_antes(row) == "2 un · R$ 90"
    assert tela._texto_depois(row) == "1 un · R$ 45"
    assert tela._texto_depois({**row, "unidades_depois": 0}) == "0 un"


@pytest.mark.parametrize("crescimentos, escala", [
    ((-0.03, 0.05), (-50, 50)),
    ((0.03, 0.05), (0, 50)),
    ((0.0, 0.0), (0, 50)),
    ((-0.03, -0.05), (-50, 50)),
    ((0.03, 0.62), (0, 70)),
    ((-0.71, 0.05), (-80, 80)),
])
def test_escala_das_barras(crescimentos, escala):
    assert tela._escala_barras(*crescimentos) == escala


def test_barra_negativa_cresce_para_a_esquerda_do_zero():
    html = tela._barra("G", -0.03, (-50, 50), "#000", "x")
    assert "left: 47.0%; width: 3.0%" in html and "left: 50.0%" in html


def test_barra_positiva_sem_lado_negativo_comeca_no_zero():
    assert "left: 0.0%; width: 10.0%" in tela._barra("G", 0.05, (0, 50), "#000", "x")


def test_csv_abre_no_excel_em_portugues():
    tabela = pd.DataFrame([{
        "produto": "Á", "ean": "1", "grupo": ra.GRUPO_NA_LOJA, "situacao": ra.SITUACAO_CAIU,
        "unidades_antes": 3.3333, "unidades_depois": 3.0, "faturamento_antes": 10.5, "faturamento_depois": 9.0,
        "variacao_unidades": -0.3333, "variacao_pct": -10.0,
    }])
    conteudo = tela.csv_da_lista(tabela)
    assert conteudo.startswith(b"\xef\xbb\xbf")
    texto = conteudo.decode("utf-8-sig")
    assert "Produto;EAN;Grupo" in texto and ";3,3;" in texto and "Em estoque;Caiu" in texto
