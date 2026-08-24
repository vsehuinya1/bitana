"""v65 follow-up: Asia-28 backtest + MAE/stop study. Runs sequentially on VPS.

Usage:
  LEGACY_RUNNER_EXITS=0 python backtest_output/v65_followup_research.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "backtest_output"

os.environ.setdefault("LEGACY_RUNNER_EXITS", "0")


def main() -> None:
    py = sys.executable
    lines = [f"=== v65 FOLLOW-UP {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC ===", ""]

    print("Running Asia-28 backtest...", flush=True)
    r1 = subprocess.run([py, str(REPO / "backtest_output" / "v65_asia28_backtest.py")], cwd=str(REPO))
    if (OUT / "v65_asia28_results.txt").exists():
        lines.append((OUT / "v65_asia28_results.txt").read_text().strip())

    lines.append("\n" + "=" * 60 + "\n")

    print("\nRunning MAE/stop study...", flush=True)
    rpath = OUT / "v6_bt_rpath_v645_coinalyze_28.csv"
    if not rpath.exists():
        rpath = OUT / "v6_bt_rpath_v645_ws_merged.csv"
    trades = OUT / "v65_revert_trades.csv"
    cmd = [py, str(REPO / "backtest_output" / "v65_mae_stop_study.py"),
           "--trades", str(trades), "--rpath", str(rpath)]
    subprocess.run(cmd, cwd=str(REPO))
    if (OUT / "v65_mae_stop_results.txt").exists():
        lines.append((OUT / "v65_mae_stop_results.txt").read_text().strip())

    text = "\n".join(lines)
    (OUT / "v65_followup_results.txt").write_text(text + "\n")
    (OUT / "v65_followup.done").write_text("done\n")
    print(f"\n{'='*60}\nFOLLOW-UP COMPLETE\n{'='*60}", flush=True)
    print(text, flush=True)
    sys.exit(max(r1.returncode, 0))


if __name__ == "__main__":
    main()
