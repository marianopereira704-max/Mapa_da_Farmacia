"""Geração do PDF da Sugestão de GC (Aba 2), inteiramente em memória.

Usa reportlab (puro Python, sem dependência de GTK/sistema) para desenhar
3 tipos de página: capa, páginas de conteúdo (tabela paginada) e
fechamento — replicando a identidade visual já usada no app (cores navy/
verde de config.py, mesmo estilo de tag de posição).

Todo o desenho é feito em baixo nível (reportlab.pdfgen.canvas), sem
flowables/Platypus, porque o layout é fixo e simples o suficiente para não
precisar do motor de fluxo de texto — isso também é o que permite calcular
a paginação analiticamente (linhas por página é uma conta, não uma
tentativa-e-erro).
"""

from __future__ import annotations

import io
import math

import pandas as pd
from PIL import Image, ImageDraw
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import getAscentDescent, stringWidth
from reportlab.pdfgen import canvas

import config

# ---------------------------------------------------------------------------
# Medidas fixas do layout das páginas de conteúdo (calculadas, não a olho:
# ver _calcular_linhas_por_pagina).
# ---------------------------------------------------------------------------

MARGEM = 1.5 * cm
DIAMETRO_LOGO_HEADER = 1.1 * cm
ALTURA_HEADER = 2.0 * cm
ALTURA_FAIXA_RODAPE = 0.5 * cm
ALTURA_RODAPE_RESERVADA = 1.3 * cm  # faixa + espaço p/ "Página X de Y" acima dela
ALTURA_HEADER_TABELA = 0.75 * cm
ALTURA_LINHA_TABELA = 0.85 * cm

LARGURA_COL_POS = 1.8 * cm
LARGURA_COL_FRENTES = 2.6 * cm
LARGURA_COL_OK = 1.8 * cm


# ---------------------------------------------------------------------------
# Logo: conversão webp -> PNG circular (reportlab não lê webp diretamente)
# ---------------------------------------------------------------------------

def _preparar_logo_circular(caminho_logo: str, tamanho_px: int = 800) -> ImageReader:
    """Abre o logo (webp), recorta para um quadrado central e aplica uma
    máscara circular (canal alfa). Gerado numa resolução alta e fixa; o
    reportlab reescala para o tamanho final em cada lugar onde é desenhado
    (capa, cabeçalho das páginas de conteúdo, fechamento)."""
    imagem = Image.open(caminho_logo).convert("RGBA")
    lado = min(imagem.size)
    esquerda = (imagem.width - lado) // 2
    topo = (imagem.height - lado) // 2
    imagem = imagem.crop((esquerda, topo, esquerda + lado, topo + lado))
    imagem = imagem.resize((tamanho_px, tamanho_px), Image.LANCZOS)

    mascara = Image.new("L", (tamanho_px, tamanho_px), 0)
    ImageDraw.Draw(mascara).ellipse((0, 0, tamanho_px, tamanho_px), fill=255)
    imagem.putalpha(mascara)

    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)


# ---------------------------------------------------------------------------
# Helpers de texto/desenho reutilizados entre os tipos de página
# ---------------------------------------------------------------------------

def _linha_base_centralizada(fonte: str, tamanho: float, y_centro: float) -> float:
    """Y da linha de base para que o texto fique verticalmente centralizado
    em y_centro, considerando ascent/descent reais da fonte (mais preciso
    do que um deslocamento fixo arbitrário)."""
    ascent, descent = getAscentDescent(fonte, tamanho)
    return y_centro - (ascent + descent) / 2


def _truncar_para_largura(texto: str, fonte: str, tamanho: float, largura_max: float) -> str:
    """Corta o texto (com reticências) se ele não couber em largura_max,
    medindo a largura real da fonte em vez de contar caracteres."""
    texto = str(texto)
    if stringWidth(texto, fonte, tamanho) <= largura_max:
        return texto
    reticencias = "..."
    while texto and stringWidth(texto + reticencias, fonte, tamanho) > largura_max:
        texto = texto[:-1]
    return (texto + reticencias) if texto else reticencias


def _desenhar_tag_posicao(c: canvas.Canvas, centro_x: float, centro_y: float, texto_posicao) -> None:
    """Retângulo navy arredondado com o número da posição em branco,
    centralizado — mesmo estilo visual da "etiqueta de prateleira" do app,
    simplificado para retângulo (sem o recorte em bico) para impressão."""
    largura, altura = 0.85 * cm, 0.6 * cm
    c.setFillColor(HexColor(config.COR_NAVY))
    c.roundRect(centro_x - largura / 2, centro_y - altura / 2, largura, altura, 3, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont("Helvetica-Bold", 8.5)
    baseline = _linha_base_centralizada("Helvetica-Bold", 8.5, centro_y)
    c.drawCentredString(centro_x, baseline, str(texto_posicao))


def _desenhar_checkbox(c: canvas.Canvas, centro_x: float, centro_y: float, tamanho: float) -> None:
    """Quadrado vazio (só contorno) para o gestor marcar à mão depois de
    imprimir — não tem preenchimento nem "x" nenhum."""
    c.setStrokeColor(HexColor("#B7BEC9"))
    c.setLineWidth(1)
    c.rect(centro_x - tamanho / 2, centro_y - tamanho / 2, tamanho, tamanho, fill=0, stroke=1)


# ---------------------------------------------------------------------------
# Página 1 — Capa
# ---------------------------------------------------------------------------

def _desenhar_capa(c: canvas.Canvas, largura: float, altura: float, logo_reader: ImageReader) -> None:
    c.saveState()

    c.setFillColor(HexColor(config.COR_NAVY))
    c.rect(0, 0, largura, altura, fill=1, stroke=0)

    largura_painel_esquerdo = 0.595 * largura
    centro_x = 0.2975 * largura

    # Faixa vertical verde (reta, não diagonal), separando o painel
    # esquerdo (logo + título) do restante da capa.
    faixa_v_x0 = 0.598 * largura
    faixa_v_x1 = 0.615 * largura
    c.setFillColor(HexColor(config.COR_VERDE))
    c.rect(faixa_v_x0, 0, faixa_v_x1 - faixa_v_x0, altura, fill=1, stroke=0)

    # Logo circular, centralizado no painel esquerdo.
    raio_logo = 0.234 * largura
    centro_y_logo = altura - 0.324 * altura  # 32.4% da altura, contada do topo
    diametro_logo = raio_logo * 2
    c.drawImage(
        logo_reader,
        centro_x - raio_logo, centro_y_logo - raio_logo,
        width=diametro_logo, height=diametro_logo,
        mask="auto",
    )

    # Faixa horizontal verde abaixo do logo, com o título centralizado.
    faixa_h_y0 = 0.3298 * altura  # contada de baixo
    faixa_h_y1 = 0.4684 * altura
    c.setFillColor(HexColor(config.COR_VERDE))
    c.rect(0, faixa_h_y0, largura_painel_esquerdo, faixa_h_y1 - faixa_h_y0, fill=1, stroke=0)

    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont("Helvetica", 17)
    y_centro_faixa = (faixa_h_y0 + faixa_h_y1) / 2
    baseline = _linha_base_centralizada("Helvetica", 17, y_centro_faixa)
    c.drawCentredString(centro_x, baseline, config.APP_TITLE)

    c.restoreState()
    c.showPage()


# ---------------------------------------------------------------------------
# Páginas de conteúdo — cabeçalho, tabela paginada e rodapé
# ---------------------------------------------------------------------------

def _calcular_linhas_por_pagina(altura_pagina: float) -> tuple[float, int]:
    """Calcula quantas linhas de produto cabem por página de conteúdo, a
    partir do espaço vertical realmente disponível (altura da página menos
    margens, cabeçalho, cabeçalho da tabela e rodapé) — não é um número
    fixo arbitrário.

    Retorna (y_topo_area_tabela, linhas_por_pagina).
    """
    y_topo_area_tabela = altura_pagina - MARGEM - ALTURA_HEADER - 0.4 * cm
    y_base_area_tabela = ALTURA_RODAPE_RESERVADA
    altura_disponivel = y_topo_area_tabela - y_base_area_tabela - ALTURA_HEADER_TABELA
    linhas_por_pagina = max(1, int(altura_disponivel // ALTURA_LINHA_TABELA))
    return y_topo_area_tabela, linhas_por_pagina


def _desenhar_cabecalho_conteudo(
    c: canvas.Canvas, largura: float, altura: float, logo_reader: ImageReader, subtitulo_ciclo: str
) -> None:
    """Logo pequena + título + subtítulo (mesmo texto usado no cabeçalho do
    app) e uma linha fina separando do conteúdo."""
    y_topo = altura - MARGEM
    raio = DIAMETRO_LOGO_HEADER / 2
    centro_y_logo = y_topo - raio
    x_logo = MARGEM

    c.drawImage(
        logo_reader,
        x_logo, centro_y_logo - raio,
        width=DIAMETRO_LOGO_HEADER, height=DIAMETRO_LOGO_HEADER,
        mask="auto",
    )

    x_texto = x_logo + DIAMETRO_LOGO_HEADER + 0.3 * cm
    c.setFillColor(HexColor(config.COR_NAVY))
    c.setFont("Helvetica-Bold", 13)
    c.drawString(x_texto, centro_y_logo + 3, config.APP_TITLE)

    c.setFillColor(HexColor("#7A8699"))
    c.setFont("Helvetica", 9)
    c.drawString(x_texto, centro_y_logo - 10, subtitulo_ciclo)

    y_linha = y_topo - ALTURA_HEADER
    c.setStrokeColor(HexColor("#E4E7EB"))
    c.setLineWidth(0.75)
    c.line(MARGEM, y_linha, largura - MARGEM, y_linha)


def _desenhar_rodape_conteudo(c: canvas.Canvas, largura: float, pagina_atual: int, total_paginas: int) -> None:
    c.setFont("Helvetica", 8)
    c.setFillColor(HexColor("#7A8699"))
    c.drawRightString(
        largura - MARGEM, ALTURA_FAIXA_RODAPE + 0.25 * cm, f"Página {pagina_atual} de {total_paginas}"
    )

    largura_navy = 0.75 * largura
    c.setFillColor(HexColor(config.COR_NAVY))
    c.rect(0, 0, largura_navy, ALTURA_FAIXA_RODAPE, fill=1, stroke=0)
    c.setFillColor(HexColor(config.COR_VERDE))
    c.rect(largura_navy, 0, largura - largura_navy, ALTURA_FAIXA_RODAPE, fill=1, stroke=0)


def _desenhar_tabela_pagina(
    c: canvas.Canvas, largura: float, produtos_pagina: list[dict], y_topo_tabela: float
) -> None:
    x0 = MARGEM
    x1 = largura - MARGEM
    largura_total = x1 - x0
    largura_produto = largura_total - LARGURA_COL_POS - LARGURA_COL_FRENTES - LARGURA_COL_OK

    colunas = [
        ("POS", LARGURA_COL_POS),
        ("PRODUTO", largura_produto),
        ("FRENTES", LARGURA_COL_FRENTES),
        ("OK", LARGURA_COL_OK),
    ]

    y = y_topo_tabela

    # Cabeçalho da tabela (fundo navy, texto branco).
    c.setFillColor(HexColor(config.COR_NAVY))
    c.rect(x0, y - ALTURA_HEADER_TABELA, largura_total, ALTURA_HEADER_TABELA, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont("Helvetica-Bold", 9)
    centro_y_cabecalho = y - ALTURA_HEADER_TABELA / 2
    baseline_cabecalho = _linha_base_centralizada("Helvetica-Bold", 9, centro_y_cabecalho)
    x_col = x0
    for nome, largura_col in colunas:
        if nome in ("POS", "OK"):
            c.drawCentredString(x_col + largura_col / 2, baseline_cabecalho, nome)
        else:
            c.drawString(x_col + 6, baseline_cabecalho, nome)
        x_col += largura_col
    y -= ALTURA_HEADER_TABELA

    # Linhas, alternando fundo branco/cinza-claro a cada duas linhas.
    for i, produto in enumerate(produtos_pagina):
        y_linha_topo = y - ALTURA_LINHA_TABELA
        if (i // 2) % 2 == 1:
            c.setFillColor(HexColor("#F7F8FA"))
            c.rect(x0, y_linha_topo, largura_total, ALTURA_LINHA_TABELA, fill=1, stroke=0)

        centro_y_linha = y_linha_topo + ALTURA_LINHA_TABELA / 2
        x_col = x0

        _desenhar_tag_posicao(c, x_col + LARGURA_COL_POS / 2, centro_y_linha, produto["posicao_label"])
        x_col += LARGURA_COL_POS

        c.setFillColor(HexColor("#1A1A1A"))
        c.setFont("Helvetica", 9.5)
        texto_produto = _truncar_para_largura(produto["produto"], "Helvetica", 9.5, largura_produto - 12)
        baseline = _linha_base_centralizada("Helvetica", 9.5, centro_y_linha)
        c.drawString(x_col + 6, baseline, texto_produto)
        x_col += largura_produto

        c.setFillColor(HexColor("#5B6472"))
        c.setFont("Helvetica", 9)
        texto_frentes = _truncar_para_largura(produto["frentes_label"], "Helvetica", 9, LARGURA_COL_FRENTES - 12)
        baseline = _linha_base_centralizada("Helvetica", 9, centro_y_linha)
        c.drawString(x_col + 6, baseline, texto_frentes)
        x_col += LARGURA_COL_FRENTES

        _desenhar_checkbox(c, x_col + LARGURA_COL_OK / 2, centro_y_linha, 0.4 * cm)

        y -= ALTURA_LINHA_TABELA


# ---------------------------------------------------------------------------
# Última página — Fechamento
# ---------------------------------------------------------------------------

def _desenhar_fechamento(c: canvas.Canvas, largura: float, altura: float, logo_reader: ImageReader) -> None:
    c.saveState()

    c.setFillColor(HexColor(config.COR_NAVY))
    c.rect(0, 0, largura, altura, fill=1, stroke=0)

    centro_x, centro_y = largura / 2, altura / 2
    raio_logo = 0.22 * min(largura, altura)

    # Sombra suave: camadas de círculos concêntricos com opacidade baixa,
    # da mais larga/transparente (mais afastada) até quase o raio do logo
    # (onde todas as camadas se sobrepõem = sombra mais "forte" bem na
    # borda do logo, esmaecendo pra fora). Puramente vetorial, sem blur.
    n_camadas = 14
    raio_maximo_sombra = raio_logo * 1.3
    c.setFillColor(HexColor("#0B2038"))
    for indice in range(n_camadas):
        fracao = indice / (n_camadas - 1)
        raio_camada = raio_maximo_sombra - fracao * (raio_maximo_sombra - raio_logo)
        c.setFillAlpha(0.035)
        c.circle(centro_x, centro_y, raio_camada, fill=1, stroke=0)
    c.setFillAlpha(1)

    diametro_logo = raio_logo * 2
    c.drawImage(
        logo_reader,
        centro_x - raio_logo, centro_y - raio_logo,
        width=diametro_logo, height=diametro_logo,
        mask="auto",
    )

    c.restoreState()
    c.showPage()


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def gerar_pdf_sugestao_gc(produtos_df: pd.DataFrame, consultor: str, loja: str, subtitulo_ciclo: str) -> bytes:
    """Recebe o DataFrame já filtrado/ordenado (saída de
    montar_tabela_sugestao_gc), gera o PDF completo em memória e retorna os
    bytes prontos para st.download_button.

    `subtitulo_ciclo` é o texto completo já formatado (ex.: "Mariano · Loja
    2043 · Julho" — o mesmo produzido por styles.montar_subtitulo()), usado
    tal qual no cabeçalho de cada página de conteúdo. `consultor`/`loja` não
    são usados no desenho em si (o design da capa é deliberadamente limpo,
    sem identificação de loja) — ficam disponíveis na assinatura para quem
    monta o nome do arquivo de download a partir do mesmo lugar.
    """
    largura, altura = A4
    logo_reader = _preparar_logo_circular(config.LOGO_PATH)

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    _desenhar_capa(c, largura, altura, logo_reader)

    y_topo_area_tabela, linhas_por_pagina = _calcular_linhas_por_pagina(altura)

    registros = produtos_df.to_dict("records") if len(produtos_df) else []
    total_paginas = math.ceil(len(registros) / linhas_por_pagina) if registros else 1

    for indice_pagina in range(total_paginas):
        _desenhar_cabecalho_conteudo(c, largura, altura, logo_reader, subtitulo_ciclo)

        if registros:
            inicio = indice_pagina * linhas_por_pagina
            fim = inicio + linhas_por_pagina
            produtos_pagina = [
                {
                    "posicao_label": registro["posicao"] if pd.notna(registro["posicao"]) else "–",
                    "produto": registro["produto"],
                    "frentes_label": registro["frentes"] if pd.notna(registro["frentes"]) else "–",
                }
                for registro in registros[inicio:fim]
            ]
            _desenhar_tabela_pagina(c, largura, produtos_pagina, y_topo_area_tabela)
        else:
            c.setFont("Helvetica", 11)
            c.setFillColor(HexColor("#7A8699"))
            c.drawCentredString(largura / 2, altura / 2, "Nenhum produto neste ajuste de mix.")

        _desenhar_rodape_conteudo(c, largura, indice_pagina + 1, total_paginas)
        c.showPage()

    _desenhar_fechamento(c, largura, altura, logo_reader)

    c.save()
    return buffer.getvalue()
