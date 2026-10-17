"""REST + SSE surface consumed by the PWA / iOS app (``/mobile/api/**``).

The wire contract is deliberately identical to the one shipped in the mobile
repo (``mobile/server.ts`` mock) so the existing React screens keep working:
``/api/agent/status``, ``/api/tasks*``, ``/api/diagnostics``, ``/api/workspaces``,
``/api/events`` (SSE) and ``/api/tasks/{id}/events`` (SSE, replayable).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from .config import Settings
from .db import utcnow
from .models import (
    ApprovalDecisionPayload,
    CreateTaskPayload,
    HeartbeatPayload,
    normalize_status,
)
from .runtime import STOPPING
from .service import ServiceError, TaskService

logger = logging.getLogger("devagent.api")

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

KEEPALIVE_SECONDS = 15.0


def _raise(error: ServiceError) -> HTTPException:
    payload: dict[str, Any] = {"error": error.message}
    payload.update(error.extra)
    return HTTPException(status_code=error.status_code, detail=payload)


def build_router(service: TaskService, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["pwa"])

    # ------------------------------------------------------------------
    # agent status
    # ------------------------------------------------------------------
    @router.get("/agent/status")
    async def agent_status() -> dict[str, Any]:
        return await service.agent_status_snapshot()

    @router.get("/agents")
    async def agents() -> dict[str, Any]:
        """The fleet: every known agent with liveness + capabilities."""
        from .models import agent_to_dict

        snapshot = await service.agent_status_snapshot()
        rows = []
        for agent in await service.db.list_agents():
            payload = agent_to_dict(agent) or {}
            payload["online"] = service.hub.is_online(agent["agent_id"])
            payload["secondsSinceHeartbeat"] = service._heartbeat_age_seconds(agent)
            rows.append(payload)
        return {
            "agents": rows,
            "onlineAgents": snapshot["onlineAgents"],
            "totalAgents": snapshot["totalAgents"],
            "primaryAgentId": (snapshot.get("info") or {}).get("agentId"),
        }

    @router.delete("/agents/{agent_id}")
    async def delete_agent(agent_id: str) -> dict[str, Any]:
        try:
            await service.remove_agent(agent_id)
        except ServiceError as error:
            raise _raise(error) from error
        return {"removed": agent_id}

    @router.post("/agent/heartbeat")
    async def heartbeat(payload: HeartbeatPayload) -> dict[str, Any]:
        """Legacy REST heartbeat - kept so existing scripts keep working."""
        await service.db.upsert_agent(
            agent_id=payload.agent_id,
            hostname=payload.hostname,
            os_name=payload.os,
            version=payload.version,
            workspace=payload.workspace,
            status=payload.status or "online",
            active_task_id=payload.active_task_id,
            battery_level=payload.battery_level,
            is_simulated=False,
            mark_connected=True,
        )
        await service.publish_agent_status()
        queued = await service.db.queued_tasks()
        return {
            "ack": True,
            "serverTime": utcnow(),
            "queuedCount": len(queued),
            "nextTaskId": queued[0]["id"] if queued else None,
            "heartbeatIntervalSeconds": settings.heartbeat_interval_seconds,
            "note": "Prefer the WebSocket channel /agent/ws for task execution.",
        }

    # ------------------------------------------------------------------
    # tasks
    # ------------------------------------------------------------------
    @router.get("/tasks")
    async def list_tasks(
        status: str | None = Query(default=None),
        search: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        normalized = normalize_status(status) if status else None
        if normalized and normalized not in {
            "queued",
            "running",
            "awaiting_approval",
            "completed",
            "failed",
            "cancelled",
        }:
            raise HTTPException(status_code=400, detail={"error": f"Unknown status: {status}"})
        return await service.list_tasks(
            status=normalized, search=search, limit=limit, offset=offset
        )

    @router.post("/tasks", status_code=201)
    async def create_task(payload: CreateTaskPayload) -> dict[str, Any]:
        try:
            task, message = await service.create_task(payload)
        except ServiceError as error:
            raise _raise(error) from error
        return {"task": task, "message": message}

    @router.get("/tasks/{task_id}")
    async def get_task(task_id: str) -> dict[str, Any]:
        try:
            return {"task": await service.get_task(task_id)}
        except ServiceError as error:
            raise _raise(error) from error

    @router.get("/tasks/{task_id}/checkpoints")
    async def task_checkpoints(task_id: str) -> dict[str, Any]:
        try:
            await service.get_task(task_id)
        except ServiceError as error:
            raise _raise(error) from error
        from .models import checkpoint_to_dict

        rows = await service.db.checkpoints_for_task(task_id)
        return {"checkpoints": [checkpoint_to_dict(row) for row in rows]}

    @router.post("/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str) -> dict[str, Any]:
        try:
            task, message = await service.cancel_task(task_id)
        except ServiceError as error:
            raise _raise(error) from error
        return {"task": task, "message": message}

    @router.post("/tasks/{task_id}/retry")
    async def retry_task(task_id: str) -> dict[str, Any]:
        try:
            task, message = await service.retry_task(task_id)
        except ServiceError as error:
            raise _raise(error) from error
        return {"task": task, "message": message}

    @router.post("/tasks/{task_id}/approval")
    async def decide_approval(
        task_id: str, payload: ApprovalDecisionPayload
    ) -> dict[str, Any]:
        # Per file / per hunk review: if every listed item is rejected, the whole
        # proposal is treated as rejected even when the client sent "approve".
        if payload.items and all(item.decision == "reject" for item in payload.items):
            payload = payload.model_copy(update={"action": "reject"})
        try:
            task, approval, message = await service.resolve_approval(task_id, payload)
        except ServiceError as error:
            raise _raise(error) from error
        return {"task": task, "approval": approval, "message": message}

    # ------------------------------------------------------------------
    # diagnostics
    # ------------------------------------------------------------------
    @router.get("/diagnostics")
    async def diagnostics() -> dict[str, Any]:
        return await service.diagnostics()

    @router.get("/workspaces")
    async def workspaces() -> dict[str, Any]:
        return await service.workspaces()

    @router.get("/health")
    async def health() -> dict[str, Any]:
        snapshot = await service.agent_status_snapshot()
        return {
            "status": "ok",
            "time": utcnow(),
            "agent": {"status": snapshot["status"], "connected": snapshot["connected"]},
            "eventBus": service.bus.backend,
            "sseClients": service.bus.subscriber_count,
        }

    # ------------------------------------------------------------------
    # SSE streams
    # ------------------------------------------------------------------
    @router.get("/tasks/{task_id}/events")
    async def task_events(task_id: str, request: Request) -> Response:
        for attempt in range(20):
            try:
                snapshot = await service.get_task(task_id)
                break
            except ServiceError as error:
                if error.status_code != 404 or attempt == 19:
                    raise _raise(error) from error
                await asyncio.sleep(0.05)
        else:  # pragma: no cover - defensive
            raise HTTPException(status_code=404, detail={"error": "Task not found"})

        resume_from = _last_event_id(request)
        generator = _task_event_stream(service, task_id, snapshot, resume_from)
        return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)

    @router.get("/events")
    async def global_events(request: Request) -> Response:
        resume_from = _last_event_id(request)
        return StreamingResponse(
            _global_event_stream(service, resume_from),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    return router


def _last_event_id(request: Request) -> int:
    raw = request.headers.get("last-event-id") or request.query_params.get("lastEventId") or "0"
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


async def _replay_task(service: TaskService, task_id: str, after_seq: int) -> AsyncIterator[str]:
    for event in await service.db.events_after(after_seq, task_id=task_id):
        snapshot = await service.task_snapshot(task_id)
        payload = {"event": _event_payload(event), "task": snapshot}
        yield f"id: {event['seq']}\nevent: message\ndata: {json.dumps(payload)}\n\n"


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    from .models import event_to_dict

    return event_to_dict(event)


async def _task_event_stream(
    service: TaskService, task_id: str, snapshot: dict[str, Any], after_seq: int
) -> AsyncIterator[str]:
    agent_status = await service.agent_status_snapshot()
    yield f"event: init\ndata: {json.dumps({'task': snapshot, 'agentInfo': agent_status['info']})}\n\n"

    if after_seq:
        async for chunk in _replay_task(service, task_id, after_seq):
            yield chunk

    last_keepalive = time.monotonic()
    async with service.bus.subscribe() as queue:
        while not STOPPING.is_set():
            try:
                # Short slices so a restart is noticed immediately (shutdown must
                # not wait for the full keep-alive interval).
                payload = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if time.monotonic() - last_keepalive >= KEEPALIVE_SECONDS:
                    last_keepalive = time.monotonic()
                    yield ": keep-alive\n\n"
                continue
            if payload.get("type") != "event":
                continue
            event = payload.get("event") or {}
            if event.get("taskId") != task_id:
                continue
            body = {"event": event, "task": payload.get("task")}
            yield f"id: {event.get('seq', 0)}\nevent: message\ndata: {json.dumps(body)}\n\n"


async def _global_event_stream(service: TaskService, after_seq: int) -> AsyncIterator[str]:
    yield (
        "event: connected\ndata: "
        + json.dumps({"status": "connected", "time": utcnow()})
        + "\n\n"
    )

    if after_seq:
        for event in await service.db.events_after(after_seq):
            task = await service.task_snapshot(event["task_id"], include_events=False)
            body = {"event": _event_payload(event), "task": task}
            yield f"id: {event['seq']}\nevent: message\ndata: {json.dumps(body)}\n\n"

    snapshot = await service.agent_status_snapshot()
    yield f"event: agent_status\ndata: {json.dumps({'agentInfo': snapshot['info'], 'status': snapshot['status']})}\n\n"

    last_keepalive = time.monotonic()
    async with service.bus.subscribe() as queue:
        while not STOPPING.is_set():
            try:
                # Short slices so a restart is noticed immediately (shutdown must
                # not wait for the full keep-alive interval).
                payload = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if time.monotonic() - last_keepalive >= KEEPALIVE_SECONDS:
                    last_keepalive = time.monotonic()
                    yield ": keep-alive\n\n"
                continue
            kind = payload.get("type")
            if kind == "agent_status":
                agent = payload.get("agent") or {}
                yield (
                    "event: agent_status\ndata: "
                    + json.dumps(
                        {
                            "agentInfo": agent.get("info"),
                            "status": agent.get("status"),
                            "agents": agent.get("agents") or [],
                            "onlineAgents": agent.get("onlineAgents", 0),
                            "totalAgents": agent.get("totalAgents", 0),
                        }
                    )
                    + "\n\n"
                )
            elif kind == "event":
                event = payload.get("event") or {}
                body = {"event": event, "task": payload.get("task")}
                yield f"id: {event.get('seq', 0)}\nevent: message\ndata: {json.dumps(body)}\n\n"
