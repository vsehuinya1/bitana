#!/usr/bin/env python3
"""gate_weekly_read.py — G0 forward-read on the gate cluster.
Reads VIEW gate_g0 (frozen thresholds, see wire_gates.py) restricted to
FORWARD_ONLY_AT and later. Floors per cell: n>=15 AND >=5 distinct days.
Also emits the PREREG-AGESHORT block (RESEARCH_PLAN.md EOF, commit fadc44f):
counts-only until FIRST_R_READ, then full paired deltas per prereg criteria.
Usage: python3 research/gate_weekly_read.py [--all]   (--all includes backfill
for the gate_g0 cluster ONLY; the PREREG-AGESHORT window is always forward-only)
"""
import sqlite3, sys
from datetime import datetime, timezone

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

# ---------------------------------------------------------------------------
# PREREG-AGESHORT — registered 2026-08-23 (commit fadc44f), RESEARCH_PLAN.md EOF.
# Cell T : SHORT, neutral, age 6-12 bars(4H), ny session, h14-21
# C1     : same SHORTs at age OUTSIDE [6,12]      M : LONG inside cell T
# Promote: dAvg(T-C1)<=-0.30 AND M_avg>=+0.20 AND C-beats-B>=+0.10/trade
# Kill   : dAvg>=0 | M<=-0.30 | top-day>40% | vol-control erases | n<15@Sep20
# Peek   : counts ONLY before 2026-09-06 (no R values).
# ---------------------------------------------------------------------------
AGESHORT_FROM = '2026-08-24T00:00'
FIRST_R_READ  = '2026-09-06'
T_WHERE  = ("side='SHORT' AND btc_trend_state='neutral' AND btc_regime_age_bars "
            "BETWEEN 6 AND 12 AND session='ny' AND hour BETWEEN 14 AND 21")
C1_WHERE = ("side='SHORT' AND btc_trend_state='neutral' AND session='ny' AND "
            "hour BETWEEN 14 AND 21 AND (btc_regime_age_bars<6 OR btc_regime_age_bars>12)")
M_WHERE  = ("side='LONG' AND btc_trend_state='neutral' AND btc_regime_age_bars "
            "BETWEEN 6 AND 12 AND session='ny' AND hour BETWEEN 14 AND 21")

def cell(where):
    return c.execute(f"""SELECT COUNT(*), COALESCE(SUM(pnl_atr),0),
        COUNT(DISTINCT substr(entry_time,1,10)) FROM shadow_trades
        WHERE status='closed' AND entry_time>='{AGESHORT_FROM}' AND {where}""").fetchone()

print(f"\n=== PREREG-AGESHORT forward read (window >= {AGESHORT_FROM}; always forward-only) ===")
nT, sT, dT = cell(T_WHERE)
nC, sC, dC = cell(C1_WHERE)
nM, sM, dM = cell(M_WHERE)
peeking = datetime.now(timezone.utc) < datetime.fromisoformat(FIRST_R_READ + 'T00:00:00+00:00')

# unit guard: 4H bars sanity on the pooled SHORT population (x12 bug tripwire)
ages = [r[0] for r in c.execute(f"""SELECT btc_regime_age_bars FROM shadow_trades
    WHERE status='closed' AND entry_time>='{AGESHORT_FROM}' AND side='SHORT'
    AND btc_trend_state='neutral' AND session='ny' AND hour BETWEEN 14 AND 21
    AND btc_regime_age_bars IS NOT NULL""")]
if len(ages) >= 20:
    ages.sort()
    p95, mx = ages[int(0.95*len(ages))-1], ages[-1]
    med = ages[len(ages)//2]
    bad = p95 < 6 or mx > 3000
    print(f"unit guard (4H bars): med={med} p95={p95} max={mx} -> {'TRIP! INVESTIGATE UNITS' if bad else 'ok'}")
else:
    print(f"unit guard: skipped (only {len(ages)} aged rows)")

hdr = "R suppressed" if peeking else "full"
print(f"[peek policy: {hdr}]")
rows = [("cell T", nT, dT, sT), ("ctrl C1", nC, dC, sC), ("mirror M", nM, dM, sM)]
if peeking:
    print(f"{'grp':9s} {'n':>6} {'days':>5}")
    for g, n, d, _ in rows:
        print(f"{g:9s} {n:>6} {d:>5}")
    need_n = max(0, 30 - nT)
    print("floors pending: counts only until 2026-09-06. "
          + (f"T needs {need_n} more trades for n-floor." if need_n else "T n-floor met."))
else:
    print(f"{'grp':9s} {'n':>6} {'days':>5} {'sum':>9} {'E':>9}")
    for g, n, d, s in rows:
        print(f"{g:9s} {n:>6} {d:>5} {s:>+9.1f} {s/n if n else 0:>+9.4f}")
    if nT and nC:
        dTC = sT/nT - sC/nC
        mE = sM/nM if nM else float('nan')
        # top-day share of arm-C avoided PnL (worst day of T)
        wd = c.execute(f"""SELECT substr(entry_time,1,10), SUM(pnl_atr) FROM shadow_trades
            WHERE status='closed' AND entry_time>='{AGESHORT_FROM}' AND {T_WHERE}
            GROUP BY 1 ORDER BY 2 ASC LIMIT 1""").fetchone()
        top_share = abs(wd[1]/sT) if (wd and sT) else float('nan')
        # rvol terciles pooled over T+C1; effect must survive middle tercile
        rv = sorted(r[0] for r in c.execute(f"""SELECT btc_realized_vol_24h FROM shadow_trades
            WHERE status='closed' AND entry_time>='{AGESHORT_FROM}'
            AND ({T_WHERE} OR {C1_WHERE}) AND btc_realized_vol_24h IS NOT NULL"""))
        q1, q2 = rv[len(rv)//3], rv[2*len(rv)//3] if rv else (None, None)
        def tE(lo, hi):
            r_ = c.execute(f"""SELECT AVG(pnl_atr) FROM shadow_trades WHERE status='closed'
                AND entry_time>='{AGESHORT_FROM}' AND {T_WHERE}
                AND btc_realized_vol_24h>{lo} AND btc_realized_vol_24h<={hi}""").fetchone()
            return r_[0]
        midT = tE(q1, q2) if rv else None
        print(f"dAvg(T-C1)={dTC:+.4f}  M_E={mE:+.4f}  top-day={wd[0] if wd else '-'} "
              f"{top_share:.0%}  rvol-mid-T E={midT:+.4f}" if midT is not None else
              f"dAvg(T-C1)={dTC:+.4f}  M_E={mE:+.4f}  top-day={wd[0] if wd else '-'} {top_share:.0%}")
        floors_ok = nT >= 30 and dT >= 5
        kills = []
        if dTC >= 0: kills.append("dAvg>=0")
        if nM and mE <= -0.30: kills.append("LONG also toxic (fold to whole-cell)")
        if top_share == top_share and top_share > 0.40: kills.append("top-day>40%")
        promotes = floors_ok and not kills and dTC <= -0.30 and (nM and mE >= 0.20)
        verdict = ("KILL: " + "; ".join(kills)) if kills else (
            "PROMOTE-CRITERIA MET (check C-vs-B +0.10 manually)" if promotes
            else ("ACCUMULATING (floors unmet)" if not floors_ok else "INCONCLUSIVE"))
        print(f"verdict: {verdict}")
        print("promote = all floors + dAvg<=-0.30 + M_E>=+0.20 (+C beats B by >=0.10/trade, manual pair)")
    else:
        print("insufficient rows for delta.")

print("\nPREREG-AGESHORT: formal call Sun Sep 20; one extension max (Oct 4); dead-sample park below.")
con.close()
