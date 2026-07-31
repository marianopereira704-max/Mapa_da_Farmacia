"""
Script de pré-processamento da base nacional de demanda.

USO:
    python scripts/preparar_base_nacional.py caminho/para/PCP_YTD_MERCADO.xlsx

O que este script faz:
  1. Lê o arquivo bruto exportado do sistema de origem (.xlsx).
  2. Remove categorias fora do escopo de salão de loja (RX_* — tarja/
     balcão), configurável em config.PREFIXOS_CATEGORIA_EXCLUIDOS.
  3. Remove produtos sem movimentação relevante, configurável em
     config.DEMANDA_MINIMA_BASE_NACIONAL.
  4. Salva o resultado como .parquet — formato muito mais rápido de ler
     que .xlsx (testado: ~170x mais rápido em ~80 mil linhas).

Depois de rodar, suba o arquivo gerado (base_mercado.parquet) para a
pasta "_Base" no OneDrive, substituindo a versão anterior. O app sempre
prioriza o .parquet quando disponível.

Nenhuma regra de filtro está fixa no código deste script — todos os
parâmetros (prefixos de categoria excluídos, demanda mínima) vêm de
config.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Permite rodar o script diretamente de dentro de scripts/ sem instalar
# o projeto como pacote.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import modules.data_loader as dl


def preparar_base(caminho_entrada: str, caminho_saida: str | None = None) -> str:
    print(f"Lendo base bruta: {caminho_entrada}")
    bruta = dl.carregar_base_nacional_bruta(caminho_entrada)
    total_original = len(bruta)
    print(f"  {total_original:,} produtos na base bruta".replace(",", "."))

    tratada = bruta.copy()

    # --- Filtro 1: categorias fora do escopo (RX_*) ------------------------
    if tratada["categoria"].notna().any() and config.PREFIXOS_CATEGORIA_EXCLUIDOS:
        mascara_excluida = tratada["categoria"].fillna("").str.startswith(
            tuple(config.PREFIXOS_CATEGORIA_EXCLUIDOS)
        )
        removidos_categoria = int(mascara_excluida.sum())
        tratada = tratada[~mascara_excluida].copy()
        print(
            f"  removidos por categoria excluída "
            f"({', '.join(config.PREFIXOS_CATEGORIA_EXCLUIDOS)}): {removidos_categoria:,}"
            .replace(",", ".")
        )

    # --- Filtro 2: demanda mínima -------------------------------------------
    mascara_baixa_demanda = tratada["demanda"] < config.DEMANDA_MINIMA_BASE_NACIONAL
    removidos_demanda = int(mascara_baixa_demanda.sum())
    tratada = tratada[~mascara_baixa_demanda].copy()
    print(
        f"  removidos por demanda abaixo de {config.DEMANDA_MINIMA_BASE_NACIONAL}: "
        f"{removidos_demanda:,}".replace(",", ".")
    )

    # A versão tratada só precisa de ean + demanda para o cruzamento em
    # produção (é o que carregar_base_nacional() espera).
    tratada = tratada[["ean", "demanda"]].reset_index(drop=True)

    total_final = len(tratada)
    reducao_pct = (1 - total_final / total_original) * 100 if total_original else 0
    print(
        f"  total final: {total_final:,} produtos "
        f"(redução de {reducao_pct:.1f}%)".replace(",", ".")
    )

    if caminho_saida is None:
        caminho_saida = str(Path(caminho_entrada).parent / "base_mercado.parquet")

    tratada.to_parquet(caminho_saida, index=False)
    print(f"Base tratada salva em: {caminho_saida}")
    print("Suba este arquivo para a pasta _Base no OneDrive.")

    return caminho_saida


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Trata a base nacional de demanda (remove RX e baixa movimentação) e salva como parquet."
    )
    parser.add_argument("entrada", help="Caminho do arquivo .xlsx bruto exportado")
    parser.add_argument(
        "-o", "--saida", default=None,
        help="Caminho do arquivo .parquet de saída (padrão: base_mercado.parquet na mesma pasta da entrada)"
    )
    args = parser.parse_args()
    preparar_base(args.entrada, args.saida)
