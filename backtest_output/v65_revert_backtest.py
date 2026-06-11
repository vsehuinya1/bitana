"""v6.5-revert backtest — V5 entry + V5 vol_trail exits on proven 28 symbols.

Usage:
  python backtest_output/v65_revert_backtest.py

Keep bar (60/40 chronological OOS):
  test_n >= 50, test_avg > 0, full_avg > -0.05
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

os.environ.setdefault("LEGACY_RUNNER_EXITS", "0")
os.environ["CAPTURE_ALL"] = "0"
os.environ["ENTRY_THESIS"] = "v645"

from backtest_output.v65_revert_config import V5_SYMBOLS, apply_v65_revert  # noqa: E402

apply_v65_revert()
os.environ["SYMBOL_OVERRIDE"] = ",".join(V5_SYMBOLS)

import backtest_output.v6_path_backtest as bt  # noqa: E402

MIN_TEST_N = 50
PASS_TEST_AVG = 0.0
PASS_FULL_AVG = -0.05


def score(trades: list[dict]) -> dict:
    if not trades:
        return {
            "n": 0, "full_avg": 0.0, "full_R": 0.0, "wr": 0.0,
            "test_n": 0, "test_avg": 0.0, "test_R": 0.0, "test_wr": 0.0,
            "pass": False,
        }

    td = pd.DataFrame(trades).sort_values("entry_time")
    split = int(len(td) * 0.6)
    test = td.iloc[split:]

    full_avg = float(td.pnl_r.mean())
    test_avg = float(test.pnl_r.mean()) if len(test) else 0.0
    passed = len(test) >= MIN_TEST_N and test_avg > PASS_TEST_AVG and full_avg > PASS_FULL_AVG

    return {
        "n": len(td),
        "full_avg": full_avg,
        "full_R": float(td.pnl_r.sum()),
        "wr": float((td.pnl_r > 0).mean()),
        "test_n": len(test),
        "test_avg": test_avg,
        "test_R": float(test.pnl_r.sum()) if len(test) else 0.0,
        "test_wr": float((test.pnl_r > 0).mean()) if len(test) else 0.0,
        "pass": passed,
    }


def _print_breakdown(trades: list[dict], lines: list[str]) -> None:
    td = pd.DataFrame(trades)
    lines.append("\n--- by decile ---")
    for d in sorted(td.decile.dropna().unique()):
        m = td[td.decile == d]
        lines.append(
            f"  D{int(d)}: n={len(m)} avg={m.pnl_r.mean():+.3f}R tot={m.pnl_r.sum():+.2f}R "
            f"wr={(m.pnl_r > 0).mean():.0%}"
        )

    lines.append("\n--- by exit reason ---")
    reasons: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        reasons[t["exit_reason"]].append(t["pnl_r"])
    for reason, rs in sorted(reasons.items(), key=lambda x: abs(sum(x[1])), reverse=True):
        lines.append(f"  {reason}: n={len(rs)} tot={sum(rs):+.2f}R avg={sum(rs)/len(rs):+.3f}R")

    lines.append("\n--- by symbol (all) ---")
    sym: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        sym[t["symbol"]].append(t["pnl_r"])
    for s, rs in sorted(sym.items(), key=lambda x: sum(x[1]), reverse=True):
        lines.append(f"  {s}: n={len(rs)} tot={sum(rs):+.2f}R avg={sum(rs)/len(rs):+.3f}R")


def main() -> None:
    out_dir = REPO / "backtest_output"
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "v65_revert_results.txt"
    summary_path = out_dir / "v65_revert_summary.csv"
    trades_path = out_dir / "v65_revert_trades.csv"

    lines = [
        f"=== v6.5-revert backtest {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC ===",
        "entry: V5 strict (vol_z>3, imb_z>2, body>=0.60, impulse>=0.30, min_confirms=4)",
        "filters: v6.2 deciles (D1,D2,D5-D9; D1/D2 imb|vol), BD>-2%, NY 14-22 UTC",
        f"exits: V5 vol_trail (RESEARCH_V5_EXITS=1)",
        f"symbols: {len(V5_SYMBOLS)} proven V5 universe",
        "",
    ]
    print("\n".join(lines), flush=True)

    trades, _rpath = bt.run_capture()
    s = score(trades)
    verdict = "KEEP" if s["pass"] else "KILL"
    summary = {
        "variant": "v65_revert",
        **s,
        "verdict": verdict,
    }

    lines.extend([
        f"\n{verdict} | n={s['n']} full={s['full_avg']:+.3f}R ({s['full_R']:+.1f}R) "
        f"wr={s['wr']:.0%}",
        f"OOS test_n={s['test_n']} test={s['test_avg']:+.3f}R ({s['test_R']:+.1f}R) "
        f"test_wr={s['test_wr']:.0%}",
        f"pass={s['pass']} (need test_n>={MIN_TEST_N}, test_avg>{PASS_TEST_AVG}, "
        f"full_avg>{PASS_FULL_AVG})",
    ])
    if trades:
        _print_breakdown(trades, lines)
        pd.DataFrame(trades).to_csv(trades_path, index=False)
        lines.append(f"\nsaved {trades_path.name}")

    text = "\n".join(lines)
    results_path.write_text(text + "\n")
    pd.DataFrame([summary]).to_csv(summary_path, index=False)
    (out_dir / "v65_revert.done").write_text(f"{verdict}\n")

    print(text, flush=True)
    print(f"\nsaved {results_path.name}, {summary_path.name}", flush=True)


if __name__ == "__main__":
    main()
