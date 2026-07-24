FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RUNGUARD_DATABASE_PATH=/data/runguard.db

WORKDIR /app

COPY pyproject.toml README.md VERSION ./
COPY apps/api ./apps/api
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 runguard \
    && mkdir -p /data \
    && chown -R runguard:runguard /data /app

USER 10001
EXPOSE 8000
CMD ["uvicorn", "runguard_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
