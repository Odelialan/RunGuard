from __future__ import annotations

import asyncio
import os
import random
from dataclasses import asdict, dataclass

from fastapi import FastAPI, Header, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field


@dataclass
class FaultState:
    latency_ms: int = 0
    error_rate: float = 0.0
    unhealthy: bool = False


class FaultConfig(BaseModel):
    latency_ms: int = Field(default=0, ge=0, le=30_000)
    error_rate: float = Field(default=0.0, ge=0, le=1)
    unhealthy: bool = False


state = FaultState()
requests_total = Counter("fault_injector_requests_total", "Requests", ["outcome"])
latency = Histogram("fault_injector_request_duration_seconds", "Injected request duration")
fault_active = Gauge("fault_injector_active", "Whether a fault is active", ["fault"])

app = FastAPI(title="RunGuard Fault Injector", version="1.1.0")


def _authorize(token: str | None) -> None:
    expected = os.getenv("FAULT_INJECTOR_TOKEN")
    if not expected or token != expected:
        raise HTTPException(status_code=401, detail="Valid X-Fault-Token required.")


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "unhealthy" if state.unhealthy else "ok", **asdict(state)}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/orders")
async def workload() -> dict[str, object]:
    with latency.time():
        if state.latency_ms:
            await asyncio.sleep(state.latency_ms / 1000)
        if state.unhealthy or random.random() < state.error_rate:
            requests_total.labels(outcome="error").inc()
            raise HTTPException(status_code=503, detail="Injected service failure.")
        requests_total.labels(outcome="ok").inc()
        return {"status": "ok", "orders": 12}


@app.get("/faults")
def current_faults() -> dict[str, object]:
    return asdict(state)


@app.post("/faults")
def configure_faults(
    payload: FaultConfig,
    x_fault_token: str | None = Header(default=None),
) -> dict[str, object]:
    _authorize(x_fault_token)
    state.latency_ms = payload.latency_ms
    state.error_rate = payload.error_rate
    state.unhealthy = payload.unhealthy
    fault_active.labels(fault="latency").set(1 if state.latency_ms else 0)
    fault_active.labels(fault="errors").set(1 if state.error_rate else 0)
    fault_active.labels(fault="unhealthy").set(1 if state.unhealthy else 0)
    return {"status": "configured", **asdict(state)}


@app.delete("/faults")
def reset_faults(x_fault_token: str | None = Header(default=None)) -> dict[str, object]:
    _authorize(x_fault_token)
    state.latency_ms = 0
    state.error_rate = 0
    state.unhealthy = False
    for fault in ("latency", "errors", "unhealthy"):
        fault_active.labels(fault=fault).set(0)
    return {"status": "reset", **asdict(state)}
