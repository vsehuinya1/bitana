#!/usr/bin/env python3
"""PREREG-CAPITULATION-BASKET reader of record (registered 2026-09-25, RESEARCH_PLAN.md EOF section).

Paper-only strategy, one position per market event: when a market-wide capitulation hits, buy an equal-weight basket
of 20 liquid perps at the next hour's open and hold 24h. Mechanism: forced selling across the whole market overshoots
and reverts. Narrow, symbol-specific flushes don't.

Frozen rule:
  universe  UNIVERSE (20 USDT-M perps); Binance public 1h klines, index = bar open time
  z         hourly log return / its trailing 720-bar sd (shifted one bar; >= 500 bars of history)
  event     >= 75% of the coins with a valid z have z <= -3 in the same hour; >= 16 valid coins (2022+ universe)
            24h cooldown between events
  trade     buy every coin at the next bar's open, exit at the open 24 bars later; basket = equal-weight mean
  costs     20 bps round trip on the basket (BTC-only reported at 10 bps)
Forward window: events whose entry bar opens at or after 2026-09-25T21:00Z.
PROMOTE (ALL): forward n >= 12 events; forward mean net >= 0; pooled (in-sample + OOS + forward) t >= 2.0;
               no forward event below -20%.
KILL (ANY):    forward mean net < -1.0% at n >= 12; pooled t < 1.5 at the formal read.
Formal read at forward n >= 12 events or 2027-03-31, whichever first; one extension to 2027-09-30, then park.
AMENDMENT 2026-09-25 (owner order "Add it"): parallel paper track MAJORS5. Same events; the executed basket is
BTC ETH SOL XRP BNB (available coins) at 15 bps. It is the implied live design (fills far more easily in a crash).
It is REPORT-ONLY: the verdict above stays on the registered 20-coin basket. At promotion the owner picks the executed
basket with both records in hand. MAJORS5 basis: in-sample n=121 +1.08% (t +2.27); OOS 2020-21 n=37 +1.85% (t +1.38).
--validate reproduces the basis:
  in-sample 2022-01-01 -> 2026-09-25T20:00Z: n=121, basket net +1.10%/event, t +2.28
  OOS 2020-2021 (min 8 valid coins): n=37, basket net +2.73%/event, t +1.77
Public data only; no account calls.
"""
import argparse
import json
import math
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

UNIVERSE = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT', 'DOTUSDT',
            'LTCUSDT', 'BCHUSDT', 'ATOMUSDT', 'NEARUSDT', 'UNIUSDT', 'FILUSDT', 'ETCUSDT', 'TRXUSDT', 'BNBUSDT',
            'XLMUSDT', 'AAVEUSDT']
Z_WIN, Z_MIN, Z_TH, BREADTH, COOLDOWN_H, HOLD_H = 720, 500, -3.0, 0.75, 24, 24
MIN_VALID = 16
COST_BASKET, COST_BTC = 0.0020, 0.0010
MAJORS5, COST_MAJORS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT'], 0.0015
FORWARD_FROM = pd.Timestamp('2026-09-25T21:00:00Z')
FORMAL_DATE, EXTENSION_DATE = '2027-03-31', '2027-09-30'
BASIS = {'is': (121, 0.0110), 'oos': (37, 0.0273)}
BASIS_MAJORS = {'is': (121, 0.010821), 'oos': (37, 0.018459)}
IS_FROM, IS_TO = pd.Timestamp('2022-01-01T00:00:00Z'), pd.Timestamp('2026-09-25T20:00:00Z')
OOS_FROM, OOS_TO = pd.Timestamp('2019-12-01T00:00:00Z'), pd.Timestamp('2022-01-02T00:00:00Z')
CTX = ssl.create_default_context()


def fetch(start: pd.Timestamp, end: pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hourly open and close frames (UTC bar-open index) for the universe, public klines."""
    end_ms = int((end or pd.Timestamp.now(tz='UTC')).timestamp() * 1000)
    opens, closes = {}, {}
    for s in UNIVERSE:
        rows, t = [], int(start.timestamp() * 1000)
        while t < end_ms:
            url = (f'https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=1h&startTime={t}'
                   f'&endTime={end_ms}&limit=1500')
            r = json.load(urllib.request.urlopen(url, context=CTX, timeout=30))
            if not r:
                break
            rows += r
            t = r[-1][0] + 3600000
            if len(r) < 1500:
                break
            time.sleep(0.12)
        if rows:
            now_ms = int(time.time() * 1000)
            rows = [k for k in rows if k[6] < now_ms]          # closed bars only
            idx = pd.to_datetime([k[0] for k in rows], unit='ms', utc=True)
            opens[s] = pd.Series([float(k[1]) for k in rows], index=idx)
            closes[s] = pd.Series([float(k[4]) for k in rows], index=idx)
    return pd.DataFrame(opens), pd.DataFrame(closes)


def detect(C: pd.DataFrame, min_valid: int = MIN_VALID) -> tuple[list, pd.Series]:
    r = np.log(C / C.shift(1))
    z = r / r.rolling(Z_WIN, min_periods=Z_MIN).std().shift(1)
    valid = z.notna().sum(1)
    breadth = ((z <= Z_TH).sum(1) / valid).where(valid >= min_valid)
    events, last = [], None
    for t in C.index[(breadth >= BREADTH).fillna(False).values]:
        if last is None or (t - last).total_seconds() >= COOLDOWN_H * 3600:
            events.append(t)
            last = t
    return events, breadth


def trades(O: pd.DataFrame, events: list) -> pd.DataFrame:
    """Per event: basket and BTC net returns; exit NaN while the 24h hold is still open."""
    ent, ex = O.shift(-1), O.shift(-1 - HOLD_H)
    out = []
    for t in events:
        if t not in ent.index:
            continue
        e, x = ent.loc[t], ex.loc[t]
        basket = (x / e - 1).mean() if x.notna().any() else np.nan
        btc = (x['BTCUSDT'] / e['BTCUSDT'] - 1) if 'BTCUSDT' in x and pd.notna(x['BTCUSDT']) else np.nan
        cols = [c for c in MAJORS5 if c in x.index]
        mok = e[cols].notna() & x[cols].notna()
        majors = (x[cols][mok] / e[cols][mok] - 1).mean() if mok.any() else np.nan
        out.append({'event_bar': t, 'entry_bar': t + pd.Timedelta(hours=1),
                    'basket_net': basket - COST_BASKET if pd.notna(basket) else np.nan,
                    'majors_net': majors - COST_MAJORS if pd.notna(majors) else np.nan,
                    'btc_net': btc - COST_BTC if pd.notna(btc) else np.nan})
    return pd.DataFrame(out)


def stats(x: pd.Series) -> dict:
    x = x.dropna()
    if len(x) == 0:
        return {'n': 0}
    t = x.mean() / x.std() * math.sqrt(len(x)) if len(x) > 2 and x.std() > 0 else float('nan')
    return {'n': len(x), 'mean': x.mean(), 'median': x.median(), 'hit': (x > 0).mean(), 't': t,
            'worst': x.min(), 'sd': x.std()}


def fmt(s: dict) -> str:
    if not s.get('n'):
        return 'n=0'
    return (f"n={s['n']} mean {s['mean'] * 100:+.2f}% median {s['median'] * 100:+.2f}% hit {s['hit']:.0%} "
            f"t {s['t']:+.2f} worst {s['worst'] * 100:+.1f}%")


def basis_frames():
    Oi, Ci = fetch(IS_FROM, IS_TO + pd.Timedelta(hours=HOLD_H + 2))
    Oo, Co = fetch(OOS_FROM, OOS_TO + pd.Timedelta(hours=HOLD_H + 2))
    ev_i, _ = detect(Ci)
    ev_i = [t for t in ev_i if IS_FROM <= t <= IS_TO]
    ev_o, _ = detect(Co, min_valid=8)
    ev_o = [t for t in ev_o if pd.Timestamp('2020-01-01T00:00:00Z') <= t < pd.Timestamp('2022-01-01T00:00:00Z')]
    return trades(Oi, ev_i), trades(Oo, ev_o)


def decide(fwd: dict, pooled: dict, today: str) -> str:
    n = fwd.get('n', 0)
    formal = n >= 12 or today >= FORMAL_DATE
    if n < 12 and not formal:
        return f'COUNTS-ONLY (forward n={n} < 12)'
    if n >= 12 and fwd['mean'] < -0.010:
        return 'KILL: forward mean < -1.0% at n >= 12'
    if formal and pooled.get('t', 0) < 1.5:
        return 'KILL: pooled t < 1.5 at the formal read'
    if n >= 12 and fwd['mean'] >= 0 and pooled.get('t', 0) >= 2.0 and fwd['worst'] >= -0.20:
        return 'PROMOTE -> owner decision on a live design (basket execution, notional sizing, disaster stop)'
    return 'PARK (extension exhausted)' if today >= EXTENSION_DATE else 'INCONCLUSIVE -> one extension to ' + EXTENSION_DATE


def forward_read():
    O, C = fetch(FORWARD_FROM - pd.Timedelta(days=45))
    ev, breadth = detect(C)
    ev = [t for t in ev if t + pd.Timedelta(hours=1) >= FORWARD_FROM]
    return trades(O, ev), breadth


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--validate', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if a.validate:
        ti, to = basis_frames()
        si, so = stats(ti.basket_net), stats(to.basket_net)
        mi, mo = stats(ti.majors_net), stats(to.majors_net)
        print(f'VALIDATE in-sample 2022-01 -> 2026-09-25: {fmt(si)}')
        print(f'VALIDATE OOS 2020-2021:                  {fmt(so)}')
        print(f'  MAJORS5 track in-sample:               {fmt(mi)}')
        print(f'  MAJORS5 track OOS 2020-2021:           {fmt(mo)}')
        ok = (si.get('n') == BASIS['is'][0] and abs(si['mean'] - BASIS['is'][1]) < 5e-4
              and so.get('n') == BASIS['oos'][0] and abs(so['mean'] - BASIS['oos'][1]) < 5e-4
              and mi.get('n') == BASIS_MAJORS['is'][0] and abs(mi['mean'] - BASIS_MAJORS['is'][1]) < 5e-4
              and mo.get('n') == BASIS_MAJORS['oos'][0] and abs(mo['mean'] - BASIS_MAJORS['oos'][1]) < 5e-4)
        print('VALIDATION', 'PASS' if ok else 'FAIL')
        sys.exit(0 if ok else 1)
    tf, breadth = forward_read()
    done = tf.dropna(subset=['basket_net']) if len(tf) else tf
    fwd = stats(done.basket_net) if len(done) else {'n': 0}
    open_n = len(tf) - len(done) if len(tf) else 0
    last = breadth.dropna()
    print(f"PREREG-CAPITULATION-BASKET forward read ({today}), entries from {FORWARD_FROM.isoformat()}")
    print(f"  closed events: {fmt(fwd)} | open (24h hold running): {open_n}")
    if len(done):
        print(f"  MAJORS5 track (report-only, implied live design): {fmt(stats(done.majors_net))}")
    if len(last):
        print(f"  latest closed hour {last.index[-1].isoformat()}: breadth {last.iloc[-1]:.0%} of coins at z <= -3 "
              f"(event threshold {BREADTH:.0%})")
    if fwd.get('n'):
        ti, to = basis_frames()
        pooled = stats(pd.concat([ti.basket_net, to.basket_net, done.basket_net]))
        print(f"  pooled (in-sample + OOS + forward): {fmt(pooled)}")
        print('VERDICT:', decide(fwd, pooled, today))
    else:
        print('VERDICT:', decide(fwd, {}, today))
    if a.json:
        print(json.dumps({'n': fwd.get('n', 0), 'open': open_n, 'today': today}))


if __name__ == '__main__':
    main()
