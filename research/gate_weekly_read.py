#!/usr/bin/env python3
"""gate_weekly_read.py — G0 forward-read on the gate cluster.
Reads VIEW gate_g0 (frozen thresholds, see wire_gates.py) restricted to
FORWARD_ONLY_AT and later. Floors per cell: n>=15 AND >=5 distinct days.
Usage: python3 research/gate_weekly_read.py [--all]   (--all includes backfill)
"""
import sqlite3, sys

DB = '/root/bitana/storage/signal_shadow.db'
FORWARD_ONLY_AT = '2026-08-22T14:10'   # wiring moment; earlier rows are backfill
ARMS = [
    ("arm_adx35",         "SHORT blocked when BTC ADX>=35"),
    ("arm_rvolq1",        "SHORT brake when rvol24<=q1(0.0648)"),
    ("arm_oi_p1",         "SHORT fade-block when OI d30m>=+1%"),
    ("arm_fund1bp",       "asia_pump% SHORT block at funding>=1bp"),
    ("arm_late_long",     "LONG block in late session"),
    ("arm_burst_s",       "burst_follow SHORT book removal"),
]
con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
c = con.cursor()
scope = "1=1" if "--all" in sys.argv else f"entry_time >= '{FORWARD_ONLY_AT}'"

print(f"=== G0 gate-cluster forward read (rows since {FORWARD_ONLY_AT}) ===")
n_all, S_all = c.execute(f"SELECT COUNT(*), COALESCE(SUM(pnl_atr),0) FROM gate_g0 WHERE is_closed=1 AND {scope}").fetchone()
print(f"closed in window: n={n_all} sum={S_all:+.1f} E={S_all/n_all if n_all else 0:+.4f}\n")
print(f"{'arm':14s} {'n':>6} {'days':>5} {'sum':>9} {'E':>9} {'floors':>12} verdict")
for col, desc in ARMS:
    n, S, d = c.execute(f"""SELECT COUNT(*), COALESCE(SUM(pnl_atr),0),
        COUNT(DISTINCT substr(entry_time,1,10)) FROM gate_g0
        WHERE is_closed=1 AND {col}=1 AND {scope}""").fetchone()
    E = S/n if n else 0.0
    ok_n, ok_d = n >= 15, d >= 5
    floors = f"n{'✓' if ok_n else '✗'} d{'✓' if ok_d else '✗'}"
    if not (ok_n and ok_d):
        verdict = "ACCUMULATING"
    elif E <= -0.30:
        verdict = "GATE CONFIRMED (blocked pool bleeds)"
    elif E >= 0.30:
        verdict = "GATE FALSIFIED (pool was profitable)"
    else:
        verdict = "INCONCLUSIVE"
    print(f"{col:14s} {n:>6} {d:>5} {S:>+9.1f} {E:>+9.4f} {floors:>12} {verdict}")
print("\nkill criteria: arm confirmed if E<=-0.30 with floors met -> promote gate to live config proposal;")
print("arm falsified if E>=+0.30 with floors met -> drop from cluster. Else next loop.")
con.close()
