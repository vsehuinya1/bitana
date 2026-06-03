#!/usr/bin/env bash
# Sync v6.4.1-hotfix from GitHub, reset paper equity, restart bitana-v5-paper.
set -euo pipefail

VPS_HOST="${VPS_HOST:-root@161.97.185.65}"
REPO_DIR="${REPO_DIR:-/root/bitana}"
BRANCH="${BRANCH:-v6.4.1-hotfix}"
SERVICE="${SERVICE:-bitana-v5-paper}"
DB="${REPO_DIR}/storage/v5_forward_test.db"

ssh "$VPS_HOST" bash -s <<EOF
set -euo pipefail
cd "$REPO_DIR"
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"
git log -1 --oneline

if [[ -f "$DB" ]]; then
  cp "$DB" "\${DB}.pre_sync_\$(date -u +%Y%m%dT%H%M%SZ)"
  sqlite3 "$DB" "UPDATE state SET value='10000' WHERE key IN ('equity','peak_equity');"
  sqlite3 "$DB" "SELECT key, value FROM state WHERE key IN ('equity','peak_equity');"
fi

systemctl stop "$SERVICE" || true
sleep 2
systemctl start "$SERVICE"
sleep 3
systemctl is-active "$SERVICE"
journalctl -u "$SERVICE" -n 20 --no-pager
EOF

echo "Done: $VPS_HOST $SERVICE @ \$(git -C . rev-parse --short HEAD 2>/dev/null || echo synced)"
