# _diag_substituicao_foto_v2.py — diagnóstico temporário, não apagar ainda.
#
# Reproduz o fluxo CORRIGIDO de app.py::pagina_upload (conflito via
# localizar_item, substituição via escrever + apagar_arquivo do antigo
# quando a extensão muda) contra o bucket real, com fotos fictícias.
import io
from PIL import Image

import config
from modules.file_resolver import localizar_arquivo, localizar_item
from modules.storage import get_storage_client
from modules.storage.base import StorageError

import tomllib
with open(".streamlit/secrets.toml", "rb") as f:
    secrets = tomllib.load(f)
storage = get_storage_client(secrets)

LOJA_TESTE = "_teste_substituicao_foto_v2"
CICLO_TESTE = f"{LOJA_TESTE}/2026-09"


def _gerar_foto(cor):
    return Image.new("RGB", (400, 300), color=cor)


def _bytes_jpg(img):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _bytes_png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _upload_corrigido(chave, nome_arquivo_enviado, conteudo, ciclo):
    """Reproduz a lógica NOVA de app.py::pagina_upload: monta caminho com a
    extensão enviada agora, detecta conflito via localizar_item (qualquer
    extensão aceita), e se houver conflito simula o clique em
    'Sim, substituir' (escreve o novo, apaga o antigo se o caminho mudou)."""
    from pathlib import Path
    nome_base = config.FILE_SPECS[chave]["basenames"][0]
    extensao = Path(nome_arquivo_enviado).suffix.lower()
    caminho_destino = f"{ciclo}/{nome_base}{extensao}"

    item_existente = localizar_item(storage, ciclo, config.FILE_SPECS[chave])
    caminho_antigo = item_existente.caminho_completo if item_existente else None

    storage.escrever_arquivo_bytes(caminho_destino, conteudo)
    apagou_antigo = False
    if caminho_antigo and caminho_antigo != caminho_destino:
        storage.apagar_arquivo(caminho_antigo)
        apagou_antigo = True

    return caminho_destino, caminho_antigo, apagou_antigo


print("=" * 90)
print("CENARIO 1: .jpg -> .png (extensao muda, era o bug relatado)")
print("=" * 90)

conteudo_antigo = _bytes_jpg(_gerar_foto((200, 50, 50)))   # vermelho
conteudo_novo = _bytes_png(_gerar_foto((50, 200, 50)))     # verde

c1, antigo1, apagou1 = _upload_corrigido("foto_antes", "foto_antes.jpg", conteudo_antigo, CICLO_TESTE)
print(f"[1] 1o envio: caminho={c1!r} conflito_antigo={antigo1!r} apagou={apagou1}")

c2, antigo2, apagou2 = _upload_corrigido("foto_antes", "foto_antes.png", conteudo_novo, CICLO_TESTE)
print(f"[2] 2o envio (substituicao): caminho={c2!r} conflito_antigo={antigo2!r} apagou={apagou2}")

itens = storage.listar_pasta(CICLO_TESTE)
caminhos_restantes = sorted(i.caminho_completo for i in itens)
print(f"\nArquivos restantes no storage: {caminhos_restantes}")
assert caminhos_restantes == [c2], f"ESPERAVA só [{c2!r}], achou {caminhos_restantes!r}"
print("OK: só o arquivo novo restou (o .jpg antigo foi apagado).")

arq = localizar_arquivo(storage, CICLO_TESTE, config.FILE_SPECS["foto_antes"], "foto_antes")
assert arq.conteudo == conteudo_novo, "ESPERAVA o conteudo NOVO (verde)"
print("OK: a tela de Conferencia mostraria a foto NOVA (verde).")

print("\n" + "=" * 90)
print("CENARIO 2 (controle): .jpg -> .jpg (mesma extensao, nao deve tentar apagar nada)")
print("=" * 90)
LOJA_TESTE2 = "_teste_substituicao_foto_v2_mesma_ext"
CICLO_TESTE2 = f"{LOJA_TESTE2}/2026-09"

c1b, antigo1b, apagou1b = _upload_corrigido("foto_antes", "foto_antes.jpg", conteudo_antigo, CICLO_TESTE2)
print(f"[1] 1o envio: caminho={c1b!r} conflito_antigo={antigo1b!r} apagou={apagou1b}")
c2b, antigo2b, apagou2b = _upload_corrigido("foto_antes", "foto_antes.jpg", conteudo_novo, CICLO_TESTE2)
print(f"[2] 2o envio (substituicao): caminho={c2b!r} conflito_antigo={antigo2b!r} apagou={apagou2b}")
assert antigo2b == c1b, "esperava detectar o antigo com o MESMO caminho"
assert apagou2b is False, "nao deveria ter tentado apagar (caminho igual)"

itens2 = storage.listar_pasta(CICLO_TESTE2)
caminhos_restantes2 = sorted(i.caminho_completo for i in itens2)
assert caminhos_restantes2 == [c2b], f"ESPERAVA só [{c2b!r}], achou {caminhos_restantes2!r}"
arq2 = localizar_arquivo(storage, CICLO_TESTE2, config.FILE_SPECS["foto_antes"], "foto_antes")
assert arq2.conteudo == conteudo_novo
print("OK: controle também passou (mesma extensão, sem chamada extra de apagar).")

print("\n" + "=" * 90)
print("CENARIO 3: apagar_arquivo é idempotente (apagar um caminho que já não existe)")
print("=" * 90)
try:
    storage.apagar_arquivo(f"{CICLO_TESTE}/isso_nunca_existiu.jpg")
    print("OK: apagar_arquivo de um caminho inexistente não levantou erro.")
except StorageError as e:
    raise AssertionError(f"apagar_arquivo deveria ser idempotente, mas levantou: {e}")

print("\n--- Limpando arquivos de teste do bucket ---")
for caminho in [c2, c1b, c2b]:
    try:
        storage.apagar_arquivo(caminho)
        print(f"  removido: {caminho}")
    except StorageError as e:
        print(f"  (nao foi possivel remover {caminho}: {e})")
print("Limpeza concluída. TODOS OS CENÁRIOS PASSARAM.")
