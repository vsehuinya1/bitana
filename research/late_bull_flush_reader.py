#!/usr/bin/env python3
"""PREREG-LATE-BULL-FLUSH reader of record — registered 2026-09-25T04:4xZ, RESEARCH_PLAN.md (EOF section).

Frozen population: shadow_trades strategy='burst_follow', side LONG, liq_imb >= 0.5, status != 'open',
session 'late' (bar-close hour 22-23 UTC), Mon-Fri, btc_trend_state='bull',
entry_vol_z >= 0 (NULL -> 0), decile >= 1 (NULL -> 0), n_confirms >= 1.
n_confirms source: the shadow_trades.n_confirms column (written since MIRROR CUT-OVER 3), else the exact
burst_snapshots join (symbol + bar_time), else UNKNOWN. Unknown rows are reported on their own line and
never pass a bar (READ-PROC-GATECOMPLETE; NULL != 0). Dedup (symbol, entry_time, side).
Exit, re-simulated from trade_r_path: SL 5 ATR, no TP, time exit at bar 12 (60m), stop-first.
Bar b <= bars_held is phase 'open' bar b; later bars come from phase 'post', where post bar 1 duplicates
the exit bar, so bar bars_held+k = post bar k+1. Legs without a complete 12-bar path are excluded and counted.
Metric: R = (pnl_atr - cost_atr) / 5, cost_atr = bps/1e4 / (entry_atr_pct/100).
Bars are judged at 20 bps round-trip; 12 bps (plan standard) is reported alongside.
Forward window: entry_time >= 2026-09-25T04:40Z (parity era by construction).
PROMOTE (ALL): n>=50, days>=10, E>=+0.05 @20bps, top-day<=40% of net, E(h22)>=0 and E(h23)>=0,
               live-symbol subset E>=0 at n>=20.
KILL (ANY):    E<0 @20bps at n>=30; top-day>40% at the formal read; live-symbol subset E<-0.02 at n>=20.
Cadence: counts only until n>=30, then R-reads. The formal read is at n>=50 and days>=10, or 2026-11-08,
whichever comes first. One extension to 2026-12-06, then park.

Modes:
  --validate   backdated gate: reproduce the registered in-sample basis (2026-08-21 -> 2026-09-23:
               n=31, 11 days, E=+0.1226 R @12bps, sumR=+3.80, top-day 59%). Exits 1 on mismatch.
  --peek       counts only (the pre-R-read policy)
  (default)    forward read with the frozen decision rule (counts only while n<30)
DB: the read never opens the live DB file. It takes a /tmp copy through the SQLite backup API (CLAUDE.md)
unless --db names an existing copy.
"""
import argparse
import json
import os
import sqlite3
import sys
import tempfile
from collections import defaultdict

LIVE_DB = '/root/bitana/storage/signal_shadow.db'
LIVE_YAML = '/root/bitana/config/live_burst_ny_asia.yaml'
FORWARD_FROM = '2026-09-25T04:40:00'
BASIS_FROM, BASIS_TO = '2026-08-21', '2026-09-24'
FORMAL_DATE, EXTENSION_DATE = '2026-11-08', '2026-12-06'
STOP_ATR, TIME_BARS = 5.0, 12
BAR_BPS, REF_BPS = 20.0, 12.0
BASIS = {'n': 31, 'days': 11, 'E12': 0.1226, 'sumR12': 3.80, 'top': 0.59}

POP_SQL = """SELECT t.id, t.symbol, t.side, t.entry_time, t.bars_held, t.entry_atr_pct, t.hour,
       t.n_confirms AS nc_col, b.n_confirms AS nc_snap, substr(t.entry_time, 1, 10) AS d
FROM shadow_trades t
LEFT JOIN burst_snapshots b ON b.symbol = t.symbol AND b.bar_time = t.entry_time
WHERE t.strategy = 'burst_follow' AND t.side = 'LONG' AND t.liq_imb >= 0.5 AND t.status <> 'open'
  AND t.session = 'late' AND t.hour IN (22, 23)
  AND CAST(strftime('%w', t.entry_time) AS INT) BETWEEN 1 AND 5
  AND t.btc_trend_state = 'bull'
  AND COALESCE(t.entry_vol_z, 0) >= 0 AND COALESCE(t.decile, 0) >= 1
  AND t.entry_time >= :frm AND t.entry_time < :to"""


def copy_db(src):
    fd, dst = tempfile.mkstemp(prefix='late_bull_flush_', suffix='.db', dir='/tmp')
    os.close(fd)
    with sqlite3.connect(f'file:{src}?mode=ro', uri=True, timeout=30) as s, sqlite3.connect(dst) as d:
        s.backup(d)
        d.execute('PRAGMA journal_mode=DELETE')   # copy inherits WAL; rollback mode leaves no -wal/-shm behind
    return dst


def live_symbols():
    import yaml
    with open(LIVE_YAML) as fh:
        return set(yaml.safe_load(fh)['symbols']['active'])


def load(db_path, frm, to):
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
            unknown.append(r)
        elif nc >= 1:
            known.append(r)
        else:
            below.append(r)
    paths = defaultdict(dict)
    ids = [r['id'] for r in known + unknown]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = (f"SELECT trade_id, phase, bar_num, r_low, r_close FROM trade_r_path WHERE trade_id IN "
             f"({','.join('?' * len(chunk))}) AND (phase = 'open' OR bar_num <= {TIME_BARS + 1})")
        for p in db.execute(q, chunk):
            paths[p['trade_id']][(p['phase'], p['bar_num'])] = (p['r_low'], p['r_close'])
    db.close()
    return known, unknown, below, paths


def simulate(r, path):
    """pnl in ATR at SL5 / no TP / 12 bars, stop-first; None if the 12-bar path is incomplete."""
    bh = r['bars_held'] or 0
    for b in range(1, TIME_BARS + 1):
        bar = path.get(('open', b)) if b <= bh else path.get(('post', b - bh + 1))
        if bar is None or bar[1] is None:
            return None
        lo, cl = bar
        if lo is not None and lo <= -STOP_ATR:
            return -STOP_ATR
        if b == TIME_BARS:
            return cl
    return None


def legs(rows, paths):
    out, missing = [], 0
    for r in rows:
        pnl = simulate(r, paths.get(r['id'], {}))
        if pnl is None:
            missing += 1
            continue
        atr_pct = r['entry_atr_pct'] or 0
        if atr_pct <= 0:
            missing += 1
            continue
        out.append({**r, 'pnl_atr': pnl})
    return out, missing


def R(leg, bps):
    return (leg['pnl_atr'] - bps / 1e4 / (leg['entry_atr_pct'] / 100)) / STOP_ATR


def stats(ls, bps):
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
            'PF': gains / losses if losses > 0 else None, 'top_day_share': top,
            'pos_days': sum(1 for v in by_day.values() if v > 0)}


def fmt(s):
    if not s.get('n'):
        return 'n=0'
    top = f"{s['top_day_share']:.0%}" if s['top_day_share'] is not None else 'n/a'
    pf = f"{s['PF']:.2f}" if s['PF'] is not None else 'n/a'
    return (f"n={s['n']} days={s['days']} E={s['E']:+.4f} sumR={s['sumR']:+.2f} PF={pf} "
            f"top-day={top} pos-days={s['pos_days']}/{s['days']}")


def read(db, frm, to):
    known, unknown, below, paths = load(db, frm, to)
    lk, miss_k = legs(known, paths)
    lu, miss_u = legs(unknown, paths)
    live = live_symbols()
    lv = [x for x in lk if x['symbol'] in live]
    return {
        'window': [frm, to], 'known_legs': lk, 'unknown_legs': lu,
        'excluded_incomplete_path': miss_k + miss_u, 'excluded_n_confirms_0': len(below),
        'bar': stats(lk, BAR_BPS), 'ref12': stats(lk, REF_BPS),
        'h22': stats([x for x in lk if x['hour'] == 22], BAR_BPS),
        'h23': stats([x for x in lk if x['hour'] == 23], BAR_BPS),
        'live_subset': stats(lv, BAR_BPS), 'unknown_line': stats(lu, BAR_BPS),
    }


def decide(res, today):
    s, lv = res['bar'], res['live_subset']
    n, days = s.get('n', 0), s.get('days', 0)
    if n < 30:
        return 'COUNTS-ONLY (n<30; no R-read yet)'
    kills = []
    if s['E'] < 0:
        kills.append('E<0 @20bps at n>=30')
    if lv.get('n', 0) >= 20 and lv['E'] < -0.02:
        kills.append('live-symbol subset E<-0.02 at n>=20')
    formal = (n >= 50 and days >= 10) or today >= FORMAL_DATE
    if formal and s['top_day_share'] is not None and s['top_day_share'] > 0.40:
        kills.append('top-day>40% at formal read')
    if kills:
        return 'KILL: ' + '; '.join(kills)
    if not formal:
        return 'R-READ (no kill fired; formal read not yet due)'
    promote = (n >= 50 and days >= 10 and s['E'] >= 0.05 and s['top_day_share'] is not None
               and s['top_day_share'] <= 0.40 and res['h22'].get('n', 0) > 0 and res['h22']['E'] >= 0
               and res['h23'].get('n', 0) > 0 and res['h23']['E'] >= 0
               and lv.get('n', 0) >= 20 and lv['E'] >= 0)
    if promote:
        return 'PROMOTE -> G1 evaluation + wiring review (NOT auto-wire; needs late arm + mirror strategy)'
    if today >= EXTENSION_DATE:
        return 'PARK (extension exhausted)'
    return 'INCONCLUSIVE -> one extension to ' + EXTENSION_DATE


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--db', help='existing /tmp copy of signal_shadow.db (default: make one via backup API)')
    ap.add_argument('--validate', action='store_true')
    ap.add_argument('--peek', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--today', help='override read date (YYYY-MM-DD)')
    a = ap.parse_args()
    from datetime import datetime, timezone
    today = a.today or datetime.now(timezone.utc).strftime('%Y-%m-%d')
    made = a.db is None
    db = copy_db(LIVE_DB) if made else a.db
    try:
        if a.validate:
            res = read(db, BASIS_FROM, BASIS_TO)
            r12 = res['ref12']
            ok = (r12.get('n') == BASIS['n'] and r12.get('days') == BASIS['days']
                  and abs(r12['E'] - BASIS['E12']) < 5e-4 and abs(r12['sumR'] - BASIS['sumR12']) < 0.01
                  and abs(r12['top_day_share'] - BASIS['top']) < 0.01 and res['unknown_line'].get('n', 0) == 0)
            print(f"VALIDATE basis {BASIS_FROM}..{BASIS_TO} @12bps: {fmt(r12)}")
            print(f"  registered: n={BASIS['n']} days={BASIS['days']} E=+{BASIS['E12']} sumR=+{BASIS['sumR12']} "
                  f"top-day {BASIS['top']:.0%} | unknown n_confirms rows: {res['unknown_line'].get('n', 0)} | "
                  f"excluded incomplete path: {res['excluded_incomplete_path']}")
            print('VALIDATION', 'PASS' if ok else 'FAIL')
            sys.exit(0 if ok else 1)
        res = read(db, FORWARD_FROM, '9999')
        s = res['bar']
        if a.peek or s.get('n', 0) < 30:
            print(f"PREREG-LATE-BULL-FLUSH counts ({today}): forward legs n={s.get('n', 0)} days={s.get('days', 0)} "
                  f"| unknown n_confirms {res['unknown_line'].get('n', 0)} | n_confirms=0 excluded "
                  f"{res['excluded_n_confirms_0']} | incomplete path {res['excluded_incomplete_path']}")
            print('POLICY: counts only until n>=30' if s.get('n', 0) < 30 else 'PEEK: counts only by request')
            if a.json:
                print(json.dumps({'n': s.get('n', 0), 'days': s.get('days', 0), 'today': today}))
            return
        verdict = decide(res, today)
        print(f"PREREG-LATE-BULL-FLUSH read ({today}), forward from {FORWARD_FROM}Z")
        print(f"  @20bps (bars): {fmt(s)}")
        print(f"  @12bps (ref):  {fmt(res['ref12'])}")
        print(f"  h22: {fmt(res['h22'])} | h23: {fmt(res['h23'])}")
        print(f"  live-symbol subset: {fmt(res['live_subset'])}")
        print(f"  UNKNOWN n_confirms line (never passes a bar): {fmt(res['unknown_line'])}")
        print(f"  excluded: n_confirms=0 {res['excluded_n_confirms_0']}, incomplete path {res['excluded_incomplete_path']}")
        print(f"VERDICT: {verdict}")
        if a.json:
            print(json.dumps({k: v for k, v in res.items() if not k.endswith('_legs')} | {'verdict': verdict}, default=str))
    finally:
        if made:
            for f in (db, db + '-wal', db + '-shm', db + '-journal'):
                if os.path.exists(f):
                    os.remove(f)


if __name__ == '__main__':
    main()
