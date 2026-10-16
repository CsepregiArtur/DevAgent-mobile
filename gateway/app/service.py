"""Domain logic shared by the PWA API, the agent API and the scheduler.

Guarantees implemented here (mapped to the product requirements):

* unique task id (``task-<10 hex>``) - :func:`TaskService.create_task`
* queue that survives an offline MacBook - tasks stay ``queued`` until an agent
  connects; on reconnect ``resume_tasks_for_agent`` re-arms them
* explicit states ``queued | running | awaiting_approval | completed | failed |
  cancelled`` (``waiting_approval`` is accepted as a synonym)
* every transition produces an event that is persisted *and* broadcast in real time
* nothing is applied before approval: approvals carry a content hash and are
  checked with optimistic locking (hash mismatch -> 409)
* interrupted work resumes from the last agent-reported checkpoint instead of
  starting over
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Iterable

from .config import Settings
from .db import Database, parse_ts, utcnow
from .events import EventBus
from .hub import AgentHub
from .models import (
    TERMINAL_STATUSES,
    ApprovalDecisionPayload,
    CreateTaskPayload,
    agent_to_dict,
    approval_to_dict,
    checkpoint_to_dict,
    event_to_dict,
    normalize_status,
    task_to_dict,
)

logger = logging.getLogger("devagent.service")

DEFAULT_GATES: dict[str, list[str]] = {
    "supervised": ["diff"],
    "dry-run": ["diff"],
    "autonomous": [],
}
GATE_ORDER = ["plan", "diff", "apply", "commit"]


class ServiceError(Exception):
    """Domain level error carrying an HTTP status code."""

    def __init__(self, message: str, status_code: int = 400, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.extra = extra


def hash_diff(diff: str) -> str:
    return "sha256:" + hashlib.sha256(diff.encode("utf-8")).hexdigest()


class TaskService:
    def __init__(self, db: Database, bus: EventBus, hub: AgentHub, settings: Settings) -> None:
        self.db = db
        self.bus = bus
        self.hub = hub
        self.settings = settings
        from datetime import datetime, timezone

        self.version = "1.0.0"
        self._started_at = datetime.now(timezone.utc)
        self._started_at_iso = self._started_at.isoformat(timespec="seconds").replace("+00:00", "Z")

    # ------------------------------------------------------------------
    # serialisation helpers
    # ------------------------------------------------------------------
    async def agent_status_snapshot(self) -> dict[str, Any]:
        agents = await self.db.list_agents()
        ordered = self._order_agents(agents)
        primary = ordered[0] if ordered else None
        live = [a for a in ordered if self._is_live(a)]

        status = (primary or {}).get("status") or "offline"
        if primary is not None and status != "offline" and not self._is_live(primary):
            # Row says online but no socket is attached -> reconnecting window.
            status = "reconnecting"
        simulated = await self.simulator_enabled()

        def row(agent: dict[str, Any]) -> dict[str, Any]:
            payload = agent_to_dict(agent) or {}
            payload["online"] = self._is_live(agent)
            payload["secondsSinceHeartbeat"] = self._heartbeat_age_seconds(agent)
            return payload

        return {
            "connected": any(self._is_live(a) for a in ordered),
            "status": status if live else ("online" if any(self._is_live(a) for a in ordered) else "offline"),
            "info": agent_to_dict(primary),
            "secondsSinceHeartbeat": self._heartbeat_age_seconds(primary) if primary else None,
            "isSimulated": simulated,
            "gatewayOnline": True,
            "agents": [row(agent) for agent in ordered],
            "onlineAgents": len(live),
            "totalAgents": len(ordered),
        }

    # -- fleet helpers ---------------------------------------------------
    def _is_live(self, agent: dict[str, Any]) -> bool:
        return self.hub.is_online(agent["agent_id"])

    def _heartbeat_age_seconds(self, agent: dict[str, Any] | None) -> int | None:
        from datetime import datetime, timezone

        if not agent:
            return None
        parsed = parse_ts(agent.get("last_heartbeat"))
        if parsed is None:
            return None
        return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))

    def _order_agents(self, agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Live sockets first, then most recently seen (a fleet, not one laptop)."""
        live = [a for a in agents if self._is_live(a)]
        idle = [a for a in agents if not self._is_live(a)]
        key = lambda agent: (agent.get("last_heartbeat") or "")  # noqa: E731
        live.sort(key=key, reverse=True)
        idle.sort(key=key, reverse=True)
        return live + idle

    @staticmethod
    def _agent_can_run(agent: dict[str, Any], task: dict[str, Any]) -> bool:
        target = task.get("target_agent_id")
        if target and target != agent["agent_id"]:
            return False
        required = {c for c in (task.get("required_capabilities_json") or []) if c}
        if not required:
            return True
        available = {c for c in (agent.get("capabilities_json") or []) if c}
        return required.issubset(available)

    async def publish_agent_status(self) -> None:
        await self.bus.publish({"type": "agent_status", "agent": await self.agent_status_snapshot()})

    async def task_snapshot(self, task_id: str, *, include_events: bool = True) -> dict[str, Any] | None:
        task = await self.db.get_task(task_id)
        if task is None:
            return None
        events = await self.db.events_for_task(task_id, self.settings.max_events_per_task) if include_events else []
        approval = await self.db.latest_approval(task_id)
        approvals = await self.db.approvals_for_task(task_id)
        checkpoints = await self.db.checkpoints_for_task(task_id)
        agent_online = bool(task.get("agent_id") and self.hub.is_online(task["agent_id"]))
        return task_to_dict(
            task,
            events=[event_to_dict(e) for e in events],
            approval=approval_to_dict(approval) if approval else None,
            approvals=[approval_to_dict(a) for a in approvals],
            checkpoints=[checkpoint_to_dict(c) for c in checkpoints[-5:]],
            agent_online=agent_online,
        )

    async def _emit(
        self,
        task_id: str,
        *,
        type: str,
        message: str,
        details: dict[str, Any] | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        include_task: bool = True,
    ) -> dict[str, Any]:
        event = await self.db.add_event(
            task_id,
            type=type,
            message=message,
            details=details,
            step_number=step_number,
            total_steps=total_steps,
        )
        payload = {
            "type": "event",
            "event": event_to_dict(event),
        }
        if include_task:
            payload["task"] = await self.task_snapshot(task_id)
        await self.bus.publish(payload)
        return event

    # ------------------------------------------------------------------
    # queue / lifecycle
    # ------------------------------------------------------------------
    async def create_task(self, payload: CreateTaskPayload) -> tuple[dict[str, Any], str]:
        if payload.idempotency_key:
            existing = await self.db.get_task_by_idempotency(payload.idempotency_key)
            if existing is not None:
                snapshot = await self.task_snapshot(existing["id"])
                return snapshot or existing, "Task already exists (idempotent request)"

        gates = list(payload.gates) if payload.gates else list(DEFAULT_GATES.get(payload.mode, ["diff"]))
        gates = self._normalize_gates(gates)

        target_agent = None
        if payload.agent_id:
            target_agent = await self.db.get_agent(payload.agent_id)
            if target_agent is None:
                raise ServiceError(f"Unknown agent: {payload.agent_id}", 404)

        ordered = self._order_agents(await self.db.list_agents())
        primary = ordered[0] if ordered else None
        workspace_agent = target_agent or primary
        workspace = (
            payload.workspace
            or (workspace_agent or {}).get("workspace")
            or self.settings.default_workspace
        )
        model = payload.model or self.settings.default_model

        task = await self.db.create_task(
            instruction=payload.instruction,
            workspace=workspace,
            mode=payload.mode,
            model=model,
            gates=gates,
            idempotency_key=payload.idempotency_key,
            target_agent_id=target_agent["agent_id"] if target_agent else None,
            required_capabilities=payload.required_capabilities,
        )
        online = (
            self._is_live(target_agent)
            if target_agent is not None
            else any(self._is_live(agent) for agent in ordered)
        )
        if online:
            message = "Task submitted and placed in the agent execution queue"
        elif target_agent is not None:
            message = (
                f"Task queued for agent '{target_agent['agent_id']}'. Execution starts when it "
                "reconnects."
            )
        else:
            message = "Task queued. Execution starts automatically once an agent reconnects."
        await self._emit(
            task["id"],
            type="status_change",
            message=message,
            details={
                "status": "queued",
                "gates": gates,
                "agentOnline": online,
                "targetAgentId": (target_agent or {}).get("agent_id"),
                "requiredCapabilities": payload.required_capabilities or [],
            },
        )
        snapshot = await self.task_snapshot(task["id"])
        assert snapshot is not None
        if online:
            # Trigger an immediate dispatch attempt so an idle agent picks the task
            # up in ~milliseconds instead of waiting for the next scheduler tick.
            await self.dispatch_pending()
        return snapshot, ("Task accepted and queued" if online else message)

    @staticmethod
    def _normalize_gates(gates: Iterable[str]) -> list[str]:
        normalized = [g.strip().lower() for g in gates if g and g.strip()]
        deduped = list(dict.fromkeys(normalized))
        unknown = [g for g in deduped if g not in GATE_ORDER]
        if unknown:
            raise ServiceError(f"Unknown approval gate(s): {', '.join(unknown)}", 400)
        return sorted(deduped, key=GATE_ORDER.index)

    async def list_tasks(
        self, *, status: str | None, search: str | None, limit: int, offset: int
    ) -> dict[str, Any]:
        normalized = normalize_status(status) if status else None
        tasks, total = await self.db.list_tasks(
            status=normalized, search=search, limit=limit, offset=offset
        )
        snapshots = []
        for task in tasks:
            snapshot = await self.task_snapshot(task["id"])
            if snapshot is not None:
                snapshots.append(snapshot)
        return {"tasks": snapshots, "total": total, "offset": offset, "limit": limit}

    async def get_task(self, task_id: str) -> dict[str, Any]:
        snapshot = await self.task_snapshot(task_id)
        if snapshot is None:
            raise ServiceError("Task not found", 404)
        return snapshot

    async def cancel_task(self, task_id: str, reason: str = "Cancelled by operator") -> tuple[dict[str, Any], str]:
        task = await self.db.get_task(task_id)
        if task is None:
            raise ServiceError("Task not found", 404)
        if task["status"] in TERMINAL_STATUSES:
            raise ServiceError(f"Cannot cancel a task that is already {task['status']}.", 400)

        await self.db.update_task(task_id, status="cancelled", completed_at=utcnow(), current_step=reason)
        await self._emit(
            task_id,
            type="status_change",
            message=f"{reason}.",
            details={"status": "cancelled", "was": task["status"]},
        )
        await self._detach_agent_task(task_id, notify=f"cancel:{task_id}")
        return await self.get_task(task_id), "Task cancelled successfully."

    async def retry_task(self, task_id: str) -> tuple[dict[str, Any], str]:
        task = await self.db.get_task(task_id)
        if task is None:
            raise ServiceError("Task not found", 404)
        if task["status"] in {"queued", "running"}:
            raise ServiceError(f"Task is already {task['status']}.", 400)

        await self.db.update_task(
            task_id,
            status="queued",
            current_step="Queued for retry",
            started_at=None,
            completed_at=None,
            interrupted_at=None,
            resumed_at=None,
            result_json=None,
            error=None,
            agent_id=None,
            attempt=(task["attempt"] or 0),
        )
        # A retry is an explicit fresh start: old checkpoints would make the agent
        # resume a worktree that no longer matches the operator's intent.
        await self.db.delete_checkpoints(task_id)
        await self._emit(
            task_id,
            type="status_change",
            message="Task re-queued for execution retry (starts from a fresh checkpoint).",
            details={"status": "queued", "attempt": task["attempt"]},
        )
        status = await self.agent_status_snapshot()
        if status["status"] in {"online", "busy", "reconnecting"}:
            await self.dispatch_pending()
        return await self.get_task(task_id), "Task re-queued for execution."

    # ------------------------------------------------------------------
    # agent driven transitions
    # ------------------------------------------------------------------
    async def mark_agent_offline(self, agent_id: str) -> None:
        """Called by the watchdog: the laptop stopped heartbeating.

        Running tasks are pushed back to the queue (they have never been applied
        without approval) and flagged ``interrupted_at`` so the next connection
        resumes them from their last checkpoint. Pending approvals stay valid.
        """
        await self.db.set_agent_status(agent_id, "offline", clear_active_task=True)
        await self._interrupt_tasks_of(agent_id, utcnow())
        await self.publish_agent_status()

    async def _interrupt_tasks_of(self, agent_id: str, now: str) -> None:
        for status in ("running", "awaiting_approval"):
            tasks, _ = await self.db.list_tasks(status=status, limit=200)
            for task in tasks:
                if task.get("agent_id") != agent_id:
                    continue
                checkpoint = await self.db.latest_checkpoint(task["id"])
                if status == "running":
                    await self.db.update_task(
                        task["id"], status="queued", interrupted_at=now
                    )
                    await self._emit(
                        task["id"],
                        type="status_change",
                        message=(
                            "MacBook agent went offline mid-task. The task is back in the queue and "
                            "will resume from the last checkpoint after reconnection."
                        ),
                        details={
                            "status": "queued",
                            "interruptedAt": now,
                            "checkpoint": checkpoint_to_dict(checkpoint) if checkpoint else None,
                        },
                    )
                else:
                    await self.db.update_task(task["id"], interrupted_at=now)
                    await self._emit(
                        task["id"],
                        type="log",
                        message=(
                            "Agent offline while an approval was pending. The proposal stays valid "
                            "and will be applied on the next connection."
                        ),
                        details={"interruptedAt": now},
                    )

    async def _detach_agent_task(self, task_id: str, *, notify: str | None = None) -> None:
        task = await self.db.get_task(task_id)
        if task is None:
            return
        agent_id = task.get("agent_id")
        if not agent_id:
            return
        agent = await self.db.get_agent(agent_id)
        if agent and agent.get("active_task_id") == task_id:
            await self.db.set_agent_status(agent_id, "online", clear_active_task=True)
        if notify:
            target, _, payload = notify.partition(":")
            if target == "cancel":
                await self.hub.send_to_agent(
                    agent_id,
                    {"type": "cancel", "taskId": payload, "reason": "Cancelled by operator"},
                )
        await self.publish_agent_status()

    async def dispatch_task(self, task_id: str, agent_id: str, *, force: bool = False) -> bool:
        """Hand a queued task to an agent (fresh start or resume from checkpoint).

        ``force`` skips the target/capability filter and is only used when the task
        is already bound to this agent (resuming after an interruption).
        """
        task = await self.db.get_task(task_id)
        if task is None or task["status"] != "queued":
            return False
        if not self.hub.is_online(agent_id):
            return False
        agent = await self.db.get_agent(agent_id)
        if agent is None:
            return False
        if not force and not self._agent_can_run(agent, task):
            return False

        checkpoint = await self.db.latest_checkpoint(task_id)
        pending = await self.db.pending_approval(task_id)
        latest = pending or await self.db.latest_approval(task_id)
        resuming = checkpoint is not None or task.get("interrupted_at") is not None
        attempt = (task.get("attempt") or 0) + 1

        if pending is not None:
            action = "await_approval"
        elif latest is not None and latest["state"] == "approved":
            action = "apply_approved"
        elif resuming:
            action = "resume"
        else:
            action = "start"

        await self.db.update_task(
            task_id,
            status="running",
            agent_id=agent_id,
            attempt=attempt,
            started_at=task.get("started_at") or utcnow(),
            resumed_at=utcnow() if resuming else None,
            current_step="Resuming from last checkpoint" if resuming else "Task assigned to agent",
        )
        await self.db.set_agent_status(agent_id, "busy", active_task_id=task_id)

        snapshot = await self.task_snapshot(task_id)
        assert snapshot is not None
        delivered = await self.hub.send_to_agent(
            agent_id,
            {
                "type": "task_assign" if action == "start" else "task_resume",
                "task": snapshot,
                "checkpoint": checkpoint_to_dict(checkpoint) if checkpoint else None,
                "attempt": attempt,
                "action": action,
                "approval": approval_to_dict(latest) if latest else None,
                "expectedGate": await self.expected_gate(task_id),
                "gates": task.get("gates_json") or ["diff"],
                "serverTime": utcnow(),
            },
        )
        if not delivered:
            # Socket vanished between the check and the send: put it back.
            await self.db.update_task(task_id, status="queued", agent_id=None)
            await self.db.set_agent_status(agent_id, "offline", clear_active_task=True)
            return False

        await self._emit(
            task_id,
            type="status_change",
            message=(
                f"Resumed on {agent.get('hostname') or agent_id} from checkpoint '{checkpoint['name']}'"
                if resuming and checkpoint
                else f"Task acquired by agent {agent.get('hostname') or agent_id}"
            ),
            details={
                "status": "running",
                "attempt": attempt,
                "resuming": resuming,
                "action": action,
                "checkpointId": checkpoint["id"] if checkpoint else None,
            },
        )
        await self.publish_agent_status()
        return True

    async def resume_tasks_for_agent(self, agent_id: str) -> list[str]:
        """Re-arm tasks that were interrupted while the agent was away."""
        resumed: list[str] = []
        for task in await self.db.tasks_pending_agent(agent_id):
            if task["status"] in {"running", "awaiting_approval"}:
                await self.db.update_task(task["id"], status="queued", agent_id=agent_id)
            if await self.dispatch_task(task["id"], agent_id, force=True):
                resumed.append(task["id"])
        return resumed

    async def handle_agent_event(self, agent_id: str, message: dict[str, Any]) -> None:
        task_id = message.get("taskId")
        if not task_id:
            return
        task = await self.db.get_task(task_id)
        if task is None:
            return
        self.hub.mark_seen(agent_id)
        await self.db.upsert_agent(
            agent_id=agent_id,
            hostname=message.get("hostname") or agent_id,
            status="busy",
            active_task_id=task_id,
        )

        event_type = message.get("eventType") or message.get("event_type") or "log"
        text = message.get("message") or ""
        details = message.get("details")
        step_number = message.get("stepNumber")
        total_steps = message.get("totalSteps")

        updates: dict[str, Any] = {}
        if message.get("currentStep"):
            updates["current_step"] = message["currentStep"]
        if step_number is not None:
            updates["step_index"] = step_number
        if total_steps is not None:
            updates["total_steps"] = total_steps
        if updates:
            await self.db.update_task(task_id, **updates)

        await self._emit(
            task_id,
            type=event_type,
            message=text,
            details=details,
            step_number=step_number,
            total_steps=total_steps,
        )

    async def save_checkpoint(
        self, agent_id: str, message: dict[str, Any]
    ) -> dict[str, Any] | None:
        task_id = message.get("taskId")
        if not task_id:
            return None
        checkpoint = await self.db.save_checkpoint(
            task_id=task_id,
            name=message.get("name") or "step",
            payload=message.get("payload") or {},
            agent_id=agent_id,
            idempotency_key=message.get("idempotencyKey"),
        )
        await self._emit(
            task_id,
            type="log",
            message=f"Checkpoint '{checkpoint['name']}' recorded (safe resume point).",
            details={"checkpointId": checkpoint["id"], "name": checkpoint["name"]},
        )
        return checkpoint

    async def submit_approval_request(
        self, agent_id: str, message: dict[str, Any]
    ) -> dict[str, Any]:
        task_id = message.get("taskId")
        if not task_id:
            raise ServiceError("taskId is required", 400)
        task = await self.db.get_task(task_id)
        if task is None:
            raise ServiceError("Task not found", 404)

        gate = (message.get("gate") or "diff").lower()
        gates = task.get("gates_json") or ["diff"]
        if gate not in gates:
            raise ServiceError(f"Gate '{gate}' is not enabled for task {task_id}", 409)
        existing = await self.db.pending_approval(task_id)
        if existing is not None:
            # Re-submission after a reconnect: never create a second live proposal.
            return approval_to_dict(existing)
        expected = await self.expected_gate(task_id)
        if expected and gate != expected:
            raise ServiceError(
                f"Gate '{gate}' cannot be reviewed yet; expected '{expected}'", 409
            )
        if task["status"] in TERMINAL_STATUSES:
            raise ServiceError(f"Task is already {task['status']}", 409)

        diff = message.get("diff") or ""
        content_hash = message.get("hash") or hash_diff(diff)
        files = message.get("files") or message.get("changes") or []
        items = message.get("items") or []
        if not items:
            items = self._derive_items(files, diff)

        approval = await self.db.create_approval(
            task_id=task_id,
            gate=gate,
            hash=content_hash,
            summary=message.get("summary") or f"Proposed changes for: {task['instruction']}",
            diff=diff,
            changes=files,
            items=items,
            timeout_seconds=self.settings.approval_timeout_seconds,
        )
        await self.db.update_task(
            task_id,
            status="awaiting_approval",
            agent_id=agent_id,
            current_step=f"Awaiting operator approval ({gate})",
        )
        await self.db.set_agent_status(agent_id, "online", active_task_id=task_id)
        await self._emit(
            task_id,
            type="approval_required",
            message=f"Review required: inspect the {gate} proposal and approve or reject it.",
            details={
                "approvalId": approval["id"],
                "gate": gate,
                "hash": approval["hash"],
                "expiresAt": approval.get("expires_at"),
                "files": [f.get("path") for f in files],
                "items": [item.get("id") for item in items],
            },
        )
        await self.publish_agent_status()
        return approval_to_dict(approval)

    @staticmethod
    def _derive_items(files: list[dict[str, Any]], diff: str) -> list[dict[str, Any]]:
        """Build per-file / per-hunk review items from a unified diff."""
        items: list[dict[str, Any]] = []
        current_file: dict[str, Any] | None = None
        hunk_index = 0
        for line in diff.splitlines():
            if line.startswith("+++ "):
                path = line[4:].strip()
                if path.startswith("b/"):
                    path = path[2:]
                current_file = {"id": path, "path": path, "hunks": []}
                items.append(current_file)
                hunk_index = 0
            elif line.startswith("@@"):
                hunk_index += 1
                if current_file is not None:
                    header = line.strip()
                    current_file["hunks"].append(
                        {"id": f"{current_file['path']}#{hunk_index}", "index": hunk_index, "header": header}
                    )
        if items:
            return items
        return [
            {
                "id": f.get("path"),
                "path": f.get("path"),
                "hunks": [],
                "previousPath": f.get("previousPath"),
            }
            for f in files
        ]

    async def expected_gate(self, task_id: str) -> str | None:
        task = await self.db.get_task(task_id)
        if task is None:
            return None
        gates = task.get("gates_json") or []
        approvals = await self.db.approvals_for_task(task_id)
        approved = {a["gate"] for a in approvals if a["state"] == "approved"}
        for gate in gates:
            if gate not in approved:
                return gate
        return None

    async def resolve_approval(
        self, task_id: str, payload: ApprovalDecisionPayload
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        task = await self.db.get_task(task_id)
        if task is None:
            raise ServiceError("Task not found", 404)
        approval = await self.db.pending_approval(task_id)
        if approval is None or task["status"] != "awaiting_approval":
            raise ServiceError(
                "Task is not awaiting approval.", 400, currentState=task["status"]
            )
        if approval["hash"] != payload.hash:
            raise ServiceError(
                "Approval proposal hash mismatch. The proposed diff may have changed or become stale.",
                409,
                expectedHash=approval["hash"],
                providedHash=payload.hash,
            )

        decisions: dict[str, Any] = {}
        if payload.items:
            for item in payload.items:
                decisions[item.path] = {
                    "decision": item.decision,
                    "hunks": item.hunks or [],
                }

        state = "approved" if payload.action == "approve" else "rejected"
        approval = await self.db.resolve_approval(
            approval["id"],
            state=state,
            decision=payload.action,
            comment=payload.comment,
            decisions=decisions,
        )
        assert approval is not None

        agent_id = task.get("agent_id")
        if payload.action == "approve":
            next_gate = await self.expected_gate(task_id)
            await self.db.update_task(
                task_id,
                status="running",
                current_step=(
                    f"Approval granted. Applying changes ({next_gate} gate pending)."
                    if next_gate
                    else "Approval granted. Applying changes and running verification."
                ),
            )
            await self._emit(
                task_id,
                type="approval_resolved",
                message=f"Approval granted for the {approval['gate']} proposal"
                + (f": {payload.comment}" if payload.comment else "."),
                details={"approvalId": approval["id"], "gate": approval["gate"], "decision": "approve"},
            )
            if agent_id:
                await self.hub.send_to_agent(
                    agent_id,
                    {
                        "type": "approval_decision",
                        "taskId": task_id,
                        "approvalId": approval["id"],
                        "gate": approval["gate"],
                        "decision": "approve",
                        "comment": payload.comment,
                        "items": approval.get("items_json") or [],
                        "decisions": decisions,
                        "nextGate": next_gate,
                    },
                )
                await self.db.set_agent_status(agent_id, "busy", active_task_id=task_id)
            message = "Changes approved and resumed."
        else:
            await self.db.update_task(
                task_id,
                status="failed",
                completed_at=utcnow(),
                current_step="Execution aborted: operator rejected the proposed changes.",
                error=f"Proposed {approval['gate']} changes rejected"
                + (f': "{payload.comment}"' if payload.comment else "."),
            )
            await self._emit(
                task_id,
                type="approval_resolved",
                message=f"Proposed changes rejected by operator ({approval['gate']} gate)"
                + (f": {payload.comment}" if payload.comment else "."),
                details={"approvalId": approval["id"], "gate": approval["gate"], "decision": "reject"},
            )
            if agent_id:
                await self.hub.send_to_agent(
                    agent_id,
                    {
                        "type": "approval_decision",
                        "taskId": task_id,
                        "approvalId": approval["id"],
                        "gate": approval["gate"],
                        "decision": "reject",
                        "comment": payload.comment,
                        "items": approval.get("items_json") or [],
                        "decisions": decisions,
                    },
                )
                await self._detach_agent_task(task_id)
            message = "Changes rejected and task aborted."

        await self.publish_agent_status()
        snapshot = await self.get_task(task_id)
        return snapshot, approval_to_dict(approval), message

    async def expire_approval(self, approval: dict[str, Any]) -> None:
        await self.db.resolve_approval(
            approval["id"], state="expired", decision=None, comment="Approval window expired"
        )
        task = await self.db.get_task(approval["task_id"])
        if task is None:
            return
        if task["status"] not in TERMINAL_STATUSES:
            await self.db.update_task(
                approval["task_id"],
                status="failed",
                completed_at=utcnow(),
                current_step="Approval window expired before a decision was taken.",
                error=f"Approval for the {approval['gate']} gate expired at {approval.get('expires_at')}.",
            )
            await self._emit(
                approval["task_id"],
                type="approval_resolved",
                message="Approval window expired; the proposal was withdrawn and the task failed.",
                details={"approvalId": approval["id"], "gate": approval["gate"], "state": "expired"},
            )
        agent_id = task.get("agent_id")
        if agent_id:
            await self.hub.send_to_agent(
                agent_id,
                {
                    "type": "approval_decision",
                    "taskId": approval["task_id"],
                    "approvalId": approval["id"],
                    "gate": approval["gate"],
                    "decision": "reject",
                    "comment": "Approval window expired",
                },
            )
            await self._detach_agent_task(approval["task_id"])

    async def report_result(self, agent_id: str, message: dict[str, Any]) -> None:
        task_id = message.get("taskId")
        if not task_id:
            return
        task = await self.db.get_task(task_id)
        if task is None:
            return
        status = normalize_status(message.get("status") or "completed")
        if status not in {"completed", "failed"}:
            raise ServiceError(f"Invalid result status: {status}", 400)

        result = message.get("result") or None
        error = message.get("error") or None
        if status == "completed":
            await self.db.update_task(
                task_id,
                status="completed",
                completed_at=utcnow(),
                current_step="Task completed successfully. Working tree clean.",
                result_json=result,
                error=None,
            )
            await self._emit(
                task_id,
                type="status_change",
                message="Task completed successfully.",
                details={"status": "completed", "result": result},
            )
        else:
            await self.db.update_task(
                task_id,
                status="failed",
                completed_at=utcnow(),
                current_step="Task failed.",
                error=error or "Agent reported a failure",
                result_json=result,
            )
            await self._emit(
                task_id,
                type="error",
                message=f"Task failed: {error or 'agent reported a failure'}",
                details={"status": "failed"},
            )
        await self._detach_agent_task(task_id)
        await self.publish_agent_status()

    # ------------------------------------------------------------------
    # scheduler helpers
    # ------------------------------------------------------------------
    async def idle_online_agents(self) -> list[dict[str, Any]]:
        idle: list[dict[str, Any]] = []
        for agent in await self.db.list_agents():
            if not self.hub.is_online(agent["agent_id"]):
                continue
            if agent.get("active_task_id"):
                continue
            idle.append(agent)
        return idle

    async def dispatch_pending(self) -> int:
        """FIFO dispatch of queued tasks to idle, connected agents.

        Each task is matched against every idle agent: an explicit target agent
        wins, tasks with ``requiredCapabilities`` only go to agents that advertise
        them, and the oldest task is preferred.
        """
        dispatched = 0
        candidates = await self.db.queued_candidates()
        if not candidates:
            return 0
        for agent in await self.idle_online_agents():
            for task in list(candidates):
                if not self._agent_can_run(agent, task):
                    continue
                if await self.dispatch_task(task["id"], agent["agent_id"]):
                    dispatched += 1
                    candidates.remove(task)
                break
            if not candidates:
                break
        return dispatched

    async def remove_agent(self, agent_id: str) -> None:
        """Forget a decommissioned agent (its tasks stay in the history)."""
        agent = await self.db.get_agent(agent_id)
        if agent is None:
            raise ServiceError("Agent not found", 404)
        if self.hub.is_online(agent_id):
            raise ServiceError("Agent is connected; disconnect it first", 409)
        await self.db.delete_agent(agent_id)
        await self.publish_agent_status()

    async def reconcile_on_startup(self) -> None:
        """A gateway restart kills every socket: nothing may stay 'online'.

        Tasks that were ``running`` are put back in the queue with
        ``interrupted_at`` so the next agent connection resumes them from their
        last checkpoint; pending approvals stay valid.
        """
        await self.db.mark_all_agents_offline()
        now = utcnow()
        for status in ("running", "awaiting_approval"):
            tasks, _ = await self.db.list_tasks(status=status, limit=500)
            for task in tasks:
                if status == "running":
                    await self.db.update_task(task["id"], status="queued", interrupted_at=now)
                    await self._emit(
                        task["id"],
                        type="status_change",
                        message=(
                            "Gateway restarted while this task was running. It is back in the "
                            "queue and resumes from its last checkpoint."
                        ),
                        details={"status": "queued", "interruptedAt": now, "reason": "gateway-restart"},
                    )
                else:
                    await self.db.update_task(task["id"], interrupted_at=now)
        await self.publish_agent_status()

    async def simulator_enabled(self) -> bool:
        return await self.db.get_flag("simulator_active", self.settings.simulator_enabled)

    async def set_simulator(self, enabled: bool | None) -> bool:
        current = await self.simulator_enabled()
        new_value = (not current) if enabled is None else bool(enabled)
        await self.db.set_flag("simulator_active", new_value)
        if new_value:
            await self.db.upsert_agent(
                agent_id="simulated-agent",
                hostname="gateway-simulator",
                os_name="simulated",
                version="v1.0.0",
                workspace=self.settings.default_workspace,
                status="online",
                is_simulated=True,
                mark_connected=True,
            )
            await self.publish_agent_status()
        return new_value

    # ------------------------------------------------------------------
    # diagnostics
    # ------------------------------------------------------------------
    async def workspaces(self) -> dict[str, Any]:
        ordered = self._order_agents(await self.db.list_agents())
        discovered: list[str] = []
        for agent in ordered:
            if agent.get("workspace"):
                discovered.append(agent["workspace"])
        discovered.extend(list(self.settings.workspaces))
        seen: list[str] = []
        for workspace in discovered:
            if workspace not in seen:
                seen.append(workspace)
        primary = ordered[0] if ordered else None
        return {
            "workspaces": seen,
            "defaultWorkspace": (primary or {}).get("workspace")
            or self.settings.default_workspace,
            "agents": [
                {
                    "agentId": agent["agent_id"],
                    "hostname": agent.get("hostname"),
                    "workspace": agent.get("workspace"),
                    "online": self._is_live(agent),
                    "status": agent.get("status"),
                }
                for agent in ordered
            ],
        }

    async def diagnostics(self) -> dict[str, Any]:
        from datetime import datetime, timezone

        agent = await self.db.primary_agent()
        status = await self.agent_status_snapshot()
        counts = await self.db.status_counts()
        base = None
        if self.settings.static_dir is not None:
            base = self.settings.static_dir / "index.html"
        uptime = int((datetime.now(timezone.utc) - self._started_at).total_seconds())
        return {
            "gateway": {
                "status": "online",
                "uptimeSeconds": uptime,
                "timestamp": utcnow(),
                "version": f"devagent-gateway v{self.version}",
                "environment": "production",
                "rootPath": self.settings.root_path,
                "eventBus": self.bus.backend,
                "sseClients": self.bus.subscriber_count,
                "agentSockets": self.hub.connection_count,
                "pwaBuild": bool(base and base.exists()),
                "startedAt": self._started_at_iso,
            },
            "agent": {
                "connected": status["connected"],
                "status": status["status"],
                "info": agent_to_dict(agent),
                "secondsSinceHeartbeat": status["secondsSinceHeartbeat"],
                "isSimulated": status["isSimulated"],
                "sockets": self.hub.online_agents(),
            },
            "aiProvider": {
                "provider": "DeepSeek Proxy (private, server-side key)",
                "endpointConfigured": bool(self.settings.deepseek_base_url),
                "endpoint": self.settings.deepseek_base_url,
                "defaultModel": self.settings.default_model,
                "availableModels": [
                    self.settings.default_model,
                    "DeepSeek-V3",
                    "Claude-3.5-Sonnet (proxy fallback)",
                ],
                "status": "ready",
            },
            "queue": counts,
            "workspaces": (await self.workspaces())["workspaces"],
            "protocol": {
                "agentTransport": "websocket (agent initiated)",
                "resume": "checkpoint based",
                "approvalGates": GATE_ORDER,
                "approvalTimeoutSeconds": self.settings.approval_timeout_seconds,
                "agentOfflineSeconds": self.settings.agent_offline_seconds,
            },
        }

    def bind_runtime(self, *, version: str) -> None:
        self.version = version
