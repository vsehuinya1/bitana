#!/usr/bin/env bash
# v65 batch research (items 1–3) on VPS — tmux-detached, does not touch paper bot.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/bitana}"
SESSION="${SESSION:-v65batch}"
LOG="${REPO_DIR}/backtest_output/v65_batch.log"

cd "$REPO_DIR"
export PYTHONUNBUFFERED=1
export LEGACY_RUNNER_EXITS=0

tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" "bash -lc '
  cd $REPO_DIR
  LEGACY_RUNNER_EXITS=0 python3 backtest_output/v65_batch_research.py 2>&1 | tee $LOG
'"

echo "v65 batch research started in tmux:$SESSION"
echo "  tail -f $LOG"
echo "  cat backtest_output/v65_batch_results.txt"
