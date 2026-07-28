from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx

from .policy import READ_TOOLS, WRITE_TOOLS, classify_risk, has_effective_rollback


def reviewer_agent_card(base_url: str) -> dict[str, Any]:
    return {
        "protocolVersion": "1.0",
        "name": "RunGuard Reviewer",
        "description": "Independent safety reviewer for SRE remediation Tool Intents.",
        "url": f"{base_url.rstrip('/')}/a2a/reviewer",
        "preferredTransport": "JSONRPC",
        "additionalInterfaces": [
            {"url": f"{base_url.rstrip('/')}/a2a/reviewer", "transport": "JSONRPC"}
        ],
        "version": "1.4.1",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["application/json", "text/plain"],
        "defaultOutputModes": ["application/json", "text/plain"],
        "skills": [
            {
                "id": "review-tool-intent",
                "name": "Review Tool Intent",
                "description": (
                    "Reviews evidence, scope, rollback, policy and blast radius before execution."
                ),
                "tags": ["sre", "safety", "kubernetes", "approval"],
                "examples": ["Review a production Deployment memory-limit change."],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            }
        ],
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": "RunGuard service bearer token.",
            }
        },
        "security": [{"bearerAuth": []}],
    }


class A2AReviewerClient:
    def __init__(self, url: str | None, token: str | None, timeout_seconds: float = 20.0) -> None:
        self.url = url
        self.token = token
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def review(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.url:
            raise RuntimeError("A2A reviewer URL is not configured.")
        request_id = uuid4().hex
        message_id = uuid4().hex
        headers = {"A2A-Version": "1.0"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": message_id,
                    "role": "user",
                    "parts": [{"kind": "data", "data": payload}],
                }
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=headers) as client:
            response = await client.post(self.url, json=request)
            response.raise_for_status()
            body = response.json()
        if body.get("error"):
            raise RuntimeError(f"A2A reviewer error: {body['error']}")
        result = body.get("result", {})
        artifacts = result.get("artifacts", [])
        for artifact in artifacts:
            for part in artifact.get("parts", []):
                if isinstance(part.get("data"), dict):
                    return part["data"]
        raise RuntimeError("A2A reviewer returned no structured review artifact.")


def review_remediation_payload(data: dict[str, Any]) -> dict[str, Any]:
    remediation = data.get("remediation", {})
    incident = data.get("incident", {})
    investigation = data.get("investigation", {})
    evidence = data.get("evidence", [])
    if not all(
        isinstance(value, dict)
        for value in (remediation, incident, investigation)
    ) or not isinstance(evidence, list):
        return _review_denial(
            "Reviewer input must contain structured incident, investigation, "
            "remediation, and evidence fields."
        )

    tool = str(remediation.get("tool_name", "")).strip().lower()
    environment = str(incident.get("environment", "")).strip().lower()
    arguments = remediation.get("arguments", {})
    rollback = remediation.get("rollback", {})
    if not isinstance(arguments, dict) or not isinstance(rollback, dict):
        return _review_denial("Tool arguments and rollback must be structured objects.")

    if tool in READ_TOOLS or tool not in WRITE_TOOLS:
        return _review_denial("Remediation may use only an exact allow-listed write tool.")

    evidence_ids = {
        str(item.get("id"))
        for item in evidence
        if isinstance(item, dict) and item.get("id")
    }
    cited = investigation.get("evidence_ids", [])
    if (
        not isinstance(cited, list)
        or not cited
        or not set(map(str, cited)).issubset(evidence_ids)
    ):
        return _review_denial(
            "The investigation must cite evidence IDs present in this review request."
        )

    parameter_error = _validate_remediation_parameters(tool, arguments, rollback)
    if parameter_error:
        return _review_denial(parameter_error)

    risk = classify_risk(tool, environment, arguments)
    if str(risk) == "R3":
        decision = "deny"
        reason = "R3 or arbitrary execution is outside the reviewer permission boundary."
    elif environment in {"production", "prod"}:
        decision = "require_human_approval"
        reason = "The plan is bounded, but production writes require a human approver."
    else:
        decision = "approve"
        reason = (
            "The evidence references, parameters, target-independent plan, and compensation "
            "contract are internally consistent; the policy gateway remains authoritative."
        )
    return {"decision": decision, "reason": reason, "concerns": []}


def _review_denial(reason: str) -> dict[str, Any]:
    return {"decision": "deny", "reason": reason, "concerns": [reason]}


def _validate_remediation_parameters(
    tool: str,
    arguments: dict[str, Any],
    rollback: dict[str, Any],
) -> str | None:
    if not has_effective_rollback(tool, rollback):
        return "The proposed write has no effective state-restoring compensation."
    if tool == "kubernetes.patch_deployment":
        changed = {key for key in ("memory_limit", "cpu_limit") if key in arguments}
        if not changed:
            return "Deployment patch requires memory_limit or cpu_limit."
        if not changed.issubset(rollback):
            return "Every changed resource limit requires a matching rollback value."
    elif tool == "kubernetes.scale_deployment":
        try:
            replicas = int(arguments["replicas"])
            rollback_replicas = int(rollback["replicas"])
        except (KeyError, TypeError, ValueError):
            return "Scale remediation and rollback require integer replica counts."
        if not 1 <= replicas <= 20 or not 1 <= rollback_replicas <= 20:
            return "Replica counts must remain between 1 and 20."
    return None
