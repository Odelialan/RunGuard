from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

REDACTED = "[REDACTED]"
QUARANTINED = "[QUARANTINED_PROMPT_INJECTION]"
MAX_STRING_LENGTH = 4096
MAX_COLLECTION_ITEMS = 50

TOOL_FIELD_ALLOWLIST: dict[str, frozenset[str]] = {
    "prometheus.query": frozenset(
        {"query", "value", "unit", "baseline", "series", "raw", "error", "tool"}
    ),
    "loki.query": frozenset(
        {
            "query",
            "matches",
            "streams",
            "samples",
            "pattern",
            "correlation",
            "error",
            "tool",
        }
    ),
    "kubernetes.get_events": frozenset(
        {
            "event_count",
            "events",
            "reason",
            "restart_count",
            "pods_affected",
            "memory_limit",
            "error",
            "tool",
        }
    ),
    "kubernetes.get_deployment": frozenset(
        {
            "name",
            "namespace",
            "replicas",
            "ready_replicas",
            "generation",
            "resource_version",
            "containers",
            "error",
            "tool",
        }
    ),
    "github.get_deployments": frozenset(
        {
            "repository",
            "deployments",
            "commit",
            "change",
            "deployed_minutes_before_alert",
            "error",
            "tool",
        }
    ),
    "kubernetes.patch_deployment": frozenset(
        {
            "mode",
            "status",
            "job_name",
            "namespace",
            "rollback",
            "runner_result",
            "applied",
            "idempotency_key",
            "error",
        }
    ),
    "kubernetes.scale_deployment": frozenset(
        {
            "mode",
            "status",
            "job_name",
            "namespace",
            "rollback",
            "runner_result",
            "applied",
            "idempotency_key",
            "error",
        }
    ),
    "kubernetes.rollout_restart": frozenset(
        {
            "mode",
            "status",
            "job_name",
            "namespace",
            "rollback",
            "runner_result",
            "applied",
            "idempotency_key",
            "error",
        }
    ),
}

NESTED_FIELD_ALLOWLIST = frozenset(
    {
        "type",
        "reason",
        "message",
        "count",
        "last_timestamp",
        "name",
        "image",
        "limits",
        "cpu",
        "memory",
        "id",
        "sha",
        "ref",
        "environment",
        "created_at",
        "updated_at",
        "metric",
        "value",
        "timestamp",
        "stream",
        "values",
        "mode",
        "status",
        "job_name",
        "namespace",
        "rollback",
        "runner_result",
        "applied",
        "idempotency_key",
        "ok",
        "before",
        "after",
        "resource_version",
        "idempotent_replay",
        "service",
        "container",
        "memory_limit",
        "cpu_limit",
        "replicas",
        "expected_resource_version",
        "restarted_at",
        "error",
    }
)

SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:authorization|cookie|password|passwd|secret|token|api[_-]?key|"
    r"private[_-]?key|client[_-]?secret|credential)(?:$|[_-])",
    re.IGNORECASE,
)
SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"(?i)\b(?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*"
        r"[^\s,;]{4,}"
    ),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
INJECTION_PATTERNS = (
    re.compile(
        r"(?i)\bignore (?:all |any )?(?:previous|prior|system)\b.{0,40}\binstructions?\b"
    ),
    re.compile(r"(?i)\b(?:system|developer|assistant)\s*prompt\b"),
    re.compile(r"(?i)\b(?:reveal|print|exfiltrate|send)\b.{0,48}\b(?:secret|token|key)\b"),
    re.compile(r"(?i)<\s*/?\s*(?:system|assistant|developer|tool)\s*>"),
    re.compile(r"(?i)\bdo not follow\b.{0,32}\binstructions?\b"),
)


@dataclass(frozen=True)
class SanitizedEvidence:
    data: dict[str, Any]
    redaction_count: int
    dropped_fields: tuple[str, ...]
    injection_detected: bool
    injection_indicators: tuple[str, ...]


def sanitize_tool_payload(tool_name: str, payload: Any) -> SanitizedEvidence:
    if not isinstance(payload, dict):
        payload = {"error": "Connector returned a non-object payload."}
    allowed = TOOL_FIELD_ALLOWLIST.get(tool_name, frozenset({"error", "tool"}))
    dropped = tuple(sorted(str(key) for key in payload if str(key) not in allowed))
    selected = {str(key): value for key, value in payload.items() if str(key) in allowed}
    scrubbed, redactions = _scrub(selected, nested=False)
    indicators = detect_prompt_injection(scrubbed)
    return SanitizedEvidence(
        data=scrubbed if isinstance(scrubbed, dict) else {},
        redaction_count=redactions,
        dropped_fields=dropped,
        injection_detected=bool(indicators),
        injection_indicators=tuple(indicators),
    )


def agent_evidence_view(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in evidence[:MAX_COLLECTION_ITEMS]:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        content, redactions = _scrub_string(str(item.get("content", "")))
        indicators = detect_prompt_injection(content)
        recorded_indicators = metadata.get("injection_indicators", [])
        if isinstance(recorded_indicators, list):
            indicators = sorted({*indicators, *(str(value) for value in recorded_indicators)})
        if indicators:
            content = QUARANTINED
        title = _agent_safe_string(str(item.get("title", "")))
        result.append(
            {
                "id": str(item.get("id", "")),
                "source_type": str(item.get("source_type", "unknown")),
                "source_uri": _safe_uri(str(item.get("source_uri", ""))),
                "title": title,
                "content": content,
                "observed_at": str(item.get("observed_at", "")),
                "trust": {
                    "classification": "external_untrusted_observation",
                    "instructions_allowed": False,
                    "prompt_injection_detected": bool(indicators),
                    "indicators": indicators,
                    "redaction_count": _safe_int(
                        metadata.get("redaction_count", 0)
                    )
                    + redactions,
                },
            }
        )
    return result


def agent_incident_view(incident: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "id",
        "title",
        "severity",
        "service",
        "environment",
        "status",
        "description",
        "created_at",
        "updated_at",
    }
    result: dict[str, Any] = {}
    for key in allowed:
        value = incident.get(key)
        if isinstance(value, str):
            result[key] = _agent_safe_string(value)
        elif value is not None and isinstance(value, (int, float, bool)):
            result[key] = value
    result["trust"] = {
        "classification": "operator_supplied_untrusted_data",
        "instructions_allowed": False,
    }
    return result


def agent_memory_view(memory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in memory[:10]:
        title = _agent_safe_string(str(item.get("title", "")))
        root_cause = _agent_safe_string(str(item.get("root_cause", "")))
        resolution = _agent_safe_string(str(item.get("resolution", "")))
        result.append(
            {
                "incident_id": str(item.get("incident_id", item.get("id", ""))),
                "service": _agent_safe_string(str(item.get("service", ""))),
                "title": title,
                "root_cause": root_cause,
                "resolution": resolution,
                "similarity": item.get("similarity"),
                "trust": {
                    "classification": "historical_untrusted_observation",
                    "instructions_allowed": False,
                },
            }
        )
    return result


def detect_prompt_injection(value: Any) -> list[str]:
    text = _flatten_strings(value)
    return [
        pattern.pattern
        for pattern in INJECTION_PATTERNS
        if pattern.search(text)
    ]


def sanitize_source_uri(value: str) -> str:
    return _safe_uri(value)


def _scrub(value: Any, *, nested: bool) -> tuple[Any, int]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        redactions = 0
        for raw_key, raw_value in list(value.items())[:MAX_COLLECTION_ITEMS]:
            key = str(raw_key)
            if SENSITIVE_KEY.search(key):
                result[key] = REDACTED
                redactions += 1
                continue
            if nested and key not in NESTED_FIELD_ALLOWLIST:
                continue
            cleaned, count = _scrub(raw_value, nested=True)
            result[key] = cleaned
            redactions += count
        return result, redactions
    if isinstance(value, (list, tuple)):
        result = []
        redactions = 0
        for item in list(value)[:MAX_COLLECTION_ITEMS]:
            cleaned, count = _scrub(item, nested=True)
            result.append(cleaned)
            redactions += count
        return result, redactions
    if isinstance(value, str):
        return _scrub_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value, 0
    return _scrub_string(str(value))


def _scrub_string(value: str) -> tuple[str, int]:
    cleaned = value[:MAX_STRING_LENGTH]
    count = 0
    for pattern in SECRET_PATTERNS:
        cleaned, replacements = pattern.subn(REDACTED, cleaned)
        count += replacements
    return cleaned, count


def _flatten_strings(value: Any) -> str:
    if isinstance(value, dict):
        return "\n".join(
            f"{key}: {_flatten_strings(item)}" for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return "\n".join(_flatten_strings(item) for item in value)
    return str(value)


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _agent_safe_string(value: str) -> str:
    cleaned = _scrub_string(value)[0]
    return QUARANTINED if detect_prompt_injection(cleaned) else cleaned


def _safe_uri(value: str) -> str:
    value = re.sub(r"(?i)([?&](?:token|key|secret|authorization)=)[^&]+", rf"\1{REDACTED}", value)
    return value[:1024]
