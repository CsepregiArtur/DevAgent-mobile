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

# ``--update`` protects against the classic accident of overwriting a *newer*
# deployment with an older checkout (rsync preserves mtimes, so newer files win).
RSYNC=(rsync -a --delete --update --exclude '__pycache__' --exclude '.venv' --exclude '*.pyc' --exclude 'agent/.venv' $DRY)

warn_if_destination_newer() {
    local src="$1" dst="$2"
    [[ -d "$dst" ]] || return 0
    local newer
    newer=$(find "$dst" -newer "$src" -type f -not -path '*/.venv/*' -not -path '*__pycache__*' 2>/dev/null | head -3)
    [[ -z "$newer" ]] || warn_msg="destination has newer files than the repo:
$newer
  (they will NOT be overwritten - sync was aborted for safety)"
}

warn_msg=""
warn_if_destination_newer "$REPO_ROOT/gateway/app" "$TARGET/gateway/app"
warn_if_destination_newer "$REPO_ROOT/agent" "$TARGET/agent"
if [[ -n "$warn_msg" && -z "$DRY" ]]; then
    echo "[ATENTIE] $warn_msg" >&2
    exit 3
fi

echo "== gateway"
"${RSYNC[@]}" "$REPO_ROOT/gateway/app/" "$TARGET/gateway/app/"
"${RSYNC[@]}" "$REPO_ROOT/gateway/tests/" "$TARGET/gateway/tests/"
cp "$REPO_ROOT/gateway/requirements.txt" "$REPO_ROOT/gateway/env.example" "$TARGET/gateway/"
mkdir -p "$TARGET/tests"

echo "== agent"
"${RSYNC[@]}" "$REPO_ROOT/agent/" "$TARGET/agent/"

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
