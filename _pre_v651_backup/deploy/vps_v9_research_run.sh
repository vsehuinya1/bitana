#!/usr/bin/env bash
# V9 scale-in research on VPS (fast — replays existing v645 trades + r_path).
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/bitana}"
SESSION="${SESSION:-v9research}"

cd "$REPO_DIR"
tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" "bash -lc '
  cd $REPO_DIR
  python3 backtest_output/v9_scale_in_backtest.py \
    --trades backtest_output/v8_v645_trades.csv \
    --rpath backtest_output/v6_bt_rpath_capture_all.csv \
    2>&1 | tee backtest_output/v9_research_results.txt
  echo DONE > backtest_output/v9_research.done
'"

echo "v9 started tmux:$SESSION"
echo "READ:  $REPO_DIR/backtest_output/v9_research_results.txt"
echo "CSV:   $REPO_DIR/backtest_output/v9_research_summary.csv"
