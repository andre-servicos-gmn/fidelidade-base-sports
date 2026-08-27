# API do programa de fidelidade (FastAPI + worker de polling).
#
# Um único processo serve o webhook do WhatsApp, a API administrativa e — com
# RUN_WORKER_IN_APP=true — o worker de ingestão. Isso é proposital: o estado da
# conversa vive na memória do processo, então webhook e worker precisam estar
# no MESMO processo enquanto não houver Redis.
FROM python:3.12-slim

# - PYTHONUNBUFFERED: log sai na hora, sem buffer (essencial em container).
# - PYTHONDONTWRITEBYTECODE: não gera .pyc no filesystem da imagem.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependências primeiro (camada cacheada enquanto o pyproject não mudar).
COPY pyproject.toml README.md alembic.ini ./
COPY app ./app
COPY scripts ./scripts
RUN pip install --no-cache-dir .

# Usuário sem privilégios: nada aqui precisa de root.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# host 0.0.0.0 é obrigatório: em 127.0.0.1 o container fica inalcançável.
# Migrations NÃO rodam aqui de propósito — aplicar DDL no boot quebra feio se
# um dia houver mais de uma réplica subindo ao mesmo tempo. Rode à parte:
#   alembic upgrade head
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
