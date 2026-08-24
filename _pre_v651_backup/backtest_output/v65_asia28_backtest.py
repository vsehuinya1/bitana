"""v65 Asia session on proven 28 symbols + hour breakdown.

Usage:
  LEGACY_RUNNER_EXITS=0 python backtest_output/v65_asia28_backtest.py
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
OUT = REPO / "backtest_output"

os.environ.setdefault("LEGACY_RUNNER_EXITS", "0")
os.environ["CAPTURE_ALL"] = "0"
os.environ["ENTRY_THESIS"] = "v645"
os.environ["OUT_TAG"] = "_asia28"

from backtest_output.v65_revert_config import V5_SYMBOLS, apply_v65_revert, apply_session  # noqa: E402
import backtest_output.v6_path_backtest as bt  # noqa: E402

MIN_TEST_N = 50
PASS_TEST_AVG = 0.0
PASS_FULL_AVG = -0.05


def score(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "full_avg": 0.0, "full_R": 0.0, "wr": 0.0,
                "test_n": 0, "test_avg": 0.0, "test_R": 0.0, "test_wr": 0.0, "pass": False}
    td = pd.DataFrame(trades).sort_values("entry_time")
    split = int(len(td) * 0.6)
    test = td.iloc[split:]
    full_avg = float(td.pnl_r.mean())
    test_avg = float(test.pnl_r.mean()) if len(test) else 0.0
    passed = len(test) >= MIN_TEST_N and test_avg > PASS_TEST_AVG and full_avg > PASS_FULL_AVG
    return {
        "n": len(td), "full_avg": full_avg, "full_R": float(td.pnl_r.sum()),
        "wr": float((td.pnl_r > 0).mean()),
        "test_n": len(test), "test_avg": test_avg,
        "test_R": float(test.pnl_r.sum()) if len(test) else 0.0,
        "test_wr": float((test.pnl_r > 0).mean()) if len(test) else 0.0,
        "pass": passed, "verdict": "KEEP" if passed else "KILL",
    }


def main() -> None:
    avail = bt.klines_symbols()
    syms = [s for s in V5_SYMBOLS if s in avail]

    apply_v65_revert()
    apply_session("asia")
    bt.LIQ_SOURCE = "coinalyze"
    os.environ["SYMBOL_OVERRIDE"] = ",".join(syms)

    lines = [
        f"=== v65 ASIA-28 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC ===",
        "session: Asia 0–8 UTC | symbols: 28 proven | entry/exits: v65-revert",
        "",
    ]
    print("\n".join(lines), flush=True)

    trades, _rpath = bt.run_capture()
    s = score(trades)
    lines.extend([
        f"{s['verdict']} | n={s['n']} full={s['full_avg']:+.3f}R ({s['full_R']:+.1f}R) wr={s['wr']:.0%}",
        f"OOS test_n={s['test_n']} test={s['test_avg']:+.3f}R ({s['test_R']:+.1f}R) test_wr={s['test_wr']:.0%}",
        f"pass={s['pass']} (need test_n>={MIN_TEST_N}, test_avg>{PASS_TEST_AVG}, full_avg>{PASS_FULL_AVG})",
    ])

    if trades:
        td = pd.DataFrame(trades)
        td["hour"] = pd.to_datetime(td.entry_time, utc=True).dt.hour
        pd.DataFrame(trades).to_csv(OUT / "v65_asia28_trades.csv", index=False)

        lines.append("\n--- by hour (Asia 0–8) ---")
        for h in range(0, 9):
            m = td[td.hour == h]
            if len(m):
                lines.append(f"  {h:02d} UTC: n={len(m)} avg={m.pnl_r.mean():+.3f}R tot={m.pnl_r.sum():+.2f}R")

        lines.append("\n--- by symbol ---")
        sym: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            sym[t["symbol"]].append(t["pnl_r"])
        for sym_name, rs in sorted(sym.items(), key=lambda x: sum(x[1]), reverse=True):
            lines.append(f"  {sym_name}: n={len(rs)} tot={sum(rs):+.2f}R avg={sum(rs)/len(rs):+.3f}R")

    text = "\n".join(lines)
    (OUT / "v65_asia28_results.txt").write_text(text + "\n")
    pd.DataFrame([{**s, "variant": "asia28"}]).to_csv(OUT / "v65_asia28_summary.csv", index=False)
    print(text, flush=True)


if __name__ == "__main__":
    main()
