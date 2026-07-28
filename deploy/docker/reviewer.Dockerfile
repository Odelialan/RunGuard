FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE VERSION ./
COPY apps/api ./apps/api
COPY agents/prompts ./agents/prompts
RUN uv sync --frozen --no-dev && \
    chown -R 65532:65532 /app

USER 65532:65532
EXPOSE 8001
CMD ["uvicorn", "runguard_api.reviewer_service:app", "--host", "0.0.0.0", "--port", "8001"]
