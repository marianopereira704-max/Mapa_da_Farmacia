# dados_demo/ — dados fictícios de demonstração

**Tudo dentro desta pasta é FICTÍCIO.** Nenhum arquivo aqui contém dado
real da Rede Melhor Compra — nem nome de produto, nem EAN, nem código de
loja, nem nome de consultor. Foi criado especificamente para demonstrar o
app publicado (Streamlit Community Cloud) para o TI, **antes** de
termos as credenciais reais do DigitalOcean Spaces configuradas.

Como o repositório GitHub deste projeto é **público**, é fundamental que
nada real circule por aqui — só dados óbvia e deliberadamente inventados:

- Loja: `0000`
- Consultor: "Consultor Demonstração"
- Produtos: "Produto Demonstração A", "Produto Demonstração B", ...
- EANs: sequenciais óbvios (`0000000000001`, `0000000000002`, ...) — têm
  o formato válido de um EAN de verdade (13 dígitos) só pra passar pela
  validação do app, não representam nenhum código de barras real.

## Como foi gerado

Por `scripts/gerar_dados_demo.py` — não foi criado manualmente. Rodar de
novo a qualquer momento regenera os arquivos do zero:

```bash
python scripts/gerar_dados_demo.py
```

O script escreve as planilhas fictícias, **relê elas de volta com os
mesmos módulos que o app usa em produção** (`modules.data_loader`) e
chama a função real de cálculo da sugestão automática
(`montar_tabela_ajuste_mix`) pra montar o `ajuste_mix.json` já "salvo" —
ou seja, os números que aparecem na demo (quais produtos recebem
sugestão automática, quantidades, etc.) vêm da lógica de negócio de
verdade, não foram inventados à mão.

## Estrutura gerada

```
dados_demo/
  0000/2026-07/
    mapa_farmacia.xlsx   (18 produtos fictícios)
    estoque.xlsx         (8 deles com estoque = 0 -- candidatos a sugestão automática)
    metadata.json        (consultor + timestamp fictícios)
    ajuste_mix.json       (já "salvo", pra Sugestão de GC e o PDF terem o que mostrar)
  _Base/
    base_mercado.parquet (demanda fictícia, mesmos EANs da loja acima)
```

Dos 8 produtos com estoque = 0, só os 5 de maior demanda (dentro desse
grupo) recebem sugestão automática — os outros 3 ficam de fora. Isso é
proposital: demonstra tanto o caso "recebe sugestão" quanto "não recebe
mesmo com estoque zerado" na mesma tela.

## ⚠️ Quando remover esta pasta

Assim que o `storage_mode` for trocado para `"digitalocean_spaces"` nos
secrets de produção (ou seja, assim que os dados reais estiverem
conectados), **esta pasta inteira deixa de servir para qualquer coisa** e
deve ser removida do repositório com um commit dedicado:

```bash
git rm -r dados_demo/
git commit -m "Remove dados fictícios de demonstração"
```

Não faz sentido manter dados de demonstração publicados junto com um app
já conectado a dados reais.
