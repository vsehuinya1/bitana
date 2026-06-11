"""v66 burst continuation backtest — honest hourly liq, keep bar.

Usage:
  python backtest_output/v66_burst_backtest.py
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
OUT = REPO / "backtest_output"

from backtest_output.v66_burst_config import (  # noqa: E402
    MIN_TEST_N, PASS_FULL_AVG, PASS_TEST_AVG, V66_CFG, V66_SYMBOLS,
)

KLINES_DB = REPO / "backtest_data" / "klines_5m.db"
EVENTS = OUT / "liq_spike_hourly_events.csv"
START = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
END = int(datetime(2026, 5, 21, 23, 59, 59, tzinfo=timezone.utc).timestamp())
FEE_RT = 0.0013


def simulate() -> pd.DataFrame:
    import numpy as np

    ev = pd.read_csv(EVENTS)
    sub = ev[(ev.direction == "short_dom") & (ev.tier >= V66_CFG.share_min)].copy()
    ck = sqlite3.connect(KLINES_DB)
    out = []
    hold = V66_CFG.hold_bars
    stop_atr = V66_CFG.stop_atr

    for sym, grp in sub.groupby("symbol"):
        if sym not in V66_SYMBOLS:
            continue
        kl = pd.read_sql_query(
            "SELECT open_time, open, high, low, close FROM klines "
            "WHERE symbol=? AND open_time>=? AND open_time<=? ORDER BY open_time",
            ck, params=(sym, START * 1000, END * 1000))
        kts = (kl.open_time // 1000).values
        o, h, l, c = kl.open.values, kl.high.values, kl.low.values, kl.close.values
        pc = np.roll(c, 1)
        tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
        tr[0] = h[0] - l[0]
        atr = pd.Series(tr).rolling(V66_CFG.atr_period).mean().values

        last_ts = -10**12
        for row in grp.itertuples():
            hr = datetime.fromtimestamp(int(row.ts), tz=timezone.utc).hour
            if hr not in V66_CFG.allowed_hours:
                continue
            if row.ts - last_ts < V66_CFG.dedup_hours * 3600:
                continue
            anchor = row.ts + 3600 - 300
            j = np.searchsorted(kts, anchor)
            if j >= len(kts) or kts[j] != anchor:
                continue
            e = j + 1
            if e + hold >= len(kts) or not atr[j] > 0:
                continue
            last_ts = row.ts
            entry = o[e]
            risk = stop_atr * atr[j]
            stop = entry - risk
            exit_px, reason = None, "time_8h"
            for k in range(e, e + hold):
                if l[k] <= stop:
                    exit_px, reason = stop, "stop"
                    break
            if exit_px is None:
                exit_px = c[e + hold - 1]
            pnl_r = (exit_px - entry) / risk - entry * FEE_RT / risk
            out.append({
                "symbol": sym,
                "entry_time": datetime.fromtimestamp(int(row.ts) + 3600, tz=timezone.utc).isoformat(),
                "side": "long",
                "burst_share": row.share,
                "pnl_r": round(float(pnl_r), 4),
                "exit_reason": reason,
                "strategy_version": "v66_burst",
            })
    ck.close()
    return pd.DataFrame(out).sort_values("entry_time").reset_index(drop=True)


def score(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"n": 0, "full_avg": 0.0, "full_R": 0.0, "wr": 0.0,
                "test_n": 0, "test_avg": 0.0, "test_R": 0.0, "test_wr": 0.0,
                "pass": False, "verdict": "KILL"}
    split = int(len(trades) * 0.6)
    test = trades.iloc[split:]
    fa = float(trades.pnl_r.mean())
    ta = float(test.pnl_r.mean()) if len(test) else 0.0
    passed = len(test) >= MIN_TEST_N and ta > PASS_TEST_AVG and fa > PASS_FULL_AVG
    return {
        "n": len(trades), "full_avg": fa, "full_R": float(trades.pnl_r.sum()),
        "wr": float((trades.pnl_r > 0).mean()),
        "test_n": len(test), "test_avg": ta,
        "test_R": float(test.pnl_r.sum()) if len(test) else 0.0,
        "test_wr": float((test.pnl_r > 0).mean()) if len(test) else 0.0,
        "pass": passed, "verdict": "KEEP" if passed else "KILL",
    }


def main() -> None:
    trades = simulate()
    s = score(trades)
    lines = [
        f"=== v66 BURST CONTINUATION {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC ===",
        "thesis: short_dom hourly burst >=35% of trailing 24h -> LONG 8h | stop 3 ATR",
        "alignment: hourly coinalyze (live-computable via WS force orders)",
        "",
        f"{s['verdict']} | n={s['n']} full={s['full_avg']:+.3f}R ({s['full_R']:+.1f}R) wr={s['wr']:.0%}",
        f"OOS test_n={s['test_n']} test={s['test_avg']:+.3f}R ({s['test_R']:+.1f}R) "
        f"test_wr={s['test_wr']:.0%}",
        f"pass={s['pass']} (need test_n>={MIN_TEST_N}, test_avg>{PASS_TEST_AVG}, "
        f"full_avg>{PASS_FULL_AVG})",
    ]
    if not trades.empty:
        trades.to_csv(OUT / "v66_burst_trades.csv", index=False)
        lines.append(f"\nsaved v66_burst_trades.csv ({len(trades)} trades)")
    text = "\n".join(lines)
    (OUT / "v66_burst_results.txt").write_text(text + "\n")
    print(text, flush=True)


if __name__ == "__main__":
    main()
