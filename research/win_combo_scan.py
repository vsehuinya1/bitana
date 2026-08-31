#!/usr/bin/env python3
"""Winning-combination scan over shadow_trades (historical read, full closed set).

Entry-time features ONLY (no fwd_*, post_*, mae/mfe_*, run_*, pnl_1h/2h, bars_to_mfe_peak).
Canonical R = pnl_atr / stop_atr. Primary books only (variant families excluded).
"""
import sqlite3, itertools, csv, statistics as st
from datetime import datetime
from collections import defaultdict

DB = 'file:/root/bitana/storage/signal_shadow.db?mode=ro'
con = sqlite3.connect(DB, uri=True)
cur = con.cursor()
cur.row_factory = sqlite3.Row

# mechanical weekday assertion (python weekday, 0=Mon): 2026-08-31 is a Monday
assert datetime(2026, 8, 31).weekday() == 0, "weekday convention broken"

# ---------- load ----------
rows = cur.execute("""
SELECT strategy, symbol, side, entry_time, exit_reason, pnl_atr, stop_atr, tp_atr,
       would_live_accept, session, hour, decile, is_weekend,
       btc_trend_state, symbol_trend_state, btc_adx, funding_rate_btc, funding_rate_symbol,
       entry_vol_z, entry_atr_pct, entry_impulse_pct, entry_cascade_strength,
       liq_imb, burst_vol_zscore, v_confirms3, v_strict, cascade_active, trigger,
       cluster_breadth, market_breadth_pct, spread_bps, concurrent_positions_total,
       oi_delta_30m_pct
FROM shadow_trades WHERE status='closed'
""").fetchall()

def q(v):
    if v is None: return None
    try: return float(v)
    except Exception: return None

# stop_atr integrity
stop_by = defaultdict(list)
for r in rows:
    s = q(r['stop_atr'])
    if s: stop_by[r['strategy']].append(s)

# variant-family exclusion: ONE canonical book per family (live-wired or parent),
# stop-variants (_s4/_s6/_s8/_tsl/_limit15/_open/_scalein) and hold-variants (_1h/_2h/_8h/_24h)
# re-count the same entries -> excluded. Session splits excluded where a parent (_all) exists.
PRIMARY = {
    'asia_pump_short_4h',   # live asia book
    'asia_burst_fade', 'london_burst_fade',
    'ny_flush_buy_4h',      # live ny book
    'burst_follow',         # live london book
    'late_fade', 'nony_momentum', 'setup_fade', 'setup_follow',
    'follow_3h_all',        # parent of follow_3h_{asia,late,london,ny}; 6h/1h/2h/tsl excluded
    'fade_3h_asia', 'fade_3h_late', 'fade_3h_london',  # 6h variants excluded
    'ny_burst_follow', 'ny_burst_fade',                # _short excluded (side subset)
    'v65_strict_long', 'v65_strict_ny_long',           # new Aug23+ books, tiny n
}
books = sorted({r['strategy'] for r in rows})
prim = [b for b in books if b in PRIMARY]
print("== books (primary kept) ==")
for b in books:
    n = sum(1 for r in rows if r['strategy'] == b)
    ss = stop_by[b]
    print(f"  {b:28s} n={n:6d} stop_atr med={st.median(ss) if ss else None} "
          f"{'PRIMARY' if b in PRIMARY else 'variant-EXCLUDED'}")

# ---------- features (entry-time only) ----------
def tercile(v, edges):
    if v is None: return 'NA'
    return 'lo' if v < edges[0] else ('mid' if v < edges[1] else 'hi')

def bucket(v, cuts, labels):
    if v is None: return 'NA'
    for c, l in zip(cuts, labels):
        if v < c: return l
    return labels[-1]

data = []
for r in rows:
    if r['strategy'] not in PRIMARY: continue
    s_atr = q(r['stop_atr']); pa = q(r['pnl_atr'])
    if not s_atr or pa is None: continue
    d = dict(strategy=r['strategy'], symbol=r['symbol'], side=r['side'],
             exit_reason=r['exit_reason'], pnl=pa, R=pa / s_atr,
             wla=r['would_live_accept'],
             dt=r['entry_time'], month=r['entry_time'][:7],
             day=r['entry_time'][:10],
             wd=datetime.fromisoformat(r['entry_time']).weekday(),
             hour=r['hour'] if r['hour'] is not None else datetime.fromisoformat(r['entry_time']).hour,
             regime=(r['btc_trend_state'] or 'NA'),
             sym_state=(r['symbol_trend_state'] or 'NA'),
             decile_band=bucket(r['decile'], [1, 3, 9], ['D1', 'D2-3', 'D4-9', 'D10']) if r['decile'] is not None else 'NA',
             weekend=bool(r['is_weekend']),
             adx=bucket(q(r['btc_adx']), [20, 30], ['<20', '20-30', '>=30']),
             volz=bucket(q(r['entry_vol_z']), [2, 5], ['<2', '2-5', '>=5']),
             imb=bucket(q(r['liq_imb']), [0.5], ['<0.5', '>=0.5']),
             breadth=bucket(r['cluster_breadth'], [2, 4], ['1', '2-3', '>=4']),
             mktb=bucket(q(r['market_breadth_pct']), [30, 60], ['<30', '30-60', '>=60']),
             spread=bucket(q(r['spread_bps']), [2, 5], ['<2', '2-5', '>=5']),
             conc=bucket(r['concurrent_positions_total'], [2, 4, 6], ['0-1', '2-3', '4-5', '>=6']),
             oid=bucket(q(r['oi_delta_30m_pct']), [-0.5, 0.5], ['<-0.5', 'mid', '>0.5']),
             v3='Y' if r['v_confirms3'] else 'N',
             vs='Y' if r['v_strict'] else 'N',
             casc='Y' if r['cascade_active'] else 'N',
             trig=(r['trigger'] or 'NA'),
             )
    # fixed-cut buckets (NULL-safe, quantile-free)
    a = q(r['entry_atr_pct']);  d['atrp'] = 'NA' if a is None else ('lo' if a < 0.15 else ('mid' if a < 0.4 else 'hi'))
    f = q(r['funding_rate_symbol']); d['fund'] = 'NA' if f is None else ('neg' if f < 0 else ('flat' if f < 0.0001 else 'pos'))
    data.append(d)

print(f"\nscanned rows (primary, closed, R-valid): {len(data)}")

CATS = ['strategy', 'regime', 'side', 'sym_state', 'weekday', 'hour', 'hour_band',
        'decile_band', 'symbol', 'adx', 'volz', 'atrp', 'imb', 'breadth', 'mktb',
        'spread', 'conc', 'oid', 'fund', 'v3', 'vs', 'casc', 'trig', 'weekend', 'session']
for d in data:
    d['weekday'] = d['wd']
    h = d['hour']
    d['hour_band'] = '0-5' if h <= 5 else ('6-11' if h <= 11 else ('12-17' if h <= 17 else '18-23'))

# ---------- cell stats ----------
def evaluate(members):
    n = len(members)
    R = [m['R'] for m in members]
    wr = sum(1 for x in R if x > 0) / n
    E = sum(R) / n
    w = sum(x for x in R if x > 0); l = -sum(x for x in R if x < 0)
    pf = w / l if l else float('inf')
    days = {m['day'] for m in members}
    dayR = defaultdict(float)
    symR = defaultdict(float)
    for m in members:
        dayR[m['day']] += m['R']; symR[m['symbol']] += m['R']
    top_day = max(dayR.values()) / sum(R) if sum(R) > 0 else float('nan')
    top_sym = max(symR.values()) / sum(R) if sum(R) > 0 else float('nan')
    jul = [m['R'] for m in members if m['month'] == '2026-07']
    aug = [m['R'] for m in members if m['month'] == '2026-08']
    tp_share = sum(1 for m in members if m['exit_reason'] == 'tp') / n
    wl = [m for m in members if m['wla'] == 1]
    return dict(n=n, WR=wr, E=E, PF=pf, sumR=sum(R), days=len(days),
                top_day=top_day, top_sym=top_sym,
                jul_n=len(jul), jul_E=(sum(jul) / len(jul) if jul else None),
                aug_n=len(aug), aug_E=(sum(aug) / len(aug) if aug else None),
                tp_share=tp_share, wla_n=len(wl),
                wla_WR=(sum(1 for m in wl if m['R'] > 0) / len(wl) if wl else None))

# ---------- enumerate ----------
cells = {}
def add_cell(desc, keys):
    groups = defaultdict(list)
    for m in data:
        k = tuple(m.get(kk) for kk in keys)
        groups[k].append(m)
    for k, mem in groups.items():
        if len(mem) < 30: continue
        desc_full = desc + '=' + '|'.join(str(x) for x in k)
        cells[desc_full] = evaluate(mem)

# 1-way
for c in CATS:
    add_cell(c, [c])
# 2-way (curated core dims x everything)
core = ['strategy', 'regime', 'side', 'weekday', 'hour_band', 'decile_band', 'weekend']
for a, b in itertools.combinations(sorted(set(core + ['adx', 'volz', 'atrp', 'imb', 'breadth',
        'mktb', 'spread', 'conc', 'oid', 'fund', 'v3', 'vs', 'casc', 'sym_state', 'session', 'symbol'])), 2):
    add_cell(f'{a}x{b}', [a, b])
# 3-way curated
for keys in [('strategy', 'regime', 'decile_band'), ('strategy', 'weekday', 'hour_band'),
             ('strategy', 'regime', 'adx'), ('strategy', 'decile_band', 'imb'),
             ('strategy', 'side', 'regime'), ('strategy', 'regime', 'breadth'),
             ('strategy', 'weekday', 'regime'), ('strategy', 'regime', 'trig')]:
    add_cell('x'.join(keys), list(keys))

print(f"cells with n>=30: {len(cells)}")

# ---------- rank & write ----------
def robust(c):
    return (c['jul_E'] is not None and c['aug_E'] is not None and c['jul_E'] > 0 and c['aug_E'] > 0
            and c['days'] >= 10 and (c['top_day'] != c['top_day'] or c['top_day'] < 0.4))

hdr = ['cell', 'n', 'WR%', 'E_R', 'PF', 'sumR', 'days', 'top_day_share', 'top_sym_share',
       'jul_n', 'jul_E', 'aug_n', 'aug_E', 'tp_share', 'wla_n', 'wla_WR%', 'ROBUST']
def row(desc, c):
    return [desc, c['n'], round(100 * c['WR'], 1), round(c['E'], 4), round(c['PF'], 2),
            round(c['sumR'], 1), c['days'],
            round(c['top_day'], 2) if c['top_day'] == c['top_day'] else 'inf',
            round(c['top_sym'], 2) if c['top_sym'] == c['top_sym'] else 'inf',
            c['jul_n'], round(c['jul_E'], 3) if c['jul_E'] is not None else '',
            c['aug_n'], round(c['aug_E'], 3) if c['aug_E'] is not None else '',
            round(c['tp_share'], 2), c['wla_n'],
            round(100 * c['wla_WR'], 1) if c['wla_WR'] is not None else '',
            'Y' if robust(c) else '']

with open('/root/bitana/research/win_combo_scan_results.csv', 'w', newline='') as f:
    w = csv.writer(f); w.writerow(hdr)
    for desc, c in sorted(cells.items(), key=lambda kv: -kv[1]['WR']):
        w.writerow(row(desc, c))

print("\n== TOP 25 by WR (n>=30) ==")
for desc, c in sorted(cells.items(), key=lambda kv: -kv[1]['WR'])[:25]:
    print(' | '.join(str(x) for x in row(desc, c)))
print("\n== TOP 15 by E (n>=50) ==")
for desc, c in sorted([kv for kv in cells.items() if kv[1]['n'] >= 50], key=lambda kv: -kv[1]['E'])[:15]:
    print(' | '.join(str(x) for x in row(desc, c)))
print("\n== ROBUST cells (Jul>0 & Aug>0, days>=10, top_day<40%), n>=30 ==")
rob = sorted([kv for kv in cells.items() if robust(kv[1]) and kv[1]['n'] >= 30],
             key=lambda kv: -kv[1]['E'])
print(f"count: {len(rob)}")
for desc, c in rob[:30]:
    print(' | '.join(str(x) for x in row(desc, c)))
