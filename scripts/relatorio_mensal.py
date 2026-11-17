"""
Gera o relatório mensal de conferência por loja (.xlsx): para cada loja
que enviou algo no ciclo informado, mostra código/nome fantasia/razão
social/consultor (todos vindos da API oficial do TI), data/hora do
último envio, checklist colorido de Mapa da Farmácia/Estoque/Modelo
(planograma), e as fotos de Antes/Depois embutidas como miniatura —
para a conferência visual poder ser feita direto na planilha, sem
precisar abrir o app.

Só entram lojas com pelo menos um arquivo no ciclo pedido (quem não
enviou nada não aparece — ver decisão registrada no pedido original).
Loja que enviou mas não está mais ativa na API ainda conta na conferência
daquele mês, só que em aba própria, separada das demais.

Uso:
    python scripts/relatorio_mensal.py --mes 2026-09
    python scripts/relatorio_mensal.py --mes 2026-09 --saida caminho/relatorio.xlsx

Lê as credenciais de .streamlit/secrets.toml (mesmo arquivo usado pelo
app) — não precisa do Streamlit rodando, é um script standalone.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tomllib
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU
from PIL import Image as PILImage

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # permite `python scripts/relatorio_mensal.py` de qualquer lugar

import config
import modules.lojas_api as lojas_api
from modules.file_resolver import localizar_item
from modules.inventario import descobrir_inventario
from modules.storage import get_storage_client
from modules.storage.base import ArquivoNaoEncontradoError, StorageError

# Brasil não tem horário de verão desde 2019 — offset fixo evita depender
# do pacote tzdata (que não vem no Windows).
FUSO_BRASILIA = timezone(timedelta(hours=-3))

TAMANHO_MINIATURA_PX = (200, 150)
ALTURA_LINHA_COM_FOTO = 115

CHAVES_CHECKLIST_TEXTO = ["mapa_farmacia", "estoque", "modelo"]
CHAVES_CHECKLIST_FOTO = ["foto_antes", "foto_depois"]
ROTULOS_COLUNA = {
    "mapa_farmacia": "Mapa da Farmácia",
    "estoque": "Estoque",
    "modelo": "Modelo (planograma)",
    "foto_antes": "Foto Antes",
    "foto_depois": "Foto Depois",
}

PREENCHIMENTO_OK = PatternFill(fill_type="solid", fgColor=config.COR_VERDE_BG.lstrip("#"))
PREENCHIMENTO_FALTA = PatternFill(fill_type="solid", fgColor=config.COR_VERMELHO_BG.lstrip("#"))
FONTE_OK = Font(color=config.COR_VERDE_TEXTO.lstrip("#"), bold=True)
FONTE_FALTA = Font(color=config.COR_VERMELHO_TEXTO.lstrip("#"), bold=True)
PREENCHIMENTO_CABECALHO = PatternFill(fill_type="solid", fgColor=config.COR_NAVY.lstrip("#"))
FONTE_CABECALHO = Font(bold=True, color="FFFFFF")
ALINHAMENTO_CENTRO = Alignment(horizontal="center", vertical="center")
MARGEM_MINIATURA_PX = 3

# Falha de conexão: tenta de novo até trazer a foto, sem limite de tempo nem de
# tentativas (decisão do dono do projeto: o relatório sai completo, mesmo que
# demore). Isto cobre também a queda no meio do download, que o retry do boto3
# não cobre (o corpo é lido depois de get_object voltar). A espera entre as
# levas só evita martelar um armazenamento fora do ar.
ESPERA_ENTRE_TENTATIVAS_S = 0.5
ESPERA_MAXIMA_ENTRE_TENTATIVAS_S = 30
DOWNLOADS_EM_PARALELO = 20
# Avisos só para o que insistir não resolve. Célula vazia quer dizer "não foi
# enviada", então estes casos nunca ficam vazios.
_FALHA_DE_CONEXAO = "falha de conexão"  # interno: nunca chega à planilha

# O Excel pronto fica no bucket, por isso vale para todos e sobrevive a um
# reinício do site. O inventário ignora caminhos com menos de 3 níveis, então
# esta pasta nunca vira uma "loja".
PASTA_RELATORIOS = "_relatorios"
# Aumente ao mudar o conteúdo ou o layout da planilha: o que está guardado
# nesse momento deixa de valer.
VERSAO_RELATORIO = 3  # 2: foto mais nova entre duplicadas; 3: "Último envio" no Checklist
AVISO_FOTO_NAO_CARREGADA = "Foto não carregada"
AVISO_FOTO_ILEGIVEL = "Foto ilegível"


def _ancorar_miniatura(ws, imagem_pil: PILImage.Image, linha: int, coluna: int) -> None:
    """Embute `imagem_pil` presa (TwoCellAnchor) dentro da célula
    (linha, coluna), em vez de só "flutuando" a partir do canto superior
    esquerdo por um tamanho em pixels (OneCellAnchor, o padrão do
    openpyxl.Workbook.add_image quando se passa só a referência da
    célula).

    Motivo: com OneCellAnchor, se uma linha vizinha (de uma loja sem
    foto) tiver altura menor que a da miniatura, a imagem flutuante
    "vaza" visualmente por cima das linhas/lojas seguintes — no Excel
    isso aparece como se a MESMA foto estivesse repetida em várias lojas
    diferentes (o que de fato aconteceu na primeira versão deste
    relatório). TwoCellAnchor com editAs="oneCell" prende as duas
    extremidades da imagem dentro dos limites exatos da célula de
    destino, então ela nunca escapa pra linha de outra loja — mesmo que,
    por algum motivo futuro, as alturas de linha voltem a variar."""
    img = XLImage(imagem_pil)
    col_idx, linha_idx = coluna - 1, linha - 1  # openpyxl usa índices 0-based aqui
    margem = pixels_to_EMU(MARGEM_MINIATURA_PX)
    origem = AnchorMarker(col=col_idx, colOff=margem, row=linha_idx, rowOff=margem)
    destino = AnchorMarker(
        col=col_idx, colOff=margem + pixels_to_EMU(img.width),
        row=linha_idx, rowOff=margem + pixels_to_EMU(img.height),
    )
    img.anchor = TwoCellAnchor(editAs="oneCell", _from=origem, to=destino)
    ws.add_image(img)


def _carregar_secrets() -> dict:
    caminho = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"
    if not caminho.exists():
        raise SystemExit(
            f"Não encontrei {caminho}. Copie .streamlit/secrets.toml.example e preencha "
            "com as credenciais reais antes de rodar este script."
        )
    with open(caminho, "rb") as f:
        return tomllib.load(f)


def _gerar_miniatura(conteudo: bytes) -> PILImage.Image:
    imagem = PILImage.open(io.BytesIO(conteudo)).convert("RGB")
    imagem.thumbnail(TAMANHO_MINIATURA_PX, PILImage.LANCZOS)
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    buffer.seek(0)
    # Reabre a partir do buffer: o openpyxl exige que a imagem tenha um
    # atributo `.fp` (arquivo de origem) pra conseguir serializar no
    # wb.save() — uma imagem só processada em memória (convert/thumbnail)
    # perde esse atributo e quebra lá na frente, depois de já ter baixado
    # tudo, se não for reaberta assim.
    return PILImage.open(buffer)


def _formatar_data(enviado_em: str | None) -> str:
    if not enviado_em:
        return "—"
    try:
        momento = datetime.fromisoformat(enviado_em)
    except ValueError:
        return enviado_em
    # Registros antigos foram gravados sem fuso, mas vinham do servidor
    # (UTC) — tratados como UTC. Os novos já trazem o offset.
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(FUSO_BRASILIA).strftime("%d/%m/%Y %H:%M")


def _formatar_data_inativacao(data_inativacao: str | None) -> str:
    """A API manda só a data, à meia-noite em UTC (ex.:
    "2026-10-29T00:00:00.000000Z") — sem conversão de fuso, que jogaria
    para o dia anterior."""
    if not data_inativacao:
        return "—"
    try:
        return datetime.fromisoformat(data_inativacao[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return data_inativacao


def _buscar_foto(storage, caminho_ciclo: str, chave: str) -> tuple[PILImage.Image | None, str | None]:
    """Uma tentativa. Devolve (miniatura, aviso): exatamente um dos dois vem
    preenchido."""
    try:
        item = localizar_item(storage, caminho_ciclo, config.FILE_SPECS[chave])
        if item is None:
            return None, AVISO_FOTO_NAO_CARREGADA  # apagada depois do inventário
        conteudo = storage.ler_arquivo_bytes(item.caminho_completo)
    except ArquivoNaoEncontradoError:
        return None, AVISO_FOTO_NAO_CARREGADA
    except StorageError:
        return None, _FALHA_DE_CONEXAO
    try:
        return _gerar_miniatura(conteudo), None
    except (OSError, ValueError, SyntaxError, PILImage.DecompressionBombError):
        # O problema está no próprio arquivo: baixar de novo não muda nada.
        return None, AVISO_FOTO_ILEGIVEL


def _buscar_ate_dar_certo(storage_reserva, linhas: list[dict], mes: str) -> None:
    """Repete, em paralelo e numa conexão separada (não herda um pool com
    defeito), até não sobrar nenhuma foto com falha de conexão."""

    def _tentar(pendente: tuple[dict, str]) -> None:
        linha, chave = pendente
        imagem, aviso = _buscar_foto(storage_reserva, f"{linha['codigo']}/{mes}", chave)
        linha[chave] = imagem
        if aviso:
            linha["avisos_foto"][chave] = aviso
        else:
            del linha["avisos_foto"][chave]

    espera = ESPERA_ENTRE_TENTATIVAS_S
    while True:
        pendentes = [
            (linha, chave)
            for linha in linhas
            for chave, aviso in linha["avisos_foto"].items()
            if aviso == _FALHA_DE_CONEXAO
        ]
        if not pendentes:
            return
        print(f"{len(pendentes)} foto(s) ainda sem baixar. Tentando de novo...", flush=True)
        time.sleep(espera)
        espera = min(espera * 2, ESPERA_MAXIMA_ENTRE_TENTATIVAS_S)
        with ThreadPoolExecutor(max_workers=DOWNLOADS_EM_PARALELO) as executor:
            list(executor.map(_tentar, pendentes))


def contar_fotos_nao_carregadas(linhas: list[dict]) -> int:
    return sum(
        aviso == AVISO_FOTO_NAO_CARREGADA
        for linha in linhas
        for aviso in linha["avisos_foto"].values()
    )


def _chave_ordenacao(linha: dict) -> tuple:
    """Alfabética por consultor (sem consultor conhecido vai pro fim),
    com código da loja (numérico) como desempate dentro do mesmo
    consultor. Acentos são ignorados — sem isso, "Álvaro" iria depois de
    "Zélia"."""
    consultor = linha["consultor"] or ""
    sem_consultor = 1 if not consultor else 0
    consultor = "".join(
        c for c in unicodedata.normalize("NFKD", consultor) if not unicodedata.combining(c)
    )
    try:
        codigo_num = int(linha["codigo"])
    except ValueError:
        codigo_num = float("inf")
    return (sem_consultor, consultor.lower(), codigo_num, linha["codigo"])


def _codigos_no_ciclo(inventario: dict, mes: str) -> list[str]:
    # Códigos de teste/diagnóstico (ex.: "_teste_substituicao_foto") nunca
    # são loja de verdade — código real de loja é sempre numérico (ver
    # lojas_api.Loja.codigo, vindo de `legacyId`).
    return sorted(
        loja for loja, ciclos in inventario.items() if mes in ciclos and not loja.startswith("_")
    )


def _dados_cadastrais(codigo: str, mes: str, lojas_api_map: dict[str, lojas_api.Loja]) -> dict:
    loja_oficial = lojas_api_map.get(codigo)
    return {
        "codigo": codigo,
        "nome_fantasia": loja_oficial.nome_fantasia if loja_oficial else None,
        "razao_social": loja_oficial.razao_social if loja_oficial else None,
        "consultor": loja_oficial.consultor if loja_oficial else None,
        "status": loja_oficial.status if loja_oficial else None,
        "data_inativacao": _formatar_data_inativacao(loja_oficial.data_inativacao if loja_oficial else None),
        "motivo_inativacao": (loja_oficial.motivo_inativacao if loja_oficial else None) or "—",
        # Mapa vazio = API indisponível: sem como afirmar que a loja saiu da rede.
        "fora_da_rede": codigo in config.LOJAS_FORA_DA_REDE_MANUAL.get(mes, ()) or (
            bool(lojas_api_map)
            and (loja_oficial is None or loja_oficial.status != lojas_api._STATUS_ATIVO)
        ),
    }


def montar_linhas(
    storage, lojas_api_map: dict[str, lojas_api.Loja], mes: str, storage_reserva=None, inventario: dict | None = None,
) -> list[dict]:
    """`storage_reserva`: cliente separado para buscar de novo as fotos que
    falharam. Sem ele, repete no próprio `storage`. `inventario`: quem já o
    tem em mãos evita listar o bucket de novo."""
    if inventario is None:
        inventario = descobrir_inventario(storage)
    codigos_no_ciclo = _codigos_no_ciclo(inventario, mes)

    def _montar(codigo: str) -> dict:
        ciclo_info = inventario[codigo][mes]
        arquivos_presentes = ciclo_info["arquivos"]
        metadata = ciclo_info["metadata"] or {}
        caminho_ciclo = f"{codigo}/{mes}"

        linha = {
            **_dados_cadastrais(codigo, mes, lojas_api_map),
            "enviado_em": metadata.get("enviado_em"),
            # A miniatura pode faltar mesmo com a foto no storage (falha de
            # leitura, formato inválido) — a aba de checklist usa isto aqui.
            "arquivos_presentes": arquivos_presentes,
            "avisos_foto": {},
        }
        for chave in CHAVES_CHECKLIST_TEXTO:
            linha[chave] = chave in arquivos_presentes
        for chave in CHAVES_CHECKLIST_FOTO:
            linha[chave] = None
            if chave in arquivos_presentes:
                linha[chave], aviso = _buscar_foto(storage, caminho_ciclo, chave)
                if aviso:
                    linha["avisos_foto"][chave] = aviso
        return linha

    with ThreadPoolExecutor(max_workers=min(DOWNLOADS_EM_PARALELO, len(codigos_no_ciclo)) or 1) as executor:
        linhas = list(executor.map(_montar, codigos_no_ciclo))

    _buscar_ate_dar_certo(storage_reserva or storage, linhas, mes)
    linhas.sort(key=_chave_ordenacao)
    return linhas


def _escrever_cabecalho(ws, cabecalho: list[str]) -> None:
    ws.append(cabecalho)
    for celula in ws[1]:
        celula.font = FONTE_CABECALHO
        celula.fill = PREENCHIMENTO_CABECALHO
        celula.alignment = ALINHAMENTO_CENTRO


def _escrever_check(ws, linha: int, coluna: int, presente: bool) -> None:
    celula = ws.cell(row=linha, column=coluna, value="✅" if presente else "❌")
    celula.fill = PREENCHIMENTO_OK if presente else PREENCHIMENTO_FALTA
    celula.font = FONTE_OK if presente else FONTE_FALTA
    celula.alignment = ALINHAMENTO_CENTRO


def _adicionar_aba_checklist(wb: Workbook, linhas: list[dict], mes: str) -> None:
    """Aba sem fotos, só com os checks de cada arquivo em colunas separadas
    — `linhas` já vem em ordem alfabética de consultor (ver
    _chave_ordenacao)."""
    ws = wb.create_sheet(f"Checklist {mes}")
    chaves = CHAVES_CHECKLIST_TEXTO + CHAVES_CHECKLIST_FOTO
    cabecalho = ["Código", "Nome fantasia", "Razão social", "Consultor", "Último envio"] + [ROTULOS_COLUNA[c] for c in chaves]
    _escrever_cabecalho(ws, cabecalho)

    for i, linha in enumerate(linhas, start=2):
        ws.cell(row=i, column=1, value=linha["codigo"])
        ws.cell(row=i, column=2, value=linha["nome_fantasia"] or "—")
        ws.cell(row=i, column=3, value=linha["razao_social"] or "—")
        ws.cell(row=i, column=4, value=linha["consultor"] or "—")
        ws.cell(row=i, column=5, value=_formatar_data(linha["enviado_em"]))
        for j, chave in enumerate(chaves, start=6):
            _escrever_check(ws, i, j, chave in linha["arquivos_presentes"])

    larguras = [12, 32, 32, 28, 18, 16, 12, 18, 12, 12]
    for idx, largura in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = largura

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cabecalho))}{len(linhas) + 1}"


def _preencher_aba_conferencia(ws, linhas: list[dict], colunas_extras: list[tuple[str, str, int]] = ()) -> None:
    """Aba com as miniaturas das fotos. `colunas_extras` são (rótulo, chave
    em `linha`, largura), inseridas logo depois de Consultor."""
    cabecalho = (
        ["Código", "Nome fantasia", "Razão social", "Consultor"]
        + [rotulo for rotulo, _, _ in colunas_extras]
        + ["Último envio"]
        + [ROTULOS_COLUNA[c] for c in CHAVES_CHECKLIST_TEXTO]
        + [ROTULOS_COLUNA[c] for c in CHAVES_CHECKLIST_FOTO]
    )
    _escrever_cabecalho(ws, cabecalho)

    col_envio = 5 + len(colunas_extras)
    col_foto_antes = col_envio + 1 + len(CHAVES_CHECKLIST_TEXTO)
    col_foto_depois = col_foto_antes + 1

    for i, linha in enumerate(linhas, start=2):
        ws.cell(row=i, column=1, value=linha["codigo"])
        ws.cell(row=i, column=2, value=linha["nome_fantasia"] or "—")
        ws.cell(row=i, column=3, value=linha["razao_social"] or "—")
        ws.cell(row=i, column=4, value=linha["consultor"] or "—")
        for j, (_, chave, _) in enumerate(colunas_extras, start=5):
            ws.cell(row=i, column=j, value=linha[chave] or "—")
        ws.cell(row=i, column=col_envio, value=_formatar_data(linha["enviado_em"]))

        for j, chave in enumerate(CHAVES_CHECKLIST_TEXTO, start=col_envio + 1):
            _escrever_check(ws, i, j, linha[chave])

        for coluna, chave in ((col_foto_antes, "foto_antes"), (col_foto_depois, "foto_depois")):
            imagem = linha[chave]
            if imagem is not None:
                _ancorar_miniatura(ws, imagem, i, coluna)
            elif chave in linha["avisos_foto"]:
                celula = ws.cell(row=i, column=coluna, value=linha["avisos_foto"][chave])
                celula.fill = PREENCHIMENTO_FALTA
                celula.font = FONTE_FALTA
                celula.alignment = ALINHAMENTO_CENTRO

        # Altura UNIFORME em todas as linhas de dado (não só nas que têm
        # foto) — ver docstring de _ancorar_miniatura sobre o motivo:
        # mesmo com TwoCellAnchor, uma linha vizinha mais baixa não tem
        # porquê existir aqui, e manter todas iguais é a garantia mais
        # simples de que isso nunca mais vira um problema.
        ws.row_dimensions[i].height = ALTURA_LINHA_COM_FOTO

    larguras = [12, 32, 32, 28] + [largura for _, _, largura in colunas_extras] + [18, 16, 12, 18, 28, 28]
    for idx, largura in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = largura

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cabecalho))}{len(linhas) + 1}"


def _montar_planilha(linhas: list[dict], mes: str) -> Workbook:
    ativas = [linha for linha in linhas if not linha["fora_da_rede"]]
    fora_da_rede = [linha for linha in linhas if linha["fora_da_rede"]]

    wb = Workbook()
    ws = wb.active
    ws.title = f"Conferência {mes}"
    _preencher_aba_conferencia(ws, ativas)

    _adicionar_aba_checklist(wb, ativas, mes)

    if fora_da_rede:
        _preencher_aba_conferencia(
            wb.create_sheet(f"Fora da rede {mes}"),
            fora_da_rede,
            [
                ("Status", "status", 14),
                ("Data de inativação", "data_inativacao", 18),
                ("Motivo da inativação", "motivo_inativacao", 30),
            ],
        )

    return wb


def gerar_planilha(linhas: list[dict], mes: str, caminho_saida: Path) -> None:
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    _montar_planilha(linhas, mes).save(caminho_saida)


def _assinatura_relatorio(inventario: dict, lojas_api_map: dict[str, lojas_api.Loja], mes: str) -> str | None:
    """Muda se qualquer coisa que aparece na planilha mudar: arquivos do mês
    de qualquer loja (o que inclui o metadata.json do "Último envio"), cadastro
    da loja na API ou marcação manual. None = não dá para garantir nada: sem
    API, a planilha sairia sem nomes e sem a separação de lojas."""
    if not lojas_api_map:
        return None
    lojas = []
    for codigo in _codigos_no_ciclo(inventario, mes):
        loja = lojas_api_map.get(codigo)
        lojas.append([
            codigo,
            inventario[codigo][mes].get("assinatura"),
            *(
                (loja.nome_fantasia, loja.razao_social, loja.consultor, loja.status,
                 loja.data_inativacao, loja.motivo_inativacao)
                if loja else ()
            ),
        ])
    dados = {
        "versao": VERSAO_RELATORIO,
        "manual": sorted(config.LOJAS_FORA_DA_REDE_MANUAL.get(mes, ())),
        "lojas": lojas,
    }
    return hashlib.sha1(json.dumps(dados, ensure_ascii=False).encode("utf-8")).hexdigest()


def _ler_guardado(storage, caminho: str) -> bytes | None:
    """Qualquer problema com o guardado (não existe, rede, arquivo quebrado)
    só significa gerar de novo."""
    try:
        conteudo = storage.ler_arquivo_bytes(caminho)
    except StorageError:
        return None
    return conteudo if zipfile.is_zipfile(io.BytesIO(conteudo)) else None


def _guardar(storage, mes: str, caminho: str, conteudo: bytes) -> None:
    """Falhar aqui não atrapalha quem pediu o relatório: só faz o próximo
    download gerar de novo. Apaga depois de gravar, e só os guardados deste
    mês, para o bucket ficar com um arquivo por mês."""
    try:
        storage.escrever_arquivo_bytes(caminho, conteudo)
        for item in storage.listar_pasta(PASTA_RELATORIOS):
            if item.nome.startswith(f"conferencia_{mes}_") and item.caminho_completo != caminho:
                storage.apagar_arquivo(item.caminho_completo)
    except StorageError:
        pass


@dataclass
class Relatorio:
    conteudo: bytes
    do_cache: bool
    lojas: int
    lojas_fora_da_rede: int | None = None  # None = veio pronto, sem remontar as linhas
    fotos_nao_carregadas: int = 0


def gerar_relatorio(
    storage, lojas_api_map: dict[str, lojas_api.Loja], mes: str, storage_reserva=None,
) -> Relatorio | None:
    """Entrega o Excel do mês: o guardado no bucket, se nada mudou desde que
    ele foi gerado, ou um novo. None = nenhuma loja com arquivos no mês.
    Relatório com foto que não carregou (apagada durante a geração) é
    entregue, mas não fica guardado: o aviso não vale para a próxima geração."""
    inventario = descobrir_inventario(storage)
    if not _codigos_no_ciclo(inventario, mes):
        return None

    assinatura = _assinatura_relatorio(inventario, lojas_api_map, mes)
    caminho = f"{PASTA_RELATORIOS}/conferencia_{mes}_{assinatura}.xlsx" if assinatura else None
    if caminho:
        guardado = _ler_guardado(storage, caminho)
        if guardado is not None:
            return Relatorio(guardado, do_cache=True, lojas=len(_codigos_no_ciclo(inventario, mes)))

    linhas = montar_linhas(storage, lojas_api_map, mes, storage_reserva=storage_reserva, inventario=inventario)
    buffer = io.BytesIO()
    _montar_planilha(linhas, mes).save(buffer)
    conteudo = buffer.getvalue()

    nao_carregadas = contar_fotos_nao_carregadas(linhas)
    if caminho and not nao_carregadas:
        _guardar(storage, mes, caminho, conteudo)
    return Relatorio(
        conteudo, do_cache=False, lojas=len(linhas),
        lojas_fora_da_rede=sum(linha["fora_da_rede"] for linha in linhas),
        fotos_nao_carregadas=nao_carregadas,
    )


def _carregar_lojas_api(secrets: dict) -> dict[str, lojas_api.Loja]:
    """Todas as lojas, inclusive as desligadas. {} se a API não responder."""
    api_cfg = secrets.get("api_lojas", {})
    if not (api_cfg.get("url") and api_cfg.get("api_key")):
        print("AVISO: [api_lojas] não configurado em secrets.toml — nome/razão social/consultor ficarão em branco.")
        return {}
    try:
        lojas = lojas_api.obter_lojas(api_cfg["url"], api_cfg["api_key"], apenas_ativas=False)
    except lojas_api.LojasAPIError as e:
        print(f"AVISO: falha ao consultar API de lojas ({e}) — nome/razão social/consultor ficarão em branco.")
        return {}
    return {loja.codigo: loja for loja in lojas}


def main():
    parser = argparse.ArgumentParser(description="Gera o relatório mensal de conferência por loja.")
    parser.add_argument("--mes", required=True, help="Ciclo no formato AAAA-MM, ex.: 2026-09")
    parser.add_argument(
        "--saida", default=None,
        help="Caminho do .xlsx de saída (padrão: relatorios/relatorio_conferencia_<mes>.xlsx)",
    )
    args = parser.parse_args()

    secrets = _carregar_secrets()
    storage = get_storage_client(secrets)
    lojas_api_map = _carregar_lojas_api(secrets)

    print(f"Descobrindo inventário e montando o relatório do ciclo {args.mes}...")
    relatorio = gerar_relatorio(storage, lojas_api_map, args.mes, storage_reserva=get_storage_client(secrets))
    if relatorio is None:
        print(f"Nenhuma loja encontrada com arquivos no ciclo {args.mes}.")
        return
    if relatorio.do_cache:
        print(f"{relatorio.lojas} loja(s) no ciclo {args.mes}. Nada mudou desde a última geração: usando o Excel guardado.")
    else:
        print(f"{relatorio.lojas} loja(s) encontrada(s) no ciclo {args.mes} ({relatorio.lojas_fora_da_rede} fora da rede).")
    if relatorio.fotos_nao_carregadas:
        print(
            f"AVISO: {relatorio.fotos_nao_carregadas} foto(s) não carregada(s), marcadas na planilha. "
            "Provavelmente foram apagadas durante a geração."
        )

    caminho_saida = (
        Path(args.saida) if args.saida
        else Path(__file__).resolve().parent.parent / "relatorios" / f"relatorio_conferencia_{args.mes}.xlsx"
    )
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    caminho_saida.write_bytes(relatorio.conteudo)
    print(f"Relatório gerado em: {caminho_saida}")


if __name__ == "__main__":
    main()
