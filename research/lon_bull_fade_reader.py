#!/usr/bin/env python3
"""PREREG-LON-BULL-FADE (Row 11) reader of record — registered 2026-09-25T14:3xZ, RESEARCH_PLAN.md (EOF section).

Claim under watch: London burst_follow LONG legs taken while BTC is in a bull regime whose 4h ADX is falling fast
(down >= 4 points over the last 3 closed 4h bars) have negative net expectancy, so the arm should stand down in a
fading bull. DARK: nothing is wired; this reader only measures.

Frozen population (London live gates at registration, re-derived; READ-PROC-GATECOMPLETE applies):
  shadow_trades strategy='burst_follow', session='london', side LONG, liq_imb >= 0.5, status != 'open',
  hour in (10, 11, 13), entry weekday Mon-Fri, entry_vol_z >= 0 (NULL -> 0), decile >= 1 (NULL -> 0),
  n_confirms >= 1: the shadow_trades.n_confirms column (since MIRROR CUT-OVER 3), else the exact burst_snapshots
  join (symbol + bar_time), else UNKNOWN. Unknown rows get their own line and never pass a bar. Dedup (symbol,
  entry_time, side).
Regime and fade flag: the live classifier replayed on Binance BTCUSDT 4h closes
  (engines.btc_regime.compute_regime_snapshot on 249 closed bars, then the ADXBAND deadband: enter bull/bear at
  ADX >= 25.5, back to neutral below 24.5). Each row takes the last 4h bar closed at or before its entry_time.
  FADE    = state bull AND ADX(bar) - ADX(3 closes earlier) <= -4.0
  CONTROL = state bull AND that 3-bar change > -4.0
  The -4 threshold is research/build_regime_survival.py's SLOPE cut, written before the Sep-25 loss; not tuned.
Exit (London live exit, re-simulated from trade_r_path): SL 6 ATR, TP 3 ATR, time exit at bar 6 (30m), stop-first
  within a bar. Bar b <= bars_held is phase 'open' bar b; later bars are phase 'post' bar (b - bars_held + 1), since
  post bar 1 duplicates the exit bar. Legs without a complete path are excluded and counted.
Metric: R = (pnl_atr - cost_atr) / 6, cost_atr = bps/1e4 / (entry_atr_pct/100). Bars at 20 bps; 12 bps reported.
Forward window: entry_time >= 2026-09-25T14:40Z.
PROMOTE (ALL, forward rows): fade n>=50 over >=5 days; fade E <= -0.02 @20bps; control E - fade E >= +0.05;
  fade top-day share of net <= 40%; both day-halves of the fade legs E < 0.
KILL (ANY): fade E >= +0.02 @20bps at n>=30; at the formal read, fade E >= control E or top-day > 40%.
Cadence: counts only until fade n>=30, then R-reads. Formal read at fade n>=50 and >=5 fade days, or 2026-12-31,
  whichever comes first. One extension to 2027-03-31, then park.

Modes:
  --validate  reproduce the registered in-sample basis (entries 2026-08-21 -> 2026-09-24T12:23:59Z, the study's
              DB copy time): fade n=39, 3 days, E=-0.0719 @20bps. HERE ONLY unknown n_confirms counts as a pass
              (the study's convention; disclosed). Exits 1 on mismatch.
  --peek      counts only
  --live      add the live-real cross-check line (London trades in the live DB, fade vs control; report only)
  (default)   forward read with the frozen decision rule (counts only while fade n<30)
DB: never opens the live DB files. Takes /tmp copies through the SQLite backup API (CLAUDE.md) unless --db names
an existing copy.
"""
import argparse
import json
import os
import sqlite3
import ssl
import sys
import tempfile
import time
import urllib.request
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, '/root/bitana')
os.environ.pop('API_FOOTBALL_KEY', None)

LIVE_DB = '/root/bitana/storage/signal_shadow.db'
LIVE_TRADES_DB = '/root/bitana/data/bitana-live-burst.db'
FORWARD_FROM = '2026-09-25T14:40:00'
BASIS_FROM, BASIS_TO = '2026-08-21', '2026-09-24T12:24:00'
FORMAL_DATE, EXTENSION_DATE = '2026-12-31', '2027-03-31'
HOURS = (10, 11, 13)
STOP_ATR, TP_ATR, TIME_BARS = 6.0, 3.0, 6
FADE_D3, EXIT_ADX, ENTER_ADX = -4.0, 24.5, 25.5
BAR_BPS, REF_BPS = 20.0, 12.0
BASIS = {'n': 39, 'days': 3, 'E20': -0.0719}
REPLAY_FROM = '2026-01-01'

POP_SQL = """SELECT t.id, t.symbol, t.side, t.entry_time, t.bars_held, t.entry_atr_pct, t.hour,
       t.n_confirms AS nc_col, b.n_confirms AS nc_snap, substr(t.entry_time, 1, 10) AS d
FROM shadow_trades t
LEFT JOIN burst_snapshots b ON b.symbol = t.symbol AND b.bar_time = t.entry_time
WHERE t.strategy = 'burst_follow' AND t.session = 'london' AND t.side = 'LONG' AND t.liq_imb >= 0.5
  AND t.status <> 'open' AND t.hour IN (10, 11, 13)
  AND CAST(strftime('%w', t.entry_time) AS INT) BETWEEN 1 AND 5
  AND COALESCE(t.entry_vol_z, 0) >= 0 AND COALESCE(t.decile, 0) >= 1
  AND t.entry_time >= :frm AND t.entry_time < :to"""


def copy_db(src, prefix='lon_bull_fade_'):
    fd, dst = tempfile.mkstemp(prefix=prefix, suffix='.db', dir='/tmp')
    os.close(fd)
    with sqlite3.connect(f'file:{src}?mode=ro', uri=True, timeout=30) as s, sqlite3.connect(dst) as d:
        s.backup(d)
        d.execute('PRAGMA journal_mode=DELETE')   # copy inherits WAL; rollback mode leaves no -wal/-shm behind
    return dst


def drop_copy(path):
    for f in (path, path + '-wal', path + '-shm', path + '-journal'):
        if os.path.exists(f):
            os.remove(f)


def _ms(ts):
    d = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    return int((d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp() * 1000)


def regime_series(frm=REPLAY_FROM):
    """[(close_ms, state, adx)] for every closed 4h bar, live classifier + ADXBAND deadband."""
    from core.models import Candle
    from engines.btc_regime import compute_regime_snapshot
    ctx, rows = ssl.create_default_context(), []
    t = int(datetime.fromisoformat(frm).replace(tzinfo=timezone.utc).timestamp() * 1000)
    while True:
        url = f'https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=4h&startTime={t}&limit=1500'
        r = json.load(urllib.request.urlopen(url, context=ctx, timeout=30))
        if not r:
            break
        rows += r
        t = r[-1][0] + 4 * 3600 * 1000
        if len(r) < 1500:
            break
        time.sleep(0.2)
    now = int(time.time() * 1000)
    rows = [k for k in rows if k[6] < now]
    C = [Candle(symbol='BTCUSDT', timeframe='4h', open_time=datetime.fromtimestamp(k[0] / 1000, timezone.utc),
                close_time=datetime.fromtimestamp(k[6] / 1000, timezone.utc), open=float(k[1]), high=float(k[2]),
                low=float(k[3]), close=float(k[4]), volume=float(k[5])) for k in rows]
    out, prev = [], None
    for i in range(248, len(C)):
        s = compute_regime_snapshot(C[i - 248:i + 1])
        if prev in ('bull', 'bear'):
            st = 'neutral' if (s.adx is not None and s.adx < EXIT_ADX) else (s.state if s.state in ('bull', 'bear') else prev)
        else:
            st = s.state if (s.state in ('bull', 'bear') and (s.adx or 0) >= ENTER_ADX) else 'neutral'
        out.append((rows[i][6], st, s.adx))
        prev = st
    return out


def tag(entry_ms, series, closes):
    """(state, adx, d3) of the last 4h bar closed at or before entry; None if unknown."""
    j = bisect_right(closes, entry_ms) - 1
    if j < 3:
        return None
    _, st, adx = series[j]
    a3 = series[j - 3][2]
    return st, adx, (adx - a3) if (adx is not None and a3 is not None) else None


def load(db_path, frm, to, unknown_passes=False):
    db = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    has_col = 'n_confirms' in {r[1] for r in db.execute('PRAGMA table_info(shadow_trades)')}
    sql = POP_SQL if has_col else POP_SQL.replace('t.n_confirms AS nc_col', 'NULL AS nc_col')
    seen, rows = set(), []
    for r in db.execute(sql, {'frm': frm, 'to': to}):
        k = (r['symbol'], r['entry_time'], r['side'])
        if k not in seen:
            seen.add(k)
            rows.append(dict(r))
    known, unknown, below = [], [], []
    for r in rows:
        nc = r['nc_col'] if r['nc_col'] is not None else r['nc_snap']
        if nc is None:
            (known if unknown_passes else unknown).append(r)
        elif nc >= 1:
            known.append(r)
        else:
            below.append(r)
    paths = defaultdict(dict)
    ids = [r['id'] for r in known + unknown]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = (f"SELECT trade_id, phase, bar_num, r_high, r_low, r_close FROM trade_r_path WHERE trade_id IN "
             f"({','.join('?' * len(chunk))}) AND (phase = 'open' OR bar_num <= {TIME_BARS + 1})")
        for p in db.execute(q, chunk):
            paths[p['trade_id']].setdefault((p['phase'], p['bar_num']), (p['r_high'], p['r_low'], p['r_close']))
    db.close()
    return known, unknown, below, paths


def simulate(r, path):
    """pnl in ATR at SL6 / TP3 / 6 bars, stop-first; None if the path is incomplete before an exit."""
    bh = r['bars_held'] or 0
    for b in range(1, TIME_BARS + 1):
        bar = path.get(('open', b)) if b <= bh else path.get(('post', b - bh + 1))
        if bar is None or bar[2] is None:
            return None
        hi, lo, cl = bar
        if lo is not None and lo <= -STOP_ATR:
            return -STOP_ATR
        if hi is not None and hi >= TP_ATR:
            return TP_ATR
        if b == TIME_BARS:
            return cl
    return None


def legs(rows, paths, series, closes):
    out, missing, untagged = [], 0, 0
    for r in rows:
        pnl = simulate(r, paths.get(r['id'], {}))
        if pnl is None or not (r['entry_atr_pct'] or 0) > 0:
            missing += 1
            continue
        tg = tag(_ms(r['entry_time']), series, closes)
        if tg is None or tg[2] is None:
            untagged += 1
            continue
        st, adx, d3 = tg
        if st != 'bull':
            continue
        out.append({**r, 'pnl_atr': pnl, 'adx': adx, 'd3': d3, 'fade': d3 <= FADE_D3})
    return out, missing, untagged


def R(leg, bps):
    return (leg['pnl_atr'] - bps / 1e4 / (leg['entry_atr_pct'] / 100)) / STOP_ATR


def stats(ls, bps=BAR_BPS):
    if not ls:
        return {'n': 0, 'days': 0}
    rs = [R(x, bps) for x in ls]
    by_day = defaultdict(float)
    for x, r in zip(ls, rs):
        by_day[x['d']] += r
    net = sum(rs)
    top = (max(by_day.values()) / net) if net > 0 else ((min(by_day.values()) / net) if net < 0 else None)
    gains, losses = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    return {'n': len(rs), 'days': len(by_day), 'E': net / len(rs), 'sumR': net,
            'PF': gains / losses if losses > 0 else None, 'WR': sum(r > 0 for r in rs) / len(rs),
            'top_day_share': top, 'pos_days': sum(1 for v in by_day.values() if v > 0)}


def fmt(s):
    if not s.get('n'):
        return 'n=0'
    top = f"{s['top_day_share']:.0%}" if s['top_day_share'] is not None else 'n/a'
    pf = f"{s['PF']:.2f}" if s['PF'] is not None else 'n/a'
    return (f"n={s['n']} days={s['days']} E={s['E']:+.4f} sumR={s['sumR']:+.2f} PF={pf} WR={s['WR']:.0%} "
            f"top-day={top} pos-days={s['pos_days']}/{s['days']}")


def read(db, frm, to, unknown_passes=False, series=None):
    series = series or regime_series()
    closes = [c for c, _, _ in series]
    known, unknown, below, paths = load(db, frm, to, unknown_passes)
    lk, miss_k, untag_k = legs(known, paths, series, closes)
    lu, miss_u, _ = legs(unknown, paths, series, closes)
    fade = [x for x in lk if x['fade']]
    days = sorted({x['d'] for x in fade})
    cut = days[len(days) // 2] if days else None
    return {
        'window': [frm, to], 'fade_legs': fade,
        'fade': stats(fade), 'fade12': stats(fade, REF_BPS),
        'control': stats([x for x in lk if not x['fade']]), 'control12': stats([x for x in lk if not x['fade']], REF_BPS),
        'half1': stats([x for x in fade if cut and x['d'] < cut]), 'half2': stats([x for x in fade if cut and x['d'] >= cut]),
        'fade_h11_13': stats([x for x in fade if x['hour'] in (11, 13)]),
        'unknown_line': stats([x for x in lu if x['fade']]),
        'excluded_incomplete_path': miss_k + miss_u, 'excluded_untagged': untag_k,
        'excluded_n_confirms_0': len(below),
        'fade_by_day': {d: round(sum(R(x, BAR_BPS) for x in fade if x['d'] == d), 2) for d in days},
    }


def live_check(series):
    """Report-only: live London trades (entry = timestamp - hold_time_s) split fade / control, pnl_r as booked."""
    closes = [c for c, _, _ in series]
    db = copy_db(LIVE_TRADES_DB, prefix='lon_bull_fade_live_')
    try:
        con = sqlite3.connect(db)
        out = {'fade': [], 'control': []}
        for ts, hold, pnl_r, sd in con.execute('SELECT timestamp, hold_time_s, pnl_r, signal_data FROM trades'):
            s = json.loads(sd or '{}')
            if s.get('shadow_strategy') != 'burst_follow' or s.get('session') != 'london':
                continue
            tg = tag(_ms(ts) - int((hold or 0) * 1000), series, closes)
            if tg is None or tg[0] != 'bull' or tg[2] is None:
                continue
            out['fade' if tg[2] <= FADE_D3 else 'control'].append((ts[:10], pnl_r, s.get('stop_atr')))
        con.close()
    finally:
        drop_copy(db)
    res = {}
    for k, v in out.items():
        days = defaultdict(float)
        for d, r, _ in v:
            days[d] += r
        res[k] = {'n': len(v), 'days': len(days), 'sumR': round(sum(r for _, r, _ in v), 2),
                  'E': round(sum(r for _, r, _ in v) / len(v), 4) if v else None,
                  'sl6_n': sum(1 for *_, st in v if st == 6.0), 'by_day': {d: round(x, 2) for d, x in sorted(days.items())}}
    return res


def decide(res, today):
    f, c = res['fade'], res['control']
    n, days = f.get('n', 0), f.get('days', 0)
    if n < 30:
        return 'COUNTS-ONLY (fade n<30; no R-read yet)'
    kills = []
    if f['E'] >= 0.02:
        kills.append('fade E>=+0.02 @20bps at n>=30')
    formal = (n >= 50 and days >= 5) or today >= FORMAL_DATE
    if formal:
        if c.get('n', 0) and f['E'] >= c['E']:
            kills.append('fade E >= control E at the formal read')
        if f['top_day_share'] is not None and f['top_day_share'] > 0.40:
            kills.append('top-day > 40% of net at the formal read')
    if kills:
        return 'KILL: ' + '; '.join(kills)
    if not formal:
        return 'R-READ (no kill fired; formal read not yet due)'
    h1, h2 = res['half1'], res['half2']
    promote = (n >= 50 and days >= 5 and f['E'] <= -0.02 and c.get('n', 0) > 0 and c['E'] - f['E'] >= 0.05
               and f['top_day_share'] is not None and f['top_day_share'] <= 0.40
               and h1.get('n', 0) > 0 and h1['E'] < 0 and h2.get('n', 0) > 0 and h2['E'] < 0)
    if promote:
        return 'PROMOTE -> owner decision on wiring (NOT auto-wire: needs an engine gate + WLA mirror + test)'
    if today >= EXTENSION_DATE:
        return 'PARK (extension exhausted)'
    return 'INCONCLUSIVE -> one extension to ' + EXTENSION_DATE


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--db', help='existing /tmp copy of signal_shadow.db (default: make one via the backup API)')
    ap.add_argument('--validate', action='store_true')
    ap.add_argument('--peek', action='store_true')
    ap.add_argument('--live', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--today', help='override read date (YYYY-MM-DD)')
    a = ap.parse_args()
    today = a.today or datetime.now(timezone.utc).strftime('%Y-%m-%d')
    made = a.db is None
    db = copy_db(LIVE_DB) if made else a.db
    try:
        series = regime_series()
        if a.validate:
            res = read(db, BASIS_FROM, BASIS_TO, unknown_passes=True, series=series)
            f = res['fade']
            ok = (f.get('n') == BASIS['n'] and f.get('days') == BASIS['days'] and abs(f['E'] - BASIS['E20']) < 5e-4)
            print(f"VALIDATE basis {BASIS_FROM}..{BASIS_TO} (unknown n_confirms counted as pass, study convention)")
            print(f"  fade    @20bps: {fmt(f)}")
            print(f"  fade    @12bps: {fmt(res['fade12'])}")
            print(f"  control @20bps: {fmt(res['control'])}")
            print(f"  fade by day: {res['fade_by_day']} | excluded incomplete path {res['excluded_incomplete_path']}, "
                  f"untagged {res['excluded_untagged']}, n_confirms=0 {res['excluded_n_confirms_0']}")
            print(f"  registered: fade n={BASIS['n']} days={BASIS['days']} E={BASIS['E20']:+.4f}")
            print('VALIDATION', 'PASS' if ok else 'FAIL')
            if a.live:
                print('LIVE-REAL cross-check (all dates, report only):', json.dumps(live_check(series)))
            sys.exit(0 if ok else 1)
        res = read(db, FORWARD_FROM, '9999', series=series)
        f = res['fade']
        live = live_check(series) if a.live else None
        if a.peek or f.get('n', 0) < 30:
            print(f"PREREG-LON-BULL-FADE counts ({today}): forward fade legs n={f.get('n', 0)} days={f.get('days', 0)} "
                  f"| control n={res['control'].get('n', 0)} | unknown n_confirms (fade) {res['unknown_line'].get('n', 0)} "
                  f"| n_confirms=0 excluded {res['excluded_n_confirms_0']} | incomplete path {res['excluded_incomplete_path']}")
            print('POLICY: counts only until fade n>=30' if f.get('n', 0) < 30 else 'PEEK: counts only by request')
        else:
            verdict = decide(res, today)
            print(f"PREREG-LON-BULL-FADE read ({today}), forward from {FORWARD_FROM}Z")
            print(f"  fade    @20bps (bars): {fmt(f)}")
            print(f"  fade    @12bps (ref):  {fmt(res['fade12'])}")
            print(f"  control @20bps:        {fmt(res['control'])}")
            print(f"  fade halves: {fmt(res['half1'])} | {fmt(res['half2'])}")
            print(f"  fade h11+h13 only (Row 8 interaction): {fmt(res['fade_h11_13'])}")
            print(f"  UNKNOWN n_confirms line (never passes a bar): {fmt(res['unknown_line'])}")
            print(f"  excluded: n_confirms=0 {res['excluded_n_confirms_0']}, incomplete path {res['excluded_incomplete_path']}, "
                  f"untagged {res['excluded_untagged']}")
            print(f"VERDICT: {verdict}")
        if live:
            print('LIVE-REAL cross-check (all dates, report only):', json.dumps(live))
        if a.json:
            print(json.dumps({'n': f.get('n', 0), 'days': f.get('days', 0), 'today': today,
                              'verdict': decide(res, today)}))
    finally:
        if made:
            drop_copy(db)


if __name__ == '__main__':
    main()
