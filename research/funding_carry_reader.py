#!/usr/bin/env python3
"""Reader of record for PREREG-FUNDING-CARRY (paper), registered 2026-09-26 (owner order "Both next": carry paper track).
Basis: reports/structural_edge_2026-09-25.md, "delta-neutral funding carry" (2020-01 -> 2026-09, CAGR +7.7% on capital,
earned almost only in bull regimes; idle through 2026 so far). DARK: nothing is wired; paper only.

Frozen rule (each coin of capitulation_reader.UNIVERSE independently):
  signal    at each funding settlement t: mean of the coin's settled rates in the trailing 24h, annualized with the coin's
            own settlement interval.
  enter     mean >= 15%/yr (not in a position)   exit   mean < 3%/yr (in a position)
  execution act at the 1h open after t on BOTH legs: long spot (api.binance.com), short USDT-M perp, equal notional.
            Funding collected = settled rates strictly after entry up to and including exit (sign as settled).
  P&L       funding + basis [(spot_exit/spot_entry - 1) - (perp_exit/perp_entry - 1)] - 0.30% round trip (taker).
  capital   20 equal slots; an active slot's notional = slot / 1.25 (spot + perp margin). Idle capital earns 0.
Forward window: signals from the 2026-09-26T16:00Z settlement on.
Formal read: >= 20 closed trades, or 2027-09-30 (regime-dependent sleeve; one extension to 2028-03-31, then park).
PROMOTE (ALL): >= 20 closed; net P&L > 0; annualized net return on DEPLOYED capital >= 8%; basis + costs drag <= 25% of
  funding collected. Promotion = owner decision (needs spot trading on the API key and a unified/portfolio-margin account).
KILL (ANY): net P&L < 0 at >= 20 closed; any single trade with basis < -3% (hedge failure) -> review before continuing.
Public data only; no account calls.
"""
import argparse
import json
import ssl
import time
import urllib.request
import sys
from datetime import datetime, timezone

import pandas as pd

sys.path.append('/root/bitana/research')
import capitulation_reader as capr  # noqa: E402

UNIVERSE = capr.UNIVERSE
ENTER, EXIT, COST, CAP_PER_NOTIONAL = 0.15, 0.03, 0.003, 1.25
FORWARD_FROM = pd.Timestamp('2026-09-26T16:00:00Z')
FORMAL_DATE, EXTENSION_DATE = '2027-09-30', '2028-03-31'
CTX = ssl.create_default_context()


def _json(url):
    for a in range(3):
        try:
            return json.load(urllib.request.urlopen(url, context=CTX, timeout=30))
        except Exception:
            time.sleep(1 + a)
    return None


def funding(sym, start):
    r = _json(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&startTime={int(start.timestamp() * 1000)}&limit=1000') or []
    s = pd.Series({pd.Timestamp(int(x['fundingTime']), unit='ms', tz='UTC').floor('min'): float(x['fundingRate']) for x in r})
    return s.sort_index()


def price(sym, t, spot):
    base = 'https://api.binance.com/api/v3/klines' if spot else 'https://fapi.binance.com/fapi/v1/klines'
    r = _json(f'{base}?symbol={sym}&interval=1h&startTime={int(t.timestamp() * 1000)}&limit=1')
    return float(r[0][1]) if r and int(r[0][0]) == int(t.timestamp() * 1000) else None


def replay(sym, now):
    """Paper trades for one coin: list of dicts (open trades have exit=None, marked at the last closed hour)."""
    fr = funding(sym, FORWARD_FROM - pd.Timedelta(days=2))
    if fr.empty:
        return [], None
    iv = fr.index.to_series().diff().dt.total_seconds().div(3600).fillna(8.0).clip(lower=1.0)
    ann = fr * (24 * 365 / iv)
    trail = ann.rolling('24h').mean()
    trades, pos = [], None
    for t, m in trail.items():
        if t < FORWARD_FROM:
            continue
        act = t + pd.Timedelta(hours=1)
        if act > now:
            break
        if pos is None and m >= ENTER:
            s0, p0 = price(sym, act, True), price(sym, act, False)
            if s0 and p0:
                pos = dict(sym=sym, entry=act, s0=s0, p0=p0, exit=None)
        elif pos is not None and m < EXIT:
            s1, p1 = price(sym, act, True), price(sym, act, False)
            if s1 and p1:
                pos.update(exit=act, s1=s1, p1=p1); trades.append(pos); pos = None
    if pos is not None:
        last = now.floor('h') - pd.Timedelta(hours=1)
        pos.update(s1=price(sym, last, True), p1=price(sym, last, False), mark=last); trades.append(pos)
    for tr in trades:
        end = tr['exit'] or now
        tr['funding'] = float(fr[(fr.index > tr['entry']) & (fr.index <= end)].sum())
        tr['basis'] = ((tr['s1'] / tr['s0'] - 1) - (tr['p1'] / tr['p0'] - 1)) if tr.get('s1') and tr.get('p1') else 0.0
        tr['net'] = tr['funding'] + tr['basis'] - (COST if tr['exit'] else COST / 2)   # open: entry half of costs
    return trades, float(trail.iloc[-1])


def read(now=None):
    now = now or pd.Timestamp.now(tz='UTC')
    allt, cur = [], {}
    for s in UNIVERSE:
        tr, last = replay(s, now)
        allt += tr
        if last is not None:
            cur[s] = last
        time.sleep(0.2)
    closed = [t for t in allt if t['exit'] is not None]
    opened = [t for t in allt if t['exit'] is None]
    w = 1 / len(UNIVERSE) / CAP_PER_NOTIONAL
    net = sum(t['net'] for t in allt) * w
    dep_days = sum(((t['exit'] or now) - t['entry']).total_seconds() / 86400 for t in allt) / len(UNIVERSE)
    fund = sum(t['funding'] for t in allt)
    drag = -(sum(t['basis'] for t in allt) - COST * len(closed) - COST / 2 * len(opened))
    return dict(closed=closed, open=opened, current=cur, net_on_capital=net, deployed_slot_days=dep_days,
                ann_on_deployed=(net / (dep_days / 365) if dep_days > 0 else None),
                drag_share=(drag / fund if fund > 0 else None), worst_basis=min([t['basis'] for t in allt], default=None))


def decide(r, today):
    n = len(r['closed'])
    if r['worst_basis'] is not None and r['worst_basis'] < -0.03:
        return 'REVIEW: a trade lost > 3% on basis (hedge failure check)'
    if n >= 20 and r['net_on_capital'] < 0:
        return 'KILL: net < 0 at >= 20 closed'
    if n < 20 and today < FORMAL_DATE:
        return f'COUNTS-ONLY ({n} closed; formal at 20 closed or {FORMAL_DATE})'
    if (n >= 20 and r['net_on_capital'] > 0 and (r['ann_on_deployed'] or 0) >= 0.08
            and r['drag_share'] is not None and r['drag_share'] <= 0.25):
        return 'PROMOTE -> owner decision (spot on the API key + unified margin)'
    return 'PARK (extension exhausted)' if today >= EXTENSION_DATE else 'INCONCLUSIVE -> one extension to ' + EXTENSION_DATE


def summary(r, today):
    hot = sorted(r['current'].items(), key=lambda x: -x[1])[:5]
    op = ', '.join(f"{t['sym'][:-4]} since {t['entry']:%d %b %H}h {t['net'] * 100:+.2f}%" for t in r['open']) or 'none'
    return (f"closed {len(r['closed'])} | open {len(r['open'])} ({op}) | net on capital {r['net_on_capital'] * 100:+.3f}% | "
            f"top trailing funding: {' '.join(f'{s[:-4]} {v:+.0%}' for s, v in hot)} (enter >= {ENTER:.0%}) | "
            f"{decide(r, today)}")


def main():
    argparse.ArgumentParser(description=__doc__.split('\n')[0]).parse_args()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    r = read()
    print(f'PREREG-FUNDING-CARRY forward read ({today}), signals from {FORWARD_FROM.isoformat()}')
    print(' ', summary(r, today))
    for t in r['closed']:
        print(f"  closed {t['sym']} {t['entry']:%m-%d %H}h -> {t['exit']:%m-%d %H}h funding {t['funding'] * 100:+.2f}% "
              f"basis {t['basis'] * 100:+.2f}% net {t['net'] * 100:+.2f}%")


if __name__ == '__main__':
    main()
