# Resultado da ação — plano de execução

Data: 04/11/2026 (revisado no mesmo dia, depois da validação na loja 604)
Mockup: https://claude.ai/artifact/PsAoukfnYJXG11MjcRUUNH
Telas afetadas: aba **Conferência** → seção "Resultado da ação"

---

## 1. O que muda (resumo das decisões)

| Tema | Hoje | Novo |
|---|---|---|
| Base da lista | Produtos do ajuste salvo ∩ Mapa, mais "novos" vindos da loja inteira | **Todos os produtos do Mapa**. Nada de fora do Mapa entra na lista |
| Ajuste de mix | Define a lista | **Não entra no cálculo.** A maioria dos consultores não pede a compra ao lojista, então o resultado olha o que aconteceu na loja, não o que foi pedido |
| Grupos | — | **Já na loja**, **Produtos novos**, **Não entraram** |
| Antes | Unidades 3 meses ÷ 3 × preço do Estoque (preço 0 quando estoque 0) | Mesmo cálculo; **preço do Retrato quando o Estoque não dá preço** |
| Depois | Retrato | Retrato (período lido do arquivo — trabalho pronto, aguardando commit) |
| Número principal | Crescimento em R$ e % | **Ganho acima da loja** (R$/mês), comparando **unidades** |
| Situações | normal, sem_venda_periodo, deixou_de_vender, novo, sem_dado | Cresceu, Caiu, Estável, Novo com venda, Novo sem venda, Zerou o estoque, Saiu do mix, Não entrou, Sem dado |
| Pouca venda | — | Cresceu/Caiu só com **≥ 1 unidade e ≥ 5%**; % só aparece com antes ≥ 2 un./mês |
| Tela | 3 cards + Top 3 + "deixaram de vender" + lista | Período · cartão principal · Por grupo (cards clicáveis) · Produto a produto (filtros) · Como é calculado |

## 2. Regras de negócio

Valores configuráveis em `config.py`:

```python
RESULTADO_VARIACAO_MIN_UNIDADES = 1     # diferença mínima para Cresceu/Caiu
RESULTADO_VARIACAO_MIN_PCT = 5          # % mínima para Cresceu/Caiu
RESULTADO_UNIDADES_MIN_PARA_PCT = 2     # abaixo disso, a variação não mostra %
RESULTADO_DIAS_COMPRA_RECENTE = 60      # "comprado recentemente"
```

Notação: `est_ini` = estoque no Estoque do mês do planograma; `antes` = unidades de 3 meses ÷ 3; `depois` = unidades do Retrato; `est_fim` = estoque no Estoque atualizado (produto ausente = 0).

### 2.1 Grupos

| Grupo | Regra | Loja 604 |
|---|---|---|
| Já na loja | `est_ini > 0` **ou** `antes > 0` | 49 |
| Produtos novos | `est_ini == 0` e `antes == 0`, e depois `depois > 0` **ou** `est_fim > 0` | 5 |
| Não entraram | `est_ini == 0` e `antes == 0`, e continuou `depois == 0` e `est_fim == 0` | 59 |

Um produto com estoque 0 que já vendia nos 3 meses (ruptura) é **Já na loja**, não é novo. Produto com EAN inválido não entra em nenhum grupo nem em nenhuma conta: aparece na lista como **Sem dado** (1 na loja 604).

### 2.2 Situação de cada produto (primeira regra que bater)

1. **Sem dado** — EAN inválido/não localizado.
2. Grupo **Não entraram** → **Não entrou**.
3. Grupo **Produtos novos** → `depois > 0`: **Novo com venda**; senão: **Novo sem venda**.
4. Grupo **Já na loja**:
   - `est_fim == 0`: `depois > 0` ou compra nos últimos 60 dias → **Zerou o estoque**; senão → **Saiu do mix**.
   - `|depois − antes| ≥ 1` **e** `|variação %| ≥ 5` → **Cresceu** / **Caiu**. Com `antes == 0` e `depois ≥ 1` → Cresceu.
   - Senão → **Estável**.

Os 60 dias contam a partir do último dia do período do Retrato. Produto sem data de compra conta como "sem compra recente".

A coluna **Variação** é em unidades ("+3 un. · +75%"), para sempre concordar com a situação. Os R$ aparecem nas colunas Antes e Depois.

### 2.3 Números do topo

Comparação em **unidades**, porque não depende de preço (na 604, o cálculo em R$ variou de −R$ 453 a −R$ 1.243 conforme o método).

- **Gôndola** = todos os produtos do Mapa. Crescimento = `depois ÷ antes − 1`, em unidades.
- **Resto da loja** = produtos do Estoque e do Retrato fora do Mapa, mesmo cálculo.
- **Ganho acima da loja (R$/mês)** = `R$ antes da gôndola × (crescimento gôndola − crescimento resto)`.
- A linha de apoio mostra as unidades e os R$: "157 → 152 unidades por mês · R$ 2.962 → R$ 3.259".
- **O número pode ser negativo**, e a tela mostra isso em vermelho, sem suavizar.
- Sem base para comparar (`antes` zerado na gôndola ou no resto): o cartão mostra "Sem base para comparar", nunca um número estimado.

### 2.4 Cards por grupo

| Card | Valor | Apoio |
|---|---|---|
| Já na loja | variação % em unidades | unidades antes → depois · quantos cresceram e caíram |
| Produtos novos | "X de N venderam" | unidades e R$ vendidos no mês |
| Não entraram | "N produtos" | "Oportunidade para a próxima conversa com o lojista" |

## 3. Validação já feita (T1)

Loja 604, ciclo 2026-09, Retrato de 01/10 a 31/10. A 76 ainda não tem Retrato nem Estoque atualizado; decisão: seguir sem ela.

| Medida | Resultado |
|---|---|
| Faturamento do Retrato que cruza com o Estoque | 85,2% — abaixo dos 90% previstos; motivou a troca de R$ por unidades |
| Gôndola × resto da loja, em unidades | −3,0% × +5,1% |
| Ganho acima da loja | cerca de −R$ 240/mês |
| Produtos novos | 5, todos com compra em set/out |
| Produtos do Estoque atualizado com data de compra | 5.900 de 6.265 |

Uma loja e um mês só mostram que a conta funciona. **Não dizem se a gôndola costuma dar resultado.**

## 4. Arquitetura

```
data_loader.py
  carregar_estoque_atualizado_resultado()  ← novo: ean, estoque, ultima_compra
  carregar_estoque_resultado()             ← já existe (antes, 3 meses)
  carregar_retrato_vendas()                ← já existe (depois)

resultado_acao.py  ← módulo novo, só regras (sem Streamlit)
  montar_resultado_acao(mapa, estoque, retrato, estoque_atual, fim_periodo) -> ResultadoAcao
    .tabela   (1 linha por produto do Mapa: grupo, situação, antes, depois, variação)
    .grupos   (resumo por grupo)
    .gondola / .resto_loja / .ganho_acima_loja

app.py
  _renderizar_resultado_acao(resultado, periodo_antes, periodo_depois)  ← reescrito

components/cards_grupo/  ← componente HTML (st.components.v2) dos 3 cards clicáveis
styles.py               ← CSS novo; remove o CSS do Top 3 / "deixaram de vender"
```

A exigência de ajuste salvo para mostrar a seção cai. Continuam obrigatórios Mapa, Estoque, Retrato e Estoque atualizado; faltando algum, a tela avisa qual.

## 5. Tarefas

Cada tarefa termina com o teste indicado passando.

### T0 — Pré-requisito: período do Retrato
O trabalho de ler o período do arquivo está pronto em `app.py` e `data_loader.py`, sem commit. O "Depois" da nova tela depende dele. O plano segue em cima dele, e você decide quando publicar.

### T2 — Configurações
Adicionar as 4 constantes da seção 2 em `config.py`, com comentário.
**Teste:** import do módulo.

### T3 — Leitura do Estoque atualizado completo
`carregar_estoque_atualizado_resultado()` em `data_loader.py`: além de ean/estoque, lê **Última compra**, aceitando as variações de nome de coluna já vistas. Coluna ausente vira vazio, sem erro.
**Teste:** arquivos reais da 604 e da 1188 e um arquivo sem a coluna.

### T4 — Módulo de regras `resultado_acao.py`
Implementar a seção 2 inteira.
**Teste (`tests/test_resultado_acao.py`, pytest):** um caso por linha das tabelas de grupo e situação, limites exatos (0,9 un.; 4,9%; 60 × 61 dias), produto fora do Mapa não entra, ruptura com venda fica em Já na loja, "sem base para comparar", e **os totais da 604** (49/5/60 e −3,0% × +5,1%).

### T5 — Carregamento no app
`_carregar_dados_resultado_acao` passa a usar o novo leitor e `pagina_conferencia` deixa de exigir ajuste salvo. Cache continua por assinatura do ciclo.
**Teste:** abrir a Conferência da 604 localmente.

### T6 — Tela: período, cartão principal e explicação
Reescrever `_renderizar_resultado_acao`:
- título + "Antes" e "Depois" em duas linhas, com ⓘ;
- cartão "Ganho acima da loja" + frase + linha de unidades e R$ + barras Gôndola × Resto da loja, com visual próprio para resultado negativo;
- "Como o resultado é calculado" em `st.expander`.
Remove Top 3, "deixaram de vender" e os 3 cards antigos.

### T7 — Tela: cards "Por grupo" clicáveis
Componente HTML próprio (`st.components.v2`) com os 3 cards: clique filtra, card ativo com borda e sombra, outros esmaecidos, clique de novo limpa. Devolve o grupo escolhido para o Python.
**Plano B**, se o componente der problema no Streamlit Cloud: cards em HTML + `st.pills` de grupo logo abaixo.

### T8 — Tela: Produto a produto
- Filtros de situação com `st.pills` (total no rótulo, dica no `help`);
- tabela em HTML com as cores do mockup, do maior para o menor impacto, paginada com o `_TAMANHO_PAGINA_LISTA` que já existe;
- "Limpar filtros", rodapé "Mostrando X de N", "Exportar lista" (CSV com a tabela filtrada).

### T9 — Estilos
Tokens de cor e espaço do mockup em `styles.py`; remoção do CSS que ficou sem uso.

### T10 — Teste da tela (AppTest)
Abrir a Conferência com dados falsos: renderiza sem erro, filtros reduzem a lista, ciclo sem Retrato mostra o aviso de arquivo faltando, ciclo sem ajuste salvo mostra o resultado normalmente.

### T11 — Conferência manual
Na 604, comparar 10 produtos e o total de cada grupo com uma conta manual em planilha. Você valida antes de publicar.

### T12 — Publicação
Só quando você pedir: commit e push na `main` (publica no Streamlit Cloud).

## 6. Riscos

| Risco | Efeito | Mitigação |
|---|---|---|
| Só uma loja validada | O número pode se comportar diferente em outras | Conferir a 76 quando o Retrato dela chegar |
| Comparação entre meses diferentes | Sazonalidade (gripe, por exemplo) afeta gôndola e resto de formas diferentes | Comparar com o resto da loja reduz, mas não elimina; explicar na tela |
| Unidades caem e R$ sobe | Pode confundir o lojista (na 604: −3% em unidades, R$ 2.962 → R$ 3.259) | Linha de apoio mostra os dois |
| Componente customizado no Cloud | Cards sem clique | Plano B do T7 |
| Retrato de mês errado | Comparação sem sentido | Período lido do arquivo (T0) e exibido no topo |
