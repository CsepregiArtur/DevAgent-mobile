"""Optional built-in agent simulator.

Purpose: let the PWA be demoed and end-to-end tested (queue -> live events ->
approval -> completion) before/without a real MacBook agent being enrolled.
It never touches the machine hosting the gateway: it only synthesises events and
approvals through the same service layer the real agent uses.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .config import Settings
from .db import Database, parse_ts
from .service import TaskService, hash_diff

logger = logging.getLogger("devagent.simulator")

SIMULATED_AGENT_ID = "simulated-agent"

SIMULATED_DIFF = """--- a/src/routes/auth.ts
+++ b/src/routes/auth.ts
@@ -12,6 +12,8 @@ import { Router } from 'express';
 import { validateCredentials } from '../services/auth';
+import { createRateLimiter } from '../middleware/rate-limiter';

-const router = Router();
+const router = Router();
+const authLimiter = createRateLimiter({ windowMs: 60 * 1000, max: 10 });

-router.post('/login', async (req, res) => {
+router.post('/login', authLimiter, async (req, res) => {
   const { email, password } = req.body;
--- /dev/null
+++ b/src/middleware/rate-limiter.ts
@@ -0,0 +1,24 @@
+import { Request, Response, NextFunction } from 'express';
+
+export function createRateLimiter(options: { windowMs: number; max: number }) {
+  const buckets = new Map<string, { count: number; resetAt: number }>();
+  return function rateLimiter(req: Request, res: Response, next: NextFunction) {
+    const key = req.ip || 'anonymous';
+    const now = Date.now();
+    const bucket = buckets.get(key);
+    if (!bucket || bucket.resetAt < now) {
+      buckets.set(key, { count: 1, resetAt: now + options.windowMs });
+      return next();
+    }
+    if (++bucket.count > options.max) {
+      return res.status(429).json({ error: 'Rate limit exceeded' });
+    }
+    next();
+  };
+}"""

SIMULATED_FILES = [
    {"path": "src/middleware/rate-limiter.ts", "status": "created", "additions": 24, "deletions": 0},
    {"path": "src/routes/auth.ts", "status": "modified", "additions": 4, "deletions": 2},
]


class Simulator:
    """Drives queued tasks through the full lifecycle when enabled."""

    def __init__(self, service: TaskService, db: Database, settings: Settings) -> None:
        self.service = service
        self.db = db
        self.settings = settings

    async def tick(self) -> None:
        if not await self.service.simulator_enabled():
            return
        await self._ensure_agent()
        task = await self.db.next_queued_task()
        if task is not None:
            await self._start(task["id"])
            return

        tasks, _ = await self.db.list_tasks(status="running", limit=50)
        for task in tasks:
            if task.get("agent_id") == SIMULATED_AGENT_ID:
                await self._advance(task)
                return

        awaiting, _ = await self.db.list_tasks(status="awaiting_approval", limit=50)
        for task in awaiting:
            if task.get("agent_id") == SIMULATED_AGENT_ID:
                return  # waiting for the operator, nothing to do

    # ------------------------------------------------------------------
    async def _ensure_agent(self) -> None:
        await self.db.upsert_agent(
            agent_id=SIMULATED_AGENT_ID,
            hostname="gateway-simulator",
            os_name="simulated",
            version="v1.0.0",
            workspace=self.settings.default_workspace,
            status="online",
            active_task_id=(await self._active_task_id()),
            is_simulated=True,
            mark_connected=True,
        )

    async def _active_task_id(self) -> str | None:
        tasks, _ = await self.db.list_tasks(status="running", limit=10)
        for task in tasks:
            if task.get("agent_id") == SIMULATED_AGENT_ID:
                return task["id"]
        return None

    async def _start(self, task_id: str) -> None:
        task = await self.db.get_task(task_id)
        if task is None:
            return
        await self.db.update_task(
            task_id,
            status="running",
            agent_id=SIMULATED_AGENT_ID,
            attempt=(task.get("attempt") or 0) + 1,
            started_at=task.get("started_at") or _now(),
            current_step="Checking repository git status and index",
            step_index=1,
            total_steps=4,
        )
        await self.db.set_agent_status(SIMULATED_AGENT_ID, "busy", active_task_id=task_id)
        await self.service._emit(
            task_id,
            type="status_change",
            message="Task acquired by the built-in gateway simulator (no MacBook connected).",
            details={"status": "running", "simulated": True},
        )

    async def _advance(self, task: dict) -> None:
        task_id = task["id"]
        events = await self.db.events_for_task(task_id, 200)
        steps = [e for e in events if e["type"] == "step"]
        updated = parse_ts(task.get("updated_at"))
        elapsed = (
            (datetime.now(timezone.utc) - updated).total_seconds() if updated else 99.0
        )
        if elapsed < 1.5:
            return

        gates = task.get("gates_json") or []
        approvals = await self.db.approvals_for_task(task_id)
        approved = [a for a in approvals if a["state"] == "approved"]

        if len(steps) == 0:
            await self.db.update_task(task_id, current_step="Parsing workspace source tree", step_index=2)
            await self.service._emit(
                task_id,
                type="step",
                message="Inspecting code dependencies and AST structure",
                step_number=2,
                total_steps=4,
            )
            return

        if not approvals and gates:
            await self.db.update_task(
                task_id, current_step="Proposed changes ready for review", step_index=3
            )
            await self.service.submit_approval_request(
                SIMULATED_AGENT_ID,
                {
                    "taskId": task_id,
                    "gate": gates[0],
                    "summary": f"Generated proposed changes for: {task['instruction'][:120]}",
                    "diff": SIMULATED_DIFF,
                    "hash": hash_diff(SIMULATED_DIFF),
                    "files": SIMULATED_FILES,
                },
            )
            return

        if gates and not approved:
            return  # rejected or expired: the task no longer runs

        if not [e for e in events if e["type"] == "test_run"]:
            await self.db.update_task(task_id, current_step="Running local test suites", step_index=4)
            await self.service._emit(
                task_id,
                type="test_run",
                message="Test runner executed: 4 passed, 0 failed, duration 1.4s",
                step_number=4,
                total_steps=4,
            )
            return

        await self.service.report_result(
            SIMULATED_AGENT_ID,
            {
                "taskId": task_id,
                "status": "completed",
                "result": {
                    "summary": f"Successfully completed: {task['instruction'][:160]}",
                    "filesModified": [f["path"] for f in SIMULATED_FILES],
                    "testsPassed": True,
                    "testOutput": "PASS tests/features/agent-action.test.ts\nAll tests passed (4/4).",
                    "durationMs": 8200,
                    "branchName": f"agent/feat-{task_id[-6:]}",
                },
            },
        )


def _now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )
