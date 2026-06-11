#!/usr/bin/env bash
# Finish v7 research locally in nohup (backup if VPS unavailable).
# Note: closing laptop may still kill this unless Mac stays awake.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG="${ROOT}/backtest_output/v7_local_research.log"

nohup bash -c "
  python3 backtest_output/backfill_klines_5m.py
  python3 backtest_output/bootstrap_backtest_data.py
  CAPTURE_ALL=1 ENTRY_THESIS=v645 python3 backtest_output/v7_entry_backtest.py | tee backtest_output/v7_v645_results.txt
  CAPTURE_ALL=1 ENTRY_THESIS=exhaustion python3 backtest_output/v7_entry_backtest.py | tee backtest_output/v7_exhaustion_results.txt
  echo DONE > backtest_output/v7_research.done
" >> "$LOG" 2>&1 &

echo "Local backup started PID $! — log: $LOG"
