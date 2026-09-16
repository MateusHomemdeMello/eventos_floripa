# Qual a Boa Floripa — Streamlit

Refatoração do notebook original para uma aplicação Python em arquitetura MVC, pronta para VS Code, GitHub e Streamlit Community Cloud.

## Estrutura

```text
app/
  models/       # configuração e objetos de resultado
  views/        # interface Streamlit
  controllers/  # orquestração do pipeline
  services/     # Apify, OpenAI, HERE e exportação
assets/         # template HTML do WebGIS
data/output/    # arquivos gerados localmente
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

1. Usuário informa perfis e parâmetros.
2. `PipelineController` coleta posts pela Apify.
3. `ai_service` interpreta legenda + imagens com OpenAI.
4. `geocoding_service` valida/localiza eventos pela HERE e pode usar pesquisa web da OpenAI como fallback.
5. `export_service` gera CSV, XLSX e o WebGIS HTML.
6. Streamlit exibe progresso, tabela, erros, downloads e prévia do WebGIS.

## Observação

A aplicação mantém a lógica central do notebook, mas elimina dependências de Colab/Jupyter (`display`, `getpass`, `%pip`, `google.colab`) e concentra configurações em objetos explícitos.
