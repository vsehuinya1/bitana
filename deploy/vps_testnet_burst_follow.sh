#!/usr/bin/env bash
# Deploy testnet burst-follow bot (parallel to v65 paper). Does not touch v65 service.
set -euo pipefail

VPS_HOST="${VPS_HOST:-root@161.97.185.65}"
REPO_DIR="${REPO_DIR:-/root/bitana}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE="${SERVICE:-bitana-testnet-burst-follow}"

FILES=(
  main.py
  data/candle_manager.py
  data/force_order_pipeline.py
  engines/liq_burst_follow_engine.py
  engines/liq_cluster_engine_v5.py
  engines/__init__.py
  risk/portfolio_manager.py
  config/loader.py
  config/testnet_burst_follow.yaml
  core/models.py
  execution/position_manager.py
  storage/database.py
  deploy/bitana-testnet-burst-follow.service
  deploy/vps_testnet_burst_follow.sh
)

echo "Uploading testnet burst-follow to ${VPS_HOST}:${REPO_DIR} ..."
for f in "${FILES[@]}"; do
  scp "$ROOT/$f" "${VPS_HOST}:${REPO_DIR}/$f"
done

echo "Installing systemd unit and starting ${SERVICE} ..."
ssh "$VPS_HOST" bash -s <<EOF
set -euo pipefail
cp ${REPO_DIR}/deploy/bitana-testnet-burst-follow.service /etc/systemd/system/${SERVICE}.service
systemctl daemon-reload
systemctl enable ${SERVICE} || true
systemctl restart ${SERVICE}
sleep 4
systemctl is-active ${SERVICE}
journalctl -u ${SERVICE} -n 20 --no-pager || tail -20 /var/log/bitana-testnet-burst.log
EOF

echo ""
echo "Testnet burst-follow on ${VPS_HOST}. v65 paper unchanged."
echo "  ssh ${VPS_HOST} journalctl -u ${SERVICE} -f"
echo "  Requires .env: BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_TESTNET=true"
