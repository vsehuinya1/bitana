#!/usr/bin/env bash
# v65 follow-up: Asia-28 + MAE/stop study on VPS (tmux, no paper bot impact).
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/bitana}"
SESSION="${SESSION:-v65followup}"
LOG="${REPO_DIR}/backtest_output/v65_followup.log"

cd "$REPO_DIR"
export PYTHONUNBUFFERED=1
export LEGACY_RUNNER_EXITS=0

tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" "bash -lc '
  cd $REPO_DIR
  LEGACY_RUNNER_EXITS=0 python3 backtest_output/v65_followup_research.py 2>&1 | tee $LOG
'"

echo "v65 follow-up started in tmux:$SESSION"
echo "  tail -f $LOG"
echo "  cat backtest_output/v65_followup_results.txt"
echo "  cat backtest_output/v65_asia28_results.txt"
echo "  cat backtest_output/v65_mae_stop_results.txt"
