"""Relatório mensal: foto que falha não pode virar célula vazia sem aviso.
Rodar da raiz do projeto: python -m pytest tests"""
import io
import sys
import threading
import time
from pathlib import Path

import pytest
from openpyxl import load_workbook
from PIL import Image

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from modules.inventario import descobrir_inventario as descobrir_inventario_real
from modules.lojas_api import Loja
from modules.storage.base import ArquivoNaoEncontradoError, ItemPasta, StorageError
from scripts import relatorio_mensal as rm

MES = "2026-09"
LOJA = "0000"
FOTO = f"{LOJA}/{MES}/foto_antes.jpg"


def _foto_valida() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


class ArmazenamentoFalso:
    """`falhas_leitura`: quantas leituras falham antes de começar a funcionar."""

    def __init__(self, falhas_leitura=0, falhas_listagem=0, conteudo=None):
        self.falhas_leitura = falhas_leitura
        self.falhas_listagem = falhas_listagem
        self.conteudo = _foto_valida() if conteudo is None else conteudo
        self.leituras = 0

    def listar_pasta(self, _caminho):
        if self.falhas_listagem > 0:
            self.falhas_listagem -= 1
            raise StorageError("listagem falhou")
        return [ItemPasta(nome="foto_antes.jpg", e_pasta=False, caminho_completo=FOTO)]

    def ler_arquivo_bytes(self, _caminho):
        self.leituras += 1
        if self.falhas_leitura > 0:
            self.falhas_leitura -= 1
            raise StorageError("leitura falhou")
        return self.conteudo


SEMPRE = 10**6


@pytest.fixture(autouse=True)
def sem_espera_e_com_inventario(monkeypatch):
    monkeypatch.setattr(rm, "ESPERA_ENTRE_TENTATIVAS_S", 0)
    monkeypatch.setattr(rm, "descobrir_inventario", lambda _s: {
        LOJA: {MES: {"arquivos": {"foto_antes"}, "metadata": {}}},
    })


def _linha(storage, reserva=None) -> dict:
    return rm.montar_linhas(storage, {}, MES, storage_reserva=reserva)[0]


def test_foto_sem_falha_nao_gera_aviso():
    linha = _linha(ArmazenamentoFalso())
    assert linha["foto_antes"] is not None
    assert linha["avisos_foto"] == {}


def test_falha_passageira_e_recuperada_na_nova_tentativa():
    linha = _linha(ArmazenamentoFalso(falhas_leitura=1))
    assert linha["foto_antes"] is not None
    assert linha["avisos_foto"] == {}


def test_falha_na_listagem_nao_derruba_o_relatorio():
    linha = _linha(ArmazenamentoFalso(falhas_listagem=1))
    assert linha["foto_antes"] is not None
    assert linha["avisos_foto"] == {}


def test_conexao_principal_com_defeito_busca_de_novo_na_reserva():
    linha = _linha(ArmazenamentoFalso(falhas_leitura=SEMPRE), reserva=ArmazenamentoFalso())
    assert linha["foto_antes"] is not None
    assert linha["avisos_foto"] == {}


def test_tenta_ate_dar_certo_sem_desistir():
    principal = ArmazenamentoFalso(falhas_leitura=SEMPRE, falhas_listagem=SEMPRE)
    reserva = ArmazenamentoFalso(falhas_leitura=200, falhas_listagem=200)
    linha = _linha(principal, reserva=reserva)
    assert linha["foto_antes"] is not None
    assert linha["avisos_foto"] == {}
    assert reserva.falhas_leitura == 0 and reserva.falhas_listagem == 0


def test_espera_entre_tentativas_cresce_ate_o_maximo(monkeypatch):
    monkeypatch.setattr(rm, "ESPERA_ENTRE_TENTATIVAS_S", 0.5)
    esperas = []
    monkeypatch.setattr(rm.time, "sleep", esperas.append)
    _linha(ArmazenamentoFalso(falhas_leitura=10))
    assert esperas[:3] == [0.5, 1, 2]
    assert max(esperas) == rm.ESPERA_MAXIMA_ENTRE_TENTATIVAS_S


def test_foto_apagada_durante_a_geracao_nao_fica_tentando():
    storage = ArmazenamentoFalso()
    storage.listar_pasta = lambda _caminho: []
    linha = _linha(storage)
    assert linha["foto_antes"] is None
    assert linha["avisos_foto"] == {"foto_antes": rm.AVISO_FOTO_NAO_CARREGADA}


def test_foto_apagada_entre_listar_e_baixar_nao_fica_tentando():
    storage = ArmazenamentoFalso()

    def sumiu(_caminho):
        raise ArquivoNaoEncontradoError("apagada")

    storage.ler_arquivo_bytes = sumiu
    linha = _linha(storage)
    assert linha["avisos_foto"] == {"foto_antes": rm.AVISO_FOTO_NAO_CARREGADA}


def test_arquivo_que_nao_e_imagem_nao_insiste_e_avisa():
    principal = ArmazenamentoFalso(conteudo=b"isto nao e uma imagem")
    reserva = ArmazenamentoFalso()
    linha = _linha(principal, reserva=reserva)
    assert linha["foto_antes"] is None
    assert linha["avisos_foto"] == {"foto_antes": rm.AVISO_FOTO_ILEGIVEL}
    assert principal.leituras == 1
    assert reserva.leituras == 0


@pytest.mark.parametrize("aviso", [rm.AVISO_FOTO_NAO_CARREGADA, rm.AVISO_FOTO_ILEGIVEL])
def test_aviso_aparece_na_celula_da_foto_no_excel(tmp_path, aviso):
    linha = _linha(ArmazenamentoFalso())
    linha["foto_antes"] = None
    linha["avisos_foto"] = {"foto_antes": aviso}
    arquivo = tmp_path / "relatorio.xlsx"
    rm.gerar_planilha([linha], MES, arquivo)

    ws = load_workbook(arquivo)[f"Conferência {MES}"]
    cabecalho = [c.value for c in ws[1]]
    assert ws.cell(row=2, column=cabecalho.index("Foto Antes") + 1).value == aviso
    assert ws.cell(row=2, column=cabecalho.index("Foto Depois") + 1).value is None


def test_reserva_busca_em_paralelo():
    """Com a conexão principal ruim, a última busca não pode ser foto por foto."""
    lojas = {str(i): {MES: {"arquivos": {"foto_antes"}, "metadata": {}}} for i in range(12)}
    trava, ativos, pico = threading.Lock(), [0], [0]

    class Reserva(ArmazenamentoFalso):
        def ler_arquivo_bytes(self, caminho):
            with trava:
                ativos[0] += 1
                pico[0] = max(pico[0], ativos[0])
            time.sleep(0.05)
            with trava:
                ativos[0] -= 1
            return super().ler_arquivo_bytes(caminho)

    linhas = rm.montar_linhas(ArmazenamentoFalso(falhas_leitura=SEMPRE), {}, MES,
                              storage_reserva=Reserva(), inventario=lojas)
    assert pico[0] > 1
    assert rm.contar_fotos_nao_carregadas(linhas) == 0


# ---- Excel guardado ----

class ComArquivos(ArmazenamentoFalso):
    """Guarda em memória o que o relatório grava na pasta dele."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.arquivos = {}
        self.leituras_de_foto = 0
        self.gravacoes = 0
        self.falha_escrita = False
        self.falha_leitura_guardado = False

    def listar_pasta(self, caminho):
        if caminho == rm.PASTA_RELATORIOS:
            if not self.arquivos:
                raise ArquivoNaoEncontradoError(caminho)
            return [ItemPasta(nome=c.rsplit("/", 1)[-1], e_pasta=False, caminho_completo=c) for c in self.arquivos]
        return super().listar_pasta(caminho)

    def ler_arquivo_bytes(self, caminho):
        if caminho.startswith(rm.PASTA_RELATORIOS + "/"):
            if self.falha_leitura_guardado:
                raise StorageError("leitura falhou")
            if caminho not in self.arquivos:
                raise ArquivoNaoEncontradoError(caminho)
            return self.arquivos[caminho]
        self.leituras_de_foto += 1
        return super().ler_arquivo_bytes(caminho)

    def escrever_arquivo_bytes(self, caminho, conteudo):
        if self.falha_escrita:
            raise StorageError("escrita falhou")
        self.gravacoes += 1
        self.arquivos[caminho] = conteudo

    def apagar_arquivo(self, caminho):
        del self.arquivos[caminho]


def _api(**campos) -> dict:
    return {LOJA: Loja(**{"codigo": LOJA, "nome": "Loja", "consultor": "C", "supervisora": "S", "status": "Ativo", **campos})}


@pytest.fixture
def inventario(monkeypatch):
    dados = {LOJA: {MES: {"arquivos": {"foto_antes"}, "metadata": {}, "assinatura": "v1"}}}
    monkeypatch.setattr(rm, "descobrir_inventario", lambda _s: dados)
    return dados


def test_segundo_download_usa_o_excel_guardado_sem_baixar_fotos(inventario):
    storage = ComArquivos()
    primeiro = rm.gerar_relatorio(storage, _api(), MES)
    assert not primeiro.do_cache
    assert storage.leituras_de_foto == 1

    segundo = rm.gerar_relatorio(storage, _api(), MES)
    assert segundo.do_cache
    assert segundo.conteudo == primeiro.conteudo
    assert storage.leituras_de_foto == 1
    assert storage.gravacoes == 1


def test_envio_novo_no_mes_gera_de_novo_e_apaga_o_antigo(inventario):
    storage = ComArquivos()
    rm.gerar_relatorio(storage, _api(), MES)
    antigos = set(storage.arquivos)

    inventario[LOJA][MES]["assinatura"] = "v2"
    novo = rm.gerar_relatorio(storage, _api(), MES)
    assert not novo.do_cache
    assert len(storage.arquivos) == 1
    assert not antigos & set(storage.arquivos)


def test_guardado_de_outro_mes_nao_e_apagado(inventario):
    storage = ComArquivos()
    outro = f"{rm.PASTA_RELATORIOS}/conferencia_2026-10_abc.xlsx"
    storage.arquivos[outro] = b"outro mes"
    rm.gerar_relatorio(storage, _api(), MES)
    assert storage.arquivos[outro] == b"outro mes"


@pytest.mark.parametrize("campos", [
    {"consultor": "Outro consultor"}, {"status": "Inativo"}, {"razao_social": "Nova razão"},
    {"nome_fantasia": "Novo nome"}, {"data_inativacao": "2026-10-29"}, {"motivo_inativacao": "Encerrada"},
])
def test_mudanca_no_cadastro_da_loja_gera_de_novo(inventario, campos):
    storage = ComArquivos()
    rm.gerar_relatorio(storage, _api(), MES)
    assert not rm.gerar_relatorio(storage, _api(**campos), MES).do_cache


def test_marcar_loja_manualmente_fora_da_rede_gera_de_novo(inventario, monkeypatch):
    storage = ComArquivos()
    rm.gerar_relatorio(storage, _api(), MES)
    monkeypatch.setattr(rm.config, "LOJAS_FORA_DA_REDE_MANUAL", {MES: (LOJA,)})
    assert not rm.gerar_relatorio(storage, _api(), MES).do_cache


def test_sem_api_nao_guarda_nem_usa_guardado(inventario):
    storage = ComArquivos()
    rm.gerar_relatorio(storage, _api(), MES)
    guardados = dict(storage.arquivos)
    assert not rm.gerar_relatorio(storage, {}, MES).do_cache
    assert storage.arquivos == guardados


def test_relatorio_com_falha_de_conexao_recuperada_fica_guardado(inventario):
    storage = ComArquivos(falhas_leitura=3)
    rm.gerar_relatorio(storage, _api(), MES)
    assert rm.gerar_relatorio(storage, _api(), MES).do_cache


def test_relatorio_com_foto_que_nao_carregou_nao_fica_guardado(inventario):
    storage = ComArquivos()
    pastas_reais = storage.listar_pasta
    storage.listar_pasta = lambda c: pastas_reais(c) if c == rm.PASTA_RELATORIOS else []
    relatorio = rm.gerar_relatorio(storage, _api(), MES)
    assert relatorio.fotos_nao_carregadas == 1
    assert storage.arquivos == {}


def test_foto_ilegivel_fica_guardada_porque_o_defeito_e_do_arquivo(inventario):
    storage = ComArquivos(conteudo=b"isto nao e uma imagem")
    rm.gerar_relatorio(storage, _api(), MES)
    assert rm.gerar_relatorio(storage, _api(), MES).do_cache


def test_falha_ao_gravar_nao_atrapalha_a_entrega(inventario):
    storage = ComArquivos()
    storage.falha_escrita = True
    assert rm.gerar_relatorio(storage, _api(), MES).conteudo


def test_falha_ao_ler_o_guardado_gera_de_novo(inventario):
    storage = ComArquivos()
    rm.gerar_relatorio(storage, _api(), MES)
    storage.falha_leitura_guardado = True
    relatorio = rm.gerar_relatorio(storage, _api(), MES)
    assert not relatorio.do_cache
    assert relatorio.conteudo


def test_guardado_corrompido_gera_de_novo(inventario):
    storage = ComArquivos()
    rm.gerar_relatorio(storage, _api(), MES)
    caminho = next(iter(storage.arquivos))
    storage.arquivos[caminho] = b"quebrado"
    relatorio = rm.gerar_relatorio(storage, _api(), MES)
    assert not relatorio.do_cache
    assert load_workbook(io.BytesIO(relatorio.conteudo))
    assert storage.arquivos[caminho] != b"quebrado"


def test_excel_entregue_abre_e_tem_a_foto(inventario):
    relatorio = rm.gerar_relatorio(ComArquivos(), _api(), MES)
    assert len(load_workbook(io.BytesIO(relatorio.conteudo))[f"Conferência {MES}"]._images) == 1


def test_mes_sem_lojas_nao_gera_nada(inventario):
    storage = ComArquivos()
    assert rm.gerar_relatorio(storage, _api(), "2026-01") is None
    assert storage.arquivos == {}


def test_pasta_de_relatorios_nao_vira_loja_no_inventario():
    class Bucket:
        def listar_todos_arquivos_com_versao(self, _prefixo):
            return {f"{rm.PASTA_RELATORIOS}/conferencia_{MES}_abc.xlsx": "e1", f"{LOJA}/{MES}/foto_antes.jpg": "e2"}

        def ler_arquivo_bytes(self, _caminho):
            raise ArquivoNaoEncontradoError("sem índice")

        def escrever_arquivo_bytes(self, _caminho, _conteudo):
            pass

    assert set(descobrir_inventario_real(Bucket())) == {LOJA}


@pytest.mark.parametrize("aba", ["Conferência", "Checklist"])
def test_ultimo_envio_aparece_com_data_e_hora_de_brasilia(tmp_path, aba, monkeypatch):
    monkeypatch.setattr(rm, "descobrir_inventario", lambda _s: {
        LOJA: {MES: {"arquivos": {"foto_antes"}, "metadata": {"enviado_em": "2026-10-01T02:30:00+00:00"}}},
    })
    arquivo = tmp_path / "relatorio.xlsx"
    rm.gerar_planilha(rm.montar_linhas(ArmazenamentoFalso(), {}, MES), MES, arquivo)

    ws = load_workbook(arquivo)[f"{aba} {MES}"]
    cabecalho = [c.value for c in ws[1]]
    assert ws.cell(row=2, column=cabecalho.index("Último envio") + 1).value == "30/09/2026 23:30"


def test_conta_so_as_fotos_que_nao_carregaram():
    linhas = [
        {"avisos_foto": {"foto_antes": rm.AVISO_FOTO_NAO_CARREGADA, "foto_depois": rm.AVISO_FOTO_ILEGIVEL}},
        {"avisos_foto": {"foto_depois": rm.AVISO_FOTO_NAO_CARREGADA}},
        {"avisos_foto": {}},
    ]
    assert rm.contar_fotos_nao_carregadas(linhas) == 2
