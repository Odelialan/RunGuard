FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN pip install --no-cache-dir \
    "fastapi>=0.116,<1" \
    "prometheus-client>=0.22,<1" \
    "uvicorn[standard]>=0.35,<1"
COPY services/fault-injector/app.py /app/app.py

RUN useradd --create-home --uid 10001 fault-injector
USER 10001

EXPOSE 8090
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
