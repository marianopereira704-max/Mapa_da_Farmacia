"""
migrar_previews_modelo.py — script de migração ÚNICA (não roda dentro do
app, roda uma vez manualmente).

Para cada loja/ciclo que já tem um arquivo "modelo" original (pdf/jpg/png)
mas ainda não tem modelo_tela.png / modelo_impressao.png, baixa o
original, gera os dois previews (modules.image_utils.gerar_previews_modelo)
e salva no mesmo ciclo. Reaproveita modules.inventario.descobrir_inventario
— a mesma descoberta de Loja -> Ciclo -> arquivos que o app usa (via
app.py::_descobrir_inventario_cache) — em vez de reimplementar a
enumeração.

Uso: python migrar_previews_modelo.py
"""
import io
import time
import tomllib

import config
import modules.data_loader as dl  # noqa: F401  (mantido por paridade com os outros diagnósticos)
import modules.image_utils as image_utils
import modules.inventario as inventario
from modules.file_resolver import localizar_arquivo
from modules.storage import get_storage_client

with open(".streamlit/secrets.toml", "rb") as f:
    secrets = tomllib.load(f)
storage = get_storage_client(secrets)

print("Descobrindo inventário (loja -> ciclo -> arquivos)...")
inv = inventario.descobrir_inventario(storage)

total_migrados = 0
total_ja_migrados = 0
total_sem_modelo = 0
total_falhas = 0

for loja in sorted(inv.keys()):
    for ciclo, info in sorted(inv[loja].items()):
        arquivos = info["arquivos"]
        caminho_ciclo = f"{loja}/{ciclo}"

        if "modelo" not in arquivos:
            total_sem_modelo += 1
            continue

        caminho_tela = f"{caminho_ciclo}/modelo_tela.png"
        caminho_impressao = f"{caminho_ciclo}/modelo_impressao.png"

        if storage.existe(caminho_tela) and storage.existe(caminho_impressao):
            print(f"[{loja}/{ciclo}] já migrado (modelo_tela.png e modelo_impressao.png já existem) — pulando")
            total_ja_migrados += 1
            continue

        try:
            t0 = time.perf_counter()
            arq_modelo = localizar_arquivo(storage, caminho_ciclo, config.FILE_SPECS["modelo"], "modelo")
            if arq_modelo is None:
                # inventário disse que tinha, mas não achou de fato —
                # inconsistência rara (ex.: nome fora do padrão tolerado);
                # não é um erro fatal do script, só pula esta loja/ciclo.
                print(f"[{loja}/{ciclo}] inventário indicou 'modelo' mas localizar_arquivo não encontrou — pulando")
                total_sem_modelo += 1
                continue

            tamanho_original_kb = len(arq_modelo.conteudo) / 1024
            t_download = time.perf_counter() - t0

            t1 = time.perf_counter()
            bytes_tela, bytes_impressao = image_utils.gerar_previews_modelo(arq_modelo.conteudo, arq_modelo.extensao)
            t_geracao = time.perf_counter() - t1

            storage.escrever_arquivo_bytes(caminho_tela, bytes_tela)
            storage.escrever_arquivo_bytes(caminho_impressao, bytes_impressao)

            tamanho_tela_kb = len(bytes_tela) / 1024
            tamanho_impressao_kb = len(bytes_impressao) / 1024

            print(
                f"[{loja}/{ciclo}] SUCESSO — original({arq_modelo.extensao})={tamanho_original_kb:.1f}KB "
                f"-> tela={tamanho_tela_kb:.1f}KB, impressao={tamanho_impressao_kb:.1f}KB "
                f"(download={t_download:.2f}s, geracao={t_geracao:.2f}s)"
            )
            total_migrados += 1
        except Exception as e:
            print(f"[{loja}/{ciclo}] FALHA — {type(e).__name__}: {e}")
            total_falhas += 1

print("\n" + "=" * 90)
print("RESUMO DA MIGRAÇÃO")
print("=" * 90)
print(f"Migrados agora:        {total_migrados}")
print(f"Já migrados antes:     {total_ja_migrados}")
print(f"Sem modelo original:   {total_sem_modelo}")
print(f"Falhas:                {total_falhas}")
