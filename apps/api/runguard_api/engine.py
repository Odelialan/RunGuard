from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar
from weakref import WeakValueDictionary

import httpx

from .config import Settings, parse_target_inventory
from .embeddings import EvidenceIndexer
from .event_stream import EventStream, LockLeaseLost, LockUnavailable
from .gateway import ToolContext, build_transport
from .models import IncidentStatus, PolicySimulationRequest
from .orchestration import LangGraphOrchestrator
from .policy import PolicyEvaluator, classify_risk, has_effective_rollback
from .postmortem import PostmortemService
from .store import Store

P = ParamSpec("P")
R = TypeVar("R")


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
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._recovery_task: asyncio.Task[None] | None = None
        self.target_inventory = parse_target_inventory(settings.target_inventory_json)

    async def _db(
        self,
        method: Callable[P, R],
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        return await asyncio.to_thread(method, *args, **kwargs)

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
                    incident = await self._db(self.store.get_incident, incident_id)
                    run_id = incident.get("current_run_id")
                    if run_id:
                        await self._db(
                            self.store.add_trace,
                            run_id,
                            incident_id,
                            "recovery",
                            "workflow.resume",
                            "recovery-controller",
                            "ERROR",
                            0,
                            {"error": str(exc)},
                        )
                    await self._db(
                        self.store.update_status,
                        incident_id,
                        IncidentStatus.HUMAN_HANDOFF,
                        "recovery-controller",
                        {"reason": "Automatic recovery failed closed."},
                    )

        recoverable = await self._db(self.store.list_recoverable_incidents)
        await asyncio.gather(*(recover_one(incident_id) for incident_id in recoverable))

    async def start(self, incident_id: str) -> dict[str, Any]:
        try:
            return await self.event_stream.run_with_lock(
                f"incident:{incident_id}",
                1800,
                lambda: self._start_unlocked(incident_id),
            )
        except (LockUnavailable, LockLeaseLost) as exc:
            raise WorkflowLocked(
                "Incident workflow lock is unavailable or its lease was lost."
            ) from exc

    async def _start_unlocked(self, incident_id: str) -> dict[str, Any]:
        lock = self._locks.setdefault(incident_id, asyncio.Lock())
        async with lock:
            incident = await self._db(self.store.get_incident, incident_id)
            if incident["status"] not in {"NEW", "INVESTIGATING", "HUMAN_HANDOFF"}:
                return incident
            target = self._target_binding(incident)

            run_id = await self._db(
                self.store.create_run,
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
            await self._checkpoint(run_id, incident_id, "RUN_CREATED")
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

            evidence_ids = await self._collect_evidence(incident, run_id, target)
            current_evidence = (
                await self._db(self.store.get_incident, incident_id)
            )["evidence"]
            successful_sources = {
                item["source_type"]
                for item in current_evidence
                if item.get("metadata", {}).get("ok") is True
            }
            if len(successful_sources) < 2 or "kubernetes" not in successful_sources:
                await self._db(self.store.finish_run, run_id, "HUMAN_HANDOFF", 0, 4)
                return await self._db(
                    self.store.update_status,
                    incident_id,
                    IncidentStatus.HUMAN_HANDOFF,
                    "investigator-agent",
                    {
                        "reason": (
                            "Minimum evidence quorum was not met; Kubernetes evidence and "
                            "one independent source are required."
                        )
                    },
                )
            await self._checkpoint(
                run_id,
                incident_id,
                "EVIDENCE_COLLECTED",
                {"evidence_ids": evidence_ids},
            )
            graph_output: dict[str, Any] | None = None
            if self.orchestrator:
                try:
                    enriched = await self._db(self.store.get_incident, incident_id)
                    graph_output = await self.orchestrator.run(
                        enriched,
                        enriched["evidence"],
                        run_id,
                    )
                    await self._checkpoint(
                        run_id,
                        incident_id,
                        "LANGGRAPH_COMPLETED",
                        await self.orchestrator.checkpoint(run_id) or {},
                    )
                except Exception as exc:
                    await self._db(
                        self.store.add_trace,
                        run_id,
                        incident_id,
                        "llm",
                        "langgraph.incident_response",
                        "orchestrator",
                        "ERROR",
                        0,
                        {"error": str(exc)},
                    )
                    await self._db(
                        self.store.finish_run, run_id, "HUMAN_HANDOFF", 0, 4
                    )
                    return await self._db(
                        self.store.update_status,
                        incident_id,
                        IncidentStatus.HUMAN_HANDOFF,
                        "orchestrator",
                        {"reason": "LangGraph or model provider failed closed."},
                    )
            investigation = (graph_output or {}).get("investigation", {})
            cause = investigation.get("root_cause") or self._infer_cause(incident)
            confidence = float(investigation.get("confidence", 0.91))
            linked_evidence = investigation.get("evidence_ids") or evidence_ids
            invalid_evidence = set(linked_evidence) - set(evidence_ids)
            if invalid_evidence:
                await self._db(self.store.finish_run, run_id, "HUMAN_HANDOFF", 0, 4)
                return await self._db(
                    self.store.update_status,
                    incident_id,
                    IncidentStatus.HUMAN_HANDOFF,
                    "investigator-agent",
                    {"reason": "Investigation cited evidence outside this incident."},
                )
            await self._db(
                self.store.add_hypothesis,
                incident_id,
                cause,
                confidence,
                linked_evidence,
            )
            await self._db(
                self.store.add_trace,
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
            try:
                arguments = self._normalize_tool_arguments(
                    tool_name,
                    remediation.get("arguments", {"memory_limit": "1Gi"}),
                    target,
                )
                rollback = self._normalize_tool_arguments(
                    tool_name,
                    remediation.get("rollback", {"memory_limit": "256Mi"}),
                    target,
                    rollback=True,
                )
                arguments, rollback = await self._capture_prechange_state(
                    tool_name,
                    arguments,
                    target,
                    incident,
                    run_id,
                )
            except ValueError as exc:
                await self._db(
                    self.store.add_trace,
                    run_id,
                    incident_id,
                    "policy",
                    "intent.normalize",
                    "policy-gateway",
                    "ERROR",
                    0,
                    {"error": str(exc)},
                )
                await self._db(self.store.finish_run, run_id, "HUMAN_HANDOFF", 0, 4)
                return await self._db(
                    self.store.update_status,
                    incident_id,
                    IncidentStatus.HUMAN_HANDOFF,
                    "policy-gateway",
                    {"reason": str(exc)},
                )
            risk = classify_risk(
                tool_name,
                incident["environment"],
                arguments,
            )
            intent = await self._db(
                self.store.create_intent,
                run_id=run_id,
                incident_id=incident_id,
                tool_name=tool_name,
                environment=incident["environment"],
                resource={**target, "kind": "Deployment"},
                arguments=arguments,
                rollback=rollback,
                risk_level=str(risk),
            )
            await self._checkpoint(
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
                resource=intent["resource"],
                arguments=intent["arguments"],
                rollback=intent["rollback"],
                risk_level=risk,
                has_rollback=self._has_effective_rollback(intent["rollback"]),
                incident_severity=incident["severity"],
            ).model_dump(mode="json")
            decision = await self.policy.evaluate(policy_input)
            review = (graph_output or {}).get("review", {})
            if self.orchestrator:
                review_decision = str(review.get("decision", "")).strip().lower()
                reviewer_backend = (
                    "a2a-reviewer"
                    if self.settings.a2a_reviewer_url
                    else "langgraph-reviewer"
                )
                if review_decision == "deny":
                    decision = {
                        "decision": "deny",
                        "risk_level": str(risk),
                        "matched_policy": "independent-reviewer-denied",
                        "reason": review.get(
                            "reason",
                            "Independent reviewer rejected the plan.",
                        ),
                        "backend": reviewer_backend,
                    }
                elif (
                    review_decision == "require_human_approval"
                    and decision["decision"] != "deny"
                ):
                    decision = {
                        "decision": "require_approval",
                        "risk_level": str(risk),
                        "matched_policy": "independent-reviewer-requires-human",
                        "reason": review.get(
                            "reason",
                            "Independent reviewer requires a human approver.",
                        ),
                        "backend": reviewer_backend,
                    }
                elif review_decision != "approve":
                    decision = {
                        "decision": "deny",
                        "risk_level": str(risk),
                        "matched_policy": "independent-reviewer-invalid-fail-closed",
                        "reason": "Independent reviewer returned no valid decision.",
                        "backend": reviewer_backend,
                    }
            await self._db(
                self.store.record_policy,
                intent["id"],
                self.settings.policy_version,
                decision,
                policy_input,
            )
            await self._checkpoint(
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
                await self._db(self.store.finish_run, run_id, "DENIED", 2860, 4)
                await self._checkpoint(run_id, incident_id, "DENIED")
                return await self._db(
                    self.store.update_status,
                    incident_id,
                    IncidentStatus.DENIED,
                    "policy-gateway",
                    {"matched_policy": decision["matched_policy"]},
                )
            if decision["decision"] == "require_approval":
                await self._db(
                    self.store.add_trace,
                    run_id,
                    incident_id,
                    "approval",
                    "human.interrupt",
                    "orchestrator",
                    "PENDING",
                    3,
                    {"tool_intent_id": intent["id"]},
                )
                await self._db(
                    self.store.finish_run, run_id, "WAITING_APPROVAL", 2860, 4
                )
                await self._checkpoint(
                    run_id,
                    incident_id,
                    "WAITING_APPROVAL",
                    {"tool_intent_id": intent["id"]},
                )
                return await self._db(
                    self.store.update_status,
                    incident_id,
                    IncidentStatus.WAITING_APPROVAL,
                    "policy-gateway",
                    {"tool_intent_id": intent["id"]},
                )
            await self._db(
                self.store.decide_approval,
                intent["id"],
                "policy-gateway",
                "approved",
                "Auto-approved by reversible staging policy.",
            )
            return await self.execute(intent["id"])

    async def execute(self, intent_id: str) -> dict[str, Any]:
        intent = await self._db(self.store.get_intent, intent_id)
        resource_lock_key = self._resource_lock_key(intent)
        local_lock = self._locks.setdefault(resource_lock_key, asyncio.Lock())
        async with local_lock:
            try:
                return await self.event_stream.run_with_lock(
                    resource_lock_key,
                    600,
                    lambda: self._execute_unlocked(intent_id),
                )
            except (LockUnavailable, LockLeaseLost) as exc:
                raise WorkflowLocked(
                    "Target resource lock is unavailable or its lease was lost."
                ) from exc

    async def _execute_unlocked(self, intent_id: str) -> dict[str, Any]:
        intent = await self._db(self.store.get_intent, intent_id)
        incident = await self._db(self.store.get_incident, intent["incident_id"])
        if intent["status"] == "EXECUTED":
            return await self._verify_and_finalize(intent, incident)
        if intent["status"] == "EXECUTION_FAILED":
            await self._db(
                self.store.finish_run, intent["run_id"], "HUMAN_HANDOFF", 0, 5
            )
            return await self._db(
                self.store.update_status,
                incident["id"],
                IncidentStatus.HUMAN_HANDOFF,
                "recovery-controller",
                {"reason": "Recovered a recorded failed execution; no retry was attempted."},
            )
        intent = await self._db(self.store.claim_execution, intent_id)
        run_id = intent["run_id"]
        await self._checkpoint(
            run_id,
            incident["id"],
            "EXECUTION_STARTED",
            {"tool_intent_id": intent_id},
        )
        await self._db(
            self.store.update_status,
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
        raw_before = runner_result.get("before")
        before = dict(raw_before) if isinstance(raw_before, dict) else {}
        if result.ok and runner_result.get("resource_version"):
            before["expected_resource_version"] = str(
                runner_result["resource_version"]
            )
        after = runner_result.get("after") or {
            key: value
            for key, value in intent["arguments"].items()
            if key not in {"service", "namespace", "name"}
        }
        await self._db(
            self.store.record_execution,
            intent_id,
            result.data,
            before,
            after,
            result.duration_ms,
            succeeded=result.ok,
        )
        await self._checkpoint(
            run_id,
            incident["id"],
            "EXECUTION_RECORDED",
            {
                "tool_intent_id": intent_id,
                "ok": result.ok,
                "source_uri": result.source_uri,
            },
        )
        await self._db(
            self.store.add_trace,
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
            await self._db(self.store.finish_run, run_id, "HUMAN_HANDOFF", 0, 5)
            await self._checkpoint(
                run_id,
                incident["id"],
                "EXECUTION_FAILED_UNCERTAIN",
                {
                    "tool_intent_id": intent_id,
                    "reason": "Execution failed; automatic compensation was not attempted.",
                },
            )
            updated = await self._db(
                self.store.update_status,
                incident["id"],
                IncidentStatus.HUMAN_HANDOFF,
                "tool-gateway",
                {
                    "reason": (
                        "Execution failed with unknown mutation state; automatic compensation "
                        "was suppressed."
                    )
                },
            )
            report = await self._reporter_output(incident["id"], run_id)
            await self._db(self.postmortems.generate, incident["id"], report)
            return updated
        latest_intent = await self._db(self.store.get_intent, intent_id)
        return await self._verify_and_finalize(latest_intent, incident)

    async def _verify_and_finalize(
        self,
        intent: dict[str, Any],
        incident: dict[str, Any],
    ) -> dict[str, Any]:
        run_id = intent["run_id"]
        await self._db(
            self.store.update_status,
            incident["id"],
            IncidentStatus.VERIFYING,
            "orchestrator",
        )
        await self._checkpoint(
            run_id,
            incident["id"],
            "VERIFYING",
            {"tool_intent_id": intent["id"]},
        )
        verification = await self._verify(incident, run_id)
        await self._db(
            self.store.add_trace,
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
            latest_intent = await self._db(self.store.get_intent, intent["id"])
            if not self._has_effective_rollback(latest_intent["rollback"]):
                await self._db(self.store.finish_run, run_id, "HUMAN_HANDOFF", 0, 6)
                await self._checkpoint(
                    run_id,
                    incident["id"],
                    "VERIFICATION_FAILED_WITHOUT_COMPENSATION",
                    {"tool_intent_id": intent["id"], "verification": verification},
                )
                updated = await self._db(
                    self.store.update_status,
                    incident["id"],
                    IncidentStatus.HUMAN_HANDOFF,
                    "verification-controller",
                    {
                        "reason": (
                            "Verification failed and no state-restoring compensation exists."
                        )
                    },
                )
                report = await self._reporter_output(incident["id"], run_id)
                await self._db(self.postmortems.generate, incident["id"], report)
                return updated
            return await self.rollback(
                intent["id"],
                "Post-change SLO verification failed.",
            )
        await self._db(self.store.finish_run, run_id, "RESOLVED", 3298, 6)
        resolved = await self._db(
            self.store.update_status,
            incident["id"],
            IncidentStatus.RESOLVED,
            "commander-agent",
            {"verification": "passed", "postmortem": "generated"},
            stream_event=(
                "incident.resolved",
                {"run_id": run_id, "tool_intent_id": intent["id"]},
            ),
        )
        report = await self._reporter_output(incident["id"], run_id)
        await self._db(self.postmortems.generate, incident["id"], report)
        await self._checkpoint(
            run_id,
            incident["id"],
            "RESOLVED",
            {"tool_intent_id": intent["id"], "verification": verification},
        )
        return resolved

    async def edit_intent(
        self,
        intent_id: str,
        arguments: dict[str, Any],
        reviewer: str,
        comment: str,
    ) -> dict[str, Any]:
        intent = await self._db(self.store.get_intent, intent_id)
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
        self._validate_mutation_arguments(normalized, rollback=False)
        updated = await self._db(
            self.store.edit_intent,
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
        await self._db(self.store.update_intent_risk, intent_id, str(risk))
        incident = await self._db(
            self.store.get_incident, updated["incident_id"]
        )
        policy_input = PolicySimulationRequest(
            environment=updated["environment"],
            tool=updated["tool_name"],
            resource=updated["resource"],
            arguments=normalized,
            rollback=updated["rollback"],
            risk_level=risk,
            has_rollback=self._has_effective_rollback(updated["rollback"]),
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
        await self._db(
            self.store.record_policy,
            intent_id,
            self.settings.policy_version,
            decision,
            policy_input,
        )
        await self._checkpoint(
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
            await self._db(
                self.store.update_status,
                updated["incident_id"],
                IncidentStatus.DENIED,
                "policy-gateway",
                {"matched_policy": decision["matched_policy"]},
            )
        else:
            await self._db(
                self.store.update_status,
                updated["incident_id"],
                IncidentStatus.WAITING_APPROVAL,
                "policy-gateway",
                {
                    "tool_intent_id": intent_id,
                    "matched_policy": decision["matched_policy"],
                },
            )
        return await self._db(self.store.get_intent, intent_id)

    async def rollback(self, intent_id: str, reason: str) -> dict[str, Any]:
        intent = await self._db(self.store.get_intent, intent_id)
        incident = await self._db(self.store.get_incident, intent["incident_id"])
        if intent["status"] == "ROLLED_BACK":
            return await self._db(
                self.store.update_status,
                incident["id"],
                IncidentStatus.ROLLED_BACK,
                "compensation-controller",
                {"rollback": "succeeded", "reason": "Recovered completed compensation."},
            )
        if intent["status"] == "ROLLBACK_FAILED":
            return await self._db(
                self.store.update_status,
                incident["id"],
                IncidentStatus.HUMAN_HANDOFF,
                "compensation-controller",
                {"rollback": "failed", "reason": "Recovered failed compensation."},
            )
        if intent["status"] != "EXECUTED":
            raise ValueError(f"Tool intent cannot be compensated: {intent['status']}")
        if not self._has_effective_rollback(intent["rollback"]):
            raise ValueError("Tool intent has no state-restoring compensation.")
        run_id = intent["run_id"]
        await self._checkpoint(
            run_id,
            incident["id"],
            "ROLLBACK_STARTED",
            {"tool_intent_id": intent_id, "reason": reason},
        )
        await self._db(
            self.store.update_status,
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
        await self._db(
            self.store.record_rollback,
            intent_id,
            {
                "ok": result.ok,
                "reason": reason,
                "source_uri": result.source_uri,
                "result": result.data,
            },
        )
        await self._db(
            self.store.add_trace,
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
        await self._db(
            self.store.finish_run,
            run_id,
            "ROLLED_BACK" if result.ok else "HUMAN_HANDOFF",
            3298,
            7,
        )
        updated = await self._db(
            self.store.update_status,
            incident["id"],
            status,
            "compensation-controller",
            {"rollback": "succeeded" if result.ok else "failed", "reason": reason},
            stream_event=(
                "incident.rollback_completed",
                {"run_id": run_id, "ok": result.ok, "reason": reason},
            ),
        )
        report = await self._reporter_output(incident["id"], run_id)
        await self._db(self.postmortems.generate, incident["id"], report)
        await self._checkpoint(
            run_id,
            incident["id"],
            "ROLLED_BACK" if result.ok else "ROLLBACK_FAILED",
            {"tool_intent_id": intent_id, "ok": result.ok, "reason": reason},
        )
        return updated

    async def resume(self, incident_id: str) -> dict[str, Any]:
        try:
            return await self.event_stream.run_with_lock(
                f"incident:{incident_id}",
                1800,
                lambda: self._resume_unlocked(incident_id),
            )
        except (LockUnavailable, LockLeaseLost) as exc:
            raise WorkflowLocked(
                "Incident recovery lock is unavailable or its lease was lost."
            ) from exc

    async def _resume_unlocked(self, incident_id: str) -> dict[str, Any]:
        lock = self._locks.setdefault(incident_id, asyncio.Lock())
        async with lock:
            incident = await self._db(self.store.get_incident, incident_id)
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
                    if intent and intent["status"] == "APPROVED":
                        return await self.execute(intent["id"])
                    return incident
                if status in {"EXECUTING", "VERIFYING"} and intent:
                    return await self.execute(intent["id"])
                if status == "ROLLING_BACK" and intent:
                    return await self.rollback(intent["id"], "Resumed interrupted rollback.")
                if status in {"RESOLVED", "DENIED", "ROLLED_BACK", "CANCELLED"}:
                    return incident
                await self._db(self.store.finish_run, run_id, "SUPERSEDED", 0, 0)
                await self._checkpoint(
                    run_id,
                    incident_id,
                    "SUPERSEDED_FOR_SAFE_REPLAN",
                    {"previous_status": status},
                )
                await self._db(
                    self.store.update_status,
                    incident_id,
                    IncidentStatus.NEW,
                    "recovery-controller",
                    {"reason": "Resuming from the last pre-execution safe checkpoint."},
                )
        return await self._start_unlocked(incident_id)

    async def replay(self, incident_id: str) -> dict[str, Any]:
        incident = await self._db(self.store.get_incident, incident_id)
        run_id = incident.get("current_run_id")
        if not run_id:
            raise ValueError("No run is available to replay.")
        original = await self._db(self.store.get_run, run_id)
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
        await self._db(self.store.update_status, incident_id, status, actor)
        await self._db(
            self.store.add_trace,
            run_id,
            incident_id,
            span_type,
            span_name,
            actor.replace("-agent", ""),
            "OK",
            duration_ms,
            attributes,
        )
        await self._checkpoint(
            run_id,
            incident_id,
            str(status),
            {"actor": actor, **(attributes or {})},
        )

    async def _checkpoint(
        self,
        run_id: str,
        incident_id: str,
        phase: str,
        state: dict[str, Any] | None = None,
    ) -> None:
        await self._db(
            self.store.checkpoint_workflow,
            run_id,
            incident_id,
            phase,
            state,
        )

    async def _collect_evidence(
        self,
        incident: dict[str, Any],
        run_id: str,
        target: dict[str, str],
    ) -> list[str]:
        calls = [
            ("prometheus.query", "Latency increase", "prometheus"),
            ("kubernetes.get_events", "Workload events", "kubernetes"),
            ("kubernetes.get_deployment", "Deployment state", "kubernetes"),
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
                    "namespace": target["namespace"],
                },
                context,
            )
            collected.append(
                {
                    "source_type": source_type,
                    "source_uri": result.source_uri,
                    "title": title,
                    "content": self._summarize_result(tool_name, result.data),
                    "metadata": {
                        "raw": result.data,
                        "duration_ms": result.duration_ms,
                        "ok": result.ok,
                    },
                }
            )
            await self._db(
                self.store.add_trace,
                run_id,
                incident["id"],
                "retrieval" if source_type in {"prometheus", "loki"} else "tool",
                tool_name,
                "investigator",
                "OK" if result.ok else "ERROR",
                result.duration_ms,
                {"source_uri": result.source_uri},
            )
        evidence_ids = await self._db(
            self.store.add_evidence, incident["id"], collected
        )
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
        if tool_name == "kubernetes.get_deployment":
            return (
                f"Deployment {data.get('namespace', 'unknown')}/{data.get('name', 'unknown')} "
                f"has {data.get('ready_replicas', 0)}/{data.get('replicas', 0)} ready replicas."
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
                namespace=self._target_binding(incident)["namespace"],
            )
            started = asyncio.get_running_loop().time()
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(url)
                    content_type = response.headers.get("content-type", "").lower()
                    if not content_type.startswith("application/json"):
                        raise ValueError(
                            "Verification endpoint did not return application/json."
                        )
                    body = response.json()
                    if not isinstance(body, dict):
                        raise ValueError(
                            "Verification endpoint returned a non-object JSON payload."
                        )
                passed = (
                    response.is_success
                    and str(body.get("status", "")).strip().lower()
                    in {"ok", "healthy"}
                )
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
        if self.settings.connector_mode in {"mock", "hybrid"}:
            return {
                "passed": True,
                "duration_ms": 1203,
                "p95_before_seconds": 2.84,
                "p95_after_seconds": 0.41,
                "restart_count_after": 0,
                "mode": "simulation",
            }
        target = self._target_binding(incident)

        async def verify_tool(
            tool_name: str,
            arguments: dict[str, Any],
            suffix: str,
        ):
            return await self.transport.call_tool(
                tool_name,
                arguments,
                ToolContext(
                    incident_id=incident["id"],
                    run_id=run_id,
                    actor="verification-controller",
                    idempotency_key=f"{incident['id'].lower()}-verify-{suffix}",
                ),
            )

        base_arguments = {
            "service": incident["service"],
            "environment": incident["environment"],
            "namespace": target["namespace"],
            "name": target["name"],
        }
        latency_result, error_result, workload_result = await asyncio.gather(
            verify_tool("prometheus.query", base_arguments, "latency"),
            verify_tool(
                "prometheus.query",
                {
                    **base_arguments,
                    "query": (
                        "sum(rate(http_server_requests_total"
                        f'{{service="{incident["service"]}",status=~"5.."}}[5m])) / '
                        "clamp_min(sum(rate(http_server_requests_total"
                        f'{{service="{incident["service"]}"}}[5m])), 0.001)'
                    ),
                },
                "errors",
            ),
            verify_tool("kubernetes.get_deployment", base_arguments, "workload"),
        )

        latency_data = (
            latency_result.data if isinstance(latency_result.data, dict) else {}
        )
        error_data = error_result.data if isinstance(error_result.data, dict) else {}
        workload_data = (
            workload_result.data if isinstance(workload_result.data, dict) else {}
        )
        try:
            p95 = float(latency_data["value"]) if latency_result.ok else float("inf")
        except (KeyError, TypeError, ValueError):
            p95 = float("inf")
        try:
            error_rate = float(error_data["value"]) if error_result.ok else float("inf")
        except (KeyError, TypeError, ValueError):
            error_rate = float("inf")
        latency_series = latency_data.get("series")
        error_series = error_data.get("series")
        metrics_valid = (
            latency_result.ok
            and error_result.ok
            and math.isfinite(p95)
            and math.isfinite(error_rate)
            and isinstance(latency_series, int)
            and not isinstance(latency_series, bool)
            and latency_series > 0
            and isinstance(error_series, int)
            and not isinstance(error_series, bool)
            and error_series > 0
        )
        desired = workload_data.get("replicas")
        ready = workload_data.get("ready_replicas")
        workload_stable = (
            workload_result.ok
            and isinstance(desired, int)
            and not isinstance(desired, bool)
            and desired > 0
            and isinstance(ready, int)
            and not isinstance(ready, bool)
            and ready >= desired
        )
        return {
            "passed": metrics_valid and workload_stable and p95 <= 1.0 and error_rate <= 0.05,
            "duration_ms": max(
                latency_result.duration_ms,
                error_result.duration_ms,
                workload_result.duration_ms,
            ),
            "p95_after_seconds": p95,
            "error_rate_after": error_rate,
            "latency_series": latency_series,
            "error_series": error_series,
            "metrics_valid": metrics_valid,
            "workload_stable": workload_stable,
            "ready_replicas": ready,
            "desired_replicas": desired,
            "threshold_seconds": 1.0,
            "error_rate_threshold": 0.05,
            "source_uris": [
                latency_result.source_uri,
                error_result.source_uri,
                workload_result.source_uri,
            ],
            "mode": self.settings.connector_mode,
        }

    async def _reporter_output(
        self,
        incident_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        if not self.orchestrator:
            return None
        try:
            incident = await self._db(self.store.get_incident, incident_id)
            return await self.orchestrator.generate_report(incident)
        except Exception as exc:
            await self._db(
                self.store.add_trace,
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

    def _target_binding(self, incident: dict[str, Any]) -> dict[str, str]:
        service = str(incident["service"]).strip()
        environment = str(incident["environment"]).strip().lower()
        target = self.target_inventory.get(service)
        if target:
            if environment != target["environment"]:
                raise ValueError(
                    f"Incident environment {environment!r} does not match the authoritative "
                    f"target inventory environment {target['environment']!r}."
                )
            return {"service": service, **target}
        if self.settings.enforce_production_guards:
            raise ValueError(f"Service {service!r} is absent from the target inventory.")
        return {
            "service": service,
            "environment": environment,
            "namespace": self.settings.kubernetes_namespace,
            "name": service,
        }

    async def _capture_prechange_state(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        target: dict[str, str],
        incident: dict[str, Any],
        run_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        context = ToolContext(
            incident_id=incident["id"],
            run_id=run_id,
            actor="preflight-controller",
            idempotency_key=f"{incident['id'].lower()}-{run_id.lower()}-preflight",
        )
        result = await self.transport.call_tool(
            "kubernetes.get_deployment",
            dict(target),
            context,
        )
        if not result.ok or not isinstance(result.data, dict):
            raise ValueError(
                "Authoritative pre-change Deployment snapshot is unavailable."
            )
        snapshot = result.data
        if (
            snapshot.get("name") != target["name"]
            or snapshot.get("namespace") != target["namespace"]
        ):
            raise ValueError(
                "Pre-change Deployment snapshot does not match the bound target."
            )
        resource_version = str(snapshot.get("resource_version") or "").strip()
        if not resource_version:
            raise ValueError(
                "Pre-change Deployment snapshot has no resourceVersion."
            )

        bound_arguments = dict(arguments)
        bound_arguments["expected_resource_version"] = resource_version
        rollback: dict[str, Any] = dict(target)
        if tool_name == "kubernetes.patch_deployment":
            containers = snapshot.get("containers")
            if not isinstance(containers, list) or not containers:
                raise ValueError(
                    "Pre-change Deployment snapshot has no container state."
                )
            requested_container = bound_arguments.get("container")
            container = next(
                (
                    item
                    for item in containers
                    if isinstance(item, dict)
                    and (
                        requested_container is None
                        or item.get("name") == requested_container
                    )
                ),
                None,
            )
            if not container or not isinstance(container.get("name"), str):
                raise ValueError(
                    "Requested container is absent from the pre-change snapshot."
                )
            limits = container.get("limits")
            if not isinstance(limits, dict):
                limits = {}
            bound_arguments["container"] = container["name"]
            rollback.update(
                {
                    "container": container["name"],
                    "memory_limit": limits.get("memory"),
                    "cpu_limit": limits.get("cpu"),
                }
            )
        elif tool_name == "kubernetes.scale_deployment":
            replicas = snapshot.get("replicas")
            if (
                not isinstance(replicas, int)
                or isinstance(replicas, bool)
                or replicas < 0
            ):
                raise ValueError(
                    "Pre-change Deployment snapshot has no valid replica count."
                )
            rollback["replicas"] = replicas
        elif tool_name != "kubernetes.rollout_restart":
            raise ValueError(f"Tool {tool_name!r} is outside the execution allowlist.")
        return bound_arguments, rollback

    @staticmethod
    def _resource_lock_key(intent: dict[str, Any]) -> str:
        resource = intent.get("resource")
        if not isinstance(resource, dict):
            raise ValueError("Tool intent has no bound resource identity.")
        namespace = str(resource.get("namespace") or "").strip().lower()
        kind = str(resource.get("kind") or "").strip().lower()
        name = str(resource.get("name") or "").strip().lower()
        if not namespace or not kind or not name:
            raise ValueError("Tool intent has an incomplete bound resource identity.")
        return f"resource:{namespace}:{kind}:{name}"

    def _target_namespace(self, environment: str) -> str:
        # Compatibility helper for callers that only need the local demo namespace.
        return self.settings.kubernetes_namespace

    @staticmethod
    def _has_effective_rollback(rollback: dict[str, Any]) -> bool:
        return any(
            has_effective_rollback(tool, rollback)
            for tool in (
                "kubernetes.patch_deployment",
                "kubernetes.scale_deployment",
            )
        )

    @staticmethod
    def _normalize_tool_arguments(
        tool_name: str,
        proposed: dict[str, Any],
        target: dict[str, str],
        *,
        rollback: bool = False,
    ) -> dict[str, Any]:
        mutable_fields = {
            "kubernetes.patch_deployment": {"container", "memory_limit", "cpu_limit"},
            "kubernetes.scale_deployment": {"replicas"},
            "kubernetes.rollout_restart": set(),
        }.get(tool_name)
        if mutable_fields is None:
            raise ValueError(f"Tool {tool_name!r} is outside the execution allowlist.")
        if not isinstance(proposed, dict):
            raise ValueError("Tool arguments must be an object.")
        protected = {"service", "environment", "namespace", "name"}
        for field in protected & proposed.keys():
            if str(proposed[field]) != target[field]:
                raise ValueError(f"Agent attempted to retarget protected field {field!r}.")
        unexpected = set(proposed) - protected - mutable_fields
        if unexpected:
            raise ValueError(
                "Agent emitted unsupported arguments: " + ", ".join(sorted(unexpected))
            )
        if not rollback:
            required_change = {
                "kubernetes.patch_deployment": {"memory_limit", "cpu_limit"},
                "kubernetes.scale_deployment": {"replicas"},
                "kubernetes.rollout_restart": set(),
            }[tool_name]
            if required_change and not (set(proposed) & required_change):
                raise ValueError(f"Tool {tool_name!r} has no requested state change.")
        normalized: dict[str, Any] = dict(target)
        normalized.update({key: proposed[key] for key in mutable_fields if key in proposed})
        IncidentEngine._validate_mutation_arguments(
            normalized,
            rollback=rollback,
        )
        if rollback and tool_name == "kubernetes.rollout_restart":
            return dict(target)
        return normalized

    @staticmethod
    def _validate_mutation_arguments(
        arguments: dict[str, Any],
        *,
        rollback: bool,
    ) -> None:
        container = arguments.get("container")
        if container is not None and (
            not isinstance(container, str)
            or len(container) > 63
            or not re.fullmatch(r"[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?", container)
        ):
            raise ValueError("Argument 'container' is not a valid Kubernetes name.")

        memory = arguments.get("memory_limit")
        if "memory_limit" in arguments:
            if memory is None and rollback:
                pass
            elif not isinstance(memory, str):
                raise ValueError("memory_limit must be a bounded Kubernetes quantity.")
            else:
                match = re.fullmatch(r"([1-9][0-9]*)(Ki|Mi|Gi)", memory)
                if not match:
                    raise ValueError(
                        "memory_limit must use a positive Ki, Mi, or Gi quantity."
                    )
                amount = int(match.group(1))
                multiplier = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024}[match.group(2)]
                memory_mib = amount * multiplier
                if not 16 <= memory_mib <= 64 * 1024:
                    raise ValueError("memory_limit must be between 16Mi and 64Gi.")

        cpu = arguments.get("cpu_limit")
        if "cpu_limit" in arguments:
            if cpu is None and rollback:
                pass
            elif not isinstance(cpu, str):
                raise ValueError("cpu_limit must be a bounded Kubernetes quantity.")
            else:
                milli_match = re.fullmatch(r"([1-9][0-9]*)m", cpu)
                core_match = re.fullmatch(r"([1-9][0-9]*)", cpu)
                if milli_match:
                    cpu_millicores = int(milli_match.group(1))
                elif core_match:
                    cpu_millicores = int(core_match.group(1)) * 1000
                else:
                    raise ValueError(
                        "cpu_limit must use positive whole cores or millicores."
                    )
                if not 10 <= cpu_millicores <= 64_000:
                    raise ValueError("cpu_limit must be between 10m and 64 cores.")

        if "replicas" in arguments:
            replicas = arguments["replicas"]
            if (
                not isinstance(replicas, int)
                or isinstance(replicas, bool)
                or not 1 <= replicas <= 20
            ):
                raise ValueError("replicas must be an integer between 1 and 20.")
