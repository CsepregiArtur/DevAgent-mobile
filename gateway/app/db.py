"""SQLite persistence for tasks, events, approvals, checkpoints and agents.

Design notes
------------
* One writer connection per process, serialised with an ``asyncio.Lock``; SQLite
  in WAL mode is more than enough for a single MacBook agent + a handful of phones.
* ``task_events.seq`` is a global monotonic counter. It doubles as the SSE
  ``id:`` so a reconnecting PWA can resume with ``Last-Event-ID`` without losing
  events (requirement: "evenimente și progres în timp real" + safe reconnection).
* Nothing is applied on the MacBook before an approval; the approval row is the
  single source of truth and carries a content hash used for optimistic locking.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

TERMINAL_STATUSES = ("completed", "failed", "cancelled")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    instruction     TEXT NOT NULL,
    workspace       TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'supervised',
    model           TEXT NOT NULL,
    status          TEXT NOT NULL,
    current_step    TEXT,
    step_index      INTEGER,
    total_steps     INTEGER,
    gates_json      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    interrupted_at  TEXT,
    resumed_at      TEXT,
    agent_id        TEXT,
    attempt         INTEGER NOT NULL DEFAULT 0,
    result_json     TEXT,
    error           TEXT,
    idempotency_key TEXT UNIQUE,
    target_agent_id TEXT,
    required_capabilities_json TEXT
);

CREATE TABLE IF NOT EXISTS task_events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    id           TEXT NOT NULL UNIQUE,
    task_id      TEXT NOT NULL,
    ts           TEXT NOT NULL,
    type         TEXT NOT NULL,
    message      TEXT NOT NULL,
    details_json TEXT,
    step_number  INTEGER,
    total_steps  INTEGER
);

CREATE TABLE IF NOT EXISTS approvals (
    id               TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL,
    gate             TEXT NOT NULL,
    hash             TEXT NOT NULL,
    summary          TEXT NOT NULL,
    diff             TEXT NOT NULL,
    changes_json     TEXT,
    items_json       TEXT,
    decisions_json   TEXT,
    state            TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    expires_at       TEXT,
    responded_at     TEXT,
    response_comment TEXT,
    decision         TEXT
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    name            TEXT NOT NULL,
    payload_json    TEXT,
    idempotency_key TEXT UNIQUE,
    agent_id        TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id          TEXT PRIMARY KEY,
    hostname          TEXT NOT NULL,
    os                TEXT,
    version           TEXT,
    workspace         TEXT,
    status            TEXT NOT NULL,
    last_heartbeat    TEXT NOT NULL,
    connected_at      TEXT,
    active_task_id    TEXT,
    battery_level     INTEGER,
    capabilities_json TEXT,
    meta_json         TEXT,
    is_simulated      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks(status, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(agent_id, status);
CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id, seq);
CREATE INDEX IF NOT EXISTS idx_approvals_task ON approvals(task_id, state);
CREATE INDEX IF NOT EXISTS idx_checkpoints_task ON checkpoints(task_id, created_at);
"""

TASK_FIELDS = {
    "instruction",
    "workspace",
    "mode",
    "model",
    "status",
    "current_step",
    "step_index",
    "total_steps",
    "gates_json",
    "started_at",
    "completed_at",
    "interrupted_at",
    "resumed_at",
    "agent_id",
    "attempt",
    "result_json",
    "error",
    "idempotency_key",
    "target_agent_id",
    "required_capabilities_json",
}

# Columns added after the first release (applied on connect, SQLite has no
# "ALTER TABLE ... IF NOT EXISTS").
MIGRATIONS: dict[str, str] = {
    "target_agent_id": "ALTER TABLE tasks ADD COLUMN target_agent_id TEXT",
    "required_capabilities_json": "ALTER TABLE tasks ADD COLUMN required_capabilities_json TEXT",
}


def utcnow() -> str:
    """ISO-8601 UTC timestamp with millisecond precision (e.g. 2026-10-16T20:41:02.123Z)."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:  # pragma: no cover - defensive
        return None


def new_task_id() -> str:
    """Human friendly, collision-free task identifier."""
    return f"task-{uuid.uuid4().hex[:10]}"


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return fallback


class Database:
    """Thin async wrapper around a single aiosqlite connection."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle -------------------------------------------------------
    async def connect(self) -> None:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        await self._migrate()

    async def _migrate(self) -> None:
        columns = {
            row["name"]
            for row in await self._fetchall("PRAGMA table_info(tasks)")
        }
        for column, statement in MIGRATIONS.items():
            if column not in columns:
                await self._write(statement)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    async def _fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        async with self._lock:
            cursor = await self.conn.execute(sql, tuple(params))
            rows = await cursor.fetchall()
            await cursor.close()
        return [dict(row) for row in rows]

    async def _fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = await self._fetchall(sql, params)
        return rows[0] if rows else None

    async def _write(self, sql: str, params: Iterable[Any] = ()) -> None:
        async with self._lock:
            await self.conn.execute(sql, tuple(params))
            await self.conn.commit()

    # -- kv --------------------------------------------------------------
    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = await self._fetchone("SELECT value FROM kv WHERE key = ?", (key,))
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self._write(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    async def get_flag(self, key: str, default: bool = False) -> bool:
        raw = await self.get_setting(key)
        if raw is None:
            return default
        return raw == "1"

    async def set_flag(self, key: str, value: bool) -> None:
        await self.set_setting(key, "1" if value else "0")

    # -- tasks -----------------------------------------------------------
    async def create_task(
        self,
        *,
        instruction: str,
        workspace: str,
        mode: str,
        model: str,
        gates: list[str],
        agent_id: str | None = None,
        idempotency_key: str | None = None,
        target_agent_id: str | None = None,
        required_capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        task_id = new_task_id()
        now = utcnow()
        await self._write(
            """
            INSERT INTO tasks (id, instruction, workspace, mode, model, status,
                               created_at, updated_at, gates_json, agent_id, idempotency_key,
                               target_agent_id, required_capabilities_json)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                instruction,
                workspace,
                mode,
                model,
                now,
                now,
                json.dumps(gates),
                agent_id,
                idempotency_key,
                target_agent_id,
                json.dumps(required_capabilities or []),
            ),
        )
        task = await self.get_task(task_id)
        assert task is not None
        return task

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = await self._fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return self._decode_task(row) if row else None

    async def get_task_by_idempotency(self, key: str) -> dict[str, Any] | None:
        row = await self._fetchone("SELECT * FROM tasks WHERE idempotency_key = ?", (key,))
        return self._decode_task(row) if row else None

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: list[Any] = []
        if status and status != "all":
            clauses.append("status = ?")
            params.append(status)
        if search:
            clauses.append("(lower(instruction) LIKE ? OR lower(id) LIKE ? OR lower(workspace) LIKE ?)")
            needle = f"%{search.lower()}%"
            params.extend([needle, needle, needle])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        count_row = await self._fetchone(f"SELECT COUNT(*) AS c FROM tasks {where}", params)
        rows = await self._fetchall(
            f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [self._decode_task(r) for r in rows], int(count_row["c"] if count_row else 0)

    async def update_task(self, task_id: str, **fields: Any) -> dict[str, Any] | None:
        updates = {k: v for k, v in fields.items() if k in TASK_FIELDS}
        if updates.get("gates_json") is not None and not isinstance(updates["gates_json"], str):
            updates["gates_json"] = json.dumps(updates["gates_json"])
        if isinstance(updates.get("result_json"), (dict, list)):
            updates["result_json"] = json.dumps(updates["result_json"])
        if not updates:
            return await self.get_task(task_id)
        updates["updated_at"] = utcnow()
        assignments = ", ".join(f"{column} = ?" for column in updates)
        await self._write(
            f"UPDATE tasks SET {assignments} WHERE id = ?",
            (*updates.values(), task_id),
        )
        return await self.get_task(task_id)

    async def next_queued_task(self) -> dict[str, Any] | None:
        row = await self._fetchone(
            "SELECT * FROM tasks WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
        )
        return self._decode_task(row) if row else None

    async def queued_candidates(self) -> list[dict[str, Any]]:
        """All queued tasks, oldest first - used for capability/agent matching."""
        rows = await self._fetchall(
            "SELECT * FROM tasks WHERE status = 'queued' ORDER BY created_at ASC"
        )
        return [self._decode_task(r) for r in rows]

    async def queued_tasks(self) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            "SELECT * FROM tasks WHERE status = 'queued' ORDER BY created_at ASC"
        )
        return [self._decode_task(r) for r in rows]

    async def tasks_pending_agent(self, agent_id: str) -> list[dict[str, Any]]:
        """Tasks that must be continued as soon as ``agent_id`` (re)connects.

        A task qualifies when it is still assigned to the agent but nobody is
        executing it right now: ``running``/``awaiting_approval`` tasks (the
        socket died, or the operator approved something while the laptop was
        away) and ``queued`` tasks that were pushed back after an interruption.
        """
        rows = await self._fetchall(
            """
            SELECT * FROM tasks
             WHERE agent_id = ?
               AND (
                    status IN ('running', 'awaiting_approval')
                 OR (status = 'queued' AND interrupted_at IS NOT NULL)
               )
             ORDER BY created_at ASC
            """,
            (agent_id,),
        )
        return [self._decode_task(r) for r in rows]

    async def status_counts(self) -> dict[str, int]:
        rows = await self._fetchall("SELECT status, COUNT(*) AS c FROM tasks GROUP BY status")
        counts = {row["status"]: int(row["c"]) for row in rows}
        return {
            "total": sum(counts.values()),
            "queued": counts.get("queued", 0),
            "running": counts.get("running", 0),
            "awaitingApproval": counts.get("awaiting_approval", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
            "cancelled": counts.get("cancelled", 0),
        }

    # -- events ----------------------------------------------------------
    async def add_event(
        self,
        task_id: str,
        *,
        type: str,
        message: str,
        details: dict[str, Any] | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        event_id = event_id or f"evt-{uuid.uuid4().hex[:12]}"
        async with self._lock:
            cursor = await self.conn.execute(
                """
                INSERT INTO task_events (id, task_id, ts, type, message, details_json,
                                         step_number, total_steps)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    task_id,
                    utcnow(),
                    type,
                    message,
                    json.dumps(details) if details else None,
                    step_number,
                    total_steps,
                ),
            )
            seq = cursor.lastrowid
            await cursor.close()
            await self.conn.commit()
        row = await self._fetchone("SELECT * FROM task_events WHERE seq = ?", (seq,))
        assert row is not None
        return self._decode_event(row)

    async def events_for_task(self, task_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            "SELECT * FROM (SELECT * FROM task_events WHERE task_id = ? ORDER BY seq DESC LIMIT ?) "
            "ORDER BY seq ASC",
            (task_id, limit),
        )
        return [self._decode_event(r) for r in rows]

    async def events_after(
        self, after_seq: int, *, task_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        if task_id:
            rows = await self._fetchall(
                "SELECT * FROM task_events WHERE task_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
                (task_id, after_seq, limit),
            )
        else:
            rows = await self._fetchall(
                "SELECT * FROM task_events WHERE seq > ? ORDER BY seq ASC LIMIT ?",
                (after_seq, limit),
            )
        return [self._decode_event(r) for r in rows]

    async def max_event_seq(self) -> int:
        row = await self._fetchone("SELECT COALESCE(MAX(seq), 0) AS s FROM task_events")
        return int(row["s"] if row else 0)

    # -- approvals -------------------------------------------------------
    async def create_approval(
        self,
        *,
        task_id: str,
        gate: str,
        hash: str,
        summary: str,
        diff: str,
        changes: list[dict[str, Any]] | None = None,
        items: list[dict[str, Any]] | None = None,
        timeout_seconds: int = 0,
    ) -> dict[str, Any]:
        approval_id = f"appr-{uuid.uuid4().hex[:10]}"
        now = utcnow()
        expires_at = None
        if timeout_seconds > 0:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        await self._write(
            """
            INSERT INTO approvals (id, task_id, gate, hash, summary, diff, changes_json,
                                   items_json, state, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                approval_id,
                task_id,
                gate,
                hash,
                summary,
                diff,
                json.dumps(changes or []),
                json.dumps(items or []),
                now,
                expires_at,
            ),
        )
        approval = await self.get_approval(approval_id)
        assert approval is not None
        return approval

    async def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        row = await self._fetchone("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        return self._decode_approval(row) if row else None

    async def pending_approval(self, task_id: str) -> dict[str, Any] | None:
        row = await self._fetchone(
            "SELECT * FROM approvals WHERE task_id = ? AND state = 'pending' "
            "ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        )
        return self._decode_approval(row) if row else None

    async def latest_approval(self, task_id: str) -> dict[str, Any] | None:
        row = await self._fetchone(
            "SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        )
        return self._decode_approval(row) if row else None

    async def approvals_for_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            "SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at ASC", (task_id,)
        )
        return [self._decode_approval(r) for r in rows]

    async def expired_pending_approvals(self) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            "SELECT * FROM approvals WHERE state = 'pending' AND expires_at IS NOT NULL "
            "AND expires_at <= ?",
            (utcnow(),),
        )
        return [self._decode_approval(r) for r in rows]

    async def resolve_approval(
        self,
        approval_id: str,
        *,
        state: str,
        decision: str | None,
        comment: str | None,
        decisions: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        await self._write(
            """
            UPDATE approvals
               SET state = ?, decision = ?, response_comment = ?, responded_at = ?,
                   decisions_json = COALESCE(?, decisions_json)
             WHERE id = ?
            """,
            (
                state,
                decision,
                comment,
                utcnow(),
                json.dumps(decisions) if decisions is not None else None,
                approval_id,
            ),
        )
        return await self.get_approval(approval_id)

    # -- checkpoints -----------------------------------------------------
    async def save_checkpoint(
        self,
        *,
        task_id: str,
        name: str,
        payload: dict[str, Any] | None = None,
        agent_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if idempotency_key:
            existing = await self._fetchone(
                "SELECT * FROM checkpoints WHERE idempotency_key = ?", (idempotency_key,)
            )
            if existing:
                return self._decode_checkpoint(existing)
        checkpoint_id = f"ckpt-{uuid.uuid4().hex[:10]}"
        await self._write(
            """
            INSERT INTO checkpoints (id, task_id, name, payload_json, idempotency_key,
                                     agent_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                checkpoint_id,
                task_id,
                name,
                json.dumps(payload or {}),
                idempotency_key,
                agent_id,
                utcnow(),
            ),
        )
        row = await self._fetchone("SELECT * FROM checkpoints WHERE id = ?", (checkpoint_id,))
        assert row is not None
        return self._decode_checkpoint(row)

    async def latest_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        row = await self._fetchone(
            "SELECT * FROM checkpoints WHERE task_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (task_id,),
        )
        return self._decode_checkpoint(row) if row else None

    async def checkpoints_for_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            "SELECT * FROM checkpoints WHERE task_id = ? ORDER BY created_at ASC", (task_id,)
        )
        return [self._decode_checkpoint(r) for r in rows]

    async def delete_checkpoints(self, task_id: str) -> None:
        await self._write("DELETE FROM checkpoints WHERE task_id = ?", (task_id,))

    # -- agents ----------------------------------------------------------
    async def upsert_agent(
        self,
        *,
        agent_id: str,
        hostname: str,
        os_name: str | None = None,
        version: str | None = None,
        workspace: str | None = None,
        status: str = "online",
        active_task_id: str | None = None,
        battery_level: int | None = None,
        capabilities: list[str] | None = None,
        is_simulated: bool = False,
        mark_connected: bool = False,
    ) -> dict[str, Any]:
        now = utcnow()
        existing = await self._fetchone("SELECT * FROM agents WHERE agent_id = ?", (agent_id,))
        if existing is None:
            await self._write(
                """
                INSERT INTO agents (agent_id, hostname, os, version, workspace, status,
                                    last_heartbeat, connected_at, active_task_id,
                                    battery_level, capabilities_json, is_simulated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    hostname,
                    os_name,
                    version,
                    workspace,
                    status,
                    now,
                    now if mark_connected else None,
                    active_task_id,
                    battery_level,
                    json.dumps(capabilities or []),
                    1 if is_simulated else 0,
                ),
            )
        else:
            updates: dict[str, Any] = {
                "hostname": hostname,
                "status": status,
                "last_heartbeat": now,
                "is_simulated": 1 if is_simulated else 0,
            }
            if os_name:
                updates["os"] = os_name
            if version:
                updates["version"] = version
            if workspace:
                updates["workspace"] = workspace
            if battery_level is not None:
                updates["battery_level"] = battery_level
            if capabilities is not None:
                updates["capabilities_json"] = json.dumps(capabilities)
            if mark_connected:
                updates["connected_at"] = now
            if status != "offline" or active_task_id is not None:
                updates["active_task_id"] = active_task_id
            assignments = ", ".join(f"{column} = ?" for column in updates)
            await self._write(
                f"UPDATE agents SET {assignments} WHERE agent_id = ?",
                (*updates.values(), agent_id),
            )
        agent = await self.get_agent(agent_id)
        assert agent is not None
        return agent

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        row = await self._fetchone("SELECT * FROM agents WHERE agent_id = ?", (agent_id,))
        return self._decode_agent(row) if row else None

    async def list_agents(self) -> list[dict[str, Any]]:
        rows = await self._fetchall("SELECT * FROM agents ORDER BY last_heartbeat DESC")
        return [self._decode_agent(row) for row in rows]

    async def primary_agent(self) -> dict[str, Any] | None:
        """Most recently seen real agent (kept for single-agent consumers)."""
        row = await self._fetchone(
            "SELECT * FROM agents WHERE is_simulated = 0 ORDER BY last_heartbeat DESC LIMIT 1"
        )
        if row:
            return self._decode_agent(row)
        row = await self._fetchone("SELECT * FROM agents ORDER BY last_heartbeat DESC LIMIT 1")
        return self._decode_agent(row) if row else None

    async def mark_all_agents_offline(self) -> None:
        """Gateway restart: no socket survives, so nothing may stay 'online'."""
        await self._write(
            "UPDATE agents SET status = 'offline', active_task_id = NULL WHERE status != 'offline'"
        )

    async def delete_agent(self, agent_id: str) -> None:
        await self._write("DELETE FROM agents WHERE agent_id = ?", (agent_id,))

    async def set_agent_status(
        self,
        agent_id: str,
        status: str,
        *,
        active_task_id: str | None = None,
        clear_active_task: bool = False,
    ) -> None:
        if clear_active_task:
            await self._write(
                "UPDATE agents SET status = ?, active_task_id = NULL WHERE agent_id = ?",
                (status, agent_id),
            )
        else:
            await self._write(
                "UPDATE agents SET status = ?, active_task_id = COALESCE(?, active_task_id) "
                "WHERE agent_id = ?",
                (status, active_task_id, agent_id),
            )

    async def stale_agents(self, cutoff_iso: str) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            "SELECT * FROM agents WHERE status != 'offline' AND last_heartbeat < ?",
            (cutoff_iso,),
        )
        return [self._decode_agent(row) for row in rows]

    # -- decoding --------------------------------------------------------
    @staticmethod
    def _decode_task(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["gates_json"] = _loads(row.get("gates_json"), ["diff"])
        row["result_json"] = _loads(row.get("result_json"), None)
        row["required_capabilities_json"] = _loads(row.get("required_capabilities_json"), [])
        return row

    @staticmethod
    def _decode_event(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["details_json"] = _loads(row.get("details_json"), None)
        return row

    @staticmethod
    def _decode_approval(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["changes_json"] = _loads(row.get("changes_json"), [])
        row["items_json"] = _loads(row.get("items_json"), [])
        row["decisions_json"] = _loads(row.get("decisions_json"), {})
        return row

    @staticmethod
    def _decode_checkpoint(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["payload_json"] = _loads(row.get("payload_json"), {})
        return row

    @staticmethod
    def _decode_agent(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["capabilities_json"] = _loads(row.get("capabilities_json"), [])
        row["meta_json"] = _loads(row.get("meta_json"), {})
        return row
