#!/usr/bin/env python3
"""Reader of record for PREREG-DBL-DIV-4H (paper), registered 2026-10-03 (owner order "Add" on the offer "put it on paper
(stop under the lows, 7-day exit, top-200 coins) and track it forward"). Source: reports/structural_edge_2026-09-25.md,
"4h patterns + RSI divergence" (commits 94f49ff, 113dac6). DARK: nothing is wired; this reader only measures.

Frozen rule (every coin independently; Binance USDT-M 4h klines, closed bars only, bar time = open time):
  pivots      swing low/high = the extreme of bars j-3..j+3; usable at bar i once confirmed (j <= i-3). RSI14 = Wilder
              (EWM alpha 1/14) on 4h closes.
  DBL         the last two confirmed pivot lows PL1 < PL2 inside the last 180 bars (30 days): lows within 3% (log),
              >= 12 bars apart, signal no later than 60 bars after PL2; neckline = highest high from PL1 to PL2,
              >= 5% above the lower low. Signal = the first close above the neckline (prior close <= neckline);
              each (PL1, PL2) pair signals once.
  DIVERGENCE  bullish RSI divergence: low(PL2) < low(PL1) and RSI(PL2) > RSI(PL1).
  TRACKED     DBL + DIVERGENCE.
  entry       next bar's open. stop = the lower of the two lows (no entry when stop >= entry).
  exit        X1: stop intrabar (a gap through the stop fills at that bar's open), otherwise the close of the 42nd bar
              held (7 days). cost 0.30% round trip. R = (exit/entry - 1 - 0.003) / ((entry - stop)/entry).
  positions   one DBL position per coin at a time (any DBL signal, with or without divergence, holds the slot: this is
              how the basis was measured).
Universe: research/pattern_div_4h_universe.json, the 200 perps frozen 2026-10-03 (top past-year quote volume, smallest
  ~$14M/day). A coin that stops trading keeps its open trade until its data ends; it then exits at the last close.
Report-only lines (verdict unchanged):
  WEDGE + DIVERGENCE: falling wedge (last two pivot highs and lows in 180 bars, both lines falling, upper steeper,
    span >= 30 bars, lines not crossed) broken by a close above the upper line; stop = PL2; same X1 exit and cost;
    one WEDGE position per coin.
  DBL without divergence.
Control (what a long earns here without the pattern): a long every 12th 4h bar per coin (offset crc32(symbol) % 12),
  stop = the prior 30-bar low, same X1 exit and cost, overlap allowed. Edge = E - control E; inference by week
  (weekly sums of R - control E, empty weeks included).
Forward window: entries >= 2026-10-03T12:00Z (fixed at registration, ~07Z: the signal bar closes after it). Data: fapi klines from
  FETCH_FROM (fixed, so pivots / RSI / slots are deterministic), cached in logs/paper_cache/p4h_div/, incremental.
PROMOTE (ALL, at the formal read): E >= +0.10R; edge vs control >= +0.10R; t(week) >= 1.5; top-5 weeks <= 60% of net.
KILL (ANY): E < 0 at n >= 50; at the formal read edge <= 0.
Formal read: n >= 60 closed trades over >= 26 weeks, or 2027-06-30, whichever first; one extension to 2027-12-31, then
  park. Promotion is an owner decision on a separately sized sleeve; never wired on the day of a loss.
Basis (frozen 2026-10-03; research, rules fixed before running):
  past year 2025-10-03 .. 2026-09-25, these 200 coins:     DBL+div n=77 WR 47% E +0.240R total +18R PF 1.68
    (report-only lines, same window: WEDGE+div n=149 E +0.209R; DBL without divergence n=460 E -0.061R)
  out-of-sample 2023-01 .. 2025-09, 805 perps incl. delisted, liquid at the signal: DBL+div n=235 E +0.17R +39R PF 1.32
  (the measured-move exit failed out-of-sample; wedge+div OOS +0.33R but its past year leans on HEI +47R).
  1h / 15m versions of every pattern: negative (113dac6) - 4h is the lowest timeframe that holds.
--validate reproduces the past-year basis from the public archive (data.binance.vision; no API weight), data cut at
  the 2026-10-02 16:00 bar like the research run. The out-of-sample figure is not re-run here (805 coins incl.
  delisted, research scratch data).
Public data only; no account calls.
"""
import argparse
import io
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
UNIVERSE = json.load(open(os.path.join(HERE, 'pattern_div_4h_universe.json')))['symbols']
PIV, LOOK, HOLD, COST = 3, 180, 42, 0.003
DBL_TOL, DBL_GAP, DBL_AGE, NECK = 0.03, 12, 60, 1.05
WEDGE_SPAN, CTRL_EVERY, CTRL_LOW = 30, 12, 30
FORWARD_FROM = pd.Timestamp('2026-10-03T12:00:00Z')
FETCH_FROM = pd.Timestamp('2026-06-01T00:00:00Z')        # fixed warm-up: ~740 bars before the forward window
FORMAL_DATE, EXTENSION_DATE = '2027-06-30', '2027-12-31'
VAL_W0, VAL_W1 = pd.Timestamp('2025-10-03', tz='UTC'), pd.Timestamp('2026-09-25', tz='UTC')
VAL_END = pd.Timestamp('2026-10-02T16:00:00Z')            # last bar (open time) of the research data
BASIS = {'DBL+div': (77, 0.2400)}   # frozen 2026-10-03 (n, E); report-only past year: WEDGE+div n=149 E +0.209, DBL no div n=460 E -0.061
CACHE = '/root/bitana/logs/paper_cache/p4h_div'
STATE = '/root/bitana/logs/paper_cache/pattern_div_4h.json'
CTX = ssl.create_default_context()


# ---------------------------------------------------------------- data
def _get(url):
    for a in range(3):
        try:
            return urllib.request.urlopen(url, context=CTX, timeout=60).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 + a)
        except Exception:
            time.sleep(2 + a)
    return None


def _frame(rows):
    if not rows:
        return pd.DataFrame(columns=list('ohlc'), index=pd.DatetimeIndex([], tz='UTC'))
    a = np.array(rows, dtype=float)
    df = pd.DataFrame(a[:, 1:5], columns=list('ohlc'), index=pd.to_datetime(a[:, 0].astype('int64'), unit='ms', utc=True))
    return df[~df.index.duplicated(keep='last')].sort_index()


def _zip_rows(b):
    z = zipfile.ZipFile(io.BytesIO(b))
    return [[float(x) for x in line.split(',')[:5]] for line in z.read(z.namelist()[0]).decode().splitlines()
            if line and line[0].isdigit()]


def archive_4h(sym, start='2025-04', end_month='2026-09', days=('2026-10-01', '2026-10-02')):
    base = 'https://data.binance.vision/data/futures/um'
    rows = []
    for m in pd.period_range(start, end_month, freq='M'):
        b = _get(f'{base}/monthly/klines/{sym}/4h/{sym}-4h-{m}.zip')
        if b:
            rows += _zip_rows(b)
    for d in days:
        b = _get(f'{base}/daily/klines/{sym}/4h/{sym}-4h-{d}.zip')
        if b:
            rows += _zip_rows(b)
    return _frame(rows)


def api_4h(sym):
    """Closed 4h bars from FETCH_FROM (cached, incremental) + the open price of the bar now forming (or None)."""
    os.makedirs(CACHE, exist_ok=True)
    path = f'{CACHE}/{sym}.pkl'
    try:
        df = pd.read_pickle(path)
    except Exception:
        df = _frame([])
    t = int(((df.index[-1] + pd.Timedelta(hours=4)) if len(df) else FETCH_FROM).timestamp() * 1000)
    now = int(time.time() * 1000)
    rows, nxt = [], None
    while t < now:
        lim = 99 if now - t < 98 * 4 * 3600000 else 1000           # weight 1 for the routine incremental call
        b = _get(f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=4h&startTime={t}&limit={lim}')
        r = json.loads(b) if b else []
        if not r:
            break
        for k in r:
            if k[6] < now:
                rows.append([float(k[0])] + [float(x) for x in k[1:5]])
            else:
                nxt = float(k[1])
        t = r[-1][0] + 4 * 3600000
        if len(r) < lim:
            break
    if rows:
        df = pd.concat([df, _frame(rows)])
        df = df[~df.index.duplicated(keep='last')].sort_index()
        df.to_pickle(path)
    return df, nxt


# ---------------------------------------------------------------- the rule
def rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    up = pd.Series(np.maximum(d, 0)).ewm(alpha=1 / n, adjust=False).mean()
    dn = pd.Series(np.maximum(-d, 0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).values


def _exit(o, h, l, c, e, stop, ent, strict):
    """X1 exit from entry bar e. Returns (exit_px, exit_idx, why) or None (strict and the hold runs past the data)."""
    n = len(o)
    for j in range(e, min(n, e + HOLD)):
        if j > e and o[j] <= stop:
            return o[j], j, 'stop (gap)'
        if l[j] <= stop:
            return stop, j, 'stop'
    if e + HOLD > n:
        return None if strict else (c[n - 1] if n > e else ent, n - 1, 'open')
    return c[e + HOLD - 1], e + HOLD - 1, '7 days'


def scan(sym, df, w0, w1, next_open=None, strict=False, control=True):
    """Signals with bar-i open time in [w0, w1] and their X1 trades. Mirrors the research loop (patterns_4h.py, X1)."""
    t = df.index
    o, h, l, c = (df[k].values.astype(float) for k in 'ohlc')
    n = len(c)
    if n < 210:
        return []
    R = rsi(c)
    LH, LL = np.log(h), np.log(l)
    ph = [j for j in range(PIV, n - PIV) if h[j] == h[j - PIV:j + PIV + 1].max()]
    pl = [j for j in range(PIV, n - PIV) if l[j] == l[j - PIV:j + PIV + 1].min()]
    lo30 = pd.Series(l).shift(1).rolling(CTRL_LOW).min().values
    off = zlib.crc32(sym.encode()) % CTRL_EVERY
    busy, dbl_done, ip, il, out = {}, set(), 0, 0, []
    last = n if (next_open is not None and not strict) else n - 1     # forward: a signal at the last close enters now
    for i in range(200, last):
        if t[i] < w0 or t[i] > w1:
            continue
        while ip < len(ph) and ph[ip] <= i - PIV:
            ip += 1
        while il < len(pl) and pl[il] <= i - PIV:
            il += 1
        PH = [j for j in ph[max(0, ip - 6):ip] if j >= i - LOOK]
        PL = [j for j in pl[max(0, il - 6):il] if j >= i - LOOK]
        sigs = []
        if len(PH) >= 2 and len(PL) >= 2:
            a1, a2 = PH[-2], PH[-1]
            b1, b2 = PL[-2], PL[-1]
            su, sl = (LH[a2] - LH[a1]) / (a2 - a1), (LL[b2] - LL[b1]) / (b2 - b1)
            up = lambda k: LH[a2] + su * (k - a2)                     # noqa: E731
            lw = lambda k: LL[b2] + sl * (k - b2)                     # noqa: E731
            if i - min(a1, b1) >= WEDGE_SPAN and up(i) > lw(i) and np.log(c[i]) > up(i) and np.log(c[i - 1]) <= up(i - 1):
                if su < 0 and sl < 0 and su < sl:
                    sigs.append(('WEDGE', float(np.exp(up(i))), float(l[b2]), bool(l[b2] < l[b1] and R[b2] > R[b1])))
        if len(PL) >= 2:
            b1, b2 = PL[-2], PL[-1]
            if abs(LL[b2] - LL[b1]) <= DBL_TOL and b2 - b1 >= DBL_GAP and i - b2 <= DBL_AGE and (b1, b2) not in dbl_done:
                neck, low = h[b1:b2 + 1].max(), min(l[b1], l[b2])
                if neck >= low * NECK and c[i] > neck and c[i - 1] <= neck:
                    sigs.append(('DBL', float(neck), float(low), bool(l[b2] < l[b1] and R[b2] > R[b1])))
                    dbl_done.add((b1, b2))
        if control and i % CTRL_EVERY == off and np.isfinite(lo30[i]):
            sigs.append(('CONTROL', float(c[i]), float(lo30[i]), False))
        for pat, level, stop, div in sigs:
            if pat != 'CONTROL' and i + 1 <= busy.get(pat, -1):
                continue
            e = i + 1
            ent = o[e] if e < n else next_open
            if ent is None or not (stop < ent):
                continue
            x = _exit(o, h, l, c, e, stop, ent, strict)
            if x is None:
                continue
            px, j, why = x
            closed = why != 'open'
            net = px / ent - 1 - COST
            risk = (ent - stop) / ent
            out.append({'sym': sym, 'pat': pat, 'div': div, 'signal': t[i], 'entry_time': t[i] + pd.Timedelta(hours=4),
                        'entry': float(ent), 'stop': float(stop), 'level': float(level), 'risk_pct': 100 * risk,
                        'exit_time': (t[j] + pd.Timedelta(hours=4)) if closed else None, 'exit': float(px) if closed else None,
                        'mark': float(px), 'why': why, 'bars': (j - e + 1) if closed else max(0, n - e),
                        'net': net, 'R': net / risk, 'closed': closed})
            if pat != 'CONTROL':
                busy[pat] = j if closed else 10 ** 9
    return out


def line(trades, pat, div):
    return [x for x in trades if x['pat'] == pat and (div is None or x['div'] == div)]


def summarize(tr, ctrl_E, a, b):
    t = pd.DataFrame([(x['entry_time'], x['R']) for x in tr if x['closed']], columns=['t', 'R'])
    if t.empty:
        return {'n': 0}
    t = t[(t.t >= a) & (t.t < b)]
    if t.empty:
        return {'n': 0}
    Rv = t.R.values
    w, ls = Rv[Rv > 0].sum(), -Rv[Rv <= 0].sum()
    weeks = pd.period_range(pd.Timestamp(a).tz_localize(None), (pd.Timestamp(b) - pd.Timedelta(days=1)).tz_localize(None), freq='W')
    wk = t.t.dt.tz_localize(None).dt.to_period('W')
    ws = (t.R - ctrl_E).groupby(wk).sum().reindex(weeks, fill_value=0.0)
    net = t.groupby(wk).R.sum().sort_values(ascending=False)
    return {'n': len(t), 'weeks': int((ws != 0).sum()), 'wr': float((Rv > 0).mean()), 'E': float(Rv.mean()),
            'total': float(Rv.sum()), 'pf': float(w / ls) if ls > 0 else float('inf'), 'ctrl': ctrl_E,
            'edge': float(Rv.mean() - ctrl_E),
            't': float(ws.mean() / ws.std() * np.sqrt(len(ws))) if len(ws) > 1 and ws.std() > 0 else float('nan'),
            'top5': float(net.head(5).sum() / net.sum()) if net.sum() > 0 else float('nan')}


def fmt(s):
    if not s.get('n'):
        return 'n=0'
    f = lambda v, spec: 'n/a' if v is None or (isinstance(v, float) and not np.isfinite(v)) else format(v, spec)  # noqa: E731
    return (f"n={s['n']} WR={s['wr']:.0%} E={s['E']:+.3f}R total={s['total']:+.1f}R PF={f(s['pf'], '.2f')} "
            f"control={f(s['ctrl'], '+.3f')}R edge={f(s['edge'], '+.3f')}R t(week)={f(s['t'], '+.2f')} "
            f"top-5 weeks={f(s['top5'], '.0%')}")


def decide(s, today):
    n = s.get('n', 0)
    if n >= 50 and s['E'] < 0:
        return 'KILL: E<0 at n>=50'
    weeks_since = (pd.Timestamp(today, tz='UTC') - FORWARD_FROM).days / 7
    formal = (n >= 60 and weeks_since >= 26) or today >= FORMAL_DATE
    if not formal:
        return f'COUNTS-ONLY (formal at n>=60 over >=26 weeks or {FORMAL_DATE})'
    if s['edge'] <= 0:
        return 'KILL: edge vs control <= 0 at the formal read'
    if s['E'] >= 0.10 and s['edge'] >= 0.10 and s['t'] >= 1.5 and s['top5'] <= 0.60:
        return 'PROMOTE -> owner decision on a separately sized sleeve'
    return 'PARK (extension exhausted)' if today >= EXTENSION_DATE else 'INCONCLUSIVE -> one extension to ' + EXTENSION_DATE


# ---------------------------------------------------------------- forward read
def read(now=None):
    """Forward trades (all lines) + stats; fetches only new bars (cache). Returns a JSON-able dict."""
    now = now or pd.Timestamp.now(tz='UTC')
    trades, stale = [], []
    for s in UNIVERSE:
        df, nxt = api_4h(s)
        if len(df) and df.index[-1] < now - pd.Timedelta(hours=12):
            stale.append(s)                    # stopped trading: open trades exit at the last close (no next open)
            nxt = None
        for x in scan(s, df, FORWARD_FROM - pd.Timedelta(hours=4), now, next_open=nxt):
            if x['entry_time'] < FORWARD_FROM:
                continue
            if s in stale and not x['closed']:
                x.update(closed=True, why='data ended', exit_time=df.index[-1] + pd.Timedelta(hours=4), exit=x['mark'])
            trades.append(x)
        time.sleep(0.1)
    ctrl = [x['R'] for x in trades if x['pat'] == 'CONTROL' and x['closed']]
    ctrl_E = float(np.mean(ctrl)) if ctrl else float('nan')
    end = now.normalize() + pd.Timedelta(days=1)
    lines = {'DBL+div': line(trades, 'DBL', True), 'WEDGE+div': line(trades, 'WEDGE', True), 'DBL no div': line(trades, 'DBL', False)}
    stats = {k: summarize(v, ctrl_E, FORWARD_FROM, end) for k, v in lines.items()}
    today = now.strftime('%Y-%m-%d')
    return {'built': now.isoformat(), 'forward_from': FORWARD_FROM.isoformat(), 'control_E': ctrl_E,
            'control_n': len(ctrl), 'stats': stats, 'verdict': decide(stats['DBL+div'], today), 'stale': stale,
            'trades': sorted([x for x in trades if x['pat'] != 'CONTROL'], key=lambda x: x['entry_time'], reverse=True)}


def _jsonable(x):
    if isinstance(x, (pd.Timestamp, datetime)):
        return x.isoformat()
    if isinstance(x, float) and not np.isfinite(x):
        return None
    if isinstance(x, (np.floating, np.integer, np.bool_)):
        return _jsonable(x.item())
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def update():
    """Write the state JSON the risk watch (alerts) and the Paper Lab read."""
    st = _jsonable(read())
    tmp = STATE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(st, f)
    os.replace(tmp, STATE)
    return st


def weekly_digest(st, now=None):
    now = now or pd.Timestamp.now(tz='UTC')
    cut = (now - pd.Timedelta(days=7)).isoformat()
    tr = [x for x in st['trades'] if x['pat'] == 'DBL' and x['div']]
    wk = [x['R'] for x in tr if x['closed'] and x['exit_time'] >= cut]
    op = [f"{x['sym'][:-4]} {x['R']:+.1f}R" for x in tr if not x['closed']]
    s = st['stats']
    return (f"PREREG-DBL-DIV-4H weekly | last 7 days: {len(wk)} closed {sum(wk):+.1f}R | since "
            f"{FORWARD_FROM:%d %b}: {fmt(s['DBL+div'])} | open now: {len(op)}" + (f" ({', '.join(op[:8])})" if op else '')
            + f" | report-only wedge+div: {fmt(s['WEDGE+div'])} | {st['verdict']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--validate', action='store_true')
    ap.add_argument('--update', action='store_true', help='write the state JSON (risk watch / Paper Lab)')
    a = ap.parse_args()
    if a.validate:
        with ThreadPoolExecutor(8) as ex:
            frames = dict(zip(UNIVERSE, ex.map(archive_4h, UNIVERSE)))
        tr = []
        for s, df in frames.items():
            tr += scan(s, df[df.index <= VAL_END], VAL_W0, VAL_W1, strict=True, control=False)
        res = {}
        for lab, pat, div in (('DBL+div', 'DBL', True), ('WEDGE+div', 'WEDGE', True), ('DBL no div', 'DBL', False)):
            R = np.array([x['R'] for x in line(tr, pat, div)])
            w, ls = R[R > 0].sum(), -R[R <= 0].sum()
            res[lab] = (len(R), R.mean() if len(R) else float('nan'))
            print(f"VALIDATE {lab:<11} n={len(R)} WR={(R > 0).mean():.0%} E={R.mean():+.4f}R total={R.sum():+.1f}R "
                  f"PF={w / ls if ls else float('inf'):.2f}")
        print('coins with data:', sum(len(f) > 0 for f in frames.values()), '/', len(frames))
        bn, bE = BASIS['DBL+div']
        ok = res['DBL+div'][0] == bn and abs(res['DBL+div'][1] - bE) < 5e-4
        print('VALIDATION', 'PASS' if ok else 'FAIL', f"(basis DBL+div n={bn} E={bE:+.4f}R)")
        sys.exit(0 if ok else 1)
    st = update() if a.update else _jsonable(read())
    print(f"PREREG-DBL-DIV-4H forward read, entries from {FORWARD_FROM.isoformat()}")
    for k, v in st['stats'].items():
        print(f"  {k:<11} {fmt(v)}" + ('' if k == 'DBL+div' else '  (report-only)'))
    print(f"  control (long every 12th bar): n={st['control_n']} E={st['control_E']}")
    op = [x for x in st['trades'] if not x['closed']]
    print(f"  open: {len(op)}" + ''.join(f"\n    {x['pat']}{'+div' if x['div'] else ''} {x['sym']} entry {x['entry']:.6g} "
                                         f"stop {x['stop']:.6g} ({x['risk_pct']:.1f}%) {x['R']:+.2f}R" for x in op))
    print('VERDICT:', st['verdict'])


if __name__ == '__main__':
    main()
