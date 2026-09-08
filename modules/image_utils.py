"""Utilitários de imagem para a Aba 3 (Conferência).

O "modelo" de planograma às vezes chega em PDF em vez de JPG/PNG (ex.:
exportado direto de uma ferramenta de planejamento de gôndola) — este
módulo extrai uma página específica desse PDF como imagem, para exibir
lado a lado com as fotos de antes/depois (que já vêm como JPG/PNG direto).

Módulo separado de modules/pdf_export.py de propósito: aquele módulo GERA
PDF (reportlab); este LÊ PDF existente (PyMuPDF) — são bibliotecas e
preocupações diferentes, sem motivo pra compartilhar um único arquivo.
"""

from __future__ import annotations

import io

import fitz  # PyMuPDF
from PIL import Image


def extrair_pagina_como_imagem(conteudo_pdf: bytes, numero_pagina: int = 2, dpi: int = 144) -> bytes:
    """Extrai uma página específica de um PDF (1-indexado — numero_pagina=2
    é a segunda página) e retorna como bytes de imagem PNG, em resolução de
    tela (dpi típico de exibição, nem pesado nem pixelado).

    Se o PDF tiver menos páginas que numero_pagina, usa a última página
    disponível como fallback em vez de lançar exceção — um planograma às
    vezes vem com uma página só, e mostrar algo é melhor que quebrar a
    tela de conferência por causa disso.
    """
    documento = fitz.open(stream=conteudo_pdf, filetype="pdf")
    try:
        if documento.page_count == 0:
            raise ValueError("PDF não contém nenhuma página.")

        indice_pagina = min(numero_pagina - 1, documento.page_count - 1)
        indice_pagina = max(indice_pagina, 0)

        pagina = documento[indice_pagina]
        pixmap = pagina.get_pixmap(dpi=dpi)
        return pixmap.tobytes("png")
    finally:
        documento.close()


def gerar_previews_modelo(
    conteudo: bytes, extensao: str, dpi_impressao: int = 144, largura_max_tela: int = 1000
) -> tuple[bytes, bytes]:
    """Recebe os bytes originais do Modelo (pdf/jpg/jpeg/png) e devolve
    (bytes_tela, bytes_impressao) — duas imagens PNG leves que passam a
    ser o que o app usa no dia a dia, no lugar do arquivo original.

    A versão de impressão mantém a MESMA qualidade que o PDF exportado já
    usa hoje (144 DPI, sem regressão). A versão de tela é uma redução
    dessa mesma imagem, só pra carregar mais rápido na Conferência/Sugestão
    de GC — não precisa de qualidade de impressão.
    """
    if extensao == ".pdf":
        bytes_impressao = extrair_pagina_como_imagem(conteudo, numero_pagina=2, dpi=dpi_impressao)
    else:
        imagem_original = Image.open(io.BytesIO(conteudo)).convert("RGB")
        buffer = io.BytesIO()
        imagem_original.save(buffer, format="PNG")
        bytes_impressao = buffer.getvalue()

    imagem_impressao = Image.open(io.BytesIO(bytes_impressao))
    if imagem_impressao.width > largura_max_tela:
        proporcao = largura_max_tela / imagem_impressao.width
        nova_altura = int(imagem_impressao.height * proporcao)
        imagem_tela = imagem_impressao.resize((largura_max_tela, nova_altura), Image.LANCZOS)
    else:
        imagem_tela = imagem_impressao

    buffer_tela = io.BytesIO()
    imagem_tela.save(buffer_tela, format="PNG", optimize=True)
    return buffer_tela.getvalue(), bytes_impressao
