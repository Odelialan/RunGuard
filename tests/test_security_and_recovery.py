from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from runguard_api.a2a import reviewer_agent_card
from runguard_api.config import load_settings, validate_settings
from runguard_api.engine import IncidentEngine
from runguard_api.event_stream import EventStream, LockLeaseLost
from runguard_api.gateway import (
    ProductionMCPTransport,
    StreamableHTTPMCPTransport,
    ToolContext,
)
from runguard_api.kubernetes_executor import KubernetesJobExecutor, KubernetesJobSpec
from runguard_api.models import IncidentCreate, IncidentStatus
from runguard_api.outbox import OutboxDispatcher
from runguard_api.policy import evaluate_policy
from runguard_api.runner import (
    LAST_BEFORE_ANNOTATION,
    LAST_EXECUTION_ANNOTATION,
    _patch_deployment,
    _scale_deployment,
)
from runguard_api.security import (
    RequestBodyLimitMiddleware,
    SecurityManager,
    SecurityMiddleware,
    verify_webhook_signature,
)
from runguard_api.store import Store


def test_a2a_agent_card_declares_required_bearer_auth() -> None:
    card = reviewer_agent_card("https://runguard.example.com")

    assert card["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    assert card["security"] == [{"bearerAuth": []}]


def test_incident_identity_is_trimmed_before_length_validation() -> None:
    with pytest.raises(ValidationError):
        IncidentCreate(title="   ", service="order-api")


def test_request_body_limit_rejects_content_length_and_streaming_bodies() -> None:
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=32)

    @app.post("/upload")
    async def upload(request: Request):
        return {"size": len(await request.body())}

    client = TestClient(app)
    oversized = b"x" * 33

    assert client.post("/upload", content=oversized).status_code == 413
    streamed = client.post(
        "/upload",
        content=(chunk for chunk in (b"x" * 20, b"y" * 20)),
    )
    assert streamed.status_code == 413


def test_incident_listing_is_bounded_and_paginated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = local_settings(tmp_path, monkeypatch)
    store = Store(settings.database_path, seed=False)
    for index in range(3):
        store.create_incident(
            IncidentCreate(title=f"Incident {index}", service="order-api")
        )

    first = store.list_incidents(limit=1, offset=0)
    second = store.list_incidents(limit=1, offset=1)

    assert len(first) == len(second) == 1
    assert first[0]["id"] != second[0]["id"]


def test_policy_recomputes_risk_instead_of_trusting_caller() -> None:
    decision = evaluate_policy(
        {
            "environment": "production",
            "tool": "kubernetes.patch_deployment",
            "arguments": {},
            "risk_level": "R0",
            "has_rollback": True,
            "rollback": {"memory_limit": "256Mi"},
        }
    )

    assert decision["decision"] == "require_approval"
    assert decision["risk_level"] == "R2"


def test_patch_runner_uses_resource_version_precondition() -> None:
    container = SimpleNamespace(
        name="api",
        resources=SimpleNamespace(limits={"memory": "128Mi"}),
    )
    deployment = SimpleNamespace(
        spec=SimpleNamespace(
            template=SimpleNamespace(
                metadata=SimpleNamespace(annotations={}),
                spec=SimpleNamespace(containers=[container]),
            )
        )
    )

    class FakeApi:
        patch: dict[str, object] | None = None

        def read_namespaced_deployment(self, name, namespace):
            return deployment

        def patch_namespaced_deployment(self, name, namespace, patch):
            self.patch = patch
            return SimpleNamespace(metadata=SimpleNamespace(resource_version="43"))

    api = FakeApi()
    result = _patch_deployment(
        api,
        {
            "namespace": "runguard-system",
            "name": "order-api",
            "memory_limit": "1Gi",
            "expected_resource_version": "42",
            "idempotency_key": "rollback-1",
        },
    )

    assert api.patch is not None
    assert api.patch["metadata"] == {"resourceVersion": "42"}
    assert result["before"]["container"] == "api"
    annotations = api.patch["spec"]["template"]["metadata"]["annotations"]
    assert annotations[LAST_EXECUTION_ANNOTATION] == "rollback-1"
    assert '"memory_limit":"128Mi"' in annotations[LAST_BEFORE_ANNOTATION]


def test_scale_runner_is_atomic_and_replays_original_before_snapshot() -> None:
    deployment = SimpleNamespace(
        metadata=SimpleNamespace(resource_version="42", annotations={}),
        spec=SimpleNamespace(replicas=3),
    )

    class FakeApi:
        patch: dict[str, object] | None = None

        def read_namespaced_deployment(self, name, namespace):
            return deployment

        def patch_namespaced_deployment(self, name, namespace, patch):
            self.patch = patch
            deployment.metadata.resource_version = "43"
            deployment.metadata.annotations = patch["metadata"]["annotations"]
            deployment.spec.replicas = patch["spec"]["replicas"]
            return deployment

    api = FakeApi()
    arguments = {
        "namespace": "runguard-system",
        "name": "order-api",
        "replicas": 5,
        "expected_resource_version": "42",
        "idempotency_key": "scale-1",
    }
    first = _scale_deployment(api, arguments)
    replay = _scale_deployment(api, arguments)

    assert api.patch is not None
    assert api.patch["metadata"]["resourceVersion"] == "42"
    assert first["before"] == {"replicas": 3}
    assert replay["before"] == {"replicas": 3}
    assert replay["after"] == {"replicas": 5}
    assert replay["idempotent_replay"] is True


def test_outbox_claim_is_exclusive_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = local_settings(tmp_path, monkeypatch)
    store = Store(settings.database_path, seed=False, outbox_enabled=True)
    incident = store.create_incident(
        IncidentCreate(title="Durable event", service="order-api")
    )
    store.update_status(
        incident["id"],
        IncidentStatus.INVESTIGATING,
        "test-worker",
    )

    first = store.claim_outbox("worker-1")
    assert len(first) == 1
    assert first[0]["event_type"] == "incident.created"
    assert first[0]["incident_id"] == incident["id"]
    assert first[0]["payload"]["severity"] == "P2"
    assert store.claim_outbox("worker-2") == []

    assert store.release_outbox_claim(first[0]["id"], "worker-1", "Redis unavailable")
    retried = store.claim_outbox("worker-2")
    assert len(retried) == 1
    assert retried[0]["attempts"] == 2
    assert store.mark_outbox_published(retried[0]["id"], "worker-2")
    next_event = store.claim_outbox("worker-1")
    assert len(next_event) == 1
    assert next_event[0]["event_type"] == "incident.status_changed"
    assert store.mark_outbox_published(next_event[0]["id"], "worker-1")
    assert store.outbox_pending_count() == 0
    with store.connect() as connection:
        connection.execute(
            "UPDATE event_outbox SET published_at = ? WHERE published_at IS NOT NULL",
            ("2020-01-01T00:00:00+00:00",),
        )
    assert store.prune_published_outbox(retention_days=1) == 2


@pytest.mark.asyncio
async def test_outbox_dispatcher_retries_failed_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = local_settings(tmp_path, monkeypatch)
    store = Store(settings.database_path, seed=False, outbox_enabled=True)
    store.create_incident(IncidentCreate(title="Retry event", service="order-api"))

    class FakeStream:
        enabled = True
        ready = True

        def __init__(self) -> None:
            self.calls: list[str] = []

        async def publish(self, event_type, incident_id, payload, *, event_id=None):
            self.calls.append(event_id)
            if len(self.calls) == 1:
                raise RuntimeError("injected Redis failure")
            return "1-0"

    stream = FakeStream()
    dispatcher = OutboxDispatcher(store, stream)  # type: ignore[arg-type]

    assert await dispatcher.flush_once() == 0
    assert store.outbox_pending_count() == 1
    assert await dispatcher.flush_once() == 1
    assert store.outbox_pending_count() == 0
    assert stream.calls[0] == stream.calls[1]


@pytest.mark.asyncio
async def test_distributed_lock_cancels_work_after_lease_loss() -> None:
    class FakeRedis:
        async def set(self, *args, **kwargs):
            return True

        async def eval(self, script, *args):
            return 0 if "EXPIRE" in script else 1

    stream = EventStream("redis://example.invalid/0", "events")
    stream._client = FakeRedis()
    cancelled = asyncio.Event()

    async def long_operation() -> None:
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    with pytest.raises(LockLeaseLost):
        await stream.run_with_lock("incident:test", 0.2, long_operation)  # type: ignore[arg-type]

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_engine_database_calls_do_not_block_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = local_settings(tmp_path, monkeypatch)
    store = Store(settings.database_path, seed=False)
    engine = IncidentEngine(store, settings)

    def slow_database_call() -> str:
        time.sleep(0.3)
        return "done"

    started = asyncio.get_running_loop().time()
    database_task = asyncio.create_task(engine._db(slow_database_call))
    await asyncio.sleep(0.02)
    heartbeat_elapsed = asyncio.get_running_loop().time() - started

    assert heartbeat_elapsed < 0.15
    assert not database_task.done()
    assert await database_task == "done"


def local_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RUNGUARD_DATABASE_PATH", str(tmp_path / "runguard.db"))
    monkeypatch.delenv("RUNGUARD_DATABASE_URL", raising=False)
    monkeypatch.setenv("RUNGUARD_DATABASE_SEED", "false")
    monkeypatch.setenv("RUNGUARD_CONNECTOR_MODE", "mock")
    monkeypatch.setenv("RUNGUARD_EXECUTION_MODE", "simulation")
    monkeypatch.setenv("RUNGUARD_AGENT_BACKEND", "deterministic")
    monkeypatch.setenv("RUNGUARD_POLICY_BACKEND", "python")
    monkeypatch.setenv("RUNGUARD_LANGGRAPH_CHECKPOINT_BACKEND", "memory")
    monkeypatch.setenv("RUNGUARD_AUTH_MODE", "disabled")
    return load_settings()


def test_api_key_rbac_and_rate_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(
        local_settings(tmp_path, monkeypatch),
        auth_mode="api_key",
        api_keys_json=(
            '{"viewer-key":{"subject":"viewer","roles":["viewer"]},'
            '"approver-key":{"subject":"sre","roles":["approver"]}}'
        ),
        rate_limit_per_minute=10,
    )
    manager = SecurityManager(settings)
    app = FastAPI()
    app.add_middleware(SecurityMiddleware, manager=manager)

    @app.get("/api/incidents")
    def read_incidents():
        return []

    @app.post("/api/tool-intents/INT-1/approve")
    def approve():
        return {"status": "approved"}

    client = TestClient(app)
    assert client.get("/api/incidents").status_code == 401
    viewer = {"Authorization": "Bearer viewer-key"}
    assert client.get("/api/incidents", headers=viewer).status_code == 200
    assert client.post("/api/tool-intents/INT-1/approve", headers=viewer).status_code == 403
    approver = {"Authorization": "Bearer approver-key"}
    response = client.post("/api/tool-intents/INT-1/approve", headers=approver)
    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit"] == "10"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_prometheus_webhook_hmac() -> None:
    body = b'{"status":"firing"}'
    timestamp = str(int(time.time()))
    signed = f"{timestamp}.".encode() + body
    signature = "sha256=" + hmac.new(b"secret", signed, hashlib.sha256).hexdigest()

    verify_webhook_signature("secret", body, signature, timestamp)
    with pytest.raises(PermissionError):
        verify_webhook_signature("secret", body, "sha256=invalid", timestamp)
    with pytest.raises(PermissionError, match="outside"):
        verify_webhook_signature("secret", body, signature, "1")


def test_invalid_webhook_signatures_do_not_exhaust_the_valid_alert_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(
        local_settings(tmp_path, monkeypatch),
        prometheus_webhook_secret="secret",
        rate_limit_per_minute=1,
    )
    manager = SecurityManager(settings)
    app = FastAPI()
    app.add_middleware(SecurityMiddleware, manager=manager)

    @app.post("/api/alerts/prometheus")
    async def webhook(request: Request):
        return {"body": (await request.body()).decode()}

    client = TestClient(app)
    body = b'{"status":"firing"}'
    timestamp = str(int(time.time()))
    signed = f"{timestamp}.".encode() + body
    signature = "sha256=" + hmac.new(b"secret", signed, hashlib.sha256).hexdigest()

    assert client.post(
        "/api/alerts/prometheus",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-RunGuard-Timestamp": timestamp,
            "X-RunGuard-Signature": "sha256=invalid",
        },
    ).status_code == 401
    valid = client.post(
        "/api/alerts/prometheus",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-RunGuard-Timestamp": timestamp,
            "X-RunGuard-Signature": signature,
        },
    )
    assert valid.status_code == 200
    assert valid.headers["x-ratelimit-remaining"] == "0"


def test_ingress_idempotency_deduplicates_replayed_alerts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = local_settings(tmp_path, monkeypatch)
    store = Store(settings.database_path, seed=False)
    payload = IncidentCreate(
        title="Repeated alert",
        service="order-api",
        environment="staging",
    )

    first = store.create_incident(
        payload,
        source="prometheus",
        idempotency_key="prometheus:same-alert",
    )
    second = store.create_incident(
        payload,
        source="prometheus",
        idempotency_key="prometheus:same-alert",
    )

    assert first["id"] == second["id"]
    assert second["_deduplicated"] is True
    assert len(store.list_incidents()) == 1


@pytest.mark.asyncio
async def test_workflow_checkpoints_and_resume_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = local_settings(tmp_path, monkeypatch)
    store = Store(settings.database_path, seed=False)
    engine = IncidentEngine(store, settings)
    incident = store.create_incident(
        IncidentCreate(
            title="Checkpoint recovery",
            severity="P2",
            service="order-api",
            environment="staging",
        )
    )

    resolved = await engine.start(incident["id"])
    run_id = resolved["current_run_id"]
    checkpoints = store.list_workflow_checkpoints(run_id)
    assert checkpoints[-1]["phase"] == "RESOLVED"
    assert any(item["phase"] == "EXECUTION_RECORDED" for item in checkpoints)

    intent = resolved["tool_intents"][0]
    store.update_status(incident["id"], IncidentStatus.VERIFYING, "test-restart")
    with store.connect() as connection:
        connection.execute(
            "UPDATE tool_intents SET status = 'EXECUTED' WHERE id = ?",
            (intent["id"],),
        )
    resumed = await engine.resume(incident["id"])
    assert resumed["status"] == "RESOLVED"


@pytest.mark.asyncio
async def test_remote_mcp_writes_stay_inside_execution_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(
        local_settings(tmp_path, monkeypatch),
        connector_mode="mcp",
        mcp_kubernetes_url="https://mcp.invalid.example/mcp",
    )
    transport = StreamableHTTPMCPTransport(settings)
    result = await transport.call_tool(
        "kubernetes.patch_deployment",
        {"namespace": "staging", "name": "order-api", "memory_limit": "1Gi"},
        ToolContext(
            incident_id="INC-TEST",
            run_id="RUN-TEST",
            actor="remediation-agent",
            idempotency_key="inc-test-write",
        ),
    )
    assert result.ok is True
    assert result.data["mode"] == "simulation"


@pytest.mark.asyncio
async def test_remote_mcp_rejects_prefix_matched_unknown_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(
        local_settings(tmp_path, monkeypatch),
        connector_mode="mcp",
        mcp_prometheus_url="https://mcp.invalid.example/mcp",
    )
    transport = StreamableHTTPMCPTransport(settings)

    result = await transport.call_tool(
        "prometheus.delete_series",
        {"match": "{service='order-api'}"},
        ToolContext("INC-TEST", "RUN-TEST", "remediation-agent", "unknown-tool"),
    )

    assert result.ok is False
    assert "exact MCP allowlist" in result.data["error"]


@pytest.mark.asyncio
async def test_simulation_rollback_never_calls_kubernetes_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = local_settings(tmp_path, monkeypatch)
    transport = ProductionMCPTransport(settings)

    class ProbeExecutor:
        calls = 0

        async def execute(self, spec, context):  # pragma: no cover - must remain unreachable
            self.calls += 1
            raise AssertionError("simulation reached the real executor")

    probe = ProbeExecutor()
    transport.executor = probe
    result = await transport.rollback(
        "kubernetes.patch_deployment",
        {"namespace": "runguard-system", "name": "order-api", "memory_limit": "256Mi"},
        ToolContext("INC-TEST", "RUN-TEST", "compensation-controller", "rollback"),
    )

    assert result.ok is True
    assert result.data["mode"] == "simulation"
    assert probe.calls == 0


@pytest.mark.asyncio
async def test_executor_requires_structured_runner_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = local_settings(tmp_path, monkeypatch)
    executor = KubernetesJobExecutor(settings)
    monkeypatch.setattr(
        executor,
        "_execute_sync",
        lambda spec, context: {"status": "succeeded", "runner_result": {}},
    )

    result = await executor.execute(
        KubernetesJobSpec(
            tool_name="kubernetes.patch_deployment",
            arguments={"namespace": "runguard-system", "name": "order-api"},
        ),
        ToolContext("INC-TEST", "RUN-TEST", "remediation-agent", "executor-result"),
    )

    assert result.ok is False
    assert "valid successful runner result" in result.data["error"]


def test_untrusted_environment_cannot_select_another_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = local_settings(tmp_path, monkeypatch)
    store = Store(settings.database_path, seed=False)
    engine = IncidentEngine(store, settings)

    assert engine._target_namespace("kube-system") == "runguard-system"


@pytest.mark.asyncio
async def test_authoritative_inventory_rejects_environment_spoofing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(
        local_settings(tmp_path, monkeypatch),
        target_inventory_json=(
            '{"payments":{"environment":"production",'
            '"namespace":"runguard-system","name":"payments"}}'
        ),
    )
    store = Store(settings.database_path, seed=False)
    engine = IncidentEngine(store, settings)
    incident = store.create_incident(
        IncidentCreate(
            title="Spoofed staging target",
            service="payments",
            environment="staging",
        )
    )

    with pytest.raises(ValueError, match="authoritative target inventory"):
        await engine.start(incident["id"])


@pytest.mark.asyncio
async def test_agent_cannot_override_authoritative_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = local_settings(tmp_path, monkeypatch)
    store = Store(settings.database_path, seed=False)
    engine = IncidentEngine(store, settings)

    class AdversarialOrchestrator:
        graph = object()

        async def run(self, incident, evidence, run_id):
            return {
                "investigation": {
                    "root_cause": "Injected plan",
                    "confidence": 0.9,
                    "evidence_ids": [],
                },
                "remediation": {
                    "tool_name": "kubernetes.patch_deployment",
                    "arguments": {
                        "name": "privileged-target",
                        "namespace": "runguard-system",
                        "memory_limit": "9Gi",
                    },
                    "rollback": {"memory_limit": "256Mi"},
                },
                "review": {"decision": "approve", "reason": "Injected approval"},
            }

        async def checkpoint(self, run_id):
            return None

        async def generate_report(self, incident):
            return {}

    engine.orchestrator = AdversarialOrchestrator()
    incident = store.create_incident(
        IncidentCreate(
            title="Target override attempt",
            service="safe-service",
            environment="staging",
        )
    )

    result = await engine.start(incident["id"])

    assert result["status"] == "HUMAN_HANDOFF"
    assert result["tool_intents"] == []
    assert "retarget" in result["events"][-1]["payload"]["reason"]


def test_production_guards_reject_insecure_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(
        local_settings(tmp_path, monkeypatch),
        enforce_production_guards=True,
    )
    with pytest.raises(RuntimeError, match="missing"):
        validate_settings(settings)


def test_production_environment_cannot_disable_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(
        local_settings(tmp_path, monkeypatch),
        environment="production",
        enforce_production_guards=False,
    )

    with pytest.raises(RuntimeError, match="requires RUNGUARD_ENFORCE_PRODUCTION_GUARDS"):
        validate_settings(settings)


def test_complete_production_guard_configuration_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(
        local_settings(tmp_path, monkeypatch),
        enforce_production_guards=True,
        database_url="postgresql://runguard:secret@postgres/runguard",
        redis_url="redis://redis:6379/0",
        auth_mode="api_key",
        api_keys_json='{"sha256:abc":{"subject":"sre","roles":["admin"]}}',
        cors_origins=("https://runguard.example.com",),
        cors_origin_regex="",
        public_base_url="https://runguard.example.com",
        policy_backend="opa",
        opa_url="http://opa:8181",
        connector_mode="production",
        prometheus_url="http://prometheus:9090",
        loki_url="http://loki:3100",
        github_repository="Odelialan/RunGuard",
        github_token="injected-secret",
        agent_backend="langgraph",
        execution_mode="kubernetes_job",
        kubernetes_runner_image=(
            "ghcr.io/odelialan/runguard-runner@sha256:"
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        ),
        prometheus_webhook_secret="webhook-secret",
        otel_endpoint="http://otel-collector:4318",
        langgraph_checkpoint_backend="postgres",
        langgraph_checkpoint_encryption_key="0123456789abcdef",
        auto_recover=True,
        target_inventory_json=(
            '{"order-api":{"environment":"production",'
            '"namespace":"runguard-system","name":"order-api"}}'
        ),
    )

    validate_settings(settings)


@pytest.mark.asyncio
async def test_edited_intent_is_reclassified_and_target_is_immutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = local_settings(tmp_path, monkeypatch)
    store = Store(settings.database_path, seed=False)
    engine = IncidentEngine(store, settings)
    incident = store.create_incident(
        IncidentCreate(
            title="Protected approval edit",
            severity="P1",
            service="payment-api",
            environment="production",
        )
    )
    waiting = await engine.start(incident["id"])
    intent = waiting["tool_intents"][0]

    edited = await engine.edit_intent(
        intent["id"],
        {"memory_limit": "2Gi"},
        "sre-approver",
        "Increase the bounded memory limit.",
    )
    assert edited["status"] == "WAITING_APPROVAL"
    assert edited["arguments"]["memory_limit"] == "2Gi"
    assert edited["matched_rule"] == "edited-intent-requires-fresh-approval"

    with pytest.raises(ValueError, match="cannot be edited"):
        await engine.edit_intent(
            intent["id"],
            {"namespace": "kube-system"},
            "sre-approver",
            "Attempt to retarget.",
        )


@pytest.mark.asyncio
async def test_approved_intent_is_recovered_after_process_crash_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = local_settings(tmp_path, monkeypatch)
    store = Store(settings.database_path, seed=False)
    engine = IncidentEngine(store, settings)
    incident = store.create_incident(
        IncidentCreate(
            title="Approval crash recovery",
            severity="P1",
            service="payment-api",
            environment="production",
        )
    )
    waiting = await engine.start(incident["id"])
    intent = waiting["tool_intents"][0]
    store.decide_approval(intent["id"], "sre-approver", "approved", "approved")

    assert incident["id"] in store.list_recoverable_incidents()
    resumed = await engine.resume(incident["id"])

    assert resumed["status"] == "RESOLVED"
    assert resumed["tool_intents"][0]["status"] == "EXECUTED"
