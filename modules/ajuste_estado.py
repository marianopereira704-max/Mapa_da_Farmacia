"""
Estado do Ajuste de mix na tela em relação ao último salvamento: quantas
alterações faltam salvar e o texto da faixa. Sem dependência de Streamlit.
"""

from __future__ import annotations

import math
from datetime import datetime

from modules.selecao_loja import FUSO_BRASILIA


def _numero(valor) -> int:
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return 0
    return int(valor)


def contar_alteracoes(atual: dict, salvo: dict) -> int:
    """Produtos com quantidade diferente da salva. Vazio e zero são iguais."""
    return sum(_numero(atual.get(chave)) != _numero(salvo.get(chave)) for chave in set(atual) | set(salvo))


def quando_salvou(salvo_em: str | None) -> str | None:
    """"06/11 às 14:32", em Brasília. None se não houver data ou ela for inválida."""
    if not salvo_em:
        return None
    try:
        momento = datetime.fromisoformat(salvo_em)
    except ValueError:
        return None
    if momento.tzinfo is None:
        return None
    return momento.astimezone(FUSO_BRASILIA).strftime("%d/%m às %H:%M")


def texto_da_faixa(alteracoes: int, tem_salvo: bool, salvo_em: str | None) -> str:
    if not tem_salvo:
        return "Ajuste ainda não salvo. A Sugestão de GC só aparece depois de salvar."
    quando = quando_salvou(salvo_em)
    # Sem horário gravado (salvamento antigo) não há o que acrescentar:
    # "Tudo salvo. Ajuste salvo." repetia a mesma informação.
    ultimo = f" Último salvamento em {quando}." if quando else ""
    if alteracoes == 0:
        return f"Tudo salvo.{ultimo}"
    plural = "alteração não salva" if alteracoes == 1 else "alterações não salvas"
    return f"{alteracoes} {plural}.{ultimo}"


def texto_aviso_gc(alteracoes: int) -> str:
    plural = "alteração" if alteracoes == 1 else "alterações"
    return (
        f"Este PDF usa o último ajuste salvo. Há {alteracoes} {plural} sem salvar "
        "no Ajuste de mix."
    )
