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

Uso:
    python scripts/relatorio_mensal.py --mes 2026-09
    python scripts/relatorio_mensal.py --mes 2026-09 --saida caminho/relatorio.xlsx

Lê as credenciais de .streamlit/secrets.toml (mesmo arquivo usado pelo
app) — não precisa do Streamlit rodando, é um script standalone.
"""

from __future__ import annotations

import argparse
import io
import sys
from concurrent.futures import ThreadPoolExecutor
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
from modules.storage.base import StorageError

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


def _buscar_foto(storage, caminho_ciclo: str, chave: str) -> PILImage.Image | None:
    item = localizar_item(storage, caminho_ciclo, config.FILE_SPECS[chave])
    if item is None:
        return None
    try:
        conteudo = storage.ler_arquivo_bytes(item.caminho_completo)
        return _gerar_miniatura(conteudo)
    except (StorageError, OSError):
        return None


def _chave_ordenacao(linha: dict) -> tuple:
    """Alfabética por consultor (sem consultor conhecido vai pro fim),
    com código da loja (numérico) como desempate dentro do mesmo
    consultor."""
    consultor = linha["consultor"] or ""
    sem_consultor = 1 if not consultor else 0
    try:
        codigo_num = int(linha["codigo"])
    except ValueError:
        codigo_num = float("inf")
    return (sem_consultor, consultor.lower(), codigo_num, linha["codigo"])


def montar_linhas(storage, lojas_api_map: dict[str, lojas_api.Loja], mes: str) -> list[dict]:
    inventario = descobrir_inventario(storage)
    # Códigos de teste/diagnóstico (ex.: "_teste_substituicao_foto") nunca
    # são loja de verdade — código real de loja é sempre numérico (ver
    # lojas_api.Loja.codigo, vindo de `legacyId`).
    codigos_no_ciclo = sorted(
        loja for loja, ciclos in inventario.items() if mes in ciclos and not loja.startswith("_")
    )

    def _montar(codigo: str) -> dict:
        ciclo_info = inventario[codigo][mes]
        arquivos_presentes = ciclo_info["arquivos"]
        metadata = ciclo_info["metadata"] or {}
        loja_oficial = lojas_api_map.get(codigo)
        caminho_ciclo = f"{codigo}/{mes}"

        linha = {
            "codigo": codigo,
            "nome_fantasia": loja_oficial.nome_fantasia if loja_oficial else None,
            "razao_social": loja_oficial.razao_social if loja_oficial else None,
            "consultor": loja_oficial.consultor if loja_oficial else None,
            "enviado_em": metadata.get("enviado_em"),
        }
        for chave in CHAVES_CHECKLIST_TEXTO:
            linha[chave] = chave in arquivos_presentes
        for chave in CHAVES_CHECKLIST_FOTO:
            linha[chave] = _buscar_foto(storage, caminho_ciclo, chave) if chave in arquivos_presentes else None
        return linha

    with ThreadPoolExecutor(max_workers=min(20, len(codigos_no_ciclo)) or 1) as executor:
        linhas = list(executor.map(_montar, codigos_no_ciclo))

    linhas.sort(key=_chave_ordenacao)
    return linhas


def gerar_planilha(linhas: list[dict], mes: str, caminho_saida: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = f"Conferência {mes}"

    cabecalho = (
        ["Código", "Nome fantasia", "Razão social", "Consultor", "Último envio"]
        + [ROTULOS_COLUNA[c] for c in CHAVES_CHECKLIST_TEXTO]
        + [ROTULOS_COLUNA[c] for c in CHAVES_CHECKLIST_FOTO]
    )
    ws.append(cabecalho)
    for celula in ws[1]:
        celula.font = FONTE_CABECALHO
        celula.fill = PREENCHIMENTO_CABECALHO
        celula.alignment = ALINHAMENTO_CENTRO

    col_foto_antes = 6 + len(CHAVES_CHECKLIST_TEXTO)
    col_foto_depois = col_foto_antes + 1

    for i, linha in enumerate(linhas, start=2):
        ws.cell(row=i, column=1, value=linha["codigo"])
        ws.cell(row=i, column=2, value=linha["nome_fantasia"] or "—")
        ws.cell(row=i, column=3, value=linha["razao_social"] or "—")
        ws.cell(row=i, column=4, value=linha["consultor"] or "—")
        ws.cell(row=i, column=5, value=_formatar_data(linha["enviado_em"]))

        for j, chave in enumerate(CHAVES_CHECKLIST_TEXTO, start=6):
            presente = linha[chave]
            celula = ws.cell(row=i, column=j, value="✅" if presente else "❌")
            celula.fill = PREENCHIMENTO_OK if presente else PREENCHIMENTO_FALTA
            celula.font = FONTE_OK if presente else FONTE_FALTA
            celula.alignment = ALINHAMENTO_CENTRO

        for coluna, chave in ((col_foto_antes, "foto_antes"), (col_foto_depois, "foto_depois")):
            imagem = linha[chave]
            if imagem is not None:
                _ancorar_miniatura(ws, imagem, i, coluna)

        # Altura UNIFORME em todas as linhas de dado (não só nas que têm
        # foto) — ver docstring de _ancorar_miniatura sobre o motivo:
        # mesmo com TwoCellAnchor, uma linha vizinha mais baixa não tem
        # porquê existir aqui, e manter todas iguais é a garantia mais
        # simples de que isso nunca mais vira um problema.
        ws.row_dimensions[i].height = ALTURA_LINHA_COM_FOTO

    larguras = [12, 32, 32, 28, 18, 16, 12, 18, 28, 28]
    for idx, largura in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = largura

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cabecalho))}{len(linhas) + 1}"

    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    wb.save(caminho_saida)


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

    lojas_api_map: dict[str, lojas_api.Loja] = {}
    api_cfg = secrets.get("api_lojas", {})
    if api_cfg.get("url") and api_cfg.get("api_key"):
        try:
            lojas = lojas_api.obter_lojas(api_cfg["url"], api_cfg["api_key"])
            lojas_api_map = {loja.codigo: loja for loja in lojas}
        except lojas_api.LojasAPIError as e:
            print(f"AVISO: falha ao consultar API de lojas ({e}) — nome/razão social/consultor ficarão em branco.")
    else:
        print("AVISO: [api_lojas] não configurado em secrets.toml — nome/razão social/consultor ficarão em branco.")

    print(f"Descobrindo inventário e montando o relatório do ciclo {args.mes}...")
    linhas = montar_linhas(storage, lojas_api_map, args.mes)
    if not linhas:
        print(f"Nenhuma loja encontrada com arquivos no ciclo {args.mes}.")
        return
    print(f"{len(linhas)} loja(s) encontrada(s) no ciclo {args.mes}.")

    caminho_saida = (
        Path(args.saida) if args.saida
        else Path(__file__).resolve().parent.parent / "relatorios" / f"relatorio_conferencia_{args.mes}.xlsx"
    )
    gerar_planilha(linhas, args.mes, caminho_saida)
    print(f"Relatório gerado em: {caminho_saida}")


if __name__ == "__main__":
    main()
