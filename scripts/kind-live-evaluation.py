#!/usr/bin/env python3
"""Create and observe twelve real Kubernetes failure modes in an isolated namespace."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

NAMESPACE = "runguard-live-eval"
PYTHON_IMAGE = (
    "python:3.12-slim@"
    "sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
)
REDIS_IMAGE = (
    "redis:7.4-alpine@"
    "sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2"
)
POSTGRES_IMAGE = (
    "pgvector/pgvector:pg16@"
    "sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb"
)


def kubectl(*args: str, stdin: dict[str, Any] | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ["kubectl", *args],
        input=json.dumps(stdin) if stdin is not None else None,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode:
        raise RuntimeError(
            f"kubectl {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result.stdout


def apply(resource: dict[str, Any]) -> None:
    kubectl("apply", "-f", "-", stdin=resource)


def pod(
    name: str,
    command: list[str],
    *,
    image: str = PYTHON_IMAGE,
    restart_policy: str = "Always",
    resources: dict[str, Any] | None = None,
    env: list[dict[str, str]] | None = None,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    container: dict[str, Any] = {
        "name": "fault",
        "image": image,
        "command": command,
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "runAsNonRoot": True,
            "runAsUser": 65532,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
    }
    if resources:
        container["resources"] = resources
    if env:
        container["env"] = env
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {"app": name, **(labels or {})},
        },
        "spec": {
            "restartPolicy": restart_policy,
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65532,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [container],
        },
    }


def postgres_pod(name: str, max_connections: int) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {"app": name},
        },
        "spec": {
            "restartPolicy": "Always",
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 999,
                "runAsGroup": 999,
                "fsGroup": 999,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "volumes": [{"name": "data", "emptyDir": {}}],
            "containers": [
                {
                    "name": "postgres",
                    "image": POSTGRES_IMAGE,
                    "args": [
                        "postgres",
                        "-c",
                        f"max_connections={max_connections}",
                        "-c",
                        "max_wal_senders=0",
                        "-c",
                        "superuser_reserved_connections=1",
                    ],
                    "env": [
                        {
                            "name": "POSTGRES_PASSWORD",
                            "value": "isolated-evaluation-only",
                        },
                        {
                            "name": "PGDATA",
                            "value": "/var/lib/postgresql/data/pgdata",
                        },
                    ],
                    "volumeMounts": [
                        {
                            "name": "data",
                            "mountPath": "/var/lib/postgresql/data",
                        }
                    ],
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                        "runAsNonRoot": True,
                        "runAsUser": 999,
                    },
                }
            ],
        },
    }


def deployment(
    name: str,
    *,
    replicas: int = 1,
    image: str = PYTHON_IMAGE,
    command: list[str] | None = None,
    node_selector: dict[str, str] | None = None,
    readiness_failure: bool = False,
) -> dict[str, Any]:
    container: dict[str, Any] = {
        "name": "app",
        "image": image,
        "command": command or ["python", "-c", "import time; time.sleep(3600)"],
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "runAsNonRoot": True,
            "runAsUser": 65532,
        },
    }
    if readiness_failure:
        container["readinessProbe"] = {
            "exec": {"command": ["python", "-c", "raise SystemExit(1)"]},
            "periodSeconds": 1,
            "failureThreshold": 1,
        }
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": NAMESPACE},
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name}},
                "spec": {
                    "automountServiceAccountToken": False,
                    "nodeSelector": node_selector or {},
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 65532,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [container],
                },
            },
        },
    }


def get(kind: str, name: str) -> dict[str, Any]:
    return json.loads(kubectl("-n", NAMESPACE, "get", kind, name, "-o", "json"))


def wait_for(
    predicate: Callable[[], tuple[bool, dict[str, Any]]],
    timeout: int = 120,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            passed, last = predicate()
            if passed:
                return last
        except (RuntimeError, KeyError, IndexError, json.JSONDecodeError):
            pass
        time.sleep(2)
    raise TimeoutError(f"fault condition was not observed; last observation={last}")


def recover_cases(cases: list[dict[str, Any]]) -> None:
    by_id = {case["id"]: case for case in cases}

    def record(case_id: str, operation: Callable[[], dict[str, Any]]) -> None:
        started = time.monotonic()
        try:
            observation = operation()
            by_id[case_id]["recovery"] = {
                "status": "PASS",
                "duration_seconds": round(time.monotonic() - started, 3),
                "observation": observation,
            }
        except Exception as exc:
            by_id[case_id]["recovery"] = {
                "status": "FAIL",
                "duration_seconds": round(time.monotonic() - started, 3),
                "error": str(exc),
            }

    def healthy_pod(name: str) -> dict[str, Any]:
        kubectl("-n", NAMESPACE, "delete", "pod", name, "--wait=true", check=False)
        apply(pod(name, ["python", "-c", "import time; time.sleep(3600)"]))
        kubectl(
            "-n",
            NAMESPACE,
            "wait",
            "--for=condition=Ready",
            f"pod/{name}",
            "--timeout=120s",
        )
        return {"ready": True}

    def healthy_deployment(name: str) -> dict[str, Any]:
        kubectl(
            "-n", NAMESPACE, "delete", "deployment", name, "--wait=true", check=False
        )
        apply(deployment(name))
        kubectl(
            "-n",
            NAMESPACE,
            "rollout",
            "status",
            f"deployment/{name}",
            "--timeout=120s",
        )
        current = get("deployment", name).get("status", {})
        return {"available_replicas": current.get("availableReplicas", 0)}

    for case_id, name in (
        ("CASE-01", "oom-killed"),
        ("CASE-02", "crash-loop"),
        ("CASE-03", "image-pull"),
        ("CASE-05", "cpu-throttle"),
        ("CASE-08", "invalid-env"),
    ):
        record(case_id, lambda name=name: healthy_pod(name))
    record("CASE-04", lambda: healthy_deployment("insufficient-replicas"))
    record("CASE-10", lambda: healthy_deployment("release-regression"))

    def database_recovered() -> dict[str, Any]:
        kubectl("-n", NAMESPACE, "delete", "pod", "db-pool", "--wait=true", check=False)
        apply(postgres_pod("db-pool", 100))
        return wait_for(
            lambda: (
                kubectl(
                    "-n",
                    NAMESPACE,
                    "exec",
                    "db-pool",
                    "--",
                    "pg_isready",
                    "-U",
                    "postgres",
                    check=False,
                ).strip().endswith("accepting connections"),
                {"max_connections": 100, "accepting_connections": True},
            ),
            150,
        )

    record("CASE-06", database_recovered)

    def redis_recovered() -> dict[str, Any]:
        time.sleep(2)
        response = kubectl(
            "-n",
            NAMESPACE,
            "exec",
            "redis-latency",
            "--",
            "redis-cli",
            "PING",
        ).strip()
        if response != "PONG":
            raise RuntimeError(f"Redis did not recover: {response}")
        return {"response": response}

    record("CASE-07", redis_recovered)

    apply(
        pod(
            "recovered-backend",
            ["python", "-c", "import time; time.sleep(3600)"],
            labels={"app": "recovered-backend"},
        )
    )
    kubectl(
        "-n",
        NAMESPACE,
        "wait",
        "--for=condition=Ready",
        "pod/recovered-backend",
        "--timeout=120s",
    )

    def service_recovered(name: str) -> dict[str, Any]:
        kubectl(
            "-n",
            NAMESPACE,
            "patch",
            "service",
            name,
            "--type=merge",
            "-p",
            '{"spec":{"selector":{"app":"recovered-backend"}}}',
        )

        def observe() -> tuple[bool, dict[str, Any]]:
            raw = kubectl(
                "-n",
                NAMESPACE,
                "get",
                "endpointslices",
                "-l",
                f"kubernetes.io/service-name={name}",
                "-o",
                "json",
            )
            addresses = [
                address
                for item in (json.loads(raw).get("items") or [])
                for endpoint in (item.get("endpoints") or [])
                for address in (endpoint.get("addresses") or [])
            ]
            return bool(addresses), {"endpoint_addresses": addresses}

        return wait_for(observe)

    record("CASE-09", lambda: service_recovered("selector-mismatch"))
    record("CASE-11", lambda: service_recovered("loki-unavailable"))

    def compound_recovered() -> dict[str, Any]:
        deployment_result = healthy_deployment("compound-failure")
        service_result = service_recovered("compound-failure")
        return {**deployment_result, **service_result}

    record("CASE-12", compound_recovered)


def terminated_reason(name: str, expected: set[str]) -> tuple[bool, dict[str, Any]]:
    current = get("pod", name)
    status = current.get("status", {}).get("containerStatuses", [{}])[0]
    state = status.get("lastState", {}).get("terminated") or status.get("state", {}).get(
        "terminated", {}
    )
    observation = {
        "reason": state.get("reason"),
        "exit_code": state.get("exitCode"),
        "restart_count": status.get("restartCount", 0),
    }
    return observation["reason"] in expected, observation


def waiting_reason(name: str, expected: set[str]) -> tuple[bool, dict[str, Any]]:
    current = get("pod", name)
    status = current.get("status", {}).get("containerStatuses", [{}])[0]
    reason = status.get("state", {}).get("waiting", {}).get("reason")
    observation = {"reason": reason, "restart_count": status.get("restartCount", 0)}
    return reason in expected, observation


def crashloop_observation(
    current: dict[str, Any],
    expected_exit_code: int,
) -> tuple[bool, dict[str, Any]]:
    """Recognize a crash loop without depending on a transient waiting snapshot."""

    statuses = current.get("status", {}).get("containerStatuses") or [{}]
    status = statuses[0]
    waiting_reason_value = status.get("state", {}).get("waiting", {}).get("reason")
    terminated = status.get("lastState", {}).get("terminated") or {}
    restart_count = status.get("restartCount", 0)
    observation = {
        "reason": waiting_reason_value,
        "last_termination_reason": terminated.get("reason"),
        "last_exit_code": terminated.get("exitCode"),
        "restart_count": restart_count,
    }
    in_backoff = waiting_reason_value == "CrashLoopBackOff"
    repeated_expected_crash = (
        restart_count >= 2 and terminated.get("exitCode") == expected_exit_code
    )
    return in_backoff or repeated_expected_crash, observation


def run_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    def execute(case_id: str, name: str, operation: Callable[[], dict[str, Any]]) -> None:
        started = time.monotonic()
        try:
            observation = operation()
            status, error = "PASS", None
        except Exception as exc:
            observation, status, error = {}, "FAIL", str(exc)
        cases.append(
            {
                "id": case_id,
                "name": name,
                "status": status,
                "duration_seconds": round(time.monotonic() - started, 3),
                "observation": observation,
                "error": error,
            }
        )

    def oom() -> dict[str, Any]:
        apply(
            pod(
                "oom-killed",
                [
                    "python",
                    "-c",
                    "import time; x=bytearray(128*1024*1024); time.sleep(60)",
                ],
                resources={"limits": {"memory": "16Mi"}, "requests": {"memory": "8Mi"}},
            )
        )
        return wait_for(lambda: terminated_reason("oom-killed", {"OOMKilled"}), 150)

    execute("CASE-01", "Pod OOMKilled", oom)

    def crashloop() -> dict[str, Any]:
        apply(pod("crash-loop", ["python", "-c", "raise SystemExit(17)"]))
        return wait_for(lambda: crashloop_observation(get("pod", "crash-loop"), 17), 150)

    execute("CASE-02", "CrashLoopBackOff", crashloop)

    def image_pull() -> dict[str, Any]:
        apply(
            pod(
                "image-pull",
                ["true"],
                image="registry.invalid/runguard/does-not-exist:never",
            )
        )
        return wait_for(
            lambda: waiting_reason("image-pull", {"ErrImagePull", "ImagePullBackOff"}),
            90,
        )

    execute("CASE-03", "Image pull failure", image_pull)

    def insufficient_replicas() -> dict[str, Any]:
        apply(
            deployment(
                "insufficient-replicas",
                replicas=2,
                node_selector={"runguard.io/nonexistent-node": "true"},
            )
        )

        def observe() -> tuple[bool, dict[str, Any]]:
            status = get("deployment", "insufficient-replicas").get("status", {})
            item = {
                "replicas": status.get("replicas", 0),
                "available_replicas": status.get("availableReplicas", 0),
                "unavailable_replicas": status.get("unavailableReplicas", 0),
            }
            return item["unavailable_replicas"] == 2, item

        return wait_for(observe)

    execute("CASE-04", "Insufficient replicas", insufficient_replicas)

    def cpu_throttle() -> dict[str, Any]:
        apply(
            pod(
                "cpu-throttle",
                ["python", "-c", "x=0\nwhile True: x+=1"],
                resources={"limits": {"cpu": "10m"}, "requests": {"cpu": "5m"}},
            )
        )
        kubectl(
            "-n",
            NAMESPACE,
            "wait",
            "--for=condition=Ready",
            "pod/cpu-throttle",
            "--timeout=120s",
        )
        time.sleep(5)
        raw = kubectl(
            "-n",
            NAMESPACE,
            "exec",
            "cpu-throttle",
            "--",
            "python",
            "-c",
            (
                "from pathlib import Path;"
                "print(Path('/sys/fs/cgroup/cpu.stat').read_text())"
            ),
        )
        values = dict(line.split() for line in raw.splitlines() if len(line.split()) == 2)
        observation = {"nr_throttled": int(values.get("nr_throttled", "0"))}
        if observation["nr_throttled"] < 1:
            raise RuntimeError(f"CPU throttling was not observed: {observation}")
        return observation

    execute("CASE-05", "CPU limit throttling", cpu_throttle)

    def pool_exhaustion() -> dict[str, Any]:
        apply(postgres_pod("db-pool", 8))
        wait_for(
            lambda: (
                kubectl(
                    "-n",
                    NAMESPACE,
                    "exec",
                    "db-pool",
                    "--",
                    "pg_isready",
                    "-U",
                    "postgres",
                    check=False,
                ).strip().endswith("accepting connections"),
                {"postgres": "starting"},
            ),
            150,
        )
        raw = kubectl(
            "-n",
            NAMESPACE,
            "exec",
            "db-pool",
            "--",
            "sh",
            "-c",
            (
                "rm -f /tmp/runguard-client-*; "
                "for i in $(seq 1 20); do "
                "psql -U postgres -c 'select pg_sleep(5)' "
                ">\"/tmp/runguard-client-$i\" 2>&1 & done; "
                "wait; grep -l 'FATAL' /tmp/runguard-client-* | wc -l"
            ),
        ).strip()
        observation = {"rejected_connections": int(raw)}
        if observation["rejected_connections"] < 1:
            raise RuntimeError(f"PostgreSQL pool exhaustion not observed: {observation}")
        return observation

    execute("CASE-06", "Database pool exhaustion", pool_exhaustion)

    def redis_latency() -> dict[str, Any]:
        apply(
            pod(
                "redis-latency",
                [
                    "redis-server",
                    "--protected-mode",
                    "no",
                    "--dir",
                    "/tmp",
                    "--dbfilename",
                    "dump.rdb",
                ],
                image=REDIS_IMAGE,
            )
        )
        kubectl(
            "-n",
            NAMESPACE,
            "wait",
            "--for=condition=Ready",
            "pod/redis-latency",
            "--timeout=120s",
        )
        response = kubectl(
            "-n",
            NAMESPACE,
            "exec",
            "redis-latency",
            "--",
            "redis-cli",
            "CLIENT",
            "PAUSE",
            "2000",
            "ALL",
        ).strip()
        if response != "OK":
            raise RuntimeError(f"Redis rejected latency injection: {response}")
        started = time.monotonic()
        ping = kubectl(
            "-n",
            NAMESPACE,
            "exec",
            "redis-latency",
            "--",
            "redis-cli",
            "PING",
        ).strip()
        latency_ms = round((time.monotonic() - started) * 1000)
        if ping != "PONG" or latency_ms < 1500:
            raise RuntimeError(
                f"Redis latency was not observed: response={ping}, latency_ms={latency_ms}"
            )
        return {
            "client_pause_ms": 2000,
            "observed_latency_ms": latency_ms,
            "redis_response": ping,
        }

    execute("CASE-07", "Redis latency", redis_latency)

    def invalid_env() -> dict[str, Any]:
        apply(
            pod(
                "invalid-env",
                [
                    "python",
                    "-c",
                    (
                        "import os;"
                        "raise SystemExit(0 if os.getenv('REQUIRED_MODE') == 'safe' else 42)"
                    ),
                ],
                env=[{"name": "REQUIRED_MODE", "value": "unsafe"}],
            )
        )
        observation = wait_for(lambda: terminated_reason("invalid-env", {"Error"}))
        if observation["exit_code"] != 42:
            raise RuntimeError(f"unexpected invalid-env exit: {observation}")
        return observation

    execute("CASE-08", "Invalid environment variable", invalid_env)

    def empty_service(name: str, selector: str) -> dict[str, Any]:
        apply(
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": name, "namespace": NAMESPACE},
                "spec": {
                    "selector": {"app": selector},
                    "ports": [{"port": 80, "targetPort": 8080}],
                },
            }
        )

        def observe() -> tuple[bool, dict[str, Any]]:
            raw = kubectl(
                "-n",
                NAMESPACE,
                "get",
                "endpointslices",
                "-l",
                f"kubernetes.io/service-name={name}",
                "-o",
                "json",
            )
            slices = json.loads(raw).get("items", [])
            addresses = [
                address
                for item in (slices or [])
                for endpoint in (item.get("endpoints") or [])
                for address in (endpoint.get("addresses") or [])
            ]
            item = {"endpoint_addresses": addresses}
            return not addresses, item

        return wait_for(observe)

    execute(
        "CASE-09",
        "Service selector mismatch",
        lambda: empty_service("selector-mismatch", "no-such-workload"),
    )

    def release_regression() -> dict[str, Any]:
        apply(deployment("release-regression", readiness_failure=True))

        def observe() -> tuple[bool, dict[str, Any]]:
            current = get("deployment", "release-regression")
            status = current.get("status", {})
            item = {
                "generation": current.get("metadata", {}).get("generation"),
                "updated_replicas": status.get("updatedReplicas", 0),
                "available_replicas": status.get("availableReplicas", 0),
                "unavailable_replicas": status.get("unavailableReplicas", 0),
            }
            return (
                item["updated_replicas"] == 1
                and item["available_replicas"] == 0
                and item["unavailable_replicas"] == 1
            ), item

        return wait_for(observe)

    execute("CASE-10", "Recent release regression", release_regression)
    execute(
        "CASE-11",
        "Log platform unavailable",
        lambda: empty_service("loki-unavailable", "loki-that-does-not-exist"),
    )

    def compound() -> dict[str, Any]:
        apply(
            deployment(
                "compound-failure",
                image="registry.invalid/runguard/compound:never",
            )
        )
        service_observation = empty_service(
            "compound-failure",
            "different-selector",
        )

        def observe_image() -> tuple[bool, dict[str, Any]]:
            pods = json.loads(
                kubectl(
                    "-n",
                    NAMESPACE,
                    "get",
                    "pods",
                    "-l",
                    "app=compound-failure",
                    "-o",
                    "json",
                )
            ).get("items", [])
            reason = (
                pods[0]
                .get("status", {})
                .get("containerStatuses", [{}])[0]
                .get("state", {})
                .get("waiting", {})
                .get("reason")
                if pods
                else None
            )
            item = {"image_reason": reason}
            return reason in {"ErrImagePull", "ImagePullBackOff"}, item

        image_observation = wait_for(observe_image, 90)
        return {**service_observation, **image_observation}

    execute("CASE-12", "Compound failure", compound)
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="artifacts/live-kubernetes-12.json",
        help="JSON evidence artifact path",
    )
    parser.add_argument("--keep", action="store_true", help="keep the evaluation namespace")
    args = parser.parse_args()
    kubectl("version", "--client=true")
    kubectl(
        "create",
        "namespace",
        NAMESPACE,
        "--dry-run=client",
        "-o",
        "json",
        check=True,
    )
    apply(
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": NAMESPACE,
                "labels": {
                    "pod-security.kubernetes.io/enforce": "restricted",
                    "pod-security.kubernetes.io/enforce-version": "latest",
                },
            },
        }
    )
    try:
        cases = run_cases()
        recover_cases(cases)
        report = {
            "suite": "live-kubernetes-12",
            "generated_at": datetime.now(UTC).isoformat(),
            "cluster": kubectl("config", "current-context").strip(),
            "live_fault_injection": True,
            "case_count": len(cases),
            "passed_cases": sum(case["status"] == "PASS" for case in cases),
            "recovered_cases": sum(
                case.get("recovery", {}).get("status") == "PASS" for case in cases
            ),
            "cases": cases,
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return (
            0
            if report["passed_cases"]
            == report["recovered_cases"]
            == report["case_count"]
            == 12
            else 1
        )
    finally:
        if not args.keep:
            kubectl("delete", "namespace", NAMESPACE, "--wait=false", check=False)


if __name__ == "__main__":
    raise SystemExit(main())
