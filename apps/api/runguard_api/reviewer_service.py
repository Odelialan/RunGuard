from __future__ import annotations

import hmac
import os
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request

from .a2a import review_remediation_payload, reviewer_agent_card
from .models import A2ARequest
from .security import RequestBodyLimitMiddleware

app = FastAPI(
    title="RunGuard Independent Reviewer",
    description="A separately deployable, credential-free A2A remediation reviewer.",
    version="1.4.2",
)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_bytes=int(os.getenv("RUNGUARD_MAX_REQUEST_BODY_BYTES", "1048576")),
)


def _authorize(authorization: str | None) -> None:
    expected = os.getenv("RUNGUARD_A2A_REVIEWER_TOKEN", "")
    supplied = (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else ""
    )
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Valid reviewer bearer token required.")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "runguard-independent-reviewer"}


@app.get("/.well-known/agent-card.json")
def agent_card(request: Request) -> dict[str, Any]:
    base_url = os.getenv("RUNGUARD_REVIEWER_PUBLIC_BASE_URL") or str(request.base_url)
    return reviewer_agent_card(base_url.rstrip("/"))


@app.post("/a2a/reviewer")
def review(
    payload: A2ARequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    message = payload.params.get("message", {})
    parts = message.get("parts", [])
    data = next(
        (part.get("data") for part in parts if isinstance(part.get("data"), dict)),
        {},
    )
    decision = review_remediation_payload(data)
    task_id = f"a2a-review-{payload.id or uuid4().hex}"
    return {
        "jsonrpc": "2.0",
        "id": payload.id,
        "result": {
            "id": task_id,
            "contextId": message.get("contextId") or task_id,
            "status": {"state": "completed"},
            "artifacts": [
                {
                    "artifactId": f"{task_id}-artifact",
                    "name": "review-decision",
                    "parts": [{"kind": "data", "data": decision}],
                }
            ],
        },
    }
