"""Run v7 entry thesis backtest (CAPTURE_ALL universe) and report OOS metrics.

Usage:
  CAPTURE_ALL=1 ENTRY_THESIS=exhaustion python backtest_output/v7_entry_backtest.py
  CAPTURE_ALL=1 ENTRY_THESIS=v645 python backtest_output/v7_entry_backtest.py  # baseline
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from backtest_output.v6_path_backtest import (  # noqa: E402
    CAPTURE_ALL,
    run_capture,
    sim_v645,
)

MIN_TEST_N = 50
PASS_TEST_AVG = 0.0
PASS_FULL_AVG = -0.05


def score_universe(trades: list[dict], rpath: list) -> dict:
    if not trades:
        return {
            "n": 0, "full_avg": 0.0, "full_R": 0.0, "wr": 0.0,
            "test_n": 0, "test_avg": 0.0, "test_R": 0.0, "test_wr": 0.0,
            "pass": False,
        }

    paths: dict[str, list[tuple[int, float, float]]] = {}
    for u, b, m, _mae, ur in rpath:
        paths.setdefault(u, []).append((int(b), float(m), float(ur)))
    for u in paths:
        paths[u].sort()

    td = pd.DataFrame(trades)
    td["exit_r"] = [
        sim_v645(paths.get(u, []), b, d)
        for u, b, d in zip(td.trade_uuid, td.pnl_r, td.decile)
    ]
    td = td.sort_values("entry_time")
    split = int(len(td) * 0.6)
    test = td.iloc[split:]

    full_avg = float(td.exit_r.mean())
    test_avg = float(test.exit_r.mean()) if len(test) else 0.0
    passed = len(test) >= MIN_TEST_N and test_avg > PASS_TEST_AVG and full_avg > PASS_FULL_AVG

    return {
        "n": len(td),
        "full_avg": full_avg,
        "full_R": float(td.exit_r.sum()),
        "wr": float((td.exit_r > 0).mean()),
        "test_n": len(test),
        "test_avg": test_avg,
        "test_R": float(test.exit_r.sum()) if len(test) else 0.0,
        "test_wr": float((test.exit_r > 0).mean()) if len(test) else 0.0,
        "pass": passed,
    }


def main() -> None:
    thesis = os.environ.get("ENTRY_THESIS", "exhaustion")
    if not CAPTURE_ALL:
        print("WARN: set CAPTURE_ALL=1 for universe-scale evaluation", flush=True)

    print(f"ENTRY_THESIS={thesis} | CAPTURE_ALL={CAPTURE_ALL}", flush=True)
    trades, rpath = run_capture()
    s = score_universe(trades, rpath)

    print(
        f"\n{'PASS' if s['pass'] else 'FAIL'} | n={s['n']} full={s['full_avg']:+.3f}R "
        f"test_n={s['test_n']} test={s['test_avg']:+.3f}R wr={s['wr']:.0%}",
        flush=True,
    )
    print(
        f"kill criteria: test_n>={MIN_TEST_N}, test_avg>{PASS_TEST_AVG}, full_avg>{PASS_FULL_AVG}",
        flush=True,
    )

    if trades:
        out = REPO / "backtest_output" / f"v7_{thesis}_trades.csv"
        pd.DataFrame(trades).to_csv(out, index=False)
        print(f"saved {out.name}", flush=True)


if __name__ == "__main__":
    main()
