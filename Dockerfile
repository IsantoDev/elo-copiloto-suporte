# Elo: servidor (FastAPI + busca na CPU). O LLM (Ollama) e o banco (Postgres) ficam fora: OLLAMA_URL e PG* no .env.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
# tzdata: o horário da agenda e das datas segue o TZ (America/Sao_Paulo)
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONUTF8=1 \
    TZ=America/Sao_Paulo HF_HOME=/cache/hf ELO_HOST=0.0.0.0
WORKDIR /app

# dependências numa camada própria: mudar o código não rebaixa o torch
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY sql ./sql
RUN uv sync --frozen --no-dev

# o modelo de busca (EmbeddingGemma, acesso restrito) baixa na primeira subida com o HF_TOKEN do .env
# e fica no volume "modelos": não vai para a imagem nem precisa de token no build

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/saude', timeout=4)"
CMD ["python", "-m", "copilot_suporte.api"]
