"""Autenticação simples por senha única compartilhada.

Não é um sistema multiusuário — é só uma camada extra de proteção caso o
link do app vaze. A senha correta vem de st.secrets["auth"]["senha"]; uma
vez validada nesta sessão do navegador (guardada em st.session_state), o
app libera o resto do fluxo sem pedir a senha de novo até a página ser
recarregada por completo.
"""

from __future__ import annotations

import streamlit as st

import modules.styles as styles

_CHAVE_SESSAO_AUTENTICADO = "autenticado"


def exigir_autenticacao() -> None:
    """Bloqueia o app até o usuário digitar a senha correta.

    Deve ser chamada logo no início de app.py, antes de qualquer outra
    lógica (storage, sidebar, abas). Se a senha ainda não foi validada
    nesta sessão do navegador, renderiza uma tela de login centralizada
    (reaproveitando a identidade visual do projeto) e interrompe a
    execução do restante do script com st.stop() até a senha estar
    correta.
    """
    if st.session_state.get(_CHAVE_SESSAO_AUTENTICADO):
        return

    senha_configurada = _obter_senha_configurada()
    if senha_configurada is None:
        st.error(
            "A senha de acesso não está configurada. Defina a seção "
            "`[auth]` com a chave `senha` em `.streamlit/secrets.toml` "
            "(veja `.streamlit/secrets.toml.example` para o formato)."
        )
        st.stop()

    _renderizar_tela_login(senha_configurada)
    st.stop()


def _obter_senha_configurada() -> str | None:
    """Lê a senha esperada de st.secrets. Retorna None (em vez de deixar
    o KeyError propagar cru) se a seção [auth] ou a chave senha não
    existirem — permite mostrar uma mensagem clara em vez de uma exceção
    confusa quando alguém esquece de configurar o secret."""
    try:
        return st.secrets["auth"]["senha"]
    except (KeyError, FileNotFoundError):
        return None


def _renderizar_tela_login(senha_configurada: str) -> None:
    """Formulário de senha centralizado, reaproveitando o cabeçalho
    institucional (logo + título) e o mesmo estilo de cartão já usado
    para o quadro de produtos (.mdf-painel-marker) — sem CSS novo."""
    styles.cabecalho("Acesso restrito")

    _, col_meio, _ = st.columns([1, 1.2, 1])
    with col_meio:
        with st.container(border=True):
            st.markdown('<span class="mdf-painel-marker"></span>', unsafe_allow_html=True)
            with st.form("mdf_form_login"):
                senha_digitada = st.text_input(
                    "Senha",
                    type="password",
                    placeholder="Digite a senha de acesso",
                    label_visibility="collapsed",
                )
                enviado = st.form_submit_button("Entrar", type="primary", width="stretch")

        if enviado:
            if senha_digitada == senha_configurada:
                st.session_state[_CHAVE_SESSAO_AUTENTICADO] = True
                st.rerun()
            else:
                st.error("Senha incorreta.")
