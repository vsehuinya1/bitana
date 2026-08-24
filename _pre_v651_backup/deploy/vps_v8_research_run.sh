#!/usr/bin/env bash
# V8 entry research battery on VPS (tmux-detached).
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/bitana}"
SESSION="${SESSION:-v8research}"
LOG="${REPO_DIR}/backtest_output/v8_research.log"

cd "$REPO_DIR"
export PYTHONUNBUFFERED=1
export CAPTURE_ALL=1

tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" "bash -lc '
  cd $REPO_DIR
  CAPTURE_ALL=1 python3 backtest_output/v8_entry_backtest.py 2>&1 | tee $LOG
  echo DONE > backtest_output/v8_research.done
'"

echo "v8 research started in tmux:$SESSION"
echo "  tail -f $LOG"
echo "  cat backtest_output/v8_research_summary.csv"
