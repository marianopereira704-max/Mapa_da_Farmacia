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
    print(f"  {len(bruta):,} produtos na base bruta".replace(",", "."))

    # A lógica dos dois filtros (categoria RX_* e demanda mínima) vive em
    # modules.data_loader.tratar_base_nacional() — mesma função usada pelo
    # upload automático da base nacional em app.py, pra não ter a regra
    # duplicada em dois lugares.
    tratada, resumo = dl.tratar_base_nacional(bruta)

    if resumo.removidos_categoria:
        print(
            f"  removidos por categoria excluída "
            f"({', '.join(config.PREFIXOS_CATEGORIA_EXCLUIDOS)}): {resumo.removidos_categoria:,}"
            .replace(",", ".")
        )
    print(
        f"  removidos por demanda abaixo de {config.DEMANDA_MINIMA_BASE_NACIONAL}: "
        f"{resumo.removidos_demanda:,}".replace(",", ".")
    )

    reducao_pct = (
        (1 - resumo.total_final / resumo.total_original) * 100 if resumo.total_original else 0
    )
    print(
        f"  total final: {resumo.total_final:,} produtos "
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
