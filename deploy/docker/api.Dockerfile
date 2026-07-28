FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS web-builder
WORKDIR /web
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web ./
RUN npm run build

FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RUNGUARD_DATABASE_PATH=/data/runguard.db \
    RUNGUARD_MIGRATIONS_DIR=/app/deploy/postgres \
    RUNGUARD_WEB_DIST=/app/apps/web/dist \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

COPY pyproject.toml uv.lock README.md VERSION ./
COPY apps/api ./apps/api
COPY agents/prompts ./agents/prompts
COPY deploy/postgres ./deploy/postgres
COPY --from=web-builder /web/dist ./apps/web/dist
RUN uv sync --frozen --no-dev --extra production

RUN useradd --create-home --uid 10001 runguard \
    && mkdir -p /data \
    && chown -R runguard:runguard /data /app

USER 10001
EXPOSE 8000
CMD ["uvicorn", "runguard_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
