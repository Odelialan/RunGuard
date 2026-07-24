from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, TypedDict

from pydantic import BaseModel, Field

from .a2a import A2AReviewerClient
from .config import Settings


class CommanderDecision(BaseModel):
    severity: str
    objective: str
    investigation_steps: list[str]


class InvestigationReport(BaseModel):
    root_cause: str
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str]
    alternatives: list[str] = Field(default_factory=list)


class RemediationPlan(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    rollback: dict[str, Any]
    verification_queries: list[str]
    rationale: str


class ReviewDecision(BaseModel):
    decision: str
    reason: str
    concerns: list[str] = Field(default_factory=list)


class ReporterSummary(BaseModel):
    summary: str
    impact: str
    contributing_factors: list[str]
    lessons: list[str]
    action_items: list[dict[str, Any]]


class GraphState(TypedDict, total=False):
    incident: dict[str, Any]
    evidence: list[dict[str, Any]]
    commander: dict[str, Any]
    investigation: dict[str, Any]
    remediation: dict[str, Any]
    review: dict[str, Any]
    report: dict[str, Any]


class LangGraphOrchestrator:
    def __init__(self, settings: Settings) -> None:
        from langchain_openai import ChatOpenAI

        kwargs: dict[str, Any] = {"model": settings.llm_model, "temperature": 0}
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        self.model = ChatOpenAI(**kwargs)
        self.settings = settings
        self.prompts = self._load_prompts()
        self.a2a = A2AReviewerClient(
            settings.a2a_reviewer_url,
            settings.a2a_reviewer_token,
        )
        self.graph = self._build_graph()

    @staticmethod
    def _load_prompts() -> dict[str, str]:
        root = Path(
            os.getenv("RUNGUARD_PROMPT_DIR")
            or str(Path.cwd() / "agents" / "prompts")
        )
        return {
            name: (root / f"{name}.md").read_text(encoding="utf-8")
            for name in ("commander", "investigator", "remediation", "reviewer", "reporter")
        }

    async def _structured(
        self,
        prompt_name: str,
        schema: type[BaseModel],
        payload: dict[str, Any],
    ) -> BaseModel:
        runnable = self.model.with_structured_output(schema)
        return await runnable.ainvoke(
            [
                ("system", self.prompts[prompt_name]),
                (
                    "user",
                    "Use only the following incident record and cited evidence.\n"
                    + json.dumps(payload, ensure_ascii=False, default=str),
                ),
            ]
        )

    def _build_graph(self):
        from langgraph.graph import END, START, StateGraph

        async def commander(state: GraphState) -> dict[str, Any]:
            result = await self._structured(
                "commander",
                CommanderDecision,
                {"incident": state["incident"]},
            )
            return {"commander": result.model_dump()}

        async def investigator(state: GraphState) -> dict[str, Any]:
            result = await self._structured(
                "investigator",
                InvestigationReport,
                {
                    "incident": state["incident"],
                    "plan": state["commander"],
                    "evidence": state["evidence"],
                },
            )
            return {"investigation": result.model_dump()}

        async def remediation(state: GraphState) -> dict[str, Any]:
            result = await self._structured(
                "remediation",
                RemediationPlan,
                {
                    "incident": state["incident"],
                    "investigation": state["investigation"],
                    "evidence": state["evidence"],
                },
            )
            return {"remediation": result.model_dump()}

        async def reviewer(state: GraphState) -> dict[str, Any]:
            payload = {
                "incident": state["incident"],
                "investigation": state["investigation"],
                "remediation": state["remediation"],
                "evidence": state["evidence"],
            }
            if self.a2a.enabled:
                result = ReviewDecision.model_validate(await self.a2a.review(payload))
            else:
                result = await self._structured("reviewer", ReviewDecision, payload)
            return {"review": result.model_dump()}

        async def reporter(state: GraphState) -> dict[str, Any]:
            result = await self._structured(
                "reporter",
                ReporterSummary,
                {
                    "incident": state["incident"],
                    "investigation": state["investigation"],
                    "remediation": state["remediation"],
                    "review": state["review"],
                },
            )
            return {"report": result.model_dump()}

        builder = StateGraph(GraphState)
        builder.add_node("commander", commander)
        builder.add_node("investigator", investigator)
        builder.add_node("remediation", remediation)
        builder.add_node("reviewer", reviewer)
        builder.add_node("reporter", reporter)
        builder.add_edge(START, "commander")
        builder.add_edge("commander", "investigator")
        builder.add_edge("investigator", "remediation")
        builder.add_edge("remediation", "reviewer")
        builder.add_edge("reviewer", "reporter")
        builder.add_edge("reporter", END)
        return builder.compile()

    async def run(
        self,
        incident: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> GraphState:
        return await self.graph.ainvoke(
            {"incident": incident, "evidence": evidence},
            {
                "configurable": {"thread_id": incident["id"]},
                "metadata": {
                    "incident_id": incident["id"],
                    "prompt_version": self.settings.prompt_version,
                },
                "recursion_limit": 12,
            },
        )

    async def generate_report(self, incident: dict[str, Any]) -> dict[str, Any]:
        result = await self._structured(
            "reporter",
            ReporterSummary,
            {
                "incident": incident,
                "evidence": incident.get("evidence", []),
                "hypotheses": incident.get("hypotheses", []),
                "tool_intents": incident.get("tool_intents", []),
                "events": incident.get("events", []),
            },
        )
        return result.model_dump()
