"""v65 entry/exits with INTRADAY rolling-24h cascade context — no same-day lookahead.

Cascade context at each hour = completed coinalyze days + rolling-24h hourly sum.
This is exactly computable live from WS force-order accumulation, so backtest == live
by construction.

Runs two configs on proven 28:
  1. NY 14-22 UTC (deployed window)
  2. all-hours capture (where does the intraday edge live?)

Usage:
  python backtest_output/v65_intraday_backtest.py
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
os.environ["LIQ_INTRADAY"] = "1"

import engines.liq_cluster_engine_v5 as eng  # noqa: E402
from backtest_output.v65_revert_config import V5_SYMBOLS, apply_v65_revert  # noqa: E402
import backtest_output.v6_path_backtest as bt  # noqa: E402

MIN_TEST_N = 50
PASS_TEST_AVG = 0.0
PASS_FULL_AVG = -0.05


def score(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "full_avg": 0.0, "full_R": 0.0, "wr": 0.0,
                "test_n": 0, "test_avg": 0.0, "test_R": 0.0, "test_wr": 0.0,
                "pass": False, "verdict": "KILL"}
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


def breakdown(trades: list[dict], lines: list[str], by_hour: bool = False) -> None:
    td = pd.DataFrame(trades)
    if by_hour:
        lines.append("\n--- by hour (UTC) ---")
        for h in sorted(td.hour.unique()):
            m = td[td.hour == h]
            lines.append(f"  {int(h):02d}: n={len(m)} avg={m.pnl_r.mean():+.3f}R "
                         f"tot={m.pnl_r.sum():+.2f}R")
    lines.append("\n--- by decile ---")
    for d in sorted(td.decile.dropna().unique()):
        m = td[td.decile == d]
        lines.append(f"  D{int(d)}: n={len(m)} avg={m.pnl_r.mean():+.3f}R "
                     f"tot={m.pnl_r.sum():+.2f}R wr={(m.pnl_r > 0).mean():.0%}")
    lines.append("\n--- by symbol (top/bottom) ---")
    sym: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        sym[t["symbol"]].append(t["pnl_r"])
    ranked = sorted(sym.items(), key=lambda x: sum(x[1]), reverse=True)
    for s, rs in ranked[:8] + ([("...", [])] if len(ranked) > 16 else []) + ranked[-8:]:
        if s == "...":
            lines.append("  ...")
        else:
            lines.append(f"  {s}: n={len(rs)} tot={sum(rs):+.2f}R avg={sum(rs)/len(rs):+.3f}R")


def run(label: str, hours: frozenset, out_tag: str, by_hour: bool) -> tuple[dict, list[str]]:
    apply_v65_revert()
    eng.SNIPER_ALLOWED_HOURS = hours
    bt.LIQ_SOURCE = "coinalyze"
    os.environ["OUT_TAG"] = out_tag
    os.environ["SYMBOL_OVERRIDE"] = ",".join(
        s for s in V5_SYMBOLS if s in bt.klines_symbols())

    print(f"\n===== {label} =====", flush=True)
    trades, _ = bt.run_capture()
    s = score(trades)
    lines = [
        f"\n===== {label} =====",
        f"{s['verdict']} | n={s['n']} full={s['full_avg']:+.3f}R ({s['full_R']:+.1f}R) "
        f"wr={s['wr']:.0%}",
        f"OOS test_n={s['test_n']} test={s['test_avg']:+.3f}R ({s['test_R']:+.1f}R) "
        f"test_wr={s['test_wr']:.0%}",
    ]
    if trades:
        breakdown(trades, lines, by_hour=by_hour)
    return s, lines


def main() -> None:
    all_lines = [
        f"=== v65 INTRADAY rolling-24h cascade {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC ===",
        "context: completed days + rolling-24h hourly liq (live-computable, no lookahead)",
        "entry: V5 strict | exits: V5 vol_trail | symbols: proven 28",
    ]
    summaries = []

    s, lines = run("NY 14-22 UTC", frozenset(range(14, 22)), "_intraday_ny", by_hour=False)
    summaries.append({"variant": "intraday_ny", **s})
    all_lines += lines

    s, lines = run("ALL HOURS", frozenset(range(24)), "_intraday_all", by_hour=True)
    summaries.append({"variant": "intraday_all", **s})
    all_lines += lines

    text = "\n".join(all_lines)
    (OUT / "v65_intraday_results.txt").write_text(text + "\n")
    pd.DataFrame(summaries).to_csv(OUT / "v65_intraday_summary.csv", index=False)
    print(text, flush=True)
    (OUT / "v65_intraday.done").write_text("DONE\n")


if __name__ == "__main__":
    main()
