from __future__ import annotations

import json
import os
from typing import Any

ALLOWED_TOOLS = {
    "kubernetes.patch_deployment",
    "kubernetes.scale_deployment",
    "kubernetes.rollout_restart",
}


def _load_kubernetes() -> tuple[Any, Any]:
    from kubernetes import client, config

    config.load_incluster_config()
    return client, client.AppsV1Api()


def _patch_deployment(api: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    namespace = arguments["namespace"]
    name = arguments["name"]
    deployment = api.read_namespaced_deployment(name, namespace)
    container_name = arguments.get("container") or deployment.spec.template.spec.containers[0].name
    before: dict[str, Any] = {}
    patch: dict[str, Any] = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {"runguard.io/last-execution": arguments["idempotency_key"]}
                },
                "spec": {"containers": []},
            }
        }
    }
    container_patch: dict[str, Any] = {"name": container_name}
    for container in deployment.spec.template.spec.containers:
        if container.name == container_name:
            resources = container.resources
            before["memory_limit"] = (resources.limits or {}).get("memory")
            before["cpu_limit"] = (resources.limits or {}).get("cpu")
            limits = dict(resources.limits or {})
            if arguments.get("memory_limit"):
                limits["memory"] = arguments["memory_limit"]
            if arguments.get("cpu_limit"):
                limits["cpu"] = arguments["cpu_limit"]
            container_patch["resources"] = {"limits": limits}
            break
    patch["spec"]["template"]["spec"]["containers"].append(container_patch)
    result = api.patch_namespaced_deployment(name, namespace, patch)
    return {
        "before": before,
        "after": {
            "memory_limit": arguments.get("memory_limit", before.get("memory_limit")),
            "cpu_limit": arguments.get("cpu_limit", before.get("cpu_limit")),
        },
        "resource_version": result.metadata.resource_version,
    }


def _scale_deployment(api: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    namespace = arguments["namespace"]
    name = arguments["name"]
    current = api.read_namespaced_deployment_scale(name, namespace)
    replicas = int(arguments["replicas"])
    result = api.patch_namespaced_deployment_scale(
        name,
        namespace,
        {"spec": {"replicas": replicas}},
    )
    return {
        "before": {"replicas": current.spec.replicas},
        "after": {"replicas": result.spec.replicas},
        "resource_version": result.metadata.resource_version,
    }


def _restart_deployment(api: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    from datetime import UTC, datetime

    namespace = arguments["namespace"]
    name = arguments["name"]
    restarted_at = datetime.now(UTC).isoformat()
    result = api.patch_namespaced_deployment(
        name,
        namespace,
        {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {"kubectl.kubernetes.io/restartedAt": restarted_at}
                    }
                }
            }
        },
    )
    return {
        "before": {},
        "after": {"restarted_at": restarted_at},
        "resource_version": result.metadata.resource_version,
    }


def run() -> int:
    payload = json.loads(os.environ["RUNGUARD_TOOL_INTENT_JSON"])
    tool = payload["tool"]
    if tool not in ALLOWED_TOOLS:
        print(json.dumps({"ok": False, "error": f"Tool {tool!r} is not allowed."}))
        return 2
    _, api = _load_kubernetes()
    arguments = dict(payload["arguments"])
    arguments["idempotency_key"] = payload["idempotency_key"]
    handlers = {
        "kubernetes.patch_deployment": _patch_deployment,
        "kubernetes.scale_deployment": _scale_deployment,
        "kubernetes.rollout_restart": _restart_deployment,
    }
    result = handlers[tool](api, arguments)
    print(json.dumps({"ok": True, "tool": tool, **result}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
