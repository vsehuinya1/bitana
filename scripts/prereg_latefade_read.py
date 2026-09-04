#!/usr/bin/env python3
"""prereg_latefade_read.py — PREREG-LATEFADE forward reader (peek-guarded, validation-gated).

Prereg: research/RESEARCH_PLAN.md EOF, section 'PREREG-LATEFADE'
(registered 2026-09-01T06:29Z; counts-only until 2026-09-06T00:00Z; first R-read
Sun Sep 6; formal call Sun Sep 20; ONE extension max Oct 4).

READ-ONLY DB access (sqlite file:...?mode=ro). Never writes, never touches any
strategy/config/service. Read-only except this script's own repo commit.

Frozen population binding (prereg, verbatim):
  shadow_trades WHERE status='closed' AND strategy='late_fade' AND session='late'
  AND btc_trend_state='neutral' AND decile>=2 AND date(entry_time)>='2026-09-01',
  dedup(symbol, entry_time, side). NO side filter (LONG/SHORT both stay,
  descriptive split only), NO hour sub-filter (h22+h23 both stay), NO symbol cut.
Metric binding: R = pnl_atr / 12.0 (stop_atr=12.0 for late_fade); E = mean(R).

PEEK GUARD — two layers:
  1. Partition guard: rows with entry_time < '2026-09-01T06:29' (registration
     moment) are validation-only — reported separately, NEVER mixed into the
     forward read. Any such row inside the date-window is reported as a leak.
  2. Wall-clock peek policy: counts ONLY until 2026-09-06T00:00Z. The full R
     battery (sum R, E, PF, WR, pos-days, top-day share, side/storm R splits,
     decision-rule evaluation) prints from the first R-read (Sun Sep 6) on.

entry_time format: T-separated ISO with '+00:00' suffix
('2026-09-01T08:19:59.999000+00:00'). SQL window filters therefore use
date(entry_time) / strftime('%H', entry_time) ONLY — a space-separated literal
bound ('YYYY-MM-DD HH:MM') sorts below every same-date 'T...' value and silently
excludes ALL rows (documented false-zero trap; never used here). Python-side
comparisons use T-separated vs T-separated ordering (lexicographic ==
chronological) or entry_time[:10] day slices.

Usage:
  python3 scripts/prereg_latefade_read.py              # forward read (peek-gated)
  python3 scripts/prereg_latefade_read.py --validate   # backdated validation gate:
      runs the binding on the backdated population (entry_time < registration
      moment) and asserts the numbers match the prereg's disclosed in-sample
      basis within rounding (n=178, +113.3 ATR, E=+0.053 R/tr, PF 2.13,
      WR 60.1%, 22 days, dups 0, top-day 2026-08-30 ~70%). Exit 1 on drift
      => reader/DB drift, read VOID until reconciled.
"""
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

DB            = '/root/bitana/storage/signal_shadow.db'
STOP_ATR      = 12.0                 # metric binding: R = pnl_atr / 12.0 (late_fade)
REGISTERED_AT = '2026-09-01T06:29'   # registration moment (T-separated literal)
FORWARD_DATE  = '2026-09-01'         # frozen binding floor: date(entry_time) >= this
FIRST_R_READ  = '2026-09-06'         # counts-only until 2026-09-06T00:00Z (peek policy)
FORMAL_CALL   = '2026-09-20'
STORM_LEGS    = 40                   # storm = calendar day with >= 40 bound legs
BOUNDARY_FRAC = 0.10                 # disclose any day within 10% of the threshold
SESSION_HOURS = (22, 23)             # late session = h22-23Z (bar-close hour)

BASE_WHERE = ("status='closed' AND strategy='late_fade' AND session='late' "
              "AND btc_trend_state='neutral' AND decile>=2")

# Disclosed in-sample basis (prereg; read 2026-09-01 pre-registration) with
# "within rounding" tolerances for the validation gate.
BASIS = {
    'n':         (178,   2),
    'sum_atr':   (113.3, 1.5),
    'E':         (0.053, 0.004),
    'PF':        (2.13,  0.06),
    'WR':        (0.601, 0.015),
    'days':      (22,    1),
    'top_share': (0.695, 0.03),
}
TOP_DAY_EXPECTED = '2026-08-30'


def fetch_binding(cur, closed=True):
    """All binding-filter rows (NO date floor — partitioned Python-side)."""
    where = BASE_WHERE if closed else BASE_WHERE.replace("status='closed'", "status!='closed'")
    return cur.execute(f"""
        SELECT symbol, entry_time, side, pnl_atr, stop_atr, status,
               CAST(strftime('%H', entry_time) AS INT) AS hh
        FROM shadow_trades WHERE {where} ORDER BY entry_time""").fetchall()


def dedup(rows):
    """dedup(symbol, entry_time, side); first occurrence wins; report dup count."""
    seen, pop, dups, conflicts = set(), [], 0, 0
    for r in rows:
        k = (r['symbol'], r['entry_time'], r['side'])
        if k in seen:
            dups += 1
            prev = next(p for p in pop if (p['symbol'], p['entry_time'], p['side']) == k)
            if abs((prev['pnl_atr'] or 0.0) - (r['pnl_atr'] or 0.0)) > 1e-9:
                conflicts += 1
            continue
        seen.add(k)
        pop.append(r)
    return pop, dups, conflicts


def summarize(pop):
    """n, sum R, E, PF, WR, distinct days, pos-days, top-day (day + n + R + share)."""
    n = len(pop)
    Rs = [(r['pnl_atr'] or 0.0) / STOP_ATR for r in pop]
    sum_R = sum(Rs)
    gp = sum(x for x in Rs if x > 0)
    gl = -sum(x for x in Rs if x < 0)
    pf = (gp / gl) if gl > 0 else (float('inf') if gp > 0 else 0.0)
    wr = (sum(1 for x in Rs if x > 0) / n) if n else 0.0
    days = defaultdict(lambda: [0, 0.0])          # day -> [legs, sum R]
    for r, x in zip(pop, Rs):
        d = r['entry_time'][:10]
        days[d][0] += 1
        days[d][1] += x
    top_day, (top_n, top_R) = (max(days.items(), key=lambda kv: kv[1][1]) if days
                               else ('-', [0, 0.0]))
    return dict(n=n, sum_R=sum_R, sum_atr=sum_R * STOP_ATR,
                E=(sum_R / n) if n else 0.0, PF=pf, WR=wr,
                days=days, n_days=len(days),
                pos_days=sum(1 for _, (_, v) in days.items() if v > 0),
                top_day=top_day, top_n=top_n, top_R=top_R,
                top_share=(top_R / sum_R) if sum_R else float('nan'))


def side_split(pop):
    out = {}
    for side in ('SHORT', 'LONG'):
        sub = [(r['pnl_atr'] or 0.0) / STOP_ATR for r in pop if r['side'] == side]
        out[side] = (len(sub), (sum(sub) / len(sub)) if sub else 0.0)
    return out


def storm_split(pop):
    """storm = calendar day with >= STORM_LEGS bound legs (descriptive only).
    Boundary disclosure: any day within BOUNDARY_FRAC of the threshold."""
    legs = Counter(r['entry_time'][:10] for r in pop)
    dayR = defaultdict(float)
    for r in pop:
        dayR[r['entry_time'][:10]] += (r['pnl_atr'] or 0.0) / STOP_ATR
    lo = STORM_LEGS * (1 - BOUNDARY_FRAC)
    hi = STORM_LEGS * (1 + BOUNDARY_FRAC)
    storm = {d: (legs[d], dayR[d]) for d in legs if legs[d] >= STORM_LEGS}
    boundary_under = {d: (legs[d], dayR[d]) for d in legs if lo <= legs[d] < STORM_LEGS}
    boundary_over = {d: (legs[d], dayR[d]) for d in storm if legs[d] <= hi}
    s_n = sum(l for l, _ in storm.values())
    s_R = sum(v for _, v in storm.values())
    o_n = len(pop) - s_n
    o_R = sum(dayR.values()) - s_R
    return storm, boundary_under, boundary_over, \
        (s_n, s_R, (s_R / s_n) if s_n else 0.0), \
        (o_n, o_R, (o_R / o_n) if o_n else 0.0)


def null_regime_audit(cur):
    """NULL btc_trend_state, late_fade late-session rows strategy-wide
    (excluded-with-count). A NULL row on an in-window day => READ VOID."""
    rows = cur.execute("""SELECT entry_time, status, symbol, side, decile
        FROM shadow_trades WHERE strategy='late_fade' AND session='late'
        AND btc_trend_state IS NULL ORDER BY entry_time""").fetchall()
    void = [r for r in rows if r['entry_time'][:10] >= FORWARD_DATE]
    return rows, void


def tag_integrity(pop):
    """session='late' must imply h22-23 (strftime('%H', entry_time))."""
    bad = [r for r in pop if r['hh'] not in SESSION_HOURS]
    return bad


def unit_guard(pop):
    stops = sorted(r['stop_atr'] for r in pop if r['stop_atr'] is not None)
    if not stops:
        return 'no stop_atr values'
    med, mn, mx = stops[len(stops) // 2], stops[0], stops[-1]
    flag = '' if med == STOP_ATR else f'  *** MED != {STOP_ATR} — METRIC DRIFT, investigate ***'
    return f'min={mn} med={med} max={mx}{flag}'


def peeking_now():
    return datetime.now(timezone.utc) < datetime(2026, 9, 6, tzinfo=timezone.utc)


def print_common(pop, dups, conflicts, leak, null_rows, void_rows, open_n):
    print(f"binding rows (dedup symbol+entry_time+side): n={len(pop)} dups={dups}"
          + (f" (CONFLICTING-VALUE dups={conflicts})" if conflicts else ""))
    print(f"peek-guard leak rows inside date window "
          f"(entry_time < {REGISTERED_AT}, validation-only, excluded): {len(leak)}")
    if leak:
        for r in leak:
            print(f"  LEAK: {r['symbol']} {r['side']} {r['entry_time']} pnl_atr={r['pnl_atr']}")
    print(f"accruing open (not yet closed) rows, binding filters, forward window: {open_n}")
    print(f"NULL btc_trend_state audit (late_fade late-session, strategy-wide): "
          f"{len(null_rows)} row(s) all-time — excluded-with-count"
          + (f"; dates: {sorted({r['entry_time'][:10] for r in null_rows})}" if null_rows else ""))
    if void_rows:
        print(f"*** READ VOID ***: {len(void_rows)} NULL-regime row(s) on in-window day(s) "
              f"(>= {FORWARD_DATE}): "
              f"{[(r['symbol'], r['entry_time']) for r in void_rows]}")
    else:
        print(f"VOID check (NULL-regime row on an in-window day >= {FORWARD_DATE}): CLEAN")
    bad = tag_integrity(pop)
    print(f"session/hour tag check (strftime('%H') in {SESSION_HOURS}): "
          + (f"OK (n mismatches=0)" if not bad else f"*** {len(bad)} MISMATCH(ES): "
             f"{[(r['symbol'], r['entry_time'], r['hh']) for r in bad]} ***"))
    print(f"stop_atr unit guard (expect {STOP_ATR}): {unit_guard(pop)}")


def print_storm(storm, boundary_under, boundary_over, s, o):
    s_n, s_R, s_E = s
    o_n, o_R, o_E = o
    fmt = lambda dd: (sorted((d, l, round(v, 2)) for d, (l, v) in dd.items()) if dd else 'none')
    print(f"storm split (storm = day with >= {STORM_LEGS} bound legs):")
    print(f"  storm:     n={s_n} sumR={s_R:+.2f} E={s_E:+.4f}  days={fmt(storm)}")
    print(f"  non-storm: n={o_n} sumR={o_R:+.2f} E={o_E:+.4f}")
    print(f"  boundary disclosure (within {BOUNDARY_FRAC:.0%} of {STORM_LEGS} legs): "
          f"under={fmt(boundary_under)} near-threshold-storm={fmt(boundary_over)}")


def run_forward(cur):
    rows = cur.execute(f"""
        SELECT symbol, entry_time, side, pnl_atr, stop_atr, status,
               CAST(strftime('%H', entry_time) AS INT) AS hh
        FROM shadow_trades WHERE {BASE_WHERE} AND date(entry_time) >= ?
        ORDER BY entry_time""", (FORWARD_DATE,)).fetchall()
    open_n = cur.execute(f"""
        SELECT COUNT(*) FROM shadow_trades
        WHERE {BASE_WHERE.replace("status='closed'", "status!='closed'")}
        AND date(entry_time) >= ? AND entry_time >= ?""",
        (FORWARD_DATE, REGISTERED_AT)).fetchone()[0]
    leak = [r for r in rows if r['entry_time'] < REGISTERED_AT]
    pop, dups, conflicts = dedup([r for r in rows if r['entry_time'] >= REGISTERED_AT])
    null_rows, void_rows = null_regime_audit(cur)
    st = summarize(pop)
    ss = side_split(pop)
    storm, bu, bo, s, o = storm_split(pop)

    print(f"=== PREREG-LATEFADE forward read (frozen binding; "
          f"date(entry_time)>='{FORWARD_DATE}') ===")
    print(f"[peek guard: forward stats NEVER include rows with entry_time < "
          f"{REGISTERED_AT} (registration moment) — those are validation-only]")
    print_common(pop, dups, conflicts, leak, null_rows, void_rows, open_n)
    print()
    if peeking_now():
        print("[peek policy: R SUPPRESSED — counts ONLY until 2026-09-06T00:00Z; "
              "first R-read Sun Sep 6]")
        print(f"counts: n={st['n']}  distinct days={st['n_days']}  "
              f"legs/day={ {d: l for d, (l, _) in sorted(st['days'].items())} }")
        print(f"side split (counts only): SHORT n={ss['SHORT'][0]}  LONG n={ss['LONG'][0]}")
        print(f"storm split (counts only, by legs): "
              f"storm days={sorted((d, l) for d, (l, _) in storm.items()) or 'none'}  "
              f"boundary under={sorted((d, l) for d, (l, _) in bu.items()) or 'none'}")
        print("counts only until 2026-09-06T00:00Z (prereg peek policy) — "
              "sum R/E/PF/WR/top-day-share suppressed.")
        print(f"PREREG-LATEFADE: first R-read Sun {FIRST_R_READ}; formal call Sun "
              f"{FORMAL_CALL}; ONE extension max Oct 4.")
        return 0

    print(f"--- full read (first R-read {FIRST_R_READ}+) ---")
    print(f"n={st['n']}  sumR={st['sum_R']:+.2f}  E={st['E']:+.4f} R/tr  "
          f"PF={st['PF']:.2f}  WR={st['WR']:.1%}  days={st['n_days']}  "
          f"pos-days={st['pos_days']}  legs/day={st['n'] / st['n_days']:.1f}"
          if st['n'] else "no closed rows yet")
    if st['n']:
        print(f"top-day: {st['top_day']} n={st['top_n']} R={st['top_R']:+.2f} "
              f"share={st['top_share']:.1%} of sumR")
        print(f"side split: SHORT n={ss['SHORT'][0]} E={ss['SHORT'][1]:+.4f}  "
              f"LONG n={ss['LONG'][0]} E={ss['LONG'][1]:+.4f}")
        print_storm(storm, bu, bo, s, o)
        floors_n = st['n'] >= 30
        floors_d = st['n_days'] >= 5
        floors_t = st['top_share'] <= 0.40
        floors_met = floors_n and floors_d and floors_t
        print(f"floors: n>=30 {'PASS' if floors_n else 'FAIL'} | days>=5 "
              f"{'PASS' if floors_d else 'FAIL'} | top-day<=40% "
              f"{'PASS' if floors_t else 'FAIL'}")
        storm_share = s[1] / st['sum_R'] if st['sum_R'] else float('nan')
        kills = []
        if st['E'] < 0 and floors_n and floors_d:
            kills.append('E<0 with n/days floors met')
        if not floors_t:
            kills.append('top-day > 40% of sumR')
        if storm_share == storm_share and storm_share >= 0.80 and o[2] <= 0:
            kills.append('storm-dominance (storm >=80% sumR AND non-storm E<=0)')
        promote = floors_met and st['E'] >= 0.05 and not kills
        verdict = ('PROMOTE-CRITERIA MET (-> G1 eval + exec review; NOT auto-wire)'
                   if promote else ('KILL: ' + '; '.join(kills)) if kills
                   else ('ACCUMULATING (floors unmet)' if not floors_met
                         else 'INCONCLUSIVE'))
        print(f"decision rule: E>=+0.05 & floors -> PROMOTE | E<0(floors) / "
              f"top-day>40% / storm-dominance -> KILL | else INCONCLUSIVE")
        print(f"criteria evaluation: {verdict}  [formal call Sun {FORMAL_CALL}; "
              f"ONE extension max Oct 4]")
        print("storm tag is descriptive only — NO decision weight (prereg).")
    return 0


def run_validate(cur):
    rows = fetch_binding(cur)                       # binding filters, NO date floor
    backdated = [r for r in rows if r['entry_time'] < REGISTERED_AT]
    forward_rows = [r for r in rows
                    if r['entry_time'] >= REGISTERED_AT
                    and r['entry_time'][:10] >= FORWARD_DATE]
    pop, dups, conflicts = dedup(backdated)
    st = summarize(pop)
    null_rows, void_rows = null_regime_audit(cur)
    storm, bu, bo, s, o = storm_split(pop)

    print(f"=== PREREG-LATEFADE validation gate (backdated population, "
          f"entry_time < {REGISTERED_AT}) ===")
    print(f"raw backdated binding rows: {len(backdated)}")
    print(f"n={st['n']}  sumATR={st['sum_atr']:+.1f}  sumR={st['sum_R']:+.2f}  "
          f"E={st['E']:+.4f} R/tr  PF={st['PF']:.2f}  WR={st['WR']:.1%}  "
          f"days={st['n_days']}  pos-days={st['pos_days']}  dups={dups}")
    print(f"top-day: {st['top_day']} n={st['top_n']} R={st['top_R']:+.2f} "
          f"share={st['top_share']:.1%}")
    top2 = sorted(st['days'].items(), key=lambda kv: -kv[1][1])[:2]
    print(f"top-2 days: {[(d, l, round(v, 1)) for d, (l, v) in top2]}")
    print_storm(storm, bu, bo, s, o)
    ss = side_split(pop)
    print(f"side split: SHORT n={ss['SHORT'][0]} E={ss['SHORT'][1]:+.4f}  "
          f"LONG n={ss['LONG'][0]} E={ss['LONG'][1]:+.4f}")
    print(f"NULL-regime audit: {len(null_rows)} strategy-wide (late_fade late "
          f"session); in-window NULL rows: {len(void_rows)} "
          f"({'READ VOID' if void_rows else 'CLEAN'})")
    print()
    checks = [
        ('n',            st['n'],                 BASIS['n'][0],         BASIS['n'][1]),
        ('sum ATR',      st['sum_atr'],           BASIS['sum_atr'][0],   BASIS['sum_atr'][1]),
        ('E R/tr',       st['E'],                 BASIS['E'][0],         BASIS['E'][1]),
        ('PF',           st['PF'],                BASIS['PF'][0],        BASIS['PF'][1]),
        ('WR',           st['WR'],                BASIS['WR'][0],        BASIS['WR'][1]),
        ('distinct days', st['n_days'],           BASIS['days'][0],      BASIS['days'][1]),
        ('dups',         dups,                    0,                     0),
        ('top-day id',   st['top_day'],           TOP_DAY_EXPECTED,      None),
        ('top-day share', st['top_share'],        BASIS['top_share'][0], BASIS['top_share'][1]),
    ]
    failures = 0
    for name, actual, expect, tol in checks:
        if tol is None:
            ok = actual == expect
            line = f"  {name:15s} actual={actual!s:12s} expected={expect!s:10s} " \
                   f"{'PASS' if ok else 'FAIL'}"
        elif tol == 0:
            ok = actual == expect
            line = f"  {name:15s} actual={actual!s:12s} expected={expect!s:10s} " \
                   f"{'PASS' if ok else 'FAIL'}"
        else:
            ok = abs(actual - expect) <= tol
            line = f"  {name:15s} actual={actual:<12.4f} expected={expect:<10.4f} " \
                   f"tol=±{tol} {'PASS' if ok else 'FAIL'}"
        print(line)
        failures += 0 if ok else 1
    if conflicts:
        print(f"  conflicting-value dups: {conflicts} FAIL")
        failures += 1
    print()
    if failures:
        print(f"VALIDATION GATE FAILED ({failures} check(s)) — reader/DB drift from "
              f"the prereg's disclosed in-sample basis; treat read as VOID until "
              f"reconciled. Do NOT commit a passing smoke-test claim.")
        return 1
    print("VALIDATION GATE PASSED — reader reproduces the prereg's disclosed "
          "in-sample basis within rounding (peek-mode backdated validation, "
          "LONDHOLD/WKNDNY pattern).")
    print(f"forward-window binding rows present (>= {REGISTERED_AT}, "
          f"date >= {FORWARD_DATE}): {len(forward_rows)} — validation-only partition "
          f"untouched.")
    return 0


def main(argv):
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    try:
        if '--validate' in argv:
            return run_validate(cur)
        return run_forward(cur)          # default ('--forward' alias, no-op)
    finally:
        con.close()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
