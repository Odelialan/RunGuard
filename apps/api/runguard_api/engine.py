from __future__ import annotations

import asyncio
from typing import Any

from .config import Settings
from .gateway import MockMCPTransport, ToolContext
from .models import IncidentStatus, PolicySimulationRequest
from .policy import classify_risk, evaluate_policy
from .store import Store


class IncidentEngine:
    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.transport = MockMCPTransport()
        self._locks: dict[str, asyncio.Lock] = {}

    async def start(self, incident_id: str) -> dict[str, Any]:
        lock = self._locks.setdefault(incident_id, asyncio.Lock())
        async with lock:
            incident = self.store.get_incident(incident_id)
            if incident["status"] not in {"NEW", "INVESTIGATING", "HUMAN_HANDOFF"}:
                return incident

            run_id = self.store.create_run(incident_id, self.settings.prompt_version)
            await self._transition(
                incident_id,
                IncidentStatus.TRIAGING,
                "commander-agent",
                run_id,
                "agent",
                "commander.classify_and_plan",
                214,
                {"severity": incident["severity"], "service": incident["service"]},
            )
            await self._transition(
                incident_id,
                IncidentStatus.INVESTIGATING,
                "investigator-agent",
                run_id,
                "agent",
                "investigator.collect_evidence",
                1180,
            )

            evidence_ids = await self._collect_evidence(incident, run_id)
            cause = self._infer_cause(incident)
            self.store.add_hypothesis(incident_id, cause, 0.91, evidence_ids)
            self.store.add_trace(
                run_id,
                incident_id,
                "llm",
                "investigator.rank_hypotheses",
                "investigator",
                "OK",
                1320,
                {"hypotheses": 3, "top_confidence": 0.91},
            )

            await self._transition(
                incident_id,
                IncidentStatus.PLAN_READY,
                "remediation-agent",
                run_id,
                "agent",
                "remediation.generate_plan",
                934,
                {"has_rollback": True},
            )
            risk = classify_risk(
                "kubernetes.patch_deployment",
                incident["environment"],
                {"memory_limit": "1Gi"},
            )
            intent = self.store.create_intent(
                run_id=run_id,
                incident_id=incident_id,
                tool_name="kubernetes.patch_deployment",
                environment=incident["environment"],
                resource={
                    "namespace": incident["environment"],
                    "kind": "Deployment",
                    "name": incident["service"],
                },
                arguments={"service": incident["service"], "memory_limit": "1Gi"},
                rollback={"memory_limit": "256Mi"},
                risk_level=str(risk),
            )
            await self._transition(
                incident_id,
                IncidentStatus.POLICY_CHECKING,
                "policy-gateway",
                run_id,
                "policy",
                "opa.evaluate",
                42,
            )
            policy_input = PolicySimulationRequest(
                environment=incident["environment"],
                tool=intent["tool_name"],
                resource=incident["service"],
                risk_level=risk,
                has_rollback=bool(intent["rollback"]),
                incident_severity=incident["severity"],
            ).model_dump(mode="json")
            decision = evaluate_policy(policy_input)
            self.store.record_policy(
                intent["id"],
                self.settings.policy_version,
                decision,
                policy_input,
            )

            if decision["decision"] == "deny":
                self.store.finish_run(run_id, "DENIED", 2860, 4)
                return self.store.update_status(
                    incident_id,
                    IncidentStatus.DENIED,
                    "policy-gateway",
                    {"matched_policy": decision["matched_policy"]},
                )
            if decision["decision"] == "require_approval":
                self.store.add_trace(
                    run_id,
                    incident_id,
                    "approval",
                    "human.interrupt",
                    "orchestrator",
                    "PENDING",
                    3,
                    {"tool_intent_id": intent["id"]},
                )
                self.store.finish_run(run_id, "WAITING_APPROVAL", 2860, 4)
                return self.store.update_status(
                    incident_id,
                    IncidentStatus.WAITING_APPROVAL,
                    "policy-gateway",
                    {"tool_intent_id": intent["id"]},
                )
            self.store.decide_approval(
                intent["id"],
                "policy-gateway",
                "approved",
                "Auto-approved by reversible staging policy.",
            )
            return await self.execute(intent["id"])

    async def execute(self, intent_id: str) -> dict[str, Any]:
        intent = self.store.get_intent(intent_id)
        incident = self.store.get_incident(intent["incident_id"])
        if intent["status"] == "EXECUTED":
            return incident
        if intent["status"] not in {"APPROVED", "PENDING"}:
            raise ValueError(f"Tool intent is not executable: {intent['status']}")
        run_id = intent["run_id"]
        self.store.update_status(
            incident["id"],
            IncidentStatus.EXECUTING,
            "tool-gateway",
            {"tool_intent_id": intent_id},
        )
        context = ToolContext(
            incident_id=incident["id"],
            run_id=run_id,
            actor=intent["agent_name"],
            idempotency_key=intent["idempotency_key"],
        )
        result = await self.transport.call_tool(
            intent["tool_name"],
            intent["arguments"],
            context,
        )
        before = {"memory_limit": intent["rollback"].get("memory_limit", "256Mi")}
        after = {"memory_limit": intent["arguments"].get("memory_limit", "1Gi")}
        self.store.record_execution(
            intent_id,
            result.data,
            before,
            after,
            result.duration_ms,
        )
        self.store.add_trace(
            run_id,
            incident["id"],
            "tool_execution",
            intent["tool_name"],
            "tool-gateway",
            "OK" if result.ok else "ERROR",
            result.duration_ms,
            {"mode": self.settings.execution_mode, "idempotency_key": intent["idempotency_key"]},
        )
        self.store.update_status(
            incident["id"],
            IncidentStatus.VERIFYING,
            "orchestrator",
        )
        self.store.add_trace(
            run_id,
            incident["id"],
            "verification",
            "slo.verify_recovery",
            "investigator",
            "OK",
            1203,
            {
                "p95_before_seconds": 2.84,
                "p95_after_seconds": 0.41,
                "restart_count_after": 0,
            },
        )
        self.store.finish_run(run_id, "RESOLVED", 3298, 6)
        return self.store.update_status(
            incident["id"],
            IncidentStatus.RESOLVED,
            "commander-agent",
            {"verification": "passed", "postmortem": "generated"},
        )

    async def replay(self, incident_id: str) -> dict[str, Any]:
        incident = self.store.get_incident(incident_id)
        run_id = incident.get("current_run_id")
        if not run_id:
            raise ValueError("No run is available to replay.")
        original = self.store.get_run(run_id)
        return {
            "incident_id": incident_id,
            "source_run_id": run_id,
            "mode": "recorded",
            "status": "REPLAYED",
            "span_count": len(original["events"]),
            "side_effects": 0,
            "deterministic": True,
        }

    async def _transition(
        self,
        incident_id: str,
        status: IncidentStatus,
        actor: str,
        run_id: str,
        span_type: str,
        span_name: str,
        duration_ms: int,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.store.update_status(incident_id, status, actor)
        self.store.add_trace(
            run_id,
            incident_id,
            span_type,
            span_name,
            actor.replace("-agent", ""),
            "OK",
            duration_ms,
            attributes,
        )

    async def _collect_evidence(
        self,
        incident: dict[str, Any],
        run_id: str,
    ) -> list[str]:
        calls = [
            ("prometheus.query", "Latency increase", "prometheus"),
            ("kubernetes.get_events", "Workload events", "kubernetes"),
            ("loki.query", "Correlated error logs", "loki"),
            ("github.get_deployments", "Recent deployment", "github"),
        ]
        collected: list[dict[str, Any]] = []
        for tool_name, title, source_type in calls:
            context = ToolContext(
                incident_id=incident["id"],
                run_id=run_id,
                actor="investigator-agent",
                idempotency_key=f"{incident['id'].lower()}-{tool_name}",
            )
            result = await self.transport.call_tool(
                tool_name,
                {
                    "service": incident["service"],
                    "environment": incident["environment"],
                },
                context,
            )
            collected.append(
                {
                    "source_type": source_type,
                    "source_uri": result.source_uri,
                    "title": title,
                    "content": self._summarize_result(tool_name, result.data),
                    "metadata": {"raw": result.data, "duration_ms": result.duration_ms},
                }
            )
            self.store.add_trace(
                run_id,
                incident["id"],
                "retrieval" if source_type in {"prometheus", "loki"} else "tool",
                tool_name,
                "investigator",
                "OK" if result.ok else "ERROR",
                result.duration_ms,
                {"source_uri": result.source_uri},
            )
        return self.store.add_evidence(incident["id"], collected)

    @staticmethod
    def _summarize_result(tool_name: str, data: dict[str, Any]) -> str:
        if tool_name == "prometheus.query":
            return f"P95 latency is {data['value']}s, up from a {data['baseline']}s baseline."
        if tool_name == "kubernetes.get_events":
            return (
                f"{data['pods_affected']} pods report {data['restart_count']} OOMKilled "
                f"restarts with a {data['memory_limit']} memory limit."
            )
        if tool_name == "loki.query":
            return f"{data['matches']} out-of-memory log lines correlate with latency spikes."
        return (
            f"Deployment {data['commit']} changed {data['change']} "
            f"{data['deployed_minutes_before_alert']} minutes before the alert."
        )

    @staticmethod
    def _infer_cause(incident: dict[str, Any]) -> str:
        return (
            f"{incident['service']} memory limit is too low after the most recent deployment, "
            "causing repeated OOM kills and elevated tail latency."
        )
