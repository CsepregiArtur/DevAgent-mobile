"""Agent control channel: ``/mobile/agent/ws`` (WebSocket, initiated by the laptop).

Why a single outbound WebSocket?
    * no inbound port on the MacBook, works behind NAT / hotel Wi-Fi;
    * the gateway can push work instantly (``task_assign`` / ``task_resume``);
    * liveness is cheap (heartbeats + ping/pong), and a dropped socket is
      detected within ``DEVAGENT_AGENT_OFFLINE_SECONDS``.

Message direction (JSON, ``type`` discriminator):

agent -> gateway
    hello, heartbeat, event, progress, checkpoint, approval_request, result,
    log, ping, cancel_ack

gateway -> agent
    welcome, task_assign, task_resume, approval_decision, cancel, ping, error

Every agent message carrying a ``taskId`` is acknowledged with the matching
``*_ack`` message so the agent can implement at-least-once delivery with the
idempotency keys (``idempotencyKey`` on checkpoints/events).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from .auth import resolve_token
from .config import Settings
from .db import utcnow
from .hub import CLOSE_UNAUTHORIZED, AgentConnection, AgentHub
from .service import ServiceError, TaskService

logger = logging.getLogger("devagent.agentapi")

SUPPORTED_AGENT_MESSAGE_TYPES = {
    "hello",
    "heartbeat",
    "event",
    "progress",
    "checkpoint",
    "approval_request",
    "result",
    "log",
    "ping",
    "cancel_ack",
}


def build_agent_router(service: TaskService, hub: AgentHub, settings: Settings) -> APIRouter:
    router = APIRouter(tags=["agent"])

    async def _reject(
        websocket: WebSocket,
        code: str,
        message: str,
        *,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Tell the client *why* before closing.

        Starlette answers an HTTP 403 to a ``close()`` issued before ``accept()``,
        which reaches the client as an opaque ``InvalidStatus: HTTP 403``. Accepting
        first lets us send a readable error frame (the wizard prints it verbatim).
        """
        await websocket.accept()
        payload: dict[str, Any] = {"type": "error", "code": code, "message": message}
        if extra:
            payload.update(extra)
        with contextlib.suppress(Exception):
            await websocket.send_json(payload)
        await websocket.close(code=CLOSE_UNAUTHORIZED, reason=reason)


    @router.websocket("/agent/ws")
    async def agent_ws(
        websocket: WebSocket,
        agent_id: str | None = Query(default=None, alias="agentId"),
        token: str | None = Query(default=None),
    ) -> None:
        provided = _extract_token(websocket, token)
        context = await resolve_token(service.db, settings, provided)
        if context is None:
            logger.warning("agent websocket rejected: invalid or revoked token")
            await _reject(
                websocket,
                "invalid_credentials",
                "Unknown, expired or revoked key. Generate a new one: app → Settings → "
                "API keys → kind 'agent' → Generate.",
                reason="Invalid agent credentials",
            )
            return
        if not (context.has("agent") or context.has("admin")):
            logger.warning(
                "agent websocket rejected: token kind=%s scopes=%s", context.kind, context.scopes
            )
            await _reject(
                websocket,
                "wrong_token_kind",
                f"That key is of kind '{context.kind}', but the agent channel needs kind "
                "'agent'. Create one in the app: Settings → API keys → kind 'agent'.",
                reason="Wrong key kind",
                extra={"kind": context.kind, "granted": list(context.scopes)},
            )
            return

        await websocket.accept()
        resolved_id = (agent_id or websocket.query_params.get("agentId") or "macbook-agent").strip()
        connection = AgentConnection(agent_id=resolved_id, websocket=websocket)
        await hub.register(connection)
        logger.info("agent %s connected from %s", resolved_id, websocket.client)
        await service.publish_agent_status()

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError):
                    await _safe_send(connection, {"type": "error", "error": "Malformed JSON payload"})
                    continue
                if not isinstance(message, dict):
                    await _safe_send(connection, {"type": "error", "error": "Expected a JSON object"})
                    continue
                await _dispatch(service, hub, settings, connection, message)
        except WebSocketDisconnect:
            logger.info("agent %s disconnected", resolved_id)
        except Exception:  # pragma: no cover - unexpected transport failure
            logger.exception("agent %s channel failed", resolved_id)
        finally:
            removed = await hub.unregister(resolved_id, websocket)
            if removed:
                # Grace period: the watchdog decides between "reconnecting" and
                # "offline" (and interrupts the active task) after the timeout.
                await service.db.set_agent_status(resolved_id, "reconnecting")
                await service.publish_agent_status()

    # ------------------------------------------------------------------
    # HTTPS long-poll fallback
    #
    # Some networks (bot protection, corporate proxies) answer 403 to a
    # WebSocket upgrade but happily proxy plain HTTPS. The same protocol is
    # tunnelled over POST: agent -> gateway via /agent/send, gateway -> agent
    # via /agent/poll. Both are mounted under /mobile and /mobile/api.
    # ------------------------------------------------------------------
    async def _http_connection(agent_id: str) -> AgentConnection:
        connection = hub.get(agent_id)
        if connection is None:
            connection = AgentConnection(
                agent_id=agent_id, transport="http", queue=asyncio.Queue()
            )
            await hub.register(connection)
            logger.info("agent %s registered over https long-poll", agent_id)
        elif connection.transport != "http":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "This agent is connected over websocket",
                    "transport": connection.transport,
                },
            )
        return connection

    def _agent_id(payload: dict[str, Any], request: Request) -> str:
        agent_id = str(payload.get("agentId") or "").strip()
        if not agent_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "Provide 'agentId' (it must match DEVAGENT_AGENT_ID)"},
            )
        return agent_id

    async def _drain(connection: AgentConnection) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        queue = connection.queue
        if queue is None:
            return messages
        while True:
            try:
                messages.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                return messages

    @router.post("/agent/poll")
    async def agent_poll(
        request: Request, payload: dict[str, Any] = Body(default={})
    ) -> dict[str, Any]:
        """Long-poll: hold the request until there is something for the agent."""
        agent_id = _agent_id(payload, request)
        try:
            wait = max(0.0, min(float(payload.get("wait") or 0), 50.0))
        except (TypeError, ValueError):
            wait = 0.0
        connection = await _http_connection(agent_id)
        hub.mark_seen(agent_id)

        messages = await _drain(connection)
        if not messages and wait > 0 and not connection.closed:
            queue = connection.queue
            if queue is not None:
                with contextlib.suppress(asyncio.TimeoutError):
                    messages.append(await asyncio.wait_for(queue.get(), timeout=wait))
                messages.extend(await _drain(connection))

        if connection.closed and not messages:
            return {
                "transport": "http",
                "replaced": True,
                "messages": [],
                "serverTime": utcnow(),
            }
        return {
            "transport": "http",
            "agentId": agent_id,
            "messages": messages,
            "serverTime": utcnow(),
        }

    @router.post("/agent/send")
    async def agent_send(
        request: Request, payload: dict[str, Any] = Body(default={})
    ) -> dict[str, Any]:
        """Feed agent -> gateway messages; returns the acknowledgements (if any)."""
        agent_id = _agent_id(payload, request)
        message = payload.get("message")
        if not isinstance(message, dict):
            raise HTTPException(
                status_code=400, detail={"error": "Expected 'message' to be a JSON object"}
            )
        connection = await _http_connection(agent_id)
        hub.mark_seen(agent_id)
        await _dispatch(service, hub, settings, connection, message)
        acks = await _drain(connection)
        return {
            "transport": "http",
            "agentId": agent_id,
            "messages": acks,
            "serverTime": utcnow(),
        }

    @router.post("/agent/disconnect")
    async def agent_disconnect(
        request: Request, payload: dict[str, Any] = Body(default={})
    ) -> dict[str, Any]:
        """Graceful shutdown: drop the long-poll channel."""
        agent_id = _agent_id(payload, request)
        connection = hub.get(agent_id)
        removed = False
        if connection is not None and connection.transport == "http":
            await connection.stop()
            removed = await hub.remove_connection(connection)
        if removed:
            await service.db.set_agent_status(agent_id, "reconnecting")
            await service.publish_agent_status()
        return {"ok": True, "removed": removed, "serverTime": utcnow()}

    return router


def _extract_token(websocket: WebSocket, query_token: str | None) -> str:
    if query_token:
        return query_token.strip()
    header = websocket.headers.get("x-devagent-token")
    if header:
        return header.strip()
    authorization = websocket.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return (websocket.query_params.get("token") or "").strip()


async def _safe_send(connection: AgentConnection, payload: dict[str, Any]) -> None:
    await connection.send(payload)


async def _dispatch(
    service: TaskService,
    hub: AgentHub,
    settings: Settings,
    connection: AgentConnection,
    message: dict[str, Any],
) -> None:
    message_type = str(message.get("type") or "").lower()
    agent_id = connection.agent_id
    hub.mark_seen(agent_id)

    try:
        if message_type == "hello":
            await _handle_hello(service, settings, connection, message)
        elif message_type == "heartbeat":
            await _handle_heartbeat(service, connection, message)
        elif message_type in {"event", "progress", "log"}:
            await service.handle_agent_event(agent_id, message)
            await _safe_send(
                connection,
                {"type": "event_ack", "taskId": message.get("taskId"), "serverTime": utcnow()},
            )
        elif message_type == "checkpoint":
            checkpoint = await service.save_checkpoint(agent_id, message)
            await _safe_send(
                connection,
                {
                    "type": "checkpoint_ack",
                    "taskId": message.get("taskId"),
                    "checkpointId": (checkpoint or {}).get("id"),
                    "idempotencyKey": message.get("idempotencyKey"),
                    "serverTime": utcnow(),
                },
            )
        elif message_type == "approval_request":
            approval = await service.submit_approval_request(agent_id, message)
            await _safe_send(
                connection,
                {
                    "type": "approval_ack",
                    "taskId": message.get("taskId"),
                    "approvalId": approval["id"],
                    "gate": approval["gate"],
                    "hash": approval["hash"],
                    "state": approval["state"],
                    "expiresAt": approval.get("expiresAt"),
                    "serverTime": utcnow(),
                },
            )
        elif message_type == "result":
            await service.report_result(agent_id, message)
            await _safe_send(
                connection,
                {
                    "type": "result_ack",
                    "taskId": message.get("taskId"),
                    "serverTime": utcnow(),
                },
            )
        elif message_type == "ping":
            await _safe_send(connection, {"type": "pong", "serverTime": utcnow()})
        elif message_type == "cancel_ack":
            await _safe_send(connection, {"type": "cancel_ack", "ack": True, "serverTime": utcnow()})
        else:
            await _safe_send(
                connection,
                {
                    "type": "error",
                    "error": f"Unsupported message type: {message_type!r}",
                    "supported": sorted(SUPPORTED_AGENT_MESSAGE_TYPES),
                },
            )
    except ServiceError as error:
        await _safe_send(
            connection,
            {
                "type": "error",
                "taskId": message.get("taskId"),
                "error": error.message,
                "details": error.extra or None,
            },
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("agent %s: failed to handle %s", agent_id, message_type)
        await _safe_send(
            connection, {"type": "error", "error": f"Gateway failed to process {message_type!r}"}
        )


async def _handle_hello(
    service: TaskService, settings: Settings, connection: AgentConnection, message: dict[str, Any]
) -> None:
    agent_id = connection.agent_id
    capabilities = message.get("capabilities") or []
    if isinstance(capabilities, list):
        connection.capabilities = [str(item) for item in capabilities]

    await service.db.upsert_agent(
        agent_id=agent_id,
        hostname=message.get("hostname") or agent_id,
        os_name=message.get("os"),
        version=message.get("version"),
        workspace=message.get("workspace"),
        status="online",
        active_task_id=message.get("activeTaskId"),
        battery_level=message.get("batteryLevel"),
        capabilities=connection.capabilities,
        is_simulated=False,
        mark_connected=True,
    )

    # Announce first (with the list of tasks about to be resumed), then push the
    # task_resume messages: the agent always sees the handshake before any work.
    pending = [task["id"] for task in await service.db.tasks_pending_agent(agent_id)]

    await connection.send(
        {
            "type": "welcome",
            "agentId": agent_id,
            "serverTime": utcnow(),
            "heartbeatIntervalSeconds": settings.heartbeat_interval_seconds,
            "agentOfflineSeconds": settings.agent_offline_seconds,
            "approvalTimeoutSeconds": settings.approval_timeout_seconds,
            "rootPath": settings.root_path,
            "defaultWorkspace": settings.default_workspace,
            "release": service.version,
            "resumedTasks": pending,
            "nextTaskId": (await service.db.next_queued_task() or {}).get("id"),
        }
    )

    await service.resume_tasks_for_agent(agent_id)
    await service.dispatch_pending()
    await service.publish_agent_status()


async def _handle_heartbeat(
    service: TaskService, connection: AgentConnection, message: dict[str, Any]
) -> None:
    agent_id = connection.agent_id
    active_task_id = message.get("activeTaskId")
    await service.db.upsert_agent(
        agent_id=agent_id,
        hostname=message.get("hostname") or agent_id,
        os_name=message.get("os"),
        version=message.get("version"),
        workspace=message.get("workspace"),
        status="busy" if active_task_id else "online",
        active_task_id=active_task_id,
        battery_level=message.get("batteryLevel"),
        is_simulated=False,
    )
    await service.publish_agent_status()
    await connection.send(
        {
            "type": "heartbeat_ack",
            "serverTime": utcnow(),
            "hasWork": bool(await service.db.next_queued_task()),
        }
    )
