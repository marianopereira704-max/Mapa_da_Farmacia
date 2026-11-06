"""
Regras da seleção de loja por mês: barra de meses, lojas do mês, busca e
paginação. Sem dependência de Streamlit — testável isoladamente.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Brasil não tem horário de verão desde 2019 — offset fixo evita depender do
# pacote tzdata (que não vem no Windows).
FUSO_BRASILIA = timezone(timedelta(hours=-3))
LOJAS_POR_PAGINA = 20

_FORMATO_MES = re.compile(r"\d{4}-\d{2}")


def mes_atual(agora: datetime | None = None) -> str:
    """AAAA-MM em Brasília. O servidor roda em UTC: sem isso, o mês virava às
    21h do último dia."""
    return (agora or datetime.now(timezone.utc)).astimezone(FUSO_BRASILIA).strftime("%Y-%m")


def _e_loja(codigo: str) -> bool:
    """Códigos de teste/diagnóstico (ex.: "_teste_substituicao_foto") nunca são
    loja de verdade."""
    return not codigo.startswith("_")


def _ordem_codigo(codigo: str) -> tuple:
    return (0, int(codigo), codigo) if codigo.isdigit() else (1, 0, codigo)


@dataclass
class MesDaBarra:
    mes: str
    lojas: int
    atual: bool


def meses_da_barra(inventario: dict, mes_de_hoje: str) -> list[MesDaBarra]:
    """O mês atual, mesmo sem nenhum envio, mais os meses com arquivos, do mais
    novo para o mais antigo."""
    contagem = {mes_de_hoje: 0}
    for codigo, ciclos in inventario.items():
        if not _e_loja(codigo):
            continue
        for mes in ciclos:
            if _FORMATO_MES.fullmatch(mes):
                contagem[mes] = contagem.get(mes, 0) + 1
    return [MesDaBarra(mes, contagem[mes], mes == mes_de_hoje) for mes in sorted(contagem, reverse=True)]


def lojas_do_mes(inventario: dict, mes: str) -> list[str]:
    return sorted(
        (codigo for codigo, ciclos in inventario.items() if _e_loja(codigo) and mes in ciclos),
        key=_ordem_codigo,
    )


def mes_de_destino(loja: str, mes_contexto: str | None, loja_contexto: str | None, mes_de_hoje: str) -> str:
    """Mês em que o Upload grava. O mês escolhido na tela de origem só vale para
    a loja com que o Upload foi aberto; trocando de loja, volta ao mês atual."""
    if mes_contexto and loja.strip() == (loja_contexto or "").strip():
        return mes_contexto
    return mes_de_hoje


def texto_de_destino(loja: str, mes: str, mes_de_hoje: str, mes_legivel) -> str:
    """Linha no topo do Upload. Vazia sem loja: ainda não há destino."""
    loja = loja.strip()
    if not loja:
        return ""
    texto = f"Destino: **Loja {loja} · {mes_legivel(mes)}**"
    return f"{texto}, mês anterior ao atual" if mes < mes_de_hoje else texto


def arquivos_do_upload(chaves: list[str], mes_ja_existe: bool) -> list[str]:
    """Retrato e Estoque atualizado só fazem sentido em mês que já tem arquivos."""
    return [c for c in chaves if mes_ja_existe or c not in ("retrato_vendas", "estoque_atualizado")]


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).casefold()


def buscar_lojas(texto: str, codigos: list[str], cadastro: dict) -> list[str]:
    """Código pelo começo; razão social e nome fantasia em qualquer parte, sem
    diferenciar maiúscula nem acento. `cadastro`: {codigo: lojas_api.Loja}. Loja
    fora dele (ou API indisponível) só é achada pelo código."""
    termo = _sem_acento(texto.strip())
    if not termo:
        return list(codigos)

    def _bate(codigo: str) -> bool:
        if codigo.startswith(termo):
            return True
        loja = cadastro.get(codigo)
        if loja is None:
            return False
        return any(termo in _sem_acento(nome) for nome in (loja.razao_social, loja.nome_fantasia) if nome)

    return [codigo for codigo in codigos if _bate(codigo)]


_MESES_CURTOS = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez")


def mes_curto(mes: str) -> str:
    """"2026-11" vira "Nov/26". Texto fora do formato volta como veio."""
    if not _FORMATO_MES.fullmatch(mes) or not 1 <= int(mes[5:]) <= 12:
        return mes
    return f"{_MESES_CURTOS[int(mes[5:]) - 1]}/{mes[2:4]}"


def rotulo_lojas(total: int) -> str:
    return "1 loja" if total == 1 else f"{total} lojas"


def rotulo_mes_da_barra(item: MesDaBarra) -> str:
    atual = " (atual)" if item.atual else ""
    return f"{mes_curto(item.mes)}{atual} · {rotulo_lojas(item.lojas)}"


def mes_anterior_com_arquivos(barra: list[MesDaBarra], mes: str) -> str | None:
    """O mês com arquivos mais próximo antes de `mes`, para o atalho do mês vazio."""
    return next((item.mes for item in barra if item.mes < mes and item.lojas), None)


@dataclass
class LinhaLoja:
    codigo: str
    razao_social: str
    consultor: str
    tem_arquivos: bool


def lojas_da_lista(
    inventario: dict,
    mes: str,
    texto: str,
    cadastro: dict,
    consultor: str | None = None,
    supervisora: str | None = None,
) -> list[LinhaLoja]:
    """Sem busca: lojas com arquivos no mês. Com busca: também as ativas do
    cadastro sem arquivos, para poderem fazer o Upload. `cadastro` vazio (API
    fora do ar) deixa só código, e os filtros não acham nada."""
    com_arquivos = set(lojas_do_mes(inventario, mes))
    universo = set(com_arquivos)
    if texto.strip():
        universo |= {codigo for codigo, loja in cadastro.items() if loja.ativa and _e_loja(codigo)}
        universo = set(buscar_lojas(texto, list(universo), cadastro))

    linhas = []
    for codigo in sorted(universo, key=_ordem_codigo):
        loja = cadastro.get(codigo)
        if consultor and (loja is None or loja.consultor != consultor):
            continue
        if supervisora and (loja is None or loja.supervisora != supervisora):
            continue
        linhas.append(LinhaLoja(
            codigo=codigo,
            razao_social=(loja and (loja.razao_social or loja.nome_fantasia)) or "—",
            consultor=(loja and loja.consultor) or "—",
            tem_arquivos=codigo in com_arquivos,
        ))
    return linhas


def opcoes_de_filtro(inventario: dict, mes: str, cadastro: dict) -> tuple[list[str], list[str]]:
    """(consultores, supervisoras) das lojas com arquivos no mês."""
    lojas = [cadastro[c] for c in lojas_do_mes(inventario, mes) if c in cadastro]
    return (
        sorted({loja.consultor for loja in lojas if loja.consultor}),
        sorted({loja.supervisora for loja in lojas if loja.supervisora}),
    )


def meses_da_loja(inventario: dict, loja: str) -> list[str]:
    """Meses com arquivos da loja, do mais novo para o mais antigo."""
    return sorted((mes for mes in inventario.get(loja, {}) if _FORMATO_MES.fullmatch(mes)), reverse=True)


def rotulo_da_loja(codigo: str, cadastro: dict) -> str:
    """"1012 · João da Silva ME". Sem cadastro, só o código."""
    loja = cadastro.get(codigo)
    nome = loja and (loja.razao_social or loja.nome_fantasia)
    return f"{codigo} · {nome}" if nome else codigo


@dataclass
class ArquivoDoMes:
    rotulo: str
    presente: bool


def arquivos_do_mes(rotulos: dict[str, str], arquivos: dict) -> list[ArquivoDoMes]:
    """Situação de cada tipo de arquivo. `rotulos` define quais e em que ordem:
    o que mais houver na pasta, como metadata ou prévias, não entra na conta."""
    return [ArquivoDoMes(rotulo, chave in arquivos) for chave, rotulo in rotulos.items()]


@dataclass
class Pagina:
    itens: list
    pagina: int
    total_paginas: int
    inicio: int  # posição do primeiro item, começando em 1; 0 = lista vazia
    fim: int
    total: int

    @property
    def tem_anterior(self) -> bool:
        return self.pagina > 1

    @property
    def tem_proxima(self) -> bool:
        return self.pagina < self.total_paginas


def paginar(itens: list, pagina: int, por_pagina: int = LOJAS_POR_PAGINA) -> Pagina:
    """Página fora do intervalo cai na mais próxima: a lista pode encolher entre
    um clique e outro, quando muda a busca, o filtro ou o mês."""
    total = len(itens)
    total_paginas = max(1, math.ceil(total / por_pagina))
    pagina = min(max(pagina, 1), total_paginas)
    primeiro = (pagina - 1) * por_pagina
    trecho = itens[primeiro:primeiro + por_pagina]
    return Pagina(
        itens=trecho,
        pagina=pagina,
        total_paginas=total_paginas,
        inicio=primeiro + 1 if trecho else 0,
        fim=primeiro + len(trecho),
        total=total,
    )
