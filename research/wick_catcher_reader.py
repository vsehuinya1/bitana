#!/usr/bin/env python3
"""Reader of record for PREREG-WICK-CATCHER (paper), registered 2026-09-27 (owner order "Register").
Source: internet-sourced cascade ideas, test #1 (reports/structural_edge_2026-09-25.md, "five ideas from public trader
sources"). Mechanism: provide liquidity to forced sellers with resting limit bids deep in liquidation wicks (the HLP /
market-maker side of a cascade). DARK: no orders are placed; fills are simulated on public 5m klines.

Frozen rule (each of the 20 coins of capitulation_reader.UNIVERSE independently; one position per coin):
  every hour H: ATR1h = mean true range of the last 14 hourly bars (known at H's close); skip if ATR1h/close < 0.1%;
  resting LIMIT BUY at L = close(H) - 5 x ATR1h, live for hour H+1 only (cancel/replace hourly).
  FILL (strict, queue-safe): the first 5m bar in H+1 whose low <= L - 0.1 x ATR1h; fill price = min(that bar's open, L).
  EXIT: limit sell at the pre-wick price close(H) (first 5m bar whose high reaches it), else the 5m open 24h after the fill.
  COST: 0.12% round trip (maker entry 0.02%, exit 0.05% + 0.05% slippage).  R/return = exit/fill - 1 - 0.12%.
Report-only second book: k = 8, exit = 4h hold (same fill rule).
Forward window: fills from 2026-09-27T10:00Z.
PROMOTE (ALL, formal read): mean net >= +0.5%/fill; day-clustered t >= 1.5; net positive in >= 3 distinct calendar
  months; no single fill below -40%. (Concentration bar set after seeing the 2024 basis, before any forward data: the
  basis has top-5 fill-days = 88% of net, so a top-5 cap would fail a known-good year; the month-spread bar still rules
  out a single-event result.) -> owner decision on a live design (resting maker bids on the exchange, hourly refresh, margin
  reserved, per-fill size small: the tail is a collapsing coin).
KILL (ANY): mean < -1% at n >= 30; mean < 0 at the formal read.
Formal read: n >= 60 closed fills over >= 10 distinct fill days, or 2027-06-30; one extension to 2027-12-31, then park.
--validate: recomputes the frozen 2024 basis from the public monthly archive (Jan-Dec 2024 fills, Dec-2023 warm-up):
  n=233 over 46 days, mean +3.11%, median +3.22%, hit 70%, t +2.02, top-5 days 88%, worst -15.7%.
"""
import argparse
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.append('/root/bitana/research')
import capitulation_reader as capr  # noqa: E402
import paper_klines as pk  # noqa: E402

UNIVERSE = capr.UNIVERSE
K, K2, COST, STRICT = 5.0, 8.0, 0.0012, 0.1
FORWARD_FROM = pd.Timestamp('2026-09-27T10:00:00Z')
FORMAL_DATE, EXTENSION_DATE = '2027-06-30', '2027-12-31'
BASIS = {'n': 233, 'mean': 0.0311}   # frozen 2026-09-27 from --validate (2024 fills)


def simulate(df, frm, k=K, exit_mode='tp', now=None):
    """Paper fills for one coin's 5m frame. Returns list of dicts; open trades marked at the last close."""
    if len(df) < 400:
        return []
    now = now or df.index[-1] + pd.Timedelta(minutes=5)
    H = df.resample('h').agg({'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    tr = np.maximum(H.h, H.c.shift(1)) - np.minimum(H.l, H.c.shift(1))
    atr = tr.rolling(14).mean()
    o, h, l, c = df.o.values, df.h.values, df.l.values, df.c.values
    t5 = df.index; out = []; busy_until = pd.Timestamp.min.tz_localize('UTC')
    for hr, a_ in atr.items():
        nxt = hr + pd.Timedelta(hours=1)
        if nxt < frm or not np.isfinite(a_) or a_ / H.c[hr] < 0.001 or nxt < busy_until:
            continue
        L = H.c[hr] - k * a_; thr = L - STRICT * a_; ref = H.c[hr]
        i0, i1 = t5.searchsorted(nxt), t5.searchsorted(nxt + pd.Timedelta(hours=1))
        hit = np.where(l[i0:i1] <= thr)[0]
        if not len(hit):
            continue
        fb = i0 + hit[0]; fill = min(o[fb], L); tf = t5[fb]
        if tf < busy_until:
            continue
        horizon = tf + pd.Timedelta(hours=24 if exit_mode == 'tp' else 4)
        j_end = t5.searchsorted(horizon)
        x, tx, closed, why = None, None, False, 'open'
        if exit_mode == 'tp':
            hh = np.where(h[fb:j_end] >= ref)[0]
            if len(hh):
                x, tx, closed, why = ref, t5[fb + hh[0]], True, 'tp'
        if x is None:
            if j_end < len(t5) and t5[j_end] >= horizon:
                x, tx, closed, why = o[j_end], t5[j_end], True, 'time'
            else:
                x, tx = c[-1], t5[-1]                                      # still open: mark at the last close
        out.append({'t': tf, 'fill': float(fill), 'ref': float(ref), 'level_k': k, 'exit_t': tx, 'exit': float(x),
                    'net': float(x / fill - 1 - COST), 'closed': closed, 'why': why,
                    'depth_pct': float(fill / ref - 1)})
        busy_until = tx if closed else pd.Timestamp.max.tz_localize('UTC')
    return out


def stats(rows):
    cl = [r for r in rows if r['closed']]
    if not cl:
        return {'n': 0, 'open': len(rows)}
    x = pd.DataFrame(cl); x['d'] = x.t.dt.floor('D')
    ds = x.net.groupby(x.d).sum()
    t = ds.mean() / ds.std() * np.sqrt(len(ds)) if len(ds) > 2 and ds.std() > 0 else float('nan')
    top = ds.sort_values(ascending=False).head(5).sum() / ds.sum() if ds.sum() > 0 else float('nan')
    pm = int((x.net.groupby(x.t.dt.strftime('%Y-%m')).sum() > 0).sum())
    return {'n': len(cl), 'days': int(x.d.nunique()), 'pos_months': pm, 'mean': float(x.net.mean()), 'median': float(x.net.median()),
            'hit': float((x.net > 0).mean()), 't': float(t), 'top5': float(top), 'worst': float(x.net.min()),
            'open': len(rows) - len(cl)}


def fmt(s):
    if not s.get('n'):
        return f"n=0 (open {s.get('open', 0)})"
    return (f"n={s['n']} days={s['days']} mean {100 * s['mean']:+.2f}% median {100 * s['median']:+.2f}% hit {s['hit']:.0%} "
            f"t {s['t']:+.2f} top-5 days {s['top5']:.0%} positive months {s['pos_months']} worst {100 * s['worst']:+.1f}% (open {s['open']})")


def decide(s, today):
    n = s.get('n', 0)
    if n >= 30 and s['mean'] < -0.01:
        return 'KILL: mean < -1% at n >= 30'
    formal = (n >= 60 and s.get('days', 0) >= 10) or today >= FORMAL_DATE
    if not formal:
        return f'COUNTS-ONLY (formal at 60 fills over 10 days or {FORMAL_DATE})'
    if n == 0 or s['mean'] < 0:
        return 'KILL: mean < 0 at the formal read'
    if s['mean'] >= 0.005 and s['t'] >= 1.5 and s['pos_months'] >= 3 and s['worst'] >= -0.40:
        return 'PROMOTE -> owner decision on a live design (resting maker bids, small per-fill size)'
    return 'PARK (extension exhausted)' if today >= EXTENSION_DATE else 'INCONCLUSIVE -> one extension to ' + EXTENSION_DATE


def read(now=None, frames=None):
    frames = frames or {s: pk.live(s, 'perp', FORWARD_FROM - pd.Timedelta(days=2)) for s in UNIVERSE}
    rows, rows2 = [], []
    for s, df in frames.items():
        for r in simulate(df, FORWARD_FROM):
            r['sym'] = s; rows.append(r)
        for r in simulate(df, FORWARD_FROM, k=K2, exit_mode='4h'):
            r['sym'] = s; rows2.append(r)
    return rows, rows2, frames


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--validate', action='store_true')
    a = ap.parse_args()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if a.validate:
        months = [str(m) for m in pd.period_range('2023-12', '2024-12', freq='M')]
        rows = []
        for s in UNIVERSE:
            df = pk.archive(s, 'perp', months)
            for r in simulate(df, pd.Timestamp('2024-01-01', tz='UTC')):
                if r['t'] < pd.Timestamp('2025-01-01', tz='UTC'):
                    rows.append(r)
        st = stats(rows)
        print('VALIDATE 2024 (primary book):', fmt(st))
        ok = bool(BASIS) and st.get('n') == BASIS['n'] and abs(st['mean'] - BASIS['mean']) < 5e-4
        print('VALIDATION', ('PASS' if ok else 'FAIL') if BASIS else 'BASIS NOT FROZEN')
        sys.exit(0 if ok else 1)
    rows, rows2, _ = read()
    s, s2 = stats(rows), stats(rows2)
    print(f'PREREG-WICK-CATCHER forward read ({today}), fills from {FORWARD_FROM.isoformat()}: {fmt(s)}')
    print(f'  report-only k=8 / 4h book: {fmt(s2)}')
    print('VERDICT:', decide(s, today))


if __name__ == '__main__':
    main()
