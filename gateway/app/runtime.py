"""Process-wide runtime flags shared by the HTTP, SSE and WebSocket layers.

``STOPPING`` is set by the lifespan shutdown hook. Long-lived responses (SSE
streams, the agent's long-poll) watch it so a restart does not have to wait for
them to time out — otherwise systemd keeps the old process alive until it is
SIGKILLed and the tunnel answers ``502`` in the meantime.
"""

from __future__ import annotations

import asyncio

STOPPING: asyncio.Event = asyncio.Event()
