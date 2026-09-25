#!/usr/bin/env python3
"""Reader of record for PREREG-NY-BREADTH (Row 12) and PREREG-NY-KNIFE (Row 13), registered 2026-09-25T16:xxZ
(RESEARCH_PLAN.md, EOF section). Both are DARK: nothing is wired; this reader only measures.

SCOPE: BULL legs only (bars). The neutral cell is reported, never judged: there narrow flushes are NY's best trades
  (basis +0.458 R/leg, n=26, 5/5 days), so a blanket narrow veto would gut it.
Row 12 NY-BREADTH claim: NY flush-buys pay when the flush is market-wide. BROAD = cluster_breadth > 20, i.e. more
  than 20 distinct symbols burst in the same 15-minute bucket, counted by the paper harness at entry
  (tools/v5_forward_test.py _cluster_breadth). NARROW = <= 20. The candidate cut is a veto on NARROW flushes.
Row 13 NY-KNIFE claim: on a red BTC day (BTCUSDT 24h return < -1.5% at entry) a NARROW flush is a falling knife.
  KNIFE = red day AND narrow; REST = every other leg. The candidate cut is a veto on KNIFE legs.

Frozen population (NY live gates at registration, re-derived; READ-PROC-GATECOMPLETE applies):
  shadow_trades strategy='ny_flush_buy_1h', session='ny', side LONG, liq_imb >= 0.5, status != 'open',
  entry_vol_z >= 0 (NULL -> 0), decile >= 1 (NULL -> 0), n_confirms >= 1 (column since MIRROR CUT-OVER 3, else the
  exact burst_snapshots join, else UNKNOWN: its own line, never passes a bar), dedup (symbol, entry_time).
  Regime = live classifier replayed on BTC 4h closes + ADXBAND (lon_bull_fade_reader.regime_series). The hour must be
  in the frozen NY lattice below for that regime and weekday (Mon/Sat/Sun skipped).
Exit (re-simulated from trade_r_path): stop 5 ATR in bull/neutral, 10 ATR in bear; no TP; time exit at bar 12 (60m);
  stop-first. R = (pnl_atr - cost_atr) / stop. Bars at 20 bps; 12 bps reported.
BTC 24h return: BTCUSDT 5m close of the last bar closed at or before entry vs the close 288 bars earlier (Binance public).
Forward window: entry_time >= 2026-09-25T16:30Z.
ROW 12 PROMOTE (ALL, forward): broad n>=50 over >=6 days AND narrow n>=100 over >=6 days; narrow E <= 0 @20bps;
  broad E >= +0.05; broad - narrow >= +0.05 overall and > 0 in both day-halves; broad top-day <= 40% of net.
ROW 12 KILL (ANY): narrow E >= +0.03 at an R-read; at the formal read, broad - narrow <= 0 or broad top-day > 40%.
ROW 12 cadence: counts only until broad n>=20 and narrow n>=60; formal read at the promote sample sizes or 2026-11-30,
  whichever first; one extension to 2027-01-31, then park.
ROW 12 fallback: at the formal read, if narrow E > 0 but broad - narrow >= +0.10 with both day-halves > 0, the verdict is
  TILT-CANDIDATE: register a sizing-tilt G0 row (broad full size, narrow reduced). Never wire the veto on that evidence.
Basis (bull, 2026-08-21 -> 2026-09-24, in-sample, does not count): broad n=93/7d E +0.0798 (ex-Sep-23 +0.219, 6/6 days);
  narrow n=216/11d E -0.0483 (ex-Sep-23 +0.041); Sep-23 narrow -17.69R vs broad -0.90R. knife n=72/4d E -0.0534 (worst day
  Aug-28 -6.29R, top-day 164%, day-halves -0.145/+0.091).
ROW 13 PROMOTE (ALL, forward): knife n>=50 over >=5 days; knife E <= -0.03 @20bps; rest E - knife E >= +0.05;
  knife top-day <= 40%; knife E < 0 in both day-halves.
ROW 13 KILL (ANY): knife E >= 0 at n>=30; at the formal read, top-day > 40% or rest E < knife E.
ROW 13 cadence: counts only until knife n>=30; formal read at knife n>=50 over >=5 days or 2026-12-31; one extension to
  2027-03-31, then park. If Row 12 promotes (all narrow flushes vetoed), Row 13 is moot and parks.

Modes:
  --validate  reproduce the registered in-sample basis (entries 2026-08-21 -> 2026-09-24, bull legs; unknown n_confirms
              excluded per READ-PROC): n and E must match BASIS.
              Exits 1 on mismatch.
  --peek      counts only
  --live      add the live-real cross-check (live NY legs matched to their paper twin by symbol + bar; report only)
  (default)   forward read with the frozen decision rules
DB: never opens the live DB files; /tmp copies via the SQLite backup API (CLAUDE.md) unless --db names a copy.
"""
import argparse
import json
import os
import sqlite3
import ssl
import sys
import time
import urllib.request
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:      # append, never prepend: research/config.py would shadow the bot's config package
    sys.path.append(_HERE)
import lon_bull_fade_reader as base  # noqa: E402  (shared: copy_db, drop_copy, _ms, regime_series)

FORWARD_FROM = '2026-09-25T16:30:00'
BASIS_FROM, BASIS_TO = '2026-08-21', '2026-09-25'
R12_FORMAL, R12_EXT = '2026-11-30', '2027-01-31'
R13_FORMAL, R13_EXT = '2026-12-31', '2027-03-31'
BROAD_MIN, RED_DAY = 20, -1.5
BAR_BPS, REF_BPS = 20.0, 12.0
STOPS = {'bull': 5.0, 'neutral': 5.0, 'bear': 10.0}
TIME_BARS = 12
LATTICE = {   # python weekday -> hours, frozen from the live config at registration (Mon/Sat/Sun skipped)
    'bull': {1: (14, 15), 2: (14, 15, 16, 17), 3: (14, 15, 16, 17, 19), 4: (14, 15, 16, 18, 19, 20)},
    'neutral': {2: (16, 17), 3: (16, 17), 4: (16,)},
    'bear': {1: (14, 15), 2: (14, 15), 3: (14, 15), 4: (14, 15, 20)},
}
BASIS = {'broad_n': 93, 'narrow_n': 216, 'knife_n': 72, 'broad_E': 0.0798, 'narrow_E': -0.0483, 'knife_E': -0.0534}

POP_SQL = """SELECT t.id, t.symbol, t.entry_time, t.bars_held, t.entry_atr_pct, t.hour, t.cluster_breadth,
       t.n_confirms AS nc_col, b.n_confirms AS nc_snap, substr(t.entry_time, 1, 10) AS d
FROM shadow_trades t
LEFT JOIN burst_snapshots b ON b.symbol = t.symbol AND b.bar_time = t.entry_time
WHERE t.strategy = 'ny_flush_buy_1h' AND t.session = 'ny' AND t.side = 'LONG' AND t.liq_imb >= 0.5
  AND t.status <> 'open' AND COALESCE(t.entry_vol_z, 0) >= 0 AND COALESCE(t.decile, 0) >= 1
  AND t.entry_time >= :frm AND t.entry_time < :to"""


def btc_5m(frm_ms):
    ctx, rows, t = ssl.create_default_context(), [], frm_ms
    while True:
        url = f'https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=5m&startTime={t}&limit=1500'
        r = json.load(urllib.request.urlopen(url, context=ctx, timeout=30))
        if not r:
            break
        rows += r
        t = r[-1][0] + 300000
        if len(r) < 1500:
            break
        time.sleep(0.15)
    now = int(time.time() * 1000)
    rows = [k for k in rows if k[6] < now]
    return [k[6] for k in rows], [float(k[4]) for k in rows]


def btc24(ms, ct, cl):
    j = bisect_right(ct, ms) - 1
    return (cl[j] / cl[j - 288] - 1) * 100 if j >= 288 else None


def load(db_path, frm, to, series, ct, cl):
    closes = [c for c, _, _ in series]
    db = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    has_col = 'n_confirms' in {r[1] for r in db.execute('PRAGMA table_info(shadow_trades)')}
    sql = POP_SQL if has_col else POP_SQL.replace('t.n_confirms AS nc_col', 'NULL AS nc_col')
    seen, rows, unknown, nc0, untagged = set(), [], 0, 0, 0
    for r in db.execute(sql, {'frm': frm, 'to': to}):
        r = dict(r)
        if (r['symbol'], r['entry_time']) in seen:
            continue
        seen.add((r['symbol'], r['entry_time']))
        nc = r['nc_col'] if r['nc_col'] is not None else r['nc_snap']
        if nc is None:
            unknown += 1
            continue
        if nc < 1:
            nc0 += 1
            continue
        ms = base._ms(r['entry_time'])
        j = bisect_right(closes, ms) - 1
        if j < 3:
            untagged += 1
            continue
        reg = series[j][1]
        wd = time.gmtime(ms / 1000).tm_wday
        if r['hour'] not in LATTICE.get(reg, {}).get(wd, ()):
            continue
        r.update(reg=reg, ms=ms, stop=STOPS[reg], b24=btc24(ms, ct, cl))
        rows.append(r)
    paths = defaultdict(dict)
    ids = [r['id'] for r in rows]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = (f"SELECT trade_id, phase, bar_num, r_low, r_close FROM trade_r_path WHERE trade_id IN "
             f"({','.join('?' * len(chunk))}) AND (phase = 'open' OR bar_num <= {TIME_BARS + 1})")
        for p in db.execute(q, chunk):
            paths[p['trade_id']].setdefault((p['phase'], p['bar_num']), (p['r_low'], p['r_close']))
    db.close()
    legs, missing = [], 0
    for r in rows:
        pnl = simulate(r, paths.get(r['id'], {}))
        if pnl is None or not (r['entry_atr_pct'] or 0) > 0:
            missing += 1
            continue
        c = 1e4 * (r['entry_atr_pct'] / 100)
        r['R20'], r['R12'] = (pnl - BAR_BPS / c) / r['stop'], (pnl - REF_BPS / c) / r['stop']
        cb = r['cluster_breadth']
        r['breadth'] = None if cb is None else ('broad' if cb > BROAD_MIN else 'narrow')
        r['knife'] = (r['breadth'] == 'narrow' and r['b24'] is not None and r['b24'] < RED_DAY)
        legs.append(r)
    return legs, {'unknown_nc': unknown, 'nc0': nc0, 'incomplete_path': missing, 'untagged': untagged}


def simulate(r, path):
    bh = r['bars_held'] or 0
    for b in range(1, TIME_BARS + 1):
        bar = path.get(('open', b)) if b <= bh else path.get(('post', b - bh + 1))
        if bar is None or bar[1] is None:
            return None
        lo, cl = bar
        if lo is not None and lo <= -r['stop']:
            return -r['stop']
        if b == TIME_BARS:
            return cl
    return None


def stats(ls, col='R20'):
    if not ls:
        return {'n': 0, 'days': 0}
    by_day = defaultdict(float)
    for x in ls:
        by_day[x['d']] += x[col]
    net = sum(x[col] for x in ls)
    top = (max(by_day.values()) / net) if net > 0 else ((min(by_day.values()) / net) if net < 0 else None)
    g, l_ = sum(x[col] for x in ls if x[col] > 0), -sum(x[col] for x in ls if x[col] < 0)
    return {'n': len(ls), 'days': len(by_day), 'E': net / len(ls), 'sumR': net, 'PF': g / l_ if l_ > 0 else None,
            'top_day_share': top, 'pos_days': sum(1 for v in by_day.values() if v > 0)}


def fmt(s):
    if not s.get('n'):
        return 'n=0'
    top = f"{s['top_day_share']:.0%}" if s['top_day_share'] is not None else 'n/a'
    pf = f"{s['PF']:.2f}" if s['PF'] is not None else 'n/a'
    return f"n={s['n']} days={s['days']} E={s['E']:+.4f} sumR={s['sumR']:+.2f} PF={pf} top-day={top} pos-days={s['pos_days']}/{s['days']}"


def halves(a, b):
    """E(a) - E(b) in each day-half (days split at the median day of a+b)."""
    days = sorted({x['d'] for x in a + b})
    if len(days) < 2:
        return None, None
    cut = days[len(days) // 2]
    out = []
    for lo_half in (True, False):
        aa = [x for x in a if (x['d'] < cut) == lo_half]
        bb = [x for x in b if (x['d'] < cut) == lo_half]
        out.append((stats(aa)['E'] - stats(bb)['E']) if aa and bb else None)
    return tuple(out)


def read(legs, regimes=('bull',)):
    L = [x for x in legs if x['reg'] in regimes]
    broad, narrow = [x for x in L if x['breadth'] == 'broad'], [x for x in L if x['breadth'] == 'narrow']
    knife, rest = [x for x in L if x['knife']], [x for x in L if not x['knife']]
    return {'broad': stats(broad), 'narrow': stats(narrow), 'breadth_unknown': stats([x for x in L if x['breadth'] is None]),
            'broad12': stats(broad, 'R12'), 'narrow12': stats(narrow, 'R12'), 'r12_halves': halves(broad, narrow),
            'knife': stats(knife), 'rest': stats(rest), 'knife12': stats(knife, 'R12'),
            'knife_halves': _knife_halves(knife),
            'by_regime': {g: {'broad': stats([x for x in broad if x['reg'] == g]), 'narrow': stats([x for x in narrow if x['reg'] == g])}
                          for g in regimes}}


def _knife_halves(knife):
    days = sorted({x['d'] for x in knife})
    if len(days) < 2:
        return None, None
    cut = days[len(days) // 2]
    return stats([x for x in knife if x['d'] < cut]).get('E'), stats([x for x in knife if x['d'] >= cut]).get('E')


def decide12(res, today):
    b, n = res['broad'], res['narrow']
    if b.get('n', 0) < 20 or n.get('n', 0) < 60:
        return 'COUNTS-ONLY (needs broad n>=20 and narrow n>=60)'
    kills = []
    if n['E'] >= 0.03:
        kills.append('narrow E>=+0.03 @20bps')
    sized = b['n'] >= 50 and b['days'] >= 6 and n['n'] >= 100 and n['days'] >= 6
    formal = sized or today >= R12_FORMAL
    if formal:
        if b['E'] - n['E'] <= 0:
            kills.append('broad - narrow <= 0 at the formal read')
        if b['top_day_share'] is not None and b['top_day_share'] > 0.40:
            kills.append('broad top-day > 40% at the formal read')
    if kills:
        return 'KILL: ' + '; '.join(kills)
    if not formal:
        return 'R-READ (no kill fired; formal read not yet due)'
    h1, h2 = res['r12_halves']
    if (sized and n['E'] <= 0 and b['E'] >= 0.05 and b['E'] - n['E'] >= 0.05 and h1 is not None and h1 > 0
            and h2 is not None and h2 > 0 and b['top_day_share'] is not None and b['top_day_share'] <= 0.40):
        return 'PROMOTE -> owner decision on wiring (NOT auto-wire: engine breadth gate + WLA mirror + test)'
    if n['E'] > 0 and b['E'] - n['E'] >= 0.10 and h1 is not None and h1 > 0 and h2 is not None and h2 > 0:
        return 'TILT-CANDIDATE -> register a sizing-tilt G0 row (broad full size, narrow reduced); never the veto'
    return 'PARK (extension exhausted)' if today >= R12_EXT else 'INCONCLUSIVE -> one extension to ' + R12_EXT


def decide13(res, today, r12_verdict=''):
    if r12_verdict.startswith('PROMOTE'):
        return 'PARK (Row 12 promoted: all narrow flushes vetoed, Row 13 is moot)'
    k, r = res['knife'], res['rest']
    if k.get('n', 0) < 30:
        return 'COUNTS-ONLY (knife n<30)'
    kills = []
    if k['E'] >= 0:
        kills.append('knife E>=0 at n>=30')
    formal = (k['n'] >= 50 and k['days'] >= 5) or today >= R13_FORMAL
    if formal:
        if k['top_day_share'] is not None and k['top_day_share'] > 0.40:
            kills.append('knife top-day > 40% at the formal read')
        if r.get('n', 0) and r['E'] < k['E']:
            kills.append('rest E < knife E at the formal read')
    if kills:
        return 'KILL: ' + '; '.join(kills)
    if not formal:
        return 'R-READ (no kill fired; formal read not yet due)'
    h1, h2 = res['knife_halves']
    if (k['n'] >= 50 and k['days'] >= 5 and k['E'] <= -0.03 and r['E'] - k['E'] >= 0.05 and k['top_day_share'] is not None
            and k['top_day_share'] <= 0.40 and h1 is not None and h1 < 0 and h2 is not None and h2 < 0):
        return 'PROMOTE -> owner decision on wiring (NOT auto-wire: engine knife veto + WLA mirror + test)'
    return 'PARK (extension exhausted)' if today >= R13_EXT else 'INCONCLUSIVE -> one extension to ' + R13_EXT


def live_check(db_path, series, ct, cl):
    """Report-only: live NY legs matched to the paper twin (symbol + signal bar) for breadth; BTC 24h from 5m."""
    twins = {}
    con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=30)
    for sym, et, cb in con.execute("SELECT symbol, entry_time, cluster_breadth FROM shadow_trades "
                                   "WHERE strategy='ny_flush_buy_1h' AND side='LONG'"):
        twins[(sym, et[:16])] = cb
    con.close()
    ldb = base.copy_db(base.LIVE_TRADES_DB, prefix='ny_quality_live_')
    out = {'broad': [], 'narrow': [], 'unmatched': [], 'knife': [], 'rest': []}
    try:
        con = sqlite3.connect(ldb)
        for ts, hold, sym, pnl_r, sd in con.execute('SELECT timestamp, hold_time_s, symbol, pnl_r, signal_data FROM trades'):
            s = json.loads(sd or '{}')
            if s.get('shadow_strategy') != 'ny_flush_buy_1h' or s.get('session') != 'ny':
                continue
            ms = base._ms(ts) - int((hold or 0) * 1000)
            bar_ms = ms - ms % 300000 - 1
            key = (sym, datetime.fromtimestamp(bar_ms / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M'))
            cb = twins.get(key)
            leg = {'d': ts[:10], 'R20': pnl_r}
            if cb is None:
                out['unmatched'].append(leg)
                continue
            br = 'broad' if cb > BROAD_MIN else 'narrow'
            out[br].append(leg)
            b = btc24(ms, ct, cl)
            out['knife' if (br == 'narrow' and b is not None and b < RED_DAY) else 'rest'].append(leg)
        con.close()
    finally:
        base.drop_copy(ldb)
    return {k: stats(v) for k, v in out.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--db')
    ap.add_argument('--validate', action='store_true')
    ap.add_argument('--peek', action='store_true')
    ap.add_argument('--live', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--today')
    a = ap.parse_args()
    today = a.today or datetime.now(timezone.utc).strftime('%Y-%m-%d')
    made = a.db is None
    db = base.copy_db(base.LIVE_DB, prefix='ny_quality_') if made else a.db
    try:
        series = base.regime_series()
        ct, cl = btc_5m(base._ms(BASIS_FROM + 'T00:00:00') - 2 * 86400000)
        if a.validate:
            legs, ex = load(db, BASIS_FROM, BASIS_TO, series, ct, cl)
            res = read(legs, ('bull',))
            print(f"VALIDATE basis {BASIS_FROM}..{BASIS_TO} (bull legs; excluded {ex})")
            for k in ('broad', 'narrow', 'breadth_unknown', 'knife', 'rest'):
                print(f"  {k:16s} @20bps: {fmt(res[k])}")
            print(f"  broad-narrow by day-half: {res['r12_halves']} | knife by day-half: {res['knife_halves']}")
            neu = read(legs, ('neutral',))
            print(f"  (neutral cell, report) broad {fmt(neu['broad'])} | narrow {fmt(neu['narrow'])}")
            ok = (res['broad']['n'] == BASIS['broad_n'] and res['narrow']['n'] == BASIS['narrow_n'] and res['knife']['n'] == BASIS['knife_n']
                  and abs(res['broad']['E'] - BASIS['broad_E']) < 5e-4 and abs(res['narrow']['E'] - BASIS['narrow_E']) < 5e-4
                  and abs(res['knife']['E'] - BASIS['knife_E']) < 5e-4)
            if a.live:
                print('LIVE-REAL cross-check (all dates, report only):', json.dumps(live_check(db, series, ct, cl), default=str))
            print('VALIDATION', 'PASS' if ok else 'FAIL')
            sys.exit(0 if ok else 1)
        legs, ex = load(db, FORWARD_FROM, '9999', series, ct, cl)
        res = read(legs, ('bull',))
        neu = read(legs, ('neutral',))
        v12 = decide12(res, today)
        v13 = decide13(res, today, v12)
        print(f"PREREG-NY-BREADTH / NY-KNIFE read ({today}), forward from {FORWARD_FROM}Z | excluded {ex}")
        if a.peek or v12.startswith('COUNTS') and v13.startswith('COUNTS'):
            print(f"  counts: broad n={res['broad']['n']} narrow n={res['narrow']['n']} knife n={res['knife']['n']} "
                  f"rest n={res['rest']['n']} | breadth unknown n={res['breadth_unknown']['n']}")
        else:
            for k in ('broad', 'narrow', 'breadth_unknown', 'knife', 'rest'):
                print(f"  {k:16s} @20bps: {fmt(res[k])}")
            print(f"  @12bps ref: broad {fmt(res['broad12'])} | narrow {fmt(res['narrow12'])} | knife {fmt(res['knife12'])}")
            print(f"  halves: broad-narrow {res['r12_halves']} | knife {res['knife_halves']}")
        print(f"  (neutral cell, report only) broad {fmt(neu['broad'])} | narrow {fmt(neu['narrow'])}")
        print(f"VERDICT Row 12 NY-BREADTH: {v12}")
        print(f"VERDICT Row 13 NY-KNIFE:   {v13}")
        if a.live:
            print('LIVE-REAL cross-check (all dates, report only):', json.dumps(live_check(db, series, ct, cl), default=str))
        if a.json:
            print(json.dumps({'today': today, 'broad_n': res['broad']['n'], 'narrow_n': res['narrow']['n'],
                              'knife_n': res['knife']['n'], 'days': max(res['broad'].get('days', 0), res['narrow'].get('days', 0)),
                              'v12': v12, 'v13': v13}))
    finally:
        if made:
            base.drop_copy(db)


if __name__ == '__main__':
    main()
