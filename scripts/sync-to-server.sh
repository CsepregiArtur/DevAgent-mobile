#!/usr/bin/env bash
# Copiază codul din acest repo în locația de deployment și (opțional) repornește
# serviciul. Repo-ul este sursa de referință pentru cod; serverul rulează din
# /opt/deepseek-proxy/devagent/...
#
# Usage:
#   scripts/sync-to-server.sh                # copiază + verifică sintaxa
#   scripts/sync-to-server.sh --restart      # copiază + restart serviciu
#   scripts/sync-to-server.sh --dry-run      # arată ce s-ar schimba
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${DEVAGENT_DEPLOY_DIR:-/opt/deepseek-proxy/devagent}"
RESTART=0
DRY=""
for arg in "$@"; do
    case "$arg" in
        --restart) RESTART=1 ;;
        --dry-run) DRY="--dry-run" ;;
        *) echo "argument necunoscut: $arg" >&2; exit 2 ;;
    esac
done

[[ "$(id -u)" -eq 0 ]] || { echo "rulează ca root (scrie în $TARGET și /etc/systemd/system)" >&2; exit 1; }
command -v rsync >/dev/null || { echo "instalează rsync" >&2; exit 1; }

RSYNC=(rsync -a --delete --exclude '__pycache__' --exclude '.venv' --exclude '*.pyc' --exclude 'agent/.venv' $DRY)

echo "== gateway"
"${RSYNC[@]}" "$REPO_ROOT/gateway/app/" "$TARGET/gateway/app/"
"${RSYNC[@]}" "$REPO_ROOT/gateway/tests/" "$TARGET/gateway/tests/"
cp "$REPO_ROOT/gateway/requirements.txt" "$REPO_ROOT/gateway/env.example" "$TARGET/gateway/"
mkdir -p "$TARGET/tests"

echo "== agent"
"${RSYNC[@]}" "$REPO_ROOT/agent/" "$TARGET/agent/"
rm -rf "$TARGET/agent/.venv" 2>/dev/null || true

echo "== smoke test + unit systemd"
cp "$REPO_ROOT/scripts/smoke.sh" "$TARGET/tests/smoke.sh"
chmod +x "$TARGET/tests/smoke.sh"
if [[ -z "$DRY" ]]; then
    install -m 644 "$REPO_ROOT/systemd/devagent-gateway.service" /etc/systemd/system/
    systemctl daemon-reload
fi

echo "== verificare sintaxă python"
if [[ -z "$DRY" ]]; then
    "$TARGET/gateway/.venv/bin/python" -m compileall -q "$TARGET/gateway/app" && echo "ok"
fi

if [[ "$RESTART" == "1" && -z "$DRY" ]]; then
    systemctl restart devagent-gateway
    sleep 2
    systemctl is-active devagent-gateway
    curl -s http://127.0.0.1:8082/healthz; echo
fi

echo "gata."
