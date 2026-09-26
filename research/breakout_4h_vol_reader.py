#!/usr/bin/env python3
"""Reader of record for PREREG-BREAKOUT-4H-VOL (paper), registered 2026-09-26 (owner order "Yes, do").
A PARALLEL track to PREREG-BREAKOUT-4H (unchanged, still the registered rule). Question it answers forward: does a
volume-confirmed breakout add edge over the plain rule?

Frozen rule = PREREG-BREAKOUT-4H (breakout_4h_reader: pivot-3 swing-high cross, close > EMA200, next-open entry, stop
  2 x ATR14, 36-bar exit, 20 bps, one position per coin, 20 coins) PLUS: signal-bar volume >= 1.5 x the mean volume of
  the prior 20 bars (VOL_MULT). Control = random long at every 4h bar, same exit (as the plain rule).
Origin (disclosed): pre-specified variant test 2026-09-26 (reports/structural_edge_2026-09-25.md, "improving
  PREREG-BREAKOUT-4H"). The procedure chose the market filter, which failed its 2020 holdout; this volume filter was the
  runner-up on discovery (2021-24) and beat the plain rule in both holdouts. Choosing it involved a 2-way look at
  holdout data, hence a separate forward track, never a replacement.
Forward window: entries >= 2026-09-26T12:00Z.
PROMOTE (ALL, formal read): E >= +0.10R; edge vs control >= +0.10R; week-clustered t(edge) >= 1.5; top-5 weeks <= 60%;
  AND edge >= the plain rule's forward edge over the same window + 0.05R (the volume filter must add, not just match).
KILL (ANY): E < 0 at n >= 70; at the formal read, edge <= the plain rule's forward edge.
Formal read: n >= 100 closed over >= 16 weeks, or 2027-03-31; one extension to 2027-06-30, then park.
--validate (public archive) basis, frozen 2026-09-26 (plain rule in brackets):
  2020 n=286 E +0.346R edge +0.055R t +0.24 [n=408 edge +0.019R]
  2021-01 .. 2025-01 n=1561 E +0.328R edge +0.257R t +2.06 [n=2336 edge +0.205R t +1.93]
  2025-02 .. 2026-08 n=472 E +0.264R edge +0.316R t +1.02 top-5 weeks 211% [n=769 edge +0.108R t +0.50]
Public data only; no account calls.
"""
import argparse
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.append('/root/bitana/research')
import breakout_4h_reader as bo4  # noqa: E402

VOL_MULT = 1.5
FORWARD_FROM = pd.Timestamp('2026-09-26T12:00:00Z')
FORMAL_DATE, EXTENSION_DATE = '2027-03-31', '2027-06-30'
BASIS = {'2020': (286, 0.346), '2021-01 .. 2025-01': (1561, 0.328), '2025-02 .. 2026-08': (472, 0.264)}  # frozen 2026-09-26


def read(frames=None, now=None):
    now = now or pd.Timestamp.now(tz='UTC')
    frames = frames or {s: bo4.api_4h(s, FORWARD_FROM - pd.Timedelta(days=150)) for s in bo4.UNIVERSE}
    end = str(now.normalize() + pd.Timedelta(days=1))
    vol = bo4.pooled(frames, str(FORWARD_FROM), end, entries_from=FORWARD_FROM, vol_mult=VOL_MULT)
    plain = bo4.pooled(frames, str(FORWARD_FROM), end, entries_from=FORWARD_FROM)   # same window, for the A/B line
    return vol, plain


def decide(s, plain, today):
    n = s.get('n', 0)
    if n >= 70 and s['E'] < 0:
        return 'KILL: E<0 at n>=70'
    weeks = (pd.Timestamp(today, tz='UTC') - FORWARD_FROM).days / 7
    formal = (n >= 100 and weeks >= 16) or today >= FORMAL_DATE
    if not formal:
        return f'COUNTS-ONLY (formal at n>=100 over >=16 weeks or {FORMAL_DATE})'
    pe = plain.get('edge', 0.0) if plain.get('n') else 0.0
    if s['edge'] <= pe:
        return 'KILL: edge <= the plain rule over the same window'
    if s['E'] >= 0.10 and s['edge'] >= 0.10 and s['t'] >= 1.5 and s['top5'] <= 0.60 and s['edge'] >= pe + 0.05:
        return 'PROMOTE -> owner decision (replace or run beside the plain rule; portfolio-level sizing)'
    return 'PARK (extension exhausted)' if today >= EXTENSION_DATE else 'INCONCLUSIVE -> one extension to ' + EXTENSION_DATE


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--validate', action='store_true')
    a = ap.parse_args()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if a.validate:
        frames = {s: bo4.archive_4h(s) for s in bo4.UNIVERSE}
        ok = True
        for lab, x, y in bo4.PERIODS:
            s = bo4.pooled(frames, x, y, vol_mult=VOL_MULT)
            print(f'VALIDATE {lab:<20} {bo4.fmt(s)}')
            if BASIS:
                bn, bE = BASIS[lab]
                ok &= s.get('n') == bn and abs(s['E'] - bE) < 1e-3
        print('VALIDATION', ('PASS' if ok else 'FAIL') if BASIS else 'BASIS NOT FROZEN')
        sys.exit(0 if (ok and BASIS) else 1)
    s, plain = read()
    print(f'PREREG-BREAKOUT-4H-VOL forward read ({today}), entries from {FORWARD_FROM.isoformat()}: {bo4.fmt(s)}')
    print(f'  plain rule, same window: {bo4.fmt(plain)}')
    print('VERDICT:', decide(s, plain, today))


if __name__ == '__main__':
    main()
