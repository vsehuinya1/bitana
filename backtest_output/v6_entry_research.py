"""Phase 2 entry diagnosis for v6.4.5 — bucket trades, label pathways, OOS slices.

Usage:
  python backtest_output/v6_entry_research.py \\
    --trades backtest_output/v6_bt_trades_capture_all.csv \\
    --rpath backtest_output/v6_bt_rpath_capture_all.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

CONFIRM_R = 0.3
GIVEBACK = 0.75
CONFIRM_BY = 10


def load_paths(rpath_csv: str) -> dict[str, list[tuple[int, float, float, float]]]:
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


def sim_v645(path: list, baseline: float) -> float:
    window = [(b, m, u) for b, m, _, u in path if b <= CONFIRM_BY]
    if not window:
        return baseline
    if not any(m >= CONFIRM_R for _, m, _ in window):
        return window[-1][2]
    first = next(i for i, (_, m, _, _) in enumerate(path) if m >= CONFIRM_R)
    for _, mfe, _, ur in path[first:]:
        stop = max(mfe - GIVEBACK, 0.0)
        if ur <= stop:
            return stop
    return baseline


def pathway_confirmed(path: list) -> bool:
    for b, mfe, _, _ in path:
        if b > CONFIRM_BY:
            break
        if mfe >= CONFIRM_R:
            return True
    return False


def enrich(td: pd.DataFrame, paths: dict) -> pd.DataFrame:
    td = td.copy()
    td["exit_r"] = [
        sim_v645(paths.get(u, []), b) for u, b in zip(td.trade_uuid, td.pnl_r)
    ]
    td["pathway_ok"] = [pathway_confirmed(paths.get(u, [])) for u in td.trade_uuid]
    td["entry_dt"] = pd.to_datetime(td.entry_time, utc=True)
    td = td.sort_values("entry_dt").reset_index(drop=True)
    return td


def bucket_report(td: pd.DataFrame, col: str, bins, labels: list[str]) -> pd.DataFrame:
    td = td.copy()
    td["bucket"] = pd.cut(td[col], bins=bins, labels=labels, include_lowest=True)
    g = td.groupby("bucket", observed=True).agg(
        n=("exit_r", "count"),
        avg_r=("exit_r", "mean"),
        wr=("exit_r", lambda s: (s > 0).mean()),
        pathway=("pathway_ok", "mean"),
    )
    return g


def oos_rule(td: pd.DataFrame, name: str, mask) -> dict | None:
    split = int(len(td) * 0.6)
    train, test = td.iloc[:split], td.iloc[split:]
    tr, te = train[mask(train)], test[mask(test)]
    if len(tr) < 20 or len(te) < 10:
        return None
    return {
        "rule": name,
        "train_n": len(tr), "train_avg": tr.exit_r.mean(),
        "test_n": len(te), "test_avg": te.exit_r.mean(), "test_wr": (te.exit_r > 0).mean(),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--rpath", required=True)
    args = ap.parse_args()

    paths = load_paths(args.rpath)
    td = enrich(pd.read_csv(args.trades), paths)

    print(f"Cohort: N={len(td)} | baseline exit_r avg={td.exit_r.mean():+.3f} WR={(td.exit_r>0).mean():.1%}")
    print(f"Pathway +0.3R by bar10: {td.pathway_ok.mean():.1%}\n")

    if "breakout_distance_pct" in td.columns:
        print("=== breakout_distance_pct buckets ===")
        print(bucket_report(
            td, "breakout_distance_pct",
            [-999, -2, 0, 0.25, 0.5, 1.0, 999],
            ["<-2%", "pullback", "0-0.25%", "0.25-0.5%", "0.5-1%", ">1%"],
        ).to_string(float_format=lambda x: f"{x:.3f}"))
        print()

    print("=== cascade_strength buckets ===")
    print(bucket_report(
        td, "cascade_strength",
        [0, 1.0, 1.38, 2.0, 3.0, 999],
        ["<1", "1-1.38", "1.38-2", "2-3", ">3"],
    ).to_string(float_format=lambda x: f"{x:.3f}"))
    print()

    print("=== vol_z buckets ===")
    print(bucket_report(
        td, "vol_z",
        [-999, 0, 1, 2, 3, 999],
        ["<0", "0-1", "1-2", "2-3", ">3"],
    ).to_string(float_format=lambda x: f"{x:.3f}"))
    print()

    print("=== decile ===")
    print(td.groupby("decile").agg(
        n=("exit_r", "count"), avg=("exit_r", "mean"), wr=("exit_r", lambda s: (s > 0).mean())
    ).to_string(float_format=lambda x: f"{x:.3f}"))
    print()

    rules = [
        ("v645 flow", lambda d: (d.vol_z > 0) & (d.cascade_strength >= 1.38)),
        ("no chase <=0.5%", lambda d: d.breakout_distance_pct <= 0.5) if "breakout_distance_pct" in td.columns else None,
        ("no chase <=0.25%", lambda d: d.breakout_distance_pct <= 0.25) if "breakout_distance_pct" in td.columns else None,
        ("imb+vol", lambda d: (d.conf_imb == 1) & (d.conf_vol == 1)) if "conf_imb" in td.columns else None,
        ("casc 1.38-3", lambda d: (d.cascade_strength >= 1.38) & (d.cascade_strength < 3)),
        ("D5-9", lambda d: d.decile.isin([5, 6, 7, 8, 9])),
        ("ny 14-22", lambda d: (d.hour >= 14) & (d.hour < 22)),
    ]
    rows = []
    for item in rules:
        if item is None:
            continue
        name, fn = item
        r = oos_rule(td, name, fn)
        if r:
            rows.append(r)
    if rows:
        print("=== OOS candidates (train_n>=20, test_n>=10) ===")
        print(pd.DataFrame(rows).sort_values("test_avg", ascending=False).to_string(
            index=False, float_format=lambda x: f"{x:+.3f}"
        ))


if __name__ == "__main__":
    main()
