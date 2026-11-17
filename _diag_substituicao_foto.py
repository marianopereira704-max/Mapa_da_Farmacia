# _diag_substituicao_foto.py — diagnóstico temporário, não apagar ainda.
#
# Testa de verdade, com fotos FICTÍCIAS, o fluxo de upload/substituição de
# "Foto Antes" — reproduzindo EXATAMENTE a lógica de app.py::pagina_upload
# (construção do caminho, checagem de conflito via storage.existe(), escrita)
# e app.py::_carregar_imagem_conferencia (leitura via localizar_arquivo, que
# testa as extensões na ordem de config.FILE_SPECS).
#
# Usa uma pasta de teste isolada no bucket real (_teste_substituicao_foto),
# apaga tudo no final via boto3 direto.
import io
from PIL import Image

import config
from modules.file_resolver import localizar_arquivo
from modules.storage import get_storage_client

import tomllib
with open(".streamlit/secrets.toml", "rb") as f:
    secrets = tomllib.load(f)
storage = get_storage_client(secrets)

LOJA_TESTE = "_teste_substituicao_foto"
CICLO_TESTE = f"{LOJA_TESTE}/2026-09"


def _gerar_foto(cor, texto):
    img = Image.new("RGB", (400, 300), color=cor)
    return img


def _bytes_jpg(img):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _bytes_png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _upload_como_o_app_faria(chave, nome_arquivo_enviado, conteudo):
    """Reproduz app.py::pagina_upload linha por linha: monta caminho_destino
    com a extensão do arquivo ENVIADO AGORA, checa conflito via
    storage.existe(), e só grava se NÃO houver conflito (pra provar que,
    quando a extensão muda, o app não detecta o conflito e grava direto)."""
    from pathlib import Path
    nome_base = config.FILE_SPECS[chave]["basenames"][0]
    extensao = Path(nome_arquivo_enviado).suffix.lower()
    caminho_destino = f"{CICLO_TESTE}/{nome_base}{extensao}"
    tem_conflito = storage.existe(caminho_destino)
    if not tem_conflito:
        storage.escrever_arquivo_bytes(caminho_destino, conteudo)
    return caminho_destino, tem_conflito


print("=" * 90)
print("CENÁRIO REAL RELATADO: 1º envio como .jpg, 2º envio (a 'substituição') como .png")
print("=" * 90)

foto_antiga = _gerar_foto((200, 50, 50), "ANTIGA")   # vermelho = foto antiga
foto_nova = _gerar_foto((50, 200, 50), "NOVA")       # verde = foto nova

conteudo_antigo = _bytes_jpg(foto_antiga)
conteudo_novo = _bytes_png(foto_nova)

print(f"\n[1] Consultor envia 'foto_antes.jpg' pela primeira vez ({len(conteudo_antigo)} bytes)")
caminho1, conflito1 = _upload_como_o_app_faria("foto_antes", "foto_antes.jpg", conteudo_antigo)
print(f"    caminho_destino={caminho1!r} | conflito_detectado={conflito1} | gravado=SIM")

print(f"\n[2] Consultor tira a foto de novo (app da câmera/celular salva como .png desta vez)")
print("    e faz 'a substituição' enviando 'foto_antes.png' pela tela de Upload")
caminho2, conflito2 = _upload_como_o_app_faria("foto_antes", "foto_antes.png", conteudo_novo)
print(f"    caminho_destino={caminho2!r} | conflito_detectado={conflito2} | gravado={'SIM (sem pedir confirmação!)' if not conflito2 else 'NAO'}")

print("\n--- O que existe DE VERDADE no storage agora? ---")
itens = storage.listar_pasta(CICLO_TESTE)
for item in itens:
    print(f"  {item.caminho_completo}")

print("\n--- O que a tela de Conferência mostraria? (mesma lógica de localizar_arquivo) ---")
arq = localizar_arquivo(storage, CICLO_TESTE, config.FILE_SPECS["foto_antes"], "foto_antes")
conteudo_exibido = arq.conteudo
if conteudo_exibido == conteudo_antigo:
    veredito = "A FOTO ANTIGA (vermelha) — o bug se confirma: a 'substituição' não substituiu nada na tela"
elif conteudo_exibido == conteudo_novo:
    veredito = "a foto nova (verde) — substituição funcionou"
else:
    veredito = "conteúdo desconhecido"
print(f"  arquivo escolhido: {arq.caminho_relativo} ({arq.extensao})")
print(f"  RESULTADO: {veredito}")

print("\n" + "=" * 90)
print("CONTROLE: mesmo cenário, mas com a MESMA extensão nas duas vezes (.jpg -> .jpg)")
print("=" * 90)
LOJA_TESTE2 = "_teste_substituicao_foto_mesma_ext"
CICLO_TESTE2 = f"{LOJA_TESTE2}/2026-09"

def _upload2(chave, nome_arquivo_enviado, conteudo):
    from pathlib import Path
    nome_base = config.FILE_SPECS[chave]["basenames"][0]
    extensao = Path(nome_arquivo_enviado).suffix.lower()
    caminho_destino = f"{CICLO_TESTE2}/{nome_base}{extensao}"
    tem_conflito = storage.existe(caminho_destino)
    if not tem_conflito:
        storage.escrever_arquivo_bytes(caminho_destino, conteudo)
    else:
        storage.escrever_arquivo_bytes(caminho_destino, conteudo)  # simula clicar "Sim, substituir"
    return caminho_destino, tem_conflito

c1, conf1 = _upload2("foto_antes", "foto_antes.jpg", conteudo_antigo)
print(f"[1] {c1} | conflito={conf1}")
c2, conf2 = _upload2("foto_antes", "foto_antes.jpg", conteudo_novo)
print(f"[2] {c2} | conflito={conf2} (esperado True -> pediria confirmação, 'Sim, substituir' sobrescreve)")

arq2 = localizar_arquivo(storage, CICLO_TESTE2, config.FILE_SPECS["foto_antes"], "foto_antes")
veredito2 = "a foto NOVA (verde) — funcionou, porque a extensão não mudou" if arq2.conteudo == conteudo_novo else "a foto antiga (falhou mesmo assim)"
print(f"RESULTADO (controle): {veredito2}")

print("\n--- Limpando arquivos de teste do bucket ---")
for caminho in [CICLO_TESTE + "/foto_antes.jpg", CICLO_TESTE + "/foto_antes.png", CICLO_TESTE2 + "/foto_antes.jpg"]:
    try:
        chave_s3 = storage._chave(caminho)
        storage._cliente.delete_object(Bucket=storage.bucket, Key=chave_s3)
        print(f"  removido: {caminho}")
    except Exception as e:
        print(f"  (nao foi possivel remover {caminho}: {e})")
print("Limpeza concluída.")
