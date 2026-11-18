"""Padrão do nome de consultor (modules/lojas_api.formatar_consultor)."""

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from modules.lojas_api import formatar_consultor


@pytest.mark.parametrize("nome, esperado", [
    ("Maria da Silva Santos", "MARIA SANTOS"),
    ("joão pereira", "JOÃO PEREIRA"),
    ("  Ana   Paula   Souza  ", "ANA SOUZA"),
    ("Caio", "CAIO"),
    ("João Silva Filho", "JOÃO SILVA FILHO"),
    ("Pedro de Souza Júnior", "PEDRO SOUZA JÚNIOR"),
    ("Luiz Neto", "LUIZ NETO"),
])
def test_maiusculo_so_nome_e_sobrenome(nome, esperado):
    assert formatar_consultor(nome) == esperado


@pytest.mark.parametrize("vazio", [None, "", "   "])
def test_vazio_volta_none(vazio):
    assert formatar_consultor(vazio) is None
