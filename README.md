# Mapa da Farmácia

App interno (Streamlit) para os consultores da Rede Melhor Compra analisarem
e ajustarem o mix de produtos de cada loja/farmácia, a partir de planilhas
de estoque, um "mapa" de gôndola e uma base nacional de demanda.

Protegido por senha única compartilhada (ver [Autenticação](#autenticação))
— não é destinado a ficar publicamente acessível.

## Visão geral: navegação e páginas

A barra lateral tem 3 páginas (não são mais abas no topo — só a página
**Análise** internamente usa `st.tabs()`, para as 3 sub-abas que já
existiam):

| Página | O que faz |
|---|---|
| **Upload** | Envio de arquivos direto pelo navegador: planilhas, fotos e planograma por loja, e a base nacional de demanda. É a porta de entrada dos dados — sem ela, não há nada para aparecer em Selecionar Loja/Análise. Se o arquivo já existe no destino, pede confirmação antes de substituir (por arquivo individual — enviar 3 arquivos juntos, com só 1 já existente, avisa só sobre esse). |
| **Selecionar Loja** | Tela de navegação visual em 2 níveis: lista de lojas (com filtro por consultor) e, ao escolher uma, os cartões de cada ciclo (mês) daquela loja, com um checklist de quais dos 5 arquivos já foram enviados. Substituiu os antigos selectboxes de Consultor/Loja/Ciclo na sidebar — ver [Descoberta do inventário](#descoberta-do-inventário) abaixo. |
| **Análise** | As 3 sub-abas de sempre (Ajuste de mix / Sugestão de GC / Conferência), abertas a partir de um cartão de ciclo escolhido em Selecionar Loja. Sem nenhuma loja/ciclo escolhido ainda (primeira vez na sessão), mostra uma mensagem com um botão para ir a Selecionar Loja, em vez de ficar em branco. |

Dentro de **Análise**, as 3 sub-abas continuam com exatamente a mesma
lógica de sempre:

| Sub-aba | O que faz |
|---|---|
| **Ajuste de mix** | Lista todos os produtos da loja (cruzando Mapa da Farmácia + Estoque + Base nacional), já com uma sugestão automática de quantidade para produtos em falta (ver [regra de negócio](#sugestão-automática-de-quantidade) abaixo). O consultor ajusta as quantidades manualmente e salva (`ajuste_mix.json`). Sempre recalcula do zero a partir dos arquivos atuais — nunca lê um ajuste salvo anteriormente. |
| **Sugestão de GC** | Mostra o **último ajuste de mix salvo** (lido de `ajuste_mix.json`, não recalculado) cruzado com posição/frentes do Mapa da Farmácia, pronto para exportar como PDF e entregar para o time de GC (Gestão de Categoria) executar a reposição física na loja. |
| **Conferência** | 3 cartões lado a lado (Antes / Modelo / Depois) com as fotos da gôndola, para o consultor comparar o estado real com o planograma esperado. Não depende do Mapa da Farmácia/Estoque — funciona mesmo que esses dois arquivos ainda não tenham sido enviados. |

### Ajuste de mix vs. Sugestão de GC — por que os números podem divergir

Ajuste de mix **sempre recalcula** a sugestão a partir dos arquivos mais
recentes (Mapa da Farmácia, Estoque, Base nacional). Sugestão de GC **só
lê o `ajuste_mix.json`** da última vez que o consultor clicou em
"Salvar" — se os arquivos de origem mudarem depois disso (nova planilha
de estoque, por exemplo) e o consultor não salvar de novo, a Sugestão de
GC fica desatualizada até o próximo save. Isso é intencional: reflete uma
"foto" que o consultor decidiu congelar, não o estado ao vivo.

## Arquitetura de armazenamento

Todo o acesso a arquivos passa por uma interface comum,
`OneDriveStorage` (`modules/storage/base.py`):

```python
listar_pasta(caminho_relativo) -> list[ItemPasta]
ler_arquivo_bytes(caminho_relativo) -> bytes
escrever_arquivo_bytes(caminho_relativo, bytes) -> None
existe(caminho_relativo) -> bool
```

Nenhum outro módulo (data_loader, file_resolver, app.py) importa um
backend diretamente — sempre pedem o cliente configurado via
`modules.storage.get_storage_client(secrets)`, que decide qual backend usar
com base em `secrets["storage_mode"]`. Trocar de backend é mudar
`.streamlit/secrets.toml`, não código.

Além dos 4 métodos acima, a interface também tem:

```python
listar_todos_arquivos(prefixo: str = "") -> list[str]
```

que devolve TODOS os arquivos sob um prefixo, recursivamente e
"achatado" (sem estrutura de pastas), numa única operação (ou o mínimo
de operações possível) — ver [Descoberta do inventário](#descoberta-do-inventário)
logo abaixo para o motivo dela existir.

| Backend | Arquivo | `storage_mode` | Status |
|---|---|---|---|
| Pasta local sincronizada | `modules/storage/local.py` | `"local"` | ✅ Funcional e testado — usado no dia a dia de desenvolvimento e em quem ainda tem o OneDrive sincronizado localmente. Não funciona hospedado num servidor remoto (depende do computador estar ligado e logado no OneDrive). |
| Microsoft Graph API | `modules/storage/graph.py` | `"graph_api"` | ⚠️ Implementado, mas **nunca testado com credenciais reais** (nunca saiu a aprovação de TI/Azure AD para uso em produção). Código pronto para retomar caso volte a ser viável no futuro. |
| DigitalOcean Spaces (S3-compatível) | `modules/storage/spaces.py` | `"digitalocean_spaces"` | 🆕 **Backend principal a partir desta fase do projeto.** Implementado e testado com [moto](https://github.com/getmoto/moto) (simulação local da API S3, sem credenciais reais) — aguardando o TI fornecer bucket/endpoint/chaves reais para o primeiro teste em produção. |

Ver exemplos de configuração de cada modo em
`.streamlit/secrets.toml.example`.

### DigitalOcean Spaces — detalhes específicos

Spaces é compatível com a API do S3, então o backend usa o `boto3` (cliente
S3 genérico da AWS) apontado para `endpoint_url` da DigitalOcean em vez do
endpoint padrão da Amazon. Diferença importante em relação aos outros dois
backends: um bucket S3 **não tem pastas de verdade** — o que existe são
chaves de objeto que podem conter `/`. `listar_pasta()` simula uma
listagem de pastas usando `Prefix` + `Delimiter="/"` do S3, then devolve os
mesmos `ItemPasta` que os outros backends devolveriam — o resto do app não
sabe (nem precisa saber) que a "pasta" é simulada.

### Descoberta do inventário

A página **Selecionar Loja** precisa saber, de uma vez, quais lojas
existem, quais ciclos (meses) cada uma tem, e quais arquivos já foram
enviados em cada ciclo — sem isso ela não consegue montar a lista nem os
cartões com checklist. A forma ingênua de fazer isso seria 1 chamada
`listar_pasta()` por loja (e mais 1 por ciclo dela) — o que significa que
o tempo de carregamento da tela cresceria linearmente com o número de
lojas (O(n)).

Em vez disso, `storage.listar_todos_arquivos()` faz **1 única operação**
(no backend Spaces: `list_objects_v2` **sem** `Delimiter`, que devolve
todo o conteúdo do bucket de uma vez, paginado automaticamente só se
passar de 1000 objetos — não por loja) e `modules/inventario.py:descobrir_inventario()`
monta a estrutura inteira (`Loja -> Ciclo -> {arquivos presentes, metadata}`)
em memória a partir dessa lista única. Isso é **O(1) em relação ao número
de lojas**, não O(n) — testado com moto simulando 2 e 20 lojas: mesmo
número de chamadas de rede nos dois casos.

Vale notar que a DigitalOcean Spaces **não cobra por chamada de API**
(diferente da AWS S3) — a otimização aqui não é para economizar
dinheiro, é para a tela carregar rápido mesmo com centenas de lojas
(cada chamada de rede tem uma latência mínima, e isso se acumula rápido
numa sequência de centenas de chamadas síncronas).

`descobrir_inventario()` é cacheado em `app.py` (mesmo padrão TTL 600s +
`versao_cache` já usado nas outras consultas) — o módulo em si
(`modules/inventario.py`) não depende de Streamlit, é testável
isoladamente. O backend `graph_api` implementa `listar_todos_arquivos()`
de forma recursiva (1 chamada por subpasta) em vez de otimizada — ver
comentário no código: aceitável porque esse backend não é mais o
principal do projeto.

## Convenção de nomenclatura de arquivos e pastas

Estrutura de pastas (idêntica nos 3 backends):

```
{Código da loja}/{AAAA-MM}/
    mapa_farmacia.xlsx
    estoque.xlsx
    modelo.pdf | .jpg | .png       (planograma — opcional)
    foto_antes.jpg | .png          (opcional)
    foto_depois.jpg | .png         (opcional)
    ajuste_mix.json                (gerado pelo app ao salvar a Aba 1)
    metadata.json                  (quem enviou por último, e quando)

_Base/
    base_mercado.parquet | .xlsx   (base nacional, compartilhada por todas as lojas)
```

- **O consultor não faz mais parte do caminho físico do arquivo** — não
  faz sentido com o DigitalOcean Spaces, que (diferente do OneDrive) não
  separa permissão por pasta. Em vez disso, cada ciclo (loja/mês) tem um
  `metadata.json` guardando pelo menos `{"consultor": "...", "enviado_em":
  "..."}`. Esse arquivo é reescrito (mesclado com o que já existir) a cada
  novo upload feito para aquele ciclo — se um segundo consultor enviar
  algo depois, o campo `consultor` passa a refletir o último a enviar (não
  vira uma lista). O consultor continua sendo usado como **filtro** na
  página Selecionar Loja, só que lido do `metadata.json`, não mais
  escolhido antes de navegar. Ciclos antigos, de antes dessa mudança, não
  têm `metadata.json` — o app trata isso graciosamente (omite o consultor
  do cabeçalho, não aparece no filtro) em vez de quebrar.
- Os nomes-base e extensões aceitos por tipo de arquivo estão centralizados
  em `config.FILE_SPECS` (e `config.BASE_NACIONAL_SPEC` para a base
  nacional) — é o único lugar do código que precisa mudar se um novo tipo
  de arquivo for adicionado.
- A busca de arquivo (`modules/file_resolver.py`) é **tolerante** a
  variações de maiúsculas/minúsculas, acentos e espaços/traços no nome
  (ex.: "Mapa_Farmacia.XLSX", "mapa da farmácia.xlsx" e
  "MAPA-FARMACIA.xlsx" são todos reconhecidos como o mesmo arquivo). O
  checklist de arquivos presentes em cada cartão de ciclo (Selecionar
  Loja) usa a mesma tolerância, via `modules/inventario.py`.
- **Subpasta de mês no formato `AAAA-MM`** (ex.: `2026-07`), não apenas o
  nome do mês — evita a colisão de "Julho de 2026" com "Julho de 2027"
  que o formato antigo (só o nome do mês) tinha.
- **Dados antigos, de antes dessas duas mudanças de convenção (consultor
  no caminho, formato do mês), não são migrados automaticamente.**
  `modules/inventario.py:descobrir_inventario()` só entende o formato
  novo de 2 níveis (`Loja/Ciclo/arquivo`) — uma estrutura antiga de 3
  níveis (`Consultor/Loja/Ciclo/arquivo`) aparece na página Selecionar
  Loja como se o nome do consultor fosse o código de uma loja (cosmético,
  não quebra nada, mas é um indício de dado desatualizado). Vale
  reorganizar manualmente qualquer dado antigo que ainda precise ser
  usado pelo app.

## Regras de negócio principais

### Sugestão automática de quantidade

Existe para apontar produtos de **incremento** de mix — só considera
produtos que a loja não tem (`estoque == 0`) e cujo EAN foi cruzado com
sucesso entre o Mapa da Farmácia, o Estoque e a Base nacional:

1. Filtra produtos com `estoque == 0` e EAN válido.
2. Ordena esse subconjunto por demanda de mercado (decrescente).
3. Os top `config.QTD_PRODUTOS_TOP_RANKING` (hoje: 5) desse subconjunto
   recebem sugestão automática de `config.QTD_SUGERIDA_ESTOQUE_ZERO`
   unidades (hoje: 3).
4. Todos os demais produtos (em estoque, fora do top N, ou com EAN não
   localizado) não recebem sugestão automática — ficam com a quantidade
   atual (editável manualmente pelo consultor).

Produtos com estoque > 0 **nunca** recebem sugestão automática, mesmo que
estivessem no topo do ranking de demanda — já estão no mix da loja, então
não fazem sentido como sugestão de inclusão. A ordem de **exibição** da
tabela continua sendo pela posição original de gôndola (Mapa da Farmácia),
não pelo ranking de demanda — o ranking serve só para escolher quem recebe
a sugestão, não para reordenar a tela.

Toda a regra está implementada em
`modules/data_loader.py:montar_tabela_ajuste_mix()` e é configurável
inteiramente via `config.py` (nenhum número mágico espalhado no código).

### Pré-processamento da base nacional

A base nacional de demanda passa por um pré-processamento local
(`scripts/preparar_base_nacional.py`) antes de ser enviada — remove
categorias fora de escopo (`config.PREFIXOS_CATEGORIA_EXCLUIDOS`) e
produtos sem demanda relevante (`config.DEMANDA_MINIMA_BASE_NACIONAL`), e
converte para `.parquet` (muito mais rápido de carregar que `.xlsx` — testado
~170x mais rápido em ~80 mil linhas). O app sempre prioriza o `.parquet`
quando ambos existem; o `.xlsx` bruto funciona como fallback caso o
pré-processamento ainda não tenha sido rodado naquele mês.

## Autenticação

Senha única, compartilhada entre todos os consultores — **não é um
sistema multiusuário**, é só uma camada extra de proteção caso o link do
app vaze (ex.: publicado no Streamlit Community Cloud). Implementada em
`modules/auth.py:exigir_autenticacao()`, chamada logo no início de
`app.py`, antes de qualquer outra lógica.

Configuração: seção `[auth]`, chave `senha`, em `.streamlit/secrets.toml`.
Sem essa seção configurada, o app mostra um erro claro em vez de travar
com uma exceção genérica.

## Dados de demonstração

A pasta `dados_demo/` na raiz do projeto contém um conjunto de dados
**100% fictícios** (loja, consultor, produtos e EANs inventados),
gerados por `scripts/gerar_dados_demo.py` — usada para demonstrar o app
publicado no Streamlit Community Cloud ao TI antes das credenciais reais
do DigitalOcean Spaces existirem. Ao contrário de `data/sample/` e
`data/onedrive_simulado/` (dados reais de teste, fora do Git), essa pasta
é **commitada de propósito**, já que o repositório é público e nada nela
é sensível.

Ver [`dados_demo/LEIA-ME.md`](dados_demo/LEIA-ME.md) para os detalhes —
inclusive **quando e como remover essa pasta** assim que o app estiver
rodando com dados reais.

## Pendências conhecidas

- **O código da loja na página Upload é digitado livremente**, sem
  validação contra uma lista real — porque essa fonte de verdade ainda
  não existe. O plano é integrar futuramente com uma API do TI que mantém
  a lista de lojas ativas na rede, mas essa API ainda não tem documentação
  técnica disponível. Até lá, um erro de digitação na loja cria uma pasta
  nova em vez de dar erro — vale conferir visualmente o que foi digitado
  antes de enviar. (O consultor também é livre, mas não tem esse risco —
  é só um metadado, não parte do caminho do arquivo.)
- **Backend `graph_api` (Microsoft Graph) nunca foi testado com
  credenciais reais** — a aprovação de TI/Azure AD para esse caminho não
  saiu, e o projeto migrou para DigitalOcean Spaces como plano principal.
  O código fica mantido caso o Graph API volte a ser viável no futuro.
- **Backend `digitalocean_spaces` aguarda as credenciais reais do TI**
  (bucket, endpoint, chaves de acesso) para o primeiro teste em produção —
  até lá, foi validado apenas com testes automatizados usando `moto`
  (simulação local da API S3).
- **O pré-processamento mensal da base nacional continua sendo manual**:
  alguém precisa rodar `scripts/preparar_base_nacional.py` localmente e
  então subir o `.parquet` resultante pela Seção B da aba Upload — não há
  automação/agendamento desse passo.

## Como rodar localmente

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# edite .streamlit/secrets.toml: configure storage_mode (recomendado:
# "local", apontando `raiz` para uma pasta no seu computador) e a senha
# em [auth]
streamlit run app.py
```

`.streamlit/secrets.toml` **nunca** deve ser commitado (já está no
`.gitignore`) — é o `.example` que fica versionado, como modelo.

## Deploy (Streamlit Community Cloud)

1. Publique o repositório (sem o `.streamlit/secrets.toml` real — ele não
   deve ir para o Git de jeito nenhum).
2. No painel do Streamlit Community Cloud, abra **Advanced settings** do
   app e cole o conteúdo do secrets.toml real (com os valores de produção)
   no campo de secrets — é o próprio painel que injeta isso como
   `st.secrets` em tempo de execução, sem precisar de nenhum arquivo no
   repositório.
3. Para produção, `storage_mode` deve ser `"digitalocean_spaces"` (não
   `"local"` — o Community Cloud não tem acesso a nenhum computador
   específico) assim que as credenciais reais do TI estiverem disponíveis.
