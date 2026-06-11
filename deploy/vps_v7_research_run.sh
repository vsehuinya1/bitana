#!/usr/bin/env bash
# Run v7 entry research on VPS inside tmux (safe to disconnect / close laptop).
#
# On VPS (or via):  bash deploy/vps_v7_research_run.sh
# From Mac (once):  bash deploy/vps_upload_and_run.sh
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/bitana}"
SESSION="${SESSION:-v7research}"
LOG_DIR="${REPO_DIR}/backtest_output"
RUN_SH="${LOG_DIR}/v7_research_runner.sh"

mkdir -p "$LOG_DIR"

cat > "$RUN_SH" <<'INNER'
#!/usr/bin/env bash
set -euo pipefail
cd /root/bitana
export PYTHONUNBUFFERED=1

echo "=== $(date -u) v7 research start ===" | tee backtest_output/v7_research.log

python3 backtest_output/backfill_klines_5m.py 2>&1 | tee -a backtest_output/v7_research.log
python3 backtest_output/bootstrap_backtest_data.py 2>&1 | tee -a backtest_output/v7_research.log

CAPTURE_ALL=1 ENTRY_THESIS=v645 python3 backtest_output/v7_entry_backtest.py \
  2>&1 | tee backtest_output/v7_v645_results.txt

CAPTURE_ALL=1 ENTRY_THESIS=exhaustion python3 backtest_output/v7_entry_backtest.py \
  2>&1 | tee backtest_output/v7_exhaustion_results.txt

CAPTURE_ALL=1 ENTRY_THESIS=squeeze python3 backtest_output/v7_entry_backtest.py \
  2>&1 | tee backtest_output/v7_squeeze_results.txt

echo "=== $(date -u) DONE ===" | tee backtest_output/v7_research.done
INNER
chmod +x "$RUN_SH"

if command -v tmux >/dev/null 2>&1; then
  tmux kill-session -t "$SESSION" 2>/dev/null || true
  tmux new-session -d -s "$SESSION" "bash $RUN_SH"
  echo "Started tmux session: $SESSION"
  echo "  attach: tmux attach -t $SESSION"
  echo "  log:    tail -f ${LOG_DIR}/v7_research.log"
  echo "  done:   cat ${LOG_DIR}/v7_research.done"
else
  echo "tmux not found — running via nohup"
  nohup bash "$RUN_SH" > "${LOG_DIR}/v7_research.nohup.log" 2>&1 &
  echo "PID $! — tail ${LOG_DIR}/v7_research.nohup.log"
fi
