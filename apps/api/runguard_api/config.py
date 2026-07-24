from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    environment: str
    database_path: Path
    database_url: str | None
    database_seed: bool
    vector_dimensions: int
    redis_url: str | None
    redis_stream: str
    cors_origins: tuple[str, ...]
    cors_origin_regex: str
    execution_mode: str
    connector_mode: str
    prometheus_url: str | None
    prometheus_token: str | None
    loki_url: str | None
    loki_token: str | None
    github_repository: str | None
    github_token: str | None
    kubernetes_context: str | None
    kubernetes_namespace: str
    kubernetes_runner_image: str
    kubernetes_service_account: str
    kubernetes_job_timeout_seconds: int
    verification_url_template: str | None
    agent_backend: str
    llm_model: str
    llm_base_url: str | None
    embeddings_enabled: bool
    embedding_model: str
    policy_backend: str
    opa_url: str | None
    opa_fail_closed: bool
    otel_endpoint: str | None
    otel_service_name: str
    a2a_reviewer_url: str | None
    a2a_reviewer_token: str | None
    prompt_version: str
    policy_version: str


def load_settings() -> Settings:
    root = Path(os.getenv("RUNGUARD_PROJECT_ROOT") or str(Path.cwd())).resolve()
    database_path = Path(os.getenv("RUNGUARD_DATABASE_PATH", ".data/runguard.db"))
    if not database_path.is_absolute():
        database_path = root / database_path
    origins = os.getenv(
        "RUNGUARD_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return Settings(
        environment=os.getenv("RUNGUARD_ENVIRONMENT", "development"),
        database_path=database_path,
        database_url=os.getenv("RUNGUARD_DATABASE_URL") or None,
        database_seed=_bool("RUNGUARD_DATABASE_SEED", True),
        vector_dimensions=int(os.getenv("RUNGUARD_VECTOR_DIMENSIONS", "1536")),
        redis_url=os.getenv("RUNGUARD_REDIS_URL") or None,
        redis_stream=os.getenv("RUNGUARD_REDIS_STREAM", "runguard.incident-events"),
        cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
        cors_origin_regex=os.getenv(
            "RUNGUARD_CORS_ORIGIN_REGEX",
            (
                r"^https?://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|"
                r"192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)"
                r"(:\d+)?$"
            ),
        ),
        execution_mode=os.getenv("RUNGUARD_EXECUTION_MODE", "simulation"),
        connector_mode=os.getenv("RUNGUARD_CONNECTOR_MODE", "mock"),
        prometheus_url=os.getenv("RUNGUARD_PROMETHEUS_URL") or None,
        prometheus_token=os.getenv("RUNGUARD_PROMETHEUS_TOKEN") or None,
        loki_url=os.getenv("RUNGUARD_LOKI_URL") or None,
        loki_token=os.getenv("RUNGUARD_LOKI_TOKEN") or None,
        github_repository=os.getenv("RUNGUARD_GITHUB_REPOSITORY") or None,
        github_token=os.getenv("RUNGUARD_GITHUB_TOKEN") or None,
        kubernetes_context=os.getenv("RUNGUARD_KUBERNETES_CONTEXT") or None,
        kubernetes_namespace=os.getenv("RUNGUARD_KUBERNETES_NAMESPACE", "runguard-system"),
        kubernetes_runner_image=os.getenv(
            "RUNGUARD_KUBERNETES_RUNNER_IMAGE", "ghcr.io/odelialan/runguard-runner:1.1.0"
        ),
        kubernetes_service_account=os.getenv(
            "RUNGUARD_KUBERNETES_SERVICE_ACCOUNT", "runguard-executor"
        ),
        kubernetes_job_timeout_seconds=int(
            os.getenv("RUNGUARD_KUBERNETES_JOB_TIMEOUT_SECONDS", "180")
        ),
        verification_url_template=os.getenv("RUNGUARD_VERIFICATION_URL_TEMPLATE") or None,
        agent_backend=os.getenv("RUNGUARD_AGENT_BACKEND", "deterministic"),
        llm_model=os.getenv("RUNGUARD_LLM_MODEL", "gpt-5-mini"),
        llm_base_url=os.getenv("RUNGUARD_LLM_BASE_URL") or None,
        embeddings_enabled=_bool("RUNGUARD_EMBEDDINGS_ENABLED", False),
        embedding_model=os.getenv(
            "RUNGUARD_EMBEDDING_MODEL", "text-embedding-3-small"
        ),
        policy_backend=os.getenv("RUNGUARD_POLICY_BACKEND", "python"),
        opa_url=os.getenv("RUNGUARD_OPA_URL") or None,
        opa_fail_closed=_bool("RUNGUARD_OPA_FAIL_CLOSED", True),
        otel_endpoint=os.getenv("RUNGUARD_OTEL_EXPORTER_OTLP_ENDPOINT") or None,
        otel_service_name=os.getenv("RUNGUARD_OTEL_SERVICE_NAME", "runguard-api"),
        a2a_reviewer_url=os.getenv("RUNGUARD_A2A_REVIEWER_URL") or None,
        a2a_reviewer_token=os.getenv("RUNGUARD_A2A_REVIEWER_TOKEN") or None,
        prompt_version=os.getenv("RUNGUARD_PROMPT_VERSION", "1.1.0"),
        policy_version=os.getenv("RUNGUARD_POLICY_VERSION", "1.1.0"),
    )
