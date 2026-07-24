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
async def test_failed_execution_runs_compensation_and_postmortem(
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

    assert result["status"] == "ROLLED_BACK"
    intent = result["tool_intents"][0]
    assert intent["status"] == "ROLLED_BACK"
    postmortem = store.get_postmortem(incident["id"])
    assert postmortem["incident_id"] == incident["id"]
    assert "Injected deployment failure" in postmortem["title"]


@pytest.mark.asyncio
async def test_opa_backend_fails_closed_without_endpoint() -> None:
    evaluator = PolicyEvaluator("opa", None, fail_closed=True)

    result = await evaluator.evaluate(
        {
            "environment": "production",
            "tool": "kubernetes.patch_deployment",
            "risk_level": "R2",
            "has_rollback": True,
        }
    )

    assert result["decision"] == "deny"
    assert result["matched_policy"] == "opa-unavailable-fail-closed"


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
