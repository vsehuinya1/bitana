"""Phase A (hourly): liquidation burst characterization — pre-registered, no entry rule.

Event: hourly liq bucket >= share_tier of trailing-24h total (info complete at bucket end).
Forward returns from the 5m bar closing at the bucket end, ATR(14, 5m)-normalized,
at +1h/+2h/+4h/+8h/+24h. Split by intensity tier x direction. 60/40 chrono split.

Qualify rule (pre-registered): n>=80, |mean|>=0.15 ATR, sign(train)==sign(test), |t|>=2.

Usage:
  python backtest_output/liq_spike_hourly_study.py
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

from backtest_output.v65_revert_config import V5_SYMBOLS  # noqa: E402

KLINES_DB = REPO / "backtest_data" / "klines_5m.db"
LIQ_DB = REPO / "backtest_data" / "coinalyze_liq.db"

START = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
END = int(datetime(2026, 5, 21, 23, 59, 59, tzinfo=timezone.utc).timestamp())

TRAIL_H = 24
MIN_TRAIL_USD = 100_000.0
TIERS = [0.10, 0.20, 0.35]
DIR_DOM = 0.70
HORIZONS = {"1h": 12, "2h": 24, "4h": 48, "8h": 96, "24h": 288}
DEDUP_H = 4
ATR_N = 14


def main() -> None:
    events = []
    ck = sqlite3.connect(KLINES_DB)
    cl = sqlite3.connect(LIQ_DB)

    for sym in V5_SYMBOLS:
        kl = pd.read_sql_query(
            "SELECT open_time, open, high, low, close FROM klines "
            "WHERE symbol=? AND open_time>=? AND open_time<=? ORDER BY open_time",
            ck, params=(sym, START * 1000, END * 1000))
        lq = pd.read_sql_query(
            "SELECT ts, long_liq, short_liq FROM liq_hourly "
            "WHERE symbol=? AND ts>=? AND ts<=? ORDER BY ts",
            cl, params=(sym, START - 2 * 86400, END))
        if len(kl) < 3000 or len(lq) < TRAIL_H * 3:
            print(f"  {sym}: insufficient data", flush=True)
            continue

        kts = (kl.open_time // 1000).values
        c = kl.close.values
        pc = np.roll(c, 1)
        tr = np.maximum(kl.high.values - kl.low.values,
                        np.maximum(np.abs(kl.high.values - pc),
                                   np.abs(kl.low.values - pc)))
        tr[0] = kl.high.values[0] - kl.low.values[0]
        atr = pd.Series(tr).rolling(ATR_N).mean().values
        lows, highs = kl.low.values, kl.high.values

        lq["tot"] = lq.long_liq + lq.short_liq
        lq["trail"] = lq.tot.rolling(TRAIL_H).sum().shift(1)
        lq["share"] = lq.tot / lq.trail.clip(lower=1.0)

        max_h = max(HORIZONS.values())
        last_ts = -10**12
        n_ev = 0
        for row in lq.itertuples():
            if (row.trail is None or np.isnan(row.trail) or row.trail < MIN_TRAIL_USD
                    or row.share < TIERS[0]):
                continue
            if row.ts - last_ts < DEDUP_H * 3600:
                continue
            # info complete at bucket end; anchor = 5m bar opening at (end - 5min)
            anchor_open = row.ts + 3600 - 300
            j = np.searchsorted(kts, anchor_open)
            if j >= len(kts) or kts[j] != anchor_open or j + max_h >= len(kts):
                continue
            if not atr[j] > 0:
                continue
            last_ts = row.ts
            n_ev += 1
            frac_l = row.long_liq / row.tot
            direction = ("long_dom" if frac_l >= DIR_DOM
                         else "short_dom" if frac_l <= 1 - DIR_DOM else "mixed")
            ev = {
                "symbol": sym, "ts": int(row.ts),
                "tier": max(t for t in TIERS if row.share >= t),
                "direction": direction, "share": round(float(row.share), 4),
                "hour": datetime.fromtimestamp(int(row.ts), tz=timezone.utc).hour,
            }
            for name, h in HORIZONS.items():
                ev[f"fwd_{name}"] = (c[j + h] - c[j]) / atr[j]
            ev["dd_24h"] = (lows[j + 1:j + 289].min() - c[j]) / atr[j]
            ev["ru_24h"] = (highs[j + 1:j + 289].max() - c[j]) / atr[j]
            events.append(ev)
        print(f"  {sym}: {n_ev} events", flush=True)

    ck.close()
    cl.close()

    ed = pd.DataFrame(events).sort_values("ts").reset_index(drop=True)
    ed.to_csv(OUT / "liq_spike_hourly_events.csv", index=False)
    split = int(len(ed) * 0.6)
    ed["seg"] = np.where(ed.index < split, "train", "test")

    lines = [
        f"=== HOURLY LIQ SPIKE STUDY {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC ===",
        f"events: {len(ed)} | symbols: {ed.symbol.nunique()} | 2026-01-01..2026-05-21",
        f"dedup {DEDUP_H}h | min trail ${MIN_TRAIL_USD:,.0f} | fwd in ATR(14,5m) units",
        "qualify: n>=80, |mean|>=0.15 ATR, sign(train)==sign(test), |t|>=2",
        "",
    ]
    qualified = []
    for tier in TIERS:
        for direction in ["long_dom", "short_dom", "mixed"]:
            m = ed[(ed.tier >= tier) & (ed.direction == direction)]
            if len(m) < 20:
                continue
            lines.append(f"--- tier>={tier:.0%} {direction} (n={len(m)}) ---")
            for name in HORIZONS:
                col = m[f"fwd_{name}"]
                tr_ = m[m.seg == "train"][f"fwd_{name}"]
                te_ = m[m.seg == "test"][f"fwd_{name}"]
                t = col.mean() / (col.std() / np.sqrt(len(col))) if col.std() > 0 else 0.0
                q = (len(m) >= 80 and abs(col.mean()) >= 0.15
                     and len(tr_) > 0 and len(te_) > 0
                     and np.sign(tr_.mean()) == np.sign(te_.mean()) and abs(t) >= 2)
                if q:
                    qualified.append((tier, direction, name, col.mean(), t))
                lines.append(
                    f"  +{name:>3}: mean={col.mean():+.3f} t={t:+.1f} "
                    f"hit={(col > 0).mean():.0%} | train={tr_.mean():+.3f} "
                    f"test={te_.mean():+.3f}{'  <-- QUALIFIES' if q else ''}")
            lines.append(f"  risk: med dd24h={m.dd_24h.median():+.2f} "
                         f"med ru24h={m.ru_24h.median():+.2f}")
            lines.append("")

    lines.append("--- events by 3h block (descriptive) ---")
    ed["blk"] = ed.hour // 3 * 3
    for b in sorted(ed.blk.unique()):
        m = ed[ed.blk == b]
        lines.append(f"  {b:02d}-{b+2:02d}: n={len(m)} fwd4h={m.fwd_4h.mean():+.3f}")

    lines.append("")
    if qualified:
        lines.append("QUALIFYING CELLS:")
        for tier, d, name, mu, t in qualified:
            lines.append(f"  tier>={tier:.0%} {d} +{name}: {mu:+.3f} ATR (t={t:+.1f})")
    else:
        lines.append("NO QUALIFYING CELLS — hourly liq bursts carry no exploitable "
                     "1h-24h signal on this universe/window. Do not proceed to Phase B.")

    text = "\n".join(lines)
    (OUT / "liq_spike_hourly_results.txt").write_text(text + "\n")
    print(text, flush=True)
    (OUT / "liq_spike_hourly.done").write_text("DONE\n")


if __name__ == "__main__":
    main()
