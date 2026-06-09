"""Run one-lever v6.4.5 entry experiments via full path replay (not offline slicing).

Usage:
  python backtest_output/v6_entry_experiments.py              # deployed gates
  CAPTURE_ALL=1 python backtest_output/v6_entry_experiments.py  # full universe
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import engines.liq_cluster_engine_v5 as eng  # noqa: E402
from backtest_output.v6_path_backtest import (  # noqa: E402
    CONFIRM_R, GIVEBACK, run_capture, sim_v645,
)

# Reset research overrides + sniper defaults before each experiment
def _reset_engine() -> None:
    eng.SNIPER_ALLOWED_HOURS = frozenset(range(14, 24))
    eng.SNIPER_MAX_ATR_PCT = 0.65
    eng.SNIPER_MIN_VOL_Z = 0.0
    eng.SNIPER_MIN_CASCADE = 1.38
    eng.RESEARCH_CHASE_MAX_PCT = None
    eng.RESEARCH_CASCADE_MAX = None
    eng.RESEARCH_REQUIRE_IMB_AND_VOL = False
    if os.environ.get("CAPTURE_ALL", "0") == "1":
        eng.SNIPER_ALLOWED_HOURS = frozenset(range(24))
        eng.SNIPER_MAX_ATR_PCT = 1e9
        eng.SNIPER_MIN_VOL_Z = -1e9
        eng.SNIPER_MIN_CASCADE = -1.0


EXPERIMENTS: list[tuple[str, dict]] = [
    ("baseline", {}),
    ("chase_max_0.5", {"RESEARCH_CHASE_MAX_PCT": 0.5}),
    ("chase_max_1.0", {"RESEARCH_CHASE_MAX_PCT": 1.0}),
    ("cascade_max_3", {"RESEARCH_CASCADE_MAX": 3.0}),
    ("vol_z_min_1", {"SNIPER_MIN_VOL_Z": 1.0}),
    ("imb_and_vol", {"RESEARCH_REQUIRE_IMB_AND_VOL": True}),
    ("session_14_22", {"SNIPER_ALLOWED_HOURS": frozenset(range(14, 22))}),
    ("chase0.5_casc3_vol1", {
        "RESEARCH_CHASE_MAX_PCT": 0.5,
        "RESEARCH_CASCADE_MAX": 3.0,
        "SNIPER_MIN_VOL_Z": 1.0,
    }),
]


def score(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "full_avg": 0.0, "test_avg": 0.0, "test_n": 0, "test_wr": 0.0}
    td = pd.DataFrame(trades)
    td["exit_r"] = td.apply(
        lambda r: r["pnl_r"], axis=1,
    )  # recorded engine exit; path sim done in analyze if needed
    td = td.sort_values("entry_time")
    split = int(len(td) * 0.6)
    test = td.iloc[split:]
    return {
        "n": len(td),
        "full_avg": float(td["pnl_r"].mean()),
        "full_R": float(td["pnl_r"].sum()),
        "wr": float((td["pnl_r"] > 0).mean()),
        "test_n": len(test),
        "test_avg": float(test["pnl_r"].mean()) if len(test) else 0.0,
        "test_wr": float((test["pnl_r"] > 0).mean()) if len(test) else 0.0,
    }


def main() -> None:
    os.environ["QUIET"] = "1"
    mode = "CAPTURE_ALL" if os.environ.get("CAPTURE_ALL") == "1" else "V645_DEPLOYED"
    print(f"mode: {mode}\n", flush=True)
    rows = []
    for name, patches in EXPERIMENTS:
        _reset_engine()
        for k, v in patches.items():
            setattr(eng, k, v)
        trades, _ = run_capture()
        s = score(trades)
        s["experiment"] = name
        rows.append(s)
        print(f"{name:22} n={s['n']:4} full={s['full_avg']:+.3f} "
              f"test_n={s['test_n']:3} test={s['test_avg']:+.3f} wr={s['wr']:.0%}", flush=True)

    res = pd.DataFrame(rows).sort_values("test_avg", ascending=False)
    out = REPO / "backtest_output" / f"v6_entry_experiments_{mode.lower()}.csv"
    res.to_csv(out, index=False)
    print(f"\nsaved {out.name}", flush=True)


if __name__ == "__main__":
    main()
