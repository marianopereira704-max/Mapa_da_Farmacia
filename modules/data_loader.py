"""
Camada de leitura e validação das planilhas.

Princípio seguido em todo este módulo: nenhuma planilha é lida assumindo
uma posição fixa de linha/coluna. O cabeçalho é localizado pelo NOME das
colunas esperadas, e o fim dos dados é detectado por linha vazia / EAN
inválido — nunca por um número de linha fixo. Isso permite que cada loja
tenha uma planilha de tamanho diferente sem quebrar a leitura.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import pandas as pd

import config
from modules.storage import StorageError


# ---------------------------------------------------------------------------
# Exceções específicas do domínio
# ---------------------------------------------------------------------------

class PlanilhaInvalidaError(Exception):
    """Levantado quando uma planilha obrigatória não pode ser lida ou não
    contém as colunas esperadas."""


class MultiplasLojasEstoqueError(Exception):
    """Levantado quando o arquivo de estoque contém mais de um Id de loja.
    A camada de UI deve capturar isso e mostrar um pop-up de aviso."""

    def __init__(self, ids_encontrados: list):
        self.ids_encontrados = ids_encontrados
        super().__init__(
            f"Foram encontrados múltiplos IDs de loja no arquivo de estoque: "
            f"{ids_encontrados}"
        )


# ---------------------------------------------------------------------------
# Normalização e validação de EAN
# ---------------------------------------------------------------------------

def normalizar_ean(valor) -> str | None:
    """Normaliza um valor de EAN vindo de qualquer planilha para uma string
    só de dígitos, ou None se não for um EAN válido.

    Aceita EAN vindo como texto, número inteiro ou número float (Excel às
    vezes converte código de barras em número, o que pode gerar notação
    científica ou ponto decimal espúrio — tratamos os casos comuns aqui).
    """
    if valor is None:
        return None
    if isinstance(valor, float) and pd.isna(valor):
        return None

    if isinstance(valor, float):
        # Ex.: 7896094922532.0 -> "7896094922532"
        if valor.is_integer():
            texto = str(int(valor))
        else:
            return None
    else:
        texto = str(valor).strip()

    # Remove qualquer separador que não seja dígito (pontos, vírgulas,
    # espaços) — cobre casos de formatação numérica do Excel.
    texto = re.sub(r"[^0-9]", "", texto)

    if not texto:
        return None
    if not (config.EAN_MIN_DIGITOS <= len(texto) <= config.EAN_MAX_DIGITOS):
        return None

    return texto


# ---------------------------------------------------------------------------
# Detecção dinâmica de cabeçalho
# ---------------------------------------------------------------------------

def _localizar_linha_cabecalho(
    df_bruto: pd.DataFrame, colunas_esperadas: list[str], min_matches: int = 2
) -> int:
    """Procura, dentro das primeiras linhas do arquivo, a linha que contém
    o maior número de colunas esperadas (comparação case-insensitive e sem
    espaço nas pontas). Retorna o índice dessa linha.

    Levanta PlanilhaInvalidaError se nenhuma linha atingir o mínimo de
    colunas coincidentes.
    """
    alvo = {c.strip().lower() for c in colunas_esperadas}
    limite_busca = min(30, len(df_bruto))

    melhor_linha = None
    melhor_score = 0

    for i in range(limite_busca):
        valores_linha = {
            str(v).strip().lower()
            for v in df_bruto.iloc[i].tolist()
            if v is not None and str(v).strip() != "" and str(v).lower() != "nan"
        }
        score = len(alvo & valores_linha)
        if score > melhor_score:
            melhor_score = score
            melhor_linha = i

    if melhor_linha is None or melhor_score < min_matches:
        raise PlanilhaInvalidaError(
            f"Não foi possível localizar o cabeçalho esperado "
            f"({colunas_esperadas}) nas primeiras {limite_busca} linhas do arquivo."
        )

    return melhor_linha


def _mapear_colunas(header_row: pd.Series, colunas_esperadas: dict[str, str]) -> dict[str, int]:
    """Dado o conteúdo da linha de cabeçalho e um dicionário
    {chave_interna: nome_da_coluna_na_planilha}, retorna
    {chave_interna: índice_da_coluna}.

    Se um nome de coluna aparecer mais de uma vez (ex.: "YTD'26" duplicado
    na base nacional), retorna sempre a primeira ocorrência (mais à
    esquerda) — o que é o comportamento correto para essa planilha
    específica (grupo "mercado" vem antes do grupo "loja").
    """
    valores = [str(v).strip().lower() if v is not None else "" for v in header_row.tolist()]
    mapa = {}
    for chave, nome_coluna in colunas_esperadas.items():
        alvo = nome_coluna.strip().lower()
        try:
            idx = valores.index(alvo)
        except ValueError:
            mapa[chave] = None
            continue
        mapa[chave] = idx
    return mapa


def _detectar_fim_dos_dados(df: pd.DataFrame, col_ean_idx: int) -> pd.DataFrame:
    """Remove linhas de rodapé/lixo com base na validade do EAN. Usado nas
    planilhas onde o EAN é o identificador mais confiável de "isto é uma
    linha de produto real" (base nacional, estoque)."""
    eans_normalizados = df.iloc[:, col_ean_idx].apply(normalizar_ean)
    return df[eans_normalizados.notna()].copy()


def _detectar_fim_dos_dados_por_texto(df: pd.DataFrame, col_idx: int) -> pd.DataFrame:
    """Remove linhas de rodapé/lixo/vazias com base em uma coluna de texto
    (normalmente o nome do produto) estar preenchida.

    Usado no Mapa da Farmácia porque, ali, o EAN às vezes vem incompleto
    ou malformado para um produto real (ex.: código interno provisório) —
    e a regra do projeto é NUNCA excluir um produto só por causa disso.
    O nome do produto é um indicador mais confiável de fim de dados
    nesse caso específico.
    """
    coluna = df.iloc[:, col_idx]
    nulos = coluna.isna()
    textos = coluna.astype(str).str.strip()
    vazio_ou_lixo = (textos == "") | (textos.str.lower() == "none") | (textos.str.lower() == "nan")
    return df[~nulos & ~vazio_ou_lixo].copy()


# ---------------------------------------------------------------------------
# Leitura: Mapa da Farmácia (por loja/mês)
# ---------------------------------------------------------------------------

def carregar_mapa_farmacia(caminho_arquivo: str) -> pd.DataFrame:
    """Lê a planilha 'Lista de produtos' do Mapa da Farmácia de uma loja.

    Retorna DataFrame com colunas: posicao, ean, produto, frentes.
    """
    colunas_cfg = config.COLUNAS_MAPA_FARMACIA
    bruto = pd.read_excel(caminho_arquivo, header=None, dtype=object)

    linha_cabecalho = _localizar_linha_cabecalho(bruto, list(colunas_cfg.values()))
    mapa_idx = _mapear_colunas(bruto.iloc[linha_cabecalho], colunas_cfg)

    faltando = [k for k, v in mapa_idx.items() if v is None and k in ("ean", "produto")]
    if faltando:
        raise PlanilhaInvalidaError(
            f"Colunas obrigatórias não encontradas no Mapa da Farmácia: {faltando}"
        )

    dados = bruto.iloc[linha_cabecalho + 1:].copy()
    dados = _detectar_fim_dos_dados_por_texto(dados, mapa_idx["produto"])

    resultado = pd.DataFrame({
        "posicao": dados.iloc[:, mapa_idx["posicao"]].values if mapa_idx["posicao"] is not None else None,
        "ean": dados.iloc[:, mapa_idx["ean"]].apply(normalizar_ean).values,
        "ean_original": dados.iloc[:, mapa_idx["ean"]].astype(str).str.strip().values,
        "produto": dados.iloc[:, mapa_idx["produto"]].astype(str).str.strip().values,
        "frentes": dados.iloc[:, mapa_idx["frentes"]].values if mapa_idx["frentes"] is not None else None,
    })
    # Nota deliberada: NÃO removemos linhas com ean=None aqui. Um produto
    # real com EAN incompleto/malformado continua aparecendo na tabela —
    # ele só não vai conseguir ser cruzado com estoque/demanda (a UI
    # sinaliza isso separadamente, sem esconder o produto do consultor).
    resultado = resultado.reset_index(drop=True)
    return resultado


# ---------------------------------------------------------------------------
# Leitura: Estoque da loja
# ---------------------------------------------------------------------------

def carregar_estoque(caminho_arquivo: str) -> pd.DataFrame:
    """Lê a planilha de estoque de uma loja.

    Retorna DataFrame com colunas: ean, estoque.

    Levanta MultiplasLojasEstoqueError se detectar mais de um Id de loja
    distinto no arquivo — a UI deve capturar essa exceção e avisar o
    consultor via pop-up, sem decidir sozinho qual loja usar.
    """
    colunas_cfg = config.COLUNAS_ESTOQUE
    bruto = pd.read_excel(caminho_arquivo, header=None, dtype=object)

    linha_cabecalho = _localizar_linha_cabecalho(bruto, list(colunas_cfg.values()))
    mapa_idx = _mapear_colunas(bruto.iloc[linha_cabecalho], colunas_cfg)

    faltando = [k for k, v in mapa_idx.items() if v is None and k in ("ean", "estoque")]
    if faltando:
        raise PlanilhaInvalidaError(
            f"Colunas obrigatórias não encontradas na planilha de estoque: {faltando}"
        )

    dados = bruto.iloc[linha_cabecalho + 1:].copy()

    # Descarta linhas de rodapé/lixo (ex.: texto de "filtros aplicados" que
    # alguns exports jogam no fim do arquivo, ou linhas em branco). Uma
    # linha só é considerada dado de produto válido se a coluna de
    # estoque contiver um número — texto solto ou célula vazia não passa.
    estoque_numerico = pd.to_numeric(dados.iloc[:, mapa_idx["estoque"]], errors="coerce")
    dados = dados[estoque_numerico.notna()].copy()

    # Validação de loja única (se a coluna existir na planilha), já sem
    # o lixo de rodapé contaminando a checagem.
    if mapa_idx["id_loja"] is not None:
        ids_unicos = (
            dados.iloc[:, mapa_idx["id_loja"]]
            .dropna()
            .astype(str)
            .str.strip()
            .unique()
            .tolist()
        )
        if len(ids_unicos) > 1:
            raise MultiplasLojasEstoqueError(ids_unicos)

    resultado = pd.DataFrame({
        "ean": dados.iloc[:, mapa_idx["ean"]].apply(normalizar_ean).values,
        "estoque": pd.to_numeric(dados.iloc[:, mapa_idx["estoque"]], errors="coerce").values,
    })

    resultado = resultado.dropna(subset=["ean"]).reset_index(drop=True)
    resultado["estoque"] = resultado["estoque"].fillna(0)

    # Se um mesmo EAN aparecer mais de uma vez, soma as unidades (defensivo
    # — não deveria acontecer numa planilha de uma única loja, mas evita
    # comportamento indefinido caso aconteça).
    resultado = resultado.groupby("ean", as_index=False)["estoque"].sum()

    return resultado


# ---------------------------------------------------------------------------
# Leitura: Base nacional de demanda
# ---------------------------------------------------------------------------

def carregar_base_nacional_bruta(caminho_arquivo: str) -> pd.DataFrame:
    """Lê a base nacional de demanda a partir do arquivo BRUTO (.xlsx/.xls,
    como ele chega do sistema de origem), mantendo também a coluna de
    categoria — usada apenas pelo script de pré-processamento
    (scripts/preparar_base_nacional.py) para poder filtrar por categoria.

    Não usar esta função na aplicação Streamlit em produção: ela lê o
    arquivo xlsx completo (lento). O app deve consumir a versão já
    tratada (.parquet), lida por carregar_base_nacional().
    """
    colunas_cfg = config.COLUNAS_BASE_NACIONAL
    bruto = pd.read_excel(caminho_arquivo, header=None, dtype=object)

    linha_cabecalho = _localizar_linha_cabecalho(bruto, list(colunas_cfg.values()))
    mapa_idx = _mapear_colunas(bruto.iloc[linha_cabecalho], colunas_cfg)

    faltando = [k for k, v in mapa_idx.items() if v is None and k in ("ean", "demanda")]
    if faltando:
        raise PlanilhaInvalidaError(
            f"Colunas obrigatórias não encontradas na base nacional: {faltando}"
        )

    dados = bruto.iloc[linha_cabecalho + 1:].copy()
    dados = _detectar_fim_dos_dados(dados, mapa_idx["ean"])

    resultado = pd.DataFrame({
        "ean": dados.iloc[:, mapa_idx["ean"]].apply(normalizar_ean).values,
        "produto": (
            dados.iloc[:, mapa_idx["produto"]].astype(str).str.strip().values
            if mapa_idx["produto"] is not None else None
        ),
        "demanda": pd.to_numeric(dados.iloc[:, mapa_idx["demanda"]], errors="coerce").values,
        "categoria": (
            dados.iloc[:, mapa_idx["categoria"]].astype(str).str.strip().values
            if mapa_idx["categoria"] is not None else None
        ),
    })

    resultado = resultado.dropna(subset=["ean"]).reset_index(drop=True)
    resultado["demanda"] = resultado["demanda"].fillna(0)

    return resultado


@dataclass
class ResumoTratamentoBaseNacional:
    """Contagens de cada etapa do filtro aplicado por tratar_base_nacional()
    — usado tanto pelo script de linha de comando quanto pelo upload no
    app, pra mostrar pro usuário quantos produtos entraram/saíram."""
    total_original: int
    removidos_categoria: int
    removidos_demanda: int
    total_final: int


def tratar_base_nacional(bruta: pd.DataFrame) -> tuple[pd.DataFrame, ResumoTratamentoBaseNacional]:
    """Aplica os dois filtros de pré-processamento da base nacional de
    demanda sobre o DataFrame bruto devolvido por
    carregar_base_nacional_bruta(): remove categorias fora do escopo de
    salão de loja (RX_* — tarja/balcão, config.PREFIXOS_CATEGORIA_EXCLUIDOS)
    e remove produtos com demanda abaixo do mínimo configurado
    (config.DEMANDA_MINIMA_BASE_NACIONAL).

    Único ponto onde essa regra de negócio existe — tanto
    scripts/preparar_base_nacional.py (rodado localmente, fora do app)
    quanto o formulário de upload da base nacional em app.py (conversão
    automática ao subir um arquivo bruto) chamam esta mesma função, cada
    um decidindo como mostrar o resumo pro usuário.

    Retorna o DataFrame já reduzido a (ean, demanda) — pronto para salvar
    como .parquet — e um resumo com as contagens de cada etapa.
    """
    total_original = len(bruta)
    tratada = bruta.copy()

    removidos_categoria = 0
    if tratada["categoria"].notna().any() and config.PREFIXOS_CATEGORIA_EXCLUIDOS:
        mascara_excluida = tratada["categoria"].fillna("").str.startswith(
            tuple(config.PREFIXOS_CATEGORIA_EXCLUIDOS)
        )
        removidos_categoria = int(mascara_excluida.sum())
        tratada = tratada[~mascara_excluida].copy()

    mascara_baixa_demanda = tratada["demanda"] < config.DEMANDA_MINIMA_BASE_NACIONAL
    removidos_demanda = int(mascara_baixa_demanda.sum())
    tratada = tratada[~mascara_baixa_demanda].copy()

    tratada = tratada[["ean", "demanda"]].reset_index(drop=True)

    resumo = ResumoTratamentoBaseNacional(
        total_original=total_original,
        removidos_categoria=removidos_categoria,
        removidos_demanda=removidos_demanda,
        total_final=len(tratada),
    )
    return tratada, resumo


def carregar_base_nacional(fonte, extensao: str | None = None) -> pd.DataFrame:
    """Lê a base nacional de demanda para uso em produção.

    Aceita tanto a versão já tratada (.parquet/.csv, gerada pelo script
    scripts/preparar_base_nacional.py — leitura rápida) quanto a versão
    bruta (.xlsx/.xls — leitura lenta, usada apenas como fallback caso o
    pré-processamento ainda não tenha sido feito naquele mês).

    `fonte` pode ser:
      - uma string com o caminho do arquivo em disco (uso em scripts/testes
        locais) — nesse caso a extensão é inferida do próprio caminho;
      - um objeto tipo arquivo em memória (io.BytesIO, como o que vem do
        storage.ler_arquivo_bytes) — nesse caso `extensao` é obrigatório,
        pois não há nome de arquivo pra inferir o formato.

    Retorna DataFrame com colunas: ean, demanda.
    """
    if extensao is None:
        if not isinstance(fonte, str):
            raise ValueError(
                "extensao é obrigatório quando 'fonte' não é um caminho de arquivo "
                "(ex.: ao ler bytes vindos do storage)."
            )
        extensao = "." + fonte.rsplit(".", 1)[-1].lower()
    else:
        extensao = extensao.lower()

    if extensao == ".parquet":
        dados = pd.read_parquet(fonte)
    elif extensao == ".csv":
        dados = pd.read_csv(fonte, dtype={"ean": str})
    else:
        # Fallback: arquivo bruto ainda não passou pelo pré-processamento.
        dados = carregar_base_nacional_bruta(fonte)

    faltando = [c for c in ("ean", "demanda") if c not in dados.columns]
    if faltando:
        raise PlanilhaInvalidaError(
            f"Arquivo de base nacional não contém as colunas esperadas: {faltando}"
        )

    dados = dados[["ean", "demanda"]].copy()
    dados["ean"] = dados["ean"].apply(normalizar_ean)
    dados = dados.dropna(subset=["ean"]).reset_index(drop=True)
    dados["demanda"] = pd.to_numeric(dados["demanda"], errors="coerce").fillna(0)

    return dados


# ---------------------------------------------------------------------------
# Leitura: Retrato de Vendas + Estoque (resultado) — Aba 3, Resultado da ação
# ---------------------------------------------------------------------------

def carregar_retrato_vendas(caminho_arquivo: str) -> pd.DataFrame:
    """Lê a planilha "Retrato de Vendas" (export mensal de vendas por
    produto, usado no comparativo "depois" da feature Resultado da ação).

    IMPORTANTE — esse export já vem FILTRADO na origem para conter só
    produtos com venda > 0 no período (um dos filtros do dashboard de
    origem é "VENDAS VLR Liquido não é R$ 0,00", confirmado contra
    arquivos reais: Quantidade nunca é zero). Um produto ausente aqui
    significa "não teve venda no mês", não "não existe" — por isso a
    feature usa um segundo arquivo (Estoque atualizado, ver
    carregar_estoque em conjunto com montar_tabela_resultado_acao) para
    diferenciar esse caso de um produto que realmente saiu do mix.

    Retorna DataFrame com colunas: ean, quantidade_vendida, valor_vendido.

    Faz uma validação de integridade contra a linha "Total" que o export
    inclui no rodapé (se houver): soma a Quantidade e o Valor de todas as
    linhas de produto lidas e compara com os totais declarados nessa
    linha — se não baterem (fora de uma tolerância pequena, cobrindo só
    arredondamento), levanta PlanilhaInvalidaError em vez de seguir com
    dado potencialmente incompleto.
    """
    colunas_cfg = config.COLUNAS_RETRATO_VENDAS
    bruto = pd.read_excel(caminho_arquivo, header=None, dtype=object)

    linha_cabecalho = _localizar_linha_cabecalho(bruto, list(colunas_cfg.values()))
    mapa_idx = _mapear_colunas(bruto.iloc[linha_cabecalho], colunas_cfg)

    faltando = [k for k, v in mapa_idx.items() if v is None]
    if faltando:
        raise PlanilhaInvalidaError(
            f"Colunas obrigatórias não encontradas no Retrato de Vendas: {faltando}"
        )

    dados = bruto.iloc[linha_cabecalho + 1:].copy()

    # Localiza a linha "Total" de rodapé (se existir) ANTES de descartar
    # lixo — usada só para a validação de integridade abaixo, nunca entra
    # no resultado final. Varre TODAS as colunas da linha (não só a
    # esperada) porque a palavra "Total" pode estar em qualquer coluna,
    # dependendo de como o dashboard de origem monta o export.
    linha_total = None
    indice_linha_total = None
    for idx, linha in dados.iterrows():
        valores_linha = {str(v).strip().lower() for v in linha.tolist() if v is not None}
        if "total" in valores_linha:
            linha_total = linha
            indice_linha_total = idx
            break

    # Descarta linhas de rodapé/lixo (o texto de "Filtros aplicados",
    # linhas em branco) — só é dado de produto válido se a coluna
    # Quantidade contiver um número (mesmo critério usado em
    # carregar_estoque). A própria linha Total é excluída aqui também,
    # explicitamente por índice — ela também tem Quantidade numérica
    # (sobreviveria ao filtro) e, se não fosse excluída aqui, seria
    # somada em dobro na validação abaixo (uma vez como "declarado", outra
    # como parte do "lido").
    quantidade_numerica = pd.to_numeric(dados.iloc[:, mapa_idx["quantidade"]], errors="coerce")
    dados = dados[quantidade_numerica.notna()].copy()
    if indice_linha_total is not None:
        dados = dados[dados.index != indice_linha_total].copy()

    # Somas de validação tiradas AQUI, sobre TODAS as linhas de produto
    # (antes do dropna de EAN abaixo) — de propósito. Planilhas reais têm
    # linhas de produto com EAN ausente/malformado (confirmado em arquivo
    # real: 3 linhas), que a linha "Total" do rodapé CONTA mas que não
    # podem ser cruzadas por EAN (e por isso saem do `resultado` final).
    # Validar aqui, antes desse descarte, garante que a checagem seja
    # "lemos o arquivo certo", não "toda linha tem EAN" — essa segunda
    # checagem já existe em outro lugar (o app mostra o produto sem
    # esconder, só sinaliza "EAN não localizado", mesmo padrão do Mapa da
    # Farmácia e do Estoque).
    soma_quantidade_lida = pd.to_numeric(dados.iloc[:, mapa_idx["quantidade"]], errors="coerce").sum()
    soma_valor_lido = pd.to_numeric(dados.iloc[:, mapa_idx["valor"]], errors="coerce").sum()

    resultado = pd.DataFrame({
        "ean": dados.iloc[:, mapa_idx["ean"]].apply(normalizar_ean).values,
        "quantidade_vendida": pd.to_numeric(dados.iloc[:, mapa_idx["quantidade"]], errors="coerce").values,
        "valor_vendido": pd.to_numeric(dados.iloc[:, mapa_idx["valor"]], errors="coerce").values,
    })
    resultado = resultado.dropna(subset=["ean"]).reset_index(drop=True)
    resultado["valor_vendido"] = resultado["valor_vendido"].fillna(0)

    # Se um mesmo EAN aparecer mais de uma vez, soma (defensivo — mesmo
    # padrão de carregar_estoque).
    resultado = resultado.groupby("ean", as_index=False).agg(
        quantidade_vendida=("quantidade_vendida", "sum"),
        valor_vendido=("valor_vendido", "sum"),
    )

    if linha_total is not None:
        soma_quantidade_declarada = pd.to_numeric(
            pd.Series([linha_total.iloc[mapa_idx["quantidade"]]]), errors="coerce"
        ).iloc[0]
        soma_valor_declarado = pd.to_numeric(
            pd.Series([linha_total.iloc[mapa_idx["valor"]]]), errors="coerce"
        ).iloc[0]

        soma_quantidade_real = soma_quantidade_lida
        soma_valor_real = soma_valor_lido

        diverge_quantidade = (
            pd.notna(soma_quantidade_declarada)
            and abs(soma_quantidade_real - soma_quantidade_declarada) > 1
        )
        diverge_valor = (
            pd.notna(soma_valor_declarado)
            and abs(soma_valor_real - soma_valor_declarado) > max(1.0, 0.005 * abs(soma_valor_declarado))
        )
        if diverge_quantidade or diverge_valor:
            raise PlanilhaInvalidaError(
                "Os totais lidos do Retrato de Vendas não batem com a linha 'Total' da "
                f"planilha (Quantidade: {soma_quantidade_real:.0f} lida vs. "
                f"{soma_quantidade_declarada:.0f} declarada na planilha; Valor: "
                f"R$ {soma_valor_real:,.2f} lido vs. R$ {soma_valor_declarado:,.2f} "
                "declarado). A planilha pode estar incompleta ou corrompida — confira o "
                "arquivo antes de reenviar."
            )

    return resultado


def carregar_estoque_resultado(caminho_arquivo: str) -> pd.DataFrame:
    """Lê a planilha de Estoque (o MESMO arquivo "antes" já usado no
    Ajuste de Mix) capturando também as colunas de venda dos últimos 3
    meses — usadas só pela feature Resultado da ação (Aba 3) para
    calcular o preço médio e o faturamento mensal "antes" de cada
    produto. Não substitui carregar_estoque(), que continua minimalista
    (ean, estoque) para o uso já existente no Ajuste de Mix — é uma
    segunda leitura do mesmo arquivo, com mais colunas.

    Retorna DataFrame com colunas: ean, estoque, estoque_venda_rs,
    unidades_vendidas_3m.

    Levanta MultiplasLojasEstoqueError nas mesmas condições de
    carregar_estoque().
    """
    colunas_cfg = config.COLUNAS_ESTOQUE_RESULTADO
    bruto = pd.read_excel(caminho_arquivo, header=None, dtype=object)

    linha_cabecalho = _localizar_linha_cabecalho(bruto, list(colunas_cfg.values()))
    mapa_idx = _mapear_colunas(bruto.iloc[linha_cabecalho], colunas_cfg)

    faltando = [k for k, v in mapa_idx.items() if v is None and k != "id_loja"]
    if faltando:
        raise PlanilhaInvalidaError(
            "Colunas necessárias para o cálculo do Resultado da ação não encontradas "
            f"na planilha de estoque: {faltando}"
        )

    dados = bruto.iloc[linha_cabecalho + 1:].copy()

    estoque_numerico = pd.to_numeric(dados.iloc[:, mapa_idx["estoque"]], errors="coerce")
    dados = dados[estoque_numerico.notna()].copy()

    if mapa_idx["id_loja"] is not None:
        ids_unicos = (
            dados.iloc[:, mapa_idx["id_loja"]]
            .dropna()
            .astype(str)
            .str.strip()
            .unique()
            .tolist()
        )
        if len(ids_unicos) > 1:
            raise MultiplasLojasEstoqueError(ids_unicos)

    resultado = pd.DataFrame({
        "ean": dados.iloc[:, mapa_idx["ean"]].apply(normalizar_ean).values,
        "estoque": pd.to_numeric(dados.iloc[:, mapa_idx["estoque"]], errors="coerce").values,
        "estoque_venda_rs": pd.to_numeric(dados.iloc[:, mapa_idx["estoque_venda_rs"]], errors="coerce").values,
        "unidades_vendidas_3m": pd.to_numeric(dados.iloc[:, mapa_idx["unidades_vendidas_3m"]], errors="coerce").values,
    })
    resultado = resultado.dropna(subset=["ean"]).reset_index(drop=True)
    resultado["estoque"] = resultado["estoque"].fillna(0)
    resultado["estoque_venda_rs"] = resultado["estoque_venda_rs"].fillna(0)
    resultado["unidades_vendidas_3m"] = resultado["unidades_vendidas_3m"].fillna(0)

    resultado = resultado.groupby("ean", as_index=False).agg(
        estoque=("estoque", "sum"),
        estoque_venda_rs=("estoque_venda_rs", "sum"),
        unidades_vendidas_3m=("unidades_vendidas_3m", "sum"),
    )

    return resultado


# ---------------------------------------------------------------------------
# Ordenação por posição (compartilhada entre Ajuste de Mix e Sugestão de GC)
# ---------------------------------------------------------------------------

def _posicao_ordenavel(v):
    """Converte o valor de 'posicao' para uma chave numérica de ordenação.
    Posições não numéricas (ou ausentes) vão para o final."""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 10**9


def _ordenar_por_posicao(tabela: pd.DataFrame) -> pd.DataFrame:
    """Reordena um DataFrame pela coluna 'posicao' (ordem da gôndola no
    Mapa da Farmácia), com posições não numéricas jogadas para o final.
    Usada tanto no Ajuste de Mix quanto na Sugestão de GC, para que as
    duas telas sigam sempre a mesma ordem física de prateleira."""
    tabela = tabela.copy()
    tabela["_ordem"] = tabela["posicao"].apply(_posicao_ordenavel)
    tabela = tabela.sort_values("_ordem").drop(columns="_ordem").reset_index(drop=True)
    return tabela


# ---------------------------------------------------------------------------
# Montagem da tabela do Ajuste de Mix (Aba 1)
# ---------------------------------------------------------------------------

def montar_tabela_ajuste_mix(
    mapa_df: pd.DataFrame,
    estoque_df: pd.DataFrame,
    base_df: pd.DataFrame,
) -> pd.DataFrame:
    """Cruza as 3 fontes por EAN e monta a tabela final da Aba 1, já com
    a sugestão automática de quantidade aplicada.

    A sugestão automática existe para apontar produtos de INCREMENTO de
    mix — por isso só considera produtos que a loja NÃO TEM (estoque ==
    0) e cujo EAN foi cruzado com sucesso (ean_valido). Produtos com
    estoque > 0 nunca recebem sugestão automática, mesmo que estejam no
    topo do ranking de demanda: eles já estão no mix da loja, então não
    fazem sentido como sugestão de inclusão.

    Regra:
      1. Filtra estoque == 0 e ean_valido == True.
      2. Ordena esse subconjunto por demanda de mercado (decrescente).
      3. Os top config.QTD_PRODUTOS_TOP_RANKING desse subconjunto recebem
         origem="auto" e quantidade_sugerida=config.QTD_SUGERIDA_ESTOQUE_ZERO.
      4. Todos os demais (estoque > 0, ou estoque == 0 fora do top N, ou
         ean_valido == False) recebem origem=None e quantidade=estoque atual.

    O ranking por demanda serve só para ESCOLHER quem recebe a sugestão
    automática — a ORDEM DE EXIBIÇÃO da tabela final continua sendo pela
    posição original do Mapa da Farmácia (gôndola), não pelo ranking.

    Colunas de saída:
      posicao, ean, ean_original, ean_valido, produto, frentes, estoque,
      demanda, quantidade_sugerida, origem ("auto" | None), quantidade
    """
    tabela = mapa_df.merge(base_df, on="ean", how="left")
    tabela = tabela.merge(estoque_df, on="ean", how="left")

    # EAN não encontrado em qualquer uma das duas fontes -> trata como 0,
    # conforme regra de negócio definida.
    tabela["demanda"] = tabela["demanda"].fillna(0)
    tabela["estoque"] = tabela["estoque"].fillna(0)

    # Sinaliza produtos cujo EAN não pôde ser normalizado/cruzado (ex.:
    # código incompleto na origem) — nunca são excluídos da tabela, mas
    # não podem receber sugestão automática (não há como confiar no
    # cruzamento de demanda/estoque para eles).
    tabela["ean_valido"] = tabela["ean"].notna()

    top_n = config.QTD_PRODUTOS_TOP_RANKING
    qtd_sugerida_zero = config.QTD_SUGERIDA_ESTOQUE_ZERO

    # Candidatos à sugestão automática: só quem a loja não tem (estoque
    # zerado) e com EAN cruzado — ranqueados por demanda dentro desse
    # subconjunto, não da tabela inteira.
    candidatos_auto = tabela[(tabela["estoque"] <= 0) & tabela["ean_valido"]]
    indices_auto = (
        candidatos_auto.sort_values("demanda", ascending=False)
        .head(top_n)
        .index
    )

    # Nota: usamos NaN (não None) como marcador de "sem sugestão", porque
    # ao montar uma Series numérica o pandas converte None em NaN de
    # qualquer forma — usar NaN explicitamente evita checagens
    # inconsistentes (`v is not None` falha silenciosamente contra NaN).
    tabela["quantidade_sugerida"] = float("nan")
    tabela.loc[indices_auto, "quantidade_sugerida"] = float(qtd_sugerida_zero)

    tabela["origem"] = None
    tabela.loc[indices_auto, "origem"] = "auto"

    # Quantidade inicial exibida/editável: sugestão automática se houver,
    # senão o estoque atual (comportamento de partida antes de qualquer
    # edição do consultor).
    tabela["quantidade"] = tabela.apply(
        lambda r: r["quantidade_sugerida"] if pd.notna(r["quantidade_sugerida"]) else r["estoque"],
        axis=1,
    )

    # Reordena por posição original do Mapa da Farmácia para exibição
    # (o ranking por demanda foi usado só para decidir o top N, não é a
    # ordem final de exibição na Aba 1 — a ordem de exibição segue a
    # gôndola, mais intuitiva para o consultor revisar a categoria inteira).
    tabela = _ordenar_por_posicao(tabela)

    return tabela[[
        "posicao", "ean", "ean_original", "ean_valido", "produto", "frentes",
        "estoque", "demanda", "quantidade_sugerida", "origem", "quantidade",
    ]]


# ---------------------------------------------------------------------------
# Ajuste de mix salvo + montagem da tabela da Sugestão de GC (Aba 2)
# ---------------------------------------------------------------------------

def carregar_ajuste_mix_salvo(storage, caminho_ciclo: str) -> dict | None:
    """Lê o ajuste_mix.json salvo (se existir) para a loja/ciclo informado.

    Retorna None se o arquivo ainda não existe (consultor não salvou nada
    ainda nesta loja/ciclo) — não é um erro, é um estado válido que a UI
    trata mostrando uma mensagem própria, em vez de quebrar a página.
    """
    caminho = f"{caminho_ciclo}/ajuste_mix.json"
    try:
        conteudo = storage.ler_arquivo_bytes(caminho)
    except StorageError:
        return None
    return json.loads(conteudo)


def montar_tabela_sugestao_gc(mapa_df: pd.DataFrame, ajuste_salvo: dict | None) -> pd.DataFrame:
    """Cruza os produtos salvos no ajuste de mix (quantidade > 0) com o
    Mapa da Farmácia, para obter posição e frentes de cada um.

    O cruzamento é feito por ean_original — presente em ambas as fontes e
    sempre preenchido, diferente de 'ean' (que é None para produtos com
    EAN inválido, mas que ainda assim precisam aparecer aqui se foram
    salvos no ajuste de mix). Produtos do ajuste salvo que não forem mais
    encontrados no mapa_df atual (ex.: o mapa_farmacia mudou desde o
    último save) são ignorados silenciosamente na tabela retornada — a
    lista deles fica disponível em `resultado.attrs["produtos_nao_encontrados"]`
    (lista de dicts com ean_original/quantidade) para quem quiser avisar o
    usuário, sem que isso mude o uso principal (quem só quer o DataFrame
    continua chamando a função exatamente como antes).

    Retorna DataFrame com colunas: posicao, ean_original, produto, frentes,
    quantidade, ean_valido — ordenado pela posição original (mesma lógica
    de _ordenar_por_posicao usada em montar_tabela_ajuste_mix). Se
    `ajuste_salvo` for None (ou não tiver produtos), retorna um DataFrame
    vazio com essas mesmas colunas.
    """
    colunas_saida = ["posicao", "ean_original", "produto", "frentes", "quantidade", "ean_valido"]

    if not ajuste_salvo or not ajuste_salvo.get("produtos"):
        vazio = pd.DataFrame(columns=colunas_saida)
        vazio.attrs["produtos_nao_encontrados"] = []
        return vazio

    produtos_salvos = pd.DataFrame(ajuste_salvo["produtos"])[["ean_original", "quantidade"]]

    # mapa_df (saída crua de carregar_mapa_farmacia) não tem 'ean_valido'
    # pronto — essa coluna só existe na tabela já enriquecida do Ajuste de
    # Mix. Derivamos aqui do mesmo jeito: EAN normalizado com sucesso.
    mapa_com_validade = mapa_df.assign(ean_valido=mapa_df["ean"].notna())

    tabela = produtos_salvos.merge(
        mapa_com_validade[["posicao", "ean_original", "produto", "frentes", "ean_valido"]],
        on="ean_original",
        how="left",
        indicator=True,
    )

    nao_encontrados = (
        tabela[tabela["_merge"] == "left_only"][["ean_original", "quantidade"]]
        .to_dict("records")
    )
    tabela = tabela[tabela["_merge"] == "both"].drop(columns="_merge").reset_index(drop=True)

    tabela = _ordenar_por_posicao(tabela)

    resultado = tabela[colunas_saida].copy()
    resultado.attrs["produtos_nao_encontrados"] = nao_encontrados
    return resultado


# ---------------------------------------------------------------------------
# Montagem da tabela do Resultado da ação (Aba 3 — Conferência)
# ---------------------------------------------------------------------------

def montar_tabela_resultado_acao(
    mapa_df: pd.DataFrame,
    ajuste_salvo: dict | None,
    estoque_resultado_df: pd.DataFrame,
    retrato_df: pd.DataFrame,
    estoque_atualizado_df: pd.DataFrame,
) -> pd.DataFrame:
    """Monta a tabela da feature "Resultado da ação" (Aba 3 — Conferência):
    compara o faturamento mensal "antes" (calculado a partir do mesmo
    Estoque já usado no Ajuste de Mix) com o "depois" (Retrato de Vendas),
    produto a produto.

    A base da lista é a mesma do ajuste de mix SALVO (ajuste_mix.json) —
    mesma lógica de montar_tabela_sugestao_gc, incluindo a ordenação pela
    posição de gôndola — e não a lista completa do Mapa da Farmácia: o
    resultado só faz sentido para os produtos que o consultor efetivamente
    decidiu acompanhar naquele ciclo. Produtos que tiveram venda no
    Retrato mas NÃO estavam no Estoque "antes" são adicionados ao FINAL
    da lista com status "novo" (sem posição de gôndola definida, já que
    não faziam parte do ajuste original).

    Cálculo do "antes" (por produto, a partir de estoque_resultado_df —
    ver carregar_estoque_resultado):
      preço médio         = Estoque venda (R$) ÷ Unidades em estoque
      unidades vendidas/mês = Unidades vendidas (3 meses) ÷ 3
      faturamento/mês     = unidades vendidas/mês × preço médio
    Quando o estoque atual é 0, o preço médio não pode ser calculado por
    essa fórmula — tratado como 0 (aproximação conservadora: sem
    referência de estoque, não há como estimar o faturamento "antes"
    desse produto a partir dela).

    Status por produto:
      "sem_dado"          — EAN não localizado (não dá pra cruzar nada)
      "normal"            — teve venda no Retrato (cresceu, caiu ou ficou
                             estável — a UI decide a partir dos números)
      "sem_venda_periodo" — ausente do Retrato, mas o Estoque atualizado
                             confirma que o produto continua em estoque
                             (neutro — só não vendeu nesse mês, não saiu
                             do mix)
      "deixou_de_vender"  — ausente do Retrato E ausente/zerado no
                             Estoque atualizado (saiu do mix de verdade)
      "novo"               — vendeu no período mas não estava no Estoque
                             "antes" (produto novo na loja)

    Retorna DataFrame com colunas: posicao, ean_original, ean, ean_valido,
    produto, frentes, status, unidades_antes, valor_unitario_antes,
    faturamento_antes, unidades_depois, faturamento_depois,
    crescimento_rs, crescimento_pct. Também expõe, em
    resultado.attrs["produtos_nao_encontrados"], os itens do ajuste_mix
    salvo que não foram mais encontrados no Mapa da Farmácia atual (mesma
    convenção de montar_tabela_sugestao_gc). Se `ajuste_salvo` for None
    (ou não tiver produtos), retorna um DataFrame vazio com essas mesmas
    colunas.
    """
    colunas_saida = [
        "posicao", "ean_original", "ean", "ean_valido", "produto", "frentes", "status",
        "unidades_antes", "valor_unitario_antes", "faturamento_antes",
        "unidades_depois", "faturamento_depois", "crescimento_rs", "crescimento_pct",
    ]

    if not ajuste_salvo or not ajuste_salvo.get("produtos"):
        vazio = pd.DataFrame(columns=colunas_saida)
        vazio.attrs["produtos_nao_encontrados"] = []
        return vazio

    produtos_salvos = pd.DataFrame(ajuste_salvo["produtos"])[["ean_original", "quantidade"]]
    mapa_com_validade = mapa_df.assign(ean_valido=mapa_df["ean"].notna())

    base = produtos_salvos.merge(
        mapa_com_validade[["posicao", "ean_original", "produto", "frentes", "ean_valido", "ean"]],
        on="ean_original",
        how="left",
        indicator=True,
    )
    nao_encontrados = (
        base[base["_merge"] == "left_only"][["ean_original", "quantidade"]]
        .to_dict("records")
    )
    base = base[base["_merge"] == "both"].drop(columns=["_merge", "quantidade"]).reset_index(drop=True)
    base = _ordenar_por_posicao(base)

    # ---- Cálculo do "antes" ----
    base = base.merge(estoque_resultado_df, on="ean", how="left")
    for col in ("estoque", "estoque_venda_rs", "unidades_vendidas_3m"):
        base[col] = base[col].fillna(0)

    base["valor_unitario_antes"] = base.apply(
        lambda r: (r["estoque_venda_rs"] / r["estoque"]) if r["estoque"] > 0 else 0.0,
        axis=1,
    )
    base["unidades_antes"] = base["unidades_vendidas_3m"] / 3
    base["faturamento_antes"] = base["unidades_antes"] * base["valor_unitario_antes"]

    # ---- Cálculo do "depois" ----
    base = base.merge(
        retrato_df.rename(columns={
            "quantidade_vendida": "unidades_depois",
            "valor_vendido": "faturamento_depois",
        }),
        on="ean",
        how="left",
        indicator="_em_retrato",
    )
    base["_ausente_no_retrato"] = base["_em_retrato"] == "left_only"
    base["unidades_depois"] = base["unidades_depois"].fillna(0)
    base["faturamento_depois"] = base["faturamento_depois"].fillna(0)

    estoque_atualizado_por_ean = (
        estoque_atualizado_df.set_index("ean")["estoque"]
        if len(estoque_atualizado_df) else pd.Series(dtype=float)
    )

    def _status_linha(row):
        if not row["ean_valido"]:
            return "sem_dado"
        if not row["_ausente_no_retrato"]:
            return "normal"
        estoque_atual = estoque_atualizado_por_ean.get(row["ean"], 0.0)
        if estoque_atual and estoque_atual > 0:
            return "sem_venda_periodo"
        return "deixou_de_vender"

    base["status"] = base.apply(_status_linha, axis=1)
    base = base.drop(columns=["_em_retrato", "_ausente_no_retrato", "estoque", "estoque_venda_rs", "unidades_vendidas_3m"])

    # ---- Produtos novos: venderam no período, mas não estavam no
    # Estoque "antes" (nem já presentes na base acima) ----
    eans_antes = set(estoque_resultado_df["ean"].dropna())
    eans_base = set(base["ean"].dropna())
    novos_df = retrato_df[
        ~retrato_df["ean"].isin(eans_antes) & ~retrato_df["ean"].isin(eans_base)
    ].copy()

    if len(novos_df) > 0:
        novos_df = novos_df.sort_values("valor_vendido", ascending=False)
        info_produto = (
            mapa_df.dropna(subset=["ean"]).drop_duplicates(subset="ean")[["ean", "produto", "frentes"]]
        )
        novos_df = novos_df.merge(info_produto, on="ean", how="left")
        novos_df["produto"] = novos_df["produto"].fillna("Produto não identificado no Mapa da Farmácia")
        novos = pd.DataFrame({
            "posicao": None,
            "ean_original": novos_df["ean"],
            "ean": novos_df["ean"],
            "ean_valido": True,
            "produto": novos_df["produto"],
            "frentes": novos_df.get("frentes"),
            "status": "novo",
            "unidades_antes": 0.0,
            "valor_unitario_antes": 0.0,
            "faturamento_antes": 0.0,
            "unidades_depois": novos_df["quantidade_vendida"],
            "faturamento_depois": novos_df["valor_vendido"],
        })
        base = pd.concat([base, novos], ignore_index=True)

    # ---- Crescimento ----
    base["crescimento_rs"] = base["faturamento_depois"] - base["faturamento_antes"]
    base["crescimento_pct"] = base.apply(
        lambda r: (r["crescimento_rs"] / r["faturamento_antes"] * 100) if r["faturamento_antes"] > 0 else None,
        axis=1,
    )

    resultado = base[colunas_saida].reset_index(drop=True)
    resultado.attrs["produtos_nao_encontrados"] = nao_encontrados
    return resultado
