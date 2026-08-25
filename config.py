"""
Configuração central do projeto Mapa da Farmácia.

Princípio do projeto: nada de caminho, nome de coluna ou regra de negócio
fixo espalhado pelo código. Tudo que pode variar (estrutura de pastas,
nomes de arquivo, regras de cálculo) vive aqui, em um único lugar.

Se qualquer coisa mudar no futuro (caminho do OneDrive, nome de um arquivo,
a regra do "top 5" virar "top 7", etc.), a alteração deve ser feita
apenas neste arquivo.
"""

# ---------------------------------------------------------------------------
# Estrutura de pastas no armazenamento
# ---------------------------------------------------------------------------

# Nome da pasta central que guarda a base nacional de demanda (única,
# compartilhada por todas as lojas).
BASE_NACIONAL_FOLDER_NAME = "_Base"

# Estrutura esperada de subpastas por loja:
#   {cod_loja}/{AAAA-MM}/
# O consultor não faz parte do caminho (é um metadado — ver metadata.json
# de cada ciclo, README). Não há lista fixa de lojas/meses — tudo é
# descoberto dinamicamente a partir do que existe no armazenamento.

# ---------------------------------------------------------------------------
# Nomes de arquivo padronizados (dentro da pasta [COD Loja]/[AAAA-MM])
# ---------------------------------------------------------------------------

# Cada entrada define o(s) nome(s) base aceitos e as extensões válidas,
# na ordem de preferência de busca.
FILE_SPECS = {
    "mapa_farmacia": {
        "basenames": ["mapa_farmacia"],
        "extensions": [".xlsx", ".xls"],
        "obrigatorio": True,
    },
    "estoque": {
        "basenames": ["estoque"],
        "extensions": [".xlsx", ".xls"],
        "obrigatorio": True,
    },
    "modelo": {
        "basenames": ["modelo"],
        "extensions": [".pdf", ".jpg", ".jpeg", ".png"],
        "obrigatorio": False,
    },
    "foto_antes": {
        "basenames": ["foto_antes"],
        "extensions": [".jpg", ".jpeg", ".png"],
        "obrigatorio": False,
    },
    "foto_depois": {
        "basenames": ["foto_depois"],
        "extensions": [".jpg", ".jpeg", ".png"],
        "obrigatorio": False,
    },
    "ajuste_mix": {
        "basenames": ["ajuste_mix"],
        "extensions": [".json"],
        "obrigatorio": False,  # não existe até o consultor salvar pela 1a vez
    },
}

# Nome do arquivo da base nacional de demanda, dentro de _Base/
# Ordem de preferência: .parquet (versão já tratada pelo script de
# pré-processamento, muito mais rápida de ler) antes de .xlsx/.xls (versão
# bruta, aceita como fallback caso o pré-processamento ainda não tenha
# sido rodado naquele mês).
BASE_NACIONAL_SPEC = {
    "basenames": ["base_mercado"],
    "extensions": [".parquet", ".xlsx", ".xls"],
    "obrigatorio": True,
}

# ---------------------------------------------------------------------------
# Filtros aplicados no pré-processamento da base nacional
# (ver scripts/preparar_base_nacional.py)
# ---------------------------------------------------------------------------

# Prefixos de categoria a excluir — categorias RX_* são de tarja/balcão,
# fora do escopo de salão de loja que este projeto trabalha.
PREFIXOS_CATEGORIA_EXCLUIDOS = ["RX_"]

# Demanda mínima (YTD) para o produto ser mantido na base tratada.
# Produtos com movimentação nula ou muito baixa não têm utilidade prática
# como sugestão de incremento de mix, e só aumentam o tamanho do arquivo.
# Ajuste este valor conforme necessário — não precisa mexer em nenhum
# outro lugar do código.
DEMANDA_MINIMA_BASE_NACIONAL = 1000

# ---------------------------------------------------------------------------
# Regras de negócio (Aba 1 — Ajuste de Mix)
# ---------------------------------------------------------------------------

# Quantos produtos, ordenados por demanda de mercado, recebem sugestão
# automática de quantidade.
QTD_PRODUTOS_TOP_RANKING = 5

# Quantidade sugerida quando o produto tem estoque = 0 (ou EAN não
# encontrado no cruzamento, que é tratado da mesma forma).
QTD_SUGERIDA_ESTOQUE_ZERO = 3

# Mínimo de produtos com quantidade > 0 esperado no ajuste de mix.
# Abaixo disso, o app avisa o consultor antes de salvar (mas não bloqueia).
QTD_MINIMA_AJUSTE_MIX = 3

# ---------------------------------------------------------------------------
# Validação de EAN
# ---------------------------------------------------------------------------

# Faixa de dígitos aceita para um EAN ser considerado válido (cobre desde
# códigos curtos tipo EAN-8 até códigos internos maiores). Usado para
# diferenciar produto real de lixo/rodapé nas planilhas.
EAN_MIN_DIGITOS = 6
EAN_MAX_DIGITOS = 14

# ---------------------------------------------------------------------------
# Nomes de colunas esperados em cada planilha (usados na detecção
# dinâmica de cabeçalho — o código procura a linha que contém estes
# nomes, em vez de assumir uma posição fixa)
# ---------------------------------------------------------------------------

COLUNAS_MAPA_FARMACIA = {
    "posicao": "Posição",
    "ean": "EAN",
    "produto": "Produto",
    "frentes": "Frentes",
}

COLUNAS_ESTOQUE = {
    "id_loja": "Id lojas",
    "ean": "Cód. barras",
    "produto": "Produto",
    "estoque": "Unidades em estoque",
}

COLUNAS_BASE_NACIONAL = {
    "ean": "EAN",
    "produto": "PRODUTO",
    "demanda": "YTD'26",  # primeiro grupo de colunas (mercado) — ver data_loader
    "categoria": "CATEGORIA",
}

# ---------------------------------------------------------------------------
# Identidade visual (usada no styles.py e na geração do PDF)
# ---------------------------------------------------------------------------

COR_NAVY = "#17375E"
COR_NAVY_CLARO = "#4A6E90"
COR_VERDE = "#8DC63F"
COR_AMBAR_BG = "#FAEEDA"
COR_AMBAR_TEXTO = "#854F0B"
COR_VERDE_BG = "#EAF3DE"
COR_VERDE_TEXTO = "#27500A"
COR_AZUL_BG = "#DCEAFB"
COR_AZUL_TEXTO = "#1D4E89"
COR_ROXO_BG = "#F0EBFA"
COR_ROXO_TEXTO = "#5B3A9E"
COR_CINZA_BG = "#EEF0F3"
COR_CINZA_TEXTO = "#4B5563"

LOGO_PATH = "assets/logo_rmc.webp"

APP_TITLE = "Mapa da Farmácia"
PDF_OUTPUT_FILENAME = "Ajuste_Mix_RMC"

# ---------------------------------------------------------------------------
# Filtro de status (Aba 1 — Ajuste de Mix)
# ---------------------------------------------------------------------------

# Rótulos exibidos no selectbox de filtro por status de cada produto. O
# mapeamento rótulo -> código interno de status vive em
# modules.styles.codigo_status_do_filtro().
OPCOES_FILTRO_STATUS = [
    "Todos",
    "Estoque confirmado",
    "Sugestão automática",
    "Sem estoque",
    "Alterado manualmente",
]

# Altura máxima (em pixels) do quadro que lista os produtos na Aba 1. Acima
# desse valor o quadro rola internamente (scroll próprio), em vez de
# empurrar o botão Salvar e as barras de busca para fora da tela — são eles
# que devem ficar sempre visíveis, não a lista inteira de produtos. Ajustar
# aqui conforme a altura real de cada linha renderizada (o objetivo é
# mostrar confortavelmente uns 5-6 produtos completos antes de precisar
# rolar dentro do quadro).
ALTURA_PAINEL_PRODUTOS_PX = 480

# ---------------------------------------------------------------------------
# Cartões de foto (Aba 3 — Conferência)
# ---------------------------------------------------------------------------

# Altura fixa (em pixels) de cada um dos 3 cartões (Antes/Modelo/Depois),
# pra que fiquem sempre alinhados entre si independente de um deles ter
# imagem carregada e os outros não. A imagem usa object-fit: contain
# dentro dessa altura — aparece inteira, sem cortar bordas, com espaço
# vazio (letterbox) se a proporção da foto não bater exatamente.
ALTURA_CARTAO_FOTO_PX = 280

# ---------------------------------------------------------------------------
# Campos Loja / Consultor (Aba Upload)
# ---------------------------------------------------------------------------

# O campo Loja usa o componente externo streamlit-searchbox (um combobox
# React, com CSS próprio) e o campo Consultor usa st.text_input NATIVO do
# Streamlit — tecnologias diferentes que, por padrão, têm aparência
# diferente (altura, borda, cantos, cor de foco). Pedido explícito: os
# dois têm que parecer EXATAMENTE o mesmo tipo de campo. Estas constantes
# são a fonte única usada nos dois lugares — style_overrides do
# st_searchbox em app.py E o CSS do text_input Consultor em styles.py —
# pra garantir que não fiquem "quase iguais" por terem sido ajustados
# separadamente.
ALTURA_CAMPO_LOJA_CONSULTOR_PX = 42
RAIO_CAMPO_LOJA_CONSULTOR_PX = 8
BORDA_CAMPO_LOJA_CONSULTOR = "#E4E7EB"  # mesma cor de borda usada nos cartões (.mdf-painel-marker etc.)
FUNDO_CAMPO_LOJA_CONSULTOR = "#FFFFFF"
TEXTO_CAMPO_LOJA_CONSULTOR = "#31333F"  # cor de texto padrão do Streamlit (tema claro)
PLACEHOLDER_CAMPO_LOJA_CONSULTOR = "#8A93A3"  # mesmo cinza usado em .mdf-produto-meta/.mdf-foto-vazio
FONTE_CAMPO_LOJA_CONSULTOR_PX = 14
