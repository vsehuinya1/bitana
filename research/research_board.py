#!/usr/bin/env python3
"""Write dashboard/research_board.json: the research rows the owner tracks, with live accrual counts where a
reader of record exists (2026-09-25).

Counts (LATE-BULL-FLUSH, LON-BULL-FADE, NY-BREADTH, NY-KNIFE) come from the readers' own code (never re-implemented here), against ONE /tmp copy of the shadow DB
per run (CLAUDE.md). Rows without a reader carry status text and their next read date only. Edit ROWS when
a row is registered, read, or killed. Run: python research/research_board.py  (about 25s, mostly the DB copy).
"""
import json

import pandas as pd
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, '/root/bitana')
sys.path.insert(0, '/root/bitana/research')
import late_bull_flush_reader as lbf  # noqa: E402
import lon_bull_fade_reader as lfade  # noqa: E402
import ny_flush_quality_reader as nyq  # noqa: E402
import asia_midvol_reader as amv  # noqa: E402
import breakout_4h_reader as bo4  # noqa: E402
import funding_carry_reader as fcr  # noqa: E402
import breakout_4h_vol_reader as bov  # noqa: E402
import capitulation_reader as capr  # noqa: E402

OUT = '/root/bitana/dashboard/research_board.json'

# name, kind, next read, status  (kind: dark = accruing, awaiting its bar; queued = formal read scheduled)
ROWS = [
    ('NY-VOLZ-OFF', 'queued', '2026-09-27', 'Sunday read: drop NY vol_z>=0 gate?'),
    ('NEUT-STOP (Row 4)', 'queued', '2026-09-27', 'NY 1h stop in neutral: revert 5 -> 6 ATR?'),
    ('FK1 / FK2 / FK3 (Rows 5-7)', 'queued', '2026-09-27', 'falling-knife filters on flush-buy arms (dark)'),
    ('PREREG-OIGATE (dark)', 'queued', '2026-09-27', 'OI gate unwound to dark; re-arm bar 1.0% at n>=100'),
    ('LON-BULL-NARROW (Row 8)', 'queued', '2026-09-27', 'cut london bull h9+h10? (h9 already blocked by LON-H9)'),
    ('NY-ZEC (Row 9)', 'dark', 'n>=30', 'ZEC veto on NY arm if E<=-0.02'),
    ('LON-DECILE (Row 10)', 'dark', 'n(D2+)>=100', 'london bull D1 vs D2+; sizing variant only'),
    ('LON-TAIL', 'dark', 'n>=30 / 5d', 'london 11:30-13:59 parity-era read'),
    ('NY-NEUT-KEEP (Row 1)', 'dark', 'n>=30 / 5d', 'NY neutral h16-17 status row'),
    ('LON-NEUT-H13 (Row 2)', 'dark', 'n>=100 / 5d', 'london neutral h12-13 measure-only'),
    ('ASIA-PUMP-NEUTRAL (Row 3)', 'dark', 'superseded', 'superseded 2026-09-25 by ASIA-MIDVOL (its trigger: -0.075 R/leg, n=224)'),
    ('TUEASIA', 'queued', '2026-10-04', 'Tuesday asia neutral re-open (formal)'),
    ('WKNDNY', 'queued', '2026-10-04', 'bull-weekend NY buy, extension read'),
    ('BEAR-* playbook', 'dark', 'first bear bar', 'no bear bars since Aug-17; wired dormant'),
]


def main():
    now = datetime.now(timezone.utc)
    rows = [{'name': n, 'kind': k, 'next': nx, 'status': st} for n, k, nx, st in ROWS]
    db = lbf.copy_db(lbf.LIVE_DB)
    try:
        res = lbf.read(db, lbf.FORWARD_FROM, '9999')
        s = res['bar']
        n, days = s.get('n', 0), s.get('days', 0)
        rows.insert(0, {'name': 'PREREG-LATE-BULL-FLUSH', 'kind': 'dark', 'next': 'n>=30 R-read / n>=50 formal',
                        'status': lbf.decide(res, now.strftime('%Y-%m-%d')),
                        'n': n, 'days': days, 'n_target': 50, 'days_target': 10,
                        'unknown': res['unknown_line'].get('n', 0)})
        fr = lfade.read(db, lfade.FORWARD_FROM, '9999')
        f = fr['fade']
        rows.insert(1, {'name': 'PREREG-LON-BULL-FADE (Row 11)', 'kind': 'dark', 'next': 'n>=30 R-read / n>=50 & 5d formal',
                        'status': lfade.decide(fr, now.strftime('%Y-%m-%d')),
                        'n': f.get('n', 0), 'days': f.get('days', 0), 'n_target': 50, 'days_target': 5,
                        'unknown': fr['unknown_line'].get('n', 0)})
        ct, cl = nyq.btc_5m(lfade._ms(nyq.FORWARD_FROM) - 2 * 86400000)
        nl, _ = nyq.load(db, nyq.FORWARD_FROM, '9999', lfade.regime_series(), ct, cl)
        nr = nyq.read(nl, ('bull',))
        v12 = nyq.decide12(nr, now.strftime('%Y-%m-%d'))
        rows.insert(2, {'name': 'PREREG-NY-BREADTH (Row 12)', 'kind': 'dark', 'next': 'broad n>=50 & narrow n>=100 over 6d',
                        'status': v12, 'n': nr['broad'].get('n', 0), 'days': nr['broad'].get('days', 0), 'n_target': 50, 'days_target': 6})
        rows.insert(3, {'name': 'PREREG-NY-KNIFE (Row 13)', 'kind': 'dark', 'next': 'knife n>=30 R-read / n>=50 & 5d formal',
                        'status': nyq.decide13(nr, now.strftime('%Y-%m-%d'), v12), 'n': nr['knife'].get('n', 0),
                        'days': nr['knife'].get('days', 0), 'n_target': 50, 'days_target': 5})
        ct, _ = capr.forward_read()
        cdone = ct.dropna(subset=['basket_net']) if len(ct) else ct
        cs = capr.stats(cdone.basket_net) if len(cdone) else {'n': 0}
        rows.insert(0, {'name': 'PREREG-CAPITULATION-BASKET (paper)', 'kind': 'dark', 'next': 'n>=12 events formal',
                        'status': (f"{cs['n']} closed events, 20-coin {cs['mean'] * 100:+.2f}%, 5 majors "
                                   f"{capr.stats(cdone.majors_net)['mean'] * 100:+.2f}%" if cs.get('n') else 'no events yet')
                                  + (f"; {len(ct) - len(cdone)} open" if len(ct) else ''),
                        'n': cs.get('n', 0), 'days': cs.get('n', 0), 'n_target': 12, 'days_target': 12})
        try:
            bf = {s_: bo4.api_4h(s_, bo4.FORWARD_FROM - pd.Timedelta(days=150)) for s_ in bo4.UNIVERSE}
            bs = bo4.pooled(bf, str(bo4.FORWARD_FROM), str(pd.Timestamp.now(tz='UTC').normalize() + pd.Timedelta(days=1)),
                            entries_from=bo4.FORWARD_FROM)
            rows.insert(0, {'name': 'PREREG-BREAKOUT-4H (paper)', 'kind': 'dark', 'next': 'n>=150 over 16 weeks formal',
                            'status': (f"{bs['n']} closed, E {bs['E']:+.3f}R, edge vs random longs {bs['edge']:+.3f}R"
                                       if bs.get('n') else 'no closed trades yet') + ' | ' + bo4.decide(bs, now.strftime('%Y-%m-%d')),
                            'n': bs.get('n', 0), 'days': bs.get('weeks', 0), 'n_target': 150, 'days_target': 16})
        except Exception as e:
            print(f'breakout row failed: {type(e).__name__}: {e}', file=sys.stderr)
        try:
            vs, vp = bov.read(bf)
            rows.insert(1, {'name': 'PREREG-BREAKOUT-4H-VOL (paper)', 'kind': 'dark', 'next': 'n>=100 over 16 weeks formal',
                            'status': (f"{vs['n']} closed, edge {vs['edge']:+.3f}R vs plain {vp.get('edge', float('nan')):+.3f}R"
                                       if vs.get('n') else 'no closed trades yet') + ' | ' + bov.decide(vs, vp, now.strftime('%Y-%m-%d')),
                            'n': vs.get('n', 0), 'days': vs.get('weeks', 0), 'n_target': 100, 'days_target': 16})
        except Exception as e:
            print(f'breakout-vol row failed: {type(e).__name__}: {e}', file=sys.stderr)
        try:
            fr_ = fcr.read()
            rows.insert(1, {'name': 'PREREG-FUNDING-CARRY (paper)', 'kind': 'dark', 'next': '20 closed or 2027-09-30 formal',
                            'status': fcr.summary(fr_, now.strftime('%Y-%m-%d')), 'n': len(fr_['closed']),
                            'days': len(fr_['open']), 'n_target': 20, 'days_target': 20})
        except Exception as e:
            print(f'carry row failed: {type(e).__name__}: {e}', file=sys.stderr)
        ak, _ = amv.load(db, amv.FORWARD_FROM, '9999')
        a_s, a_lv = amv.stats(ak), amv.stats([x for x in ak if x['symbol'] in amv.LIVE])
        rows.insert(0, {'name': 'PREREG-ASIA-MIDVOL (paper since 2026-09-25)', 'kind': 'dark', 'next': 'n>=30 revert check / n>=50 & 10d formal',
                        'status': amv.decide(a_s, a_lv, now.strftime('%Y-%m-%d')), 'n': a_s.get('n', 0),
                        'days': a_s.get('days', 0), 'n_target': 50, 'days_target': 10})
    finally:
        for f in (db, db + '-wal', db + '-shm', db + '-journal'):
            if os.path.exists(f):
                os.remove(f)
    json.dump({'built': now.isoformat(timespec='seconds'), 'rows': rows}, open(OUT, 'w'), indent=1)
    print(f'wrote {OUT}: {len(rows)} rows')


if __name__ == '__main__':
    main()
