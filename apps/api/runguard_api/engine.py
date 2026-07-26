from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from .config import Settings
from .embeddings import EvidenceIndexer
from .event_stream import EventStream
from .gateway import ToolContext, build_transport
from .models import IncidentStatus, PolicySimulationRequest
from .orchestration import LangGraphOrchestrator
from .policy import PolicyEvaluator, classify_risk
from .postmortem import PostmortemService
from .store import Store


class WorkflowLocked(ValueError):
    pass


class IncidentEngine:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        event_stream: EventStream | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.transport = build_transport(settings)
        self.policy = PolicyEvaluator(
            settings.policy_backend,
            settings.opa_url,
            fail_closed=settings.opa_fail_closed,
        )
        self.event_stream = event_stream or EventStream(None, settings.redis_stream)
        self.postmortems = PostmortemService(store)
        self.evidence_index = EvidenceIndexer(settings, store)
        self.orchestrator = (
            LangGraphOrchestrator(settings) if settings.agent_backend == "langgraph" else None
        )
        self._locks: dict[str, asyncio.Lock] = {}
        self._recovery_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        if self.orchestrator:
            await self.orchestrator.initialize()
        if self.settings.auto_recover:
            self._recovery_task = asyncio.create_task(
                self.recover_incomplete(),
                name="runguard-workflow-recovery",
            )

    async def close(self) -> None:
        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()
            try:
                await self._recovery_task
            except asyncio.CancelledError:
                pass
        self._recovery_task = None
        if self.orchestrator:
            await self.orchestrator.close()

    async def recover_incomplete(self) -> None:
        semaphore = asyncio.Semaphore(self.settings.recovery_concurrency)

        async def recover_one(incident_id: str) -> None:
            async with semaphore:
                try:
                    await self.resume(incident_id)
                except WorkflowLocked:
                    return
                except Exception as exc:
                    incident = self.store.get_incident(incident_id)
                    run_id = incident.get("current_run_id")
                    if run_id:
                        self.store.add_trace(
                            run_id,
                            incident_id,
                            "recovery",
                            "workflow.resume",
                            "recovery-controller",
                            "ERROR",
                            0,
                            {"error": str(exc)},
                        )
                    self.store.update_status(
                        incident_id,
                        IncidentStatus.HUMAN_HANDOFF,
                        "recovery-controller",
                        {"reason": "Automatic recovery failed closed."},
                    )

        await asyncio.gather(
            *(recover_one(incident_id) for incident_id in self.store.list_recoverable_incidents())
        )

    async def start(self, incident_id: str) -> dict[str, Any]:
        lock_name = f"incident:{incident_id}"
        token = await self.event_stream.acquire_lock(lock_name)
        if token is None:
            raise WorkflowLocked("Incident workflow is already active on another worker.")
        try:
            return await self._start_unlocked(incident_id)
        finally:
            await self.event_stream.release_lock(lock_name, token)

    async def _start_unlocked(self, incident_id: str) -> dict[str, Any]:
        lock = self._locks.setdefault(incident_id, asyncio.Lock())
        async with lock:
            incident = self.store.get_incident(incident_id)
            if incident["status"] not in {"NEW", "INVESTIGATING", "HUMAN_HANDOFF"}:
                return incident

            run_id = self.store.create_run(
                incident_id,
                self.settings.prompt_version,
                graph_version=(
                    "incident-response-langgraph-v1.2"
                    if self.orchestrator
                    else "incident-response-v1"
                ),
                model_config={
                    "provider": (
                        "langchain-openai" if self.orchestrator else "deterministic"
                    ),
                    "model": self.settings.llm_model if self.orchestrator else "demo-v1",
                },
            )
            self._checkpoint(run_id, incident_id, "RUN_CREATED")
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
            self._checkpoint(
                run_id,
                incident_id,
                "EVIDENCE_COLLECTED",
                {"evidence_ids": evidence_ids},
            )
            graph_output: dict[str, Any] | None = None
            if self.orchestrator:
                try:
                    enriched = self.store.get_incident(incident_id)
                    graph_output = await self.orchestrator.run(
                        enriched,
                        enriched["evidence"],
                        run_id,
                    )
                    self._checkpoint(
                        run_id,
                        incident_id,
                        "LANGGRAPH_COMPLETED",
                        await self.orchestrator.checkpoint(run_id) or {},
                    )
                except Exception as exc:
                    self.store.add_trace(
                        run_id,
                        incident_id,
                        "llm",
                        "langgraph.incident_response",
                        "orchestrator",
                        "ERROR",
                        0,
                        {"error": str(exc)},
                    )
                    self.store.finish_run(run_id, "HUMAN_HANDOFF", 0, 4)
                    return self.store.update_status(
                        incident_id,
                        IncidentStatus.HUMAN_HANDOFF,
                        "orchestrator",
                        {"reason": "LangGraph or model provider failed closed."},
                    )
            investigation = (graph_output or {}).get("investigation", {})
            cause = investigation.get("root_cause") or self._infer_cause(incident)
            confidence = float(investigation.get("confidence", 0.91))
            linked_evidence = investigation.get("evidence_ids") or evidence_ids
            self.store.add_hypothesis(incident_id, cause, confidence, linked_evidence)
            self.store.add_trace(
                run_id,
                incident_id,
                "llm",
                "investigator.rank_hypotheses",
                "investigator",
                "OK",
                1320,
                {
                    "hypotheses": 3,
                    "top_confidence": confidence,
                    "backend": self.settings.agent_backend,
                },
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
            remediation = (graph_output or {}).get("remediation", {})
            tool_name = remediation.get("tool_name", "kubernetes.patch_deployment")
            target_namespace = self._target_namespace(incident["environment"])
            arguments = {
                "service": incident["service"],
                "namespace": target_namespace,
                "name": incident["service"],
                **remediation.get("arguments", {"memory_limit": "1Gi"}),
            }
            rollback = {
                "service": incident["service"],
                "namespace": target_namespace,
                "name": incident["service"],
                **remediation.get("rollback", {"memory_limit": "256Mi"}),
            }
            risk = classify_risk(
                tool_name,
                incident["environment"],
                arguments,
            )
            intent = self.store.create_intent(
                run_id=run_id,
                incident_id=incident_id,
                tool_name=tool_name,
                environment=incident["environment"],
                resource={
                    "namespace": target_namespace,
                    "kind": "Deployment",
                    "name": incident["service"],
                },
                arguments=arguments,
                rollback=rollback,
                risk_level=str(risk),
            )
            self._checkpoint(
                run_id,
                incident_id,
                "INTENT_CREATED",
                {"tool_intent_id": intent["id"]},
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
            decision = await self.policy.evaluate(policy_input)
            review = (graph_output or {}).get("review", {})
            if str(review.get("decision", "")).lower() in {"deny", "rejected", "reject"}:
                decision = {
                    "decision": "deny",
                    "risk_level": str(risk),
                    "matched_policy": "independent-reviewer-denied",
                    "reason": review.get("reason", "Independent reviewer rejected the plan."),
                    "backend": (
                        "a2a-reviewer" if self.settings.a2a_reviewer_url else "langgraph-reviewer"
                    ),
                }
            self.store.record_policy(
                intent["id"],
                self.settings.policy_version,
                decision,
                policy_input,
            )
            self._checkpoint(
                run_id,
                incident_id,
                "POLICY_DECIDED",
                {
                    "tool_intent_id": intent["id"],
                    "decision": decision["decision"],
                    "matched_policy": decision["matched_policy"],
                },
            )

            if decision["decision"] == "deny":
                self.store.finish_run(run_id, "DENIED", 2860, 4)
                self._checkpoint(run_id, incident_id, "DENIED")
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
                self._checkpoint(
                    run_id,
                    incident_id,
                    "WAITING_APPROVAL",
                    {"tool_intent_id": intent["id"]},
                )
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
            return await self._verify_and_finalize(intent, incident)
        if intent["status"] not in {"APPROVED", "PENDING"}:
            raise ValueError(f"Tool intent is not executable: {intent['status']}")
        run_id = intent["run_id"]
        self._checkpoint(
            run_id,
            incident["id"],
            "EXECUTION_STARTED",
            {"tool_intent_id": intent_id},
        )
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
        runner_result = (
            result.data.get("runner_result", {}) if isinstance(result.data, dict) else {}
        )
        before = runner_result.get("before") or {
            key: value
            for key, value in intent["rollback"].items()
            if key not in {"service", "namespace", "name"}
        }
        after = runner_result.get("after") or {
            key: value
            for key, value in intent["arguments"].items()
            if key not in {"service", "namespace", "name"}
        }
        self.store.record_execution(
            intent_id,
            result.data,
            before,
            after,
            result.duration_ms,
        )
        self._checkpoint(
            run_id,
            incident["id"],
            "EXECUTION_RECORDED",
            {
                "tool_intent_id": intent_id,
                "ok": result.ok,
                "source_uri": result.source_uri,
            },
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
        if not result.ok:
            return await self.rollback(intent_id, "Execution Job failed.")
        return await self._verify_and_finalize(self.store.get_intent(intent_id), incident)

    async def _verify_and_finalize(
        self,
        intent: dict[str, Any],
        incident: dict[str, Any],
    ) -> dict[str, Any]:
        run_id = intent["run_id"]
        self.store.update_status(
            incident["id"],
            IncidentStatus.VERIFYING,
            "orchestrator",
        )
        self._checkpoint(
            run_id,
            incident["id"],
            "VERIFYING",
            {"tool_intent_id": intent["id"]},
        )
        verification = await self._verify(incident, run_id)
        self.store.add_trace(
            run_id,
            incident["id"],
            "verification",
            "slo.verify_recovery",
            "investigator",
            "OK" if verification["passed"] else "ERROR",
            verification["duration_ms"],
            verification,
        )
        if not verification["passed"]:
            return await self.rollback(
                intent["id"],
                "Post-change SLO verification failed.",
            )
        self.store.finish_run(run_id, "RESOLVED", 3298, 6)
        resolved = self.store.update_status(
            incident["id"],
            IncidentStatus.RESOLVED,
            "commander-agent",
            {"verification": "passed", "postmortem": "generated"},
        )
        report = await self._reporter_output(incident["id"], run_id)
        self.postmortems.generate(incident["id"], report)
        self._checkpoint(
            run_id,
            incident["id"],
            "RESOLVED",
            {"tool_intent_id": intent["id"], "verification": verification},
        )
        await self.event_stream.publish(
            "incident.resolved",
            incident["id"],
            {"run_id": run_id, "tool_intent_id": intent["id"]},
        )
        return resolved

    async def edit_intent(
        self,
        intent_id: str,
        arguments: dict[str, Any],
        reviewer: str,
        comment: str,
    ) -> dict[str, Any]:
        intent = self.store.get_intent(intent_id)
        allowed_arguments = {
            "kubernetes.patch_deployment": {
                "service",
                "namespace",
                "name",
                "container",
                "memory_limit",
                "cpu_limit",
            },
            "kubernetes.scale_deployment": {
                "service",
                "namespace",
                "name",
                "replicas",
            },
            "kubernetes.rollout_restart": {"service", "namespace", "name"},
        }.get(intent["tool_name"])
        if allowed_arguments is None:
            raise ValueError("This tool does not support editable arguments.")
        unexpected = set(arguments) - allowed_arguments
        if unexpected:
            raise ValueError(
                "Unrecognized or protected arguments: " + ", ".join(sorted(unexpected))
            )
        normalized = dict(intent["arguments"])
        normalized.update(arguments)
        for field in ("service", "namespace", "name"):
            if normalized.get(field) != intent["arguments"].get(field):
                raise ValueError(f"Protected target field {field!r} cannot be edited.")
        for field in ("memory_limit", "cpu_limit", "container"):
            value = normalized.get(field)
            if value is not None and (
                not isinstance(value, str)
                or len(value) > 80
                or not re.fullmatch(r"[A-Za-z0-9_.+-]+", value)
            ):
                raise ValueError(f"Argument {field!r} has an invalid value.")
        if "replicas" in normalized:
            replicas = normalized["replicas"]
            if (
                not isinstance(replicas, int)
                or isinstance(replicas, bool)
                or not 0 <= replicas <= 100
            ):
                raise ValueError("replicas must be an integer between 0 and 100.")
        updated = self.store.edit_intent(
            intent_id,
            normalized,
            reviewer,
            comment,
        )
        risk = classify_risk(
            updated["tool_name"],
            updated["environment"],
            normalized,
        )
        self.store.update_intent_risk(intent_id, str(risk))
        incident = self.store.get_incident(updated["incident_id"])
        policy_input = PolicySimulationRequest(
            environment=updated["environment"],
            tool=updated["tool_name"],
            resource=str(updated["resource"].get("name", "unknown")),
            risk_level=risk,
            has_rollback=bool(updated["rollback"]),
            edited=True,
            incident_severity=incident["severity"],
        ).model_dump(mode="json")
        decision = await self.policy.evaluate(policy_input)
        if decision["decision"] == "allow":
            decision = {
                "decision": "require_approval",
                "risk_level": str(risk),
                "matched_policy": "edited-intent-requires-fresh-approval",
                "reason": "Every edited intent requires a fresh human approval.",
                "backend": decision.get("backend", self.settings.policy_backend),
            }
        self.store.record_policy(
            intent_id,
            self.settings.policy_version,
            decision,
            policy_input,
        )
        self._checkpoint(
            updated["run_id"],
            updated["incident_id"],
            "INTENT_EDITED_AND_REEVALUATED",
            {
                "tool_intent_id": intent_id,
                "reviewer": reviewer,
                "decision": decision["decision"],
                "matched_policy": decision["matched_policy"],
            },
        )
        if decision["decision"] == "deny":
            self.store.update_status(
                updated["incident_id"],
                IncidentStatus.DENIED,
                "policy-gateway",
                {"matched_policy": decision["matched_policy"]},
            )
        else:
            self.store.update_status(
                updated["incident_id"],
                IncidentStatus.WAITING_APPROVAL,
                "policy-gateway",
                {
                    "tool_intent_id": intent_id,
                    "matched_policy": decision["matched_policy"],
                },
            )
        return self.store.get_intent(intent_id)

    async def rollback(self, intent_id: str, reason: str) -> dict[str, Any]:
        intent = self.store.get_intent(intent_id)
        incident = self.store.get_incident(intent["incident_id"])
        run_id = intent["run_id"]
        self._checkpoint(
            run_id,
            incident["id"],
            "ROLLBACK_STARTED",
            {"tool_intent_id": intent_id, "reason": reason},
        )
        self.store.update_status(
            incident["id"],
            IncidentStatus.ROLLING_BACK,
            "tool-gateway",
            {"tool_intent_id": intent_id, "reason": reason},
        )
        context = ToolContext(
            incident_id=incident["id"],
            run_id=run_id,
            actor="compensation-controller",
            idempotency_key=f"{intent['idempotency_key']}-rollback",
        )
        rollback_method = getattr(self.transport, "rollback", None)
        if rollback_method:
            result = await rollback_method(intent["tool_name"], intent["rollback"], context)
        else:
            result = await self.transport.call_tool(
                intent["tool_name"],
                intent["rollback"],
                context,
            )
        self.store.record_rollback(
            intent_id,
            {
                "ok": result.ok,
                "reason": reason,
                "source_uri": result.source_uri,
                "result": result.data,
            },
        )
        self.store.add_trace(
            run_id,
            incident["id"],
            "compensation",
            f"{intent['tool_name']}.rollback",
            "compensation-controller",
            "OK" if result.ok else "ERROR",
            result.duration_ms,
            {"reason": reason, "source_uri": result.source_uri},
        )
        status = IncidentStatus.ROLLED_BACK if result.ok else IncidentStatus.HUMAN_HANDOFF
        self.store.finish_run(
            run_id,
            "ROLLED_BACK" if result.ok else "HUMAN_HANDOFF",
            3298,
            7,
        )
        updated = self.store.update_status(
            incident["id"],
            status,
            "compensation-controller",
            {"rollback": "succeeded" if result.ok else "failed", "reason": reason},
        )
        report = await self._reporter_output(incident["id"], run_id)
        self.postmortems.generate(incident["id"], report)
        self._checkpoint(
            run_id,
            incident["id"],
            "ROLLED_BACK" if result.ok else "ROLLBACK_FAILED",
            {"tool_intent_id": intent_id, "ok": result.ok, "reason": reason},
        )
        await self.event_stream.publish(
            "incident.rollback_completed",
            incident["id"],
            {"run_id": run_id, "ok": result.ok, "reason": reason},
        )
        return updated

    async def resume(self, incident_id: str) -> dict[str, Any]:
        lock_name = f"incident:{incident_id}"
        token = await self.event_stream.acquire_lock(lock_name)
        if token is None:
            raise WorkflowLocked("Incident recovery is already active on another worker.")
        try:
            return await self._resume_unlocked(incident_id)
        finally:
            await self.event_stream.release_lock(lock_name, token)

    async def _resume_unlocked(self, incident_id: str) -> dict[str, Any]:
        lock = self._locks.setdefault(incident_id, asyncio.Lock())
        async with lock:
            incident = self.store.get_incident(incident_id)
            run_id = incident.get("current_run_id")
            if not run_id:
                should_start = True
            else:
                should_start = False
            if should_start:
                pass
            else:
                status = incident["status"]
                intents = incident.get("tool_intents", [])
                intent = intents[0] if intents else None
                if status == "WAITING_APPROVAL":
                    return incident
                if status in {"EXECUTING", "VERIFYING"} and intent:
                    return await self.execute(intent["id"])
                if status == "ROLLING_BACK" and intent:
                    return await self.rollback(intent["id"], "Resumed interrupted rollback.")
                if status in {"RESOLVED", "DENIED", "ROLLED_BACK", "CANCELLED"}:
                    return incident
                self.store.finish_run(run_id, "SUPERSEDED", 0, 0)
                self._checkpoint(
                    run_id,
                    incident_id,
                    "SUPERSEDED_FOR_SAFE_REPLAN",
                    {"previous_status": status},
                )
                self.store.update_status(
                    incident_id,
                    IncidentStatus.NEW,
                    "recovery-controller",
                    {"reason": "Resuming from the last pre-execution safe checkpoint."},
                )
        return await self._start_unlocked(incident_id)

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
        await self.event_stream.publish(
            "incident.status_changed",
            incident_id,
            {"status": str(status), "actor": actor, **(attributes or {})},
        )
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
        self._checkpoint(
            run_id,
            incident_id,
            str(status),
            {"actor": actor, **(attributes or {})},
        )

    def _checkpoint(
        self,
        run_id: str,
        incident_id: str,
        phase: str,
        state: dict[str, Any] | None = None,
    ) -> None:
        self.store.checkpoint_workflow(run_id, incident_id, phase, state)

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
                    "namespace": self._target_namespace(incident["environment"]),
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
        evidence_ids = self.store.add_evidence(incident["id"], collected)
        await self.evidence_index.index(
            evidence_ids,
            [item["content"] for item in collected],
        )
        return evidence_ids

    @staticmethod
    def _summarize_result(tool_name: str, data: dict[str, Any]) -> str:
        if data.get("error"):
            return f"{tool_name} failed: {data['error']}"
        if tool_name == "prometheus.query":
            baseline = data.get("baseline")
            suffix = f", up from a {baseline}s baseline" if baseline is not None else ""
            return f"P95 latency is {data.get('value', 0)}s{suffix}."
        if tool_name == "kubernetes.get_events":
            if "events" in data:
                reasons = sorted({item.get("reason", "Unknown") for item in data["events"]})
                return f"{data.get('event_count', 0)} Kubernetes events: {', '.join(reasons)}."
            return (
                f"{data['pods_affected']} pods report {data['restart_count']} OOMKilled "
                f"restarts with a {data['memory_limit']} memory limit."
            )
        if tool_name == "loki.query":
            return f"{data.get('matches', 0)} correlated error log lines were found."
        if "deployments" in data:
            deployments = data["deployments"]
            if not deployments:
                return "No recent GitHub deployments matched the incident environment."
            latest = deployments[0]
            return f"Latest deployment {latest['sha'][:8]} was created at {latest['created_at']}."
        return (
            f"Deployment {data['commit']} changed {data['change']} "
            f"{data['deployed_minutes_before_alert']} minutes before the alert."
        )

    async def _verify(self, incident: dict[str, Any], run_id: str) -> dict[str, Any]:
        if self.settings.verification_url_template:
            url = self.settings.verification_url_template.format(
                service=incident["service"],
                namespace=self._target_namespace(incident["environment"]),
            )
            started = asyncio.get_running_loop().time()
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(url)
                    body = response.json() if response.headers.get("content-type", "").startswith(
                        "application/json"
                    ) else {}
                passed = response.is_success and body.get("status", "ok") == "ok"
            except (httpx.HTTPError, ValueError) as exc:
                passed = False
                body = {"error": str(exc)}
            return {
                "passed": passed,
                "duration_ms": int(
                    (asyncio.get_running_loop().time() - started) * 1000
                ),
                "verification_url": url,
                "response": body,
                "mode": "service-health",
            }
        if self.settings.connector_mode != "production":
            return {
                "passed": True,
                "duration_ms": 1203,
                "p95_before_seconds": 2.84,
                "p95_after_seconds": 0.41,
                "restart_count_after": 0,
                "mode": "simulation",
            }
        context = ToolContext(
            incident_id=incident["id"],
            run_id=run_id,
            actor="verification-controller",
            idempotency_key=f"{incident['id'].lower()}-verify",
        )
        result = await self.transport.call_tool(
            "prometheus.query",
            {
                "service": incident["service"],
                "environment": incident["environment"],
                "namespace": self._target_namespace(incident["environment"]),
            },
            context,
        )
        p95 = float(result.data.get("value", float("inf"))) if result.ok else float("inf")
        return {
            "passed": result.ok and p95 <= 1.0,
            "duration_ms": result.duration_ms,
            "p95_after_seconds": p95,
            "threshold_seconds": 1.0,
            "source_uri": result.source_uri,
            "mode": "production",
        }

    async def _reporter_output(
        self,
        incident_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        if not self.orchestrator:
            return None
        try:
            return await self.orchestrator.generate_report(
                self.store.get_incident(incident_id)
            )
        except Exception as exc:
            self.store.add_trace(
                run_id,
                incident_id,
                "llm",
                "reporter.generate_postmortem",
                "reporter",
                "ERROR",
                0,
                {"error": str(exc)},
            )
            return None

    @staticmethod
    def _infer_cause(incident: dict[str, Any]) -> str:
        return (
            f"{incident['service']} memory limit is too low after the most recent deployment, "
            "causing repeated OOM kills and elevated tail latency."
        )

    def _target_namespace(self, environment: str) -> str:
        if environment in self.settings.kubernetes_allowed_namespaces:
            return environment
        if environment.lower() in {
            "production",
            "prod",
            "staging",
            "stage",
            "development",
            "dev",
            "test",
        }:
            return self.settings.kubernetes_namespace
        return self.settings.kubernetes_namespace
