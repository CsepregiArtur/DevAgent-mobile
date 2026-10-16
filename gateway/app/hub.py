"""WebSocket hub: one control connection per MacBook agent (agent-initiated).

The MacBook dials out to ``/mobile/agent/ws`` (NAT friendly, no inbound ports on
the laptop). The hub keeps the live sockets, forwards gateway -> agent commands
and exposes ``is_online`` so the scheduler can keep queueing tasks while the
laptop is asleep.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from .db import utcnow

logger = logging.getLogger("devagent.hub")

CLOSE_REPLACED = 4001
CLOSE_UNAUTHORIZED = 4401
CLOSE_SHUTDOWN = 1001


@dataclass
class AgentConnection:
    agent_id: str
    websocket: WebSocket
    connected_at: str = field(default_factory=utcnow)
    last_seen_monotonic: float = field(default_factory=lambda: asyncio.get_event_loop().time())
    capabilities: list[str] = field(default_factory=list)
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, payload: dict[str, Any]) -> bool:
        async with self._send_lock:
            try:
                await self.websocket.send_json(payload)
                return True
            except Exception as exc:  # pragma: no cover - broken socket
                logger.info("agent %s: send failed (%s)", self.agent_id, exc)
                return False


class AgentHub:
    def __init__(self) -> None:
        self._connections: dict[str, AgentConnection] = {}
        self._lock = asyncio.Lock()

    async def register(self, connection: AgentConnection) -> None:
        """Register a connection, replacing (and closing) any previous socket."""
        async with self._lock:
            previous = self._connections.get(connection.agent_id)
            self._connections[connection.agent_id] = connection
        if previous is not None:
            logger.info(
                "agent %s: replacing previous connection (%s)", connection.agent_id, previous.connected_at
            )
            with contextlib.suppress(Exception):
                await previous.websocket.close(code=CLOSE_REPLACED, reason="replaced by a new session")

    async def unregister(self, agent_id: str, websocket: WebSocket | None = None) -> bool:
        """Remove a connection; ignores stale unregisters from replaced sockets."""
        async with self._lock:
            current = self._connections.get(agent_id)
            if current is None:
                return False
            if websocket is not None and current.websocket is not websocket:
                return False
            del self._connections[agent_id]
            return True

    def get(self, agent_id: str) -> AgentConnection | None:
        return self._connections.get(agent_id)

    def is_online(self, agent_id: str) -> bool:
        return agent_id in self._connections

    def online_agents(self) -> list[str]:
        return list(self._connections)

    def mark_seen(self, agent_id: str) -> None:
        connection = self._connections.get(agent_id)
        if connection is not None:
            connection.last_seen_monotonic = asyncio.get_event_loop().time()

    def seconds_since_seen(self, agent_id: str) -> float | None:
        connection = self._connections.get(agent_id)
        if connection is None:
            return None
        return asyncio.get_event_loop().time() - connection.last_seen_monotonic

    async def send_to_agent(self, agent_id: str, payload: dict[str, Any]) -> bool:
        connection = self._connections.get(agent_id)
        if connection is None:
            return False
        return await connection.send(payload)

    async def send_to_any(self, payload: dict[str, Any], *, exclude: str | None = None) -> int:
        delivered = 0
        for agent_id, connection in list(self._connections.items()):
            if agent_id == exclude:
                continue
            if await connection.send(payload):
                delivered += 1
        return delivered

    async def close_all(self) -> None:
        async with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
        for connection in connections:
            with contextlib.suppress(Exception):
                await connection.websocket.close(code=CLOSE_SHUTDOWN, reason="gateway shutting down")

    @property
    def connection_count(self) -> int:
        return len(self._connections)
