#!/usr/bin/env python3
"""Reader of record for PREREG-PERP-DISCOUNT (paper), registered 2026-09-27 (owner order "Register").
Source: internet-sourced cascade ideas, test #4 (reports/structural_edge_2026-09-25.md). Mechanism: forced perp selling
(liquidations) pushes the perpetual below spot; the overshoot rebounds. DARK: simulated on public 5m klines only.

Frozen rule (each of the 20 coins of capitulation_reader.UNIVERSE; one position per coin):
  basis = perp 5m close / spot 5m close - 1 (same bar). SIGNAL: basis crosses below -30 bps (prior bar > -30 bps).
  ENTRY: buy the perp at the next 5m open. EXIT: the perp 5m open 4h after entry. COST: 0.20% round trip (taker).
Report-only second book: exit at the next open after basis >= -5 bps (convergence), else 24h.
Forward window: signals from 2026-09-27T10:00Z.
Report-only BTC-FALLING line (amendment 2026-09-30, owner order "Add"; verdict unchanged): trades whose signal bar had
  BTC perp 5m close / BTC close 60 min earlier - 1 <= -1.0%. Research (reports/structural_edge_2026-09-25.md, test #8):
  IN vs OUT 2020-21 +3.02% vs +0.23%, 2022-23 +1.44% vs +0.29%, 2024-26 +7.68% vs -0.00% (2026 YTD IN +0.59%, n=33).
PROMOTE (ALL, formal read): mean net >= +0.3%/trade; day-clustered t >= 1.5; net positive in >= 3 distinct calendar
  months. (Month-spread bar instead of a top-5-day cap, set before any forward data: the 2024 basis has top-5 days = 93%.)
KILL (ANY): mean < -0.5% at n >= 40; mean < 0 at the formal read.
Formal read: n >= 80 closed trades over >= 10 distinct days, or 2027-06-30; one extension to 2027-12-31, then park.
--validate: recomputes the frozen 2024 basis from the public monthly archive (perp + spot, Jan-Dec 2024):
  n=143 over 44 days, mean +4.15%, median +3.38%, hit 78%, t +2.19, top-5 days 93%, worst -5.6%.
"""
import argparse
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.append('/root/bitana/research')
import capitulation_reader as capr  # noqa: E402
import paper_klines as pk  # noqa: E402
import wick_catcher_reader as wc  # noqa: E402  (shared stats/fmt)

UNIVERSE = capr.UNIVERSE
THR, CONV, COST, HOLD = -0.003, -0.0005, 0.002, pd.Timedelta(hours=4)
FORWARD_FROM = pd.Timestamp('2026-09-27T10:00:00Z')
FORMAL_DATE, EXTENSION_DATE = '2027-06-30', '2027-12-31'
BASIS = {'n': 143, 'mean': 0.0415}   # frozen 2026-09-27 from --validate (2024 trades)
BTC_FALL = -0.01                     # report-only BTC-falling line (amendment 2026-09-30)


def simulate(perp, spot, frm, exit_mode='4h'):
    j = perp[['o', 'c']].join(spot[['c']].rename(columns={'c': 'sc'}), how='inner')
    if len(j) < 50:
        return []
    b = (j.c / j.sc - 1).values; o = j.o.values; t = j.index; c = j.c.values
    out, busy = [], pd.Timestamp.min.tz_localize('UTC')
    for i in np.where((b <= THR) & (np.r_[np.nan, b[:-1]] > THR))[0]:
        if i + 1 >= len(t) or t[i + 1] < frm or t[i + 1] < busy:
            continue
        e = i + 1; te = t[e]
        if exit_mode == '4h':
            k = t.searchsorted(te + HOLD)
            closed = k < len(t) and t[k] >= te + HOLD
        else:
            conv = np.where(b[e:] >= CONV)[0]
            k = e + conv[0] + 1 if len(conv) else t.searchsorted(te + pd.Timedelta(hours=24))
            closed = k < len(t) and (len(conv) > 0 or t[k] >= te + pd.Timedelta(hours=24))
        x, tx = (o[k], t[k]) if closed else (c[-1], t[-1])
        out.append({'t': te, 'fill': float(o[e]), 'basis': float(b[i]), 'exit_t': tx, 'exit': float(x),
                    'net': float(x / o[e] - 1 - COST), 'closed': bool(closed), 'why': exit_mode if closed else 'open'})
        busy = tx if closed else pd.Timestamp.max.tz_localize('UTC')
    return out


def tag_btc(rows, perp_frames):
    """Report-only BTC-falling tag (amendment 2026-09-30): BTC 1h move to the signal-bar close (entry bar - 5 min)."""
    btc = perp_frames['BTCUSDT'].c; m5 = pd.Timedelta(minutes=5)
    for r in rows:
        ts = r['t'] - m5
        b1, b0 = btc.get(ts), btc.get(ts - pd.Timedelta(minutes=60))
        r['btc1h'] = float(b1 / b0 - 1) if b1 is not None and b0 is not None and b0 > 0 else None
        r['btc_fall'] = r['btc1h'] is not None and r['btc1h'] <= BTC_FALL
    return rows


def read(now=None, perp_frames=None):
    perp_frames = perp_frames or {s: pk.live(s, 'perp', FORWARD_FROM - pd.Timedelta(days=2)) for s in UNIVERSE}
    spot = {s: pk.live(s, 'spot', FORWARD_FROM - pd.Timedelta(days=2)) for s in UNIVERSE}
    rows, rows2, cur = [], [], {}
    for s in UNIVERSE:
        p, sp = perp_frames[s], spot[s]
        for r in simulate(p, sp, FORWARD_FROM):
            r['sym'] = s; rows.append(r)
        for r in simulate(p, sp, FORWARD_FROM, exit_mode='conv'):
            r['sym'] = s; rows2.append(r)
        jj = p[['c']].join(sp[['c']].rename(columns={'c': 'sc'}), how='inner')
        if len(jj):
            bb = jj.c / jj.sc - 1
            cur[s] = {'basis_now': float(bb.iloc[-1]), 'min_24h': float(bb[bb.index >= bb.index[-1] - pd.Timedelta(hours=24)].min()),
                      'bar': bb.index[-1]}
    return rows, rows2, cur


def decide(s, today):
    n = s.get('n', 0)
    if n >= 40 and s['mean'] < -0.005:
        return 'KILL: mean < -0.5% at n >= 40'
    formal = (n >= 80 and s.get('days', 0) >= 10) or today >= FORMAL_DATE
    if not formal:
        return f'COUNTS-ONLY (formal at 80 trades over 10 days or {FORMAL_DATE})'
    if n == 0 or s['mean'] < 0:
        return 'KILL: mean < 0 at the formal read'
    if s['mean'] >= 0.003 and s['t'] >= 1.5 and s['pos_months'] >= 3:
        return 'PROMOTE -> owner decision on a live design'
    return 'PARK (extension exhausted)' if today >= EXTENSION_DATE else 'INCONCLUSIVE -> one extension to ' + EXTENSION_DATE


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--validate', action='store_true')
    a = ap.parse_args()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if a.validate:
        months = [str(m) for m in pd.period_range('2024-01', '2024-12', freq='M')]
        rows, pf = [], {}
        for s in UNIVERSE:
            pf[s] = pk.archive(s, 'perp', months)
            for r in simulate(pf[s], pk.archive(s, 'spot', months), pd.Timestamp('2024-01-01', tz='UTC')):
                r['sym'] = s; rows.append(r)
        st = wc.stats(rows)
        print('VALIDATE 2024 (primary book):', wc.fmt(st))
        tag_btc(rows, pf)
        print('  2024 BTC-falling line (research 2024: IN 99 / +6.00%, OUT 43 / +0.04%):',
              'IN', wc.fmt(wc.stats([r for r in rows if r['btc_fall']])), '| OUT', wc.fmt(wc.stats([r for r in rows if not r['btc_fall']])))
        ok = bool(BASIS) and st.get('n') == BASIS['n'] and abs(st['mean'] - BASIS['mean']) < 5e-4
        print('VALIDATION', ('PASS' if ok else 'FAIL') if BASIS else 'BASIS NOT FROZEN')
        sys.exit(0 if ok else 1)
    perp = {sym: pk.live(sym, 'perp', FORWARD_FROM - pd.Timedelta(days=2)) for sym in UNIVERSE}
    rows, rows2, cur = read(perp_frames=perp)
    tag_btc(rows, perp)
    s, s2 = wc.stats(rows), wc.stats(rows2)
    print(f'PREREG-PERP-DISCOUNT forward read ({today}), signals from {FORWARD_FROM.isoformat()}: {wc.fmt(s)}')
    print(f'  report-only BTC-falling line: {wc.fmt(wc.stats([r for r in rows if r["btc_fall"]]))} | '
          f'not falling: {wc.fmt(wc.stats([r for r in rows if not r["btc_fall"]]))}')
    print(f'  report-only convergence-exit book: {wc.fmt(s2)}')
    deepest = sorted(cur.items(), key=lambda x: x[1]['basis_now'])[:5]
    print('  basis now (lowest 5):', ' '.join(f"{k[:-4]} {100 * v['basis_now']:+.3f}%" for k, v in deepest))
    print('VERDICT:', decide(s, today))


if __name__ == '__main__':
    main()
