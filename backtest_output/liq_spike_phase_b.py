"""Phase B: trade the pre-registered qualifying cells from the hourly spike study.

Rules (fixed BEFORE running, from Phase A qualifying cells only):
  RULE_LONG : short_dom burst tier>=20% -> LONG  next 5m bar open, exit +8h close
  RULE_SHORT: long_dom  burst tier>=10% -> SHORT next 5m bar open, exit +8h close
Stop: 3 * ATR(14,5m) intrabar, stop priority. Fees: 13 bps round trip.
R = pnl / (3*ATR). Keep bar: test_n>=50, test_avg>0, full_avg>-0.05 (60/40 chrono).

Usage:
  python backtest_output/liq_spike_phase_b.py
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
OUT = REPO / "backtest_output"

KLINES_DB = REPO / "backtest_data" / "klines_5m.db"
START = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
END = int(datetime(2026, 5, 21, 23, 59, 59, tzinfo=timezone.utc).timestamp())

STOP_ATR = 3.0
HOLD_BARS = 96  # 8h
FEE_RT = 0.0013  # taker+slip both ways
MIN_TEST_N = 50


def simulate(ev: pd.DataFrame, side: int, label: str) -> pd.DataFrame:
    ck = sqlite3.connect(KLINES_DB)
    out = []
    for sym, grp in ev.groupby("symbol"):
        kl = pd.read_sql_query(
            "SELECT open_time, open, high, low, close FROM klines "
            "WHERE symbol=? AND open_time>=? AND open_time<=? ORDER BY open_time",
            ck, params=(sym, START * 1000, END * 1000))
        kts = (kl.open_time // 1000).values
        o, h, l, c = kl.open.values, kl.high.values, kl.low.values, kl.close.values
        pc = np.roll(c, 1)
        tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
        tr[0] = h[0] - l[0]
        atr = pd.Series(tr).rolling(14).mean().values

        for row in grp.itertuples():
            anchor_open = row.ts + 3600 - 300
            j = np.searchsorted(kts, anchor_open)
            if j >= len(kts) or kts[j] != anchor_open:
                continue
            e = j + 1  # enter next bar open
            if e + HOLD_BARS >= len(kts) or not atr[j] > 0:
                continue
            entry = o[e]
            risk = STOP_ATR * atr[j]
            stop = entry - side * risk
            exit_px, reason = None, "time"
            for k in range(e, e + HOLD_BARS):
                if side > 0 and l[k] <= stop:
                    exit_px, reason = stop, "stop"
                    break
                if side < 0 and h[k] >= stop:
                    exit_px, reason = stop, "stop"
                    break
            if exit_px is None:
                exit_px = c[e + HOLD_BARS - 1]
            pnl_r = side * (exit_px - entry) / risk - entry * FEE_RT / risk
            out.append({"rule": label, "symbol": sym, "ts": int(row.ts),
                        "entry_time": datetime.fromtimestamp(int(row.ts) + 3600,
                                                             tz=timezone.utc).isoformat(),
                        "side": "long" if side > 0 else "short",
                        "tier": row.tier, "share": row.share,
                        "pnl_r": round(float(pnl_r), 4), "exit_reason": reason})
    ck.close()
    return pd.DataFrame(out).sort_values("ts").reset_index(drop=True)


def score(td: pd.DataFrame) -> str:
    if td.empty:
        return "no trades"
    split = int(len(td) * 0.6)
    test = td.iloc[split:]
    fa, ta = td.pnl_r.mean(), test.pnl_r.mean()
    passed = len(test) >= MIN_TEST_N and ta > 0 and fa > -0.05
    return (f"{'KEEP' if passed else 'KILL'} | n={len(td)} full={fa:+.3f}R "
            f"({td.pnl_r.sum():+.1f}R) wr={(td.pnl_r > 0).mean():.0%} | "
            f"OOS test_n={len(test)} test={ta:+.3f}R ({test.pnl_r.sum():+.1f}R) "
            f"test_wr={(test.pnl_r > 0).mean():.0%} | "
            f"stops={(td.exit_reason == 'stop').mean():.0%}")


def main() -> None:
    ev = pd.read_csv(OUT / "liq_spike_hourly_events.csv")
    long_ev = ev[(ev.direction == "short_dom") & (ev.tier >= 0.20)]
    short_ev = ev[(ev.direction == "long_dom") & (ev.tier >= 0.10)]

    lines = [f"=== PHASE B: liq burst continuation {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC ===",
             f"stop {STOP_ATR} ATR | hold 8h | fees {FEE_RT*1e4:.0f}bps RT | events: "
             f"long={len(long_ev)} short={len(short_ev)}", ""]

    tl = simulate(long_ev, +1, "squeeze_long")
    lines.append(f"RULE_LONG  (short_dom >=20% -> long 8h):\n  {score(tl)}")
    ts_ = simulate(short_ev, -1, "cascade_short")
    lines.append(f"RULE_SHORT (long_dom >=10% -> short 8h):\n  {score(ts_)}")
    both = pd.concat([tl, ts_]).sort_values("ts").reset_index(drop=True)
    lines.append(f"COMBINED:\n  {score(both)}")

    both.to_csv(OUT / "liq_spike_phase_b_trades.csv", index=False)
    text = "\n".join(lines)
    (OUT / "liq_spike_phase_b_results.txt").write_text(text + "\n")
    print(text, flush=True)


if __name__ == "__main__":
    main()
