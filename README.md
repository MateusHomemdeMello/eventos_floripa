# Qual a Boa Floripa — Streamlit

Refatoração do notebook original para uma aplicação Python em arquitetura MVC, pronta para VS Code, GitHub e Streamlit Community Cloud.

## Estrutura

```text
app/
  models/       # configuração e objetos de resultado
  views/        # interface Streamlit
  controllers/  # orquestração do pipeline
  services/     # Apify, OpenAI, HERE e exportação
data/output/    # arquivos gerados localmente
assets/         # template HTML do WebGIS
interface/      # versão antiga, não utilizada pelo gerador atual
streamlit_app.py
requirements.txt
```

## Segurança

Nenhuma chave do notebook original foi copiada para este projeto. **Revogue/rotacione as chaves que estavam expostas no notebook antes de publicar o repositório.**

No desenvolvimento local, copie `.streamlit/secrets.toml.example` para `.streamlit/secrets.toml` e preencha as credenciais. Nunca faça commit desse arquivo. Também é possível digitar as chaves na barra lateral durante a sessão.

## Rodar no VS Code

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

## Publicar no GitHub + Streamlit Community Cloud

1. Crie um repositório no GitHub e envie esta pasta.
2. Confirme que `secrets.toml` não entrou no Git.
3. No Streamlit Community Cloud, selecione o repositório e `streamlit_app.py`.
4. Em **App settings > Secrets**, cadastre:

```toml
OPENAI_API_KEY = "..."
APIFY_API_TOKEN = "..."
HERE_API_KEY = "..."
```

5. Faça o deploy.

## Fluxo

### Datas de processamento e CSVs cumulativos

Os três dataframes exibem `data_scraping`, `data_analise_ia` e
`data_geolocalizacao`, com data/hora ISO 8601 e fuso de São Paulo (`-03:00`).
A coleta registra sua conclusão; IA e HERE registram a conclusão de cada
resultado. Etapas ainda não executadas e datas históricas desconhecidas ficam
em branco. `data_publicacao` continua sendo a data original do post.

Os levantamentos são armazenados automaticamente em:

* `data/levantamentos/posts_coletados.csv`: cada post de cada nova coleta;
* `data/levantamentos/eventos_analisados.csv`: novos eventos extraídos pela IA;
* `data/levantamentos/eventos_geolocalizados.csv`: resultados concluídos da HERE,
  inclusive retornos sem coordenadas que precisem de revisão.

Há três botões para baixar esses arquivos na aba **Processamento**. Novas
coletas recebem `id_coleta`; os resultados seguintes preservam essa origem.
Cada linha tem `id_registro` para evitar duplicação ao restaurar a sessão ou
retomar uma gravação interrompida. Consultar novamente um post pela Apify gera
uma nova observação de coleta; reutilizar sua análise/localização já salva não
gera outra observação nessas etapas. Posts sem eventos permanecem no CSV de
coleta e no banco; não geram linhas artificiais no CSV de eventos.

Os registros existentes são preservados e os novos são acrescentados. A gravação
usa substituição atômica do arquivo completo, mantendo a união das colunas, para
evitar CSV parcialmente escrito e permitir novos campos. Listas e objetos são
JSON dentro das células. UTF-8 com BOM preserva os acentos ao abrir no Excel.
O mesmo bloqueio do banco protege as atualizações simultâneas dos levantamentos.

Ao abrir uma base antiga, seus resultados são incorporados uma única vez aos
CSVs, sem inventar datas anteriores. Falhas não recebem data de conclusão;
continuam nos arquivos de falhas. O WebMap permanece um único HTML em
`data/output`; os CSVs são arquivos de histórico do processamento separados.

### Custos, datas e localização

O modelo padrão é `gpt-4.1-mini`. As instruções fixas precedem os dados do post,
favorecendo o cache automático de prefixo da OpenAI; isso não elimina a cobrança
do prompt em cada chamada nem garante um acerto de cache. Imagens repetidas no
mesmo post são enviadas uma única vez. A busca web auxiliar fica desativada por
padrão e pode ser ativada na barra lateral (custo adicional). JSONs importados
preservam o modelo e a opção de busca web que foram salvos neles.

A extração procura o período completo de visitação, separando encerramento de
inauguração e de prazos de inscrição. A agenda mostra eventos de longa duração uma
única vez em “Em cartaz agora” quando já começaram, preservando o intervalo completo.
Os filtros verificam a interseção com esse intervalo. Eventos encerrados
antes da atualização não são exportados para o mapa; quando o horário final é
conhecido, o corte também considera esse horário em America/Sao_Paulo. Sem horário
final, o evento permanece durante o último dia. Sem data final, só há evidência
para o dia inicial. O histórico CSV é preservado. Datas já extraídas não são
reinterpretadas automaticamente; a melhoria do prompt vale para novas análises.

A HERE repete até três vezes falhas de conexão, HTTP 429 e erros de servidor.
HTTP 401/403 interrompe a etapa com orientação sobre credenciais e permissões.
Falhas na busca web preservam candidatos HERE já encontrados para revisão.
Com busca web desativada, a etapa HERE não exige chave OpenAI.

### Banco CSV e atualização incremental

O banco `data/banco_posts.csv` é carregado ao abrir o aplicativo. Cada linha
identifica um post pelo shortcode do Instagram e guarda o estado do processamento,
a data de atualização e os resultados em colunas JSON (um post pode ter vários eventos).
O arquivo é criado pela ferramenta e pode ser baixado na aba Resultados.

Após a coleta, a coluna `ja_analisado` mostra os posts reconhecidos no banco.
GPT é chamado somente para posts ainda não analisados, inclusive quando análises
anteriores falharam. Posts analisados sem eventos também são registrados.
Resultados GPT são salvos antes da localização; uma falha na HERE permite tentar
novamente apenas a localização pendente. Localizações concluídas são reutilizadas.
Posts editados com o mesmo shortcode continuam usando a análise armazenada.

Eventos novos são acrescentados ao histórico, sem remover eventos de coletas
anteriores. A gravação substitui o CSV de forma atômica e um bloqueio impede dois
processamentos simultâneos sobre o mesmo banco. Um banco inválido interrompe o
processamento sem sobrescrever o arquivo. Guarde cópias de segurança desse CSV.

Na primeira abertura, se o banco ainda não existir, a ferramenta aproveita
`data/output/eventos_processados.csv` (ou sua cópia arquivada em
`data/backups/legacy_output`): recupera os eventos extraídos e considera
pendente a localização dos registros sem coordenadas. Esse arquivo antigo não
permite recuperar posts que não produziram eventos.

O banco intermediário mantém o histórico completo. O HTML usa apenas eventos
com data final maior ou igual ao dia da exportação, no fuso
`America/Sao_Paulo`. Sem `data_fim`, usa `data_inicio`. Eventos que terminam hoje
permanecem; datas de início inválidas e intervalos invertidos ficam fora do mapa
e agenda. Eventos sem coordenadas aparecem na agenda/lista, sem marcador.
Eventos encerrados continuam no banco para evitar reanálises.

Na barra lateral, use **Importar configurações JSON** para selecionar um arquivo
ou colar JSON e clicar em **Aplicar JSON**. **Exportar configurações JSON** salva
todos os parâmetros de `AppConfig`, inclusive os avançados, e as chaves
`OPENAI_API_KEY`, `APIFY_API_TOKEN` e `HERE_API_KEY` em texto. Guarde o arquivo
em local privado. Na importação, chaves omitidas preservam as credenciais da sessão.
Use `configuracoes.exemplo.json` como modelo; parâmetros omitidos assumem
os valores padrão. Arquivos inválidos não alteram a configuração atual.

O botão **Testar conexão HERE** verifica a credencial antes de iniciar a coleta.
HTTP 401/403 exige conferir a chave, o acesso a Geocoding & Search e as restrições
de Trusted Domains no portal HERE. Durante o processamento, a HERE só é
consultada quando há eventos com localização pendente.

O WebMap abre diretamente no navegador, sem servidor ou framework frontend.
OpenStreetMap Standard é a base inicial. Leaflet, MarkerCluster, fontes e tiles
precisam de internet. CSS, JavaScript e eventos estão embutidos no único HTML.
O painel Streamlit existente continua sendo apenas a interface de processamento.

Mapa e Agenda compartilham busca sem acentos, categoria, favoritos e período
(todos, hoje, hoje à noite, amanhã, fim de semana). “Hoje à noite” considera
horários conhecidos que se sobrepõem ao período a partir das 18h; horários
ausentes não são inventados. O fim de semana corresponde a sábado e domingo,
incluindo o fim de semana atual aos sábados/domingos.

Há um marcador por endereço/local. Seu badge conta os eventos filtrados e o
cluster soma os eventos de todos os marcadores. O detalhe abre um carrossel com
setas, teclado, dots e swipe. “Me leva pra algum rolê” sorteia somente entre os
resultados filtrados e abre o evento escolhido no carrossel correto.

Favoritos são gravados em localStorage por identidade do post/evento e filtram
todas as visualizações. Ao mover o HTML para outro caminho ou navegador, o
armazenamento local pode mudar; se bloqueado, os favoritos duram só a sessão.
Geolocalização e compartilhamento dependem do navegador e suas permissões.
Fotos remotas são opcionais, com ícone da categoria enquanto carregam ou se falham.

1. Usuário informa perfis e parâmetros.
2. `PipelineController` coleta posts pela Apify.
3. `ai_service` interpreta legenda + imagens com OpenAI.
4. `geocoding_service` valida/localiza eventos pela HERE e pode usar pesquisa web da OpenAI como fallback.
5. `export_service.export_all` gera somente `data/output/qual_a_boa_floripa.html`.
6. Streamlit exibe progresso, tabela, erros, downloads e prévia do WebGIS.

O processamento pode ser executado de ponta a ponta ou em quatro etapas
independentes: **Coletar**, **Extrair**, **Localizar** e **Exportar**. A coleta é
salva em `data/posts_coletados.json`; cada extração e localização concluída é
persistida no banco imediatamente, permitindo retomar após interrupções. As
falhas ficam em `data/falhas_extracao.json` e `data/falhas_localizacao.json`.
Ao reabrir a ferramenta, todas as etapas salvas são restauradas. Extrair e
Localizar também atualizam Resultados e WebGIS automaticamente.

Na aba **Resultados**, a coluna **Publicar** controla a presença do evento no
WebGIS. Desmarcar e salvar mantém o evento no banco, mas o omite do HTML público.

## Gerador e testes do HTML

`assets/webgis_template.html` define a interface; `events_for_webgis` prepara os
registros; `render_webgis` serializa JSON com UTF-8 e proteção de fechamento de
script; `generate_webgis` grava o HTML. Os delimitadores EVENTOS_INICIO/FIM são
preservados. Não há dependência de arquivos locais auxiliares. A pasta antiga
`interface` não recebe novas exportações.

Execute `python -m pytest tests -q`. Os cenários de navegador estão em
`tests/test_webmap_browser.py` e usam Playwright/Chromium em desktop e celular,
com Leaflet real. Tiles e URLs de imagens quebradas são bloqueados nesses testes
para tornar as verificações determinísticas. `python -m tests.review_webmap`
regenera o HTML real e captura telas em `.webmap_review` para revisão visual.

## Observação

A aplicação mantém a lógica central do notebook, mas elimina dependências de Colab/Jupyter (`display`, `getpass`, `%pip`, `google.colab`) e concentra configurações em objetos explícitos.
