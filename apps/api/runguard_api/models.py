from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class IncidentStatus(StrEnum):
    NEW = "NEW"
    TRIAGING = "TRIAGING"
    INVESTIGATING = "INVESTIGATING"
    PLAN_READY = "PLAN_READY"
    POLICY_CHECKING = "POLICY_CHECKING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    DENIED = "DENIED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"
    CANCELLED = "CANCELLED"


class Severity(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class RiskLevel(StrEnum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"


class IncidentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    severity: Severity = Severity.P2
    service: str = Field(min_length=2, max_length=80)
    environment: str = Field(default="staging", min_length=2, max_length=40)
    description: str = Field(default="", max_length=2000)

    @field_validator("title", "service", "environment", mode="before")
    @classmethod
    def normalize_identity_fields(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("environment")
    @classmethod
    def normalize_environment(cls, value: str) -> str:
        aliases = {
            "prod": "production",
            "stage": "staging",
            "dev": "development",
        }
        normalized = value.strip().lower()
        return aliases.get(normalized, normalized)


class PrometheusAlert(BaseModel):
    status: str = "firing"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: str | None = None


class ToolIntentCreate(BaseModel):
    tool: str
    actor: str = "remediation-agent"
    incident_id: str
    environment: str
    resource: dict[str, Any]
    arguments: dict[str, Any]
    rollback: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str


class ApprovalRequest(BaseModel):
    reviewer: str = Field(default="SRE Operator", min_length=2, max_length=80)
    comment: str = Field(default="", max_length=1000)


class ToolIntentEdit(BaseModel):
    arguments: dict[str, Any]
    reviewer: str = Field(default="SRE Operator", min_length=2, max_length=80)
    comment: str = Field(default="", max_length=1000)


class PolicySimulationRequest(BaseModel):
    agent: str = "remediation-agent"
    role: str = "incident_remediator"
    environment: str
    tool: str
    resource: str | dict[str, Any]
    arguments: dict[str, Any] = Field(default_factory=dict)
    rollback: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel | None = None
    has_rollback: bool = True
    edited: bool = False
    incident_severity: Severity = Severity.P2

    @field_validator("environment", "tool")
    @classmethod
    def normalize_policy_fields(cls, value: str) -> str:
        return value.strip().lower()


class EvalRunRequest(BaseModel):
    suite: Literal["baseline-12"] = "baseline-12"
    model: str = "deterministic-demo"
    prompt_version: str = "1.2.1"


class PostmortemActionItem(BaseModel):
    title: str
    owner: str
    priority: Literal["P0", "P1", "P2", "P3"] = "P2"
    due_date: str | None = None
    status: Literal["OPEN", "IN_PROGRESS", "DONE"] = "OPEN"


class PostmortemDocument(BaseModel):
    id: str | None = None
    incident_id: str
    run_id: str | None = None
    status: Literal["DRAFT", "FINAL"] = "FINAL"
    title: str
    summary: str
    impact: str
    root_cause: str
    contributing_factors: list[str] = Field(default_factory=list)
    timeline: list[dict[str, str]] = Field(default_factory=list)
    remediation: list[str] = Field(default_factory=list)
    action_items: list[PostmortemActionItem] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    generated_by: str = "reporter-agent"


class A2AMessagePart(BaseModel):
    kind: str = "text"
    text: str | None = None
    data: dict[str, Any] | None = None


class A2AMessage(BaseModel):
    messageId: str
    role: Literal["user", "agent"]
    parts: list[A2AMessagePart]
    contextId: str | None = None


class A2ARequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int
    method: Literal["message/send"]
    params: dict[str, Any]


class EvidenceSearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=1000)
    limit: int = Field(default=8, ge=1, le=50)
