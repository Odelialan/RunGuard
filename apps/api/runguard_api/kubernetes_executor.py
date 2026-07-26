from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .gateway import ToolContext, ToolResult


@dataclass(frozen=True)
class KubernetesJobSpec:
    tool_name: str
    arguments: dict[str, Any]
    rollback: bool = False


class KubernetesJobExecutor:
    """Runs allow-listed mutations inside a short-lived, restricted Kubernetes Job."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._configured = False

    def _configure(self) -> None:
        if self._configured:
            return
        from kubernetes import config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config(context=self.settings.kubernetes_context)
        self._configured = True

    async def execute(
        self,
        spec: KubernetesJobSpec,
        context: ToolContext,
    ) -> ToolResult:
        started = time.perf_counter()
        try:
            data = await asyncio.to_thread(self._execute_sync, spec, context)
            runner_result = data.get("runner_result")
            ok = (
                data.get("status") == "succeeded"
                and isinstance(runner_result, dict)
                and runner_result.get("ok") is True
            )
            if data.get("status") == "succeeded" and not ok:
                data["error"] = "Executor Job succeeded without a valid successful runner result."
        except Exception as exc:
            data = {"status": "failed", "error": str(exc), "rollback": spec.rollback}
            ok = False
        return ToolResult(
            ok=ok,
            data=data,
            source_uri=(
                f"k8s://{self.settings.kubernetes_namespace}/job/"
                f"{data.get('job_name', 'creation-failed')}"
            ),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _execute_sync(
        self,
        spec: KubernetesJobSpec,
        context: ToolContext,
    ) -> dict[str, Any]:
        from kubernetes import client

        self._configure()
        batch = client.BatchV1Api()
        core = client.CoreV1Api()
        idempotency_label = hashlib.sha256(
            context.idempotency_key.encode("utf-8")
        ).hexdigest()[:24]
        incident_slug = re.sub(
            r"[^a-z0-9-]+",
            "-",
            context.incident_id.lower(),
        ).strip("-")[-20:] or "incident"
        job_name = f"runguard-{incident_slug}-{idempotency_label[:16]}"
        payload = {
            "tool": spec.tool_name,
            "arguments": spec.arguments,
            "rollback": spec.rollback,
            "incident_id": context.incident_id,
            "run_id": context.run_id,
            "actor": context.actor,
            "idempotency_key": context.idempotency_key,
        }
        labels = {
            "app.kubernetes.io/name": "runguard-executor",
            "app.kubernetes.io/managed-by": "runguard",
            "runguard.io/incident": context.incident_id.lower(),
            "runguard.io/idempotency": idempotency_label,
        }
        security = client.V1SecurityContext(
            allow_privilege_escalation=False,
            capabilities=client.V1Capabilities(drop=["ALL"]),
            privileged=False,
            read_only_root_filesystem=True,
            run_as_non_root=True,
            run_as_user=10001,
            seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
        )
        pod_security = client.V1PodSecurityContext(
            run_as_non_root=True,
            run_as_user=10001,
            run_as_group=10001,
            fs_group=10001,
            seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
        )
        container = client.V1Container(
            name="executor",
            image=self.settings.kubernetes_runner_image,
            args=["python", "-m", "runguard_api.runner"],
            env=[
                client.V1EnvVar(name="RUNGUARD_TOOL_INTENT_JSON", value=json.dumps(payload)),
                client.V1EnvVar(
                    name="RUNGUARD_ALLOWED_NAMESPACES",
                    value=",".join(self.settings.kubernetes_allowed_namespaces),
                ),
            ],
            resources=client.V1ResourceRequirements(
                requests={"cpu": "25m", "memory": "64Mi"},
                limits={"cpu": "250m", "memory": "256Mi"},
            ),
            security_context=security,
            volume_mounts=[
                # The path is backed by a pod-local emptyDir, not the host filesystem.
                client.V1VolumeMount(name="tmp", mount_path="/tmp"),  # nosec B108
            ],
        )
        template = client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(labels=labels),
            spec=client.V1PodSpec(
                automount_service_account_token=True,
                containers=[container],
                restart_policy="Never",
                service_account_name=self.settings.kubernetes_service_account,
                security_context=pod_security,
                termination_grace_period_seconds=10,
                volumes=[client.V1Volume(name="tmp", empty_dir=client.V1EmptyDirVolumeSource())],
            ),
        )
        job = client.V1Job(
            metadata=client.V1ObjectMeta(
                name=job_name,
                namespace=self.settings.kubernetes_namespace,
                labels=labels,
                annotations={
                    "runguard.io/run-id": context.run_id,
                    "runguard.io/idempotency-key": context.idempotency_key,
                },
            ),
            spec=client.V1JobSpec(
                template=template,
                backoff_limit=0,
                active_deadline_seconds=self.settings.kubernetes_job_timeout_seconds,
                ttl_seconds_after_finished=600,
            ),
        )
        existing = batch.list_namespaced_job(
            self.settings.kubernetes_namespace,
            label_selector=f"runguard.io/idempotency={idempotency_label}",
        ).items
        if existing:
            job_name = existing[0].metadata.name
        else:
            try:
                batch.create_namespaced_job(self.settings.kubernetes_namespace, job)
            except client.ApiException as exc:
                # Concurrent replicas converge on the deterministic Job name.
                if exc.status != 409:
                    raise
        deadline = time.monotonic() + self.settings.kubernetes_job_timeout_seconds
        status = "running"
        while time.monotonic() < deadline:
            current = batch.read_namespaced_job_status(
                job_name,
                self.settings.kubernetes_namespace,
            )
            if current.status.succeeded:
                status = "succeeded"
                break
            if current.status.failed:
                status = "failed"
                break
            time.sleep(2)
        else:
            status = "timeout"
        pods = core.list_namespaced_pod(
            self.settings.kubernetes_namespace,
            label_selector=f"job-name={job_name}",
        ).items
        logs = ""
        if pods:
            try:
                logs = core.read_namespaced_pod_log(
                    pods[0].metadata.name,
                    self.settings.kubernetes_namespace,
                    tail_lines=200,
                )
            except Exception:
                logs = ""
        parsed: dict[str, Any] = {}
        for line in reversed(logs.splitlines()):
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        return {
            "status": status,
            "job_name": job_name,
            "namespace": self.settings.kubernetes_namespace,
            "rollback": spec.rollback,
            "runner_result": parsed,
        }
