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

import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

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

    @router.websocket("/agent/ws")
    async def agent_ws(
        websocket: WebSocket,
        agent_id: str | None = Query(default=None, alias="agentId"),
        token: str | None = Query(default=None),
    ) -> None:
        provided = _extract_token(websocket, token)
        expected = settings.agent_token
        if not provided or not hmac.compare_digest(provided, expected):
            logger.warning("agent websocket rejected: invalid token")
            close_code = CLOSE_UNAUTHORIZED
            await websocket.close(code=close_code, reason="Invalid agent credentials")
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

    # A real agent is enrolled: the built-in simulator must stop stealing tasks.
    if await service.simulator_enabled():
        await service.set_simulator(False)
        logger.info("simulator disabled because agent %s connected", agent_id)

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
