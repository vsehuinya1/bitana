#!/usr/bin/env bash
# V10 scrap-and-rebuild entry research on VPS (ws_merged liq, ~45-60 min).
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/bitana}"
SESSION="${SESSION:-v10research}"

cd "$REPO_DIR"
export PYTHONUNBUFFERED=1
export CAPTURE_ALL=1
export LIQ_SOURCE=ws_merged

tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" "bash -lc '
  cd $REPO_DIR
  export CAPTURE_ALL=1 LIQ_SOURCE=ws_merged
  python3 backtest_output/v10_entry_backtest.py 2>&1 | tee backtest_output/v10_research_results.txt
'"

echo "v10 research started tmux:$SESSION"
echo "READ:  $REPO_DIR/backtest_output/v10_research_results.txt"
echo "CSV:   $REPO_DIR/backtest_output/v10_research_summary.csv"
echo "DONE:  $REPO_DIR/backtest_output/v10_research.done"
