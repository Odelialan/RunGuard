from __future__ import annotations

from typing import Any

import httpx

from .models import PolicySimulationRequest, RiskLevel

READ_TOOLS = {
    "prometheus.query",
    "loki.query",
    "kubernetes.get_pods",
    "kubernetes.get_events",
    "kubernetes.get_deployment",
    "github.get_deployments",
}

WRITE_TOOLS = {
    "kubernetes.patch_deployment",
    "kubernetes.scale_deployment",
    "kubernetes.rollout_restart",
}

R3_TOOLS = {
    "kubernetes.delete_namespace",
    "database.drop",
    "shell.execute",
}

ROLLBACK_FIELDS = {
    "kubernetes.patch_deployment": {"memory_limit", "cpu_limit"},
    "kubernetes.scale_deployment": {"replicas"},
    "kubernetes.rollout_restart": set(),
}


def has_effective_rollback(tool: str, rollback: object) -> bool:
    if not isinstance(rollback, dict):
        return False
    return bool(set(rollback) & ROLLBACK_FIELDS.get(tool.strip().lower(), set()))


def classify_risk(
    tool: str,
    environment: str,
    arguments: dict[str, object] | None = None,
) -> RiskLevel:
    arguments = arguments or {}
    tool = tool.strip().lower()
    environment = environment.strip().lower()
    if tool in R3_TOOLS or arguments.get("privileged") is True:
        return RiskLevel.R3
    if tool in READ_TOOLS:
        return RiskLevel.R0
    if tool not in WRITE_TOOLS:
        return RiskLevel.R3
    if environment in {"production", "prod"}:
        return RiskLevel.R2
    if tool in WRITE_TOOLS:
        return RiskLevel.R1
    return RiskLevel.R3


def evaluate_policy(
    payload: PolicySimulationRequest | dict[str, object],
) -> dict[str, object]:
    data = (
        payload.model_dump(mode="json")
        if isinstance(payload, PolicySimulationRequest)
        else dict(payload)
    )
    risk = classify_risk(
        data["tool"],
        data["environment"],
        data.get("arguments"),
    )
    risk_value = str(risk)
    environment = str(data["environment"]).strip().lower()
    tool = str(data["tool"]).strip().lower()
    has_rollback = has_effective_rollback(tool, data.get("rollback"))

    if risk_value == "R3" or tool not in READ_TOOLS | WRITE_TOOLS:
        return {
            "decision": "deny",
            "risk_level": risk_value,
            "matched_policy": "destructive-operations-denied",
            "reason": (
                "Destructive or arbitrary execution is outside the Agent permission boundary."
            ),
        }
    if bool(data.get("edited")):
        return {
            "decision": "require_approval",
            "risk_level": risk_value,
            "matched_policy": "edited-intent-requires-fresh-approval",
            "reason": "Every edited intent requires a fresh human approval.",
        }
    if environment in {"production", "prod"} and risk_value in {"R1", "R2"}:
        return {
            "decision": "require_approval",
            "risk_level": "R2",
            "matched_policy": "prod-write-requires-human",
            "reason": "Production write operation requires SRE approval.",
        }
    if risk_value in {"R1", "R2"} and not has_rollback:
        return {
            "decision": "require_approval",
            "risk_level": risk_value,
            "matched_policy": "write-without-rollback-requires-human",
            "reason": "Write operation has no verified rollback action.",
        }
    if risk_value == "R0" or (
        risk_value == "R1"
        and environment in {"staging", "development", "test", "kind", "runguard-system"}
        and has_rollback
    ):
        return {
            "decision": "allow",
            "risk_level": risk_value,
            "matched_policy": "readonly-or-reversible-staging",
            "reason": "Operation is read-only or reversible within an isolated environment.",
        }
    return {
        "decision": "deny",
        "risk_level": risk_value,
        "matched_policy": "default-deny",
        "reason": "No policy allows this tool intent.",
    }


class PolicyEvaluator:
    """OPA is authoritative in production; Python evaluation is an explicit demo backend."""

    def __init__(
        self,
        backend: str,
        opa_url: str | None,
        *,
        fail_closed: bool = True,
        timeout_seconds: float = 3.0,
    ) -> None:
        self.backend = backend
        self.opa_url = opa_url.rstrip("/") if opa_url else None
        self.fail_closed = fail_closed
        self.timeout_seconds = timeout_seconds

    async def evaluate(
        self,
        payload: PolicySimulationRequest | dict[str, object],
    ) -> dict[str, Any]:
        data = (
            payload.model_dump(mode="json")
            if isinstance(payload, PolicySimulationRequest)
            else dict(payload)
        )
        risk = str(
            classify_risk(
                str(data["tool"]),
                str(data["environment"]),
                data.get("arguments"),
            )
        )
        data["risk_level"] = risk
        data["has_rollback"] = has_effective_rollback(
            str(data["tool"]),
            data.get("rollback"),
        )
        if self.backend != "opa":
            return evaluate_policy(data)
        if not self.opa_url:
            return self._unavailable(risk, "OPA backend selected without RUNGUARD_OPA_URL.")
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.opa_url}/v1/data/runguard/tool_intent/decision",
                    json={"input": data},
                )
                response.raise_for_status()
                body = response.json()
            result = body.get("result")
            if not isinstance(result, dict):
                return self._unavailable(risk, "OPA returned an undefined policy decision.")
            if result.get("decision") not in {"allow", "require_approval", "deny"}:
                return self._unavailable(risk, "OPA returned an invalid policy decision.")
            if not all(
                isinstance(result.get(field), str) and result[field].strip()
                for field in ("matched_policy", "reason")
            ):
                return self._unavailable(risk, "OPA returned incomplete policy metadata.")
            return {
                "decision": result["decision"],
                "risk_level": risk,
                "matched_policy": result["matched_policy"],
                "reason": result["reason"],
                "decision_id": body.get("decision_id"),
                "backend": "opa",
            }
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            return self._unavailable(risk, f"OPA evaluation failed: {exc}")

    def _unavailable(self, risk: str, reason: str) -> dict[str, Any]:
        if not self.fail_closed:
            fallback = evaluate_policy(
                {
                    "tool": "unknown",
                    "environment": "production",
                    "risk_level": risk,
                    "has_rollback": False,
                }
            )
            return {**fallback, "backend": "python-fallback", "warning": reason}
        return {
            "decision": "deny",
            "risk_level": risk,
            "matched_policy": "opa-unavailable-fail-closed",
            "reason": reason,
            "backend": "opa",
        }

    async def health(self) -> str:
        if self.backend != "opa":
            return "python-demo"
        if not self.opa_url:
            return "unavailable"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(f"{self.opa_url}/health?bundles=true")
            return "ready" if response.is_success else "unavailable"
        except httpx.HTTPError:
            return "unavailable"
