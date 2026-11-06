"""Seleção de loja por mês: regras da barra de meses, busca e paginação.
Rodar da raiz do projeto: python -m pytest tests"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from modules.lojas_api import Loja
from modules.selecao_loja import (
    MesDaBarra,
    arquivos_do_mes,
    arquivos_do_upload,
    buscar_lojas,
    lojas_da_lista,
    lojas_do_mes,
    mes_anterior_com_arquivos,
    mes_atual,
    mes_curto,
    mes_de_destino,
    meses_da_barra,
    meses_da_loja,
    opcoes_de_filtro,
    paginar,
    rotulo_da_loja,
    rotulo_mes_da_barra,
    texto_de_destino,
)

INVENTARIO = {
    "10": {"2026-09": {}, "2026-10": {}},
    "9": {"2026-10": {}},
    "200": {"2026-08": {}},
    "_teste": {"2026-11": {}},
    "30": {"pasta_solta": {}},
}


def _loja(codigo, razao=None, fantasia=None):
    return Loja(codigo=codigo, nome=fantasia, consultor=None, supervisora=None, nome_fantasia=fantasia, razao_social=razao)


# ---- Mês atual ----

@pytest.mark.parametrize("agora, esperado", [
    (datetime(2026, 11, 1, 2, 59, tzinfo=timezone.utc), "2026-10"),  # 23:59 de 31/10 em Brasília
    (datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc), "2026-11"),   # 00:00 de 01/11 em Brasília
    (datetime(2027, 1, 1, 1, 0, tzinfo=timezone.utc), "2026-12"),    # virada de ano
])
def test_mes_atual_segue_o_horario_de_brasilia(agora, esperado):
    assert mes_atual(agora) == esperado


def test_mes_atual_usa_o_relogio_quando_nao_recebe_data():
    assert len(mes_atual()) == 7


# ---- Barra de meses ----

def test_barra_vem_do_mais_novo_para_o_mais_antigo_com_a_contagem():
    barra = meses_da_barra(INVENTARIO, "2026-11")
    assert [(m.mes, m.lojas, m.atual) for m in barra] == [
        ("2026-11", 0, True),
        ("2026-10", 2, False),
        ("2026-09", 1, False),
        ("2026-08", 1, False),
    ]


def test_mes_atual_com_arquivos_nao_aparece_duas_vezes():
    barra = meses_da_barra(INVENTARIO, "2026-10")
    assert [m.mes for m in barra].count("2026-10") == 1
    assert barra[0].atual and barra[0].lojas == 2


def test_inventario_vazio_mostra_so_o_mes_atual():
    assert [m.mes for m in meses_da_barra({}, "2026-11")] == ["2026-11"]


def test_mes_futuro_no_bucket_nao_fica_antes_do_atual_sem_motivo():
    barra = meses_da_barra({"1": {"2026-12": {}}}, "2026-11")
    assert [m.mes for m in barra] == ["2026-12", "2026-11"]


# ---- Lojas do mês ----

def test_lojas_do_mes_em_ordem_numerica_e_sem_codigos_de_teste():
    assert lojas_do_mes(INVENTARIO, "2026-10") == ["9", "10"]
    assert lojas_do_mes(INVENTARIO, "2026-11") == []


# ---- Destino do Upload ----

def test_upload_grava_no_mes_escolhido_para_a_loja_de_origem():
    assert mes_de_destino("1012", "2026-09", "1012", "2026-11") == "2026-09"


def test_mes_escolhido_vale_mesmo_com_espaco_digitado_no_codigo():
    assert mes_de_destino(" 1012 ", "2026-09", "1012", "2026-11") == "2026-09"


def test_trocar_de_loja_volta_para_o_mes_atual():
    assert mes_de_destino("2101", "2026-09", "1012", "2026-11") == "2026-11"


def test_upload_pelo_menu_usa_o_mes_atual():
    assert mes_de_destino("1012", None, None, "2026-11") == "2026-11"


def test_texto_de_destino():
    legivel = {"2026-09": "Setembro/2026", "2026-11": "Novembro/2026"}.get
    assert texto_de_destino("1012", "2026-11", "2026-11", legivel) == "Destino: **Loja 1012 · Novembro/2026**"
    assert texto_de_destino("1012", "2026-09", "2026-11", legivel) == (
        "Destino: **Loja 1012 · Setembro/2026**, mês anterior ao atual"
    )
    assert texto_de_destino("  ", "2026-11", "2026-11", legivel) == ""


def test_retrato_e_estoque_atualizado_so_em_mes_com_arquivos():
    chaves = ["mapa_farmacia", "estoque", "retrato_vendas", "estoque_atualizado"]
    assert arquivos_do_upload(chaves, True) == chaves
    assert arquivos_do_upload(chaves, False) == ["mapa_farmacia", "estoque"]


# ---- Busca ----

CADASTRO = {
    "1012": _loja("1012", razao="João da Silva ME", fantasia="Farmácia Centro"),
    "2101": _loja("2101", razao="Comércio Saúde Ltda", fantasia="Drogaria Praça"),
    "3101": _loja("3101"),
}
CODIGOS = ["1012", "2101", "3101", "4000"]


def test_busca_vazia_devolve_tudo():
    assert buscar_lojas("  ", CODIGOS, CADASTRO) == CODIGOS


def test_codigo_busca_pelo_comeco():
    assert buscar_lojas("101", CODIGOS, CADASTRO) == ["1012"]
    assert buscar_lojas("210", CODIGOS, CADASTRO) == ["2101"]


@pytest.mark.parametrize("texto, esperado", [
    ("joao", ["1012"]),
    ("SILVA", ["1012"]),
    ("comercio saude", ["2101"]),
    ("farmacia", ["1012"]),
    ("praça", ["2101"]),
])
def test_razao_social_e_nome_fantasia_em_qualquer_parte_sem_acento(texto, esperado):
    assert buscar_lojas(texto, CODIGOS, CADASTRO) == esperado


def test_loja_sem_cadastro_e_achada_so_pelo_codigo():
    assert buscar_lojas("4000", CODIGOS, CADASTRO) == ["4000"]
    assert buscar_lojas("4000", CODIGOS, {}) == ["4000"]
    assert buscar_lojas("loja", CODIGOS, {}) == []


def test_numero_no_meio_do_codigo_nao_acha():
    assert buscar_lojas("012", CODIGOS, CADASTRO) == []


# ---- Textos da barra ----

@pytest.mark.parametrize("mes, esperado", [("2026-11", "Nov/26"), ("2027-01", "Jan/27"), ("pasta", "pasta"), ("2026-13", "2026-13")])
def test_mes_curto(mes, esperado):
    assert mes_curto(mes) == esperado


def test_rotulo_do_mes_na_barra():
    assert rotulo_mes_da_barra(MesDaBarra("2026-11", 22, True)) == "Nov/26 (atual) · 22 lojas"
    assert rotulo_mes_da_barra(MesDaBarra("2026-08", 1, False)) == "Ago/26 · 1 loja"


def test_atalho_do_mes_vazio_vai_para_o_anterior_com_arquivos():
    barra = [MesDaBarra("2026-11", 0, True), MesDaBarra("2026-10", 0, False), MesDaBarra("2026-09", 5, False)]
    assert mes_anterior_com_arquivos(barra, "2026-11") == "2026-09"
    assert mes_anterior_com_arquivos(barra, "2026-09") is None
    assert mes_anterior_com_arquivos([MesDaBarra("2026-11", 0, True)], "2026-11") is None


# ---- Lista de lojas ----

def _cadastrada(codigo, razao, consultor=None, supervisora=None, status="Ativo"):
    return Loja(codigo=codigo, nome=razao, consultor=consultor, supervisora=supervisora, razao_social=razao, status=status)


LISTA_INVENTARIO = {
    "10": {"2026-11": {}},
    "9": {"2026-11": {}},
    "77": {"2026-11": {}},
    "20": {"2026-10": {}},
    "_teste": {"2026-11": {}},
}
LISTA_CADASTRO = {
    "9": _cadastrada("9", "Alfa Ltda", "Ana", "Bia"),
    "10": _cadastrada("10", "Beta Ltda", "Caio", "Bia"),
    "20": _cadastrada("20", "Alfa Sul", "Ana", "Dora"),
    "30": _cadastrada("30", "Alfa Norte", "Ana", "Bia"),
    "40": _cadastrada("40", "Alfa Fechada", "Ana", "Bia", status="Inativo"),
    "_x": _cadastrada("_x", "Alfa Teste"),
}


def test_sem_busca_so_lojas_com_arquivos_no_mes_em_ordem():
    linhas = lojas_da_lista(LISTA_INVENTARIO, "2026-11", "", LISTA_CADASTRO)
    assert [(l.codigo, l.tem_arquivos) for l in linhas] == [("9", True), ("10", True), ("77", True)]


def test_busca_acha_loja_ativa_sem_arquivos_e_marca_que_so_tem_upload():
    linhas = lojas_da_lista(LISTA_INVENTARIO, "2026-11", "alfa", LISTA_CADASTRO)
    assert [(l.codigo, l.tem_arquivos) for l in linhas] == [("9", True), ("20", False), ("30", False)]


def test_busca_nao_traz_loja_inativa_sem_arquivos():
    assert lojas_da_lista(LISTA_INVENTARIO, "2026-11", "fechada", LISTA_CADASTRO) == []


def test_loja_inativa_com_arquivos_continua_na_lista():
    inventario = {"40": {"2026-11": {}}}
    assert [l.codigo for l in lojas_da_lista(inventario, "2026-11", "", LISTA_CADASTRO)] == ["40"]
    assert [l.codigo for l in lojas_da_lista(inventario, "2026-11", "fechada", LISTA_CADASTRO)] == ["40"]


def test_filtros_valem_juntos_e_loja_sem_cadastro_nao_passa():
    assert [l.codigo for l in lojas_da_lista(LISTA_INVENTARIO, "2026-11", "", LISTA_CADASTRO, consultor="Ana")] == ["9"]
    assert [l.codigo for l in lojas_da_lista(LISTA_INVENTARIO, "2026-11", "", LISTA_CADASTRO, supervisora="Bia")] == ["9", "10"]
    assert lojas_da_lista(LISTA_INVENTARIO, "2026-11", "", LISTA_CADASTRO, consultor="Caio", supervisora="Dora") == []


def test_loja_sem_cadastro_aparece_com_traco():
    linha = lojas_da_lista(LISTA_INVENTARIO, "2026-11", "77", LISTA_CADASTRO)[0]
    assert (linha.razao_social, linha.consultor) == ("—", "—")


def test_api_fora_do_ar_lista_so_codigo():
    linhas = lojas_da_lista(LISTA_INVENTARIO, "2026-11", "", {})
    assert [l.razao_social for l in linhas] == ["—", "—", "—"]
    assert [l.codigo for l in lojas_da_lista(LISTA_INVENTARIO, "2026-11", "10", {})] == ["10"]


def test_opcoes_de_filtro_vem_das_lojas_do_mes():
    assert opcoes_de_filtro(LISTA_INVENTARIO, "2026-11", LISTA_CADASTRO) == (["Ana", "Caio"], ["Bia"])
    assert opcoes_de_filtro({}, "2026-11", LISTA_CADASTRO) == ([], [])


# ---- Cabeçalho da Análise ----

def test_meses_da_loja_do_mais_novo_para_o_mais_antigo():
    assert meses_da_loja(INVENTARIO, "10") == ["2026-10", "2026-09"]
    assert meses_da_loja(INVENTARIO, "30") == []
    assert meses_da_loja(INVENTARIO, "nao_existe") == []


def test_rotulo_da_loja():
    assert rotulo_da_loja("9", LISTA_CADASTRO) == "9 · Alfa Ltda"
    assert rotulo_da_loja("77", LISTA_CADASTRO) == "77"
    assert rotulo_da_loja("3101", CADASTRO) == "3101"


def test_arquivos_do_mes_segue_a_ordem_e_ignora_o_que_nao_esta_nos_rotulos():
    rotulos = {"mapa": "Mapa", "estoque": "Estoque", "foto": "Foto"}
    situacao = arquivos_do_mes(rotulos, {"foto": "x", "metadata": "y"})
    assert [(a.rotulo, a.presente) for a in situacao] == [("Mapa", False), ("Estoque", False), ("Foto", True)]


# ---- Paginação ----

def test_primeira_pagina():
    p = paginar(list(range(45)), 1)
    assert p.itens == list(range(20))
    assert (p.pagina, p.total_paginas, p.inicio, p.fim, p.total) == (1, 3, 1, 20, 45)
    assert not p.tem_anterior and p.tem_proxima


def test_ultima_pagina_vem_incompleta():
    p = paginar(list(range(45)), 3)
    assert p.itens == list(range(40, 45))
    assert (p.inicio, p.fim) == (41, 45)
    assert p.tem_anterior and not p.tem_proxima


def test_pagina_alem_do_fim_volta_para_a_ultima():
    assert paginar(list(range(45)), 99).pagina == 3


def test_pagina_invalida_volta_para_a_primeira():
    assert paginar(list(range(45)), 0).pagina == 1
    assert paginar(list(range(45)), -5).pagina == 1


def test_lista_vazia_tem_uma_pagina_sem_itens():
    p = paginar([], 1)
    assert (p.itens, p.pagina, p.total_paginas, p.inicio, p.fim, p.total) == ([], 1, 1, 0, 0, 0)


def test_lista_exata_nao_cria_pagina_vazia():
    assert paginar(list(range(40)), 1).total_paginas == 2
