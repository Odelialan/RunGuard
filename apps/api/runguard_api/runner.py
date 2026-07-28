from __future__ import annotations

import json
import os
from typing import Any

ALLOWED_TOOLS = {
    "kubernetes.patch_deployment",
    "kubernetes.scale_deployment",
    "kubernetes.rollout_restart",
    "kubernetes.set_http_route_weights",
}

ALLOWED_ARGUMENTS = {
    "kubernetes.patch_deployment": {
        "service",
        "environment",
        "namespace",
        "name",
        "container",
        "memory_limit",
        "cpu_limit",
        "expected_resource_version",
        "idempotency_key",
    },
    "kubernetes.scale_deployment": {
        "service",
        "environment",
        "namespace",
        "name",
        "replicas",
        "expected_resource_version",
        "idempotency_key",
    },
    "kubernetes.rollout_restart": {
        "service",
        "environment",
        "namespace",
        "name",
        "expected_resource_version",
        "idempotency_key",
    },
    "kubernetes.set_http_route_weights": {
        "environment",
        "namespace",
        "route_name",
        "stable_service",
        "canary_service",
        "canary_weight",
        "idempotency_key",
    },
}

LAST_EXECUTION_ANNOTATION = "runguard.io/last-execution"
LAST_BEFORE_ANNOTATION = "runguard.io/last-before-snapshot"


def _replayed_before(annotations: dict[str, str], idempotency_key: str) -> dict[str, Any] | None:
    if annotations.get(LAST_EXECUTION_ANNOTATION) != idempotency_key:
        return None
    raw = annotations.get(LAST_BEFORE_ANNOTATION)
    if not raw:
        raise RuntimeError("Idempotent replay found no durable pre-change snapshot.")
    try:
        before = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Idempotent replay found an invalid pre-change snapshot.") from exc
    if not isinstance(before, dict):
        raise RuntimeError("Idempotent replay found a non-object pre-change snapshot.")
    return before


def _load_kubernetes() -> tuple[Any, Any, Any]:
    from kubernetes import client, config

    config.load_incluster_config()
    return client, client.AppsV1Api(), client.CustomObjectsApi()


def _set_http_route_weights(api: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    namespace = arguments["namespace"]
    route_name = arguments["route_name"]
    stable_service = arguments["stable_service"]
    canary_service = arguments["canary_service"]
    canary_weight = int(arguments["canary_weight"])
    if not 0 <= canary_weight <= 100:
        raise ValueError("Canary traffic weight must be between 0 and 100.")
    route = api.get_namespaced_custom_object(
        "gateway.networking.k8s.io",
        "v1",
        namespace,
        "httproutes",
        route_name,
    )
    metadata = route.get("metadata", {})
    annotations = dict(metadata.get("annotations") or {})
    rules = route.get("spec", {}).get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError(f"HTTPRoute {route_name!r} has no routing rules.")
    before: dict[str, int] = {}
    found = set()
    for rule in rules:
        for backend in rule.get("backendRefs", []):
            name = backend.get("name")
            if name in {stable_service, canary_service}:
                found.add(name)
                before[name] = int(backend.get("weight", 1))
                backend["weight"] = (
                    canary_weight if name == canary_service else 100 - canary_weight
                )
    if found != {stable_service, canary_service}:
        raise ValueError(
            "HTTPRoute must contain both the inventory-bound stable and canary Services."
        )
    patch = {
        "metadata": {
            "resourceVersion": metadata.get("resourceVersion"),
            "annotations": {
                **annotations,
                LAST_EXECUTION_ANNOTATION: arguments["idempotency_key"],
                LAST_BEFORE_ANNOTATION: json.dumps(before, separators=(",", ":")),
            },
        },
        "spec": {"rules": rules},
    }
    updated = api.patch_namespaced_custom_object(
        "gateway.networking.k8s.io",
        "v1",
        namespace,
        "httproutes",
        route_name,
        patch,
    )
    return {
        "before": before,
        "after": {
            stable_service: 100 - canary_weight,
            canary_service: canary_weight,
        },
        "resource_version": updated.get("metadata", {}).get("resourceVersion"),
    }


def _patch_deployment(api: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    namespace = arguments["namespace"]
    name = arguments["name"]
    deployment = api.read_namespaced_deployment(name, namespace)
    annotations = deployment.spec.template.metadata.annotations or {}
    replayed_before = _replayed_before(annotations, arguments["idempotency_key"])
    if replayed_before is not None:
        return {
            "before": replayed_before,
            "after": {
                key: value
                for key, value in arguments.items()
                if key in {"container", "memory_limit", "cpu_limit"}
            },
            "resource_version": deployment.metadata.resource_version,
            "idempotent_replay": True,
        }
    container_name = arguments.get("container") or deployment.spec.template.spec.containers[0].name
    before: dict[str, Any] = {"container": container_name}
    patch: dict[str, Any] = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {}
                },
                "spec": {"containers": []},
            }
        }
    }
    if arguments.get("expected_resource_version"):
        patch["metadata"] = {
            "resourceVersion": arguments["expected_resource_version"]
        }
    container_patch: dict[str, Any] = {"name": container_name}
    found_container = False
    for container in deployment.spec.template.spec.containers:
        if container.name == container_name:
            found_container = True
            limits = dict(getattr(container.resources, "limits", None) or {})
            before["memory_limit"] = limits.get("memory")
            before["cpu_limit"] = limits.get("cpu")
            if "memory_limit" in arguments:
                if arguments["memory_limit"] is None:
                    limits.pop("memory", None)
                else:
                    limits["memory"] = arguments["memory_limit"]
            if "cpu_limit" in arguments:
                if arguments["cpu_limit"] is None:
                    limits.pop("cpu", None)
                else:
                    limits["cpu"] = arguments["cpu_limit"]
            container_patch["resources"] = {"limits": limits}
            break
    if not found_container:
        raise ValueError(f"Container {container_name!r} does not exist in Deployment {name!r}.")
    patch["spec"]["template"]["metadata"]["annotations"] = {
        LAST_EXECUTION_ANNOTATION: arguments["idempotency_key"],
        LAST_BEFORE_ANNOTATION: json.dumps(before, separators=(",", ":")),
    }
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
    deployment = api.read_namespaced_deployment(name, namespace)
    annotations = deployment.metadata.annotations or {}
    replayed_before = _replayed_before(annotations, arguments["idempotency_key"])
    if replayed_before is not None:
        return {
            "before": replayed_before,
            "after": {"replicas": deployment.spec.replicas},
            "resource_version": deployment.metadata.resource_version,
            "idempotent_replay": True,
        }
    before = {"replicas": deployment.spec.replicas}
    replicas = int(arguments["replicas"])
    scale_patch: dict[str, Any] = {
        "metadata": {
            "annotations": {
                LAST_EXECUTION_ANNOTATION: arguments["idempotency_key"],
                LAST_BEFORE_ANNOTATION: json.dumps(before, separators=(",", ":")),
            }
        },
        "spec": {"replicas": replicas},
    }
    if arguments.get("expected_resource_version"):
        scale_patch["metadata"]["resourceVersion"] = arguments["expected_resource_version"]
    result = api.patch_namespaced_deployment(
        name,
        namespace,
        scale_patch,
    )
    return {
        "before": before,
        "after": {"replicas": result.spec.replicas},
        "resource_version": result.metadata.resource_version,
    }


def _restart_deployment(api: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    from datetime import UTC, datetime

    namespace = arguments["namespace"]
    name = arguments["name"]
    deployment = api.read_namespaced_deployment(name, namespace)
    annotations = deployment.spec.template.metadata.annotations or {}
    if annotations.get(LAST_EXECUTION_ANNOTATION) == arguments["idempotency_key"]:
        return {
            "before": {},
            "after": {},
            "resource_version": deployment.metadata.resource_version,
            "idempotent_replay": True,
        }
    restarted_at = datetime.now(UTC).isoformat()
    patch: dict[str, Any] = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": restarted_at,
                        LAST_EXECUTION_ANNOTATION: arguments["idempotency_key"],
                    }
                }
            }
        }
    }
    if arguments.get("expected_resource_version"):
        patch["metadata"] = {
            "resourceVersion": arguments["expected_resource_version"]
        }
    result = api.patch_namespaced_deployment(
        name,
        namespace,
        patch,
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
    _, api, custom_api = _load_kubernetes()
    arguments = dict(payload["arguments"])
    unexpected = set(arguments) - ALLOWED_ARGUMENTS[tool]
    if unexpected:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Unsupported executor arguments: "
                    + ", ".join(sorted(unexpected)),
                }
            )
        )
        return 4
    allowed_namespaces = {
        item.strip()
        for item in os.getenv("RUNGUARD_ALLOWED_NAMESPACES", "").split(",")
        if item.strip()
    }
    if (
        not allowed_namespaces
        or arguments.get("namespace") not in allowed_namespaces
    ):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Target namespace is outside the executor allowlist.",
                }
            )
        )
        return 3
    arguments["idempotency_key"] = payload["idempotency_key"]
    handlers = {
        "kubernetes.patch_deployment": _patch_deployment,
        "kubernetes.scale_deployment": _scale_deployment,
        "kubernetes.rollout_restart": _restart_deployment,
        "kubernetes.set_http_route_weights": lambda _, args: _set_http_route_weights(
            custom_api,
            args,
        ),
    }
    result = handlers[tool](api, arguments)
    print(json.dumps({"ok": True, "tool": tool, **result}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
