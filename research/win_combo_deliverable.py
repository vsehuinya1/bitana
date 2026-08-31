#!/usr/bin/env python3
"""Winning-combination deliverable — checklist-graded, single auditable run.

Maps 1:1 to the 16-item checklist. Writes:
  research/WIN_COMBO_DELIVERABLE.md      (deliverable, sections 1-16)
  research/win_combo_segments_full.csv   (every declared-dimension segment, all n)
  research/win_combo_candidates.csv      (n>=30 cells: CI/p/BH/IS/OOS/WF/verdict)
Read-only DB access; no live orders. Stdlib only.
"""
import sqlite3, csv, math
from datetime import datetime
from collections import defaultdict

RUN_TS = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
assert datetime(2026, 8, 31).weekday() == 0, "weekday convention broken (0=Mon)"

PRIMARY = {'asia_pump_short_4h','asia_burst_fade','london_burst_fade','ny_flush_buy_4h',
           'burst_follow','late_fade','nony_momentum','setup_fade','setup_follow',
           'follow_3h_all','fade_3h_asia','fade_3h_late','fade_3h_london',
           'ny_burst_follow','ny_burst_fade','v65_strict_long','v65_strict_ny_long'}
FEE_SLIP = 0.0010  # 10 bps round trip = taker 4bps x2 + slippage 1bp x2 (Binance USDT-M)
IS_CUT = '2026-08-01'
WF_CUTS = ('2026-07-20', '2026-08-10')

con = sqlite3.connect('file:/root/bitana/storage/signal_shadow.db?mode=ro', uri=True)
cur = con.cursor(); cur.row_factory = sqlite3.Row

def q(v):
    if v is None: return None
    try: return float(v)
    except Exception: return None

def bucket(v, cuts, labels):
    if v is None: return 'NA'
    for c, l in zip(cuts, labels):
        if v < c: return l
    return labels[-1]

def wilson(k, n, z=1.96):
    if n == 0: return (0.0, 1.0)
    p = k / n; d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d
    h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / d
    return (max(0.0, c-h), min(1.0, c+h))

def binom_sf(k, n, p0):
    if n == 0: return 1.0
    if p0 <= 0: return 0.0 if k > 0 else 1.0
    if p0 >= 1: return 1.0
    lg = math.lgamma; s = 0.0
    for i in range(k, n+1):
        lp = lg(n+1)-lg(i+1)-lg(n-i+1) + i*math.log(p0) + (n-i)*math.log(1-p0)
        s += math.exp(lp)
    return min(1.0, s)

def bh(pvals):
    m = len(pvals); out = [1.0]*m
    idx = sorted(range(m), key=lambda i: pvals[i])
    prev = 1.0
    for rank in range(m, 0, -1):
        i = idx[rank-1]
        prev = min(prev, pvals[i]*m/rank)
        out[i] = prev
    return out

# ---------- load + enrich ----------
rows = cur.execute("""SELECT strategy, session, symbol, side, entry_time, exit_time, exit_reason,
       pnl_atr, stop_atr, would_live_accept, hour, decile, is_weekend,
       btc_trend_state, symbol_trend_state, btc_adx, funding_rate_symbol,
       entry_vol_z, entry_atr_pct, liq_imb, v_confirms3, v_strict, cascade_active, trigger,
       cluster_breadth, market_breadth_pct, spread_bps, concurrent_positions_total,
       oi_delta_30m_pct
FROM shadow_trades WHERE status='closed'""").fetchall()

T = []; skip_r = 0; book_n = defaultdict(int)
for r in rows:
    book_n[r['strategy']] += 1
    if r['strategy'] not in PRIMARY: continue
    s_atr = q(r['stop_atr']); pa = q(r['pnl_atr'])
    if not s_atr or pa is None: skip_r += 1; continue
    et = datetime.fromisoformat(r['entry_time'])
    xt = datetime.fromisoformat(r['exit_time']) if r['exit_time'] else None
    hold_h = ((xt - et).total_seconds()/3600.0) if xt else 0.0
    side_sign = 1.0 if r['side'] == 'LONG' else -1.0
    fr = q(r['funding_rate_symbol'])
    T.append(dict(strat=r['strategy'], session=r['session'] or 'NA', sym=r['symbol'],
                  side=r['side'], exit=r['exit_reason'], pnl=pa, s_atr=s_atr,
                  atr_pct=q(r['entry_atr_pct']), gross_R=pa/s_atr,
                  order_key=(r['exit_time'] or r['entry_time']),
                  day=r['entry_time'][:10], mon=r['entry_time'][:7],
                  wd=et.weekday(), hour=r['hour'] if r['hour'] is not None else et.hour,
                  regime=r['btc_trend_state'] or 'NA', wla=r['would_live_accept'],
                  decile=r['decile'], v3=bool(r['v_confirms3']),
                  rvz=q(r['entry_vol_z']), adx=q(r['btc_adx']), spr=q(r['spread_bps']),
                  fr=fr, hold_h=hold_h,
                  fund_pct=(side_sign*fr*(max(hold_h,0.0)/8.0)) if fr is not None else 0.0))

book_atr = defaultdict(list)
for t in T:
    if t['atr_pct'] is not None: book_atr[t['strat']].append(t['atr_pct'])
book_med = {b: sorted(v)[len(v)//2] for b, v in book_atr.items()}
_gm = sorted([t['atr_pct'] for t in T if t['atr_pct'] is not None])[len([t for t in T if t['atr_pct'] is not None])//2]
n_imputed = 0; n_fund_na = 0
for t in T:
    if t['atr_pct'] is None: t['atr_pct'] = book_med.get(t['strat'], _gm); n_imputed += 1
    if t['fr'] is None: n_fund_na += 1
    stop_pct = t['s_atr'] * t['atr_pct']
    cost_pct = FEE_SLIP + t['fund_pct']
    t['net_R'] = (t['pnl']*t['atr_pct'] - cost_pct) / stop_pct if stop_pct > 0 else 0.0
    t['cost_R'] = cost_pct / stop_pct if stop_pct > 0 else 0.0
    t['volb'] = bucket(t['atr_pct'], [0.0015, 0.0040], ['lo<0.15%', 'mid', 'hi>=0.40%'])
    t['rvb'] = bucket(t['rvz'], [2, 5], ['<2', '2-5', '>=5'])
    t['adxb'] = bucket(t['adx'], [20, 30], ['<20', '20-30', '>=30'])
    t['sprb'] = bucket(t['spr'], [2, 5], ['<2bps', '2-5bps', '>=5bps'])
    t['hourb'] = '0-5' if t['hour'] <= 5 else ('6-11' if t['hour'] <= 11 else ('12-17' if t['hour'] <= 17 else '18-23'))
    t['decb'] = bucket(t['decile'], [1, 3, 9], ['D1', 'D2-3', 'D4-9', 'D10']) if t['decile'] is not None else 'NA'
    t['weekend'] = t['wd'] >= 5
    t['weekday'] = t['wd']

N = len(T)
all_R = [t['net_R'] for t in T]
wins_all = sum(1 for x in all_R if x > 0)
E_all = sum(all_R)/N
lo_all, hi_all = wilson(wins_all, N)
aw_all = sum(x for x in all_R if x > 0)/wins_all
al_all = -sum(x for x in all_R if x < 0)/(N - wins_all)
E_identity = (wins_all/N)*aw_all - (1 - wins_all/N)*al_all

# ---------- segment engine ----------
def seg_stats(mem):
    n = len(mem)
    Rs = sorted(mem, key=lambda t: t['order_key'])
    vals = [t['net_R'] for t in Rs]
    w = sum(v for v in vals if v > 0); l = -sum(v for v in vals if v < 0)
    nw = sum(1 for v in vals if v > 0)
    wr = nw/n if n else 0.0
    aw = (sum(v for v in vals if v > 0)/nw) if nw else 0.0
    nl = n - nw
    al = (-sum(v for v in vals if v < 0)/nl) if nl else 0.0
    pf = w/l if l else float('inf')
    streak = mx = 0; cum = peak = mdd = 0.0
    for v in vals:
        streak = streak+1 if v < 0 else 0
        mx = max(mx, streak)
        cum += v; peak = max(peak, cum); mdd = max(mdd, peak-cum)
    lo, hi = wilson(nw, n)
    W = aw if nw else 0.0; L = al if nl else 0.0
    be = (L/(W+L)) if (W+L) > 0 else 0.0
    p = binom_sf(nw, n, be)
    days = {t['day'] for t in mem}
    dayR = defaultdict(float)
    for t in mem: dayR[t['day']] += t['net_R']
    tot = sum(vals)
    top_day = (max(dayR.values())/tot) if tot > 0 else float('nan')
    return dict(n=n, nw=nw, wr=wr, wr_g=sum(1 for t in mem if t['gross_R'] > 0)/n,
                aw=aw, al=al, E=tot/n, pf=pf, sumR=tot, streak=mx, mdd=mdd,
                lo=lo, hi=hi, be=be, p=p, days=len(days), top_day=top_day)

def split_stats(mem, cut):
    a = [t for t in mem if t['day'] < cut]; b = [t for t in mem if t['day'] >= cut]
    def one(m):
        if not m: return None
        s = seg_stats(m); return s
    return one(a), one(b)

def wf_stats(mem):
    out = []
    bounds = [(None, WF_CUTS[0]), (WF_CUTS[0], WF_CUTS[1]), (WF_CUTS[1], None)]
    for lo_, hi_ in bounds:
        m = [t for t in mem if (lo_ is None or t['day'] >= lo_) and (hi_ is None or t['day'] < hi_)]
        out.append(seg_stats(m) if m else None)
    return out

# ---------- full declared-dimension segmentation ----------
DIMS = ['strat', 'session', 'sym', 'regime', 'volb', 'rvb', 'adxb', 'sprb',
        'hourb', 'decb', 'weekday', 'weekend', 'side']
full_rows = []
def emit(dimname, mem, dims_used):
    if not mem: return
    s = seg_stats(mem)
    full_rows.append([dimname, dims_used, s['n'], round(100*s['wr'], 1), round(100*s['wr_g'], 1),
                      round(s['aw'], 3), round(s['al'], 3), round(s['E'], 4),
                      (round(s['pf'], 2) if s['pf'] != float('inf') else 'inf'),
                      round(s['sumR'], 1), s['streak'], round(s['mdd'], 1), s['days'],
                      (round(s['top_day'], 2) if s['top_day'] == s['top_day'] else 'inf'),
                      round(100*s['lo'], 1), round(100*s['hi'], 1), round(100*s['be'], 1),
                      (f"{s['p']:.2e}" if s['p'] < 1 else '1.00'),
                      'LOW-N' if s['n'] < 30 else 'OK'])

for d in DIMS:
    g = defaultdict(list)
    for t in T: g[t[d]].append(t)
    for k in sorted(g, key=str): emit(d, g[k], f"{d}={k}")
# pair x book x regime = the declared core cross, every combination
g = defaultdict(list)
for t in T: g[(t['sym'], t['strat'], t['regime'])].append(t)
for k in sorted(g, key=str): emit('symxstratxregime', g[k], '|'.join(map(str, k)))
# book x each other dim
for d in ['regime', 'volb', 'adxb', 'decb', 'weekday', 'hourb', 'weekend']:
    g = defaultdict(list)
    for t in T: g[(t['strat'], t[d])].append(t)
    for k in sorted(g, key=str): emit(f'stratx{d}', g[k], '|'.join(map(str, k)))

with open('/root/bitana/research/win_combo_segments_full.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['segment_type', 'dims', 'n', 'WR_net%', 'WR_gross%', 'avg_win_R', 'avg_loss_R',
                'E_net', 'PF_net', 'sumR_net', 'max_losing_streak', 'maxDD_R', 'days',
                'top_day_share', 'wilson_lo%', 'wilson_hi%', 'breakeven_WR%', 'p_vs_be', 'confidence'])
    w.writerows(full_rows)
n_low = sum(1 for r in full_rows if r[-1] == 'LOW-N')

# ---------- candidate enumeration (n>=30), IS discovery -> OOS validation ----------
cells = {}
def add_cell(desc, keys):
    gg = defaultdict(list)
    for t in T: gg[tuple(t[k] for k in keys)].append(t)
    for k, mem in gg.items():
        if len(mem) < 30: continue
        cells[desc + '=' + '|'.join(str(x) for x in k)] = mem

CATS1 = ['strat', 'session', 'sym', 'regime', 'volb', 'rvb', 'adxb', 'sprb', 'hourb',
         'decb', 'weekday', 'weekend', 'side', 'v3']
for c in CATS1: add_cell(c, [c])
core = ['strat', 'regime', 'side', 'weekday', 'hourb', 'decb', 'weekend', 'sym']
others = ['volb', 'rvb', 'adxb', 'sprb', 'decb', 'v3', 'session']
import itertools
for a, b in itertools.combinations(sorted(set(core + others)), 2):
    add_cell(f'{a}x{b}', [a, b])
for keys in [('strat', 'regime', 'decb'), ('strat', 'weekday', 'regime'),
             ('strat', 'weekday', 'hourb'), ('strat', 'regime', 'adxb'),
             ('strat', 'side', 'regime'), ('strat', 'regime', 'rvb')]:
    add_cell('x'.join(keys), list(keys))

cands = []
for desc, mem in cells.items():
    s = seg_stats(mem)
    iss, oos = split_stats(mem, IS_CUT)
    wf = wf_stats(mem)
    gate_days = s['days'] >= 10
    gate_td = (s['top_day'] == s['top_day'] and s['top_day'] < 0.40)
    g_is = bool(iss and iss['n'] >= 20 and iss['lo'] > iss['be'])
    g_oos = bool(oos and oos['n'] >= 10 and oos['E'] > 0 and oos['p'] < 0.05)
    g_wf = all(w and w['E'] > 0 for w in wf)
    cands.append(dict(desc=desc, s=s, iss=iss, oos=oos, wf=wf,
                      g_days=gate_days, g_td=gate_td, g_is=g_is, g_oos=g_oos, g_wf=g_wf))

qs = bh([c['s']['p'] for c in cands])
for c, qq in zip(cands, qs):
    c['q'] = qq
    c['g_bh'] = c['q'] < 0.05
    c['validated'] = all(c[g] for g in ('g_days', 'g_td', 'g_is', 'g_oos', 'g_wf', 'g_bh'))

n_valid = sum(1 for c in cands if c['validated'])
n_is = sum(1 for c in cands if c['g_is'])
n_is_oos = sum(1 for c in cands if c['g_is'] and c['g_oos'])

with open('/root/bitana/research/win_combo_candidates.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['cell', 'n', 'WR_net%', 'WR_gross%', 'E_net', 'PF_net', 'sumR_net', 'days',
                'top_day_share', 'wilson95%', 'breakeven_WR%', 'p_vs_be', 'BH_q',
                'IS_n', 'IS_WR%', 'IS_E', 'IS_sig', 'OOS_n', 'OOS_WR%', 'OOS_E', 'OOS_sig',
                'WF1_E', 'WF2_E', 'WF3_E', 'streak', 'maxDD_R', 'VALIDATED'])
    for c in sorted(cands, key=lambda c: (not c['validated'], -(c['oos']['E'] if c['oos'] else -9), -c['s']['E'])):
        s, iss, oos, wf = c['s'], c['iss'], c['oos'], c['wf']
        w.writerow([c['desc'], s['n'], round(100*s['wr'], 1), round(100*s['wr_g'], 1),
                    round(s['E'], 4), (round(s['pf'], 2) if s['pf'] != float('inf') else 'inf'),
                    round(s['sumR'], 1), s['days'],
                    (round(s['top_day'], 2) if s['top_day'] == s['top_day'] else 'inf'),
                    f"[{100*s['lo']:.1f},{100*s['hi']:.1f}]", round(100*s['be'], 1),
                    f"{s['p']:.2e}", f"{c['q']:.3f}",
                    iss['n'] if iss else 0, round(100*iss['wr'], 1) if iss else '',
                    round(iss['E'], 3) if iss else '', 'Y' if c['g_is'] else 'N',
                    oos['n'] if oos else 0, round(100*oos['wr'], 1) if oos else '',
                    round(oos['E'], 3) if oos else '', 'Y' if c['g_oos'] else 'N',
                    round(wf[0]['E'], 3) if wf[0] else '', round(wf[1]['E'], 3) if wf[1] else '',
                    round(wf[2]['E'], 3) if wf[2] else '', s['streak'], round(s['mdd'], 1),
                    'YES' if c['validated'] else 'NO'])

# ---------- report ----------
syms = sorted({t['sym'] for t in T})
null_reg = [t for t in T if t['regime'] == 'NA']
null_span = (min(t['day'] for t in null_reg), max(t['day'] for t in null_reg)) if null_reg else None
dr = (min(t['day'] for t in T), max(t['day'] for t in T))
per_book = sorted(book_n.items(), key=lambda kv: -kv[1])

L = []
A = L.append
A(f"# Winning-Combination Analysis — Shadow DB (Checklist Deliverable)\n")
A(f"**Run (single auditable execution)**: {RUN_TS} UTC | **DB**: `/root/bitana/storage/signal_shadow.db` (SQLite, read-only URI `file:...?mode=ro`) | **No live orders placed** — analysis-only connection.\n")

A("\n## 1. Database identification & connection\n")
A("- Type: SQLite (sqlite3 stdlib), connected read-only via URI `file:/root/bitana/storage/signal_shadow.db?mode=ro`; no write handle, no exchange endpoints called, no live trading orders placed at any point.")
A(f"- Tables in DB: snapshots, burst_snapshots, setup_snapshots, setup_r_path, shadow_trades, shadow_pending_entries, trade_r_path, sqlite_sequence. **Analysis uses `shadow_trades` only** ({len(rows)} closed rows scanned, {N} used).")
A("- Writer: `research/signal_shadow.py` (INSERT at open, UPDATEs on exit only — entry features frozen at decision time; no back-fill path).")

A("\n## 2. Data coverage\n")
A(f"- Date range: **{dr[0]} → {dr[1]}** (~9 weeks). Records: {len(rows)} closed in table, **{N} analyzed** (17 primary books), {skip_r} skipped (NULL/zero stop).")
A(f"- Instruments: {len(syms)} pairs — {', '.join(syms)}.")
A("- Exclusions: variant/hold-time re-count books excluded (one canonical book per family; `_s4/_s6/_s8/_tsl/_limit15/_open/_scalein`, hold variants `_1h/_2h/_8h/_24h`, session splits where a `_all` parent exists). Books: " + ", ".join(f"{b}({n})" for b, n in per_book) + ".")
A(f"- Gaps: btc_trend_state NULL cluster n={len(null_reg)} spanning {null_span[0]}→{null_span[1]} (known writer warm-up gap) — regime='NA' bucket kept visible, never silently dropped. `entry_atr_pct` NULL n={n_imputed} (imputed per-book median, documented); `funding_rate_symbol` NULL n={n_fund_na} (funding cost treated as 0 for those rows).")

A("\n## 3. Win definition (applied to every record)\n")
A("- Canonical gross R = `pnl_atr / stop_atr` (shadow's stop-normalized multiple, 10-ATR books / 4-ATR books).")
A(f"- **Win := net_R > 0**, net_R = (gross price-move % − costs %) / stop-distance %, where costs % = {FEE_SLIP*1e4:.0f} bps fees+slippage round trip + funding cost % (side-signed entry funding × hold_hours/8). Applied to **every** record including n<3 segments; gross WR reported alongside for transparency.")
A(f"- Whole-book baseline: n={N}, net WR={100*wins_all/N:.1f}% (Wilson 95% [{100*lo_all:.1f}, {100*hi_all:.1f}]), E_net={E_all:+.4f}R/trade. Identity check: WR·avg_win − (1−WR)·avg_loss = {100*wins_all/N:.4f}·{aw_all:+.4f} − {100*(1-wins_all/N):.4f}·{al_all:.4f} = {E_identity:+.4f} ✓ (matches E_net={E_all:+.4f}).")

A("\n## 4. Enrichment — declared dimensions (entry-time only; exact buckets)\n")
A("- session: writer `session` column (UTC-hour-derived: asia/london/ny/late; timezone UTC). hour: UTC 0-23; hour-band 0-5/6-11/12-17/18-23.")
A("- weekday: Python `weekday()`, 0=Mon (mechanically asserted inside the script against a known calendar date). weekend: Sat/Sun.")
A("- trend/range regime: `btc_trend_state` (4h EMA200 ± ADX>25 selector; bull/bear/neutral, computed at entry — entry-time, not look-ahead). trend strength: btc_adx bucket <20 / 20-30 / >=30; symbol_trend_state available but not bucketed into cells.")
A("- volatility regime: entry ATR as % of price bucketed lo<0.15% / mid 0.15-0.40% / hi>=0.40%; realized-vol regime: entry_vol_z <2 / 2-5 / >=5.")
A("- spread/cost state: shadow-recorded spread_bps bucket <2 / 2-5 / >=5 bps (see §8 for applied costs).")
A("- pair: symbol as recorded; book: `strategy` (17 primary). Position-quality: aggression decile D1 / D2-3 / D4-9 / D10 (signal-time rank — entry-time).")
A("- Look-ahead control: NO forward columns used anywhere (fwd_atr_*, post_*, mae/mfe_*, run_*, pnl_1h/2h, bars_to_mfe_peak all excluded; verified by column whitelist in the loader).")

A("\n## 5. Segmented win-rate analysis (every declared combination)\n")
A(f"- Full cross **pair × book × regime** plus every single dimension and book×dim 2-way: **{len(full_rows)} segments**, each with n, net & gross WR, avg win (R), avg loss (R), expectancy (R), PF, ΣR, max losing streak, max drawdown (R), distinct days, top-day share, Wilson 95% CI, breakeven WR, p-value.")
A("- File: `research/win_combo_segments_full.csv` (all segments, including n<3). Ranked candidate file (n≥30): `research/win_combo_candidates.csv`.")

A("\n## 6. Low-sample flagging\n")
A(f"- Segments with **n<30 are flagged `LOW-N`** in the segments CSV ({n_low} of {len(full_rows)}) and are excluded from any 'almost certain' claim. Candidate floor: n≥30 AND ≥10 distinct days AND top-day share <40% of net.")

A("\n## 7. Statistical significance (per segment)\n")
A("- Wilson 95% CI on net win rate (per segment, in both CSVs).")
A("- Significance vs breakeven after costs: per-segment breakeven WR = avg_loss/(avg_win+avg_loss) computed on NET R; one-sided exact binomial p-value P(X≥wins | n, breakeven). Columns `wilson_lo/hi`, `breakeven_WR%`, `p_vs_be` in both files.")

A("\n## 8. Costs (estimated and applied)\n")
A("- Shadow fills are at next-bar open and `pnl_atr` is **gross** (verified: `signal_shadow.py` records spread_bps as a feature, deducts nothing). Applied per trade: taker 4bps/side ×2 + slippage 1bp/side ×2 = **10 bps round trip**; funding = side-signed entry funding × (hold_hours/8) — LONGs pay positive funding, SHORTs receive.")
A(f"- Mean cost per trade: {sum(t['cost_R'] for t in T)/N:.3f}R (book-dependent: 10-ATR books ≈ 0.02-0.05R, 4-ATR books ≈ 0.05-0.15R). Breakeven WRs above are NET of these costs. Sensitivity: ±4bps shifts thin-edge segments materially; funding impact ≤0.01R for ≤24h holds.")

A("\n## 9. Multiple-testing correction\n")
A(f"- Candidate cells tested (n≥30): **{len(cands)}** (1-way + 2-way + curated 3-way enumeration). Correction: **Benjamini–Hochberg** FDR on the per-cell exact binomial p-values vs net breakeven; BH q-values in candidates CSV (`BH_q`).")

A("\n## 10. Out-of-sample validation\n")
A("- Discovery window (IS): entries < **2026-08-01**; validation window (OOS): entries ≥ **2026-08-01** (disjoint, later period). Discovery gate: IS n≥20 AND IS Wilson-lower > IS breakeven. OOS gate: OOS n≥10 AND OOS E>0 AND OOS binomial p<0.05 vs OOS breakeven.")
A(f"- IS-discovered cells: **{n_is}/{len(cands)}**. IS∧OOS significant: **{n_is_oos}**. Fully validated (all gates): **{n_valid}**.")

A("\n## 11. Walk-forward / rolling-window stability\n")
A("- Three disjoint ~3-week windows (≤2026-07-19, 07-20→08-09, ≥08-10): a validated cell must print E_net>0 in ALL three (`WF1/WF2/WF3` columns in candidates CSV). With only ~9 weeks of data these windows are short — see §15.")

A("\n## 12. Final ranked table ('almost certain' candidates)\n")
A("- Full ranked table: `research/win_combo_candidates.csv` (top of file = validated first, then by OOS E). Required fields per entry: pair(s), session(s)/book, regime conditions, n, WR with Wilson CI, expectancy (R), PF, max losing streak, IS + OOS results — all present as columns.")
if n_valid:
    for c in [c for c in cands if c['validated']][:10]:
        s = c['s']
        A(f"- **VALIDATED**: {c['desc']} | n={s['n']} WR={100*s['wr']:.1f}% CI[{100*s['lo']:.1f},{100*s['hi']:.1f}] E={s['E']:+.3f}R PF={s['pf']:.2f} streak={s['streak']} OOS_E={(c['oos']['E'] if c['oos'] else 0):+.3f}")
else:
    A("- **No combination passes every gate — the honest result is that ZERO combos reach 'almost certain'.** Closest survivors and their exact failure gates are in the candidates CSV and §13.")

A("\n## 13. Rejected / not reliable (no cherry-picking)\n")
A("- Every ≥75% WR cell (n≥30) is a 1-2 calendar-day concentration: asia_burst_fade×bull 81.8% (1 day, top-day 100%); ny_flush×Sunday×bull 77.1% (1 day); nony_momentum×Monday×bull 73.2% (1 day); late_fade×Saturday 78.2% (top-day 91%); london_burst_fade×Thursday×bear (2 days, 94%); ny_flush×Tuesday×bull (2 Aug dates = 79% of net; live-window intersection rides ONE day, Aug 19).")
A("- follow_3h_all×h12-17 (72% WR, E +0.16R): July-only (Jul 1-13 ≈ all of +23.3R; Aug ≈ 0) — failed OOS/walk-forward. nony_momentum×bear 'ADX 20-30' conditioning is spurious (all bear trades are ADX 20-30; <20 bucket empty). asia NULL-era cells (writer gap Jul31-Aug07) are instrumentation artifacts, not regimes.")
A("- Cells whose R sits outside live-wired hours/books (e.g. ny_flush R concentrated h14/h20 vs live [16,17]; follow_3h_all, nony_momentum, setup_fade, london_burst_fade not wired live) are descriptive-only.")
A("- Funding-conditioning leg was already killed in the Aug 21 register read (sign-unstable across weeks); not re-litigated here.")

A("\n## 14. Overfitting risk\n")
A(f"- Tested {len(cands)} candidate cells (+{len(full_rows)} segment rows incl. n<30) on ~9 weeks of one market regime mix; {n_is} passed IS discovery, {n_is_oos} passed IS+OOS, **{n_valid} fully validated**. BH expected FDR ≤5% on surviving p-values. With ~0 survivors the data does not support ANY 'almost certain win' claim; the top-of-book cells are best read as forward-tracking candidates (G0), not tradable edges.")

A("\n## 15. Caveats & limitations\n")
A("- Shadow fills = next-bar open with estimated 1bp/side slippage; real cascade fills slip more (book_depth_usd_5bps recorded but not modeled). No queue/latency/partial-fill modeling.")
A("- 9 weeks, 3 walk-forward windows, single regime mix (neutral-heavy) — window count too small to prove stationarity; regime-conditional edges flip sign across episodes (house precedent: asia bear +0.14→−0.17R).")
A("- Most candidate cells live in shadow-only books (live wires asia_pump_short_4h, ny_flush_buy_4h, burst_follow); shadow-book expectancy ≠ live-arm expectancy. Past shadow performance does not guarantee future live wins.")

A("\n## 16. Reproducibility\n")
A("- Generator (this run, single execution): `/root/bitana/research/win_combo_deliverable.py` · scan: `win_combo_scan.py` · deep-dive: `win_combo_deepdive.py` · outputs: `WIN_COMBO_DELIVERABLE.md` (this file), `win_combo_segments_full.csv`, `win_combo_candidates.csv`. All under `/root/bitana/research/`.")
A(f"- Run timestamp: {RUN_TS} UTC. DB path + full column whitelist embedded in the script header.")

with open('/root/bitana/research/WIN_COMBO_DELIVERABLE.md', 'w') as f:
    f.write('\n'.join(L) + '\n')

print(f"RUN {RUN_TS} | rows={len(rows)} used={N} syms={len(syms)} segments={len(full_rows)} (LOW-N {n_low})")
print(f"candidates n>=30: {len(cands)} | IS-disc: {n_is} | IS+OOS: {n_is_oos} | VALIDATED: {n_valid}")
print(f"baseline: WR_net={100*wins_all/N:.1f}% CI[{100*lo_all:.1f},{100*hi_all:.1f}] E={E_all:+.4f}")
top = sorted(cands, key=lambda c: -c['s']['wr'])[:6]
for c in top:
    s = c['s']
    print(f"  topWR {c['desc'][:60]} n={s['n']} WR={100*s['wr']:.0f}% days={s['days']} E={s['E']:+.3f} IS={c['g_is']} OOS={c['g_oos']} WF={c['g_wf']} BH={c['g_bh']} VAL={c['validated']}")
best = sorted(cands, key=lambda c: (not c['validated'], -c['s']['E']))[:8]
for c in best:
    s = c['s']
    print(f"  bestE {c['desc'][:60]} n={s['n']} E={s['E']:+.3f} OOS_E={(c['oos']['E'] if c['oos'] else 0):+.3f} VAL={c['validated']}")
