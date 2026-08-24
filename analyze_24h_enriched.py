"""24h+ analysis of enriched shadow logs (post Jul-14 09:23 UTC fix)."""
import sqlite3
from datetime import datetime, timezone

conn = sqlite3.connect("storage/signal_shadow.db")
conn.row_factory = sqlite3.Row
FIX = datetime(2026, 7, 14, 9, 23, 0, tzinfo=timezone.utc).timestamp()
WEEK = datetime(2026, 7, 8, 0, 0, 0, tzinfo=timezone.utc).timestamp()

n = conn.execute("SELECT COUNT(*) FROM shadow_trades WHERE created_at > ?", (FIX,)).fetchone()[0]
closed = conn.execute(
    "SELECT COUNT(*) FROM shadow_trades WHERE created_at > ? AND status='closed'", (FIX,)
).fetchone()[0]
print(f"total_since_fix: {n}, closed: {closed}")

print("\n=== per strategy (closed, since fix, n>=3) ===")
for r in conn.execute(
    """SELECT strategy, COUNT(*) n, ROUND(SUM(pnl_atr),2) tot, ROUND(AVG(pnl_atr),3) avg,
       SUM(CASE WHEN pnl_atr>0 THEN 1 ELSE 0 END) wins
       FROM shadow_trades WHERE created_at > ? AND status='closed'
       GROUP BY strategy HAVING n>=3 ORDER BY tot DESC""",
    (FIX,),
):
    print(f"  {r['strategy']:32s} n={r['n']:3d} tot={r['tot']:+8.2f} avg={r['avg']:+.3f} wr={100*r['wins']/r['n']:.0f}%")

print("\n=== live-book strategies, Jul 8+ ===")
for strat in (
    "ny_flush_buy_4h_open_tsl", "asia_pump_short_4h_tsl", "follow_3h_london",
    "fade_6h_late", "ny_flush_buy_4h_open", "asia_pump_short_4h",
):
    r = conn.execute(
        """SELECT COUNT(*) n, ROUND(SUM(pnl_atr),2) tot, ROUND(AVG(pnl_atr),3) avg
           FROM shadow_trades WHERE created_at > ? AND status='closed' AND strategy=?""",
        (WEEK, strat),
    ).fetchone()
    print(f"  {strat:32s} n={r['n']:3d} tot={(r['tot'] or 0):+8.2f} avg={(r['avg'] or 0):+.3f}")

print("\n=== live-book strategies, since fix only ===")
for strat in (
    "ny_flush_buy_4h_open_tsl", "asia_pump_short_4h_tsl", "follow_3h_london", "fade_6h_late",
):
    r = conn.execute(
        """SELECT COUNT(*) n, ROUND(SUM(pnl_atr),2) tot
           FROM shadow_trades WHERE created_at > ? AND status='closed' AND strategy=?""",
        (FIX, strat),
    ).fetchone()
    print(f"  {strat:32s} n={r['n']:3d} tot={(r['tot'] or 0):+8.2f}")

print("\n=== regime x top strategies (since fix, closed) ===")
for r in conn.execute(
    """SELECT btc_trend_state reg, strategy, COUNT(*) n, ROUND(SUM(pnl_atr),2) tot
       FROM shadow_trades WHERE created_at > ? AND status='closed'
       AND strategy IN ('ny_flush_buy_4h_open_tsl','asia_pump_short_4h_tsl','follow_3h_london','fade_6h_late')
       GROUP BY reg, strategy ORDER BY strategy, reg""",
    (FIX,),
):
    print(f"  {r['strategy']:28s} {str(r['reg']):8s} n={r['n']:2d} tot={r['tot']:+7.2f}")

print("\n=== pnl checkpoints ===")
p1 = conn.execute("SELECT COUNT(*) FROM shadow_trades WHERE created_at > ? AND pnl_1h IS NOT NULL", (FIX,)).fetchone()[0]
p2 = conn.execute("SELECT COUNT(*) FROM shadow_trades WHERE created_at > ? AND pnl_2h IS NOT NULL", (FIX,)).fetchone()[0]
print(f"  pnl_1h: {p1}, pnl_2h: {p2}")

print("\n=== would_live_accept split on burst strategies ===")
for r in conn.execute(
    """SELECT would_live_accept wla, COUNT(*) n, ROUND(SUM(pnl_atr),2) tot
       FROM shadow_trades WHERE created_at > ? AND status='closed' AND trigger='burst'
       GROUP BY wla""",
    (FIX,),
):
    print(f"  wla={r['wla']} n={r['n']} tot={r['tot']:+7.2f}")

print("\n=== spread cost check: avg spread on live-book entries ===")
r = conn.execute(
    """SELECT ROUND(AVG(spread_bps),2) sp, ROUND(AVG(book_depth_usd_5bps),0) dp, COUNT(*) n
       FROM shadow_trades WHERE created_at > ?
       AND strategy IN ('ny_flush_buy_4h_open_tsl','asia_pump_short_4h_tsl')""",
    (FIX,),
).fetchone()
print(f"  n={r['n']} avg_spread_bps={r['sp']} avg_depth_usd={r['dp']}")

print("\n=== BTC regime timeline (last entries per hour) ===")
for r in conn.execute(
    """SELECT substr(entry_time,1,13) hr, btc_trend_state, ROUND(AVG(btc_adx),1) adx, COUNT(*) n
       FROM shadow_trades WHERE created_at > ?
       GROUP BY hr ORDER BY hr DESC LIMIT 12""",
    (FIX,),
):
    print(f"  {r['hr']} {str(r['btc_trend_state']):8s} adx={r['adx']} n={r['n']}")

print("\n=== per-session totals (closed, since fix) ===")
for r in conn.execute(
    """SELECT session, COUNT(*) n, ROUND(SUM(pnl_atr),2) tot
       FROM shadow_trades WHERE created_at > ? AND status='closed' GROUP BY session""",
    (FIX,),
):
    print(f"  {r['session']:8s} n={r['n']:3d} tot={r['tot']:+8.2f}")
