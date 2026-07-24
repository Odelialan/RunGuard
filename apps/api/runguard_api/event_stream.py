from __future__ import annotations

import json
from typing import Any


class EventStream:
    """Redis Streams publisher with an explicit disabled mode for local demos."""

    def __init__(self, redis_url: str | None, stream: str) -> None:
        self.redis_url = redis_url
        self.stream = stream
        self._client: Any = None

    @property
    def enabled(self) -> bool:
        return bool(self.redis_url)

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

    async def publish(
        self,
        event_type: str,
        incident_id: str,
        payload: dict[str, Any],
    ) -> str | None:
        if self._client is None:
            return None
        return await self._client.xadd(
            self.stream,
            {
                "event_type": event_type,
                "incident_id": incident_id,
                "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
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
