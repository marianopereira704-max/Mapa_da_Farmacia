"""
Relatório de conferência "pela última atualização": o mesmo do
relatorio_mensal.py, mas que também conta o que a loja lançou na pasta do
mês ANTERIOR depois que o mês pedido já tinha começado (ex.: Foto Depois
enviada em outubro para a pasta de setembro, querendo valer para outubro).
Entre duas versões do mesmo arquivo, vale a mais recente.

O que veio da pasta anterior fica em âmbar e listado na última coluna, para
a conferência saber de onde saiu cada check. "Último envio" passa a ser a
data do arquivo mais recente considerado.

Não substitui o relatório oficial e não fica guardado no bucket.

Uso:
    python scripts/relatorio_ultima_atualizacao.py --mes 2026-10
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # permite rodar de qualquer lugar

import config
import modules.lojas_api as lojas_api
from modules.inventario import descobrir_inventario
from modules.storage import get_storage_client
from modules.storage.base import ArquivoNaoEncontradoError
from scripts import relatorio_mensal as rm

CHAVES = rm.CHAVES_CHECKLIST_TEXTO + rm.CHAVES_CHECKLIST_FOTO
PREENCHIMENTO_ANTERIOR = PatternFill(fill_type="solid", fgColor=config.COR_AMBAR_BG.lstrip("#"))
FONTE_ANTERIOR = Font(color=config.COR_AMBAR_TEXTO.lstrip("#"), bold=True)


def _mes_anterior(mes: str) -> str:
    ano, m = map(int, mes.split("-"))
    return f"{ano - 1}-12" if m == 1 else f"{ano}-{m - 1:02d}"


def _arquivos_com_data(storage, caminho_ciclo: str) -> dict[str, datetime | None]:
    """{chave: data do arquivo mais recente}. Só o nome padrão, que é como o
    Upload sempre grava — prévias como modelo_tela.png não contam."""
    try:
        itens = storage.listar_pasta(caminho_ciclo)
    except ArquivoNaoEncontradoError:
        return {}
    datas: dict[str, datetime | None] = {}
    for item in itens:
        base, _, extensao = item.nome.rpartition(".")
        chave = base.lower()
        if chave not in CHAVES or f".{extensao.lower()}" not in config.FILE_SPECS[chave]["extensions"]:
            continue
        if chave not in datas or (item.modificado_em and (datas[chave] is None or item.modificado_em > datas[chave])):
            datas[chave] = item.modificado_em
    return datas


def _buscar_foto_insistindo(storage, caminho_ciclo: str, chave: str):
    """Mesma regra do relatório oficial: falha de conexão tenta de novo, sem limite."""
    espera = rm.ESPERA_ENTRE_TENTATIVAS_S
    while True:
        imagem, aviso = rm._buscar_foto(storage, caminho_ciclo, chave)
        if aviso != rm._FALHA_DE_CONEXAO:
            return imagem, aviso
        time.sleep(espera)
        espera = min(espera * 2, rm.ESPERA_MAXIMA_ENTRE_TENTATIVAS_S)


def montar_linhas(storage, lojas_api_map: dict[str, lojas_api.Loja], mes: str, inventario: dict) -> list[dict]:
    anterior = _mes_anterior(mes)
    ano, m = map(int, mes.split("-"))
    inicio_mes = datetime(ano, m, 1, tzinfo=rm.FUSO_BRASILIA)
    codigos = sorted(set(rm._codigos_no_ciclo(inventario, mes)) | set(rm._codigos_no_ciclo(inventario, anterior)))

    def _montar(codigo: str) -> dict | None:
        ciclos = inventario[codigo]
        atual = _arquivos_com_data(storage, f"{codigo}/{mes}") if mes in ciclos else {}
        do_anterior = _arquivos_com_data(storage, f"{codigo}/{anterior}") if anterior in ciclos else {}

        escolha = {chave: (mes, data) for chave, data in atual.items()}
        for chave, data in do_anterior.items():
            if data is None or data < inicio_mes:
                continue  # lançado no próprio mês anterior: é dele mesmo
            if chave not in escolha or (escolha[chave][1] is not None and data > escolha[chave][1]):
                escolha[chave] = (anterior, data)
        if not escolha:
            return None

        datas = [data for _, data in escolha.values() if data is not None]
        metadata = (ciclos.get(mes) or {}).get("metadata") or {}
        linha = {
            **rm._dados_cadastrais(codigo, mes, lojas_api_map),
            "enviado_em": max(datas).isoformat() if datas else metadata.get("enviado_em"),
            "arquivos_presentes": set(escolha),
            "origem": {chave: ciclo for chave, (ciclo, _) in escolha.items()},
            "avisos_foto": {},
        }
        for chave in rm.CHAVES_CHECKLIST_TEXTO:
            linha[chave] = chave in escolha
        for chave in rm.CHAVES_CHECKLIST_FOTO:
            linha[chave] = None
            if chave in escolha:
                linha[chave], aviso = _buscar_foto_insistindo(storage, f"{codigo}/{escolha[chave][0]}", chave)
                if aviso:
                    linha["avisos_foto"][chave] = aviso
        return linha

    with ThreadPoolExecutor(max_workers=min(rm.DOWNLOADS_EM_PARALELO, len(codigos)) or 1) as executor:
        linhas = [linha for linha in executor.map(_montar, codigos) if linha is not None]
    linhas.sort(key=rm._chave_ordenacao)
    return linhas


def _marcar_origem(wb, linhas: list[dict], anterior: str) -> None:
    """Acrescenta a coluna do que veio da pasta anterior e pinta esses
    checks de âmbar, em todas as abas."""
    por_codigo = {linha["codigo"]: linha for linha in linhas}
    for ws in wb:
        cabecalho = [celula.value for celula in ws[1]]
        coluna = len(cabecalho) + 1
        titulo = ws.cell(row=1, column=coluna, value=f"Lançado na pasta {anterior}")
        titulo.font, titulo.fill, titulo.alignment = rm.FONTE_CABECALHO, rm.PREENCHIMENTO_CABECALHO, rm.ALINHAMENTO_CENTRO
        for i in range(2, ws.max_row + 1):
            origem = por_codigo[str(ws.cell(row=i, column=1).value)]["origem"]
            vindos = [chave for chave in CHAVES if origem.get(chave) == anterior]
            ws.cell(row=i, column=coluna, value=", ".join(rm.ROTULOS_COLUNA[c] for c in vindos) or "—")
            for chave in vindos:
                celula = ws.cell(row=i, column=cabecalho.index(rm.ROTULOS_COLUNA[chave]) + 1)
                celula.fill = PREENCHIMENTO_ANTERIOR
                if celula.value == "✅":
                    celula.font = FONTE_ANTERIOR
        ws.column_dimensions[get_column_letter(coluna)].width = 40
        ws.auto_filter.ref = f"A1:{get_column_letter(coluna)}{ws.max_row}"


def main():
    parser = argparse.ArgumentParser(description="Relatório de conferência pela última atualização.")
    parser.add_argument("--mes", required=True, help="Ciclo no formato AAAA-MM, ex.: 2026-10")
    parser.add_argument(
        "--saida", default=None,
        help="Caminho do .xlsx (padrão: relatorios/relatorio_conferencia_<mes>_ultima_atualizacao.xlsx)",
    )
    args = parser.parse_args()
    anterior = _mes_anterior(args.mes)

    secrets = rm._carregar_secrets()
    storage = get_storage_client(secrets)
    lojas_api_map = rm._carregar_lojas_api(secrets)

    print(f"Montando o ciclo {args.mes}, com o que foi lançado na pasta {anterior} a partir de {args.mes}-01...")
    linhas = montar_linhas(storage, lojas_api_map, args.mes, descobrir_inventario(storage))
    if not linhas:
        print("Nenhuma loja encontrada.")
        return

    com_anterior = sum(any(c == anterior for c in linha["origem"].values()) for linha in linhas)
    so_anterior = sum(all(c == anterior for c in linha["origem"].values()) for linha in linhas)
    print(
        f"{len(linhas)} loja(s), {sum(linha['fora_da_rede'] for linha in linhas)} fora da rede. "
        f"{com_anterior} com arquivo da pasta {anterior} ({so_anterior} só com arquivos de lá)."
    )
    nao_carregadas = rm.contar_fotos_nao_carregadas(linhas)
    if nao_carregadas:
        print(f"AVISO: {nao_carregadas} foto(s) não carregada(s), marcadas na planilha.")

    wb = rm._montar_planilha(linhas, args.mes)
    _marcar_origem(wb, linhas, anterior)
    caminho_saida = (
        Path(args.saida) if args.saida
        else Path(__file__).resolve().parent.parent / "relatorios" / f"relatorio_conferencia_{args.mes}_ultima_atualizacao.xlsx"
    )
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    wb.save(caminho_saida)
    print(f"Relatório gerado em: {caminho_saida}")


if __name__ == "__main__":
    main()
