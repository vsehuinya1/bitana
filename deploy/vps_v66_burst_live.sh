#!/usr/bin/env bash
# Deploy v66 burst continuation paper bot (parallel to v65-revert).
set -euo pipefail

VPS_HOST="${VPS_HOST:-root@161.97.185.65}"
REPO_DIR="${REPO_DIR:-/root/bitana}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE="${SERVICE:-bitana-v66-burst}"

FILES=(
  engines/liq_burst_engine.py
  config/v66_burst_forward_test.yaml
  tools/v66_burst_forward_test.py
  backtest_output/v66_burst_config.py
  backtest_output/v66_burst_backtest.py
  deploy/bitana-v66-burst.service
)

echo "Uploading v66 burst to ${VPS_HOST}:${REPO_DIR} ..."
for f in "${FILES[@]}"; do
  scp "$ROOT/$f" "${VPS_HOST}:${REPO_DIR}/$f"
done

echo "Installing systemd unit and starting ${SERVICE} ..."
ssh "$VPS_HOST" bash -s <<EOF
set -euo pipefail
cp ${REPO_DIR}/deploy/bitana-v66-burst.service /etc/systemd/system/${SERVICE}.service
systemctl daemon-reload
systemctl enable ${SERVICE} || true
systemctl restart ${SERVICE}
sleep 4
systemctl is-active ${SERVICE}
journalctl -u ${SERVICE} -n 15 --no-pager || tail -15 /var/log/bitana-v66.log
EOF

echo ""
echo "v66 burst paper on ${VPS_HOST}. v65-revert unchanged."
echo "  ssh ${VPS_HOST} tail -f /var/log/bitana-v66.log"
