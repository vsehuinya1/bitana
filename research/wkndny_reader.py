#!/usr/bin/env python3
"""PREREG-WKNDNY reader of record — registered 2026-08-25T06:45Z, RESEARCH_PLAN.md L778.

Frozen population: shadow_trades WHERE status='closed' AND strategy='ny_flush_buy_4h'
AND btc_trend_state='bull' AND CAST(strftime('%w',entry_time) AS INT) IN (0,6)
AND entry_time >= '2026-08-29T00:00', dedup(symbol, entry_time, side).
LONG assert (133/133 historical; violations excluded + counted).
NULL-regime rows on in-window days => read VOID (counts-only until re-audited).
Metric: R = pnl_atr / stop_atr; E = mean(R).
Floors: n>=30, distinct weekend days>=5, top-day<=40% of sumR.
Decision: PROMOTE E>=+0.05 at floors -> G1 eval + exec review (NOT auto-wire);
KILL E<0 with n>=30 & days>=5 -> park permanently; else INCONCLUSIVE -> keep exclusion.

Modes:
  --validate   backdated validation gate: reproduce the IN-SAMPLE basis
               (Jul1->Aug-28 bull weekends, registered: n=65, E=+0.114, top-day 57%)
  --peek       counts ONLY (pre-formal policy)
  (default)    full read, formal decision rule
"""
import argparse
import sqlite3
import sys
from collections import defaultdict

LIVE_FORMAL_DATE = '2026-09-20'
WINDOW_FROM = '2026-08-29'
VALIDATE_FROM, VALIDATE_TO = '2026-07-01', '2026-08-29'
BULL_IN_BAND = 25.5  # not used here; regime tag pre-computed by writer

POP_SQL = """SELECT symbol, entry_time, side, pnl_atr, stop_atr, btc_trend_state,
       substr(entry_time,1,10) d, CAST(strftime('%w',entry_time) AS INT) w
FROM shadow_trades
WHERE status='closed' AND strategy='ny_flush_buy_4h'
  AND CAST(strftime('%w',entry_time) AS INT) IN (0,6)
  AND entry_time >= :frm {to_clause}"""


def load(db_path, frm, to=None):
    db = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    to_clause = 'AND entry_time < :to' if to else ''
    rows = [dict(r) for r in db.execute(POP_SQL.format(to_clause=to_clause), {'frm': frm, 'to': to})]
    seen, dedup = set(), []
    for r in rows:
        k = (r['symbol'], r['entry_time'], r['side'])
        if k in seen:
            continue
        seen.add(k)
        dedup.append(r)
    return dedup


def classify(rows):
    longs = [r for r in rows if r['side'] == 'LONG']
    nonlong = [r for r in rows if r['side'] != 'LONG']
    nullreg = [r for r in rows if r['btc_trend_state'] is None]
    bull = [r for r in longs if r['btc_trend_state'] == 'bull']
    for r in bull:
        r['R'] = r['pnl_atr'] / r['stop_atr'] if r['stop_atr'] else 0.0
    return bull, nonlong, nullreg, longs


def stats(bull):
    n = len(bull)
    if not n:
        return {'n': 0}
    s = sum(r['R'] for r in bull)
    days = defaultdict(float)
    for r in bull:
        days[r['d']] += r['R']
    top = max(days.values(), key=abs)
    tot = sum(abs(v) for v in days.values()) or 1e-9
    return {'n': n, 'E': s / n, 'sumR': s, 'days': len(days),
            'pos_days': sum(1 for v in days.values() if v > 0),
            'top_day_pct': abs(top) / tot * 100}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='/root/bitana/storage/signal_shadow.db')
    ap.add_argument('--peek', action='store_true')
    ap.add_argument('--validate', action='store_true')
    a = ap.parse_args()

    if a.validate:
        rows = load(a.db, VALIDATE_FROM, VALIDATE_TO)
        bull, nonlong, nullreg, _ = classify(rows)
        st = stats(bull)
        print(f"VALIDATION GATE (in-sample Jul1->Aug-28, registered basis: n=65 E=+0.114 top-day 57%)")
        print(f"repro: n={st['n']} E={st.get('E', 0):+.4f} top-day={st.get('top_day_pct', 0):.0f}% "
              f"LONG-violations={len(nonlong)} NULL-regime={len(nullreg)}")
        ok = abs(st['n'] - 65) <= 5 and abs(st.get('E', 0) - 0.114) <= 0.02
        print("VALIDATION:", "PASS" if ok else "FAIL — reader does not reproduce basis, DO NOT run formal read")
        sys.exit(0 if ok else 1)

    rows = load(a.db, WINDOW_FROM)
    bull, nonlong, nullreg, longs = classify(rows)
    st = stats(bull)
    print(f"PREREG-WKNDNY (window {WINDOW_FROM}->, live-mirror: ny_flush_buy_4h bull weekends)")
    print(f"population: n={len(rows)} dedup'd | LONG={len(longs)} SHORT={len(nonlong)} (violations excluded+counted) "
          f"| NULL-regime={len(nullreg)}" + (" => READ VOID" if nullreg else ""))
    if a.peek:
        print(f"PEEK (counts only): bull-weekend legs={st['n']} distinct-days={st.get('days', 0)}")
        print(f"floors: n>=30? {st['n']>=30} | days>=5? {st.get('days',0)>=5}")
        sys.exit(0)
    print(f"bull book: n={st['n']} E={st.get('E',0):+.4f} sumR={st.get('sumR',0):+.2f} "
          f"days={st.get('days',0)} pos_days={st.get('pos_days',0)} top-day={st.get('top_day_pct',0):.0f}%")
    floors = st['n'] >= 30 and st.get('days', 0) >= 5 and st.get('top_day_pct', 100) <= 40
    print(f"floors: n>=30? {st['n']>=30} | days>=5? {st.get('days',0)>=5} | top-day<=40%? {st.get('top_day_pct',100)<=40}")
    if nullreg:
        print("DECISION: READ VOID (NULL-regime rows on in-window days) — counts-only until re-audited")
    elif floors and st['E'] >= 0.05:
        print("DECISION: PROMOTE -> G1 eval + execution-feasibility review (NOT auto-wire)")
    elif st['n'] >= 30 and st.get('days', 0) >= 5 and st['E'] < 0:
        print("DECISION: KILL -> park permanently, no re-proposal without new regime structure")
    else:
        print("DECISION: INCONCLUSIVE -> keep exclude_weekdays unchanged (extension max Oct 4)")


if __name__ == '__main__':
    main()
