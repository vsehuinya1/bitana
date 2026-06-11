"""MAE/stop study on v65 r_path — winners vs losers, stop sizing implications.

Usage:
  python backtest_output/v65_mae_stop_study.py
  python backtest_output/v65_mae_stop_study.py --trades ... --rpath ...
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "backtest_output"

DEFAULT_TRADES = OUT / "v6_bt_trades_v645_coinalyze_28.csv"
FALLBACK_TRADES = OUT / "v65_revert_trades.csv"
DEFAULT_RPATH = OUT / "v6_bt_rpath_v645_coinalyze_28.csv"
FALLBACK_RPATH = OUT / "v6_bt_rpath_v645_ws_merged.csv"


def load_mae_mfe(rpath_csv: Path) -> dict[str, dict]:
    rp = pd.read_csv(rpath_csv)
    out: dict[str, dict] = {}
    for u, g in rp.groupby("trade_uuid"):
        out[u] = {
            "mae": float(g.mae_so_far.min()),
            "mfe": float(g.mfe_so_far.max()),
            "bars": int(g.bar_index.max()),
        }
    return out


def pct_below(vals: list[float], threshold: float) -> float:
    if not vals:
        return 0.0
    return sum(1 for v in vals if v < threshold) / len(vals)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default=str(DEFAULT_TRADES))
    ap.add_argument("--rpath", default="")
    args = ap.parse_args()

    trades_p = Path(args.trades)
    rpath_p = Path(args.rpath) if args.rpath else DEFAULT_RPATH
    if not rpath_p.exists():
        rpath_p = FALLBACK_RPATH
    if not trades_p.exists() or not rpath_p.exists():
        print(f"MISSING trades={trades_p.exists()} rpath={rpath_p.exists()}", flush=True)
        return

    trades = pd.read_csv(trades_p)
    paths = load_mae_mfe(rpath_p)

    rows = []
    for _, t in trades.iterrows():
        p = paths.get(t.trade_uuid)
        if not p:
            continue
        rows.append({
            "trade_uuid": t.trade_uuid,
            "symbol": t.symbol,
            "decile": t.decile,
            "pnl_r": float(t.pnl_r),
            "exit_reason": t.exit_reason,
            "mae": p["mae"],
            "mfe": p["mfe"],
            "winner": float(t.pnl_r) > 0,
        })

    td = pd.DataFrame(rows)
    if td.empty:
        print(f"No matched trades between {trades_p.name} and {rpath_p.name}", flush=True)
        return

    winners = td[td["winner"]]
    losers = td[~td["winner"]]
    stops = td[td.exit_reason == "stop_loss"]

    lines = [
        "=== v65 MAE / STOP STUDY ===",
        f"trades={trades_p.name} rpath={rpath_p.name} matched={len(td)}/{len(trades)}",
        "",
        f"Winners: n={len(winners)} avg_mae={winners.mae.mean():+.3f}R "
        f"p50={winners.mae.median():+.3f}R p90={winners.mae.quantile(0.9):+.3f}R",
        f"Losers:  n={len(losers)} avg_mae={losers.mae.mean():+.3f}R "
        f"p50={losers.mae.median():+.3f}R",
        f"Stop_loss exits: n={len(stops)} (all should be ~-1R)",
        "",
        "Winners MAE below threshold (% of winners):",
    ]
    for thr in (-0.25, -0.5, -0.75, -1.0):
        lines.append(f"  MAE < {thr:+.2f}R: {pct_below(winners.mae.tolist(), thr):.0%}")

    lines.extend([
        "",
        "Implication (current stop = 2.5 ATR ≈ -1R):",
    ])
    w_p50 = winners.mae.median() if len(winners) else 0
    w_p90 = winners.mae.quantile(0.9) if len(winners) else 0
    if w_p90 > -0.5:
        lines.append(f"  p90 winner MAE is {w_p90:+.2f}R — tightening stop below 0.5R would clip winners.")
    else:
        lines.append(f"  p90 winner MAE is {w_p90:+.2f}R — room to tighten stop (e.g. 1.5–2.0 ATR).")
    if pct_below(winners.mae.tolist(), -0.5) > 0.9:
        lines.append(f"  {pct_below(winners.mae.tolist(), -0.5):.0%} of winners never dip below -0.5R — stop may be oversized.")

    lines.append("\n--- by decile (winner MAE p50) ---")
    for d in sorted(td.decile.dropna().unique()):
        w = td[(td.decile == d) & td.winner]
        if len(w):
            lines.append(f"  D{int(d)}: n={len(w)} mae_p50={w.mae.median():+.3f}R avg_R={w.pnl_r.mean():+.3f}R")

    text = "\n".join(lines)
    (OUT / "v65_mae_stop_results.txt").write_text(text + "\n")
    td.to_csv(OUT / "v65_mae_stop_trades.csv", index=False)
    print(text, flush=True)


if __name__ == "__main__":
    main()
