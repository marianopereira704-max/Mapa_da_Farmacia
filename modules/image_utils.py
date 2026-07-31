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

import fitz  # PyMuPDF


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
