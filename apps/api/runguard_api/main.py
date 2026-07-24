from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .config import load_settings
from .engine import IncidentEngine
from .evaluation import run_baseline
from .models import (
    ApprovalRequest,
    EvalRunRequest,
    IncidentCreate,
    IncidentStatus,
    PolicySimulationRequest,
    PrometheusAlert,
    ToolIntentEdit,
)
from .policy import evaluate_policy
from .store import Store

settings = load_settings()
store = Store(settings.database_path)
engine = IncidentEngine(store, settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(
    title="RunGuard API",
    description="Trusted Agentic SRE incident response API",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": "1.0.0",
        "execution_mode": settings.execution_mode,
        "database": "ready",
    }


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    return store.overview()


@app.post("/api/alerts/prometheus", status_code=201)
def prometheus_alert(payload: PrometheusAlert) -> dict[str, Any]:
    labels = payload.labels
    annotations = payload.annotations
    incident = IncidentCreate(
        title=annotations.get("summary") or labels.get("alertname") or "Prometheus alert",
        severity=labels.get("severity", "P2").upper(),
        service=labels.get("service", "unknown-service"),
        environment=labels.get("environment", "staging"),
        description=annotations.get("description", ""),
    )
    return store.create_incident(incident, source="prometheus")


@app.post("/api/incidents", status_code=201)
def create_incident(payload: IncidentCreate) -> dict[str, Any]:
    return store.create_incident(payload)


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
def simulate_policy(payload: PolicySimulationRequest) -> dict[str, Any]:
    return evaluate_policy(payload)


@app.get("/api/policies/versions")
def policy_versions() -> list[dict[str, Any]]:
    return [
        {
            "version": settings.policy_version,
            "status": "active",
            "rules": 4,
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


def _not_found(function, *args):
    try:
        return function(*args)
    except KeyError:
        raise HTTPException(status_code=404, detail="Resource not found.") from None


def run() -> None:
    uvicorn.run("runguard_api.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
