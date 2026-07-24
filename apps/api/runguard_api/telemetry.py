from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


class Telemetry:
    def __init__(self, endpoint: str | None, service_name: str) -> None:
        self.endpoint = endpoint
        self.service_name = service_name
        self._tracer: Any = None

    @property
    def enabled(self) -> bool:
        return self._tracer is not None

    def configure(self) -> None:
        if not self.endpoint or self._tracer is not None:
            return
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: self.service_name}))
        endpoint = self.endpoint.rstrip("/")
        if not endpoint.endswith("/v1/traces"):
            endpoint = f"{endpoint}/v1/traces"
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        self._tracer = trace.get_tracer("runguard.incident")

    def record(
        self,
        name: str,
        duration_ms: int,
        attributes: dict[str, Any],
        status: str,
    ) -> tuple[str | None, str | None]:
        if self._tracer is None:
            return None, None
        from opentelemetry.trace import Status, StatusCode

        end = datetime.now(UTC)
        start = end - timedelta(milliseconds=max(duration_ms, 0))
        with self._tracer.start_as_current_span(
            name,
            start_time=int(start.timestamp() * 1_000_000_000),
            end_on_exit=False,
        ) as span:
            for key, value in attributes.items():
                if isinstance(value, (str, bool, int, float)):
                    span.set_attribute(f"runguard.{key}", value)
            span.set_status(Status(StatusCode.ERROR if status == "ERROR" else StatusCode.OK))
            context = span.get_span_context()
            trace_id = f"{context.trace_id:032x}"
            span_id = f"{context.span_id:016x}"
            span.end(end_time=int(end.timestamp() * 1_000_000_000))
        return trace_id, span_id
