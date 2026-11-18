"""Faixa de alterações não salvas do Ajuste de mix.
Rodar da raiz do projeto: python -m pytest tests"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from modules.ajuste_estado import contar_alteracoes, quando_salvou, texto_aviso_gc, texto_da_faixa


def test_conta_so_o_que_mudou():
    salvo = {"a": 2, "b": 0, "c": 5}
    assert contar_alteracoes(dict(salvo), salvo) == 0
    assert contar_alteracoes({"a": 3, "b": 0, "c": 5}, salvo) == 1
    assert contar_alteracoes({"a": 3, "b": 1, "c": 0}, salvo) == 3


def test_vazio_e_zero_sao_iguais():
    assert contar_alteracoes({"a": None, "b": float("nan")}, {"a": 0}) == 0
    assert contar_alteracoes({"a": 0}, {}) == 0
    assert contar_alteracoes({"a": 1}, {}) == 1


def test_quando_salvou_em_brasilia():
    assert quando_salvou("2026-11-09T17:32:00+00:00") == "09/11 às 14:32"
    assert quando_salvou(None) is None
    assert quando_salvou("") is None
    assert quando_salvou("ontem") is None
    assert quando_salvou("2026-11-09T17:32:00") is None  # sem fuso: não dá para saber a hora


def test_textos_da_faixa():
    assert texto_da_faixa(0, False, None).startswith("Ajuste ainda não salvo")
    assert texto_da_faixa(0, True, "2026-11-09T17:32:00+00:00") == "Tudo salvo. Último salvamento em 09/11 às 14:32."
    assert texto_da_faixa(1, True, None) == "1 alteração não salva."
    assert texto_da_faixa(0, True, None) == "Tudo salvo."
    assert texto_da_faixa(3, True, "2026-11-09T17:32:00+00:00") == "3 alterações não salvas. Último salvamento em 09/11 às 14:32."


def test_aviso_da_sugestao_de_gc():
    assert texto_aviso_gc(1) == "Este PDF usa o último ajuste salvo. Há 1 alteração sem salvar no Ajuste de mix."
    assert "2 alterações" in texto_aviso_gc(2)
