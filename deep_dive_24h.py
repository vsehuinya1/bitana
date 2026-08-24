"""Deep-dive: day-by-day live-book candidates, regime splits, TSL vs plain."""
import sqlite3
from datetime import datetime, timezone

conn = sqlite3.connect("storage/signal_shadow.db")
conn.row_factory = sqlite3.Row
WEEK = datetime(2026, 7, 8, 0, 0, 0, tzinfo=timezone.utc).timestamp()

CANDS = [
    "asia_pump_short_4h", "asia_pump_short_4h_tsl",
    "ny_flush_buy_4h", "ny_flush_buy_4h_open", "ny_flush_buy_4h_open_tsl", "ny_flush_buy_4h_tsl",
    "fade_6h_late", "follow_3h_london",
]

print("=== day-by-day totals (closed, Jul 8+) ===")
for strat in CANDS:
    rows = conn.execute(
        """SELECT substr(entry_time,1,10) d, COUNT(*) n, ROUND(SUM(pnl_atr),2) tot
           FROM shadow_trades WHERE created_at > ? AND status='closed' AND strategy=?
           GROUP BY d ORDER BY d""",
        (WEEK, strat),
    ).fetchall()
    days = " ".join(f"{r['d'][5:]}:{r['tot']:+.1f}({r['n']})" for r in rows)
    print(f"  {strat:28s} {days}")

print("\n=== regime split (Jul 8+, closed; regime tagged at entry) ===")
for strat in CANDS:
    for r in conn.execute(
        """SELECT btc_trend_state reg, COUNT(*) n, ROUND(SUM(pnl_atr),2) tot, ROUND(AVG(pnl_atr),3) avg
           FROM shadow_trades WHERE created_at > ? AND status='closed' AND strategy=?
           GROUP BY reg""",
        (WEEK, strat),
    ):
        print(f"  {strat:28s} {str(r['reg']):8s} n={r['n']:3d} tot={r['tot']:+8.2f} avg={r['avg']:+.3f}")

print("\n=== asia plain vs tsl: biggest winners/losers Jul 8+ ===")
for strat in ("asia_pump_short_4h", "asia_pump_short_4h_tsl"):
    rows = conn.execute(
        """SELECT entry_time, symbol, pnl_atr, exit_reason FROM shadow_trades
           WHERE created_at > ? AND status='closed' AND strategy=?
           ORDER BY pnl_atr DESC LIMIT 3""",
        (WEEK, strat),
    ).fetchall()
    print(f"  {strat} top3: " + " | ".join(f"{r['symbol']} {r['pnl_atr']:+.1f} ({r['exit_reason']})" for r in rows))
    rows = conn.execute(
        """SELECT entry_time, symbol, pnl_atr, exit_reason FROM shadow_trades
           WHERE created_at > ? AND status='closed' AND strategy=?
           ORDER BY pnl_atr ASC LIMIT 3""",
        (WEEK, strat),
    ).fetchall()
    print(f"  {strat} bot3: " + " | ".join(f"{r['symbol']} {r['pnl_atr']:+.1f} ({r['exit_reason']})" for r in rows))

print("\n=== exit reason mix (Jul 8+) ===")
for strat in ("asia_pump_short_4h", "asia_pump_short_4h_tsl", "ny_flush_buy_4h_open", "ny_flush_buy_4h_open_tsl"):
    for r in conn.execute(
        """SELECT exit_reason, COUNT(*) n, ROUND(SUM(pnl_atr),2) tot
           FROM shadow_trades WHERE created_at > ? AND status='closed' AND strategy=?
           GROUP BY exit_reason""",
        (WEEK, strat),
    ):
        print(f"  {strat:28s} {r['exit_reason']:6s} n={r['n']:3d} tot={r['tot']:+8.2f}")

print("\n=== new-field edge checks (since fix, closed burst trades) ===")
FIX = datetime(2026, 7, 14, 9, 23, 0, tzinfo=timezone.utc).timestamp()
print("-- by symbol_trend_state --")
for r in conn.execute(
    """SELECT symbol_trend_state st, COUNT(*) n, ROUND(SUM(pnl_atr),2) tot, ROUND(AVG(pnl_atr),3) avg
       FROM shadow_trades WHERE created_at > ? AND status='closed' AND trigger='burst'
       GROUP BY st""",
    (FIX,),
):
    print(f"   {str(r['st']):8s} n={r['n']:3d} tot={r['tot']:+8.2f} avg={r['avg']:+.3f}")
print("-- by cluster_breadth --")
for r in conn.execute(
    """SELECT CASE WHEN cluster_breadth>=3 THEN '3+' ELSE CAST(cluster_breadth AS TEXT) END cb,
       COUNT(*) n, ROUND(SUM(pnl_atr),2) tot, ROUND(AVG(pnl_atr),3) avg
       FROM shadow_trades WHERE created_at > ? AND status='closed' AND trigger='burst'
       GROUP BY cb""",
    (FIX,),
):
    print(f"   breadth={r['cb']} n={r['n']:3d} tot={r['tot']:+8.2f} avg={r['avg']:+.3f}")
print("-- by market_liq_flow sign --")
for r in conn.execute(
    """SELECT CASE WHEN market_liq_flow_usd>0 THEN 'long-liq-dom' ELSE 'short-liq-dom' END fl,
       COUNT(*) n, ROUND(SUM(pnl_atr),2) tot, ROUND(AVG(pnl_atr),3) avg
       FROM shadow_trades WHERE created_at > ? AND status='closed' AND trigger='burst'
       GROUP BY fl""",
    (FIX,),
):
    print(f"   {r['fl']:14s} n={r['n']:3d} tot={r['tot']:+8.2f} avg={r['avg']:+.3f}")
print("-- pnl_1h -> final (do winners at 1h stay winners?) --")
for r in conn.execute(
    """SELECT CASE WHEN pnl_1h>0 THEN 'up@1h' ELSE 'down@1h' END s,
       COUNT(*) n, ROUND(AVG(pnl_atr),3) avg_final
       FROM shadow_trades WHERE created_at > ? AND status='closed' AND pnl_1h IS NOT NULL
       GROUP BY s""",
    (FIX,),
):
    print(f"   {r['s']:8s} n={r['n']:3d} avg_final={r['avg_final']:+.3f}")
