"""Phase A: 5-minute liquidation burst characterization (pre-registered, no entry rule).

Event: 5m bar whose liq >= share_tier of trailing-24h total (info complete at bar close).
Measures ATR-normalized forward returns at fixed horizons, split by:
  intensity tier (share of trailing 24h: 10% / 20% / 35%)
  direction (long-dom >=70% / short-dom >=70% / mixed)
60/40 chronological split for stability. Dedup: first event per symbol per hour.

Pre-registered decision rule for Phase B: a cell qualifies only if
  n_full >= 80, |mean| >= 0.15 ATR at some horizon, same sign in train and test,
  and |t| >= 2 in full sample.

Usage:
  python backtest_output/liq_spike_study.py
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

TRAIL = 288                       # 24h of 5m bars
MIN_TRAIL_USD = 100_000.0         # dead-tape floor
TIERS = [0.10, 0.20, 0.35]        # bar liq as share of trailing 24h
DIR_DOM = 0.70
HORIZONS = [1, 3, 6, 12, 36, 72]
DEDUP_BARS = 12                   # 1h
ATR_N = 14


def load_sym(sym: str) -> pd.DataFrame | None:
    ck = sqlite3.connect(KLINES_DB)
    kl = pd.read_sql_query(
        "SELECT open_time, open, high, low, close FROM klines "
        "WHERE symbol=? AND open_time>=? AND open_time<=? ORDER BY open_time",
        ck, params=(sym, START * 1000, END * 1000))
    ck.close()
    if len(kl) < TRAIL * 2:
        return None
    kl["ts"] = kl.open_time // 1000

    cl = sqlite3.connect(LIQ_DB)
    lq = pd.read_sql_query(
        "SELECT ts, long_liq, short_liq FROM liq_5min "
        "WHERE symbol=? AND ts>=? AND ts<=? ORDER BY ts",
        cl, params=(sym, START - 86400, END))
    cl.close()
    if lq.empty:
        return None

    df = kl.merge(lq, on="ts", how="left")
    df[["long_liq", "short_liq"]] = df[["long_liq", "short_liq"]].fillna(0.0)
    df["liq"] = df.long_liq + df.short_liq

    # ATR(14) in price units
    pc = df.close.shift(1)
    tr = np.maximum(df.high - df.low,
                    np.maximum((df.high - pc).abs(), (df.low - pc).abs()))
    df["atr"] = tr.rolling(ATR_N).mean()

    df["trail"] = df.liq.rolling(TRAIL).sum().shift(1)  # excludes current bar
    df["share"] = df.liq / df.trail.clip(lower=1.0)
    return df


def main() -> None:
    events = []
    for sym in V5_SYMBOLS:
        df = load_sym(sym)
        if df is None:
            print(f"  {sym}: no data", flush=True)
            continue
        c = df.close.values
        atr = df.atr.values
        ok = (df.trail.values >= MIN_TRAIL_USD) & (atr > 0)
        spike = ok & (df.share.values >= TIERS[0])
        idxs = np.flatnonzero(spike)
        last_i = -10**9
        n_ev = 0
        for i in idxs:
            if i - last_i < DEDUP_BARS or i + max(HORIZONS) >= len(df):
                continue
            last_i = i
            n_ev += 1
            liq_l, liq_s = df.long_liq.values[i], df.short_liq.values[i]
            tot = liq_l + liq_s
            if liq_l / tot >= DIR_DOM:
                direction = "long_dom"
            elif liq_s / tot >= DIR_DOM:
                direction = "short_dom"
            else:
                direction = "mixed"
            share = df.share.values[i]
            tier = max(t for t in TIERS if share >= t)
            ev = {
                "symbol": sym, "ts": int(df.ts.values[i]),
                "tier": tier, "direction": direction, "share": round(share, 4),
                "hour": datetime.fromtimestamp(int(df.ts.values[i]), tz=timezone.utc).hour,
            }
            for h in HORIZONS:
                ev[f"fwd_{h}"] = (c[i + h] - c[i]) / atr[i]
            lo72 = df.low.values[i + 1:i + 73].min()
            hi72 = df.high.values[i + 1:i + 73].max()
            ev["dd_72"] = (lo72 - c[i]) / atr[i]
            ev["ru_72"] = (hi72 - c[i]) / atr[i]
            events.append(ev)
        print(f"  {sym}: {n_ev} events", flush=True)

    ed = pd.DataFrame(events).sort_values("ts").reset_index(drop=True)
    ed.to_csv(OUT / "liq_spike_events.csv", index=False)
    split = int(len(ed) * 0.6)
    ed["seg"] = np.where(ed.index < split, "train", "test")

    lines = [
        f"=== 5m LIQ SPIKE STUDY {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC ===",
        f"events: {len(ed)} | symbols: {ed.symbol.nunique()} | "
        f"window: 2026-01-01..2026-05-21 | dedup 1h | min trail ${MIN_TRAIL_USD:,.0f}",
        "fwd returns in ATR(14) units from spike-bar close",
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
            for h in HORIZONS:
                col = m[f"fwd_{h}"]
                tr_ = m[m.seg == "train"][f"fwd_{h}"]
                te_ = m[m.seg == "test"][f"fwd_{h}"]
                t = col.mean() / (col.std() / np.sqrt(len(col))) if col.std() > 0 else 0
                q = (len(m) >= 80 and abs(col.mean()) >= 0.15
                     and len(tr_) > 0 and len(te_) > 0
                     and np.sign(tr_.mean()) == np.sign(te_.mean())
                     and abs(t) >= 2)
                flag = "  <-- QUALIFIES" if q else ""
                if q:
                    qualified.append((tier, direction, h, col.mean(), t))
                lines.append(
                    f"  +{h:>3}b: mean={col.mean():+.3f} t={t:+.1f} "
                    f"hit={(col > 0).mean():.0%} | train={tr_.mean():+.3f} "
                    f"test={te_.mean():+.3f}{flag}")
            dd = ed[(ed.tier >= tier) & (ed.direction == direction)]
            lines.append(f"  risk: med dd72={dd.dd_72.median():+.2f} "
                         f"med ru72={dd.ru_72.median():+.2f}")
            lines.append("")

    lines.append("--- events by 3h block (descriptive only) ---")
    ed["blk"] = ed.hour // 3 * 3
    for b in sorted(ed.blk.unique()):
        m = ed[ed.blk == b]
        lines.append(f"  {b:02d}-{b+2:02d}: n={len(m)} fwd12={m.fwd_12.mean():+.3f}")

    lines.append("")
    if qualified:
        lines.append("QUALIFYING CELLS:")
        for tier, d, h, mu, t in qualified:
            lines.append(f"  tier>={tier:.0%} {d} +{h}b: {mu:+.3f} ATR (t={t:+.1f})")
    else:
        lines.append("NO QUALIFYING CELLS — liq spikes carry no exploitable 5m-72b signal "
                     "on this universe/window. Do not proceed to Phase B.")

    text = "\n".join(lines)
    (OUT / "liq_spike_results.txt").write_text(text + "\n")
    print(text, flush=True)
    (OUT / "liq_spike.done").write_text("DONE\n")


if __name__ == "__main__":
    main()
