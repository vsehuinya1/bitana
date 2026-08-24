import sqlite3
from datetime import datetime

c = sqlite3.connect('storage/signal_shadow.db')
c.row_factory = sqlite3.Row

def q(sql, *args):
    return c.execute(sql, args).fetchall()

def R_for(row):
    # R = pnl_atr / stop_atr where stop_atr is the stop distance in ATR
    if row['stop_atr'] is None or row['stop_atr'] == 0:
        return None
    return row['pnl_atr'] / row['stop_atr']

def summarize(rows, label, stop_note=""):
    if not rows:
        print(f"{label}: NO ROWS")
        return
    Rs = [R_for(r) for r in rows]
    Rs = [x for x in Rs if x is not None]
    wins = [x for x in Rs if x > 0]
    # distinct days
    days = set(r['entry_time'][:10] for r in rows)
    total_R = sum(x for x in Rs if x is not None)
    print(f"{label}: n={len(rows)} distinct_days={len(days)} | "
          f"avgR={sum(Rs)/len(Rs) if Rs else 0:+.3f} WR={100*len(wins)/len(Rs) if Rs else 0:.0f}% "
          f"sumR={total_R:+.1f} {stop_note}")
    return Rs

print("=" * 100)
print("VERIFYING PROPOSALS (status=closed only; R = pnl_atr/stop_atr)")
print("=" * 100)

# ---- Claim 1: NY h14 bull + 4 ATR stop (s4 variant) ----
print("\n--- CLAIM 1: NY h14 bull + 4 ATR stop ---")
rows = q("""SELECT * FROM shadow_trades WHERE status='closed'
   AND strategy='ny_flush_buy_4h_s4' AND hour=14 AND btc_trend_state='bull'""")
summarize(rows, "ny_flush_buy_4h_s4 hour=14 bull")

# ---- Claim 2: Weekend NY h21 bear ----
print("\n--- CLAIM 2: Weekend NY h21 bear ---")
for st in ['10.0', '4.0']:
    rows = q("""SELECT * FROM shadow_trades WHERE status='closed'
       AND session='ny' AND hour=21 AND is_weekend=1 AND btc_trend_state='bear' AND side='SHORT'""")
    # filter stop variants
    filt = [r for r in rows if r['stop_atr'] is not None and abs(r['stop_atr']-float(st))<0.01]
    summarize(filt, f"weekend NY h21 bear stop={st}")

# ---- Claim 3: Asia D10 at h5 ----
print("\n--- CLAIM 3: Asia D10 h5 positive (decile=10) ---")
rows = q("""SELECT * FROM shadow_trades WHERE status='closed'
   AND session='asia' AND decile=10 AND hour=5""")
summarize(rows, "asia decile=10 hour=5 (all regimes)")
rows_b = q("""SELECT * FROM shadow_trades WHERE status='closed'
   AND session='asia' AND decile=10 AND hour IN (6,7)""")
summarize(rows_b, "asia decile=10 hour 6-7 (the 'toxic' band)")

# ---- Claim 4: NY h20 neutral setup_fade ----
print("\n--- CLAIM 4: NY h20 neutral setup_fade ---")
rows = q("""SELECT * FROM shadow_trades WHERE status='closed'
   AND strategy='setup_fade' AND session='ny' AND hour=20 AND btc_trend_state='neutral'""")
summarize(rows, "setup_fade NY h20 neutral")

# ---- Claim 5: Asia h7 bear setup_fade ----
print("\n--- CLAIM 5: Asia h7 bear setup_fade ---")
rows = q("""SELECT * FROM shadow_trades WHERE status='closed'
   AND strategy='setup_fade' AND session='asia' AND hour=7 AND btc_trend_state='bear'""")
summarize(rows, "setup_fade Asia h7 bear")

# ---- Claim 6: Asia h3 bear asia_burst_fade ----
print("\n--- CLAIM 6: Asia h3 bear asia_burst_fade ---")
rows = q("""SELECT * FROM shadow_trades WHERE status='closed'
   AND strategy='asia_burst_fade' AND session='asia' AND hour=3 AND btc_trend_state='bear'""")
summarize(rows, "asia_burst_fade Asia h3 bear")
rows = q("""SELECT * FROM shadow_trades WHERE status='closed'
   AND strategy='asia_burst_fade' AND session='asia' AND hour=1 AND btc_trend_state='bear'""")
summarize(rows, "asia_burst_fade Asia h1 bear")

# ---- Claim 7: London h11 neutral setup_fade ----
print("\n--- CLAIM 7: London h11 neutral setup_fade ---")
rows = q("""SELECT * FROM shadow_trades WHERE status='closed'
   AND strategy='setup_fade' AND session='london' AND hour=11 AND btc_trend_state='neutral'""")
summarize(rows, "setup_fade London h11 neutral")

# ---- Claim 8: LUNCUSDT NY bear ----
print("\n--- CLAIM 8: LUNCUSDT NY bear ---")
rows = q("""SELECT * FROM shadow_trades WHERE status='closed'
   AND symbol='1000LUNCUSDT' AND session='ny' AND btc_trend_state='bear'""")
summarize(rows, "1000LUNCUSDT NY bear")

# ---- Claim 9: Late h23 bear burst_follow ----
print("\n--- CLAIM 9: Late h23 bear burst_follow ---")
rows = q("""SELECT * FROM shadow_trades WHERE status='closed'
   AND strategy='burst_follow' AND hour=23 AND btc_trend_state='bear'""")
summarize(rows, "burst_follow h23 bear")

# ---- Claim 10: Asia nony_momentum bear h2+h4 ----
print("\n--- CLAIM 10: Asia nony_momentum bear h2+h4 ---")
rows = q("""SELECT * FROM shadow_trades WHERE status='closed'
   AND strategy='nony_momentum' AND session='asia' AND hour=2 AND btc_trend_state='bear'""")
summarize(rows, "nony_momentum Asia h2 bear")
rows = q("""SELECT * FROM shadow_trades WHERE status='closed'
   AND strategy='nony_momentum' AND session='asia' AND hour=4 AND btc_trend_state='bear'""")
summarize(rows, "nony_momentum Asia h4 bear")

# ---- Claim 11: NY h17 bear setup_follow weekend ----
print("\n--- CLAIM 11: NY h17 bear setup_follow weekend ---")
rows = q("""SELECT * FROM shadow_trades WHERE status='closed'
   AND strategy='setup_follow' AND session='ny' AND hour=17 AND btc_trend_state='bear' AND is_weekend=1""")
summarize(rows, "setup_follow NY h17 bear weekend")

# ---- Claim 12: Monday Asia premium ----
print("\n--- CLAIM 12: Monday Asia premium ---")
def dow(ts):
    return datetime.fromisoformat(ts.replace('Z','+00:00')).strftime('%a')
rows_mon = q("""SELECT * FROM shadow_trades WHERE status='closed'
   AND strategy='asia_pump_short_4h' AND session='asia' AND btc_trend_state='neutral'""")
mon = [r for r in rows_mon if dow(r['entry_time'])=='Mon']
rest = [r for r in rows_mon if dow(r['entry_time'])!='Mon']
summarize(mon, "asia_pump_short_4h Monday neutral")
summarize(rest, "asia_pump_short_4h Tue-Fri neutral")

# ---- Claim 13: limit entries vs market ----
print("\n--- CLAIM 13: limit entries worse ---")
for base, lim, label in [
    ('ny_flush_buy_4h','ny_flush_buy_4h_limit15','NY'),
    ('asia_pump_short_4h','asia_pump_short_4h_limit15','Asia'),
]:
    rows_m = q("""SELECT * FROM shadow_trades WHERE status='closed' AND strategy=?""", base)
    rows_l = q("""SELECT * FROM shadow_trades WHERE status='closed' AND strategy=?""", lim)
    summarize(rows_m, f"{label} {base} market")
    summarize(rows_l, f"{label} {lim} limit")

c.close()
