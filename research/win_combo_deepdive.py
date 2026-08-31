#!/usr/bin/env python3
"""Deep-dive the cells that survived first-pass floors in win_combo_scan."""
import sqlite3
from datetime import datetime
from collections import defaultdict

con = sqlite3.connect('file:/root/bitana/storage/signal_shadow.db?mode=ro', uri=True)
cur = con.cursor()
cur.row_factory = sqlite3.Row

PRIMARY = {'asia_pump_short_4h','asia_burst_fade','london_burst_fade','ny_flush_buy_4h',
           'burst_follow','late_fade','nony_momentum','setup_fade','setup_follow',
           'follow_3h_all','fade_3h_asia','fade_3h_late','fade_3h_london',
           'ny_burst_follow','ny_burst_fade','v65_strict_long','v65_strict_ny_long'}

rows = cur.execute("""SELECT strategy, symbol, side, entry_time, exit_reason, pnl_atr, stop_atr,
       would_live_accept, hour, decile, btc_trend_state, v_confirms3, btc_adx, liq_imb
FROM shadow_trades WHERE status='closed'""").fetchall()

T = []
for r in rows:
    if r['strategy'] not in PRIMARY: continue
    if not r['stop_atr']: continue
    T.append(dict(strat=r['strategy'], sym=r['symbol'], side=r['side'],
                  R=r['pnl_atr']/r['stop_atr'], pnl=r['pnl_atr'],
                  dt=r['entry_time'], day=r['entry_time'][:10], mon=r['entry_time'][:7],
                  wd=datetime.fromisoformat(r['entry_time']).weekday(),
                  hour=r['hour'], dec=r['decile'], reg=r['btc_trend_state'] or 'NA',
                  v3=r['v_confirms3'], adx=r['btc_adx'], imb=r['liq_imb'],
                  exit=r['exit_reason'], wla=r['would_live_accept']))

def rep(name, mem, extra=""):
    if not mem:
        print(f"\n### {name}: EMPTY"); return
    n = len(mem); R = sum(m['R'] for m in mem)
    wr = 100*sum(1 for m in mem if m['R'] > 0)/n
    w = sum(m['R'] for m in mem if m['R'] > 0); l = -sum(m['R'] for m in mem if m['R'] < 0)
    pf = w/l if l else float('inf')
    wl = [m for m in mem if m['wla'] == 1]
    print(f"\n### {name} {extra}")
    print(f"n={n} sumR={R:+.1f} E={R/n:+.3f} WR={wr:.0f}% PF={pf:.2f} "
          f"WLA: n={len(wl)} WR={100*sum(1 for m in wl if m['R']>0)/len(wl) if wl else 0:.0f}%")
    dd = defaultdict(lambda: [0, 0.0])
    for m in mem: dd[m['day']][0] += 1; dd[m['day']][1] += m['R']
    print("dates (n, sumR):", ', '.join(f"{d}:{v[0]}|{v[1]:+.1f}" for d, v in sorted(dd.items())))
    sy = defaultdict(float)
    for m in mem: sy[m['sym']] += m['R']
    top3 = sorted(sy.items(), key=lambda kv: -abs(kv[1]))[:3]
    tot = sum(sy.values())
    print("sym mix:", ', '.join(f"{s}={v:+.1f}R ({100*v/tot:.0f}%)" for s, v in top3))
    ex = defaultdict(int)
    for m in mem: ex[m['exit']] += 1
    print("exits:", dict(sorted(ex.items(), key=lambda kv: -kv[1])))

# A: follow_3h_all hours 12-17
rep("A. follow_3h_all h12-17", [m for m in T if m['strat']=='follow_3h_all' and 12 <= m['hour'] <= 17])
rep("A2. ... h12-13 (london)", [m for m in T if m['strat']=='follow_3h_all' and m['hour'] in (12,13)])
rep("A3. ... h16-17 (ny)", [m for m in T if m['strat']=='follow_3h_all' and m['hour'] in (16,17)])

# B: asia Monday neutral
rep("B. asia_pump_short_4h Monday neutral", [m for m in T if m['strat']=='asia_pump_short_4h' and m['wd']==0 and m['reg']=='neutral'])
rep("B2. ... Monday neutral D2-3", [m for m in T if m['strat']=='asia_pump_short_4h' and m['wd']==0 and m['reg']=='neutral' and m['dec'] and 2 <= m['dec'] <= 3])
rep("B3. asia Monday ALL regimes", [m for m in T if m['strat']=='asia_pump_short_4h' and m['wd']==0])

# C: ny_flush Tuesday bull + live-window intersection
c = [m for m in T if m['strat']=='ny_flush_buy_4h' and m['wd']==2 and m['reg']=='bull']
rep("C. ny_flush_buy_4h Tuesday bull (all hours)", c)
c16 = [m for m in c if m['hour'] in (16,17)]
rep("C2. ... x live hours 16-17", c16)
hh = defaultdict(lambda: [0, 0.0])
for m in c: hh[m['hour']][0] += 1; hh[m['hour']][1] += m['R']
print("hour mix:", {h: (v[0], round(v[1],1)) for h, v in sorted(hh.items())})

# D: nony_momentum bear adx 20-30
rep("D. nony_momentum bear adx20-30", [m for m in T if m['strat']=='nony_momentum' and m['reg']=='bear' and m['adx'] and 20 <= m['adx'] < 30])
rep("D2. nony_momentum bear adx<20", [m for m in T if m['strat']=='nony_momentum' and m['reg']=='bear' and m['adx'] and m['adx'] < 20])
rep("D3. nony_momentum bear adx>=30", [m for m in T if m['strat']=='nony_momentum' and m['reg']=='bear' and m['adx'] and m['adx'] >= 30])

# E: LUNC v3=N
rep("E. 1000LUNC v_confirms3=N (all books)", [m for m in T if m['sym']=='1000LUNCUSDT' and not m['v3']])
bk = defaultdict(lambda: [0, 0.0])
for m in T:
    if m['sym']=='1000LUNCUSDT' and not m['v3']: bk[m['strat']][0] += 1; bk[m['strat']][1] += m['R']
print("book mix:", {k: (v[0], round(v[1],1)) for k, v in sorted(bk.items(), key=lambda kv: -kv[1][1])})

# F: setup_fade APT
rep("F. setup_fade APTUSDT", [m for m in T if m['strat']=='setup_fade' and m['sym']=='APTUSDT'])

# G: london_burst_fade D10 imb<0.5
rep("G. london_burst_fade D10 imb<0.5", [m for m in T if m['strat']=='london_burst_fade' and m['dec']==10 and m['imb'] is not None and m['imb'] < 0.5])
rep("G2. london_burst_fade D10 imb>=0.5", [m for m in T if m['strat']=='london_burst_fade' and m['dec']==10 and m['imb'] is not None and m['imb'] >= 0.5])
