"""V9 — two-stage scale-in on v645 entries (Phase 5 pathway hypothesis).

Mechanism:
  - Bar 0: enter probe at 25% risk (same v645 signal, same stop).
  - Bar 3: if MFE >= 0.3R, add remaining 75% at bar-3 close (same stop).
  - If not scaled by bar 3: exit probe only via confirm-or-cut (bar 10 / stop).
  - If scaled: combined PnL = 0.25*r_probe + 0.75*(r_exit - r_b3) using initial rpu.

Compares vs full-size v645 baseline on same trade set + OOS 60/40 split.

Usage:
  python backtest_output/v9_scale_in_backtest.py
  python backtest_output/v9_scale_in_backtest.py --trades ... --rpath ...
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
DEFAULT_TRADES = REPO / "backtest_output" / "v8_v645_trades.csv"
DEFAULT_RPATH = REPO / "backtest_output" / "v6_bt_rpath_capture_all.csv"
OUT_TXT = REPO / "backtest_output" / "v9_research_results.txt"
OUT_CSV = REPO / "backtest_output" / "v9_research_summary.csv"

PROBE_W = 0.25
ADD_W = 0.75
SCALE_BY = 3
SCALE_MFE = 0.3
CONFIRM_BY = 10
CONFIRM_R = 0.3
GIVEBACK = 0.75
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


def sim_full_v645(path: list, baseline: float) -> float:
    window = [(b, m, u) for b, m, _, u in path if b <= CONFIRM_BY]
    if not window:
        return baseline
    if not any(m >= CONFIRM_R for _, m, _ in window):
        return window[-1][2]
    first = next(i for i, (_, m, _, _) in enumerate(path) if m >= CONFIRM_R)
    for _, _mfe, _, ur in path[first:]:
        stop = max(_mfe - GIVEBACK, 0.0)
        if ur <= stop:
            return stop
    return baseline


def sim_scale_in(path: list, baseline: float) -> tuple[float, bool]:
    """Return (exit_r, scaled_flag)."""
    if not path:
        return baseline * PROBE_W, False

    mfe_b3 = _mfe_by(path, SCALE_BY)
    b3 = _at_bar(path, SCALE_BY)
    if b3 is None:
        # short path — probe only, cut at last bar or baseline
        window = [(b, m, u) for b, m, _, u in path if b <= CONFIRM_BY]
        if not window:
            return baseline * PROBE_W, False
        if not any(m >= CONFIRM_R for _, m, _ in window):
            return window[-1][2] * PROBE_W, False
        return sim_full_v645(path, baseline) * PROBE_W, False  # unlikely

    _mfe3, r3 = b3
    if mfe_b3 < SCALE_MFE:
        # no scale — probe confirm-or-cut
        window = [(b, m, u) for b, m, _, u in path if b <= CONFIRM_BY]
        if not any(m >= CONFIRM_R for _, m, _ in window):
            cut_r = window[-1][2] if window else baseline
            return cut_r * PROBE_W, False
        # showed life but not by bar 3 threshold — still no add; probe through cut window
        if not any(m >= CONFIRM_R for _, m, _ in window):
            return window[-1][2] * PROBE_W, False
        cut_r = window[-1][2]
        return cut_r * PROBE_W, False

    # scaled at bar 3
    r_exit = sim_full_v645(path, baseline)
    combined = PROBE_W * r3 + ADD_W * (r_exit - r3)
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
        "test_n": len(test),
        "test_avg": test_avg,
        "test_R": float(test.sum()) if len(test) else 0.0,
        "pass": passed,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default=str(DEFAULT_TRADES))
    ap.add_argument("--rpath", default=str(DEFAULT_RPATH))
    args = ap.parse_args()

    trades = pd.read_csv(args.trades)
    paths = load_paths(Path(args.rpath))
    trades = trades.sort_values("entry_time").reset_index(drop=True)

    full_rs = []
    scale_rs = []
    scaled_flags = []
    for u, base in zip(trades.trade_uuid, trades.pnl_r):
        path = paths.get(u, [])
        path.sort(key=lambda x: x[0])
        full_rs.append(sim_full_v645(path, float(base)))
        sr, sc = sim_scale_in(path, float(base))
        scale_rs.append(sr)
        scaled_flags.append(sc)

    trades["exit_r_full"] = full_rs
    trades["exit_r_scale"] = scale_rs
    trades["scaled_at_b3"] = scaled_flags

    full_s = score_series(trades["exit_r_full"])
    scale_s = score_series(trades["exit_r_scale"])
    full_s["variant"] = "v645_full"
    scale_s["variant"] = "v9_scale_in_25_75"
    scale_s["scale_rate"] = float(trades["scaled_at_b3"].mean())

    # per-symbol OOS on scale-in
    split = int(len(trades) * 0.6)
    test = trades.iloc[split:]
    sym_oos = (
        test.groupby("symbol")["exit_r_scale"]
        .agg(n="count", avg="mean", tot="sum")
        .query("n >= 5")
        .sort_values("avg", ascending=False)
    )
    keep_syms = sym_oos[sym_oos.avg > 0].index.tolist()
    tiered = test[test.symbol.isin(keep_syms)]
    tier_s = score_series(tiered["exit_r_scale"]) if len(tiered) >= 20 else {
        "n": len(tiered), "full_avg": 0.0, "full_R": 0.0,
        "test_n": len(tiered), "test_avg": tiered["exit_r_scale"].mean() if len(tiered) else 0.0,
        "test_R": float(tiered["exit_r_scale"].sum()) if len(tiered) else 0.0,
        "pass": False,
    }
    tier_s["variant"] = f"scale_in_tier_{len(keep_syms)}syms"
    tier_s["scale_rate"] = float(tiered["scaled_at_b3"].mean()) if len(tiered) else 0.0

    lines = [
        "V9 TWO-STAGE SCALE-IN RESEARCH",
        f"trades={args.trades}",
        f"rpath={args.rpath}",
        f"probe={PROBE_W} add={ADD_W} scale_if_mfe>={SCALE_MFE}R by bar{SCALE_BY}",
        "",
        f"v645_full:      n={full_s['n']} full={full_s['full_avg']:+.3f}R "
        f"test_n={full_s['test_n']} test={full_s['test_avg']:+.3f}R "
        f"{'PASS' if full_s['pass'] else 'KILL'}",
        f"v9_scale_in:    n={scale_s['n']} full={scale_s['full_avg']:+.3f}R "
        f"test_n={scale_s['test_n']} test={scale_s['test_avg']:+.3f}R "
        f"scale_rate={scale_s['scale_rate']:.1%} "
        f"{'PASS' if scale_s['pass'] else 'KILL'}",
        f"scale_in_tier:  n={tier_s['n']} full={tier_s['full_avg']:+.3f}R "
        f"test_n={tier_s['test_n']} test={tier_s['test_avg']:+.3f}R "
        f"keep_symbols={len(keep_syms)} "
        f"{'PASS' if tier_s['pass'] else 'KILL'}",
        "",
        f"kill criteria: test_n>={MIN_TEST_N}, test_avg>{PASS_TEST_AVG}, full_avg>{PASS_FULL_AVG}",
        "",
        "TOP OOS symbols (scale-in, n>=5):",
        sym_oos.head(10).to_string(float_format=lambda x: f"{x:+.3f}"),
        "",
        "BOTTOM OOS symbols:",
        sym_oos.tail(5).to_string(float_format=lambda x: f"{x:+.3f}"),
    ]

    if scale_s["pass"]:
        decision = "KEEP v9 scale-in — spec for paper shadow"
    elif tier_s["pass"]:
        decision = f"KEEP tiered scale-in ({len(keep_syms)} symbols) — shadow only"
    else:
        decision = "KILL scale-in — no promote"

    lines.extend(["", f"DECISION: {decision}"])
    text = "\n".join(lines)

    OUT_TXT.write_text(text)
    summary = pd.DataFrame([full_s, scale_s, tier_s])
    summary.to_csv(OUT_CSV, index=False)
    trades.to_csv(REPO / "backtest_output" / "v9_scale_in_trades.csv", index=False)

    print(text, flush=True)
    print(f"\nsaved {OUT_TXT.name} and {OUT_CSV.name}", flush=True)


if __name__ == "__main__":
    main()
