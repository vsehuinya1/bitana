import sqlite3
con = sqlite3.connect('file:storage/signal_shadow.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()
cur.row_factory = sqlite3.Row

# live bf/nyflush 4h book stop ~3.0% (verified on live Friday fills: XRP 3.05%, APT 3.00%)
STOP_LIVE = 0.030

tot_a = tot_b = 0.0
for a, b, label in [("2026-08-28T20:05", "2026-08-29T00:00", "Fri eve"),
                    ("2026-08-29T00:00", "2026-08-29T12:00", "Sat Asia")]:
    tot = 0.0; n = 0
    print(f"== {label} ==")
    for x in cur.execute("""SELECT entry_time, symbol, entry_price, exit_price, entry_atr_pct, pnl_atr FROM shadow_trades
        WHERE status='closed' AND strategy IN ('burst_follow','ny_flush_buy_4h') AND side='LONG'
        AND entry_time>=? AND entry_time<? AND would_live_accept=1 ORDER BY entry_time""", (a, b)):
        ret = (x['exit_price'] - x['entry_price']) / x['entry_price']
        R = ret / STOP_LIVE
        tot += R; n += 1
        print(f"  {x['entry_time'][5:16]} {x['symbol']:9s} ret {ret*100:+5.2f}%  atr% {x['entry_atr_pct']:.2f}  -> {R:+.2f}R_live")
    print(f"  -> {label}: n={n} sum {tot:+.2f}R_live")
    if label == "Fri eve": tot_a = tot
    else: tot_b = tot
print(f"\nPOOL TOTAL: {tot_a + tot_b:+.2f}R_live  (Fri eve {tot_a:+.2f}, Sat Asia {tot_b:+.2f})")
