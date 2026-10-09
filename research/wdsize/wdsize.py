"""Wick catcher market-wide sizing on the engine's own rules (registered: reports/wd_sizing_prereg.md, written before
running). Dollar-level chronological replay of wd_engine v2 (config.yaml + rules.py) with the 5x margin cap, Binance
minimum order values and lot steps, the equity floor, cooldowns, and a marked-to-low worst moment.
Read-only: public 5m archive data in research_cache/edge, the readers' 1m fill-bar cache (copied), the daily 1m archive
for cache misses (never the API).
Usage: python wdsize.py   -> research_cache/wdsize/results.txt + runs.pkl"""
import heapq
import io
import json
import math
import os
import ssl
import sys
import time
import urllib.request
import zipfile

import numpy as np
import pandas as pd

D0 = '/root/bitana/research_cache/edge/'
OUT = '/root/bitana/research_cache/wdsize/'
K1M_SRC = '/root/bitana/logs/paper_cache/k1m_fillbars.pkl'
EXI = '/root/bitana/research_cache/nl/exchangeInfo_20261006.json'
CORE = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'BNBUSDT']
ALL20 = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT', 'DOTUSDT', 'LTCUSDT',
         'BCHUSDT', 'ATOMUSDT', 'NEARUSDT', 'UNIUSDT', 'FILUSDT', 'ETCUSDT', 'TRXUSDT', 'BNBUSDT', 'XLMUSDT', 'AAVEUSDT']
T0 = int(pd.Timestamp('2020-01-01').value // 10 ** 6); N = 2459 * 288; NH = N // 12; B5 = 300000
R_USD, R_ATR, STOP_R, LADDER, STRICT, MIN_ATR, HOLD = 1.0, 3.0, 20.0, (5.0, 6.5, 8.0), 0.1, 0.001, 288
DUMP, DISC_THR, DISC_FALL, DISC_HOLD = -0.017, -0.003, -0.01, 48
C_WICK, C_ADD, C_DISC = 0.0012, 0.0020, 0.0020
LEV, MMR, FLOOR, START, COOL = 5.0, 0.01, 60.0, 104.0, 288
PERIODS = [('2020-21', '2020-10-01', '2022-01-01'), ('2022-23', '2022-01-01', '2024-01-01'), ('2024-26', '2024-01-01', '2026-09-24'),
           ('continuous', '2020-10-01', '2026-09-24')]
DESIGNS = {'A0': ('core', 1), 'A2': ('core', 2), 'A3': ('core', 3), 'A5': ('core', 5),
           'B1': ('all', 1), 'B2': ('all', 2), 'B3': ('all', 3), 'B5': ('all', 5)}


def bi(ts):
    return int((pd.Timestamp(ts).value // 10 ** 6 - T0) // B5)


# ------------------------------------------------------------------ data
def load_grid(path):
    a = np.load(path)
    k = ((a[:, 0] - T0) // B5).astype(np.int64); ok = (k >= 0) & (k < N)
    A = np.full((N, 4), np.nan); A[k[ok]] = a[ok, 1:5]
    return A


Z = {s: load_grid(D0 + f'k5m/{s}.npy') for s in ALL20}
SPOT = {s: load_grid(D0 + f'spot5m/{s}.npy')[:, 3] for s in CORE}
ei = {x['symbol']: {f['filterType']: f for f in x['filters']} for x in json.load(open(EXI))['symbols']}
FILT = {s: dict(minn=float(ei[s]['MIN_NOTIONAL']['notional']), step=float(ei[s]['LOT_SIZE']['stepSize']),
                tick=float(ei[s]['PRICE_FILTER']['tickSize'])) for s in ALL20}
H = {}
for s, A in Z.items():
    o, h, l, c = A.T
    hh = h.reshape(NH, 12); hl = l.reshape(NH, 12); hcl = c.reshape(NH, 12)
    with np.errstate(all='ignore'):
        hhm, hlm = np.nanmax(hh, 1), np.nanmin(hl, 1)
    hc = pd.Series(hcl[:, -1]).ffill().values
    tr = np.r_[np.nan, np.maximum(hhm[1:], hc[:-1]) - np.minimum(hlm[1:], hc[:-1])]
    atr = pd.Series(tr).rolling(14).mean().values
    H[s] = dict(hc=hc, atr=atr, hlow=hl)
HC = pd.DataFrame({s: H[s]['hc'] for s in ALL20})
lr = np.log(HC).diff(); zz = lr / lr.rolling(720, min_periods=200).std().shift(1)
valid = zz.notna().sum(axis=1)
MKT = (((zz <= -2).sum(axis=1) >= 0.5 * valid) & (valid >= 16)).values
BTC_C = Z['BTCUSDT'][:, 3]


def floor_to(x, step):
    return math.floor(x / step + 1e-9) * step


def ceil_to(x, step):
    return math.ceil(x / step - 1e-9) * step


# ------------------------------------------------------------------ 1m fill-bar check (cache, else daily archive)
os.makedirs(OUT, exist_ok=True)
if os.path.exists(K1M_SRC) and not os.path.exists(OUT + 'k1m_fillbars_copy.pkl'):
    import shutil; shutil.copy(K1M_SRC, OUT + 'k1m_fillbars_copy.pkl')     # read a copy, never the live cache
_K1M = pd.read_pickle(OUT + 'k1m_fillbars_copy.pkl') if os.path.exists(OUT + 'k1m_fillbars_copy.pkl') else {}
_EXTRA_F = OUT + 'k1m_extra.pkl'
_EXTRA = pd.read_pickle(_EXTRA_F) if os.path.exists(_EXTRA_F) else {}
_DAY = {}
MISS = {'cache': 0, 'archive': 0, 'absent': 0}


def k1m(sym, j):
    t5 = T0 + j * B5
    key = f'{sym}|{t5 // 1000}'
    if key in _K1M:
        MISS['cache'] += 1; return _K1M[key]
    if key in _EXTRA:
        MISS['archive'] += 1; return _EXTRA[key]
    d = pd.Timestamp(t5, unit='ms').strftime('%Y-%m-%d')
    if (sym, d) not in _DAY:
        rows = {}
        try:
            b = urllib.request.urlopen(f'https://data.binance.vision/data/futures/um/daily/klines/{sym}/1m/{sym}-1m-{d}.zip',
                                       context=ssl.create_default_context(), timeout=60).read()
            z = zipfile.ZipFile(io.BytesIO(b))
            for line in z.read(z.namelist()[0]).decode().splitlines():
                if line and line[0].isdigit():
                    f = line.split(','); t = int(f[0]); t = t // 1000 if t > 10 ** 14 else t
                    rows[t] = [float(x) for x in f[1:5]]
        except Exception:
            rows = {}
        _DAY[(sym, d)] = rows
    rows = _DAY[(sym, d)]
    m = [rows.get(t5 + 60000 * i) for i in range(5)]
    if any(x is None for x in m):
        MISS['absent'] += 1; _EXTRA[key] = None; return None
    _EXTRA[key] = np.array(m); MISS['archive'] += 1
    return _EXTRA[key]


def tp_in_fill_bar(sym, j, thr, ref):
    m = k1m(sym, j)
    if m is None:
        return False
    fm = np.where(m[:, 2] <= thr)[0]
    return bool(len(fm) and (m[fm[0] + 1:, 1] > ref).any())


# ------------------------------------------------------------------ exits (price path only)
def wick_exit(s, fb, fill, ref, atr, thr):
    o, h, l, c = Z[s].T
    stop = fill - STOP_R * R_ATR * atr
    tp = ceil_to(ref, FILT[s]['tick']) + FILT[s]['tick']
    if l[fb] <= stop:
        return fb, stop, 'stop'
    if h[fb] >= tp and tp_in_fill_bar(s, fb, thr, ref):
        return fb, tp, 'tp'
    end = min(fb + HOLD, N - 1)
    seg_l, seg_h = l[fb + 1:end + 1], h[fb + 1:end + 1]
    hs = np.where(seg_l <= stop)[0]; ht = np.where(seg_h >= tp)[0]
    s0 = hs[0] if len(hs) else 10 ** 9; t0 = ht[0] if len(ht) else 10 ** 9
    if s0 < 10 ** 9 and s0 <= t0:
        j = fb + 1 + s0; px = o[j] if o[j] < stop else stop
        return j, px, 'stop'
    if t0 < 10 ** 9:
        return fb + 1 + t0, tp, 'tp'
    j = end
    while j < N - 1 and not np.isfinite(o[j]):
        j += 1
    return j, o[j], 'time'


# ------------------------------------------------------------------ candidate events (independent of size)
def wick_candidates(s):
    """Per bid hour hi and rung k: the first 5m bar of hour hi+1 with low <= bid - 0.1 ATR."""
    hc, atr, hl = H[s]['hc'], H[s]['atr'], H[s]['hlow']
    tick = FILT[s]['tick']
    out = {}
    nxt_low = np.r_[hl[1:], np.full((1, 12), np.nan)]
    okh = np.isfinite(atr) & (atr / hc >= MIN_ATR)
    for k in LADDER:
        bid = np.floor((hc - k * atr) / tick + 1e-9) * tick
        thr = bid - STRICT * atr
        with np.errstate(invalid='ignore'):
            hit = nxt_low <= thr[:, None]
        anyh = hit.any(1) & okh & (bid > 0)
        first = np.argmax(hit, 1)
        for hi in np.nonzero(anyh)[0]:
            out[(hi, k)] = (12 * (hi + 1) + first[hi], bid[hi], thr[hi])
    return out


def disc_candidates(s):
    o, h, l, c = Z[s].T; sc = SPOT[s]
    with np.errstate(all='ignore'):
        b = c / sc - 1
        btc60 = BTC_C / np.r_[np.full(12, np.nan), BTC_C[:-12]] - 1
    sig = (b <= DISC_THR) & (np.r_[np.nan, b[:-1]] > DISC_THR) & (btc60 <= DISC_FALL)
    return [int(j) + 1 for j in np.nonzero(sig)[0] if j + 1 + DISC_HOLD < N]


CAND = {s: wick_candidates(s) for s in ALL20}
DCAND = {s: disc_candidates(s) for s in CORE}


# ------------------------------------------------------------------ portfolio replay
def run(design, a, b, want_curve=False):
    univ, m = DESIGNS[design]
    syms = CORE if univ == 'core' else ALL20
    j0, j1 = bi(a), bi(b)
    ev = []                                                   # (bar, order, kind, payload)
    for s in syms:
        for (hi, k), (fb, bid, thr) in CAND[s].items():
            if j0 <= fb < j1:
                ev.append((fb, 0, LADDER.index(k), s, 'wick', (hi, k, bid, thr)))
    for s in CORE:
        for e in DCAND[s]:
            if j0 <= e < j1:
                ev.append((e, 0, 9, s, 'disc', None))
    ev.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    wallet = START; legs = []; open_ = []; pend = []          # pend: heap of (exit_bar, leg id)
    slot_free = {}; disc_free = {}; cool = {}
    stats = dict(rej_wick=0, rej_add=0, rej_disc=0, floor_block=0, boosted=0, boosted_pnl=0.0, fills=0, adds=0, discs=0, bumped_single=0)
    real_by_bar = []

    def realize(upto):
        nonlocal wallet
        while pend and pend[0][0] < upto:
            xb, i = heapq.heappop(pend)
            L = legs[i]; wallet += L['pnl']; real_by_bar.append((xb, L['pnl']))
            open_.remove(i)

    def equity_at(j):
        u = 0.0
        for i in open_:
            L = legs[i]; px = Z[L['s']][j, 3]
            if np.isfinite(px):
                u += L['q'] * (px - L['e'])
        return wallet + u

    def margin_ok(j, notional):
        used = sum(legs[i]['q'] * legs[i]['e'] for i in open_) / LEV
        return notional / LEV <= equity_at(j) - used

    def add_leg(s, kind, fb, xb, e, x, q, cost, boosted=False, why=''):
        pnl = q * (x - e) - cost * q * e
        legs.append(dict(s=s, kind=kind, fb=fb, xb=xb, e=e, x=x, q=q, pnl=pnl, boosted=boosted, why=why))
        i = len(legs) - 1; open_.append(i); heapq.heappush(pend, (xb, i))
        if boosted:
            stats['boosted'] += 1; stats['boosted_pnl'] += pnl
        return i

    # ladder/single plans are decided per (coin, bid hour); cache them
    plan_cache = {}

    def plan(s, hi):
        key = (s, hi)
        if key in plan_cache:
            return plan_cache[key]
        hc, atr = H[s]['hc'][hi], H[s]['atr'][hi]
        mk = bool(MKT[hi])
        mult = (m if mk else 1) if s in CORE else (m if mk else 0)
        if mult == 0:
            plan_cache[key] = None; return None
        f = FILT[s]; r = R_USD * mult
        rungs = {}
        bump = False
        for k in LADDER:
            bid = math.floor((hc - k * atr) / f['tick'] + 1e-9) * f['tick']
            q = floor_to(r / len(LADDER) / (R_ATR * atr), f['step'])
            if q * bid < f['minn']:
                bump = True
            rungs[k] = q
        if bump:
            bid = math.floor((hc - LADDER[0] * atr) / f['tick'] + 1e-9) * f['tick']
            q = floor_to(r / (R_ATR * atr), f['step'])
            if q * bid < f['minn']:
                q = ceil_to(f['minn'] / bid, f['step'])
            rungs = {LADDER[0]: q}
            stats['bumped_single'] += 1
        # 1x add-on quantity (the add-on is never boosted): this rung's quantity divided by the multiplier
        res = dict(rungs=rungs, mult=mult, mk=mk, atr=atr, ref=hc)
        plan_cache[key] = res
        return res

    for j, _, kord, s, kind, pl in ev:
        realize(j)
        if kind == 'wick':
            hi, k, bid, thr = pl
            P = plan(s, hi)
            if P is None or k not in P['rungs']:
                continue
            if slot_free.get((s, k), -1) >= 12 * (hi + 1) or cool.get(s, -1) > 12 * (hi + 1):
                continue
            if wallet < FLOOR:
                stats['floor_block'] += 1; continue
            q = P['rungs'][k]
            if q <= 0:
                continue
            o = Z[s][j, 0]; fill = min(o, bid) if np.isfinite(o) else bid
            if not margin_ok(j, q * fill):
                stats['rej_wick'] += 1; continue
            xb, x, why = wick_exit(s, j, fill, P['ref'], P['atr'], thr)
            stats['fills'] += 1
            add_leg(s, 'wick', j, xb, fill, x, q, C_WICK, boosted=P['mult'] > 1, why=why)
            slot_free[(s, k)] = xb
            if why == 'stop':
                cool[s] = xb + COOL
            # BTC-dump add-on at the fill-bar close, 1x quantity, exits with its rung
            bc0, bc1 = H['BTCUSDT']['hc'][hi], BTC_C[j]
            if xb > j and np.isfinite(bc1) and bc1 / bc0 - 1 <= DUMP:
                e2 = Z[s][j, 3]
                q2 = floor_to(q / P['mult'], FILT[s]['step'])
                if q2 * e2 < FILT[s]['minn']:
                    q2 = ceil_to(FILT[s]['minn'] / e2, FILT[s]['step'])
                if wallet < FLOOR:
                    stats['floor_block'] += 1
                elif not margin_ok(j, q2 * e2):
                    stats['rej_add'] += 1
                else:
                    stats['adds'] += 1
                    stop2 = e2 - STOP_R * R_ATR * P['atr']
                    x2, xb2 = x, xb
                    seg = Z[s][j + 1:xb + 1, 2]
                    hs = np.where(seg <= stop2)[0]
                    if len(hs):
                        xb2 = j + 1 + hs[0]; x2 = stop2
                    add_leg(s, 'addon', j + 1, xb2, e2, x2, q2, C_ADD, why='addon')
        else:                                                # discount entry at the open of bar j
            if disc_free.get(s, -1) >= j or wallet < FLOOR:
                if wallet < FLOOR:
                    stats['floor_block'] += 1
                continue
            hi = j // 12 - 1                                  # last closed hour before the entry bar
            atr = H[s]['atr'][hi]; e = Z[s][j, 0]
            if not (np.isfinite(atr) and np.isfinite(e) and atr > 0):
                continue
            q = floor_to(R_USD / (R_ATR * atr), FILT[s]['step'])
            if q * e < FILT[s]['minn']:
                continue
            if not margin_ok(j, q * e):
                stats['rej_disc'] += 1; continue
            xb = j + DISC_HOLD
            while xb < N - 1 and not np.isfinite(Z[s][xb, 0]):
                xb += 1
            stats['discs'] += 1
            add_leg(s, 'disc', j, xb, e, Z[s][xb, 0], q, C_DISC, why='time')
            disc_free[s] = xb
    realize(10 ** 12)

    # ---------------- marked equity on the 5m grid
    n = j1 - j0 + HOLD + 2
    U_low = np.zeros(n); U_cls = np.zeros(n); NOT = np.zeros(n); W = np.zeros(n)
    for L in legs:
        a_, b_ = L['fb'] - j0, L['xb'] - j0
        if b_ <= a_:
            continue
        A = Z[L['s']]
        lows = A[L['fb']:L['xb'], 2]; cls = A[L['fb']:L['xb'], 3]
        U_low[a_:b_] += np.nan_to_num(L['q'] * (lows - L['e']))
        U_cls[a_:b_] += np.nan_to_num(L['q'] * (cls - L['e']))
        NOT[a_:b_] += L['q'] * L['e']
    for xb, pnl in real_by_bar:
        W[max(xb - j0, 0):] += pnl
    W += START
    E_low, E_cls = W + U_low, W + U_cls
    peak = np.maximum.accumulate(E_cls)
    ddmark = (E_low - peak).min(); imin = int(np.argmin(E_low - peak))
    liq = bool(((E_low <= MMR * NOT) & (NOT > 0)).any())
    days = pd.Series(E_cls[::288])
    tot = W[-1] - START
    gross = np.where(E_cls > 0, NOT / np.maximum(E_cls, 1e-9), 0).max()
    yrs = {}
    for L in legs:
        y = pd.Timestamp(T0 + L['xb'] * B5, unit='ms').year
        yrs[y] = yrs.get(y, 0.0) + L['pnl']
    worst_t = pd.Timestamp(T0 + (j0 + imin) * B5, unit='ms')
    curve = dict(E_low=E_low, E_cls=E_cls, j0=j0) if want_curve else {}
    return dict(design=design, total=tot, ddmark=ddmark, worst_t=worst_t, min_eq=E_low.min(), liq=liq,
                ratio=tot / abs(ddmark) if ddmark < 0 else np.inf, worst_day=days.diff().min(), gross=gross,
                legs=len(legs), years=yrs, **curve, **stats), legs


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time(); res = []; legs_all = {}
    for lab, a, b in PERIODS:
        for d in DESIGNS:
            r, L = run(d, a, b)
            r['period'] = lab; res.append(r); legs_all[(lab, d)] = L
            print(f"{lab:<10} {d}: total ${r['total']:+8.1f} | worst marked DD ${r['ddmark']:+7.1f} ({r['worst_t']:%Y-%m-%d %H:%M}) "
                  f"| ratio {r['ratio']:5.2f} | min equity ${r['min_eq']:6.1f} | liq {r['liq']} | rejected w/a/d {r['rej_wick']}/{r['rej_add']}/{r['rej_disc']} "
                  f"| floor {r['floor_block']} | fills {r['fills']} adds {r['adds']} disc {r['discs']} | boosted {r['boosted']} (${r['boosted_pnl']:+.1f}) "
                  f"| worst day ${r['worst_day']:+.1f} | gross {r['gross']:.1f}x | {time.time() - t0:.0f}s", flush=True)
    pd.to_pickle(_EXTRA, _EXTRA_F)
    pd.to_pickle({'res': res, 'legs': legs_all}, OUT + 'runs.pkl')
    print('1m lookups:', MISS)
