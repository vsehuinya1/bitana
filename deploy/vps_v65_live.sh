#!/usr/bin/env bash
# Deploy v6.5-revert to VPS paper bot and restart.
set -euo pipefail

VPS_HOST="${VPS_HOST:-root@161.97.185.65}"
REPO_DIR="${REPO_DIR:-/root/bitana}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE="${SERVICE:-bitana-v5-paper}"
DB="${REPO_DIR}/storage/v5_forward_test.db"

RESET_EQUITY="${RESET_EQUITY:-0}"

FILES=(
  engines/liq_cluster_engine_v5.py
  config/v5_forward_test.yaml
  tools/v5_forward_test.py
  research/v65_monitoring.py
  research/v6_telemetry.py
  backtest_output/v6_path_backtest.py
  backtest_output/v65_revert_config.py
  backtest_output/v65_revert_backtest.py
)

echo "Uploading v6.5-revert to ${VPS_HOST}:${REPO_DIR} ..."
for f in "${FILES[@]}"; do
  scp "$ROOT/$f" "${VPS_HOST}:${REPO_DIR}/$f"
done

echo "Restarting ${SERVICE} ..."
ssh "$VPS_HOST" bash -s <<EOF
set -euo pipefail
cd "$REPO_DIR"
DB="$DB"

if [[ "$RESET_EQUITY" == "1" ]]; then
if [[ -f "\$DB" ]]; then
  cp "\$DB" "\${DB}.pre_v65_\$(date -u +%Y%m%dT%H%M%SZ)"
  python3 - <<PY
import sqlite3
from pathlib import Path
db = Path("$DB")
conn = sqlite3.connect(db)
conn.execute("UPDATE state SET value='9861' WHERE key IN ('equity','peak_equity')")
conn.execute("DELETE FROM open_positions")
conn.commit()
for row in conn.execute("SELECT key, value FROM state WHERE key IN ('equity','peak_equity')"):
    print("state", row)
print("open_positions", conn.execute("SELECT COUNT(*) FROM open_positions").fetchone()[0])
conn.close()
PY
fi
fi

systemctl stop "$SERVICE" || true
sleep 2
systemctl start "$SERVICE"
sleep 4
systemctl is-active "$SERVICE"
journalctl -u "$SERVICE" -n 25 --no-pager
EOF

echo ""
echo "v6.5-revert live on ${VPS_HOST}. Monitor:"
echo "  ssh ${VPS_HOST} journalctl -u ${SERVICE} -f"
