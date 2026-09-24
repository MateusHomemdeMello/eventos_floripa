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
interface/      # site estático e dados exportados
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

### Custos, datas e localização

O modelo padrão é `gpt-4.1-mini`. As instruções fixas precedem os dados do post,
favorecendo o cache automático de prefixo da OpenAI; isso não elimina a cobrança
do prompt em cada chamada nem garante um acerto de cache. Imagens repetidas no
mesmo post são enviadas uma única vez. A busca web auxiliar fica desativada por
padrão e pode ser ativada na barra lateral (custo adicional). JSONs importados
preservam o modelo e a opção de busca web que foram salvos neles.

A extração procura o período completo de visitação, separando encerramento de
inauguração e de prazos de inscrição. O calendário repete eventos em cada dia
do intervalo, inclusive o último, respeitando o filtro de datas. Eventos encerrados
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
`data/output/eventos_processados.csv`: recupera os eventos extraídos e considera
pendente a localização dos registros sem coordenadas. Esse arquivo antigo não
permite recuperar posts que não produziram eventos.

O CSV e o Excel de saída mantêm o histórico completo. O HTML usa apenas eventos
com coordenadas válidas e data final maior ou igual ao dia da exportação, no fuso
`America/Sao_Paulo`. Sem `data_fim`, usa `data_inicio`. Eventos que terminam hoje
permanecem; datas de início inválidas e intervalos invertidos ficam fora do mapa
e calendário. Eventos encerrados continuam no banco para evitar reanálises.

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

O WebGIS oferece ruas OpenStreetMap, satélite Esri e base cinza Esri nos modos
claro e escuro (o escuro aplica um filtro somente aos tiles). Os mapas precisam
de internet. O HTML exibido e baixado usa o template atualizado, inclusive para
resultados que já estão na sessão.

No HTML, o botão de relógio abre os filtros de data inicial/final e faixa diária
de horário. As categorias e os filtros são compartilhados entre Mapa e Agenda,
incluindo a busca e a lista de eventos. Eventos de vários dias são incluídos
quando seu período cruza as datas escolhidas. Com início e fim conhecidos,
o filtro de hora considera a interseção de horários; com apenas um horário,
considera somente esse horário. A opção de incluir eventos sem horário pode ser
desmarcada para mostrar apenas horários conhecidos. Limpar filtros restaura
todas as datas, horários e categorias.

1. Usuário informa perfis e parâmetros.
2. `PipelineController` coleta posts pela Apify.
3. `ai_service` interpreta legenda + imagens com OpenAI.
4. `geocoding_service` valida/localiza eventos pela HERE e pode usar pesquisa web da OpenAI como fallback.
5. `export_service` gera CSV, XLSX, o WebGIS HTML e atualiza `interface/dados.js`.
6. Streamlit exibe progresso, tabela, erros, downloads e prévia do WebGIS.

O processamento pode ser executado de ponta a ponta ou em quatro etapas
independentes: **Coletar**, **Extrair**, **Localizar** e **Exportar**. A coleta é
salva em `data/posts_coletados.json`; cada extração e localização concluída é
persistida no banco imediatamente, permitindo retomar após interrupções. As
falhas ficam em `data/falhas_extracao.json` e `data/falhas_localizacao.json`.
Ao reabrir a ferramenta, todas as etapas salvas são restauradas. Extrair e
Localizar também atualizam Resultados, WebGIS e o site automaticamente.

Na aba **Resultados**, a coluna **Publicar** controla a presença do evento no
WebGIS e no site estático. Desmarcar e salvar mantém o evento no histórico, CSV
e Excel, mas o omite das duas interfaces públicas.

## Interface web estática

A pasta `interface` contém uma aplicação web responsiva independente do Streamlit.
Ela oferece mapa, agenda, busca, categorias, filtros de data e detalhes dos eventos.
Ao concluir **Iniciar processamento**, o mesmo conjunto de eventos vigentes usado
no WebGIS é gravado em `interface/dados.js`. Assim, basta publicar essa pasta em
um serviço de hospedagem estática para disponibilizar a versão atualizada do site.

## Observação

A aplicação mantém a lógica central do notebook, mas elimina dependências de Colab/Jupyter (`display`, `getpass`, `%pip`, `google.colab`) e concentra configurações em objetos explícitos.
