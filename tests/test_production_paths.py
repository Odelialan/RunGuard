from __future__ import annotations

from pathlib import Path

import pytest
from runguard_api.config import load_settings
from runguard_api.engine import IncidentEngine
from runguard_api.gateway import (
    MockMCPTransport,
    ProductionMCPTransport,
    ToolContext,
    ToolResult,
)
from runguard_api.models import IncidentCreate
from runguard_api.policy import PolicyEvaluator
from runguard_api.postmortem import PostmortemService
from runguard_api.store import Store


class FailingWriteTransport(MockMCPTransport):
    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolResult:
        if tool_name == "kubernetes.patch_deployment":
            if context.actor == "compensation-controller":
                return ToolResult(
                    ok=True,
                    data={"status": "rollback-applied", "arguments": arguments},
                    source_uri="k8s://test/rollback",
                    duration_ms=20,
                )
            return ToolResult(
                ok=False,
                data={"status": "failed", "error": "injected Job failure"},
                source_uri="k8s://test/job/failure",
                duration_ms=25,
            )
        return await super().call_tool(tool_name, arguments, context)


class CapturingRollbackTransport(MockMCPTransport):
    def __init__(self) -> None:
        self.rollback_arguments: dict[str, object] | None = None

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolResult:
        if tool_name == "kubernetes.patch_deployment":
            if context.actor == "compensation-controller":
                self.rollback_arguments = arguments
                return ToolResult(
                    ok=True,
                    data={"status": "rollback-applied"},
                    source_uri="k8s://test/rollback",
                    duration_ms=10,
                )
            return ToolResult(
                ok=True,
                data={
                    "status": "succeeded",
                    "runner_result": {
                        "ok": True,
                        "before": {"memory_limit": "128Mi", "cpu_limit": None},
                        "after": {"memory_limit": "1Gi", "cpu_limit": None},
                        "resource_version": "42",
                    },
                },
                source_uri="k8s://test/write",
                duration_ms=15,
            )
        return await super().call_tool(tool_name, arguments, context)


class VerificationFailsEngine(IncidentEngine):
    async def _verify(self, incident, run_id):
        return {"passed": False, "duration_ms": 1, "mode": "injected"}


class ProductionVerificationTransport(MockMCPTransport):
    def __init__(self, *, ready_replicas: int) -> None:
        self.ready_replicas = ready_replicas

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolResult:
        if tool_name == "prometheus.query":
            is_error_rate = "query" in arguments
            return ToolResult(
                ok=True,
                data={
                    "value": 0.01 if is_error_rate else 0.4,
                    "series": 1,
                },
                source_uri="prometheus://verification",
                duration_ms=10,
            )
        if tool_name == "kubernetes.get_deployment":
            return ToolResult(
                ok=True,
                data={
                    "name": arguments["name"],
                    "namespace": arguments["namespace"],
                    "replicas": 3,
                    "ready_replicas": self.ready_replicas,
                },
                source_uri="k8s://verification",
                duration_ms=12,
            )
        return await super().call_tool(tool_name, arguments, context)


def settings_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RUNGUARD_DATABASE_PATH", str(tmp_path / "runguard.db"))
    monkeypatch.delenv("RUNGUARD_DATABASE_URL", raising=False)
    monkeypatch.setenv("RUNGUARD_DATABASE_SEED", "false")
    monkeypatch.setenv("RUNGUARD_CONNECTOR_MODE", "mock")
    monkeypatch.setenv("RUNGUARD_EXECUTION_MODE", "simulation")
    monkeypatch.setenv("RUNGUARD_AGENT_BACKEND", "deterministic")
    monkeypatch.setenv("RUNGUARD_POLICY_BACKEND", "python")
    return load_settings()


@pytest.mark.asyncio
async def test_production_verification_requires_metrics_and_ready_workload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(tmp_path, monkeypatch)
    store = Store(settings.database_path, seed=False)
    engine = IncidentEngine(store, settings)
    incident = store.create_incident(
        IncidentCreate(
            title="Verification quorum",
            service="order-api",
            environment="staging",
        )
    )
    engine.settings = type(settings)(**{**settings.__dict__, "connector_mode": "production"})

    engine.transport = ProductionVerificationTransport(ready_replicas=3)
    healthy = await engine._verify(incident, "RUN-VERIFY")
    engine.transport = ProductionVerificationTransport(ready_replicas=2)
    degraded = await engine._verify(incident, "RUN-VERIFY")

    assert healthy["passed"] is True
    assert healthy["metrics_valid"] is True
    assert healthy["workload_stable"] is True
    assert degraded["passed"] is False
    assert degraded["workload_stable"] is False


@pytest.mark.asyncio
async def test_failed_execution_suppresses_unsafe_compensation_and_writes_postmortem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(tmp_path, monkeypatch)
    store = Store(settings.database_path, seed=False)
    engine = IncidentEngine(store, settings)
    engine.transport = FailingWriteTransport()
    incident = store.create_incident(
        IncidentCreate(
            title="Injected deployment failure",
            severity="P2",
            service="order-api",
            environment="staging",
            description="Exercise the compensation path.",
        )
    )

    result = await engine.start(incident["id"])

    assert result["status"] == "HUMAN_HANDOFF"
    intent = result["tool_intents"][0]
    assert intent["status"] == "EXECUTION_FAILED"
    postmortem = store.get_postmortem(incident["id"])
    assert postmortem["incident_id"] == incident["id"]
    assert "Injected deployment failure" in postmortem["title"]


@pytest.mark.asyncio
async def test_verification_failure_rolls_back_to_captured_before_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(tmp_path, monkeypatch)
    store = Store(settings.database_path, seed=False)
    engine = VerificationFailsEngine(store, settings)
    transport = CapturingRollbackTransport()
    engine.transport = transport
    incident = store.create_incident(
        IncidentCreate(
            title="Captured rollback snapshot",
            service="order-api",
            environment="staging",
        )
    )

    result = await engine.start(incident["id"])

    assert result["status"] == "ROLLED_BACK"
    assert transport.rollback_arguments is not None
    assert transport.rollback_arguments["memory_limit"] == "128Mi"
    assert transport.rollback_arguments["cpu_limit"] is None
    assert transport.rollback_arguments["expected_resource_version"] == "42"


@pytest.mark.asyncio
async def test_retry_after_failed_execution_uses_a_new_intent_and_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(tmp_path, monkeypatch)
    store = Store(settings.database_path, seed=False)
    engine = IncidentEngine(store, settings)
    engine.transport = FailingWriteTransport()
    incident = store.create_incident(
        IncidentCreate(
            title="Safe retry generation",
            service="order-api",
            environment="staging",
        )
    )

    first = await engine.start(incident["id"])
    first_intent = first["tool_intents"][0]
    engine.transport = MockMCPTransport()
    second = await engine.start(incident["id"])

    assert second["status"] == "RESOLVED"
    assert len(second["tool_intents"]) == 2
    latest = second["tool_intents"][0]
    assert latest["id"] != first_intent["id"]
    assert latest["run_id"] == second["current_run_id"]


@pytest.mark.asyncio
async def test_opa_backend_fails_closed_without_endpoint() -> None:
    evaluator = PolicyEvaluator("opa", None, fail_closed=True)

    result = await evaluator.evaluate(
        {
            "environment": "production",
            "tool": "kubernetes.patch_deployment",
            "risk_level": "R2",
            "has_rollback": True,
            "rollback": {"memory_limit": "256Mi"},
        }
    )

    assert result["decision"] == "deny"
    assert result["matched_policy"] == "opa-unavailable-fail-closed"


@pytest.mark.asyncio
async def test_unknown_tool_is_denied_by_python_policy() -> None:
    evaluator = PolicyEvaluator("python", None)

    result = await evaluator.evaluate(
        {
            "environment": "production",
            "tool": "prometheus.delete_series",
            "has_rollback": True,
        }
    )

    assert result["decision"] == "deny"
    assert result["risk_level"] == "R3"


@pytest.mark.asyncio
async def test_python_policy_recomputes_staging_write_risk() -> None:
    evaluator = PolicyEvaluator("python", None)

    result = await evaluator.evaluate(
        {
            "environment": "staging",
            "tool": "kubernetes.patch_deployment",
            "risk_level": "R2",
            "has_rollback": True,
            "rollback": {"memory_limit": "256Mi"},
        }
    )

    assert result["decision"] == "allow"
    assert result["risk_level"] == "R1"


@pytest.mark.asyncio
async def test_production_connector_cannot_write_in_simulation_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(tmp_path, monkeypatch)
    transport = ProductionMCPTransport(settings)

    result = await transport.call_tool(
        "kubernetes.patch_deployment",
        {"namespace": "staging", "name": "order-api", "memory_limit": "1Gi"},
        ToolContext(
            incident_id="INC-TEST",
            run_id="RUN-TEST",
            actor="remediation-agent",
            idempotency_key="inc-test-action",
        ),
    )

    assert result.ok is True
    assert result.data["mode"] == "simulation"


def test_postmortem_markdown_contains_required_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(tmp_path, monkeypatch)
    store = Store(settings.database_path, seed=False)
    incident = store.create_incident(
        IncidentCreate(
            title="Documented incident",
            service="checkout",
            description="Checkout requests were delayed.",
        )
    )
    service = PostmortemService(store)
    document = service.generate(incident["id"])

    markdown = service.markdown(document)

    assert "## Executive summary" in markdown
    assert "## Root cause" in markdown
    assert "## Timeline" in markdown
    assert "## Action items" in markdown
