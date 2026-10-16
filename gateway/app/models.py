"""Pydantic models for inbound payloads + camelCase serialisers for the PWA.

The wire format is owned by the PWA (`mobile/src/types/agent.ts`), therefore all
outgoing JSON uses camelCase keys. Inbound payloads are validated here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TaskMode = Literal["autonomous", "supervised", "dry-run"]
TaskStatus = Literal[
    "queued",
    "running",
    "awaiting_approval",
    "waiting_approval",
    "completed",
    "failed",
    "cancelled",
]
GateName = Literal["plan", "diff", "commit", "apply"]
DecisionAction = Literal["approve", "reject"]
ApprovalState = Literal["pending", "approved", "rejected", "expired"]

STATUS_ALIASES: dict[str, str] = {
    "waiting_approval": "awaiting_approval",
    "awaiting-approval": "awaiting_approval",
    "waitingapproval": "awaiting_approval",
    "canceled": "cancelled",
}

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def normalize_status(value: str | None) -> str | None:
    """Map user supplied status synonyms onto the canonical set."""
    if value is None:
        return None
    key = value.strip().lower()
    return STATUS_ALIASES.get(key, key)


class CreateTaskPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    instruction: str = Field(min_length=1, max_length=8000)
    workspace: str | None = None
    mode: TaskMode = "supervised"
    model: str | None = None
    gates: list[GateName] | None = None
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey", max_length=128)
    # Multi-agent fleet: pin the task to one agent and/or require capabilities
    # (e.g. ["macos", "pull-request"]) advertised by the agent in its hello.
    agent_id: str | None = Field(default=None, alias="agentId", max_length=128)
    required_capabilities: list[str] | None = Field(
        default=None, alias="requiredCapabilities"
    )

    @field_validator("instruction")
    @classmethod
    def _strip_instruction(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Instruction text is required.")
        return value


class ItemDecision(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    path: str = Field(min_length=1)
    decision: DecisionAction
    hunks: list[int] | None = None


class ApprovalDecisionPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    action: DecisionAction
    hash: str = Field(min_length=1)
    comment: str | None = None
    # Per file / per hunk granularity: optional explicit decisions. When present,
    # only the approved paths/hunks are applied by the agent.
    items: list[ItemDecision] | None = None


class SimulatorTogglePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    enabled: bool | None = None


class HeartbeatPayload(BaseModel):
    """Legacy REST heartbeat (kept for compatibility with the PWA mock server)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    agent_id: str = Field(default="macbook-agent", alias="agentId")
    hostname: str = "local-macbook"
    workspace: str = "~/dev"
    status: str = "online"
    active_task_id: str | None = Field(default=None, alias="activeTaskId")
    os: str = "macOS"
    version: str = "v1.0.0"
    battery_level: int | None = Field(default=None, alias="batteryLevel")


def task_to_dict(
    row: dict[str, Any],
    *,
    events: list[dict[str, Any]] | None = None,
    approval: dict[str, Any] | None = None,
    approvals: list[dict[str, Any]] | None = None,
    checkpoints: list[dict[str, Any]] | None = None,
    agent_online: bool = True,
) -> dict[str, Any]:
    """Serialise a task row for the PWA (camelCase, TS compatible)."""
    payload: dict[str, Any] = {
        "id": row["id"],
        "instruction": row["instruction"],
        "workspace": row["workspace"],
        "mode": row["mode"],
        "model": row["model"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "events": events if events is not None else [],
    }
    optional = {
        "currentStep": row.get("current_step"),
        "startedAt": row.get("started_at"),
        "completedAt": row.get("completed_at"),
        "error": row.get("error"),
    }
    for key, value in optional.items():
        if value is not None:
            payload[key] = value

    if row.get("step_index") is not None:
        payload["stepIndex"] = row["step_index"]
    if row.get("total_steps") is not None:
        payload["totalSteps"] = row["total_steps"]
    if row.get("result_json"):
        payload["result"] = row["result_json"]
    if approval is not None:
        payload["approval"] = approval
    if approvals:
        payload["approvals"] = approvals
    if checkpoints:
        payload["checkpoints"] = checkpoints

    payload["agentId"] = row.get("agent_id")
    payload["attempt"] = row.get("attempt") or 0
    payload["agentOnline"] = agent_online
    if row.get("interrupted_at"):
        payload["interruptedAt"] = row["interrupted_at"]
    if row.get("resumed_at"):
        payload["resumedAt"] = row["resumed_at"]
    if row.get("idempotency_key"):
        payload["idempotencyKey"] = row["idempotency_key"]
    payload["gates"] = row.get("gates_json") or ["diff"]
    return payload


def event_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": row["id"],
        "taskId": row["task_id"],
        "timestamp": row["ts"],
        "type": row["type"],
        "message": row["message"],
    }
    if row.get("details_json"):
        payload["details"] = row["details_json"]
    if row.get("step_number") is not None:
        payload["stepNumber"] = row["step_number"]
    if row.get("total_steps") is not None:
        payload["totalSteps"] = row["total_steps"]
    payload["seq"] = row["seq"]
    return payload


def approval_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": row["id"],
        "taskId": row["task_id"],
        "gate": row["gate"],
        "hash": row["hash"],
        "summary": row["summary"],
        "proposedChanges": row.get("changes_json") or [],
        "diff": row["diff"],
        "state": row["state"],
        "createdAt": row["created_at"],
        "items": row.get("items_json") or [],
        "decisions": row.get("decisions_json") or {},
    }
    for key, column in (
        ("expiresAt", "expires_at"),
        ("respondedAt", "responded_at"),
        ("responseComment", "response_comment"),
        ("decision", "decision"),
    ):
        if row.get(column) is not None:
            payload[key] = row[column]
    return payload


def checkpoint_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "taskId": row["task_id"],
        "name": row["name"],
        "payload": row.get("payload_json") or {},
        "createdAt": row["created_at"],
        "agentId": row.get("agent_id"),
        "idempotencyKey": row.get("idempotency_key"),
    }


def agent_to_dict(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    capabilities = row.get("capabilities_json")
    if isinstance(capabilities, str):
        try:
            import json as _json

            capabilities = _json.loads(capabilities)
        except ValueError:
            capabilities = []
    return {
        "agentId": row["agent_id"],
        "hostname": row["hostname"],
        "workspace": row["workspace"],
        "status": row["status"],
        "lastHeartbeat": row["last_heartbeat"],
        "version": row["version"],
        "os": row["os"],
        "activeTaskId": row.get("active_task_id"),
        "batteryLevel": row.get("battery_level"),
        "isSimulated": bool(row.get("is_simulated")),
        "capabilities": capabilities or [],
    }
