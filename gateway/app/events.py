"""Real-time fan-out for task events: Redis pub/sub with an in-process fallback.

* ``publish`` always fans out to local subscribers and (when Redis is reachable)
  also publishes to ``devagent:events`` so several uvicorn workers - or a future
  second gateway instance - see the same stream.
* A payload carries the originating instance id, so a message coming back from
  Redis is not delivered twice.
* Redis is optional: if it is down the gateway keeps working, degraded only in
  the sense that other worker processes will not receive the events.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any, AsyncIterator

logger = logging.getLogger("devagent.events")


class EventBus:
    def __init__(self, redis_url: str | None, channel: str, *, queue_size: int = 256) -> None:
        self.redis_url = redis_url
        self.channel = channel
        self.instance_id = uuid.uuid4().hex[:8]
        self.backend = "memory"
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._redis: Any = None
        self._pubsub_task: asyncio.Task[None] | None = None
        self._queue_size = queue_size

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        if not self.redis_url:
            logger.info("event bus: no Redis configured, using in-process fan-out")
            return
        try:
            import redis.asyncio as aioredis  # imported lazily: optional dependency

            self._redis = aioredis.from_url(
                self.redis_url, encoding="utf-8", decode_responses=True
            )
            await self._redis.ping()
        except Exception as exc:  # pragma: no cover - depends on environment
            logger.warning("event bus: Redis unavailable (%s), falling back to memory", exc)
            self._redis = None
            return

        self.backend = "redis"
        self._pubsub_task = asyncio.create_task(self._listen(), name="devagent-eventbus")
        logger.info("event bus: Redis pub/sub active on %s", self.channel)

    async def close(self) -> None:
        if self._pubsub_task is not None:
            self._pubsub_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pubsub_task
            self._pubsub_task = None
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
            self._redis = None
        self._subscribers.clear()

    async def _listen(self) -> None:
        assert self._redis is not None
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self.channel)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    payload = json.loads(message["data"])
                except (TypeError, ValueError):
                    continue
                if payload.get("origin") == self.instance_id:
                    continue
                self._fanout(payload)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception as exc:  # pragma: no cover - network hiccup
            logger.warning("event bus: Redis listener stopped (%s)", exc)
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(self.channel)
                await pubsub.aclose()

    # -- pub/sub ---------------------------------------------------------
    async def publish(self, payload: dict[str, Any]) -> None:
        envelope = dict(payload)
        envelope["origin"] = self.instance_id
        self._fanout(envelope)
        if self._redis is not None:
            try:
                await self._redis.publish(self.channel, json.dumps(envelope))
            except Exception as exc:  # pragma: no cover - Redis restart
                logger.warning("event bus: publish failed (%s)", exc)

    def _fanout(self, payload: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:  # pragma: no cover - slow consumer
                logger.warning("event bus: dropping event for a saturated subscriber")

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
