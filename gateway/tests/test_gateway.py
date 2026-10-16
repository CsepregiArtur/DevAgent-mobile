"""End-to-end tests for the DevAgent gateway.

They cover exactly the product requirements:

* queue survives an offline MacBook (tasks stay ``queued``)
* unique task identifiers
* state machine: queued -> running -> awaiting_approval -> completed / failed
* live events (SSE, replayable by ``Last-Event-ID``)
* approval before applying, hash protected (409 on stale proposal)
* per-file / per-hunk review items and multi gate ordering
* reconnect + safe resumption from the last checkpoint
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.server import create_app

TOKEN = "test-token-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
DIFF = """--- a/src/routes/auth.ts
+++ b/src/routes/auth.ts
@@ -1,3 +1,4 @@
-const router = Router();
+const router = Router();
+const limiter = createRateLimiter({ windowMs: 60000, max: 10 });
"""


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        pwa_token=TOKEN,
        agent_token=TOKEN,
        root_path="/mobile",
        host="127.0.0.1",
        port=8082,
        database_path=tmp_path / "gateway.sqlite3",
        redis_url=None,
        static_dir=None,
        agent_offline_seconds=1,
        heartbeat_interval_seconds=1,
        approval_timeout_seconds=0,
        queue_scan_seconds=0.2,
        workspaces=("~/dev/mobile-agent-platform",),
        default_workspace="~/dev/mobile-agent-platform",
        deepseek_base_url="",
        default_model="DeepSeek-Coder-V2.5",
        max_events_per_task=500,
        simulator_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture()
def client(tmp_path: Path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


def create_task(client: TestClient, **payload) -> dict:
    body = {"instruction": "Add a rate limiter to the auth routes", "mode": "supervised"}
    body.update(payload)
    response = client.post("/mobile/api/tasks", json=body, headers=AUTH)
    assert response.status_code == 201, response.text
    return response.json()["task"]


# ----------------------------------------------------------------------
# auth + health
# ----------------------------------------------------------------------
def test_healthz_is_public_and_api_requires_token(client: TestClient) -> None:
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/mobile/api/agent/status").status_code == 401
    assert client.get("/mobile/api/agent/status", headers=AUTH).status_code == 200
    # query-string token keeps EventSource (which cannot set headers) working
    assert client.get(f"/mobile/api/diagnostics?token={TOKEN}").status_code == 200


# ----------------------------------------------------------------------
# queue + ids
# ----------------------------------------------------------------------
def test_tasks_are_queued_while_agent_is_offline_and_ids_are_unique(client: TestClient) -> None:
    first = create_task(client)
    second = create_task(client, instruction="Refactor the DB pool")

    assert first["id"] != second["id"]
    assert first["id"].startswith("task-")
    for task in (first, second):
        assert task["status"] == "queued"
        assert task["events"][0]["type"] == "status_change"

    listed = client.get("/mobile/api/tasks", headers=AUTH).json()
    assert listed["total"] == 2
    assert [t["id"] for t in listed["tasks"]] == [second["id"], first["id"]]

    status = client.get("/mobile/api/agent/status", headers=AUTH).json()
    assert status["connected"] is False
    assert status["status"] == "offline"


def test_status_filter_accepts_waiting_approval_alias(client: TestClient) -> None:
    response = client.get("/mobile/api/tasks?status=waiting_approval", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["tasks"] == []
    assert client.get("/mobile/api/tasks?status=nonsense", headers=AUTH).status_code == 400


def test_diagnostics_and_workspaces(client: TestClient) -> None:
    diagnostics = client.get("/mobile/api/diagnostics", headers=AUTH).json()
    assert diagnostics["gateway"]["status"] == "online"
    assert diagnostics["queue"]["total"] == 0
    assert diagnostics["protocol"]["resume"] == "checkpoint based"

    workspaces = client.get("/mobile/api/workspaces", headers=AUTH).json()
    assert workspaces["defaultWorkspace"] == "~/dev/mobile-agent-platform"


def test_simulator_drives_the_queue_without_a_macbook(client: TestClient) -> None:
    response = client.post("/mobile/api/agent/simulate-toggle", json={"enabled": True}, headers=AUTH)
    assert response.json()["simulatorActive"] is True

    task = create_task(client)
    deadline = time.time() + 8
    status = "queued"
    while time.time() < deadline:
        status = client.get(f"/mobile/api/tasks/{task['id']}", headers=AUTH).json()["task"]["status"]
        if status in {"awaiting_approval", "completed"}:
            break
        time.sleep(0.2)
    assert status == "awaiting_approval"

    detail = client.get(f"/mobile/api/tasks/{task['id']}", headers=AUTH).json()["task"]
    approval = detail["approval"]
    assert approval["gate"] == "diff"
    assert approval["state"] == "pending"
    assert [item["id"] for item in approval["items"]]
    assert approval["items"][0]["hunks"], "per-hunk review items derived from the diff"


# ----------------------------------------------------------------------
# full agent lifecycle over the WebSocket
# ----------------------------------------------------------------------
def test_agent_lifecycle_with_approval_gate(client: TestClient) -> None:
    with client.websocket_connect(f"/mobile/agent/ws?token={TOKEN}&agentId=macbook-test") as ws:
        ws.send_json(
            {
                "type": "hello",
                "hostname": "Arthur-MacBook-Pro.local",
                "os": "macOS 15.1 arm64",
                "version": "v1.0.0",
                "workspace": "~/dev/mobile-agent-platform",
                "capabilities": ["git", "shell", "llm"],
            }
        )
        welcome = ws.receive_json()
        assert welcome["type"] == "welcome"
        assert welcome["heartbeatIntervalSeconds"] == 1

        task = create_task(client)
        assignment = ws.receive_json()
        assert assignment["type"] == "task_assign"
        assert assignment["action"] == "start"
        assert assignment["task"]["id"] == task["id"]

        ws.send_json(
            {
                "type": "checkpoint",
                "taskId": task["id"],
                "name": "workspace-ready",
                "idempotencyKey": "ck-1",
                "payload": {"branch": f"agent/{task['id']}", "step": "analysis"},
            }
        )
        assert ws.receive_json()["type"] == "checkpoint_ack"

        ws.send_json(
            {
                "type": "approval_request",
                "taskId": task["id"],
                "gate": "diff",
                "summary": "Add rate limiting to /login",
                "diff": DIFF,
                "files": [{"path": "src/routes/auth.ts", "status": "modified", "additions": 2, "deletions": 1}],
            }
        )
        ack = ws.receive_json()
        assert ack["type"] == "approval_ack"
        assert ack["gate"] == "diff"

        detail = client.get(f"/mobile/api/tasks/{task['id']}", headers=AUTH).json()["task"]
        assert detail["status"] == "awaiting_approval"
        approval_hash = detail["approval"]["hash"]

        # stale hash is rejected -> optimistic locking
        stale = client.post(
            f"/mobile/api/tasks/{task['id']}/approval",
            json={"action": "approve", "hash": "sha256:deadbeef"},
            headers=AUTH,
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["expectedHash"] == approval_hash

        approved = client.post(
            f"/mobile/api/tasks/{task['id']}/approval",
            json={"action": "approve", "hash": approval_hash, "comment": "looks good"},
            headers=AUTH,
        )
        assert approved.status_code == 200
        assert approved.json()["task"]["status"] == "running"

        decision = ws.receive_json()
        assert decision["type"] == "approval_decision"
        assert decision["decision"] == "approve"

        ws.send_json(
            {
                "type": "result",
                "taskId": task["id"],
                "status": "completed",
                "result": {
                    "summary": "Rate limiter applied",
                    "filesModified": ["src/routes/auth.ts"],
                    "testsPassed": True,
                    "durationMs": 1200,
                    "branchName": "agent/rate-limit",
                },
            }
        )
        assert ws.receive_json()["type"] == "result_ack"

        final = client.get(f"/mobile/api/tasks/{task['id']}", headers=AUTH).json()["task"]
        assert final["status"] == "completed"
        assert final["result"]["testsPassed"] is True
        assert final["approval"]["state"] == "approved"
        event_types = [event["type"] for event in final["events"]]
        assert "approval_required" in event_types
        assert "approval_resolved" in event_types


def test_rejection_fails_the_task(client: TestClient) -> None:
    with client.websocket_connect(f"/mobile/agent/ws?token={TOKEN}&agentId=macbook-test") as ws:
        ws.send_json({"type": "hello", "hostname": "mbp", "workspace": "~/dev/x"})
        ws.receive_json()
        task = create_task(client)
        ws.receive_json()

        ws.send_json(
            {"type": "approval_request", "taskId": task["id"], "gate": "diff", "diff": DIFF, "files": []}
        )
        ws.receive_json()
        detail = client.get(f"/mobile/api/tasks/{task['id']}", headers=AUTH).json()["task"]

        rejected = client.post(
            f"/mobile/api/tasks/{task['id']}/approval",
            json={"action": "reject", "hash": detail["approval"]["hash"], "comment": "too risky"},
            headers=AUTH,
        )
        assert rejected.status_code == 200
        assert rejected.json()["task"]["status"] == "failed"
        assert "too risky" in rejected.json()["task"]["error"]


def test_multi_gate_order_is_enforced_and_items_are_recorded(client: TestClient) -> None:
    with client.websocket_connect(f"/mobile/agent/ws?token={TOKEN}&agentId=macbook-test") as ws:
        ws.send_json({"type": "hello", "hostname": "mbp", "workspace": "~/dev/x"})
        ws.receive_json()

        task = create_task(client, gates=["plan", "diff"])
        assert task["gates"] == ["plan", "diff"]
        ws.receive_json()

        # diff before plan: the gateway refuses out-of-order gates
        ws.send_json({"type": "approval_request", "taskId": task["id"], "gate": "diff", "diff": DIFF})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert "cannot be reviewed yet" in error["error"]

        ws.send_json({"type": "approval_request", "taskId": task["id"], "gate": "plan", "diff": DIFF})
        assert ws.receive_json()["type"] == "approval_ack"

        detail = client.get(f"/mobile/api/tasks/{task['id']}", headers=AUTH).json()["task"]
        approved = client.post(
            f"/mobile/api/tasks/{task['id']}/approval",
            json={"action": "approve", "hash": detail["approval"]["hash"]},
            headers=AUTH,
        )
        assert approved.status_code == 200
        assert ws.receive_json()["nextGate"] == "diff"

        ws.send_json({"type": "approval_request", "taskId": task["id"], "gate": "diff", "diff": DIFF})
        assert ws.receive_json()["type"] == "approval_ack"

        # per file / per hunk decisions
        detail = client.get(f"/mobile/api/tasks/{task['id']}", headers=AUTH).json()["task"]
        hash_value = detail["approval"]["hash"]
        partial = client.post(
            f"/mobile/api/tasks/{task['id']}/approval",
            json={
                "action": "approve",
                "hash": hash_value,
                "items": [
                    {"path": "src/routes/auth.ts", "decision": "approve", "hunks": [1]},
                    {"path": "src/middleware/rate-limiter.ts", "decision": "reject"},
                ],
            },
            headers=AUTH,
        )
        assert partial.status_code == 200
        decisions = partial.json()["approval"]["decisions"]
        assert decisions["src/routes/auth.ts"]["hunks"] == [1]
        assert decisions["src/middleware/rate-limiter.ts"]["decision"] == "reject"
        assert ws.receive_json()["decision"] == "approve"


def test_all_items_rejected_becomes_a_rejection(client: TestClient) -> None:
    with client.websocket_connect(f"/mobile/agent/ws?token={TOKEN}&agentId=macbook-test") as ws:
        ws.send_json({"type": "hello", "hostname": "mbp", "workspace": "~/dev/x"})
        ws.receive_json()
        task = create_task(client)
        ws.receive_json()
        ws.send_json({"type": "approval_request", "taskId": task["id"], "gate": "diff", "diff": DIFF})
        ws.receive_json()
        detail = client.get(f"/mobile/api/tasks/{task['id']}", headers=AUTH).json()["task"]

        response = client.post(
            f"/mobile/api/tasks/{task['id']}/approval",
            json={
                "action": "approve",
                "hash": detail["approval"]["hash"],
                "items": [{"path": "src/routes/auth.ts", "decision": "reject"}],
            },
            headers=AUTH,
        )
        assert response.status_code == 200
        assert response.json()["task"]["status"] == "failed"


# ----------------------------------------------------------------------
# reconnect + safe resumption
# ----------------------------------------------------------------------
def test_agent_reconnect_resumes_interrupted_task_from_checkpoint(client: TestClient) -> None:
    with client.websocket_connect(f"/mobile/agent/ws?token={TOKEN}&agentId=macbook-test") as ws:
        ws.send_json({"type": "hello", "hostname": "mbp", "workspace": "~/dev/x"})
        ws.receive_json()
        task = create_task(client)
        ws.receive_json()
        ws.send_json(
            {
                "type": "checkpoint",
                "taskId": task["id"],
                "name": "diff-ready",
                "idempotencyKey": "ck-42",
                "payload": {"branch": "agent/x", "worktree": "/tmp/wt", "stage": "diff"},
            }
        )
        ws.receive_json()

    # The laptop goes away: the watchdog must interrupt the task safely.
    deadline = time.time() + 6
    status = None
    while time.time() < deadline:
        status = client.get(f"/mobile/api/tasks/{task['id']}", headers=AUTH).json()["task"]["status"]
        if status == "queued":
            break
        time.sleep(0.2)
    assert status == "queued"

    detail = client.get(f"/mobile/api/tasks/{task['id']}", headers=AUTH).json()["task"]
    assert detail["interruptedAt"] is not None
    assert detail["checkpoints"][-1]["name"] == "diff-ready"
    assert any("checkpoint" in event["message"].lower() for event in detail["events"])

    with client.websocket_connect(f"/mobile/agent/ws?token={TOKEN}&agentId=macbook-test") as ws:
        ws.send_json({"type": "hello", "hostname": "mbp", "workspace": "~/dev/x"})
        welcome = ws.receive_json()
        assert task["id"] in welcome["resumedTasks"]

        resume = ws.receive_json()
        assert resume["type"] == "task_resume"
        assert resume["action"] == "resume"
        assert resume["checkpoint"]["name"] == "diff-ready"
        assert resume["checkpoint"]["payload"]["worktree"] == "/tmp/wt"
        assert resume["attempt"] == 2


def test_approved_proposal_is_applied_after_reconnect(client: TestClient) -> None:
    with client.websocket_connect(f"/mobile/agent/ws?token={TOKEN}&agentId=macbook-test") as ws:
        ws.send_json({"type": "hello", "hostname": "mbp", "workspace": "~/dev/x"})
        ws.receive_json()
        task = create_task(client)
        ws.receive_json()
        ws.send_json({"type": "approval_request", "taskId": task["id"], "gate": "diff", "diff": DIFF})
        ws.receive_json()
        detail = client.get(f"/mobile/api/tasks/{task['id']}", headers=AUTH).json()["task"]
        approval_hash = detail["approval"]["hash"]

    client.post(
        f"/mobile/api/tasks/{task['id']}/approval",
        json={"action": "approve", "hash": approval_hash, "comment": "ship it while I'm offline"},
        headers=AUTH,
    )

    with client.websocket_connect(f"/mobile/agent/ws?token={TOKEN}&agentId=macbook-test") as ws:
        ws.send_json({"type": "hello", "hostname": "mbp", "workspace": "~/dev/x"})
        ws.receive_json()
        message = ws.receive_json()
        assert message["type"] == "task_resume"
        assert message["action"] == "apply_approved"
        assert message["approval"]["state"] == "approved"


# ----------------------------------------------------------------------
# cancellation
# ----------------------------------------------------------------------
def test_cancel_notifies_the_agent_and_is_terminal(client: TestClient) -> None:
    with client.websocket_connect(f"/mobile/agent/ws?token={TOKEN}&agentId=macbook-test") as ws:
        ws.send_json({"type": "hello", "hostname": "mbp", "workspace": "~/dev/x"})
        ws.receive_json()
        task = create_task(client)
        ws.receive_json()

        response = client.post(f"/mobile/api/tasks/{task['id']}/cancel", headers=AUTH)
        assert response.status_code == 200
        assert response.json()["task"]["status"] == "cancelled"

        cancel = ws.receive_json()
        assert cancel["type"] == "cancel"
        assert cancel["taskId"] == task["id"]

        again = client.post(f"/mobile/api/tasks/{task['id']}/cancel", headers=AUTH)
        assert again.status_code == 400

        retried = client.post(f"/mobile/api/tasks/{task['id']}/retry", headers=AUTH)
        assert retried.status_code == 200
        # The idle connected agent picks the task up immediately, so it is either
        # queued or already running again at this point.
        assert retried.json()["task"]["status"] in {"queued", "running"}
        assert ws.receive_json()["type"] == "task_assign"


# ----------------------------------------------------------------------
# SSE
# ----------------------------------------------------------------------
# The SSE endpoints are validated against a real uvicorn instance (see
# scripts/smoke.sh): starlette's in-process TestClient does not stream
# an endless ``text/event-stream`` body, so a trivially passing assertion here
# would not prove that live progress works on a phone.


# ----------------------------------------------------------------------
# multi-agent fleet (the MacBook is not the only worker)
# ----------------------------------------------------------------------
HELLO = {"hostname": "host", "os": "test-os", "workspace": "~/dev/x", "capabilities": ["git"]}


def agent_url(agent_id: str) -> str:
    return f"/mobile/agent/ws?token={TOKEN}&agentId={agent_id}"


def say_hello(ws, **overrides) -> dict:
    ws.send_json({"type": "hello", **HELLO, **overrides})
    return ws.receive_json()


def test_fleet_status_aggregates_every_agent(client: TestClient) -> None:
    with client.websocket_connect(agent_url("agent-a")) as first, client.websocket_connect(
        agent_url("agent-b")
    ) as second:
        assert say_hello(first, hostname="macbook", capabilities=["git", "macos"])["type"] == "welcome"
        assert say_hello(second, hostname="linux-box", capabilities=["git"])["type"] == "welcome"

        status = client.get("/mobile/api/agent/status", headers=AUTH).json()
        assert status["connected"] is True
        assert status["onlineAgents"] == 2
        assert status["totalAgents"] == 2
        assert {agent["agentId"] for agent in status["agents"]} == {"agent-a", "agent-b"}
        assert all(agent["online"] for agent in status["agents"])
        # capabilities must be a list, not a JSON string
        macos = next(agent for agent in status["agents"] if agent["agentId"] == "agent-a")
        assert macos["capabilities"] == ["git", "macos"]
        assert macos["secondsSinceHeartbeat"] == 0

        fleet = client.get("/mobile/api/agents", headers=AUTH).json()
        assert fleet["onlineAgents"] == 2
        assert fleet["primaryAgentId"] in {"agent-a", "agent-b"}

        workspaces = client.get("/mobile/api/workspaces", headers=AUTH).json()
        assert {agent["agentId"] for agent in workspaces["agents"]} == {"agent-a", "agent-b"}

    after = client.get("/mobile/api/agent/status", headers=AUTH).json()
    assert after["onlineAgents"] == 0
    assert after["connected"] is False


def test_task_can_be_pinned_to_one_agent(client: TestClient) -> None:
    with client.websocket_connect(agent_url("agent-a")) as first, client.websocket_connect(
        agent_url("agent-b")
    ) as second:
        say_hello(first)
        say_hello(second)

        task = create_task(client, agentId="agent-b")
        assignment = second.receive_json()
        assert assignment["type"] == "task_assign"
        assert assignment["task"]["id"] == task["id"]
        assert assignment["task"]["agentId"] == "agent-b"

        detail = client.get(f"/mobile/api/tasks/{task['id']}", headers=AUTH).json()["task"]
        assert detail["status"] == "running"

        # an unknown target is rejected instead of silently running elsewhere
        response = client.post(
            "/mobile/api/tasks",
            json={"instruction": "x", "agentId": "nope"},
            headers=AUTH,
        )
        assert response.status_code == 404


def test_required_capabilities_route_to_a_capable_agent(client: TestClient) -> None:
    with client.websocket_connect(agent_url("plain")) as plain, client.websocket_connect(
        agent_url("turbo")
    ) as turbo:
        say_hello(plain, capabilities=["git"])
        say_hello(turbo, capabilities=["git", "pull-request"])

        task = create_task(client, requiredCapabilities=["pull-request"])
        assignment = turbo.receive_json()
        assert assignment["type"] == "task_assign"
        assert assignment["task"]["id"] == task["id"]


def test_task_waits_in_queue_when_no_agent_has_the_capability(client: TestClient) -> None:
    with client.websocket_connect(agent_url("plain")) as plain:
        say_hello(plain, capabilities=["git"])
        task = create_task(client, requiredCapabilities=["gpu"])

        detail = client.get(f"/mobile/api/tasks/{task['id']}", headers=AUTH).json()["task"]
        assert detail["status"] == "queued"
        assert detail["events"][-1]["details"]["requiredCapabilities"] == ["gpu"]


def test_old_agents_can_be_forgotten_once_disconnected(client: TestClient) -> None:
    with client.websocket_connect(agent_url("agent-old")) as ws:
        say_hello(ws)
        assert client.delete("/mobile/api/agents/agent-old", headers=AUTH).status_code == 409

    assert client.delete("/mobile/api/agents/agent-old", headers=AUTH).status_code == 200
    assert client.get("/mobile/api/agents", headers=AUTH).json()["agents"] == []
    assert client.delete("/mobile/api/agents/agent-old", headers=AUTH).status_code == 404


def test_gateway_restart_reconciles_agents_and_running_tasks(tmp_path: Path) -> None:
    """A restart must not leave a ghost 'online' agent or a stuck 'running' task."""
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as first_run:
        with first_run.websocket_connect(agent_url("agent-a")) as ws:
            say_hello(ws)
            task = create_task(first_run)
            assert ws.receive_json()["type"] == "task_assign"
            running = first_run.get(f"/mobile/api/tasks/{task['id']}", headers=AUTH).json()["task"]
            assert running["status"] == "running"

    with TestClient(create_app(make_settings(tmp_path))) as second_run:
        recovered = second_run.get(f"/mobile/api/tasks/{task['id']}", headers=AUTH).json()["task"]
        assert recovered["status"] == "queued"
        assert recovered["interruptedAt"] is not None
        assert recovered["agentId"] == "agent-a"
        assert any("Gateway restarted" in event["message"] for event in recovered["events"])

        fleet = second_run.get("/mobile/api/agents", headers=AUTH).json()
        assert fleet["agents"][0]["status"] == "offline"
        assert fleet["onlineAgents"] == 0

        # ...and the task is picked up again as soon as an agent reconnects
        with second_run.websocket_connect(agent_url("agent-a")) as ws:
            welcome = say_hello(ws)
            assert task["id"] in welcome["resumedTasks"]
            resume = ws.receive_json()
            assert resume["type"] == "task_resume"
            assert resume["action"] == "resume"
