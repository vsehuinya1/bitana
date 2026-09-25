#!/usr/bin/env python3
"""ASIA-MIDVOL watch reader (DRAFT, 2026-09-25). Not registered: registration needs an owner order.

Candidate: wire Asia back in BTC-neutral with the old Asia arm's own gates PLUS a band on the symbol's 5m ATR% at
entry (0.30 <= atr_pct < 0.50). Structural reason: Asia's median ATR is ~half London/NY's, so fixed costs weigh
double (20 bps = 0.67 ATR in Asia vs 0.28-0.33 in NY/London); below ~0.3% costs eat every edge, and above ~0.5%
pumps keep running. Live and paper ATR% agree exactly (20 matched legs: corr 0.999, ratio 1.000, same side of
both cuts).

Frozen population (paper book, own exit = the old arm's live exit):
  shadow_trades strategy='asia_pump_short_4h', side SHORT, session 'asia', status != 'open';
  regime = live classifier replay + ADXBAND, 'neutral' (lon_bull_fade_reader.regime_series);
  old arm gates: liq_imb <= -0.5 (neg_imb_only, min_imb 0.5), weekday not Tue/Sat/Sun, BTC EMA dist < 5.0% (NULL
  passes, as the engine's fail-open), decile >= 2, entry_vol_z >= 0, n_confirms >= 1 (column, else snapshot join,
  else UNKNOWN: own line, never passes a bar); band 0.30 <= entry_atr_pct < 0.50.
Exit: the strategy's own SL10 / 48 bars (4h), time-exit only, which is the old arm's live exit. R = (pnl_atr - cost)/10.
Forward window: entry_time >= 2026-09-25T19:00Z. Bars at 20 bps; 12 bps reported.
PROMOTE (ALL, forward): n >= 50 over >= 10 days; E >= +0.03 @20bps; top-day <= 40% of net; both day-halves > 0;
  live-symbol subset E >= 0 at n >= 20.
KILL (ANY): E < 0 @20bps at n >= 30; top-day > 40% at the formal read.
Formal read at n >= 50 and >= 10 days, or 2027-01-31, whichever first; one extension to 2027-04-30, then park.
--validate reproduces the in-sample basis (entries < 2026-09-25T12:00Z; unknown n_confirms counted as pass, the
  study convention): n=24, 7 days, E +0.157 @20bps.
"""
import argparse
import os
import sqlite3
import sys
import time
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.append(_HERE)     # append: research/config would shadow the bot's config package
import lon_bull_fade_reader as base  # noqa: E402

FORWARD_FROM, BASIS_TO = '2026-09-25T19:00:00', '2026-09-25T12:00:00'
FORMAL_DATE, EXTENSION_DATE = '2027-01-31', '2027-04-30'
ATR_LO, ATR_HI, DIST_MAX = 0.30, 0.50, 5.0
BAR_BPS, REF_BPS = 20.0, 12.0
BASIS = {'n': 24, 'days': 7, 'E20': 0.157}
LIVE = {'BTCUSDT', 'SOLUSDT', 'ETHUSDT', 'WLDUSDT', 'XRPUSDT', 'NEARUSDT', 'ZECUSDT', 'APTUSDT', 'ADAUSDT', 'UNIUSDT'}

SQL = """SELECT t.symbol, t.entry_time, t.pnl_atr, t.stop_atr, t.entry_atr_pct, t.liq_imb, t.decile, t.entry_vol_z,
       t.btc_distance_from_ema_pct AS dist, t.n_confirms AS nc_col, b.n_confirms AS nc_snap,
       substr(t.entry_time, 1, 10) AS d
FROM shadow_trades t LEFT JOIN burst_snapshots b ON b.symbol = t.symbol AND b.bar_time = t.entry_time
WHERE t.strategy = 'asia_pump_short_4h' AND t.side = 'SHORT' AND t.session = 'asia' AND t.status <> 'open'
  AND t.pnl_atr IS NOT NULL AND t.stop_atr > 0 AND t.entry_atr_pct > 0
  AND t.entry_time >= :frm AND t.entry_time < :to"""


def load(db_path, frm, to, unknown_passes=False):
    series = base.regime_series()
    closes = [c for c, _, _ in series]
    db = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    if 'n_confirms' not in {r[1] for r in db.execute('PRAGMA table_info(shadow_trades)')}:
        sql = SQL.replace('t.n_confirms AS nc_col', 'NULL AS nc_col')
    else:
        sql = SQL
    known, unknown, seen = [], [], set()
    for r in db.execute(sql, {'frm': frm, 'to': to}):
        r = dict(r)
        if (r['symbol'], r['entry_time']) in seen:
            continue
        seen.add((r['symbol'], r['entry_time']))
        ms = base._ms(r['entry_time'])
        j = bisect_right(closes, ms) - 1
        if j < 0 or series[j][1] != 'neutral':
            continue
        if time.gmtime(ms / 1000).tm_wday in (1, 5, 6):
            continue
        if (r['liq_imb'] or 0) > -0.5 or (r['decile'] or 0) < 2 or (r['entry_vol_z'] or 0) < 0:
            continue
        if r['dist'] is not None and r['dist'] >= DIST_MAX:
            continue
        if not (ATR_LO <= r['entry_atr_pct'] < ATR_HI):
            continue
        nc = r['nc_col'] if r['nc_col'] is not None else r['nc_snap']
        c = 1e4 * (r['entry_atr_pct'] / 100)
        r['R20'] = (r['pnl_atr'] - BAR_BPS / c) / r['stop_atr']
        r['R12'] = (r['pnl_atr'] - REF_BPS / c) / r['stop_atr']
        if nc is None and not unknown_passes:
            unknown.append(r)
        elif nc is None or nc >= 1:
            known.append(r)
    db.close()
    return known, unknown


def stats(ls, col='R20'):
    if not ls:
        return {'n': 0, 'days': 0}
    by_day = defaultdict(float)
    for x in ls:
        by_day[x['d']] += x[col]
    net = sum(x[col] for x in ls)
    days = sorted(by_day)
    cut = days[len(days) // 2]
    h1 = [x[col] for x in ls if x['d'] < cut]
    h2 = [x[col] for x in ls if x['d'] >= cut]
    return {'n': len(ls), 'days': len(days), 'E': net / len(ls), 'sumR': net,
            'top_day_share': (max(by_day.values()) / net) if net > 0 else None,
            'H1': sum(h1) / len(h1) if h1 else None, 'H2': sum(h2) / len(h2) if h2 else None,
            'pos_days': sum(1 for v in by_day.values() if v > 0)}


def fmt(s):
    if not s.get('n'):
        return 'n=0'
    top = f"{s['top_day_share']:.0%}" if s['top_day_share'] is not None else 'n/a'
    h = lambda v: f'{v:+.3f}' if v is not None else 'n/a'  # noqa: E731
    return (f"n={s['n']} days={s['days']} E={s['E']:+.4f} sumR={s['sumR']:+.2f} halves {h(s['H1'])}/{h(s['H2'])} "
            f"top-day={top} pos-days={s['pos_days']}/{s['days']}")


def decide(s, lv, today):
    n, days = s.get('n', 0), s.get('days', 0)
    if n < 30:
        return 'COUNTS-ONLY (n<30)'
    if s['E'] < 0:
        return 'KILL: E<0 @20bps at n>=30'
    formal = (n >= 50 and days >= 10) or today >= FORMAL_DATE
    if not formal:
        return 'R-READ (no kill; formal read not yet due)'
    if s['top_day_share'] is not None and s['top_day_share'] > 0.40:
        return 'KILL: top-day > 40% at the formal read'
    if (n >= 50 and days >= 10 and s['E'] >= 0.03 and s['H1'] is not None and s['H1'] > 0
            and s['H2'] is not None and s['H2'] > 0 and lv.get('n', 0) >= 20 and lv['E'] >= 0):
        return 'PROMOTE -> owner decision: apply reports/asia_midvol_gate.patch + uncomment the asia block with the band'
    return 'PARK (extension exhausted)' if today >= EXTENSION_DATE else 'INCONCLUSIVE -> one extension to ' + EXTENSION_DATE


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--db')
    ap.add_argument('--validate', action='store_true')
    a = ap.parse_args()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    made = a.db is None
    db = base.copy_db(base.LIVE_DB, prefix='asia_midvol_') if made else a.db
    try:
        if a.validate:
            k, _ = load(db, '2026-07-01', BASIS_TO, unknown_passes=True)
            s = stats(k)
            ok = s.get('n') == BASIS['n'] and s.get('days') == BASIS['days'] and abs(s['E'] - BASIS['E20']) < 5e-4
            print(f"VALIDATE basis ..{BASIS_TO} @20bps: {fmt(s)} | @12bps E={stats(k, 'R12').get('E', 0):+.4f}")
            print(f"  live-symbol subset: {fmt(stats([x for x in k if x['symbol'] in LIVE]))}")
            print('VALIDATION', 'PASS' if ok else 'FAIL')
            sys.exit(0 if ok else 1)
        k, u = load(db, FORWARD_FROM, '9999')
        s, lv = stats(k), stats([x for x in k if x['symbol'] in LIVE])
        print(f"ASIA-MIDVOL read ({today}), forward from {FORWARD_FROM}Z: {fmt(s)}")
        print(f"  @12bps: {fmt(stats(k, 'R12'))} | live-symbol subset: {fmt(lv)} | UNKNOWN n_confirms: {fmt(stats(u))}")
        print('VERDICT:', decide(s, lv, today))
    finally:
        if made:
            base.drop_copy(db)


if __name__ == '__main__':
    main()
