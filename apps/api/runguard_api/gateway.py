from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from .policy import READ_TOOLS, WRITE_TOOLS

if TYPE_CHECKING:
    from .config import Settings


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
    """Deterministic transport used for the safe local demonstration."""

    async def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "prometheus.query", "write": False},
            {"name": "loki.query", "write": False},
            {"name": "kubernetes.get_events", "write": False},
            {"name": "kubernetes.get_deployment", "write": False},
            {"name": "kubernetes.patch_deployment", "write": True},
            {"name": "kubernetes.scale_deployment", "write": True},
            {"name": "kubernetes.rollout_restart", "write": True},
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
            "kubernetes.get_deployment": ToolResult(
                True,
                {
                    "name": arguments.get("name", service),
                    "namespace": arguments.get("namespace", "runguard-system"),
                    "replicas": 3,
                    "ready_replicas": 3,
                    "generation": 7,
                    "resource_version": "sim-1041",
                    "containers": [
                        {
                            "name": "api",
                            "image": "example.invalid/order-api:demo",
                            "limits": {"memory": "256Mi", "cpu": "500m"},
                        }
                    ],
                },
                (
                    f"k8s://{arguments.get('namespace', 'runguard-system')}"
                    f"/deployment/{arguments.get('name', service)}"
                ),
                220,
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
                    "runner_result": {
                        "ok": True,
                        "before": {
                            "container": arguments.get("container", "api"),
                            "memory_limit": "256Mi",
                            "cpu_limit": "500m",
                        },
                        "after": {
                            "memory_limit": arguments.get("memory_limit", "256Mi"),
                            "cpu_limit": arguments.get("cpu_limit", "500m"),
                        },
                        "resource_version": "sim-1042",
                    },
                },
                f"k8s://simulation/deployment/{service}",
                684,
            )
        if tool_name == "kubernetes.scale_deployment":
            return ToolResult(
                True,
                {
                    "mode": "simulation",
                    "runner_result": {
                        "ok": True,
                        "before": {"replicas": 3},
                        "after": {"replicas": arguments["replicas"]},
                        "resource_version": "sim-1042",
                    },
                },
                f"k8s://simulation/deployment/{service}/scale",
                500,
            )
        if tool_name == "kubernetes.rollout_restart":
            return ToolResult(
                True,
                {
                    "mode": "simulation",
                    "runner_result": {
                        "ok": True,
                        "before": {},
                        "after": {"restarted": True},
                        "resource_version": "sim-1042",
                    },
                },
                f"k8s://simulation/deployment/{service}/restart",
                500,
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


class ProductionMCPTransport:
    """Real Prometheus, Loki, Kubernetes and GitHub adapters behind MCP-style tools."""

    def __init__(self, settings: Settings) -> None:
        from .kubernetes_executor import KubernetesJobExecutor

        self.settings = settings
        self.executor = KubernetesJobExecutor(settings)
        self._kubernetes_configured = False

    async def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "prometheus.query", "write": False, "backend": "prometheus-http"},
            {"name": "loki.query", "write": False, "backend": "loki-http"},
            {"name": "kubernetes.get_events", "write": False, "backend": "kubernetes-api"},
            {"name": "kubernetes.get_deployment", "write": False, "backend": "kubernetes-api"},
            {"name": "github.get_deployments", "write": False, "backend": "github-rest"},
            {
                "name": "kubernetes.patch_deployment",
                "write": True,
                "backend": "restricted-kubernetes-job",
            },
            {
                "name": "kubernetes.scale_deployment",
                "write": True,
                "backend": "restricted-kubernetes-job",
            },
            {
                "name": "kubernetes.rollout_restart",
                "write": True,
                "backend": "restricted-kubernetes-job",
            },
        ]

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        started = time.perf_counter()
        try:
            if tool_name == "prometheus.query":
                data, uri = await self._prometheus(arguments)
            elif tool_name == "loki.query":
                data, uri = await self._loki(arguments)
            elif tool_name in {"kubernetes.get_events", "kubernetes.get_deployment"}:
                data, uri = await asyncio.to_thread(self._kubernetes_read, tool_name, arguments)
            elif tool_name == "github.get_deployments":
                data, uri = await self._github(arguments)
            elif tool_name.startswith("kubernetes.") and tool_name in {
                "kubernetes.patch_deployment",
                "kubernetes.scale_deployment",
                "kubernetes.rollout_restart",
            }:
                if self.settings.execution_mode != "kubernetes_job":
                    return ToolResult(
                        ok=True,
                        data={
                            "mode": "simulation",
                            "applied": arguments,
                            "idempotency_key": context.idempotency_key,
                        },
                        source_uri="k8s://simulation/restricted-job",
                        duration_ms=int((time.perf_counter() - started) * 1000),
                    )
                from .kubernetes_executor import KubernetesJobSpec

                return await self.executor.execute(
                    KubernetesJobSpec(tool_name=tool_name, arguments=arguments),
                    context,
                )
            else:
                raise ValueError(f"Tool {tool_name!r} is not registered.")
            return ToolResult(
                ok=True,
                data=data,
                source_uri=uri,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                data={"error": str(exc), "tool": tool_name},
                source_uri=f"mcp://production/{tool_name}/error",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

    async def rollback(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        if self.settings.execution_mode != "kubernetes_job":
            return ToolResult(
                ok=True,
                data={
                    "mode": "simulation",
                    "rollback": True,
                    "applied": arguments,
                    "idempotency_key": context.idempotency_key,
                },
                source_uri="k8s://simulation/restricted-job/rollback",
                duration_ms=0,
            )
        from .kubernetes_executor import KubernetesJobSpec

        return await self.executor.execute(
            KubernetesJobSpec(tool_name=tool_name, arguments=arguments, rollback=True),
            context,
        )

    async def _prometheus(self, arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if not self.settings.prometheus_url:
            raise RuntimeError("RUNGUARD_PROMETHEUS_URL is not configured.")
        service = arguments["service"]
        query = arguments.get("query") or (
            "histogram_quantile(0.95, sum by (le) "
            f'(rate(http_server_request_duration_seconds_bucket{{service="{service}"}}[5m])))'
        )
        headers = self._bearer(self.settings.prometheus_token)
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            response = await client.get(
                f"{self.settings.prometheus_url.rstrip('/')}/api/v1/query",
                params={"query": query},
            )
            response.raise_for_status()
            body = response.json()
        if body.get("status") != "success":
            raise RuntimeError(f"Prometheus query failed: {body}")
        rows = body.get("data", {}).get("result", [])
        values = [float(row["value"][1]) for row in rows if row.get("value")]
        if not rows or not values:
            raise RuntimeError("Prometheus query returned no time series samples.")
        return (
            {
                "query": query,
                "value": max(values),
                "unit": "seconds",
                "series": len(rows),
                "raw": rows[:20],
            },
            f"{self.settings.prometheus_url.rstrip('/')}/graph?g0.expr={query}",
        )

    async def _loki(self, arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if not self.settings.loki_url:
            raise RuntimeError("RUNGUARD_LOKI_URL is not configured.")
        service = arguments["service"]
        query = arguments.get("query") or f'{{service="{service}"}} |~ "(?i)(error|oom|panic)"'
        end = datetime.now(UTC)
        start = end - timedelta(minutes=int(arguments.get("minutes", 15)))
        headers = self._bearer(self.settings.loki_token)
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            response = await client.get(
                f"{self.settings.loki_url.rstrip('/')}/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": str(int(start.timestamp() * 1_000_000_000)),
                    "end": str(int(end.timestamp() * 1_000_000_000)),
                    "limit": "200",
                    "direction": "backward",
                },
            )
            response.raise_for_status()
            body = response.json()
        streams = body.get("data", {}).get("result", [])
        lines = [line for stream in streams for _, line in stream.get("values", [])]
        return (
            {
                "query": query,
                "matches": len(lines),
                "streams": len(streams),
                "samples": lines[:20],
            },
            f"{self.settings.loki_url.rstrip('/')}/explore",
        )

    def _configure_kubernetes(self) -> None:
        if self._kubernetes_configured:
            return
        from kubernetes import config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config(context=self.settings.kubernetes_context)
        self._kubernetes_configured = True

    def _kubernetes_read(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        from kubernetes import client

        self._configure_kubernetes()
        namespace = arguments.get("namespace") or arguments.get("environment") or "default"
        service = arguments["service"]
        name = arguments.get("name") or service
        if tool_name == "kubernetes.get_deployment":
            deployment = client.AppsV1Api().read_namespaced_deployment(name, namespace)
            containers = deployment.spec.template.spec.containers
            return (
                {
                    "name": deployment.metadata.name,
                    "namespace": namespace,
                    "replicas": deployment.spec.replicas,
                    "ready_replicas": deployment.status.ready_replicas or 0,
                    "generation": deployment.metadata.generation,
                    "resource_version": deployment.metadata.resource_version,
                    "containers": [
                        {
                            "name": container.name,
                            "image": container.image,
                            "limits": dict(container.resources.limits or {}),
                        }
                        for container in containers
                    ],
                },
                f"k8s://{namespace}/deployment/{name}",
            )
        events = client.CoreV1Api().list_namespaced_event(
            namespace,
            field_selector=f"involvedObject.name={service}",
            limit=100,
        ).items
        return (
            {
                "event_count": len(events),
                "events": [
                    {
                        "type": event.type,
                        "reason": event.reason,
                        "message": event.message,
                        "count": event.count,
                        "last_timestamp": str(event.last_timestamp or event.event_time or ""),
                    }
                    for event in events[-30:]
                ],
            },
            f"k8s://{namespace}/events/{service}",
        )

    async def _github(self, arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if not self.settings.github_repository:
            raise RuntimeError("RUNGUARD_GITHUB_REPOSITORY is not configured.")
        repository = self.settings.github_repository
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **self._bearer(self.settings.github_token),
        }
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            response = await client.get(
                f"https://api.github.com/repos/{repository}/deployments",
                params={
                    "environment": arguments.get("environment", "production"),
                    "per_page": 20,
                },
            )
            response.raise_for_status()
            deployments = response.json()
        return (
            {
                "repository": repository,
                "deployments": [
                    {
                        "id": item["id"],
                        "sha": item["sha"],
                        "ref": item["ref"],
                        "environment": item["environment"],
                        "created_at": item["created_at"],
                        "description": item.get("description"),
                    }
                    for item in deployments
                ],
            },
            f"https://github.com/{repository}/deployments",
        )

    @staticmethod
    def _bearer(token: str | None) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"} if token else {}


class HybridMCPTransport(ProductionMCPTransport):
    """Uses real Kubernetes reads/writes while allowing recorded external telemetry."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.mock = MockMCPTransport()

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        result = await super().call_tool(tool_name, arguments, context)
        if result.ok or tool_name.startswith("kubernetes."):
            return result
        return await self.mock.call_tool(tool_name, arguments, context)


class StreamableHTTPMCPTransport:
    """Calls remote MCP servers over the protocol's Streamable HTTP transport.

    Infrastructure writes remain delegated to RunGuard's restricted Kubernetes Job
    executor so a remote MCP server can never bypass the local policy and sandbox boundary.
    """

    TOOL_SERVERS = {
        "prometheus.": "prometheus",
        "loki.": "loki",
        "kubernetes.": "kubernetes",
        "github.": "github",
    }
    WRITE_TOOLS = WRITE_TOOLS
    READ_TOOLS = READ_TOOLS

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.production = ProductionMCPTransport(settings)
        self.servers = {
            "prometheus": (
                settings.mcp_prometheus_url,
                settings.mcp_prometheus_token,
            ),
            "loki": (settings.mcp_loki_url, settings.mcp_loki_token),
            "kubernetes": (
                settings.mcp_kubernetes_url,
                settings.mcp_kubernetes_token,
            ),
            "github": (settings.mcp_github_url, settings.mcp_github_token),
        }

    async def list_tools(self) -> list[dict[str, Any]]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        discovered: list[dict[str, Any]] = []
        for server_name, (url, token) in self.servers.items():
            if not url:
                continue
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            async with httpx.AsyncClient(headers=headers, timeout=20.0) as client:
                async with streamable_http_client(url, http_client=client) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        tools = await session.list_tools()
            discovered.extend(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "backend": f"mcp-streamable-http:{server_name}",
                    "write": tool.name in self.WRITE_TOOLS,
                }
                for tool in tools.tools
                if tool.name in self.READ_TOOLS | self.WRITE_TOOLS
            )
        return discovered

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        if tool_name in self.WRITE_TOOLS:
            return await self.production.call_tool(tool_name, arguments, context)
        if tool_name not in self.READ_TOOLS:
            return ToolResult(
                False,
                {"error": f"Tool {tool_name!r} is outside the exact MCP allowlist."},
                "mcp://denied/error",
                0,
            )
        server_name = next(
            (name for prefix, name in self.TOOL_SERVERS.items() if tool_name.startswith(prefix)),
            None,
        )
        if not server_name:
            return ToolResult(
                False,
                {"error": f"No MCP server mapping exists for {tool_name!r}."},
                "mcp://unmapped/error",
                0,
            )
        url, token = self.servers[server_name]
        if not url:
            return ToolResult(
                False,
                {"error": f"The {server_name} MCP server URL is not configured."},
                f"mcp://{server_name}/unavailable",
                0,
            )
        return await self._call_remote(url, token, server_name, tool_name, arguments)

    async def rollback(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        return await self.production.rollback(tool_name, arguments, context)

    @staticmethod
    async def _call_remote(
        url: str,
        token: str | None,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        from mcp import ClientSession, types
        from mcp.client.streamable_http import streamable_http_client

        started = time.perf_counter()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
                async with streamable_http_client(url, http_client=client) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        result = await session.call_tool(tool_name, arguments=arguments)
            data = result.structuredContent
            if not isinstance(data, dict):
                texts = [
                    block.text
                    for block in result.content
                    if isinstance(block, types.TextContent)
                ]
                try:
                    parsed = json.loads(texts[0]) if len(texts) == 1 else None
                except json.JSONDecodeError:
                    parsed = None
                data = parsed if isinstance(parsed, dict) else {"content": texts}
            return ToolResult(
                ok=not bool(result.isError),
                data=data,
                source_uri=f"mcp+http://{server_name}/{tool_name}",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                data={"error": str(exc), "tool": tool_name},
                source_uri=f"mcp+http://{server_name}/{tool_name}/error",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )


def build_transport(settings: Settings) -> MCPTransport:
    if settings.connector_mode == "mcp":
        return StreamableHTTPMCPTransport(settings)
    if settings.connector_mode == "production":
        return ProductionMCPTransport(settings)
    if settings.connector_mode == "hybrid":
        return HybridMCPTransport(settings)
    return MockMCPTransport()
