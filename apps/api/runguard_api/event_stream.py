from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, TypeVar
from uuid import uuid4

T = TypeVar("T")


class LockUnavailable(RuntimeError):
    pass


class LockLeaseLost(RuntimeError):
    pass


class EventStream:
    """Redis Streams publisher with an explicit disabled mode for local demos."""

    def __init__(self, redis_url: str | None, stream: str) -> None:
        self.redis_url = redis_url
        self.stream = stream
        self._client: Any = None

    @property
    def enabled(self) -> bool:
        return bool(self.redis_url)

    @property
    def ready(self) -> bool:
        return self._client is not None

    async def connect(self) -> None:
        if not self.redis_url:
            return
        from redis.asyncio import Redis

        self._client = Redis.from_url(
            self.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            health_check_interval=30,
        )
        await self._client.ping()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def publish(
        self,
        event_type: str,
        incident_id: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> str | None:
        if self._client is None:
            return None
        return await self._client.xadd(
            self.stream,
            {
                "event_type": event_type,
                "incident_id": incident_id,
                "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                **({"event_id": event_id} if event_id else {}),
            },
            maxlen=100_000,
            approximate=True,
        )

    async def recent(self, count: int = 100) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        rows = await self._client.xrevrange(self.stream, count=count)
        return [
            {
                "id": row_id,
                **fields,
                "payload": json.loads(fields.get("payload", "{}")),
            }
            for row_id, fields in rows
        ]

    async def ensure_consumer_group(self, group: str) -> None:
        if self._client is None:
            return
        from redis.exceptions import ResponseError

        try:
            await self._client.xgroup_create(
                self.stream,
                group,
                id="0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def consume(
        self,
        group: str,
        consumer: str,
        *,
        count: int = 10,
        block_ms: int = 5000,
    ) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        await self.ensure_consumer_group(group)
        batches = await self._client.xreadgroup(
            group,
            consumer,
            {self.stream: ">"},
            count=count,
            block=block_ms,
        )
        return [
            {
                "id": row_id,
                **fields,
                "payload": json.loads(fields.get("payload", "{}")),
            }
            for _, rows in batches
            for row_id, fields in rows
        ]

    async def acknowledge(self, group: str, *event_ids: str) -> int:
        if self._client is None or not event_ids:
            return 0
        return int(await self._client.xack(self.stream, group, *event_ids))

    async def health(self) -> str:
        if not self.redis_url:
            return "disabled"
        if self._client is None:
            return "unavailable"
        try:
            return "ready" if await self._client.ping() else "unavailable"
        except Exception:
            return "unavailable"

    async def acquire_lock(self, resource: str, ttl_seconds: int = 300) -> str | None:
        token = uuid4().hex
        if self._client is None:
            return token
        acquired = await self._client.set(
            f"runguard:lock:{resource}",
            token,
            ex=ttl_seconds,
            nx=True,
        )
        return token if acquired else None

    async def release_lock(self, resource: str, token: str) -> None:
        if self._client is None:
            return
        script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
          return redis.call('DEL', KEYS[1])
        end
        return 0
        """
        await self._client.eval(script, 1, f"runguard:lock:{resource}", token)

    async def renew_lock(
        self,
        resource: str,
        token: str,
        ttl_seconds: int,
    ) -> bool:
        if self._client is None:
            return True
        script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
          return redis.call('EXPIRE', KEYS[1], ARGV[2])
        end
        return 0
        """
        renewed = await self._client.eval(
            script,
            1,
            f"runguard:lock:{resource}",
            token,
            ttl_seconds,
        )
        return bool(renewed)

    async def run_with_lock(
        self,
        resource: str,
        ttl_seconds: int,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        token = await self.acquire_lock(resource, ttl_seconds)
        if token is None:
            raise LockUnavailable(resource)
        if self._client is None:
            try:
                return await operation()
            finally:
                await self.release_lock(resource, token)

        operation_task = asyncio.create_task(operation())
        renewal_task = asyncio.create_task(
            self._renew_until_lost(resource, token, ttl_seconds)
        )
        try:
            done, _ = await asyncio.wait(
                {operation_task, renewal_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if operation_task in done:
                return await operation_task
            operation_task.cancel()
            with suppress(asyncio.CancelledError):
                await operation_task
            raise LockLeaseLost(resource)
        finally:
            renewal_task.cancel()
            with suppress(asyncio.CancelledError):
                await renewal_task
            with suppress(Exception):
                await self.release_lock(resource, token)

    async def _renew_until_lost(
        self,
        resource: str,
        token: str,
        ttl_seconds: int,
    ) -> None:
        interval = min(max(ttl_seconds / 3, 0.1), 30.0)
        deadline = time.monotonic() + ttl_seconds
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await self.renew_lock(resource, token, ttl_seconds)
            except Exception:
                renewed = False
            if renewed:
                deadline = time.monotonic() + ttl_seconds
            elif time.monotonic() >= deadline:
                return
