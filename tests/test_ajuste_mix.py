"""Ajuste de mix: o que foi salvo precisa voltar ao reabrir a loja.
Rodar da raiz do projeto: python -m pytest tests"""
import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import modules.data_loader as dl
from modules.storage import StorageError

CICLO = "0000/2026-09"
COM_ESTOQUE = "0000000000001"
SUGERIDO_A = "0000000000002"
SUGERIDO_B = "0000000000003"
# Só os 5 produtos sem estoque de maior demanda recebem sugestão: estes três,
# mais os dois acima, deixam o de estoque negativo (menor demanda) de fora dela.
OUTROS_SUGERIDOS = ["0000000000004", "0000000000005", "0000000000006"]
ESTOQUE_NEGATIVO = "0000000000018"


def _tabela(linhas: dict) -> pd.DataFrame:
    return pd.DataFrame({"chave_produto": list(linhas), "quantidade": list(linhas.values())})


# ---- Regra ----

def test_sem_ajuste_salvo_usa_o_calculo_automatico():
    assert dl.quantidades_iniciais_ajuste(_tabela({"a": 5, "b": 3, "c": 0}), None) == {"a": 5, "b": 3, "c": 0}


def test_ajuste_salvo_vale_sobre_o_calculo_automatico():
    salvo = {"produtos": [{"ean_original": "a", "quantidade": 2}, {"ean_original": "c", "quantidade": 7}]}
    assert dl.quantidades_iniciais_ajuste(_tabela({"a": 5, "b": 3, "c": 0}), salvo) == {"a": 2, "b": 0, "c": 7}


def test_estoque_negativo_fora_do_salvo_continua_aparecendo():
    assert dl.quantidades_iniciais_ajuste(_tabela({"a": -2}), {"produtos": []}) == {"a": -2}


def test_produto_salvo_que_saiu_do_mapa_nao_volta():
    salvo = {"produtos": [{"ean_original": "antigo", "quantidade": 9}]}
    assert dl.quantidades_iniciais_ajuste(_tabela({"a": 5}), salvo) == {"a": 0}


def test_codigo_numerico_no_arquivo_salvo_casa_com_o_texto_da_tabela():
    salvo = {"produtos": [{"ean_original": 123456, "quantidade": 4}]}
    assert dl.quantidades_iniciais_ajuste(_tabela({"123456": 1}), salvo) == {"123456": 4}


# ---- Tela ----

def _planilha(caminho: Path, cabecalho: list, linhas: list) -> None:
    pd.DataFrame([cabecalho, *linhas], dtype=object).to_excel(caminho, header=False, index=False)


@pytest.fixture
def dados(tmp_path) -> Path:
    pasta = tmp_path / "0000" / "2026-09"
    pasta.mkdir(parents=True)
    shutil.copytree(RAIZ / "dados_demo" / "_Base", tmp_path / "_Base")
    _planilha(pasta / "mapa_farmacia.xlsx", ["Módulo", "Posição", "EAN", "Produto", "Frentes"], [
        [1, 1, COM_ESTOQUE, "Com estoque", 1],
        [1, 2, SUGERIDO_A, "Sugerido A", 1],
        [1, 3, SUGERIDO_B, "Sugerido B", 1],
        [1, 4, ESTOQUE_NEGATIVO, "Estoque negativo", 1],
        *[[2, i, ean, f"Outro sugerido {i}", 1] for i, ean in enumerate(OUTROS_SUGERIDOS, start=1)],
    ])
    _planilha(pasta / "estoque.xlsx", ["Id lojas", "Cód. barras", "Unidades em estoque"], [
        ["0000", COM_ESTOQUE, 5],
        ["0000", ESTOQUE_NEGATIVO, -2],
    ])
    (pasta / "metadata.json").write_text(json.dumps({"consultor": "Teste"}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def abrir(dados, monkeypatch):
    """Cada chamada é uma sessão nova, como abrir o site outro dia. O AppTest
    sempre volta para a página padrão: torna o Ajuste de mix a padrão no teste."""
    pagina_original = st.Page

    def pagina_ajuste_padrao(pagina, **kwargs):
        kwargs["default"] = kwargs.get("url_path") == "analise-ajuste-mix"
        return pagina_original(pagina, **kwargs)

    monkeypatch.setattr(st, "Page", pagina_ajuste_padrao)
    st.cache_data.clear()

    def _abrir() -> AppTest:
        at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=120)
        at.secrets["storage_mode"] = "local"
        at.secrets["local"] = {"raiz": str(dados)}
        at.secrets["api_lojas"] = {"url": "", "api_key": ""}
        at.secrets["auth"] = {"senha": "x"}
        at.session_state["autenticado"] = True
        at.session_state["loja_atual_analise"] = "0000"
        at.session_state["ciclo_atual_analise"] = CICLO
        at.session_state["metadata_atual_analise"] = {"consultor": "Teste"}
        at.run()
        assert not at.exception, at.exception
        return at

    return _abrir


def _campo(at: AppTest, ean: str):
    return next(n for n in at.number_input if n.key.endswith(f"::{ean}"))


def _quantidades(at: AppTest) -> dict:
    return {ean: _campo(at, ean).value for ean in (COM_ESTOQUE, SUGERIDO_A, SUGERIDO_B, ESTOQUE_NEGATIVO)}


def _salvar(at: AppTest) -> None:
    next(b for b in at.button if "Salvar" in b.label).click().run()
    assert not at.exception, at.exception


def _salvo_no_arquivo(dados: Path) -> dict:
    produtos = json.loads((dados / "0000" / "2026-09" / "ajuste_mix.json").read_text(encoding="utf-8"))["produtos"]
    return {p["ean_original"]: p["quantidade"] for p in produtos}


def test_sem_ajuste_salvo_abre_com_a_sugestao(abrir):
    assert _quantidades(abrir()) == {COM_ESTOQUE: 5, SUGERIDO_A: 3, SUGERIDO_B: 3, ESTOQUE_NEGATIVO: -2}


def test_edicao_salva_volta_ao_reabrir_a_loja(abrir, dados):
    at = abrir()
    _campo(at, COM_ESTOQUE).set_value(8).run()
    _campo(at, SUGERIDO_A).set_value(0).run()
    _campo(at, SUGERIDO_B).set_value(6).run()
    _salvar(at)
    assert _salvo_no_arquivo(dados) == {COM_ESTOQUE: 8, SUGERIDO_B: 6, **{ean: 3 for ean in OUTROS_SUGERIDOS}}

    assert _quantidades(abrir()) == {COM_ESTOQUE: 8, SUGERIDO_A: 0, SUGERIDO_B: 6, ESTOQUE_NEGATIVO: -2}


def test_salvar_de_novo_sem_mexer_nao_muda_o_ajuste(abrir, dados):
    at = abrir()
    _campo(at, SUGERIDO_A).set_value(0).run()
    _campo(at, COM_ESTOQUE).set_value(8).run()
    _salvar(at)
    antes = _salvo_no_arquivo(dados)

    _salvar(abrir())
    assert _salvo_no_arquivo(dados) == antes


def test_abrir_a_loja_nao_grava_nada(abrir, dados):
    abrir()
    assert not (dados / "0000" / "2026-09" / "ajuste_mix.json").exists()


def test_falha_de_rede_nao_abre_a_sugestao_por_cima_do_salvo(abrir, monkeypatch):
    def falha(*args, **kwargs):
        raise StorageError("sem conexão")

    monkeypatch.setattr(dl, "carregar_ajuste_mix_salvo", falha)
    at = abrir()
    assert any("Não foi possível ler o ajuste" in e.value for e in at.error)
    assert not at.number_input
    assert not [b for b in at.button if "Salvar" in b.label]


def test_arquivo_corrompido_avisa_e_abre_a_sugestao(abrir, dados):
    (dados / "0000" / "2026-09" / "ajuste_mix.json").write_text("{quebrado", encoding="utf-8")
    at = abrir()
    assert any("corrompido" in w.value for w in at.warning)
    assert _campo(at, SUGERIDO_A).value == 3
