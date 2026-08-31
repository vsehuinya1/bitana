#!/usr/bin/env python3
"""Last-week ROI sim: $100 start, 10% equity/trade (pre-brake flat), current rules.

Window: Mon 2026-08-24T00:00Z -> Fri 2026-08-28T24:00Z (entries). Exits may spill
past Fri into the weekend (their PnL belongs to the week). Sequential event-driven
replay with the current rule set: per-symbol 1 (engine-wide), max concurrent 8,
cluster cap 3, per-cluster aggregate-risk cap 15% (sizing-only), spread <= 15bps.
Risk: flat 10% of current equity (NO brake/drawdown reductions — "before loss brakes").
Live-R conversion (live stops are ATR-based): asia pnl_atr/10 · ny pnl_atr/8 bull
or /6 neutral (regime_stop_atr) · bf pnl_atr/10 (TP3/30m exits already in pnl_atr).
"""
import sqlite3
from collections import defaultdict
from datetime import datetime

import sys
W0, W1 = (sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else ("2026-08-24", "2026-08-29")
RISK = float(sys.argv[3]) if len(sys.argv) > 3 else 0.10
BRAKES = len(sys.argv) > 4 and sys.argv[4] == "1"
CLUSTER_RISK_CAP = float(sys.argv[5]) if len(sys.argv) > 5 else 0.15
REDUCED = 0.0875  # live reduced_risk_pct (absolute)
BOOKS = ("asia_pump_short_4h", "ny_flush_buy_4h", "burst_follow")
CLUSTER_CAP = 3
MAX_CONC = 8
MAX_SPREAD = 15.0

con = sqlite3.connect("file:/root/bitana/storage/signal_shadow.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()
rows = cur.execute(f"""
SELECT strategy, symbol, side, entry_time, exit_time, hour, decile, pnl_atr,
       stop_atr, btc_trend_state, spread_bps, cluster_bucket
FROM shadow_trades
WHERE status='closed' AND strategy IN (?,?,?) AND entry_time >= '{W0}' AND entry_time < '{W1}'
""", BOOKS).fetchall()

def wd(ts): return datetime.fromisoformat(ts).weekday()
def hr(ts): return int(ts[11:13])

def live_R(r):
    if r["strategy"] == "asia_pump_short_4h":
        return r["pnl_atr"] / 10.0
    if r["strategy"] == "ny_flush_buy_4h":
        return r["pnl_atr"] / (8.0 if r["btc_trend_state"] == "bull" else 6.0)
    return r["pnl_atr"] / 10.0  # burst_follow

def gates(r):
    s, reg, h, d = r["strategy"], r["btc_trend_state"], hr(r["entry_time"]), r["decile"]
    w = wd(r["entry_time"])
    if r["spread_bps"] is not None and r["spread_bps"] > MAX_SPREAD:
        return "spread"
    if s == "asia_pump_short_4h":
        if w not in (0, 2, 3, 4): return "weekday"
        if reg != "neutral": return "regime"
        if d is None or d < 2: return "decile"
    elif s == "ny_flush_buy_4h":
        if w not in (1, 2, 3, 4): return "weekday"
        if reg == "bull":
            allowed = {14, 16, 17, 19, 20}
            if w == 1: allowed = {14, 16}
            elif w == 3: allowed = {14, 16, 17, 19}
        elif reg == "neutral":
            allowed = {16, 17}
        else:
            return "regime"
        if h not in allowed: return "hours"
        if d is None or d < 2: return "decile"
    else:  # burst_follow
        if w not in (0, 1, 2, 3, 4): return "weekday"
        if reg != "bull": return "regime"
        if h not in (9, 10, 11, 13): return "hours"
        if d is None or d < 1: return "decile"
        if r["side"] != "LONG": return "side"
    return None

cands, skips = [], defaultdict(int)
for r in rows:
    why = gates(r)
    if why: skips[why] += 1; continue
    cands.append(dict(strategy=r["strategy"], symbol=r["symbol"], side=r["side"],
                      entry=r["entry_time"], exit=r["exit_time"],
                      bucket=r["cluster_bucket"], hour=hr(r["entry_time"]),
                      R=live_R(r)))

events = []
for i, c in enumerate(cands):
    events.append((c["entry"], 1, i))
    events.append((c["exit"], 0, i))
events.sort(key=lambda e: (e[0], e[1]))

equity = 100.0
peak = equity; maxdd = 0.0
open_pos = {}   # idx -> dict(risk_usd, R, symbol, bucket, session-ish)
taken, skipped, day_pnl = [], defaultdict(int), defaultdict(float)
risk_active = RISK
streak = defaultdict(int)
reduced_remaining = 0
peak_eq = 100.0
cur_day = W0
curve = [(W0, equity)]

for t, kind, i in sorted(events, key=lambda e: (e[0], e[1])):
    day = t[:10]
    if kind == 0 and i in open_pos:
        p = open_pos.pop(i)
        pnl = p["R"] * p["risk_usd"]
        equity += pnl
        day_pnl[p["entry_day"]] += pnl
        p["rec"]["pnl_usd"] = pnl
        p["rec"]["equity_after"] = equity
        if BRAKES:
            peak_eq = max(peak_eq, equity)
            dd = (peak_eq - equity) / peak_eq
            if pnl < 0:
                streak[p["bucket"]] += 1
                if streak[p["bucket"]] >= 3:
                    reduced_remaining = 5
                    risk_active = REDUCED
            else:
                streak[p["bucket"]] = 0
            if reduced_remaining > 0:
                reduced_remaining -= 1
                if reduced_remaining <= 0 and dd < 0.10:
                    risk_active = RISK
            elif dd < 0.10:
                risk_active = RISK
            if dd > 0.15:
                risk_active = REDUCED
        continue
    if kind == 0: continue
    c = cands[i]
    # --- caps (current rules) ---
    if any(o["symbol"] == c["symbol"] for o in open_pos.values()):
        skipped["per_symbol_dup"] += 1; continue
    if len(open_pos) >= MAX_CONC:
        skipped["max_concurrent"] += 1; continue
    same_bucket = [o for o in open_pos.values() if o["bucket"] == c["bucket"]
                   and o["strategy"] == c["strategy"] and o["side"] == c["side"]]
    if len(same_bucket) >= CLUSTER_CAP:
        skipped["cluster_cap"] += 1; continue
    open_risk_frac = sum(o["risk_frac"] for o in same_bucket)
    if CLUSTER_RISK_CAP > 0:
        remaining = CLUSTER_RISK_CAP - open_risk_frac
        if remaining <= 0:
            skipped["cluster_risk_budget"] += 1; continue
    else:
        remaining = 1e9
    leg_risk = risk_active if BRAKES else RISK
    risk_frac = min(leg_risk, remaining)
    if equity * risk_frac < 5.0:  # exchange min-notional guard ($5)
        skipped["min_notional"] += 1; continue
    pos = dict(risk_frac=risk_frac, risk_usd=equity * risk_frac, R=c["R"],
               symbol=c["symbol"], bucket=c["bucket"], strategy=c["strategy"],
               side=c["side"], entry_day=t[:10])
    open_pos[i] = pos
    taken.append(dict(entry=t, symbol=c["symbol"], strategy=c["strategy"],
                      R=c["R"], risk_frac=risk_frac, bucket=c["bucket"][-9:-4]))
    pos["rec"] = taken[-1]

peak, maxdd, pk = 100.0, 0.0, 100.0
# daily curve from day_pnl
d = W0
import datetime as dt
d0, d1 = dt.date.fromisoformat(W0), dt.date.fromisoformat(W1) - dt.timedelta(days=1)
eq = 100.0
daily = []
while d0 <= d1:
    eq += day_pnl.get(str(d0), 0.0)
    daily.append((str(d0), round(eq, 2), round(day_pnl.get(str(d0), 0.0), 2)))
    pk = max(pk, eq); maxdd = max(maxdd, (pk - eq) / pk * 100.0)
    d0 += dt.timedelta(days=1)
final = eq

print(f"candidates (post static gates): {len(cands)} | skipped static: {dict(skips)}")
print(f"taken: {len([t for t in taken])} | dynamic skips: per_symbol_dup={skipped['per_symbol_dup']} "
      f"max_concurrent={skipped['max_concurrent']} cluster_cap={skipped['cluster_cap']} "
      f"cluster_risk_budget={skipped['cluster_risk_budget']} min_notional={skipped['min_notional']}")
print(f"\n$100 start, 10%/trade compounded, cluster-risk cap 15%:")
for d, e, p in daily: print(f"  {d}: equity ${e:.2f}  (day {p:+.2f})")
print(f"FINAL: ${final:.2f}  ROI {100*(final-100)/100:+.1f}%  | maxDD {maxdd:.1f}% "
      f"| risk={100*RISK:.0f}% brakes={int(BRAKES)} cluster_risk_cap={100*CLUSTER_RISK_CAP:.0f}%")
big = sorted([t for t in taken if 'pnl_usd' in t], key=lambda t: -abs(t['pnl_usd']))[:5]
print("\nbiggest legs:")
for t in big:
    print(f"  {t['entry'][:16]} {t['symbol'][:10]:10s} {t['strategy'][:12]:12s} R={t['R']:+.2f} risk%={100*t['risk_frac']:.1f} pnl=${t['pnl_usd']:+.2f}")
per_book = defaultdict(int)
for t in taken: per_book[t["strategy"]] += t.get("pnl_usd", 0) if 'pnl_usd' in t else 0
print("per-book PnL:", {k: round(v, 2) for k, v in per_book.items()})
