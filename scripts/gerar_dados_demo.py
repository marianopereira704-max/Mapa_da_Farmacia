"""
Gera o conjunto de dados FICTÍCIOS em dados_demo/, usado para demonstrar
o app publicado (Streamlit Community Cloud) para o TI antes de termos as
credenciais reais do DigitalOcean Spaces.

USO:
    python scripts/gerar_dados_demo.py

Reprodutível: pode ser rodado de novo a qualquer momento para regenerar
os arquivos do zero (sobrescreve o que já existir em dados_demo/) — não
é um conjunto de arquivos criados manualmente uma única vez.

IMPORTANTE — nada aqui é dado real da Rede Melhor Compra:
  - Loja "0000", consultor "Consultor Demonstração".
  - Produtos "Produto Demonstração A", "B", "C"...
  - EANs sequenciais óbvios (0000000000001, 0000000000002, ...).
Ver dados_demo/LEIA-ME.md para o motivo da pasta existir e quando removê-la.

Como garante consistência com a lógica real do app: em vez de calcular a
sugestão automática "na mão" (duplicando a regra de negócio e arriscando
divergir dela), este script ESCREVE as planilhas brutas, RELÊ elas de
volta com os mesmos módulos que o app usa em produção
(modules.data_loader.carregar_mapa_farmacia/carregar_estoque/
carregar_base_nacional) e chama montar_tabela_ajuste_mix() de verdade
para montar o ajuste_mix.json pré-salvo — se a regra de negócio mudar no
futuro, regenerar os dados de demo automaticamente reflete a mudança.
"""

from __future__ import annotations

import json
import string
import sys
from pathlib import Path

import pandas as pd

# Permite rodar o script diretamente de dentro de scripts/ sem instalar
# o projeto como pacote.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import modules.data_loader as dl

RAIZ_DEMO = Path(__file__).resolve().parent.parent / "dados_demo"
LOJA_DEMO = "0000"
CICLO_DEMO = "2026-07"
CICLO_COMPLETO = f"{LOJA_DEMO}/{CICLO_DEMO}"
CONSULTOR_DEMO = "Consultor Demonstração"
ENVIADO_EM_DEMO = "2026-07-15T10:00:00"  # fixo, pra regenerar sempre dar o mesmo resultado

N_PRODUTOS = 18

# Os primeiros N produtos (por posição) ficam com estoque = 0 -- são os
# ÚNICOS candidatos a sugestão automática (ver
# modules.data_loader.montar_tabela_ajuste_mix). São 8 candidatos para
# só 5 (config.QTD_PRODUTOS_TOP_RANKING) caberem no top-N por demanda —
# de propósito, pra demonstrar tanto quem RECEBE quanto quem NÃO recebe
# sugestão automática mesmo estando com estoque zerado.
POSICOES_ESTOQUE_ZERO = set(range(1, 9))  # {1, ..., 8}

assert len(POSICOES_ESTOQUE_ZERO) > config.QTD_PRODUTOS_TOP_RANKING, (
    "precisa de mais candidatos a estoque=0 do que o top-N, senão todos "
    "recebem sugestão automática e a demo não mostra a diferença"
)


def _ean_ficticio(posicao: int) -> str:
    """EAN fictício, obviamente falso (sequencial a partir de 1), mas com
    formato válido (13 dígitos, dentro de EAN_MIN/MAX_DIGITOS)."""
    return str(posicao).zfill(13)


def _nome_produto(posicao: int) -> str:
    letra = string.ascii_uppercase[posicao - 1]
    return f"Produto Demonstração {letra}"


def gerar_mapa_farmacia_bruto() -> pd.DataFrame:
    """DataFrame com os nomes de COLUNA REAIS esperados na planilha (ver
    config.COLUNAS_MAPA_FARMACIA) -- é isso que vai pro .xlsx."""
    cols = config.COLUNAS_MAPA_FARMACIA
    linhas = [
        {
            cols["posicao"]: posicao,
            cols["ean"]: _ean_ficticio(posicao),
            cols["produto"]: _nome_produto(posicao),
            cols["frentes"]: 1 if posicao % 2 else 2,
        }
        for posicao in range(1, N_PRODUTOS + 1)
    ]
    return pd.DataFrame(linhas)


def gerar_estoque_bruto() -> pd.DataFrame:
    """DataFrame com os nomes de coluna reais esperados (ver
    config.COLUNAS_ESTOQUE)."""
    cols = config.COLUNAS_ESTOQUE
    linhas = []
    indice_nao_zero = 0
    for posicao in range(1, N_PRODUTOS + 1):
        if posicao in POSICOES_ESTOQUE_ZERO:
            estoque = 0
        else:
            indice_nao_zero += 1
            estoque = indice_nao_zero * 5  # 5, 10, 15, ...
        linhas.append({
            cols["id_loja"]: LOJA_DEMO,
            cols["ean"]: _ean_ficticio(posicao),
            cols["produto"]: _nome_produto(posicao),
            cols["estoque"]: estoque,
        })
    return pd.DataFrame(linhas)


def gerar_base_nacional_tratada() -> pd.DataFrame:
    """Já no formato TRATADO (ean, demanda) que
    modules.data_loader.carregar_base_nacional() espera de um .parquet --
    ver scripts/preparar_base_nacional.py, que produz esse mesmo formato
    a partir da planilha bruta em produção.

    Demanda decrescente conforme a posição: dentro do grupo de estoque=0
    (posições 1-8), isso faz as posições 1-5 (as 5 de maior demanda desse
    grupo) caírem no top-N e ganharem sugestão automática, e 6-8 ficarem
    de fora -- demonstra a regra de forma clara e determinística.
    """
    linhas = [
        {"ean": _ean_ficticio(posicao), "demanda": (N_PRODUTOS - posicao + 1) * 1000}
        for posicao in range(1, N_PRODUTOS + 1)
    ]
    return pd.DataFrame(linhas)


def montar_ajuste_mix_salvo(mapa_df: pd.DataFrame, estoque_df: pd.DataFrame, base_df: pd.DataFrame) -> dict:
    """Réplica exata da lógica de montagem do payload salvo por app.py ao
    clicar 'Salvar' na Ajuste de mix (mesma condição `if qtd and qtd > 0`,
    mesma escolha de chave ean/ean_original) -- construída a partir da
    tabela real (montar_tabela_ajuste_mix), não recalculada à mão."""
    tabela_base = dl.montar_tabela_ajuste_mix(mapa_df, estoque_df, base_df)

    quantidades = {
        row["ean_original"] if not row["ean_valido"] else row["ean"]: row["quantidade"]
        for _, row in tabela_base.iterrows()
    }

    n_auto = int((tabela_base["origem"] == "auto").sum())
    print(f"  produtos com sugestão automática: {n_auto}")
    print(f"  produtos no total: {len(tabela_base)}")

    return {
        "loja": LOJA_DEMO,
        "consultor": CONSULTOR_DEMO,
        "ciclo": CICLO_COMPLETO,
        "produtos": [
            {
                "ean": chave if str(chave).isdigit() and len(str(chave)) >= config.EAN_MIN_DIGITOS else None,
                "ean_original": chave,
                "quantidade": int(qtd),
            }
            for chave, qtd in quantidades.items()
            if qtd and qtd > 0
        ],
    }


def gerar() -> None:
    pasta_ciclo = RAIZ_DEMO / LOJA_DEMO / CICLO_DEMO
    pasta_base = RAIZ_DEMO / config.BASE_NACIONAL_FOLDER_NAME
    pasta_ciclo.mkdir(parents=True, exist_ok=True)
    pasta_base.mkdir(parents=True, exist_ok=True)

    print(f"Gerando dados fictícios em: {RAIZ_DEMO}")

    # ---- Mapa da Farmácia + Estoque (planilhas brutas, como um
    # consultor de verdade enviaria pela aba Upload) ----
    caminho_mapa = pasta_ciclo / "mapa_farmacia.xlsx"
    caminho_estoque = pasta_ciclo / "estoque.xlsx"
    gerar_mapa_farmacia_bruto().to_excel(caminho_mapa, index=False)
    gerar_estoque_bruto().to_excel(caminho_estoque, index=False)
    print(f"  {caminho_mapa.relative_to(RAIZ_DEMO.parent)} ({N_PRODUTOS} produtos)")
    print(f"  {caminho_estoque.relative_to(RAIZ_DEMO.parent)} ({N_PRODUTOS} produtos)")

    # ---- Base nacional (já no formato tratado/.parquet) ----
    caminho_base = pasta_base / "base_mercado.parquet"
    gerar_base_nacional_tratada().to_parquet(caminho_base, index=False)
    print(f"  {caminho_base.relative_to(RAIZ_DEMO.parent)} ({N_PRODUTOS} produtos)")

    # ---- metadata.json ----
    caminho_metadata = pasta_ciclo / "metadata.json"
    metadata = {"consultor": CONSULTOR_DEMO, "enviado_em": ENVIADO_EM_DEMO}
    caminho_metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {caminho_metadata.relative_to(RAIZ_DEMO.parent)}")

    # ---- ajuste_mix.json: relê as planilhas recém-escritas com os
    # módulos reais do app, pra garantir que o payload salvo é
    # exatamente o que a Ajuste de mix calcularia ao abrir essa loja. ----
    print("Recalculando ajuste de mix com a lógica real do app (montar_tabela_ajuste_mix)...")
    mapa_df = dl.carregar_mapa_farmacia(str(caminho_mapa))
    estoque_df = dl.carregar_estoque(str(caminho_estoque))
    base_df = dl.carregar_base_nacional(str(caminho_base))

    ajuste_salvo = montar_ajuste_mix_salvo(mapa_df, estoque_df, base_df)
    caminho_ajuste = pasta_ciclo / "ajuste_mix.json"
    caminho_ajuste.write_text(json.dumps(ajuste_salvo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {caminho_ajuste.relative_to(RAIZ_DEMO.parent)} ({len(ajuste_salvo['produtos'])} produtos salvos)")

    print("\nOK. Estrutura gerada:")
    print(f"  dados_demo/{LOJA_DEMO}/{CICLO_DEMO}/  (mapa_farmacia.xlsx, estoque.xlsx, metadata.json, ajuste_mix.json)")
    print(f"  dados_demo/{config.BASE_NACIONAL_FOLDER_NAME}/  (base_mercado.parquet)")


if __name__ == "__main__":
    gerar()
