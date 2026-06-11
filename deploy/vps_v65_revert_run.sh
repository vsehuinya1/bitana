#!/usr/bin/env bash
# v6.5-revert backtest on VPS (tmux-detached).
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/bitana}"
SESSION="${SESSION:-v65revert}"
LOG="${REPO_DIR}/backtest_output/v65_revert.log"

cd "$REPO_DIR"
export PYTHONUNBUFFERED=1
export RESEARCH_V5_EXITS=1

tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" "bash -lc '
  cd $REPO_DIR
  RESEARCH_V5_EXITS=1 python3 backtest_output/v65_revert_backtest.py 2>&1 | tee $LOG
'"

echo "v6.5-revert started in tmux:$SESSION"
echo "  tail -f $LOG"
echo "  cat backtest_output/v65_revert_results.txt"
echo "  cat backtest_output/v65_revert_summary.csv"
