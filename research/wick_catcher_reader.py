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
Report-only MARKET-WIDE line (amendment 2026-09-28, owner order "Yes"; verdict unchanged): primary-book fills whose
  bid hour H was a market-wide selloff hour (>= 50% of the 20 coins at hourly z <= -2, z = log return / trailing
  720-bar sd shifted by 1, >= 16 coins valid). Research basis (6.7y, 20 + 12 collapsed coins): 305 fills / 49 days,
  +9.4%/fill, win 87%, worst fill -18.5% (vs coin-specific fills +1.2%, win 62%, worst -73.6%).
Report-only SIGNATURE lines (amendment 2026-09-28, owner order "Add"; verdict unchanged; thresholds = 2022-23 discovery
  medians, reports/structural_edge_2026-09-25.md "what else marks the winners"):
  btc_dump  BTC 5m close at the fill bar / BTC close at the bid-hour close - 1 <= -1.70% (known at the fill-bar close).
  low_sell  taker-sell share of the 3 5m bars before the fill bar <= 56.87% (known at the fill).
  discount  perp/spot - 1 at the 5m bar before the fill <= -0.062% (known at the fill).
  tier      market (bid-hour market-wide) > btc (btc_dump) > both (low_sell and discount) > one > neither.
  ADD-ON book: every btc_dump fill buys a second unit at the fill-bar close (taker, 0.20% round trip), same exit; skipped
  when the exit printed inside the fill bar. Research: add-on +3.4%/trade 2024-26 vs -1.5% for non-dump fills.
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
BTC_DUMP, LOW_SELL, DISCOUNT, ADD_COST = -0.0170, 0.5687, -0.00062, 0.0020   # signature lines (2026-09-28)
TIERS = ['market', 'btc', 'both', 'one', 'neither']


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


def market_selloff_hours(now=None):
    """Set of hour-start timestamps H whose hour was a market-wide selloff (>= 50% of coins at hourly z <= -2)."""
    now = now or pd.Timestamp.now(tz='UTC')
    _, C = capr.fetch(min(FORWARD_FROM, now) - pd.Timedelta(days=40))
    r = np.log(C / C.shift(1)); z = r / r.rolling(720, min_periods=500).std().shift(1)
    valid = z.notna().sum(1)
    br = ((z <= -2).sum(1) / valid).where(valid >= 16)
    return set(br.index[br >= 0.5])


def tag_market(rows, sell_hours):
    for r in rows:
        r['mkt'] = (r['t'].floor('h') - pd.Timedelta(hours=1)) in sell_hours     # bid hour H = the hour before the fill hour
    return rows


def tag_signatures(rows, frames, spot):
    """Report-only signature flags, the tier and the add-on leg (amendment 2026-09-28). Needs rows tagged by tag_market."""
    btc = frames['BTCUSDT'].c; m5 = pd.Timedelta(minutes=5)
    for r in rows:
        df, sp, tf = frames[r['sym']], spot.get(r['sym']), r['t']; hc = tf.floor('h') - m5
        b1, b0 = btc.get(tf), btc.get(hc)
        r['btc_move'] = float(b1 / b0 - 1) if b1 is not None and b0 is not None and tf + m5 <= btc.index[-1] + m5 else None
        pre = df.loc[tf - 3 * m5: tf - m5]
        r['taker_sell'] = float(1 - pre.tb.sum() / pre.v.sum()) if 'tb' in df and len(pre) == 3 and pre.v.sum() > 0 else None
        pc, sc = df.c.get(tf - m5), (sp.c.get(tf - m5) if sp is not None and len(sp) else None)
        r['basis_pre'] = float(pc / sc - 1) if pc is not None and sc is not None and sc > 0 else None
        r['btc_dump'] = r['btc_move'] is not None and r['btc_move'] <= BTC_DUMP
        ls = None if r['taker_sell'] is None else r['taker_sell'] <= LOW_SELL
        dc = None if r['basis_pre'] is None else r['basis_pre'] <= DISCOUNT
        r['low_sell'], r['discount'] = ls, dc
        r['tier'] = ('market' if r.get('mkt') else 'btc' if r['btc_dump'] else 'both' if ls and dc
                     else 'neither' if ls is False and dc is False else 'one')
        cf = df.c.get(tf)
        r['addon'] = None
        if r['btc_dump'] and cf is not None and r['exit_t'] > tf:
            r['addon'] = {'t': tf + m5, 'fill': float(cf), 'exit_t': r['exit_t'], 'exit': r['exit'], 'closed': r['closed'],
                          'why': r['why'], 'net': float(r['exit'] / cf - 1 - ADD_COST), 'sym': r['sym']}
    return rows


def signature_lines(rows):
    """{name: stats} for the report-only lines."""
    out = {'addon': stats([r['addon'] for r in rows if r.get('addon')])}
    for t in TIERS:
        out[t] = stats([r for r in rows if r.get('tier') == t])
    return out


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
    rows, rows2, frames = read()
    tag_market(rows, market_selloff_hours())
    s, s2 = stats(rows), stats(rows2)
    print(f'PREREG-WICK-CATCHER forward read ({today}), fills from {FORWARD_FROM.isoformat()}: {fmt(s)}')
    print(f'  report-only market-wide line: {fmt(stats([r for r in rows if r["mkt"]]))} | coin-specific: {fmt(stats([r for r in rows if not r["mkt"]]))}')
    spot = {sym: pk.live(sym, 'spot', FORWARD_FROM - pd.Timedelta(days=2)) for sym in UNIVERSE}
    sl = signature_lines(tag_signatures(rows, frames, spot))
    print(f'  report-only BTC-dump add-on book: {fmt(sl["addon"])}')
    for t in TIERS:
        print(f'  report-only tier {t:<8}: {fmt(sl[t])}')
    print(f'  report-only k=8 / 4h book: {fmt(s2)}')
    print('VERDICT:', decide(s, today))


if __name__ == '__main__':
    main()
