"""Background loops: heartbeat watchdog, approval expiry and queue dispatch.

Everything the gateway must do while nobody is looking lives here:

* an agent that stops heartbeating is marked ``offline`` and its in-flight tasks
  are interrupted safely (queued again, resumable from checkpoint);
* queued tasks are dispatched FIFO as soon as an agent is connected and idle;
* pending approvals can expire after ``DEVAGENT_APPROVAL_TIMEOUT_SECONDS``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .config import Settings
from .db import Database, parse_ts, utcnow
from .hub import AgentHub
from .service import TaskService

logger = logging.getLogger("devagent.scheduler")


class Scheduler:
    def __init__(
        self, service: TaskService, db: Database, hub: AgentHub, settings: Settings
    ) -> None:
        self.service = service
        self.db = db
        self.hub = hub
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="devagent-scheduler")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:  # pragma: no cover - shutdown
                raise
            except Exception:  # pragma: no cover - never kill the loop
                logger.exception("scheduler tick failed")
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self.settings.queue_scan_seconds
                )
            except asyncio.TimeoutError:
                continue

    async def tick(self) -> None:
        await self._heartbeat_watchdog()
        await self._expire_approvals()
        await self.service.dispatch_pending()

    # -- watchdog --------------------------------------------------------
    async def _heartbeat_watchdog(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        cutoff = (
            (now - timedelta(seconds=self.settings.agent_offline_seconds))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        for agent in await self.db.stale_agents(cutoff):
            if self.hub.is_online(agent["agent_id"]):
                # A socket is attached: liveness is judged by the socket itself
                # below (a just-reconnected agent may not have sent ``hello`` yet).
                continue
            logger.warning(
                "agent %s missed %ss of heartbeats -> offline",
                agent["agent_id"],
                self.settings.agent_offline_seconds,
            )
            await self.service.mark_agent_offline(agent["agent_id"])

        # Sockets that are technically open but silent for twice the interval.
        for agent_id in self.hub.online_agents():
            seconds = self.hub.seconds_since_seen(agent_id)
            if seconds is not None and seconds > self.settings.agent_offline_seconds * 2:
                logger.warning("agent %s socket silent for %.0fs -> closing", agent_id, seconds)
                connection = self.hub.get(agent_id)
                if connection is not None:
                    with contextlib.suppress(Exception):
                        await connection.websocket.close(code=4000, reason="stale socket")
                    await self.hub.unregister(agent_id, connection.websocket)
                await self.service.mark_agent_offline(agent_id)

    # -- approvals -------------------------------------------------------
    async def _expire_approvals(self) -> None:
        if self.settings.approval_timeout_seconds <= 0:
            return
        for approval in await self.db.expired_pending_approvals():
            logger.info(
                "approval %s for task %s expired", approval["id"], approval["task_id"]
            )
            await self.service.expire_approval(approval)

    # -- housekeeping helpers used by tests ------------------------------
    async def heartbeat_age_seconds(self, agent_id: str) -> float | None:
        agent = await self.db.get_agent(agent_id)
        if agent is None:
            return None
        last = parse_ts(agent["last_heartbeat"])
        if last is None:
            return None
        from datetime import datetime, timezone

        return (datetime.now(timezone.utc) - last).total_seconds()

    async def touch_now(self, agent_id: str) -> str:
        return utcnow() if agent_id else utcnow()
