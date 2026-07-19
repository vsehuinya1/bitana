#!/usr/bin/env bash
# Deploy live mainnet burst-follow (NY + Asia). Does not stop shadow paper services.
set -euo pipefail

VPS_HOST="${VPS_HOST:-root@161.97.185.65}"
REPO_DIR="${REPO_DIR:-/root/bitana}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE="${SERVICE:-bitana-live-burst-follow}"

FILES=(
  main.py
  data/candle_manager.py
  data/force_order_pipeline.py
  engines/liq_burst_follow_engine.py
  engines/liq_cluster_engine_v5.py
  engines/__init__.py
  risk/portfolio_manager.py
  config/loader.py
  config/live_burst_ny_asia.yaml
  core/models.py
  execution/position_manager.py
  storage/database.py
  tools/transfer_spot_to_futures.py
  deploy/bitana-live-burst-follow.service
  deploy/vps_live_burst_follow.sh
)

echo "Uploading live burst-follow to ${VPS_HOST}:${REPO_DIR} ..."
for f in "${FILES[@]}"; do
  scp "$ROOT/$f" "${VPS_HOST}:${REPO_DIR}/$f"
done

echo "Installing systemd unit and starting ${SERVICE} ..."
ssh "$VPS_HOST" bash -s <<EOF
set -euo pipefail
cp ${REPO_DIR}/deploy/bitana-live-burst-follow.service /etc/systemd/system/${SERVICE}.service
systemctl daemon-reload
systemctl enable ${SERVICE} || true
systemctl restart ${SERVICE}
sleep 4
systemctl is-active ${SERVICE}
journalctl -u ${SERVICE} -n 25 --no-pager || tail -25 /var/log/bitana-live-burst.log
EOF

echo ""
echo "Live burst-follow on ${VPS_HOST}."
echo "  ssh ${VPS_HOST} journalctl -u ${SERVICE} -f"
echo "  Requires .env: BINANCE_TESTNET=false, futures + transfer API perms"
