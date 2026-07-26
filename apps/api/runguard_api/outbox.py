from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from uuid import uuid4

from .event_stream import EventStream
from .store import Store

logger = logging.getLogger(__name__)


class OutboxDispatcher:
    """Publishes database-committed events to Redis Streams with at-least-once delivery."""

    def __init__(
        self,
        store: Store,
        event_stream: EventStream,
        *,
        poll_interval_seconds: float = 1.0,
        batch_size: int = 100,
    ) -> None:
        self.store = store
        self.event_stream = event_stream
        self.poll_interval_seconds = poll_interval_seconds
        self.batch_size = batch_size
        self.worker_id = f"outbox-{uuid4().hex}"
        self._stop = asyncio.Event()
        self._flush_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        return self.store.outbox_enabled and self.event_stream.enabled

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._stop.clear()
        await self.flush_once()
        self._task = asyncio.create_task(
            self._run(),
            name=f"runguard-{self.worker_id}",
        )

    async def close(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=5.0)
        except TimeoutError:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def flush_once(self) -> int:
        if not self.enabled or not self.event_stream.ready:
            return 0
        published = 0
        async with self._flush_lock:
            events = await asyncio.to_thread(
                self.store.claim_outbox,
                self.worker_id,
                limit=self.batch_size,
            )
            for event in events:
                try:
                    stream_id = await self.event_stream.publish(
                        event["event_type"],
                        event["incident_id"],
                        event["payload"],
                        event_id=event["id"],
                    )
                    if stream_id is None:
                        raise RuntimeError("Redis Stream publisher is unavailable.")
                    marked = await asyncio.to_thread(
                        self.store.mark_outbox_published,
                        event["id"],
                        self.worker_id,
                    )
                    if not marked:
                        raise RuntimeError("Outbox claim expired before acknowledgement.")
                    published += 1
                except Exception as exc:
                    await asyncio.to_thread(
                        self.store.release_outbox_claim,
                        event["id"],
                        self.worker_id,
                        str(exc),
                    )
            if published:
                await asyncio.to_thread(self.store.prune_published_outbox)
        return published

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.flush_once()
            except Exception:
                # The row remains unacknowledged and is retried on the next poll.
                logger.exception("Outbox flush failed; the batch will be retried.")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.poll_interval_seconds,
                )
            except TimeoutError:
                continue
