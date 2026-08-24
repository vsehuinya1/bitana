#!/usr/bin/env python3
"""gate_weekly_read.py — G0 forward-read on the gate cluster.
Reads VIEW gate_g0 (frozen thresholds, see wire_gates.py) restricted to
FORWARD_ONLY_AT and later. Floors per cell: n>=15 AND >=5 distinct days.
Also emits the PREREG-AGESHORT block (RESEARCH_PLAN.md EOF, commit fadc44f)
and the PREREG-LONDHOLD block (commit post-435433e): counts-only until
FIRST_R_READ, then full paired replay deltas per prereg criteria.
Usage: python3 research/gate_weekly_read.py [--all]   (--all includes backfill
for the gate_g0 cluster ONLY; prereg windows are always forward-only)
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

# ---------------------------------------------------------------------------
# PREREG-LONDHOLD — registered 2026-08-24T05:45Z, BEFORE first eligible entry
# (London h8 => earliest possible 2026-08-24T08:0xZ; zero look-back).
# Pop  : burst_follow/london/LONG/bull, hour 8-13 UTC, Mon-Fri, dedup;
#        liq_imb>=0.5 ASSERTED (guard, not filter — held on all 193 in-sample).
# Arms : LIVE = pnl_atr/stop_atr (canonical R)
#        T1   = replay SL10, NO TP, time-exit at 36th 5m bar close (180m)  [primary]
#        T2   = replay SL10/TP3, hold 48b (240m) if entry hour 8-11 else 24b [secondary]
#        noTP@120m & TP2@120m printed DESCRIPTIVE ONLY (no promotion path).
# Replay semantics FROZEN to validated harness (commit 435433e): entry index =
# bisect_right(bar_opens, entry_ms); collision order STOP-FIRST; time exit =
# Nth bar close; replay ATR-units / stop_atr -> R. Baseline validation gate:
# TP3@6bar replay must match stored R on >=90% of forward pairs (|dR|<0.05),
# else read VOID (counts only). Incomplete pairs excluded, must be <=10%.
# Promote: meanDelta >= +0.05 R/tr AND floors(n_pairs>=30, days>=5,
#          top-day<=40% of sumD, LIVE-leg E>=0) AND validation gate.
#          T2 additionally requires h8-11 cell pairs >= 20.
# Kill   : meanDelta <= -0.05 R/tr with floors met -> keep live, park question.
#          LIVE E<0 with floors -> ARM-LEVEL REGRESSION, exit tuning moot.
# Peek   : counts ONLY before 2026-09-06. First R-read Sep 6; formal call
#          Sep 20; ONE extension max (Oct 4).
# Power note: in-sample point estimate meanD(T1)=+0.028 R/tr < promote bar.
# Expected outcome absent a truly larger effect is NO-PROMOTION (keep live);
# this prereg catches a real >=+0.05 edge or kills the leaderboard — it is
# NOT sized to confirm the in-sample estimate.
# ---------------------------------------------------------------------------
import os as _os, json as _json, time as _time, urllib.request as _ureq
from collections import defaultdict as _dd, Counter as _ddc

LH_FROM     = '2026-08-24T00:00'
LH_FIRST_R  = '2026-09-06'
LH_BARS     = _os.environ.get('LONDHOLD_BARS', '/tmp/london_bull_bars.json')
SL, TP      = 10.0, 3.0
con.row_factory = sqlite3.Row   # LONDHOLD block uses keyed access
c.row_factory = sqlite3.Row     # cursor predates this block -> must set per-cursor

def _ts(s): return int(datetime.fromisoformat(s).timestamp() * 1000)

def _fetch(sym, a, b):
    out, cur = [], a
    while cur < b:
        url = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=5m&startTime=%d&limit=1500" % (sym, cur))
        raw = None
        for att in range(3):
            try:
                with _ureq.urlopen(url, timeout=20) as resp: raw = _json.loads(resp.read())
                break
            except Exception:
                if att == 2: raise
                _time.sleep(2)
        if not raw: break
        out.extend((int(k[0]), float(k[2]), float(k[3]), float(k[4])) for k in raw)
        nxt = int(raw[-1][0]) + 300_000
        if nxt <= cur: break
        cur = nxt
        if len(raw) < 1500: break
        _time.sleep(0.05)
    return sorted(set(out))

def _replay(bars, entry_ms, ep, atr, sl, tp, n_bars):
    import bisect as _b
    i = _b.bisect_right([x[0] for x in bars], entry_ms); w = 0
    for (ot, h, l, cl) in bars[i:]:
        w += 1
        if l <= ep - sl * atr: return -sl, "sl", w
        if tp is not None and h >= ep + tp * atr: return tp, "tp", w
        if w == n_bars: return (cl - ep) / atr, "time", w
    return None, "incomplete", w

print(f"\n=== PREREG-LONDHOLD forward read (window >= {LH_FROM}; always forward-only) ===")
_lh = c.execute("""SELECT symbol, side, entry_time, entry_price, atr, pnl_atr, stop_atr,
    hour, liq_imb, CAST(strftime('%w', entry_time) AS INT) dow FROM shadow_trades
    WHERE strategy='burst_follow' AND session='london' AND side='LONG'
    AND btc_trend_state='bull' AND entry_time>=?""", (LH_FROM,)).fetchall()
_n_open = c.execute(f"""SELECT COUNT(*) FROM shadow_trades
    WHERE strategy='burst_follow' AND session='london' AND side='LONG'
    AND btc_trend_state='bull' AND status!='closed' AND entry_time>='{LH_FROM}'""").fetchone()[0]
_seen, pop, _ximb = set(), [], 0
for r in _lh:
    k = (r["symbol"], r["entry_time"], r["side"])
    if k in _seen: continue
    _seen.add(k)
    if r["dow"] not in (1, 2, 3, 4, 5): continue
    if r["liq_imb"] is not None and r["liq_imb"] < 0.5: _ximb += 1; continue
    pop.append(r)
_days = len({r["entry_time"][:10] for r in pop})
_syms = len({r["symbol"] for r in pop})
print(f"eligible closed: n={len(pop)} days={_days} syms={_syms} open={_n_open} excl_imb_guard={_ximb}")
if _ximb: print("GUARD: imb<0.5 rows excluded — investigate live gate drift.")
_sa = sorted(r["stop_atr"] or 10.0 for r in pop)
if _sa: print(f"unit guard (stop_atr): med={_sa[len(_sa)//2]} min={_sa[0]} max={_sa[-1]}")

peeking_lh = datetime.now(timezone.utc) < datetime.fromisoformat(LH_FIRST_R + 'T00:00:00+00:00')
if peeking_lh or not pop:
    print("[peek policy: R suppressed]" if peeking_lh else "[peek policy: no closed pairs yet]")
    hh = _ddc(int(r["hour"]) for r in pop if r["hour"] is not None)
    print("hour hist:", dict(sorted(hh.items())) if hh else "-")
    if peeking_lh: print("counts only until 2026-09-06.")
else:
    try:
        store = {}
        if _os.path.exists(LH_BARS):
            store = {s: (v[0], v[1], [tuple(b) for b in v[2]]) for s, v in _json.load(open(LH_BARS)).items()}
        bysym = _dd(list)
        for r in pop: bysym[r["symbol"]].append(r)
        for sym, trs in bysym.items():
            lo = min(_ts(t["entry_time"]) for t in trs) - 120_000
            hi = max(_ts(t["entry_time"]) for t in trs) + 52 * 300_000
            if sym in store and store[sym][0] <= lo and store[sym][1] >= hi: continue
            if sym in store and store[sym][0] <= lo:
                s0, s1, bars = store[sym]
                store[sym] = (s0, hi, sorted(set(bars + _fetch(sym, s1 + 300_000, hi))))
            else:
                store[sym] = (lo, hi, _fetch(sym, lo, hi))
        _json.dump({s: (v[0], v[1], [list(b) for b in v[2]]) for s, v in store.items()}, open(LH_BARS, "w"))
    except Exception as e:
        print(f"BAR FETCH FAILED ({e}) — R-read deferred, counts above stand."); pop = []
    if pop:
        # validation gate: TP3@6bar baseline vs stored canonical R
        ok = inc = 0
        for r in pop:
            v, _, _ = _replay(store[r["symbol"]][2], _ts(r["entry_time"]), r["entry_price"], r["atr"], SL, TP, 6)
            if v is None: inc += 1; continue
            if abs(v / (r["stop_atr"] or 10.0) - r["pnl_atr"] / (r["stop_atr"] or 10.0)) < 0.05: ok += 1
        vr, ir = ok / len(pop), inc / len(pop)
        print(f"validation gate: {ok}/{len(pop)} match ({vr:.0%}), incomplete={inc} ({ir:.0%})")
        def arm(tpv, nb_fn, label):
            ds = []
            for r in pop:
                v, _, _ = _replay(store[r["symbol"]][2], _ts(r["entry_time"]), r["entry_price"], r["atr"], SL, tpv, nb_fn(int(r["hour"])))
                if v is None: continue
                ds.append((r["entry_time"][:10], int(r["hour"]),
                           v / (r["stop_atr"] or 10.0) - r["pnl_atr"] / (r["stop_atr"] or 10.0)))
            return label, ds
        eL = sum(r["pnl_atr"] / (r["stop_atr"] or 10.0) for r in pop) / len(pop)
        arms = [arm(None, lambda h: 36, "T1 noTP@180m"),
                arm(TP, lambda h: 48 if h <= 11 else 24, "T2 cond-hold"),
                arm(None, lambda h: 24, "desc noTP@120m"),
                arm(2.0, lambda h: 24, "desc TP2@120m")]
        print(f"LIVE leg: n={len(pop)} E={eL:+.4f} R/tr")
        print(f"{'arm':16s} {'n':>5} {'meanD':>8} {'sumD':>8} {'wrD':>5} {'days':>5} {'top-day':>8} verdict")
        for label, ds in arms:
            desc = label.startswith("desc")
            n = len(ds); md = sum(d for _, _, d in ds) / n if n else 0.0
            sd = sum(d for _, _, d in ds)
            dys = _dd(float)
            for dy, _, d in ds: dys[dy] += d
            td, tv = (max(dys.items(), key=lambda kv: kv[1]) if sd > 0 else ("-", 0)) if dys else ("-", 0)
            share = tv / sd if sd > 0 else float('nan')
            fl = n >= 30 and len(dys) >= 5
            if desc:
                verd = "descriptive (no path)"
            elif vr < 0.90 or ir > 0.10:
                verd = "READ VOID (gate fail)"
            elif not fl:
                verd = "ACCUMULATING"
            elif md <= -0.05:
                verd = "KILL (keep live)"
            elif eL < 0:
                verd = "ARM-REGRESSION (tuning moot)"
            elif md >= 0.05 and not (share == share and share > 0.40):
                extra_ok = True
                if label.startswith("T2"):
                    ncell = sum(1 for _, h, _ in ds if 8 <= h <= 11)
                    extra_ok = ncell >= 20
                    verd = "PROMOTE-CRITERIA MET" + ("" if extra_ok else f" (early-cell n={ncell}<20)")
                else:
                    verd = "PROMOTE-CRITERIA MET"
            else:
                verd = "INCONCLUSIVE"
            print(f"{label:16s} {n:>5} {md:>+8.4f} {sd:>+8.2f} "
                  f"{100 * sum(1 for _, _, d in ds if d > 0) / n if n else 0:>4.0f}% {len(dys):>5} "
                  f"{(td + f' {share:.0%}') if td != '-' else '-':>8} {verd}")
        print("promote = floors + meanD>=+0.05R/tr + LIVE E>=0 + gate; T2 needs h8-11 pairs>=20.")
        print("PREREG-LONDHOLD: formal call Sun Sep 20; one extension max (Oct 4).")

con.close()
