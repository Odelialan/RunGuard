from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .a2a import reviewer_agent_card
from .config import load_settings
from .engine import IncidentEngine
from .evaluation import run_baseline
from .event_stream import EventStream
from .models import (
    A2ARequest,
    ApprovalRequest,
    EvalRunRequest,
    EvidenceSearchRequest,
    IncidentCreate,
    IncidentStatus,
    PolicySimulationRequest,
    PrometheusAlert,
    ToolIntentEdit,
)
from .postmortem import PostmortemService
from .store import Store
from .telemetry import Telemetry

settings = load_settings()
telemetry = Telemetry(settings.otel_endpoint, settings.otel_service_name)
telemetry.configure()
event_stream = EventStream(settings.redis_url, settings.redis_stream)
store = Store(
    settings.database_path,
    settings.database_url,
    seed=settings.database_seed,
    vector_dimensions=settings.vector_dimensions,
    telemetry=telemetry,
)
engine = IncidentEngine(store, settings, event_stream)
postmortems = PostmortemService(store)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await event_stream.connect()
    try:
        yield
    finally:
        await event_stream.close()


app = FastAPI(
    title="RunGuard API",
    description="Trusted Agentic SRE incident response API",
    version="1.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": "1.1.0",
        "execution_mode": settings.execution_mode,
        "connector_mode": settings.connector_mode,
        "agent_backend": settings.agent_backend,
        "policy_backend": settings.policy_backend,
        "database": store.database_health(),
        "database_backend": store.backend,
        "redis_stream": "configured" if event_stream.enabled else "disabled",
        "opentelemetry": "configured" if telemetry.endpoint else "disabled",
        "frontend": "ready" if web_index.is_file() else "not-built",
    }


@app.get("/api/ready")
async def readiness() -> dict[str, Any]:
    components = {
        "database": store.database_health(),
        "redis": await event_stream.health(),
        "policy": await engine.policy.health(),
    }
    unavailable = [name for name, status in components.items() if status == "unavailable"]
    if unavailable:
        raise HTTPException(
            status_code=503,
            detail={"status": "not-ready", "components": components},
        )
    return {"status": "ready", "components": components}


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    return store.overview()


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics() -> PlainTextResponse:
    data = store.overview()
    lines = [
        "# HELP runguard_incidents_total Total incidents in the store.",
        "# TYPE runguard_incidents_total gauge",
        f"runguard_incidents_total {data['incidents']}",
        "# HELP runguard_incidents_active Active incidents.",
        "# TYPE runguard_incidents_active gauge",
        f"runguard_incidents_active {data['active']}",
        "# HELP runguard_approvals_pending Tool intents waiting for approval.",
        "# TYPE runguard_approvals_pending gauge",
        f"runguard_approvals_pending {data['approvals']}",
        "# HELP runguard_trace_spans_total Recorded RunGuard spans.",
        "# TYPE runguard_trace_spans_total gauge",
        f"runguard_trace_spans_total {data['spans']}",
    ]
    for status, count in data["by_status"].items():
        lines.append(f'runguard_incidents_by_status{{status="{status}"}} {count}')
    return PlainTextResponse(
        "\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4",
    )


@app.post("/api/alerts/prometheus", status_code=201)
async def prometheus_alert(payload: PrometheusAlert) -> dict[str, Any]:
    labels = payload.labels
    annotations = payload.annotations
    incident = IncidentCreate(
        title=annotations.get("summary") or labels.get("alertname") or "Prometheus alert",
        severity=labels.get("severity", "P2").upper(),
        service=labels.get("service", "unknown-service"),
        environment=labels.get("environment", "staging"),
        description=annotations.get("description", ""),
    )
    created = store.create_incident(incident, source="prometheus")
    await event_stream.publish(
        "incident.created",
        created["id"],
        {"source": "prometheus", "severity": created["severity"]},
    )
    return created


@app.post("/api/incidents", status_code=201)
async def create_incident(payload: IncidentCreate) -> dict[str, Any]:
    created = store.create_incident(payload)
    await event_stream.publish(
        "incident.created",
        created["id"],
        {"source": "manual", "severity": created["severity"]},
    )
    return created


@app.get("/api/events/stream")
async def recent_stream_events(limit: int = Query(default=100, ge=1, le=1000)):
    return await event_stream.recent(limit)


@app.post("/api/evidence/search")
async def search_evidence(payload: EvidenceSearchRequest):
    if not engine.evidence_index.enabled:
        raise HTTPException(
            status_code=503,
            detail="Semantic evidence search requires PostgreSQL/pgvector and embeddings.",
        )
    return await engine.evidence_index.search(payload.query, payload.limit)


@app.get("/api/incidents")
def list_incidents() -> list[dict[str, Any]]:
    return store.list_incidents()


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str) -> dict[str, Any]:
    return _not_found(store.get_incident, incident_id)


@app.post("/api/incidents/{incident_id}/start")
async def start_incident(incident_id: str) -> dict[str, Any]:
    try:
        return await engine.start(incident_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Incident not found.") from None


@app.post("/api/incidents/{incident_id}/cancel")
def cancel_incident(incident_id: str) -> dict[str, Any]:
    return _not_found(
        store.update_status,
        incident_id,
        IncidentStatus.CANCELLED,
        "human-operator",
    )


@app.post("/api/incidents/{incident_id}/handoff")
def handoff_incident(incident_id: str) -> dict[str, Any]:
    return _not_found(
        store.update_status,
        incident_id,
        IncidentStatus.HUMAN_HANDOFF,
        "human-operator",
    )


@app.post("/api/incidents/{incident_id}/replay")
async def replay_incident(incident_id: str) -> dict[str, Any]:
    try:
        return await engine.replay(incident_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Incident not found.") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return _not_found(store.get_run, run_id)


@app.get("/api/runs/{run_id}/events")
@app.get("/api/runs/{run_id}/trace")
def get_trace(run_id: str) -> list[dict[str, Any]]:
    return store.list_traces(run_id)


@app.get("/api/traces")
def list_traces(run_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
    return store.list_traces(run_id)


@app.get("/api/approvals")
def list_approvals() -> list[dict[str, Any]]:
    return store.list_approvals()


@app.post("/api/tool-intents/{intent_id}/approve")
async def approve_intent(intent_id: str, payload: ApprovalRequest) -> dict[str, Any]:
    try:
        store.decide_approval(
            intent_id,
            payload.reviewer,
            "approved",
            payload.comment,
        )
        return await engine.execute(intent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Tool intent not found.") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/tool-intents/{intent_id}/reject")
def reject_intent(intent_id: str, payload: ApprovalRequest) -> dict[str, Any]:
    try:
        intent = store.decide_approval(
            intent_id,
            payload.reviewer,
            "rejected",
            payload.comment,
        )
        store.update_status(
            intent["incident_id"],
            IncidentStatus.HUMAN_HANDOFF,
            payload.reviewer,
            {"reason": "remediation rejected"},
        )
        return intent
    except KeyError:
        raise HTTPException(status_code=404, detail="Tool intent not found.") from None


@app.post("/api/tool-intents/{intent_id}/edit")
def edit_intent(intent_id: str, payload: ToolIntentEdit) -> dict[str, Any]:
    return _not_found(
        store.edit_intent,
        intent_id,
        payload.arguments,
        payload.reviewer,
        payload.comment,
    )


@app.post("/api/policies/simulate")
async def simulate_policy(payload: PolicySimulationRequest) -> dict[str, Any]:
    return await engine.policy.evaluate(payload)


@app.get("/api/policies/versions")
def policy_versions() -> list[dict[str, Any]]:
    return [
        {
            "version": settings.policy_version,
            "status": "active",
            "rules": 5,
            "published_at": "2026-07-24",
        }
    ]


@app.post("/api/evals/run", status_code=201)
def start_evaluation(payload: EvalRunRequest) -> dict[str, Any]:
    return run_baseline(store, payload)


@app.get("/api/evals")
def list_evaluations() -> list[dict[str, Any]]:
    return store.list_eval_runs()


@app.get("/api/evals/{eval_id}")
@app.get("/api/evals/{eval_id}/report")
def get_evaluation(eval_id: str) -> dict[str, Any]:
    return _not_found(store.get_eval_run, eval_id)


@app.post("/api/incidents/{incident_id}/postmortem", status_code=201)
def generate_postmortem(incident_id: str) -> dict[str, Any]:
    try:
        return postmortems.generate(incident_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Incident not found.") from None


@app.get("/api/incidents/{incident_id}/postmortem")
def get_postmortem(incident_id: str) -> dict[str, Any]:
    return _not_found(store.get_postmortem, incident_id)


@app.get("/api/incidents/{incident_id}/postmortem/export")
def export_postmortem(
    incident_id: str,
    format: str = Query(default="markdown", pattern="^(markdown|json)$"),
):
    document = _not_found(store.get_postmortem, incident_id)
    if format == "json":
        return document
    filename = f"{incident_id.lower()}-postmortem.md"
    return PlainTextResponse(
        postmortems.markdown(document),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/.well-known/agent-card.json")
def a2a_agent_card(request: Request) -> dict[str, Any]:
    return reviewer_agent_card(str(request.base_url).rstrip("/"))


@app.post("/a2a/reviewer")
def a2a_reviewer(payload: A2ARequest) -> dict[str, Any]:
    message = payload.params.get("message", {})
    parts = message.get("parts", [])
    data = next(
        (part.get("data") for part in parts if isinstance(part.get("data"), dict)),
        {},
    )
    remediation = data.get("remediation", {})
    incident = data.get("incident", {})
    tool = remediation.get("tool_name", "unknown")
    environment = incident.get("environment", "production")
    rollback = remediation.get("rollback", {})
    from .policy import classify_risk

    risk = classify_risk(tool, environment, remediation.get("arguments", {}))
    if str(risk) == "R3":
        decision = "deny"
        reason = "R3 or arbitrary execution is outside the reviewer permission boundary."
    elif environment in {"production", "prod"}:
        decision = "require_human_approval"
        reason = "The plan is bounded, but production writes require a human approver."
    elif not rollback:
        decision = "deny"
        reason = "The proposed write has no compensating action."
    else:
        decision = "approve"
        reason = "The change is scoped, reversible, and remains behind the policy gateway."
    review = {"decision": decision, "reason": reason, "concerns": []}
    task_id = f"a2a-review-{payload.id}"
    return {
        "jsonrpc": "2.0",
        "id": payload.id,
        "result": {
            "id": task_id,
            "contextId": message.get("contextId") or task_id,
            "status": {
                "state": "completed",
                "message": {
                    "messageId": f"{task_id}-message",
                    "role": "agent",
                    "parts": [{"kind": "text", "text": reason}],
                },
            },
            "artifacts": [
                {
                    "artifactId": f"{task_id}-artifact",
                    "name": "review-decision",
                    "parts": [{"kind": "data", "data": review}],
                }
            ],
        },
    }


web_dist = Path(
    os.getenv(
        "RUNGUARD_WEB_DIST",
        str(Path(__file__).resolve().parents[2] / "web" / "dist"),
    )
)
web_index = web_dist / "index.html"
if web_index.is_file():
    app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="web-assets")

    @app.get("/", include_in_schema=False)
    def web_application() -> FileResponse:
        return FileResponse(web_index)


def _not_found(function, *args):
    try:
        return function(*args)
    except KeyError:
        raise HTTPException(status_code=404, detail="Resource not found.") from None


def run() -> None:
    uvicorn.run("runguard_api.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
