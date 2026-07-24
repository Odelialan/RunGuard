from __future__ import annotations

from .models import PolicySimulationRequest, RiskLevel

READ_TOOLS = {
    "prometheus.query",
    "loki.query",
    "kubernetes.get_pods",
    "kubernetes.get_events",
    "kubernetes.get_deployment",
    "github.get_deployments",
}

STAGING_WRITES = {
    "kubernetes.patch_deployment",
    "kubernetes.scale_deployment",
    "kubernetes.rollout_restart",
}

R3_TOOLS = {
    "kubernetes.delete_namespace",
    "database.drop",
    "shell.execute",
}


def classify_risk(
    tool: str,
    environment: str,
    arguments: dict[str, object] | None = None,
) -> RiskLevel:
    arguments = arguments or {}
    if tool in R3_TOOLS or arguments.get("privileged") is True:
        return RiskLevel.R3
    if tool in READ_TOOLS or tool.startswith(("prometheus.", "loki.")):
        return RiskLevel.R0
    if environment.lower() in {"production", "prod"}:
        return RiskLevel.R2
    if tool in STAGING_WRITES:
        return RiskLevel.R1
    return RiskLevel.R2


def evaluate_policy(
    payload: PolicySimulationRequest | dict[str, object],
) -> dict[str, object]:
    data = (
        payload.model_dump(mode="json")
        if isinstance(payload, PolicySimulationRequest)
        else dict(payload)
    )
    risk = data.get("risk_level") or classify_risk(
        data["tool"],
        data["environment"],
        data.get("arguments"),
    )
    risk_value = str(risk)
    environment = data["environment"].lower()
    has_rollback = bool(data.get("has_rollback", False))

    if risk_value == "R3":
        return {
            "decision": "deny",
            "risk_level": risk_value,
            "matched_policy": "destructive-operations-denied",
            "reason": (
                "Destructive or arbitrary execution is outside the Agent permission boundary."
            ),
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
    return {
        "decision": "allow",
        "risk_level": risk_value,
        "matched_policy": "readonly-or-reversible-staging",
        "reason": "Operation is read-only or reversible within an isolated environment.",
    }
