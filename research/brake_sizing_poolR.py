import sqlite3
con = sqlite3.connect('file:storage/signal_shadow.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()
cur.row_factory = sqlite3.Row

print("== Friday-eve + Sat Asia blocked pool, proper R = pnl_atr/stop_atr ==")
for a, b, label in [("2026-08-28T20:05", "2026-08-29T00:00", "Fri eve 20:05-24:00"),
                    ("2026-08-29T00:00", "2026-08-29T12:00", "Sat 00:00-12:00 (Asia)")]:
    tot = cnt = 0.0
    for x in cur.execute("""SELECT entry_time, symbol, pnl_atr, stop_atr FROM shadow_trades
        WHERE status='closed' AND strategy IN ('burst_follow','ny_flush_buy_4h') AND side='LONG'
        AND entry_time>=? AND entry_time<? AND would_live_accept=1 ORDER BY entry_time""", (a, b)):
        R = x['pnl_atr'] / x['stop_atr']
        tot += R; cnt += 1
        print(f"  {label}  {x['entry_time'][5:16]} {x['symbol']:9s} pnl_atr {x['pnl_atr']:+.2f} / stop {x['stop_atr']:.2f} = {R:+.2f}R")
    print(f"  -> {label}: n={cnt:.0f} sumR={tot:+.2f}")
    print()
