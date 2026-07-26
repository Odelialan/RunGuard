from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    environment: str
    enforce_production_guards: bool
    database_path: Path
    database_url: str | None
    database_seed: bool
    database_pool_min_size: int
    database_pool_max_size: int
    vector_dimensions: int
    redis_url: str | None
    redis_stream: str
    cors_origins: tuple[str, ...]
    cors_origin_regex: str
    public_base_url: str | None
    execution_mode: str
    connector_mode: str
    mcp_prometheus_url: str | None
    mcp_prometheus_token: str | None
    mcp_loki_url: str | None
    mcp_loki_token: str | None
    mcp_kubernetes_url: str | None
    mcp_kubernetes_token: str | None
    mcp_github_url: str | None
    mcp_github_token: str | None
    prometheus_url: str | None
    prometheus_token: str | None
    loki_url: str | None
    loki_token: str | None
    github_repository: str | None
    github_token: str | None
    kubernetes_context: str | None
    kubernetes_namespace: str
    kubernetes_allowed_namespaces: tuple[str, ...]
    target_inventory_json: str
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
    auth_mode: str
    api_keys_json: str
    oidc_issuer: str | None
    oidc_audience: str | None
    oidc_jwks_url: str | None
    oidc_algorithms: tuple[str, ...]
    oidc_roles_claim: str
    rate_limit_per_minute: int
    max_request_body_bytes: int
    prometheus_webhook_secret: str | None
    langgraph_checkpoint_backend: str
    langgraph_checkpoint_encryption_key: str | None
    auto_recover: bool
    recovery_concurrency: int
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
    kubernetes_namespace = os.getenv(
        "RUNGUARD_KUBERNETES_NAMESPACE",
        "runguard-system",
    )
    allowed_namespaces = os.getenv(
        "RUNGUARD_KUBERNETES_ALLOWED_NAMESPACES",
        kubernetes_namespace,
    )
    return Settings(
        environment=os.getenv("RUNGUARD_ENVIRONMENT", "development"),
        enforce_production_guards=_bool("RUNGUARD_ENFORCE_PRODUCTION_GUARDS", False),
        database_path=database_path,
        database_url=os.getenv("RUNGUARD_DATABASE_URL") or None,
        database_seed=_bool("RUNGUARD_DATABASE_SEED", True),
        database_pool_min_size=int(os.getenv("RUNGUARD_DATABASE_POOL_MIN_SIZE", "2")),
        database_pool_max_size=int(os.getenv("RUNGUARD_DATABASE_POOL_MAX_SIZE", "20")),
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
        public_base_url=os.getenv("RUNGUARD_PUBLIC_BASE_URL") or None,
        execution_mode=os.getenv("RUNGUARD_EXECUTION_MODE", "simulation"),
        connector_mode=os.getenv("RUNGUARD_CONNECTOR_MODE", "mock"),
        mcp_prometheus_url=os.getenv("RUNGUARD_MCP_PROMETHEUS_URL") or None,
        mcp_prometheus_token=os.getenv("RUNGUARD_MCP_PROMETHEUS_TOKEN") or None,
        mcp_loki_url=os.getenv("RUNGUARD_MCP_LOKI_URL") or None,
        mcp_loki_token=os.getenv("RUNGUARD_MCP_LOKI_TOKEN") or None,
        mcp_kubernetes_url=os.getenv("RUNGUARD_MCP_KUBERNETES_URL") or None,
        mcp_kubernetes_token=os.getenv("RUNGUARD_MCP_KUBERNETES_TOKEN") or None,
        mcp_github_url=os.getenv("RUNGUARD_MCP_GITHUB_URL") or None,
        mcp_github_token=os.getenv("RUNGUARD_MCP_GITHUB_TOKEN") or None,
        prometheus_url=os.getenv("RUNGUARD_PROMETHEUS_URL") or None,
        prometheus_token=os.getenv("RUNGUARD_PROMETHEUS_TOKEN") or None,
        loki_url=os.getenv("RUNGUARD_LOKI_URL") or None,
        loki_token=os.getenv("RUNGUARD_LOKI_TOKEN") or None,
        github_repository=os.getenv("RUNGUARD_GITHUB_REPOSITORY") or None,
        github_token=os.getenv("RUNGUARD_GITHUB_TOKEN") or None,
        kubernetes_context=os.getenv("RUNGUARD_KUBERNETES_CONTEXT") or None,
        kubernetes_namespace=kubernetes_namespace,
        kubernetes_allowed_namespaces=tuple(
            namespace.strip()
            for namespace in allowed_namespaces.split(",")
            if namespace.strip()
        ),
        target_inventory_json=os.getenv("RUNGUARD_TARGET_INVENTORY_JSON", "{}"),
        kubernetes_runner_image=os.getenv(
            "RUNGUARD_KUBERNETES_RUNNER_IMAGE", "ghcr.io/odelialan/runguard-runner:1.2.1"
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
        auth_mode=os.getenv("RUNGUARD_AUTH_MODE", "disabled").lower(),
        api_keys_json=os.getenv("RUNGUARD_API_KEYS_JSON", "{}"),
        oidc_issuer=os.getenv("RUNGUARD_OIDC_ISSUER") or None,
        oidc_audience=os.getenv("RUNGUARD_OIDC_AUDIENCE") or None,
        oidc_jwks_url=os.getenv("RUNGUARD_OIDC_JWKS_URL") or None,
        oidc_algorithms=tuple(
            item.strip()
            for item in os.getenv("RUNGUARD_OIDC_ALGORITHMS", "RS256").split(",")
            if item.strip()
        ),
        oidc_roles_claim=os.getenv("RUNGUARD_OIDC_ROLES_CLAIM", "roles"),
        rate_limit_per_minute=int(os.getenv("RUNGUARD_RATE_LIMIT_PER_MINUTE", "120")),
        max_request_body_bytes=int(
            os.getenv("RUNGUARD_MAX_REQUEST_BODY_BYTES", "1048576")
        ),
        prometheus_webhook_secret=os.getenv("RUNGUARD_PROMETHEUS_WEBHOOK_SECRET") or None,
        langgraph_checkpoint_backend=os.getenv(
            "RUNGUARD_LANGGRAPH_CHECKPOINT_BACKEND",
            "postgres" if os.getenv("RUNGUARD_DATABASE_URL") else "memory",
        ).lower(),
        langgraph_checkpoint_encryption_key=os.getenv("LANGGRAPH_AES_KEY") or None,
        auto_recover=_bool("RUNGUARD_AUTO_RECOVER", False),
        recovery_concurrency=int(os.getenv("RUNGUARD_RECOVERY_CONCURRENCY", "4")),
        prompt_version=os.getenv("RUNGUARD_PROMPT_VERSION", "1.2.1"),
        policy_version=os.getenv("RUNGUARD_POLICY_VERSION", "1.2.1"),
    )


def validate_settings(settings: Settings) -> None:
    inventory = parse_target_inventory(settings.target_inventory_json)
    if settings.auth_mode not in {"disabled", "api_key", "oidc"}:
        raise RuntimeError("RUNGUARD_AUTH_MODE must be disabled, api_key, or oidc.")
    if settings.connector_mode not in {"mock", "hybrid", "production", "mcp"}:
        raise RuntimeError("RUNGUARD_CONNECTOR_MODE must be mock, hybrid, production, or mcp.")
    if settings.langgraph_checkpoint_backend not in {"memory", "postgres"}:
        raise RuntimeError(
            "RUNGUARD_LANGGRAPH_CHECKPOINT_BACKEND must be memory or postgres."
        )
    if settings.recovery_concurrency < 1 or settings.recovery_concurrency > 32:
        raise RuntimeError("RUNGUARD_RECOVERY_CONCURRENCY must be between 1 and 32.")
    if not 1024 <= settings.max_request_body_bytes <= 10 * 1024 * 1024:
        raise RuntimeError(
            "RUNGUARD_MAX_REQUEST_BODY_BYTES must be between 1024 and 10485760."
        )
    if not (
        1
        <= settings.database_pool_min_size
        <= settings.database_pool_max_size
        <= 100
    ):
        raise RuntimeError("Database pool sizes must satisfy 1 <= min <= max <= 100.")
    if settings.auth_mode == "api_key" and settings.api_keys_json.strip() in {"", "{}"}:
        raise RuntimeError("RUNGUARD_API_KEYS_JSON is required when API key auth is enabled.")
    if settings.auth_mode == "oidc" and not (
        settings.oidc_issuer and settings.oidc_audience and settings.oidc_jwks_url
    ):
        raise RuntimeError("OIDC auth requires issuer, audience, and JWKS URL.")
    if (
        settings.langgraph_checkpoint_backend == "postgres"
        and not settings.database_url
    ):
        raise RuntimeError("PostgreSQL is required for the LangGraph Postgres checkpointer.")
    if settings.langgraph_checkpoint_encryption_key and len(
        settings.langgraph_checkpoint_encryption_key.encode("utf-8")
    ) not in {16, 24, 32}:
        raise RuntimeError("LANGGRAPH_AES_KEY must contain exactly 16, 24, or 32 bytes.")
    if settings.connector_mode == "mcp" and not any(
        (
            settings.mcp_prometheus_url,
            settings.mcp_loki_url,
            settings.mcp_kubernetes_url,
            settings.mcp_github_url,
        )
    ):
        raise RuntimeError("At least one Streamable HTTP MCP server URL is required.")
    if settings.kubernetes_namespace not in settings.kubernetes_allowed_namespaces:
        raise RuntimeError(
            "RUNGUARD_KUBERNETES_NAMESPACE must be included in the allowed namespace list."
        )
    for service, target in inventory.items():
        if target["namespace"] not in settings.kubernetes_allowed_namespaces:
            raise RuntimeError(
                f"Target inventory service {service!r} uses a namespace outside the allowlist."
            )
    if settings.environment.strip().lower() in {"production", "prod"} and not (
        settings.enforce_production_guards
    ):
        raise RuntimeError(
            "RUNGUARD_ENVIRONMENT=production requires RUNGUARD_ENFORCE_PRODUCTION_GUARDS=true."
        )
    if not settings.enforce_production_guards:
        return
    required = {
        "RUNGUARD_DATABASE_URL": settings.database_url,
        "RUNGUARD_REDIS_URL": settings.redis_url,
        "RUNGUARD_PROMETHEUS_WEBHOOK_SECRET": settings.prometheus_webhook_secret,
        "RUNGUARD_OTEL_EXPORTER_OTLP_ENDPOINT": settings.otel_endpoint,
        "LANGGRAPH_AES_KEY": settings.langgraph_checkpoint_encryption_key,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Production guard validation failed; missing: {', '.join(missing)}")
    if settings.auth_mode == "disabled":
        raise RuntimeError("Production guards require API key or OIDC authentication.")
    if settings.cors_origin_regex:
        raise RuntimeError("Production guards require an explicit CORS origin allowlist.")
    if any(not origin.startswith("https://") for origin in settings.cors_origins):
        raise RuntimeError("Production CORS origins must use HTTPS.")
    if not settings.public_base_url:
        raise RuntimeError("Production guards require RUNGUARD_PUBLIC_BASE_URL.")
    public_url = urlsplit(settings.public_base_url)
    if (
        public_url.scheme != "https"
        or not public_url.netloc
        or public_url.username
        or public_url.password
        or public_url.path not in {"", "/"}
        or public_url.query
        or public_url.fragment
    ):
        raise RuntimeError(
            "RUNGUARD_PUBLIC_BASE_URL must be an HTTPS origin without credentials or a path."
        )
    https_endpoints = {
        "RUNGUARD_OIDC_ISSUER": (
            settings.oidc_issuer if settings.auth_mode == "oidc" else None
        ),
        "RUNGUARD_OIDC_JWKS_URL": (
            settings.oidc_jwks_url if settings.auth_mode == "oidc" else None
        ),
        "RUNGUARD_MCP_PROMETHEUS_URL": settings.mcp_prometheus_url,
        "RUNGUARD_MCP_LOKI_URL": settings.mcp_loki_url,
        "RUNGUARD_MCP_KUBERNETES_URL": settings.mcp_kubernetes_url,
        "RUNGUARD_MCP_GITHUB_URL": settings.mcp_github_url,
        "RUNGUARD_A2A_REVIEWER_URL": settings.a2a_reviewer_url,
        "RUNGUARD_LLM_BASE_URL": settings.llm_base_url,
        "RUNGUARD_VERIFICATION_URL_TEMPLATE": settings.verification_url_template,
    }
    for name, value in https_endpoints.items():
        if not value:
            continue
        endpoint = urlsplit(value)
        if (
            endpoint.scheme != "https"
            or not endpoint.netloc
            or endpoint.username
            or endpoint.password
        ):
            raise RuntimeError(
                f"{name} must use HTTPS and must not contain URL credentials "
                "when production guards are enabled."
            )
    if settings.policy_backend != "opa":
        raise RuntimeError("Production guards require the OPA policy backend.")
    if not settings.opa_fail_closed:
        raise RuntimeError("Production guards require RUNGUARD_OPA_FAIL_CLOSED=true.")
    if settings.execution_mode != "kubernetes_job":
        raise RuntimeError("Production guards require restricted Kubernetes Job execution.")
    if not re.search(r"@sha256:[0-9a-f]{64}$", settings.kubernetes_runner_image):
        raise RuntimeError(
            "Production guards require RUNGUARD_KUBERNETES_RUNNER_IMAGE pinned by SHA-256 digest."
        )
    if settings.connector_mode not in {"production", "mcp"}:
        raise RuntimeError("Production guards require production or MCP connectors.")
    if settings.connector_mode == "production":
        connector_values = {
            "RUNGUARD_PROMETHEUS_URL": settings.prometheus_url,
            "RUNGUARD_LOKI_URL": settings.loki_url,
            "RUNGUARD_GITHUB_REPOSITORY": settings.github_repository,
            "RUNGUARD_GITHUB_TOKEN": settings.github_token,
        }
    else:
        connector_values = {
            "RUNGUARD_MCP_PROMETHEUS_URL": settings.mcp_prometheus_url,
            "RUNGUARD_MCP_LOKI_URL": settings.mcp_loki_url,
            "RUNGUARD_MCP_KUBERNETES_URL": settings.mcp_kubernetes_url,
            "RUNGUARD_MCP_GITHUB_URL": settings.mcp_github_url,
        }
    missing_connectors = [
        name for name, value in connector_values.items() if not value
    ]
    if missing_connectors:
        raise RuntimeError(
            "Production connector validation failed; missing: "
            + ", ".join(missing_connectors)
        )
    if settings.agent_backend != "langgraph":
        raise RuntimeError("Production guards require the LangGraph agent backend.")
    if settings.langgraph_checkpoint_backend != "postgres":
        raise RuntimeError("Production guards require the PostgreSQL LangGraph checkpointer.")
    if not settings.auto_recover:
        raise RuntimeError("Production guards require RUNGUARD_AUTO_RECOVER=true.")
    if not inventory:
        raise RuntimeError("Production guards require RUNGUARD_TARGET_INVENTORY_JSON.")
    for service, target in inventory.items():
        if target["namespace"] != settings.kubernetes_namespace:
            raise RuntimeError(
                f"Production target {service!r} must be in the RunGuard release namespace."
            )


def parse_target_inventory(raw: str) -> dict[str, dict[str, str]]:
    try:
        value: Any = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("RUNGUARD_TARGET_INVENTORY_JSON is not valid JSON.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("RUNGUARD_TARGET_INVENTORY_JSON must be a JSON object.")
    inventory: dict[str, dict[str, str]] = {}
    required = {"environment", "namespace", "name"}
    for service, target in value.items():
        if not isinstance(service, str) or not service.strip() or not isinstance(target, dict):
            raise RuntimeError("Every target inventory entry must map a service to an object.")
        service = service.strip()
        if (
            len(service) > 253
            or not re.fullmatch(
                r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
                r"(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*",
                service,
            )
        ):
            raise RuntimeError(f"Target inventory has an invalid service key {service!r}.")
        if service in inventory:
            raise RuntimeError(f"Target inventory contains duplicate service {service!r}.")
        normalized = {
            field: str(target.get(field, "")).strip()
            for field in required
        }
        if not all(normalized.values()):
            raise RuntimeError(
                f"Target inventory service {service!r} requires environment, namespace, and name."
            )
        for field in ("namespace", "name"):
            if len(normalized[field]) > 253 or not re.fullmatch(
                r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
                r"(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*",
                normalized[field],
            ):
                raise RuntimeError(
                    f"Target inventory service {service!r} has an invalid {field}."
                )
        environment = normalized["environment"].lower()
        normalized["environment"] = {
            "prod": "production",
            "stage": "staging",
            "dev": "development",
        }.get(environment, environment)
        inventory[service] = normalized
    return inventory
