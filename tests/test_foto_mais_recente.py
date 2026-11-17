"""Com duas versões da mesma foto na pasta, vale a enviada por último.
Rodar da raiz do projeto: python -m pytest tests"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import config
from modules.file_resolver import localizar_arquivo, localizar_item
from modules.storage.base import ItemPasta
from modules.storage.local import LocalFolderStorage
from modules.storage.spaces import DigitalOceanSpacesStorage
from scripts import relatorio_mensal as rm

PASTA = "0000/2026-09"
AGORA = datetime(2026, 9, 20, tzinfo=timezone.utc)
FOTO = config.FILE_SPECS["foto_antes"]


def _item(nome, dias_atras=None):
    data = None if dias_atras is None else AGORA - timedelta(days=dias_atras)
    return ItemPasta(nome=nome, e_pasta=False, caminho_completo=f"{PASTA}/{nome}", modificado_em=data)


def _nome(spec, itens):
    return localizar_item(None, PASTA, spec, itens=itens).nome


def test_mais_nova_vale_mesmo_com_extensao_de_menor_prioridade():
    assert _nome(FOTO, [_item("foto_antes.jpg", 5), _item("foto_antes.jpeg", 1)]) == "foto_antes.jpeg"
    assert _nome(FOTO, [_item("foto_antes.jpg", 5), _item("foto_antes.png", 1)]) == "foto_antes.png"


def test_mais_nova_vale_quando_ja_e_a_de_maior_prioridade():
    assert _nome(FOTO, [_item("foto_antes.jpg", 1), _item("foto_antes.jpeg", 5)]) == "foto_antes.jpg"


def test_foto_depois_segue_a_mesma_regra():
    spec = config.FILE_SPECS["foto_depois"]
    assert _nome(spec, [_item("foto_depois.jpg", 3), _item("foto_depois.jpeg", 2)]) == "foto_depois.jpeg"


def test_foto_unica_continua_sendo_achada():
    assert _nome(FOTO, [_item("foto_antes.png", 1)]) == "foto_antes.png"


def test_mesma_data_usa_a_prioridade_de_extensao():
    assert _nome(FOTO, [_item("foto_antes.png", 1), _item("foto_antes.jpg", 1)]) == "foto_antes.jpg"


def test_sem_data_usa_a_prioridade_de_extensao():
    assert _nome(FOTO, [_item("foto_antes.jpeg"), _item("foto_antes.jpg")]) == "foto_antes.jpg"


def test_item_com_data_vence_item_sem_data():
    assert _nome(FOTO, [_item("foto_antes.jpg"), _item("foto_antes.jpeg", 5)]) == "foto_antes.jpeg"


def test_nome_exato_antigo_vence_nome_aproximado_mais_novo():
    assert _nome(FOTO, [_item("foto_antes.jpg", 9), _item("foto_antes_copia.jpg", 1)]) == "foto_antes.jpg"


def test_entre_nomes_aproximados_tambem_vale_a_mais_nova():
    assert _nome(FOTO, [_item("foto_antes_a.jpg", 9), _item("foto_antes_b.jpg", 1)]) == "foto_antes_b.jpg"


def test_extensao_nao_aceita_continua_ignorada():
    assert _nome(FOTO, [_item("foto_antes.jpg", 9), _item("foto_antes.heic", 1)]) == "foto_antes.jpg"


def test_outros_arquivos_mantem_a_prioridade_de_extensao():
    spec = config.FILE_SPECS["estoque"]
    extensoes = spec["extensions"]
    if len(extensoes) > 1:
        itens = [_item(f"estoque{extensoes[0]}", 9), _item(f"estoque{extensoes[1]}", 1)]
        assert _nome(spec, itens) == f"estoque{extensoes[0]}"


def test_armazenamento_local_informa_a_data_e_a_mais_nova_e_lida(tmp_path):
    pasta = tmp_path / PASTA
    pasta.mkdir(parents=True)
    antiga, nova = pasta / "foto_antes.jpg", pasta / "foto_antes.jpeg"
    antiga.write_bytes(b"antiga")
    nova.write_bytes(b"nova")
    momento = AGORA.timestamp()
    os.utime(antiga, (momento - 86400, momento - 86400))
    os.utime(nova, (momento, momento))
    arquivo = localizar_arquivo(LocalFolderStorage(str(tmp_path)), PASTA, FOTO, "foto_antes")
    assert arquivo.conteudo == b"nova"


def test_spaces_informa_a_data_da_listagem():
    storage = DigitalOceanSpacesStorage.__new__(DigitalOceanSpacesStorage)
    storage.bucket, storage.prefixo = "b", ""
    pagina = {"Contents": [
        {"Key": f"{PASTA}/foto_antes.jpg", "LastModified": AGORA - timedelta(days=4)},
        {"Key": f"{PASTA}/foto_antes.jpeg", "LastModified": AGORA},
    ]}
    storage._cliente = SimpleNamespace(
        get_paginator=lambda _nome: SimpleNamespace(paginate=lambda **_k: [pagina])
    )
    assert localizar_item(storage, PASTA, FOTO).nome == "foto_antes.jpeg"


def test_relatorio_tem_versao_nova_para_nao_reaproveitar_excel_com_foto_antiga():
    assert rm.VERSAO_RELATORIO >= 2
