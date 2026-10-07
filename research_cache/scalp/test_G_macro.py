# Test G — macro releases (rules: reports/scalping_prereg.md, registered before this run).
import json, os, sys
import numpy as np, pandas as pd
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_B_intraday_mom.py')).read().split('def main')[0]
exec(src)   # opens(), _px(), ret(), capr, E, ny_times

CAL = json.load(open('/root/bitana/research_cache/scalp/macro_calendar.json'))
DISC_END = '2023-12-31'
COSTG = {'BTCUSDT': 12.0, 'ETHUSDT': 12.0, 'BNBUSDT': 12.0}
HS = {'15m': 15, '30m': 30, '60m': 60}
FIVE = pd.Timedelta('5min')

P = {s: opens(s) for s in capr.UNIVERSE}
SIG = {}
for s, p in P.items():
    lr = np.log(p).diff()
    SIG[s] = lr.rolling(8640, min_periods=4000).std().shift(1) * 1e4     # 30-day σ of 5m returns, prior bars only


def event_times(kind):
    days = pd.DatetimeIndex(pd.to_datetime(CAL[kind]))
    hm = '14:00' if kind.startswith('FOMC') else '08:30'
    return ny_times(days, hm)


def cells(t0, kind_tag):
    """Per event: mean across coins of gross for each cell; plus coin cost mean."""
    out = []
    for s, p in P.items():
        r0 = ret(p, t0, t0 + FIVE)
        sig = SIG[s].reindex(t0).values
        d = np.sign(r0)
        rec = pd.DataFrame({'t0': t0, 'sym': s, 'r0': r0, 'tail': np.abs(r0) >= 2 * sig, 'cost': COSTG.get(s, 13.0)})
        for hk, hm in HS.items():
            fwd = ret(p, t0 + FIVE, t0 + FIVE + pd.Timedelta(minutes=hm))
            rec[f'CONT_{hk}'] = d * fwd
            rec[f'REV_{hk}'] = -d * fwd
        out.append(rec)
    x = pd.concat(out).dropna(subset=['r0'])
    x = x[x.r0 != 0]
    x['kind'] = kind_tag
    return x


ev = pd.concat([cells(event_times(k), k.split('_')[0]) for k in ('CPI_0830ET', 'NFP_0830ET', 'FOMC_1400ET')])
ev = ev[ev.t0 <= '2026-09-24']
alld = set(pd.to_datetime(CAL['CPI_0830ET'] + CAL['NFP_0830ET'] + CAL['FOMC_1400ET']).date)
wd = pd.date_range('2020-09-07', DISC_END, freq='B')
wd = wd[[d.date() not in alld for d in wd]]
plc = {'0830': cells(ny_times(wd, '08:30'), 'P0830'), '1400': cells(ny_times(wd, '14:00'), 'P1400')}
print(f"events: {ev.groupby('kind').t0.nunique().to_dict()}, coin-events {len(ev)}; placebo days {len(wd)}")

rows = []
for sub in ('all', 'tail'):
    e = ev if sub == 'all' else ev[ev['tail']]
    for dirn in ('CONT', 'REV'):
        for hk in HS:
            c = f'{dirn}_{hk}'
            per_ev = e.groupby('t0').apply(lambda g: pd.Series({'g': g[c].mean(), 'n': (g[c] - g.cost).mean()}))
            # placebo gross, weighted like the events (08:30 vs 14:00 mix), discovery only
            pw = []
            for key, kinds in (('0830', ('CPI', 'NFP')), ('1400', ('FOMC',))):
                pe = plc[key] if sub == 'all' else plc[key][plc[key]['tail']]
                w = e[(e.t0 <= DISC_END) & e.kind.isin(kinds)].t0.nunique()
                pw.append((pe.groupby('t0')[c].mean().mean(), w))
            pg = sum(a * w for a, w in pw) / max(sum(w for _, w in pw), 1)
            for per, m in (('disc', per_ev.index <= DISC_END), ('hold', per_ev.index > DISC_END)):
                y = per_ev[m]
                rows.append(dict(sub=sub, cell=c, per=per, events=len(y), gross=y.g.mean(), net=y.n.mean(),
                                 t=y.n.mean() / y.n.std() * np.sqrt(len(y)) if len(y) > 2 else np.nan,
                                 placebo_gross=pg if per == 'disc' else np.nan))
out = pd.DataFrame(rows); out.to_pickle('/root/bitana/research_cache/scalp/test_G.pkl')
pd.set_option('display.width', 200)
print(out.round(2).to_string(index=False))
print('\nby release type (all events, gross bp, disc / hold):')
for k in ('CPI', 'NFP', 'FOMC'):
    e = ev[ev.kind == k]
    print(' ', k, ' '.join(f"{c}: {e[e.t0 <= DISC_END].groupby('t0')[c].mean().mean():6.1f}/{e[e.t0 > DISC_END].groupby('t0')[c].mean().mean():6.1f}"
                         for c in ('CONT_15m', 'CONT_60m')),
          f"| |r0| median {e.r0.abs().median():.0f} bp")
