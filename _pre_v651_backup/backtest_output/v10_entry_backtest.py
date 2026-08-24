"""V10 entry research battery — two scrap-and-rebuild theses vs v645 baseline.

Results:
  backtest_output/v10_research_results.txt   ← read this from phone
  backtest_output/v10_research_summary.csv
  backtest_output/v10_research.done

Usage:
  CAPTURE_ALL=1 LIQ_SOURCE=ws_merged python backtest_output/v10_entry_backtest.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from backtest_output.v6_path_backtest import CAPTURE_ALL, run_capture, sim_v645  # noqa: E402
from backtest_output.v10_entry_theses import all_thesis_names  # noqa: E402

OUT_TXT = REPO / "backtest_output" / "v10_research_results.txt"
OUT_CSV = REPO / "backtest_output" / "v10_research_summary.csv"
OUT_DONE = REPO / "backtest_output" / "v10_research.done"

MIN_TEST_N = 50
PASS_TEST_AVG = 0.0
PASS_FULL_AVG = -0.05


def score(trades: list[dict], rpath: list) -> dict:
    if not trades:
        return {
            "n": 0, "full_avg": 0.0, "full_R": 0.0, "wr": 0.0,
            "test_n": 0, "test_avg": 0.0, "test_R": 0.0, "test_wr": 0.0, "pass": False,
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


def run_one(thesis: str) -> dict:
    os.environ["ENTRY_THESIS"] = thesis
    import backtest_output.v6_path_backtest as bt
    bt.ENTRY_THESIS = thesis
    print(f"\n{'='*60}\nENTRY_THESIS={thesis}\n{'='*60}", flush=True)
    trades, rpath = run_capture()
    s = score(trades, rpath)
    s["thesis"] = thesis
    tag = "PASS" if s["pass"] else "KILL"
    print(
        f"{tag} | n={s['n']} full={s['full_avg']:+.3f}R test_n={s['test_n']} "
        f"test={s['test_avg']:+.3f}R wr={s['wr']:.0%}",
        flush=True,
    )
    if trades:
        pd.DataFrame(trades).to_csv(REPO / f"backtest_output/v10_{thesis}_trades.csv", index=False)
    return s


def main() -> None:
    liq = os.environ.get("LIQ_SOURCE", "coinalyze")
    lines = [
        "V10 ENTRY RESEARCH — scrap v645 breakout stack",
        f"CAPTURE_ALL={CAPTURE_ALL} LIQ_SOURCE={liq}",
        f"kill: test_n>={MIN_TEST_N}, test_avg>{PASS_TEST_AVG}, full_avg>{PASS_FULL_AVG}",
        "",
        "Thesis 1 dip_absorption: post long-liq dip buy, no breakout, cascade 0.8–2.5x",
        "Thesis 2 squeeze_flow: short-liq momentum continuation, no cascade gate",
        "",
    ]
    rows = []
    for thesis in ["v645", *all_thesis_names()]:
        rows.append(run_one(thesis))

    summary = pd.DataFrame(rows)
    summary.to_csv(OUT_CSV, index=False)

    winners = summary[summary["pass"] == True]  # noqa: E712
    lines.append("SUMMARY")
    lines.append(summary.to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    if winners.empty:
        lines.append("\nDECISION: KILL all — no engine promote")
        best = summary[summary.thesis != "v645"].sort_values("test_avg", ascending=False).head(1)
        if len(best):
            b = best.iloc[0]
            lines.append(f"Best new thesis: {b.thesis} test={b.test_avg:+.3f}R (still below keep bar)")
    else:
        lines.append(f"\nDECISION: KEEP {', '.join(winners.thesis)} — spec for shadow/paper")

    text = "\n".join(lines)
    OUT_TXT.write_text(text)
    OUT_DONE.write_text("done\n")
    print(f"\n{text}", flush=True)
    print(f"\nsaved {OUT_TXT.name}", flush=True)


if __name__ == "__main__":
    main()
