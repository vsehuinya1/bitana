# Test B — intraday time-series momentum (rules: reports/scalping_prereg.md, registered before this run).
import sys
import numpy as np, pandas as pd
sys.path.append('/root/bitana/research')
import capitulation_reader as capr

E = '/root/bitana/research_cache/edge/'
DISC_END, HOLD_START, END = '2024-12-31', '2025-01-01', '2026-09-24'
COST = {'BTCUSDT': 10.1, 'ETHUSDT': 10.1, 'BNBUSDT': 10.1}


def opens(sym):
    a = np.load(f'{E}k5m/{sym}.npy')
    s = pd.Series(a[:, 1], index=pd.to_datetime(a[:, 0], unit='ms', utc=True))
    s = s[~s.index.duplicated()]
    s = s[s.index >= '2020-09-01']
    return s.reindex(pd.date_range(s.index[0], s.index[-1], freq='5min'))


def _px(p, t):
    """Price at timestamps t via integer positions on the regular 5m grid (fast equivalent of p.reindex(t))."""
    d = pd.DatetimeIndex(t).as_unit('ns').asi8 - p.index[0].value
    i = d // 300_000_000_000
    exact = d % 300_000_000_000 == 0
    v = p.values; ok = (i >= 0) & (i < len(v)) & exact
    out = np.full(len(i), np.nan); out[ok] = v[i[ok]]
    return out


def ret(p, t1, t2):
    return (_px(p, t2) / _px(p, t1) - 1) * 1e4


def cell_trades(p, s1, s2, e1, e2, tail):
    sig = pd.Series(ret(p, s1, s2), index=s1.normalize())
    tr = pd.Series(ret(p, e1, e2), index=s1.normalize())
    if tail:
        thr = sig.abs().rolling(60, min_periods=40).quantile(0.8).shift(1)
        sig = sig.where(sig.abs() >= thr)
    g = np.sign(sig) * tr
    return g[np.isfinite(g) & (sig != 0)]


def ny_times(days, hm):
    loc = [pd.Timestamp(f'{d.date()} {hm}', tz='America/New_York').tz_convert('UTC') for d in days]
    return pd.DatetimeIndex(loc)


def day_stats(per_coin, cost):
    """per_coin: dict sym -> gross Series (index day). Returns day-level net series."""
    df = pd.DataFrame({s: g - cost.get(s, 11.0) for s, g in per_coin.items()})
    gr = pd.DataFrame(per_coin)
    return df.mean(axis=1).dropna(), gr.mean(axis=1).dropna(), int(df.notna().sum().sum())


def summ(x):
    return x.mean(), x.mean() / x.std() * np.sqrt(len(x)) if len(x) > 2 else np.nan, len(x)


def main():
    P = {s: opens(s) for s in capr.UNIVERSE}
    utc_days = pd.date_range('2020-09-02', END, freq='D', tz='UTC')
    wk = utc_days[utc_days.weekday < 5]
    cells = {
        'B1 UTC 00:00-00:30 -> 23:30-24:00': lambda: (utc_days, utc_days + pd.Timedelta('30min'),
                                                      utc_days + pd.Timedelta('23h30min'), utc_days + pd.Timedelta('24h')),
        'B2 NY 09:30-10:00 -> 10:00-11:00': lambda: (ny_times(wk, '09:30'), ny_times(wk, '10:00'), ny_times(wk, '10:00'), ny_times(wk, '11:00')),
        'B3 NY 09:30-10:00 -> 15:30-16:00': lambda: (ny_times(wk, '09:30'), ny_times(wk, '10:00'), ny_times(wk, '15:30'), ny_times(wk, '16:00')),
    }
    geom = {'B1': ('30min', '23h', '30min', utc_days), 'B2': ('30min', '0min', '60min', wk), 'B3': ('30min', '5h30min', '30min', wk)}
    rows = []
    for name, f in cells.items():
        s1, s2, e1, e2 = f()
        for tail in (False, True):
            pc = {s: cell_trades(p, s1, s2, e1, e2, tail) for s, p in P.items()}
            net, gross, n = day_stats(pc, COST)
            # placebo: same geometry at the 48 half-hour UTC offsets, discovery gross mean
            sl, gap, tl, days = geom[name[:2]]
            plc = []
            for k in range(48):
                o = pd.Timedelta(minutes=30 * k)
                a1 = days + o; a2 = a1 + pd.Timedelta(sl); b1 = a2 + pd.Timedelta(gap); b2 = b1 + pd.Timedelta(tl)
                pg = {s: cell_trades(p, a1, a2, b1, b2, tail) for s, p in P.items()}
                _, gg, _ = day_stats(pg, COST)
                plc.append(gg[:DISC_END].mean())
            gd = gross[:DISC_END].mean()
            for per, sl_ in (('disc', slice(None, DISC_END)), ('hold', slice(HOLD_START, None))):
                m, t, nd = summ(net[sl_])
                rows.append(dict(cell=name, tail=tail, period=per, days=nd, gross=gross[sl_].mean(), net=m, t=t,
                                 net_MT=m + 2.5,
                                 placebo_pct=(np.array(plc) < gd).mean() * 100 if per == 'disc' else np.nan,
                                 placebo_med=np.median(plc) if per == 'disc' else np.nan))
    out = pd.DataFrame(rows)
    out.to_pickle('/root/bitana/research_cache/scalp/test_B.pkl')
    pd.set_option('display.width', 250)
    print(out.round(2).to_string(index=False))
    # high-vol subset: days in the top quintile of BTC realised 5m vol over the previous day (no look-ahead)
    btc = P['BTCUSDT']; rv = np.log(btc).diff().groupby(btc.index.normalize()).std().shift(1)
    hv = rv[rv >= rv.rolling(250, min_periods=60).quantile(0.8)].index
    print('\nHigh-vol days subset (prev-day BTC 5m vol in top 20% of trailing 250d):')
    for name, f in cells.items():
        s1, s2, e1, e2 = f()
        pc = {s: cell_trades(p, s1, s2, e1, e2, False) for s, p in P.items()}
        net, gross, _ = day_stats(pc, COST)
        for per, sl_ in (('disc', slice(None, DISC_END)), ('hold', slice(HOLD_START, None))):
            x = net[sl_]; x = x[x.index.isin(hv)]
            m, t, nd = summ(x)
            print(f'  {name:36s} {per}  days {nd:4d}  net {m:7.2f}  t {t:5.2f}  gross {gross[sl_][gross[sl_].index.isin(hv)].mean():6.2f}')


if __name__ == '__main__':
    main()
