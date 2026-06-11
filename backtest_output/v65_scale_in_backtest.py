"""v65 scale-in — 25% probe / 75% add at +0.3R bar 3 on v65 V5-exit trades.

Uses recorded engine exit R (vol_trail path) as full-size baseline, not runner sim.

Usage:
  python backtest_output/v65_scale_in_backtest.py \\
    --trades backtest_output/v65_revert_trades.csv \\
    --rpath backtest_output/v6_bt_rpath_v645_ws_merged.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent

PROBE_W = 0.25
ADD_W = 0.75
SCALE_BY = 3
SCALE_MFE = 0.3
CONFIRM_BY = 10
MIN_TEST_N = 50
PASS_TEST_AVG = 0.0
PASS_FULL_AVG = -0.05


def load_paths(rpath_csv: Path) -> dict[str, list[tuple[int, float, float, float]]]:
    rp = pd.read_csv(rpath_csv)
    out: dict[str, list[tuple[int, float, float, float]]] = {}
    for u, g in rp.groupby("trade_uuid"):
        out[u] = list(zip(
            g.bar_index.astype(int),
            g.mfe_so_far.astype(float),
            g.mae_so_far.astype(float),
            g.unrealized_r.astype(float),
        ))
    return out


def _at_bar(path: list, bar: int) -> tuple[float, float] | None:
    for b, mfe, _mae, ur in path:
        if b == bar:
            return mfe, ur
    return None


def _mfe_by(path: list, bar: int) -> float:
    best = 0.0
    for b, mfe, _, _ in path:
        if b <= bar:
            best = max(best, mfe)
    return best


def _probe_cut_r(path: list, recorded_r: float) -> float:
    """Probe-only exit before scale window closes."""
    window = [(b, m, u) for b, m, _, u in path if b <= CONFIRM_BY]
    if not window:
        return recorded_r * PROBE_W
    return window[-1][2] * PROBE_W


def sim_v65_full(recorded_r: float) -> float:
    return recorded_r


def sim_v65_scale_in(path: list, recorded_r: float) -> tuple[float, bool]:
    if not path:
        return recorded_r * PROBE_W, False

    mfe_b3 = _mfe_by(path, SCALE_BY)
    b3 = _at_bar(path, SCALE_BY)
    if b3 is None:
        return _probe_cut_r(path, recorded_r), False

    _mfe3, r3 = b3
    if mfe_b3 < SCALE_MFE:
        return _probe_cut_r(path, recorded_r), False

    combined = PROBE_W * r3 + ADD_W * (recorded_r - r3)
    return combined, True


def score_series(r: pd.Series) -> dict:
    td = r.reset_index(drop=True)
    split = int(len(td) * 0.6)
    test = td.iloc[split:]
    full_avg = float(td.mean())
    test_avg = float(test.mean()) if len(test) else 0.0
    passed = len(test) >= MIN_TEST_N and test_avg > PASS_TEST_AVG and full_avg > PASS_FULL_AVG
    return {
        "n": len(td),
        "full_avg": full_avg,
        "full_R": float(td.sum()),
        "wr": float((td > 0).mean()),
        "test_n": len(test),
        "test_avg": test_avg,
        "test_R": float(test.sum()) if len(test) else 0.0,
        "test_wr": float((test > 0).mean()) if len(test) else 0.0,
        "pass": passed,
    }


def run_scale_in(trades_csv: Path, rpath_csv: Path, label: str) -> dict:
    trades = pd.read_csv(trades_csv).sort_values("entry_time").reset_index(drop=True)
    paths = load_paths(rpath_csv)

    full_rs = []
    scale_rs = []
    scaled_flags = []
    for u, rec in zip(trades.trade_uuid, trades.pnl_r):
        path = paths.get(u, [])
        path.sort(key=lambda x: x[0])
        rec_f = float(rec)
        full_rs.append(sim_v65_full(rec_f))
        sr, sc = sim_v65_scale_in(path, rec_f)
        scale_rs.append(sr)
        scaled_flags.append(sc)

    trades["exit_r_full"] = full_rs
    trades["exit_r_scale"] = scale_rs
    trades["scaled_at_b3"] = scaled_flags

    full_s = score_series(trades["exit_r_full"])
    scale_s = score_series(trades["exit_r_scale"])
    full_s["variant"] = f"{label}_full"
    scale_s["variant"] = f"{label}_scale_in"
    scale_s["scale_rate"] = float(trades["scaled_at_b3"].mean())
    scale_s["verdict"] = "KEEP" if scale_s["pass"] else "KILL"
    full_s["verdict"] = "KEEP" if full_s["pass"] else "KILL"

    out_trades = REPO / "backtest_output" / f"v65_scale_in_{label}_trades.csv"
    trades.to_csv(out_trades, index=False)

    return {"full": full_s, "scale": scale_s, "trades_path": str(out_trades.name)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--rpath", required=True)
    ap.add_argument("--label", default="v65")
    args = ap.parse_args()

    res = run_scale_in(Path(args.trades), Path(args.rpath), args.label)
    full_s, scale_s = res["full"], res["scale"]

    lines = [
        f"V65 SCALE-IN ({args.label})",
        f"trades={args.trades}",
        f"rpath={args.rpath}",
        f"probe={PROBE_W} add={ADD_W} scale_if_mfe>={SCALE_MFE}R by bar{SCALE_BY}",
        "",
        f"full:      n={full_s['n']} avg={full_s['full_avg']:+.3f}R "
        f"test_n={full_s['test_n']} test={full_s['test_avg']:+.3f}R {full_s['verdict']}",
        f"scale_in:  n={scale_s['n']} avg={scale_s['full_avg']:+.3f}R "
        f"test_n={scale_s['test_n']} test={scale_s['test_avg']:+.3f}R "
        f"scale_rate={scale_s['scale_rate']:.1%} {scale_s['verdict']}",
    ]
    text = "\n".join(lines)
    print(text, flush=True)

    out_txt = REPO / "backtest_output" / f"v65_scale_in_{args.label}.txt"
    out_txt.write_text(text + "\n")


if __name__ == "__main__":
    main()
