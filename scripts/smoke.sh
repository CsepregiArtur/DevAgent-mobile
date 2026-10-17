#!/usr/bin/env bash
# End-to-end smoke test: real gateway + real reference agent + real approvals.
#
# Usage:  devagent/tests/smoke.sh [http://127.0.0.1:8082/mobile] [ws://127.0.0.1:8082/mobile/agent/ws]
#
# It creates a throw-away git repo, starts the macOS agent against it, submits a
# task, waits for the approval gate, approves the proposal and asserts that the
# task completes and that the change landed on the agent branch.
set -euo pipefail

BASE=${1:-http://127.0.0.1:8082/mobile}
WS=${2:-ws://127.0.0.1:8082/mobile/agent/ws}
ENV_FILE=${DEVAGENT_ENV_FILE:-/etc/deepseek-devagent.env}

read_env() { grep -m1 "^$1=" "$ENV_FILE" | cut -d'"' -f2; }
TOKEN=$(read_env DEVAGENT_TOKEN)
AGENT_TOKEN=$(read_env DEVAGENT_AGENT_TOKEN); AGENT_TOKEN=${AGENT_TOKEN:-$TOKEN}
# Interpreter that has the ``websockets`` package installed (defaults to python3).
AGENT_PYTHON=${DEVAGENT_AGENT_PYTHON:-python3}
# Path to the reference agent (defaults to the server deployment).
AGENT_SCRIPT=${DEVAGENT_AGENT_SCRIPT:-/opt/deepseek-proxy/devagent/agent/devagent_agent.py}
[[ -f "$AGENT_SCRIPT" ]] || { echo "agent script not found: $AGENT_SCRIPT" >&2; exit 2; }

WORKDIR=$(mktemp -d /tmp/devagent-smoke-XXXXXX)
REPO="$WORKDIR/repo"
mkdir -p "$REPO" "$WORKDIR/state"
git -C "$REPO" init -q
git -C "$REPO" config user.email "agent@devagent.local"
git -C "$REPO" config user.name "DevAgent Smoke"
printf '# demo repo\n' > "$REPO/README.md"
printf "console.log('hello');\n" > "$REPO/app.js"
git -C "$REPO" add -A && git -C "$REPO" commit -qm "initial commit"

cleanup() {
    [[ -n "${AGENT_PID:-}" ]] && kill "$AGENT_PID" 2>/dev/null || true
    echo "workdir: $WORKDIR"
}
trap cleanup EXIT

echo "== 1. gateway reachable"
curl -sf "$BASE/../healthz" >/dev/null 2>&1 || curl -sf "${BASE%/mobile}/healthz" >/dev/null

echo "== 2. start reference agent (ws://.../agent/ws)"
DEVAGENT_GATEWAY_URL="$WS" \
DEVAGENT_AGENT_TOKEN="$AGENT_TOKEN" \
DEVAGENT_AGENT_ID="smoke-agent" \
DEVAGENT_WORKSPACES="$WORKDIR" \
DEVAGENT_STATE_DIR="$WORKDIR/state" \
DEVAGENT_LLM_BASE_URL="" \
DEVAGENT_APPROVE_COMMANDS="false" \
DEVAGENT_HEARTBEAT_SECONDS="2" \
DEVAGENT_IGNORE_PROVIDER="true" \
DEVAGENT_AGENT_OFFLINE_SECONDS="10" \
"$AGENT_PYTHON" "$AGENT_SCRIPT" --env-file /dev/null \
    > "$WORKDIR/agent.log" 2>&1 &
AGENT_PID=$!
sleep 3
grep -q "enrolled as smoke-agent" "$WORKDIR/agent.log" || { echo "agent did not enrol"; tail -20 "$WORKDIR/agent.log"; exit 1; }

echo "== 3. submit a task (agent online -> running)"
TASK_JSON=$(curl -sf -X POST "$BASE/api/tasks?token=$TOKEN" \
    -H 'Content-Type: application/json' \
    -d "{\"instruction\":\"Smoke test: document the demo repo\",\"workspace\":\"$REPO\",\"mode\":\"supervised\"}")
TASK_ID=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['task']['id'])" "$TASK_JSON")
echo "   task id: $TASK_ID"

echo "== 4. wait for the approval gate"
for _ in $(seq 1 60); do
    STATUS=$(curl -sf "$BASE/api/tasks/$TASK_ID?token=$TOKEN" | python3 -c "import json,sys;print(json.load(sys.stdin)['task']['status'])")
    [[ "$STATUS" == "awaiting_approval" || "$STATUS" == "completed" || "$STATUS" == "failed" ]] && break
    sleep 1
done
echo "   status: $STATUS"
[[ "$STATUS" == "awaiting_approval" ]] || { echo "unexpected status"; exit 1; }

HASH=$(curl -sf "$BASE/api/tasks/$TASK_ID?token=$TOKEN" | python3 -c "import json,sys;print(json.load(sys.stdin)['task']['approval']['hash'])")
echo "   proposal hash: $HASH"

echo "== 5. approve the proposal"
curl -sf -X POST "$BASE/api/tasks/$TASK_ID/approval?token=$TOKEN" \
    -H 'Content-Type: application/json' \
    -d "{\"action\":\"approve\",\"hash\":\"$HASH\",\"comment\":\"smoke test\"}" >/dev/null

echo "== 6. wait for completion"
for _ in $(seq 1 60); do
    STATUS=$(curl -sf "$BASE/api/tasks/$TASK_ID?token=$TOKEN" | python3 -c "import json,sys;print(json.load(sys.stdin)['task']['status'])")
    [[ "$STATUS" == "completed" || "$STATUS" == "failed" ]] && break
    sleep 1
done
echo "   final status: $STATUS"
[[ "$STATUS" == "completed" ]] || { echo "task did not complete"; tail -30 "$WORKDIR/agent.log"; exit 1; }

echo "== 7. verify the change landed on the agent branch"
BRANCH=$(curl -sf "$BASE/api/tasks/$TASK_ID?token=$TOKEN" | python3 -c "import json,sys;print(json.load(sys.stdin)['task']['result']['branchName'])")
git -C "$REPO" branch --list "$BRANCH" | grep -q . || { echo "branch $BRANCH missing"; exit 1; }
COMMITS=$(git -C "$REPO" rev-list --count "$BRANCH")
[[ "$COMMITS" -ge 2 ]] || { echo "no agent commit on $BRANCH (commits=$COMMITS)"; exit 1; }
git -C "$REPO" --no-pager log --oneline -1 "$BRANCH"
git -C "$REPO" --no-pager show --stat --oneline "$BRANCH" | tail -3
git -C "$REPO" show --name-only --pretty=format: "$BRANCH" | grep -q DEVAGENT_PLAN.md || { echo "expected file missing on branch"; exit 1; }

echo "== 8. verify reconnection resume path (agent restarts mid-approval)"
TASK2=$(curl -sf -X POST "$BASE/api/tasks?token=$TOKEN" -H 'Content-Type: application/json' \
    -d "{\"instruction\":\"Smoke test: second change\",\"workspace\":\"$REPO\",\"mode\":\"supervised\"}" \
    | python3 -c "import json,sys;print(json.load(sys.stdin)['task']['id'])")
for _ in $(seq 1 60); do
    S=$(curl -sf "$BASE/api/tasks/$TASK2?token=$TOKEN" | python3 -c "import json,sys;print(json.load(sys.stdin)['task']['status'])")
    [[ "$S" == "awaiting_approval" ]] && break
    sleep 1
done
kill "$AGENT_PID"; wait "$AGENT_PID" 2>/dev/null || true
sleep 2
HASH2=$(curl -sf "$BASE/api/tasks/$TASK2?token=$TOKEN" | python3 -c "import json,sys;print(json.load(sys.stdin)['task']['approval']['hash'])")
curl -sf -X POST "$BASE/api/tasks/$TASK2/approval?token=$TOKEN" -H 'Content-Type: application/json' \
    -d "{\"action\":\"approve\",\"hash\":\"$HASH2\",\"comment\":\"approved while offline\"}" >/dev/null
DEVAGENT_GATEWAY_URL="$WS" DEVAGENT_AGENT_TOKEN="$AGENT_TOKEN" DEVAGENT_AGENT_ID="smoke-agent" \
DEVAGENT_WORKSPACES="$WORKDIR" DEVAGENT_STATE_DIR="$WORKDIR/state" DEVAGENT_LLM_BASE_URL="" \
DEVAGENT_APPROVE_COMMANDS="false" DEVAGENT_HEARTBEAT_SECONDS="2" DEVAGENT_IGNORE_PROVIDER="true" \
"$AGENT_PYTHON" "$AGENT_SCRIPT" --env-file /dev/null \
    >> "$WORKDIR/agent.log" 2>&1 &
AGENT_PID=$!
for _ in $(seq 1 60); do
    S=$(curl -sf "$BASE/api/tasks/$TASK2?token=$TOKEN" | python3 -c "import json,sys;print(json.load(sys.stdin)['task']['status'])")
    [[ "$S" == "completed" || "$S" == "failed" ]] && break
    sleep 1
done
echo "   resumed task status: $S"
[[ "$S" == "completed" ]] || { echo "resume after reconnect failed"; tail -30 "$WORKDIR/agent.log"; exit 1; }

echo
echo "✅ smoke test passed: queue -> approval -> apply -> complete, plus resume after reconnect"
