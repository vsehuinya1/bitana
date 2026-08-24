"""Run v8 entry thesis battery on CAPTURE_ALL universe with OOS scoring.

Usage:
  CAPTURE_ALL=1 python backtest_output/v8_entry_backtest.py
  CAPTURE_ALL=1 ENTRY_THESIS=bar3_proof python backtest_output/v8_entry_backtest.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from backtest_output.v6_path_backtest import CAPTURE_ALL, run_capture, sim_v645  # noqa: E402
from backtest_output.v8_entry_theses import all_thesis_names, reset_thesis_state  # noqa: E402

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
        "thesis": os.environ.get("ENTRY_THESIS", ""),
        "n": len(td),
        "full_avg": full_avg,
        "full_R": float(td.exit_r.sum()),
        "wr": float((td.exit_r > 0).mean()),
        "test_n": len(test),
        "test_avg": test_avg,
        "test_R": float(test.exit_r.sum()) if len(test) else 0.0,
        "test_wr": float((test["exit_r"] > 0).mean()) if len(test) else 0.0,
        "pass": passed,
    }


def run_one(thesis: str) -> dict:
    reset_thesis_state()
    os.environ["ENTRY_THESIS"] = thesis
    import backtest_output.v6_path_backtest as bt
    bt.ENTRY_THESIS = thesis

    print(f"\n{'='*60}\nENTRY_THESIS={thesis}\n{'='*60}", flush=True)
    trades, rpath = run_capture()
    s = score_universe(trades, rpath)
    s["thesis"] = thesis
    verdict = "PASS" if s["pass"] else "KILL"
    print(
        f"{verdict} | n={s['n']} full={s['full_avg']:+.3f}R "
        f"test_n={s['test_n']} test={s['test_avg']:+.3f}R wr={s['wr']:.0%}",
        flush=True,
    )
    if trades:
        pd.DataFrame(trades).to_csv(REPO / "backtest_output" / f"v8_{thesis}_trades.csv", index=False)
    return s


def main() -> None:
    if not CAPTURE_ALL:
        print("WARN: set CAPTURE_ALL=1 for universe evaluation", flush=True)

    single = os.environ.get("ENTRY_THESIS", "").strip()
    battery = [single] if single and single != "all" else ["v645", *all_thesis_names()]

    rows = []
    for name in battery:
        rows.append(run_one(name))

    summary = pd.DataFrame(rows)
    out = REPO / "backtest_output" / "v8_research_summary.csv"
    summary.to_csv(out, index=False)

    print(f"\n{'='*60}\nSUMMARY\n{'='*60}", flush=True)
    print(summary.to_string(index=False, float_format=lambda x: f"{x:+.3f}"), flush=True)
    print(f"\nsaved {out.name}", flush=True)

    winners = summary[summary["pass"] == True]  # noqa: E712
    if winners.empty:
        print("\nDECISION: KILL all v8 theses — no engine promote.", flush=True)
    else:
        print(f"\nDECISION: KEEP candidate(s): {', '.join(winners.thesis)}", flush=True)


if __name__ == "__main__":
    main()
