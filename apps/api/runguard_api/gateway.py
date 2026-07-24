from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolContext:
    incident_id: str
    run_id: str
    actor: str
    idempotency_key: str


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict[str, Any]
    source_uri: str
    duration_ms: int


class MCPTransport(Protocol):
    async def list_tools(self) -> list[dict[str, Any]]:
        ...

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        ...


class MockMCPTransport:
    """Deterministic transport used for the safe v1.0 demonstration."""

    async def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "prometheus.query", "write": False},
            {"name": "loki.query", "write": False},
            {"name": "kubernetes.get_events", "write": False},
            {"name": "kubernetes.patch_deployment", "write": True},
            {"name": "github.get_deployments", "write": False},
        ]

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        service = arguments.get("service", "service")
        fixtures: dict[str, ToolResult] = {
            "prometheus.query": ToolResult(
                True,
                {
                    "query": (
                        "histogram_quantile(0.95, "
                        f'rate(http_duration_bucket{{service="{service}"}}[5m]))'
                    ),
                    "value": 2.84,
                    "unit": "seconds",
                    "baseline": 0.34,
                },
                f"prometheus://query/{service}-p95",
                312,
            ),
            "loki.query": ToolResult(
                True,
                {
                    "matches": 19,
                    "pattern": "runtime: out of memory",
                    "correlation": 0.96,
                },
                f"loki://query/{service}-oom",
                289,
            ),
            "kubernetes.get_events": ToolResult(
                True,
                {
                    "reason": "OOMKilled",
                    "restart_count": 19,
                    "pods_affected": 3,
                    "memory_limit": "256Mi",
                },
                f"k8s://{arguments.get('environment', 'staging')}/deployment/{service}",
                438,
            ),
            "github.get_deployments": ToolResult(
                True,
                {
                    "commit": "8a71c2d",
                    "change": "memory limit 1Gi -> 256Mi",
                    "deployed_minutes_before_alert": 2,
                },
                f"github://deployments/{service}/8a71c2d",
                356,
            ),
        }
        if tool_name in fixtures:
            return fixtures[tool_name]
        if tool_name == "kubernetes.patch_deployment":
            return ToolResult(
                True,
                {
                    "mode": "simulation",
                    "resource_version": "sim-1042",
                    "applied": arguments,
                    "idempotency_key": context.idempotency_key,
                },
                f"k8s://simulation/deployment/{service}",
                684,
            )
        return ToolResult(
            False,
            {"error": f"Tool {tool_name!r} is not registered."},
            "mcp://mock/error",
            1,
        )


class RecordedMCPTransport(MockMCPTransport):
    """Replay transport: responses are supplied by a previously recorded run."""

    def __init__(self, recording: dict[str, ToolResult] | None = None) -> None:
        self.recording = recording or {}

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        if tool_name in self.recording:
            return self.recording[tool_name]
        return await super().call_tool(tool_name, arguments, context)
