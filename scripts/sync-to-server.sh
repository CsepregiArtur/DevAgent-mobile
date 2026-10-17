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

# Warn (never abort) about files that exist in the deployment and are newer than
# the checkout: with --update rsync leaves them untouched, but the operator should
# know that the deployment contains local changes.
warn_if_destination_newer() {
    local src="$1" dst="$2"
    [[ -d "$dst" ]] || return 0
    python3 - "$src" "$dst" <<'PY'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
skipped = []
for root, dirs, files in os.walk(src):
    dirs[:] = [d for d in dirs if d not in {'.venv', '__pycache__'}]
    for name in files:
        if name.endswith('.pyc'):
            continue
        s = os.path.join(root, name)
        rel = os.path.relpath(s, src)
        d = os.path.join(dst, rel)
        if os.path.exists(d):
            try:
                if os.path.getmtime(d) > os.path.getmtime(s) + 1:
                    skipped.append(rel)
            except OSError:
                pass
if skipped:
    print('[ATENTIE] fisiere mai noi in deployment (nu se suprascriu):')
    for rel in skipped[:8]:
        print('   ', rel)
    print('    -> daca vrei sa le suprascrii: rsync fara --update, sau sterge-le intai')
PY
}

warn_if_destination_newer "$REPO_ROOT/gateway/app" "$TARGET/gateway/app"
warn_if_destination_newer "$REPO_ROOT/gateway/tests" "$TARGET/gateway/tests"
warn_if_destination_newer "$REPO_ROOT/agent" "$TARGET/agent"

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
