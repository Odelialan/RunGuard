from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from runguard_api.config import load_settings, validate_settings
from runguard_api.engine import IncidentEngine
from runguard_api.gateway import StreamableHTTPMCPTransport, ToolContext
from runguard_api.models import IncidentCreate, IncidentStatus
from runguard_api.security import (
    SecurityManager,
    SecurityMiddleware,
    verify_webhook_signature,
)
from runguard_api.store import Store


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


def test_untrusted_environment_cannot_select_another_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = local_settings(tmp_path, monkeypatch)
    store = Store(settings.database_path, seed=False)
    engine = IncidentEngine(store, settings)

    assert engine._target_namespace("kube-system") == "runguard-system"


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
        policy_backend="opa",
        opa_url="http://opa:8181",
        connector_mode="production",
        prometheus_url="http://prometheus:9090",
        loki_url="http://loki:3100",
        github_repository="Odelialan/RunGuard",
        github_token="injected-secret",
        agent_backend="langgraph",
        execution_mode="kubernetes_job",
        prometheus_webhook_secret="webhook-secret",
        otel_endpoint="http://otel-collector:4318",
        langgraph_checkpoint_backend="postgres",
        langgraph_checkpoint_encryption_key="0123456789abcdef",
        auto_recover=True,
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
