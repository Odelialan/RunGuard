from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import jwt
from fastapi import Request
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from .config import Settings


@dataclass(frozen=True)
class AuthContext:
    subject: str
    roles: frozenset[str]
    auth_mode: str


ROLE_GRANTS = {
    "viewer": {"viewer"},
    "operator": {"viewer", "operator"},
    "approver": {"viewer", "operator", "approver"},
    "admin": {"viewer", "operator", "approver", "service", "admin"},
    "service": {"service"},
}


class RateLimiter:
    def __init__(self, redis_url: str | None, limit: int) -> None:
        self.redis_url = redis_url
        self.limit = max(limit, 1)
        self._client: Any = None
        self._local: dict[str, tuple[int, int]] = {}
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        if not self.redis_url:
            return
        from redis.asyncio import Redis

        self._client = Redis.from_url(self.redis_url, decode_responses=True)
        await self._client.ping()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def check(self, key: str) -> tuple[bool, int, int]:
        now = int(time.time())
        window = now // 60
        reset = (window + 1) * 60
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        if self._client is not None:
            redis_key = f"runguard:rate-limit:{window}:{digest}"
            script = """
            local count = redis.call('INCR', KEYS[1])
            if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
            return count
            """
            count = int(await self._client.eval(script, 1, redis_key, 70))
        else:
            async with self._lock:
                previous_window, count = self._local.get(digest, (window, 0))
                count = count + 1 if previous_window == window else 1
                self._local[digest] = (window, count)
                if len(self._local) > 10_000:
                    self._local = {
                        item_key: item_value
                        for item_key, item_value in self._local.items()
                        if item_value[0] == window
                    }
        return count <= self.limit, max(self.limit - count, 0), reset


class SecurityManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.rate_limiter = RateLimiter(
            settings.redis_url,
            settings.rate_limit_per_minute,
        )
        self._api_keys = self._load_api_keys(settings.api_keys_json)
        self._jwks = (
            PyJWKClient(settings.oidc_jwks_url)
            if settings.auth_mode == "oidc" and settings.oidc_jwks_url
            else None
        )

    async def connect(self) -> None:
        await self.rate_limiter.connect()

    async def close(self) -> None:
        await self.rate_limiter.close()

    @staticmethod
    def _load_api_keys(raw: str) -> list[tuple[str, AuthContext]]:
        try:
            values = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("RUNGUARD_API_KEYS_JSON is not valid JSON.") from exc
        if not isinstance(values, dict):
            raise RuntimeError("RUNGUARD_API_KEYS_JSON must be a JSON object.")
        result: list[tuple[str, AuthContext]] = []
        for key, value in values.items():
            if isinstance(value, str):
                subject = value
                roles = {"admin"}
            elif isinstance(value, dict):
                subject = str(value.get("subject") or "api-client")
                raw_roles = value.get("roles", ["viewer"])
                roles = {str(role).lower() for role in raw_roles}
            else:
                raise RuntimeError("Every API key entry must be a subject or role object.")
            result.append(
                (
                    str(key),
                    AuthContext(
                        subject=subject,
                        roles=frozenset(roles),
                        auth_mode="api_key",
                    ),
                )
            )
        return result

    async def authenticate(self, request: Request) -> AuthContext:
        if self.settings.auth_mode == "disabled":
            return AuthContext(
                subject="local-demo",
                roles=frozenset({"admin"}),
                auth_mode="disabled",
            )
        authorization = request.headers.get("authorization", "")
        token = (
            authorization.removeprefix("Bearer ").strip()
            if authorization.startswith("Bearer ")
            else request.headers.get("x-api-key", "").strip()
        )
        if not token:
            raise PermissionError("Missing bearer token.")
        if self.settings.auth_mode == "api_key":
            return self._authenticate_api_key(token)
        return await asyncio.to_thread(self._authenticate_oidc, token)

    def _authenticate_api_key(self, token: str) -> AuthContext:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        for configured, context in self._api_keys:
            candidate = token_hash if configured.startswith("sha256:") else token
            expected = configured.removeprefix("sha256:")
            if hmac.compare_digest(candidate, expected):
                return context
        raise PermissionError("Invalid API key.")

    def _authenticate_oidc(self, token: str) -> AuthContext:
        if self._jwks is None:
            raise PermissionError("OIDC JWKS client is not configured.")
        signing_key = self._jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=list(self.settings.oidc_algorithms),
            audience=self.settings.oidc_audience,
            issuer=self.settings.oidc_issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
        roles_value: Any = claims
        for part in self.settings.oidc_roles_claim.split("."):
            roles_value = roles_value.get(part, {}) if isinstance(roles_value, dict) else {}
        roles = set(roles_value if isinstance(roles_value, list) else [])
        roles.update(str(claims.get("scope", "")).split())
        normalized = {
            str(role).lower().removeprefix("runguard:")
            for role in roles
            if str(role)
        }
        return AuthContext(
            subject=str(claims["sub"]),
            roles=frozenset(normalized),
            auth_mode="oidc",
        )

    @staticmethod
    def authorize(context: AuthContext, required: str) -> bool:
        granted = {
            permission
            for role in context.roles
            for permission in ROLE_GRANTS.get(role, set())
        }
        return required in granted


PUBLIC_PATHS = {
    "/api/health",
    "/api/ready",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/.well-known/agent-card.json",
    "/api/alerts/prometheus",
    "/",
}


def required_role(request: Request) -> str | None:
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/assets/"):
        return None
    if path == "/a2a/reviewer":
        return "service"
    if not path.startswith("/api/"):
        return None
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return "viewer"
    if path.startswith("/api/tool-intents/") and path.rsplit("/", 1)[-1] in {
        "approve",
        "reject",
        "edit",
    }:
        return "approver"
    return "operator"


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, manager: SecurityManager) -> None:
        super().__init__(app)
        self.manager = manager

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = request.headers.get("x-request-id", "")
        if not request_id or len(request_id) > 128:
            request_id = uuid4().hex
        required = required_role(request)
        context = AuthContext("public", frozenset(), "public")
        if required:
            try:
                context = await self.manager.authenticate(request)
            except (PermissionError, jwt.PyJWTError) as exc:
                return self._error(401, str(exc), request_id)
            except Exception:
                return self._error(503, "Identity provider is unavailable.", request_id)
            if not self.manager.authorize(context, required):
                return self._error(403, f"Role {required!r} is required.", request_id)
            try:
                allowed, remaining, reset = await self.manager.rate_limiter.check(
                    f"{context.auth_mode}:{context.subject}"
                )
            except Exception:
                return self._error(503, "Distributed rate limiter is unavailable.", request_id)
            if not allowed:
                response = self._error(429, "Rate limit exceeded.", request_id)
                response.headers["Retry-After"] = str(max(reset - int(time.time()), 1))
                return response
        elif request.url.path == "/api/alerts/prometheus":
            client_host = request.client.host if request.client else "unknown"
            try:
                allowed, remaining, reset = await self.manager.rate_limiter.check(
                    f"prometheus-webhook:{client_host}"
                )
            except Exception:
                return self._error(503, "Distributed rate limiter is unavailable.", request_id)
            if not allowed:
                response = self._error(429, "Rate limit exceeded.", request_id)
                response.headers["Retry-After"] = str(max(reset - int(time.time()), 1))
                return response
        else:
            remaining = self.manager.settings.rate_limit_per_minute
            reset = (int(time.time()) // 60 + 1) * 60
        request.state.auth = context
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = (
            "no-store" if request.url.path.startswith("/api/") else "no-cache"
        )
        if self.manager.settings.enforce_production_guards:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
                "form-action 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; connect-src 'self'; font-src 'self'"
            )
        if required or request.url.path == "/api/alerts/prometheus":
            response.headers["X-RateLimit-Limit"] = str(
                self.manager.settings.rate_limit_per_minute
            )
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Reset"] = str(reset)
        return response

    @staticmethod
    def _error(status: int, detail: str, request_id: str) -> JSONResponse:
        response = JSONResponse(
            {"detail": detail, "request_id": request_id},
            status_code=status,
        )
        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if status == 401:
            response.headers["WWW-Authenticate"] = "Bearer"
        return response


def verify_webhook_signature(
    secret: str | None,
    body: bytes,
    signature: str | None,
    timestamp: str | None = None,
    *,
    max_age_seconds: int = 300,
) -> None:
    if not secret:
        return
    try:
        signed_at = int(timestamp or "")
    except ValueError as exc:
        raise PermissionError("Missing or invalid webhook timestamp.") from exc
    if abs(int(time.time()) - signed_at) > max_age_seconds:
        raise PermissionError("Webhook timestamp is outside the accepted window.")
    signed_payload = f"{signed_at}.".encode() + body
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise PermissionError("Invalid Prometheus webhook signature.")


def request_actor(request: Request, fallback: str) -> str:
    context = getattr(request.state, "auth", None)
    return context.subject if context and context.subject != "public" else fallback
