FROM node:22-alpine AS web-builder
WORKDIR /web
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RUNGUARD_DATABASE_PATH=/data/runguard.db \
    RUNGUARD_MIGRATIONS_DIR=/app/deploy/postgres \
    RUNGUARD_WEB_DIST=/app/apps/web/dist

WORKDIR /app

COPY pyproject.toml README.md VERSION ./
COPY apps/api ./apps/api
COPY agents/prompts ./agents/prompts
COPY deploy/postgres ./deploy/postgres
COPY --from=web-builder /web/dist ./apps/web/dist
RUN pip install --no-cache-dir ".[production]"

RUN useradd --create-home --uid 10001 runguard \
    && mkdir -p /data \
    && chown -R runguard:runguard /data /app

USER 10001
EXPOSE 8000
CMD ["uvicorn", "runguard_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
