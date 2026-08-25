"""
Cliente da API de lojas do TI (Rede Melhor Compra).

Fonte oficial da lista de lojas ativas — código, nome, consultor e
supervisora responsáveis por cada uma. Usado:

  1. na página Upload, pra substituir o campo de texto livre "Loja
     (código)" por uma lista oficial (elimina a pendência conhecida de
     erro de digitação criando uma "loja" nova por engano — ver
     PENDÊNCIA em app.py/README) e pré-preencher o campo "Consultor" a
     partir do valor devolvido pela API para a loja escolhida (editável
     manualmente depois, caso quem esteja enviando não seja o consultor
     oficial daquela loja);
  2. na página Selecionar Loja, pra filtrar as lojas por Consultor e
     Supervisora com o dado oficial (em vez do metadata.json de cada
     envio, que registra quem efetivamente fez o upload — pode ser
     outra pessoa, ex.: cobertura de férias).

Autenticação: chave fixa enviada no header "X-API-Key" (configurada em
st.secrets["api_lojas"], seção separada do storage — ver
.streamlit/secrets.toml.example).

Assim como o módulo de storage, este cliente nunca deixa uma exceção
"crua" (requests.RequestException, KeyError, json inválido, etc.)
escapar para quem chamou — tudo vira LojasAPIError, com mensagem clara,
para o app.py poder mostrar um aviso e cair de volta em texto livre em
vez de quebrar a página Upload.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests


class LojasAPIError(Exception):
    """Erro genérico de acesso à API de lojas (configuração ausente, rede,
    autenticação, formato de resposta inesperado, etc.)."""


@dataclass
class Loja:
    """Uma loja devolvida pela API do TI. `nome`, `consultor` e
    `supervisora` podem vir None se a API não os informar para aquela
    loja em particular — só `codigo` é garantido (itens sem código
    utilizável são descartados em obter_lojas(), nunca viram um objeto
    Loja "quebrado")."""
    codigo: str
    nome: str | None
    consultor: str | None
    supervisora: str | None


# Nomes de campo confirmados contra uma resposta real da API (endpoint
# /api/full-stores). `legacyId` é o código "de sistema" (numérico, é o
# que aparece na estrutura de pastas do bucket, ex.: loja "1124").
# `fantasyName` é o nome comercial da loja (ex.: "FARMÁCIA DOM BOSCO") —
# `businessName` fica como fallback só se faltar fantasyName, mas é a
# razão social/titular do CNPJ, não o nome da loja (ex.: "JOÃO BOSCO DE
# ANDRADE NOGUEIRA CHAGAS"), então não deve ser a primeira opção.
_CAMPOS_CODIGO = ["legacyId"]
_CAMPOS_NOME = ["fantasyName", "businessName"]

# "Consultor" e "supervisora" não são campos soltos no item — são
# integrantes da lista `team` cujo `sector` é exatamente um destes
# valores (confirmados contra os setores reais devolvidos pela API — ver
# _pessoa_do_setor()).
_SETOR_CONSULTOR = "Consultoria Interna"
_SETOR_SUPERVISORA = "Supervisão"

# Só lojas com este status_title entram na lista (decisão confirmada com
# o usuário — ver README). Comparação exata, sem normalizar maiúsculas/
# acentos: ajustar aqui se a API vier a usar outra grafia.
_STATUS_ATIVO = "Ativo"


def obter_lojas(url: str, api_key: str, timeout: int = 15) -> list[Loja]:
    """Busca a lista de lojas ATIVAS na API do TI (item com
    `status_title` diferente de "Ativo" é descartado — ver
    _STATUS_ATIVO).

    Levanta LojasAPIError se `url`/`api_key` não estiverem configurados,
    se a requisição falhar (rede, timeout), se a API responder com
    status != 200 (ex.: 401 por chave inválida), ou se o corpo da
    resposta não puder ser interpretado como a lista esperada.
    """
    if not url or not api_key:
        raise LojasAPIError(
            "API de lojas não configurada — defina 'url' e 'api_key' em "
            "[api_lojas] no secrets.toml."
        )

    try:
        resposta = requests.get(url, headers={"X-API-Key": api_key}, timeout=timeout)
    except requests.RequestException as e:
        raise LojasAPIError(f"Falha ao conectar à API de lojas: {e}") from e

    if resposta.status_code != 200:
        raise LojasAPIError(
            f"API de lojas retornou status {resposta.status_code}: {resposta.text[:200]}"
        )

    try:
        corpo = resposta.json()
    except ValueError as e:
        raise LojasAPIError(f"Resposta da API de lojas não é um JSON válido: {e}") from e

    itens = _extrair_lista(corpo)

    lojas: list[Loja] = []
    for item in itens:
        if not isinstance(item, dict):
            continue
        if item.get("status_title") != _STATUS_ATIVO:
            continue  # loja inativa (ou status desconhecido) — não entra na lista
        codigo = _primeiro_valor(item, _CAMPOS_CODIGO)
        if not codigo:
            continue  # item sem código utilizável — ignora silenciosamente
        lojas.append(Loja(
            codigo=str(codigo).strip(),
            nome=_primeiro_valor(item, _CAMPOS_NOME),
            consultor=_pessoa_do_setor(item, _SETOR_CONSULTOR),
            supervisora=_pessoa_do_setor(item, _SETOR_SUPERVISORA),
        ))
    return lojas


def _extrair_lista(corpo) -> list:
    """A API pode devolver a lista direto (`[...]`) ou embrulhada num
    envelope tipo `{"success": true, "data": [...]}` (formato observado
    na resposta de erro da própria API: `{"success": false, "message":
    "..."}`, então é razoável esperar `{"success": true, "data": [...]}`
    no caminho feliz) — tenta as chaves de envelope mais comuns antes de
    desistir."""
    if isinstance(corpo, list):
        return corpo
    if isinstance(corpo, dict):
        for chave in ("data", "lojas", "stores", "results", "items"):
            valor = corpo.get(chave)
            if isinstance(valor, list):
                return valor
    raise LojasAPIError(
        f"Formato de resposta inesperado da API de lojas (esperava uma lista "
        f"ou um envelope {{'data': [...]}}, recebi: {type(corpo).__name__})."
    )


def _primeiro_valor(item: dict, chaves: list[str]) -> str | None:
    for chave in chaves:
        valor = item.get(chave)
        if valor not in (None, ""):
            return valor
    return None


def _pessoa_do_setor(item: dict, setor: str) -> str | None:
    """Procura, na lista `team` do item, o primeiro integrante cujo
    `sector` é exatamente `setor` (ex.: "Consultoria Interna" ou
    "Supervisão") e devolve o nome dele. Retorna None se a loja não
    tiver ninguém desse setor na equipe (`team` ausente, vazio, ou sem
    ninguém com esse setor) — não é um erro, é um estado válido: o campo
    correspondente fica em branco (Consultor no Upload, editável
    manualmente) ou a loja simplesmente não aparece ao filtrar por esse
    setor na página Selecionar Loja."""
    time = item.get("team")
    if not isinstance(time, list):
        return None
    for integrante in time:
        if not isinstance(integrante, dict):
            continue
        if integrante.get("sector") == setor:
            nome = integrante.get("name")
            if nome:
                return nome
    return None
