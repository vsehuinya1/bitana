"""Outlook update: Thu Jul 16 + early Fri Jul 17 enriched shadow logs."""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timezone

DB = "storage/signal_shadow.db"
LIVE = (
    "asia_pump_short_4h",
    "asia_pump_short_4h_tsl",
    "ny_flush_buy_4h_open",
    "ny_flush_buy_4h_open_tsl",
)
BULL_CAND = (
    "london_burst_fade",
    "asia_pump_short_4h",
    "asia_pump_short_4h_tsl",
    "ny_flush_buy_4h_open",
)


def metrics(rows: list[sqlite3.Row]) -> str:
    pnl = [float(r["pnl_atr"]) for r in rows]
    if not pnl:
        return "n=0"
    wins = sum(v > 0 for v in pnl)
    return (
        f"n={len(pnl):3d} tot={sum(pnl):+7.2f} avg={statistics.mean(pnl):+6.3f} "
        f"med={statistics.median(pnl):+6.3f} wr={100*wins/len(pnl):4.0f}%"
    )


def cap_sim(rows: list[sqlite3.Row], cap: int = 3) -> tuple[int, float, float]:
    rows = sorted(rows, key=lambda r: (r["entry_time"], r["id"]))
    active: list[sqlite3.Row] = []
    accepted: list[sqlite3.Row] = []
    for row in rows:
        active = [r for r in active if r["exit_time"] > row["entry_time"]]
        if len(active) >= cap or any(r["symbol"] == row["symbol"] for r in active):
            continue
        active.append(row)
        accepted.append(row)
    gross = sum(float(r["pnl_atr"]) for r in accepted)
    cost = sum(0.12 / float(r["entry_atr_pct"]) for r in accepted if r["entry_atr_pct"])
    return len(accepted), gross, gross - cost


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print("=" * 70)
print("REGIME TIMELINE (entries by day x btc_trend_state)")
for r in conn.execute(
    """
    SELECT substr(entry_time,1,10) day, btc_trend_state reg, COUNT(*) n,
           ROUND(SUM(CASE WHEN status='closed' THEN pnl_atr ELSE 0 END),2) closed_tot
    FROM shadow_trades WHERE entry_time >= '2026-07-14'
    GROUP BY day, reg ORDER BY day, reg
    """
):
    print(f"  {r['day']} {str(r['reg']):8s} n={r['n']:4d} closed_pnl={r['closed_tot'] or 0:+8.2f}")

print("\n" + "=" * 70)
print("THU JUL 16 (all regimes, closed)")
thu = [dict(r) for r in conn.execute(
    "SELECT * FROM shadow_trades WHERE entry_time >= '2026-07-16' "
    "AND entry_time < '2026-07-17' AND status='closed'"
).fetchall()]
print("ALL", metrics(thu))
for reg in ("bull", "neutral", "bear"):
    sub = [r for r in thu if r["btc_trend_state"] == reg]
    if sub:
        print(f"  {reg:8s} {metrics(sub)}")

print("\nBY SESSION (Thu closed)")
for sess in ("asia", "london", "ny", "late"):
    sub = [r for r in thu if r["session"] == sess]
    if sub:
        print(f"  {sess:8s} {metrics(sub)}")

print("\nKEY STRATEGIES Thu")
for strat in LIVE + BULL_CAND + ("follow_3h_london", "fade_6h_late", "burst_follow"):
    sub = [r for r in thu if r["strategy"] == strat]
    if sub:
        print(f"  {strat:32s} {metrics(sub)}")

print("\n" + "=" * 70)
print("EARLY FRI JUL 17 (00:00-06:00 UTC, closed)")
fri = [dict(r) for r in conn.execute(
    "SELECT * FROM shadow_trades WHERE entry_time >= '2026-07-17' "
    "AND entry_time < '2026-07-17T06:00:00' AND status='closed'"
).fetchall()]
print("ALL", metrics(fri))
for reg in ("bull", "neutral", "bear"):
    sub = [r for r in fri if r["btc_trend_state"] == reg]
    if sub:
        print(f"  {reg:8s} {metrics(sub)}")
print("\nKEY STRATEGIES early Fri")
for strat in LIVE + BULL_CAND + ("follow_3h_london", "fade_6h_late"):
    sub = [r for r in fri if r["strategy"] == strat]
    if sub:
        print(f"  {strat:32s} {metrics(sub)}")

print("\n" + "=" * 70)
print("BULL RUN UPDATE (Jul 15 01:00 through last bull entry)")
bull_rows = [dict(r) for r in conn.execute(
    "SELECT * FROM shadow_trades WHERE btc_trend_state='bull' "
    "AND entry_time >= '2026-07-15' AND status='closed'"
).fetchall()]
last_bull = conn.execute(
    "SELECT MAX(entry_time) FROM shadow_trades WHERE btc_trend_state='bull'"
).fetchone()[0]
print(f"Last bull entry: {last_bull}")
print("ALL BULL CLOSED", metrics(bull_rows))
print("\nBULL BY DAY")
by_day: dict[str, list] = defaultdict(list)
for r in bull_rows:
    by_day[r["entry_time"][:10]].append(r)
for day in sorted(by_day):
    print(f"  {day} {metrics(by_day[day])}")

print("\nBULL CANDIDATES BY DAY")
for strat in BULL_CAND:
    parts = []
    for day in sorted(by_day):
        sub = [r for r in by_day[day] if r["strategy"] == strat]
        if sub:
            parts.append(f"{day[5:]} {metrics(sub)}")
    if parts:
        print(f"  {strat:28s} {' | '.join(parts)}")

print("\nLONDON LONG HOUR SPLIT (bull only, closed)")
london = [r for r in bull_rows if r["strategy"] == "london_burst_fade" and r["side"] == "LONG"]
for hour in sorted(set(r["hour"] for r in london)):
    sub = [r for r in london if r["hour"] == hour]
    print(f"  hour {hour:2d} {metrics(sub)}")

print("\nCAP SIM (closed, FIFO max 3/symbol)")
for label, filt in (
    ("london_all_bull", lambda r: r["strategy"] == "london_burst_fade"),
    ("london_long_bull", lambda r: r["strategy"] == "london_burst_fade" and r["side"] == "LONG"),
    ("london_8_10_12_long", lambda r: r["strategy"] == "london_burst_fade" and r["side"] == "LONG" and r["hour"] in (8, 10, 12)),
    ("asia_tsl_bull", lambda r: r["strategy"] == "asia_pump_short_4h_tsl"),
    ("asia_plain_bull", lambda r: r["strategy"] == "asia_pump_short_4h"),
    ("ny_open_bull", lambda r: r["strategy"] == "ny_flush_buy_4h_open"),
):
    rows = [r for r in bull_rows if filt(r)]
    for day in ("all", "2026-07-15", "2026-07-16"):
        sub = rows if day == "all" else [r for r in rows if r["entry_time"].startswith(day)]
        if not sub:
            continue
        acc, gross, net = cap_sim(sub)
        print(f"  {label:22s} {day:10s} acc={acc:2d}/{len(sub):2d} gross={gross:+7.2f} net={net:+7.2f}")

print("\n" + "=" * 70)
print("NEUTRAL REGIME (early Fri + live-book relevance)")
neutral_rows = [dict(r) for r in conn.execute(
    "SELECT * FROM shadow_trades WHERE btc_trend_state='neutral' "
    "AND entry_time >= '2026-07-17' AND status='closed'"
).fetchall()]
print("Fri neutral closed", metrics(neutral_rows))
for strat in ("asia_pump_short_4h", "asia_pump_short_4h_tsl", "ny_flush_buy_4h_open", "london_burst_fade"):
    sub = [r for r in neutral_rows if r["strategy"] == strat]
    if sub:
        print(f"  {strat:32s} {metrics(sub)}")

print("\nLIVE-BOOK NEUTRAL+BEAR since retune (Jul 15+, closed)")
live_rows = [dict(r) for r in conn.execute(
    "SELECT * FROM shadow_trades WHERE btc_trend_state IN ('neutral','bear') "
    "AND entry_time >= '2026-07-15' AND status='closed' "
    "AND strategy IN ('asia_pump_short_4h','ny_flush_buy_4h_open')"
).fetchall()]
print("ALL", metrics(live_rows))
for day in ("2026-07-15", "2026-07-16", "2026-07-17"):
    sub = [r for r in live_rows if r["entry_time"].startswith(day)]
    if sub:
        print(f"  {day} {metrics(sub)}")

print("\n" + "=" * 70)
print("OUTLOOK DELTAS vs Wed Jul 16 analysis")
# Wed had: bull ~28h, london +30.8 n=47 one session, asia tsl +9.69 n=9, asia plain -1.55 n=5
london_all = [r for r in bull_rows if r["strategy"] == "london_burst_fade"]
asia_tsl = [r for r in bull_rows if r["strategy"] == "asia_pump_short_4h_tsl"]
asia_plain = [r for r in bull_rows if r["strategy"] == "asia_pump_short_4h"]
_, _, london_net = cap_sim(london_all)
_, _, tsl_net = cap_sim(asia_tsl)
_, _, plain_net = cap_sim(asia_plain)
print(f"London bull fade:     was +30.8/47 one day -> now {sum(float(r['pnl_atr']) for r in london_all):+.1f}/{len(london_all)} gross, cap3 net {london_net:+.1f}")
print(f"Asia TSL bull:        was +9.7/9 two days   -> now {sum(float(r['pnl_atr']) for r in asia_tsl):+.1f}/{len(asia_tsl)} gross, cap3 net {tsl_net:+.1f}")
print(f"Asia plain bull:      was -1.6/5          -> now {sum(float(r['pnl_atr']) for r in asia_plain):+.1f}/{len(asia_plain)} gross, cap3 net {plain_net:+.1f}")
thu_london = [r for r in london_all if r["entry_time"].startswith("2026-07-16")]
print(f"London Thu alone:     {metrics(thu_london)}")
thu_asia_tsl = [r for r in asia_tsl if r["entry_time"].startswith("2026-07-16")]
print(f"Asia TSL Thu:         {metrics(thu_asia_tsl)}")

conn.close()
