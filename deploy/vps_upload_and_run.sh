#!/usr/bin/env bash
# Upload v7 research files to VPS and start detached run. Run once from your Mac Terminal.
set -euo pipefail

VPS_HOST="${VPS_HOST:-root@161.97.185.65}"
REPO_DIR="${REPO_DIR:-/root/bitana}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

FILES=(
  backtest_output/bootstrap_backtest_data.py
  backtest_output/v6_path_backtest.py
  backtest_output/v7_entry_theses.py
  backtest_output/v7_entry_backtest.py
  deploy/vps_v7_research_run.sh
)

echo "Uploading to ${VPS_HOST}:${REPO_DIR} ..."
for f in "${FILES[@]}"; do
  scp "$ROOT/$f" "${VPS_HOST}:${REPO_DIR}/$f"
done

echo "Starting detached research on VPS ..."
ssh "$VPS_HOST" "bash ${REPO_DIR}/deploy/vps_v7_research_run.sh"

echo ""
echo "Safe to close laptop. When back, on VPS run:"
echo "  tail -f ${REPO_DIR}/backtest_output/v7_research.log"
echo "  cat ${REPO_DIR}/backtest_output/v7_exhaustion_results.txt"
