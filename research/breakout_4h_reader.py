#!/usr/bin/env python3
"""Reader of record for PREREG-BREAKOUT-4H (paper), registered 2026-09-26 (owner order "Put the breakout rule on
paper yes and register"). Source: Risk Lab floor card "4h bos 36" (@BIGROCKS, BTC only), replicated on 20 coins x
6.7 years (reports/structural_edge_2026-09-25.md, "Risk Lab review"). DARK: nothing is wired; this reader only measures.

Frozen rule (every coin independently; one position per coin at a time):
  bars      Binance USDT-M 4h klines (UTC 00/04/.../20), closed bars only.
  pivot     swing high = a bar whose high is the max of itself and the 3 bars each side; known at the close of the
            3rd bar after it. The level stays until the next swing high replaces it.
  signal    close crosses above the last swing-high level (prior close <= level < this close) AND close > EMA200
            (EMA of 4h closes, span 200, adjust=False).
  entry     next bar's open. stop = entry - 2 x ATR14 (mean true range of the 14 bars ending at the signal bar).
  exit      stop intrabar (a gap through the stop fills at the open); otherwise the close of the 36th bar held.
  cost      20 bps round trip. R = (exit/entry - 1 - 0.002) / ((entry - stop)/entry).
Control (the drift a long earns without the trigger): a long at EVERY 4h open of the same coins and window with the
  same stop/exit (overlap allowed). Edge = forward E - control E. Inference by week: weekly sums of (R - control E),
  empty weeks included.
Universe: the 20 coins of PREREG-CAPITULATION-BASKET (capitulation_reader.UNIVERSE).
Forward window: entries >= 2026-09-26T08:00Z (first bar that closed after registration).
PROMOTE (ALL, forward, formal read): E >= +0.10R; edge vs control >= +0.10R; week-clustered t(edge) >= 1.5;
  top-5 weeks <= 60% of net.
KILL (ANY): E < 0 at n >= 100; at the formal read edge <= 0.
Formal read: n >= 150 closed trades over >= 16 weeks, or 2027-03-31, whichever first; one extension to 2027-06-30,
  then park. Promotion is an owner decision on a separately sized sleeve; never wired on the day of a loss.
--validate reproduces the basis from the public monthly archive (data.binance.vision; no API weight):
  2020 n=408 E +0.310R control +0.291R edge +0.019R t(week) +0.10
  2021-01 .. 2025-01 n=2336 E +0.276R control +0.071R edge +0.205R t(week) +1.93 top-5 weeks 54%
  2025-02 .. 2026-08 n=769 E +0.055R control -0.053R edge +0.108R t(week) +0.50 top-5 weeks 714%
  Read: small edge over random longs in every window, never significant alone; the recent window is thin.
Public data only; no account calls.
"""
import argparse
import io
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.append('/root/bitana/research')
import capitulation_reader as capr  # noqa: E402

UNIVERSE = capr.UNIVERSE
PIVOT, EMA_SPAN, ATR_N, STOP_ATR, HOLD, COST = 3, 200, 14, 2.0, 36, 0.0020
FORWARD_FROM = pd.Timestamp('2026-09-26T08:00:00Z')
FORMAL_DATE, EXTENSION_DATE = '2027-03-31', '2027-06-30'
PERIODS = [('2020', '2020-01-01', '2021-01-01'), ('2021-01 .. 2025-01', '2021-01-01', '2025-02-01'),
           ('2025-02 .. 2026-08', '2025-02-01', '2026-09-01')]
BASIS = {'2020': (408, 0.310), '2021-01 .. 2025-01': (2336, 0.276), '2025-02 .. 2026-08': (769, 0.055)}  # frozen 2026-09-26
CTX = ssl.create_default_context()


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


def archive_4h(sym, start='2019-12', end='2026-08'):
    rows = []
    for m in pd.period_range(start, end, freq='M'):
        b = _get(f'https://data.binance.vision/data/futures/um/monthly/klines/{sym}/4h/{sym}-4h-{m}.zip')
        if not b:
            continue
        z = zipfile.ZipFile(io.BytesIO(b))
        for line in z.read(z.namelist()[0]).decode().splitlines():
            if line and line[0].isdigit():
                rows.append([float(x) for x in line.split(',')[:5]])
    return _frame(rows)


def api_4h(sym, start):
    rows, t = [], int(start.timestamp() * 1000)
    now = int(time.time() * 1000)
    while t < now:
        r = json.load(urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=4h&startTime={t}&limit=1000',
            context=CTX, timeout=30))
        if not r:
            break
        rows += [[float(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])] for k in r if k[6] < now]
        t = r[-1][0] + 4 * 3600000
        if len(r) < 1000:
            break
        time.sleep(0.3)
    return _frame(rows)


def _frame(rows):
    if not rows:
        return pd.DataFrame(columns=list('ohlc'))
    a = np.array(rows)
    df = pd.DataFrame(a[:, 1:5], columns=list('ohlc'), index=pd.to_datetime(a[:, 0], unit='ms', utc=True))
    return df[~df.index.duplicated(keep='last')].sort_index()


def signals(df):
    h, c, n = df.h.values, df.c.values, len(df)
    level, last = np.full(n, np.nan), np.nan
    for i in range(2 * PIVOT, n):
        j = i - PIVOT
        if h[j] == h[j - PIVOT:j + PIVOT + 1].max():
            last = h[j]
        level[i] = last
    sig = np.zeros(n, bool)
    sig[1:] = (c[1:] > level[1:]) & (c[:-1] <= level[:-1])
    sig &= c > df.c.ewm(span=EMA_SPAN, adjust=False).mean().values
    sig[:EMA_SPAN] = False
    pc = df.c.shift(1)
    atr = (np.maximum(df.h, pc) - np.minimum(df.l, pc)).rolling(ATR_N).mean().values
    return sig, atr


def run(df, entries_from=None, every_bar=False):
    """Trades (entry_time, R, closed). every_bar=True -> the control (overlap allowed)."""
    o, h, l, c = df.o.values, df.h.values, df.l.values, df.c.values
    sig, atr = signals(df)
    n, out, busy = len(df), [], -1
    cand = np.where(np.isfinite(atr))[0] if every_bar else np.where(sig)[0]
    for s in cand:
        e = s + 1
        if e >= n or (not every_bar and e <= busy) or not np.isfinite(atr[s]):
            continue
        if entries_from is not None and df.index[e] < entries_from:
            continue
        ent = o[e]
        stop = ent - STOP_ATR * atr[s]
        if not stop < ent:
            continue
        x, j = None, e
        for j in range(e, min(n, e + HOLD)):
            if o[j] <= stop:
                x = o[j]
                break
            if l[j] <= stop:
                x = stop
                break
        closed = x is not None or e + HOLD <= n
        if x is None:
            j = min(n, e + HOLD) - 1
            x = c[j]
        out.append((df.index[e], (x / ent - 1 - COST) / ((ent - stop) / ent), closed))
        if not every_bar:
            busy = j
    return out


def summarize(tr, ctrl_E, a, b):
    t = pd.DataFrame(tr, columns=['t', 'R', 'closed'])
    t = t[(t.t >= a) & (t.t < b) & t.closed]
    if t.empty:
        return {'n': 0}
    weeks = pd.period_range(pd.Timestamp(a).tz_localize(None), (pd.Timestamp(b) - pd.Timedelta(days=1)).tz_localize(None), freq='W')
    ws = (t.R - ctrl_E).groupby(t.t.dt.tz_localize(None).dt.to_period('W')).sum().reindex(weeks, fill_value=0.0)
    net = t.groupby(t.t.dt.tz_localize(None).dt.to_period('W')).R.sum().sort_values(ascending=False)
    return {'n': len(t), 'weeks': int((ws != 0).sum()), 'E': t.R.mean(), 'ctrl': ctrl_E, 'edge': t.R.mean() - ctrl_E,
            't': ws.mean() / ws.std() * np.sqrt(len(ws)) if ws.std() > 0 else float('nan'),
            'top5': net.head(5).sum() / net.sum() if net.sum() > 0 else float('nan')}


def fmt(s):
    if not s.get('n'):
        return 'n=0'
    return (f"n={s['n']} weeks={s['weeks']} E={s['E']:+.3f}R control={s['ctrl']:+.3f}R edge={s['edge']:+.3f}R "
            f"t(week)={s['t']:+.2f} top-5 weeks={s['top5']:.0%}")


def pooled(frames, a, b, entries_from=None):
    tr, ct = [], []
    for df in frames.values():
        if len(df) < EMA_SPAN + 50:
            continue
        tr += run(df, entries_from)
        ct += [x for x in run(df, entries_from, every_bar=True) if a <= str(x[0]) < b and x[2]]
    ctrl_E = float(np.mean([x[1] for x in ct])) if ct else float('nan')
    return summarize(tr, ctrl_E, pd.Timestamp(a, tz='UTC'), pd.Timestamp(b, tz='UTC'))


def decide(s, today):
    n = s.get('n', 0)
    if n >= 100 and s['E'] < 0:
        return 'KILL: E<0 at n>=100'
    weeks_since = (pd.Timestamp(today, tz='UTC') - FORWARD_FROM).days / 7
    formal = (n >= 150 and weeks_since >= 16) or today >= FORMAL_DATE
    if not formal:
        return f'COUNTS-ONLY (formal at n>=150 over >=16 weeks or {FORMAL_DATE})'
    if s['edge'] <= 0:
        return 'KILL: edge vs control <= 0 at the formal read'
    if s['E'] >= 0.10 and s['edge'] >= 0.10 and s['t'] >= 1.5 and s['top5'] <= 0.60:
        return 'PROMOTE -> owner decision on a separately sized sleeve'
    return 'PARK (extension exhausted)' if today >= EXTENSION_DATE else 'INCONCLUSIVE -> one extension to ' + EXTENSION_DATE


def weekly_digest(now=None):
    """Telegram text for the weekly update (risk watch, Sundays): last 7 days, since start, open paper positions."""
    now = now or pd.Timestamp.now(tz='UTC')
    frames = {s: api_4h(s, FORWARD_FROM - pd.Timedelta(days=150)) for s in UNIVERSE}
    tot = pooled(frames, str(FORWARD_FROM), str(now.normalize() + pd.Timedelta(days=1)), entries_from=FORWARD_FROM)
    wk, opened = [], []
    for sym, df in frames.items():
        if len(df) < EMA_SPAN + 50:
            continue
        for t, r, closed in run(df, FORWARD_FROM):
            if not closed:
                opened.append(f"{sym.replace('USDT', '')} {r:+.1f}R")
            elif t + pd.Timedelta(hours=4 * HOLD) >= now - pd.Timedelta(days=7):
                wk.append(r)
    head = (f"last 7 days: {len(wk)} closed, {sum(wk):+.1f}R ({np.mean(wk):+.2f}R/trade)" if wk
            else "last 7 days: no closed trades")
    since = (f"since {FORWARD_FROM:%d %b}: {tot['n']} closed, E {tot['E']:+.3f}R, random longs {tot['ctrl']:+.3f}R, "
             f"edge {tot['edge']:+.3f}R (t {tot['t']:+.2f})" if tot.get('n') else f"since {FORWARD_FROM:%d %b}: none closed yet")
    op = f"open now: {len(opened)}" + (f" ({', '.join(opened[:8])}{'…' if len(opened) > 8 else ''})" if opened else '')
    return f"PREREG-BREAKOUT-4H weekly | {head} | {since} | {op} | {decide(tot, now.strftime('%Y-%m-%d'))}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--validate', action='store_true')
    a = ap.parse_args()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if a.validate:
        frames = {s: archive_4h(s) for s in UNIVERSE}
        ok = True
        for lab, x, y in PERIODS:
            s = pooled(frames, x, y)
            print(f'VALIDATE {lab:<20} {fmt(s)}')
            if BASIS:
                bn, bE = BASIS[lab]
                ok &= s.get('n') == bn and abs(s['E'] - bE) < 1e-3
        print('VALIDATION', ('PASS' if ok else 'FAIL') if BASIS else 'BASIS NOT FROZEN')
        sys.exit(0 if (ok and BASIS) else 1)
    frames = {s: api_4h(s, FORWARD_FROM - pd.Timedelta(days=150)) for s in UNIVERSE}
    s = pooled(frames, str(FORWARD_FROM), str(pd.Timestamp.now(tz='UTC').normalize() + pd.Timedelta(days=1)), entries_from=FORWARD_FROM)
    print(f'PREREG-BREAKOUT-4H forward read ({today}), entries from {FORWARD_FROM.isoformat()}: {fmt(s)}')
    print('VERDICT:', decide(s, today))


if __name__ == '__main__':
    main()
