# Plano: mês como filtro principal e painel de Pendências (etapas 4 e 5)

Leva para o app real o que foi aprovado no protótipo de novembro/2026.
Commit e publicação só com pedido explícito, em cada passo.

## Decisões já tomadas

- O mês é o filtro principal e abre sempre no mês atual.
- Lista de lojas: código, razão social, consultor, "Ir para análise" e "Upload",
  sem pílulas.
- Loja sem arquivos no mês só aparece na busca, e só com "Upload".
- Pendências mostra todas as lojas com arquivos no mês, um mês por vez.
- O Excel sai sempre com todas as lojas do mês, sem os filtros da tela.
- Loja que não enviou nada não entra no Excel.
- Regra para a aba principal do Excel: loja ativa na API, com consultor e fora da
  lista manual. Os demais vão para a aba "Fora da rede", com o motivo. O termo
  "fora da regra" não aparece em lugar nenhum.
- API fora do ar: o Excel não é gerado.
- Falha de conexão ao baixar foto: tenta até dar certo, sem limite de tempo.
- Com duas versões da mesma foto, vale a mais nova, sem apagar nada do bucket.
- Lista de lojas com paginação, 20 por página. Busca, filtro ou troca de mês voltam
  à primeira página.
- Mês atual vazio: mensagem e atalho para o mês anterior.
- O envio para um mês passado é permitido. O Excel mostra a data e a hora do envio
  ("Último envio", em Brasília) nas abas Conferência, Checklist e Fora da rede.
- Exportar com um clique. Se não funcionar bem no teste, passa a dois cliques.
- Além de "Último envio", o Excel mostra a data de Foto Antes e de Foto Depois, lida
  do próprio arquivo no bucket, porque um envio posterior muda o "Último envio".
- Tabela de Pendências também com paginação, 20 lojas por página.

## Etapa 4: Selecionar Loja por mês

### 4.1 Regras, sem Streamlit (`modules/selecao_loja.py`): feito

- `mes_atual()` no fuso de Brasília. Hoje o Upload usa `date.today()`, que no
  servidor em UTC vira o mês às 21h do último dia. A troca no Upload entra na 4.2.
- `meses_da_barra(inventario, hoje)`: mês atual mais os meses com arquivos, do
  mais novo para o mais antigo, com a contagem de lojas. Ignora códigos com `_`.
- `lojas_do_mes(inventario, mes)` e `buscar_lojas(texto, ...)`: código pelo começo;
  razão social e nome fantasia em qualquer parte, sem diferenciar acento.
- `paginar(itens, pagina)`: 20 por página.

### 4.2 Upload abre pronto: feito

Pré-requisito do botão "Upload".

- Corrigir o pré-preenchimento: hoje a loja aparece no campo, mas o envio recusa
  com "Preencha o código da loja" até clicar na sugestão. Passar `default=loja_prefill`
  ao `st_searchbox`.
- Respeitar o mês vindo da barra, mesmo se a loja ainda não tiver arquivos nele.
- Mostrar no topo a loja e o mês de destino, para evitar envio no mês errado.
- Mostrar Retrato e Estoque atualizado em mês já existente, sem depender do ajuste
  salvo.
- Testes: envio com a loja pré-preenchida grava na pasta `{loja}/{mês}` certa, sem
  novo clique.

### 4.3 Nova tela de Selecionar Loja: feito

Publicar junto com a 4.4: sem os cartões, a lista de arquivos do mês só volta a ser
visível com o painel "Arquivos do mês".

- Barra de meses, busca e filtros de consultor e supervisora atrás de um link.
- Lista com `st.button` nativo e paginação: 20 lojas por página dão 40 botões, em
  vez dos 382 sem paginação, e dispensam um componente próprio.
- Extrair `_abrir_analise(loja, mes)`, hoje dentro do cartão de mês.
- Remover o nível 2 (cartões de mês por loja), que a barra substitui.
- Precisa da lista completa da API (`apenas_ativas=False`) em cache próprio, porque
  a lista atual só traz lojas ativas.
- Mês atual vazio: mensagem e atalho para o mês anterior.
- API fora do ar: lista só com código, como os filtros já fazem hoje.

### 4.4 Cabeçalho da Análise: feito

- "Trocar de loja" limitado ao mês aberto.
- Troca de mês, quando a loja tem mais de um.
- Painel "Arquivos do mês · N de 7", com botão para enviar.
- "Enviar agora" na Conferência, quando faltar Retrato ou Estoque atualizado.

### 4.5 Ajuste de mix com alteração não salva

- Faixa com o número de alterações, último salvamento, "Salvar" e "Descartar".
- Na Sugestão de GC, aviso de que o PDF usa o último ajuste salvo.

## Etapa 5: Pendências e exportação

### 5.1 Relatório dentro do app

- Mover a lógica de `scripts/relatorio_mensal.py` para `modules/relatorio_mensal.py`.
  O script fica só com os argumentos e a gravação do arquivo.
- "Último envio" no Checklist: feito.
- Colunas com a data de Foto Antes e de Foto Depois (`modificado_em` do bucket), em
  Brasília.
- Incluir colunas de Retrato e Estoque atualizado (7 arquivos, igual a Pendências).
- Aba "Fora da rede": coluna de motivo e loja ativa sem consultor passa a ir para lá.
- Não gerar com a API fora do ar, no script e no app.
- Aumentar `VERSAO_RELATORIO`.

### 5.2 Página Pendências

- Novo item no menu lateral, com a mesma barra de meses.
- Filtros de consultor e supervisora.
- Quadros por arquivo: o clique filtra a tabela.
- Tabela com quem tem mais arquivos faltando primeiro, botão "Abrir" e paginação de
  20 lojas por página.
- Regras sem Streamlit e com testes (`pendencias_do_mes`).

### 5.3 Botão Exportar

Primeiro com um clique: `download_button` com função geradora.

Pontos a observar no teste: na documentação do Streamlit, a função geradora ignora
comandos do Streamlit, então provavelmente não dá para mostrar "Gerando relatório"
durante a espera. Em espera longa, o download também pode ser cortado.

Se não funcionar bem: "Exportar relatório" gera com `st.spinner` e, ao terminar,
aparece "Baixar".

### 5.4 Verificação antes de publicar

- Testes automáticos e navegador local com dados reais, sem gravar fora de `_relatorios/`.
- Tempo da lista com o mês mais cheio (191 lojas).
- Tela durante a espera, com bucket fora do ar simulado.
- Depois de publicar, com autorização: tempo e memória no Streamlit Cloud, e uma
  geração com espera longa.

## Ordem

4.1 → 4.2 → 4.3 → 4.4 → 4.5 → 5.1 → 5.2 → 5.3 → 5.4. A 4.2 pode ser publicada
sozinha, porque corrige um erro que existe hoje.

## Fora deste plano

Validação dos arquivos no envio (`.xls`, arquivo corrompido, colunas), da etapa 3,
fica para depois.

## Riscos

- "Último envio" é único por loja e mês: qualquer envio posterior, como o Retrato do
  mês seguinte, muda essa data. As colunas de data de cada foto cobrem esse caso.
- Trocar a tela inicial muda o hábito de quem já usa os cartões de mês.
- O Cloud pode reiniciar durante uma espera muito longa, e aí a geração recomeça do
  zero. Só dá para verificar depois de publicar.
