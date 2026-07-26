FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

COPY pyproject.toml uv.lock README.md VERSION ./
COPY apps/api ./apps/api
RUN uv sync --frozen --no-dev --extra production

RUN useradd --create-home --uid 10001 runguard
USER 10001

CMD ["python", "-m", "runguard_api.runner"]
