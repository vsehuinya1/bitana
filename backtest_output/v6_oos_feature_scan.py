"""OOS feature scan on v6 path-backtest trades (759-trade cohort).

Replays v6.4.5 two-stage exit on bar-by-bar r_path, then scans entry filters
with a strict 60/40 chronological train/test split plus 3-fold walk-forward.

Usage:
  python backtest_output/v6_oos_feature_scan.py \\
    --trades /tmp/v6_bt_trades.csv --rpath /tmp/v6_bt_rpath.csv
"""
from __future__ import annotations

import argparse
from typing import Callable

import numpy as np
import pandas as pd

CONFIRM_R = 0.3
GIVEBACK = 0.75
CONFIRM_BY = 10
BLOCKED = {"AVAXUSDT", "HYPEUSDT"}


def sim_v645(path: list[tuple[int, float, float]], baseline: float) -> float:
    window = [(b, m, u) for b, m, u in path if b <= CONFIRM_BY]
    if not window:
        return baseline
    if not any(m >= CONFIRM_R for _, m, _ in window):
        return window[-1][2]
    first = next(i for i, (_, m, _) in enumerate(path) if m >= CONFIRM_R)
    for _, mfe, ur in path[first:]:
        stop = max(mfe - GIVEBACK, 0.0)
        if ur <= stop:
            return stop
    return baseline


def load_paths(rpath_csv: str) -> dict[str, list[tuple[int, float, float]]]:
    rp = pd.read_csv(rpath_csv)
    out: dict[str, list[tuple[int, float, float]]] = {}
    for u, g in rp.groupby("trade_uuid"):
        out[u] = list(zip(
            g.bar_index.astype(int),
            g.mfe_so_far.astype(float),
            g.unrealized_r.astype(float),
        ))
    return out


def enrich(td: pd.DataFrame, paths: dict) -> pd.DataFrame:
    td = td.copy()
    td["exit_r"] = [
        sim_v645(paths.get(u, []), b) for u, b in zip(td.trade_uuid, td.pnl_r)
    ]
    td["entry_dt"] = pd.to_datetime(td.entry_time, utc=True)
    td["hour"] = td.entry_dt.dt.hour
    td["session"] = np.select(
        [
            (td.hour >= 14) & (td.hour < 22),
            (td.hour >= 8) & (td.hour < 14),
            (td.hour >= 0) & (td.hour < 8),
        ],
        ["ny", "london", "asia"],
        default="late",
    )
    td["sniper"] = (
        ~td.symbol.isin(BLOCKED)
        & (td.hour >= 14)
        & (td.hour < 24)
        & (td.atr_pct < 0.65)
    )
    td["flow"] = (td.vol_z > 0) & (td.cascade_strength >= 1.38)
    return td.sort_values("entry_dt").reset_index(drop=True)


def scan_rule(name: str, mask_fn: Callable[[pd.DataFrame], pd.Series],
              train: pd.DataFrame, test: pd.DataFrame) -> dict | None:
    tr = train[mask_fn(train)]
    te = test[mask_fn(test)]
    if len(tr) < 15 or len(te) < 8:
        return None
    return {
        "rule": name,
        "train_n": len(tr),
        "train_avg": tr.exit_r.mean(),
        "test_n": len(te),
        "test_avg": te.exit_r.mean(),
        "test_R": te.exit_r.sum(),
        "test_wr": (te.exit_r > 0).mean(),
    }


def walk_forward(td: pd.DataFrame, name: str, mask_fn: Callable) -> str:
    n = len(td)
    parts = []
    for i in range(3):
        t_end = int(n * (i + 1) / 4)
        test_end = int(n * (i + 2) / 4) if i < 2 else n
        te = td.iloc[t_end:test_end]
        sub = te[mask_fn(te)]
        if len(sub) < 5:
            parts.append(f"fold{i + 1}:n/a")
        else:
            parts.append(f"fold{i + 1}:n={len(sub)} avg={sub.exit_r.mean():+.3f}")
    return f"{name:28} | " + " | ".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--rpath", required=True)
    args = ap.parse_args()

    paths = load_paths(args.rpath)
    td = enrich(pd.read_csv(args.trades), paths)
    split = int(len(td) * 0.6)
    train, test = td.iloc[:split], td.iloc[split:]

    rules: list[tuple[str, Callable]] = [
        ("ALL", lambda d: pd.Series(True, index=d.index)),
        ("sniper", lambda d: d.sniper),
        ("sniper+flow", lambda d: d.sniper & d.flow),
        ("casc>=2", lambda d: d.cascade_strength >= 2),
        ("ny 14-22", lambda d: (d.hour >= 14) & (d.hour < 22)),
        ("ny+casc>=2", lambda d: (d.hour >= 14) & (d.hour < 22) & (d.cascade_strength >= 2)),
        ("D2+D6", lambda d: d.decile.isin([2, 6])),
        ("D9", lambda d: d.decile == 9),
        ("deployed v6.4.5", lambda d: d.sniper & d.flow),
    ]
    for col, vals in [("decile", [1, 2, 6, 9]), ("session", ["ny", "late"])]:
        for v in vals:
            rules.append((f"{col}={v}", lambda d, c=col, val=v: d[c] == val))
    for q in [1.0, 1.38, 2.0, 3.0]:
        rules.append((f"casc>={q}", lambda d, v=q: d.cascade_strength >= v))

    rows = []
    for name, fn in rules:
        row = scan_rule(name, fn, train, test)
        if row:
            rows.append(row)

    res = pd.DataFrame(rows).sort_values("test_avg", ascending=False)
    pos = res[res.test_avg > 0]

    print(f"Cohort: {len(td)} trades | baseline exit_r avg={td.exit_r.mean():+.3f}")
    print(f"60/40 OOS rules tested (train_n>=15, test_n>=8): {len(res)}")
    print(f"OOS-positive: {len(pos)}\n")
    if len(pos):
        print("TOP OOS-POSITIVE:")
        print(pos.head(15).to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    print("\nWALK-FORWARD (3 folds):")
    for name, fn in rules[:9]:
        print(walk_forward(td, name, fn))


if __name__ == "__main__":
    main()
