from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx


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
        "version": "1.1.0",
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
        "securitySchemes": {},
        "security": [],
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
