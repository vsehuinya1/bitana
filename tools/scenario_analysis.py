import sqlite3, math
from datetime import datetime

conn = sqlite3.connect("storage/v5_forward_test.db")
conn.row_factory = sqlite3.Row

trades = conn.execute("""
    SELECT id, symbol, side, entry_price, exit_price, pnl_r, pnl_usd,
           exit_reason, entry_time, exit_time, hold_candles,
           mae, mfe, aggression, decile, confirmations, equity_after,
           stop_dist
    FROM trades 
    WHERE exit_time IS NOT NULL
      AND entry_time >= '2026-05-27'
    ORDER BY exit_time
""").fetchall()

trades = [dict(t) for t in trades]

# Convert MAE/MFE from USD to R-multiple
for t in trades:
    if t['stop_dist'] and t['stop_dist'] > 0:
        if t['side'] == 'LONG':
            risk_price = abs(t['entry_price'] - t['stop_dist'])
        else:
            risk_price = abs(t['stop_dist'] - t['entry_price'])
        t['mfe_r'] = t['mfe'] / risk_price if risk_price > 0 else 0
        t['mae_r'] = t['mae'] / risk_price if risk_price > 0 else 0
    else:
        t['mfe_r'] = 0
        t['mae_r'] = 0

winners = [t for t in trades if t['pnl_r'] > 0]
losers = [t for t in trades if t['pnl_r'] <= 0]
n = len(trades)
wr = len(winners) / n

print("=" * 80)
print(f"POST-GATE DATASET: {n} closed trades | {len(winners)}W / {len(losers)}L | WR {wr:.1%}")
print(f"Total PnL: {sum(t['pnl_r'] for t in trades):.3f}R | "
      f"Winners: +{sum(t['pnl_r'] for t in winners):.3f}R | "
      f"Losers: {sum(t['pnl_r'] for t in losers):.3f}R")
print("=" * 80)

# ── SECTION 1: ACTUAL PERFORMANCE WITH COMPOUNDING ──
print("\n━━━ SECTION 1: Actual Compounding Performance (Start $100) ━━━\n")

for risk_pct in [0.005, 0.01, 0.02]:
    equity = 100.0
    peak = equity
    max_dd = 0
    for t in trades:
        pnl_usd = equity * risk_pct * t['pnl_r']
        equity += pnl_usd
        peak = max(peak, equity)
        dd = (peak - equity) / peak
        max_dd = max(max_dd, dd)
    total_r = sum(t['pnl_r'] for t in trades)
    print(f"  Risk {risk_pct:.1%}/trade:  $100 → ${equity:.2f} | ROI {((equity/100)-1)*100:+.1f}% | "
          f"Max DD: {max_dd:.1%} | Total R: {total_r:.3f}")

# ── SECTION 2: WHAT IF WE CUT LOSERS EARLY ──
print("\n━━━ SECTION 2: Cut Losers Early Scenarios ━━━\n")

avg_win = sum(t['pnl_r'] for t in winners) / len(winners) if winners else 0
avg_loss = sum(t['pnl_r'] for t in losers) / len(losers) if losers else 0

print(f"  Avg winner: +{avg_win:.3f}R | Avg loser: {avg_loss:.3f}R")
print(f"  Losers that went positive at some point: "
      f"{len([t for t in losers if t['mfe_r'] > 0.1])}/{len(losers)}")
print(f"  Losers with MFE > 0.3R: {len([t for t in losers if t['mfe_r'] > 0.3])}/{len(losers)}")
print(f"  Losers with MFE > 0.5R: {len([t for t in losers if t['mfe_r'] > 0.5])}/{len(losers)}")

# Scenario: cut at various R levels
cut_levels = [0, -0.25, -0.5, -0.75]
for cut_r in cut_levels:
    total_r_adj = sum(t['pnl_r'] for t in winners) + sum(max(t['pnl_r'], cut_r) for t in losers)
    
    for risk_pct in [0.005, 0.01]:
        equity = 100.0
        peak = equity
        max_dd = 0
        for t in trades:
            actual_r = t['pnl_r']
            if actual_r > 0:
                sim_r = actual_r
            else:
                sim_r = max(actual_r, cut_r)
            pnl_usd = equity * risk_pct * sim_r
            equity += pnl_usd
            peak = max(peak, equity)
            dd = (peak - equity) / peak
            max_dd = max(max_dd, dd)
        
        print(f"  Cut@ {cut_r:>5.2f}R | Risk {risk_pct:.1%}: "
              f"$100 → ${equity:.2f} ({((equity/100)-1)*100:+.1f}%) | "
              f"TotalR {total_r_adj:+.3f} | MaxDD {max_dd:.1%}")

# Scenario: MFE-based exits (what trajectory looks like)
print("\n━━━ SECTION 3: Loser Trajectory — How They Die ━━━\n")
print(f"  {'Sym':<8} {'MFE(R)':>7} {'MAE(R)':>7} {'Final(R)':>9} {'Hold':>5} {'Reason':<15} {'Death Profile'}")
print("  " + "-" * 85)
for t in sorted(losers, key=lambda x: x['mfe_r'], reverse=True):
    if t['mfe_r'] > 0.5:
        profile = "went positive DIED"
    elif t['mfe_r'] > 0.2:
        profile = "briefly positive, reversed"
    elif t['mfe_r'] > 0:
        profile = "never positive, bled out"
    else:
        profile = "immediate drop, no bounce"
    print(f"  {t['symbol']:<8} {t['mfe_r']:>7.2f} {t['mae_r']:>7.2f} {t['pnl_r']:>9.3f} "
          f"{t['hold_candles']:>5}c {t['exit_reason']:<15} {profile}")

print("\n━━━ SECTION 4: Winner vs Loser — The Real Separator ━━━\n")
print(f"  {'Metric':<25} {'Winners':>10} {'Losers':>10} {'Delta':>10} {'d':>6}")
print("  " + "-" * 65)

def cohen_d(g1, g2):
    n1, n2 = len(g1), len(g2)
    if n1 < 2 or n2 < 2:
        return 0
    v1 = sum((x - sum(g1)/n1)**2 for x in g1) / (n1-1)
    v2 = sum((x - sum(g2)/n2)**2 for x in g2) / (n2-1)
    return (sum(g1)/n1 - sum(g2)/n2) / math.sqrt((v1+v2)/2) if (v1+v2) > 0 else 0

metrics = [
    ("Max MFE (R)", [t['mfe_r'] for t in winners], [t['mfe_r'] for t in losers]),
    ("Max MAE (R)", [t['mae_r'] for t in winners], [t['mae_r'] for t in losers]),
    ("PnL (R)", [t['pnl_r'] for t in winners], [t['pnl_r'] for t in losers]),
    ("Hold (candles)", [t['hold_candles'] for t in winners], [t['hold_candles'] for t in losers]),
    ("Aggression", [t['aggression'] for t in winners], [t['aggression'] for t in losers]),
    ("Decile", [t['decile'] for t in winners], [t['decile'] for t in losers]),
    ("Exit price/Entry", [t['exit_price']/t['entry_price'] for t in winners if t['entry_price']>0],
     [t['exit_price']/t['entry_price'] for t in losers if t['entry_price']>0]),
]

for name, wvals, lvals in metrics:
    wm = sum(wvals)/len(wvals) if wvals else 0
    lm = sum(lvals)/len(lvals) if lvals else 0
    d = cohen_d(wvals, lvals)
    star = "***" if abs(d) > 1.0 else "**" if abs(d) > 0.7 else "*" if abs(d) > 0.5 else ""
    print(f"  {name:<25} {wm:>10.3f} {lm:>10.3f} {wm-lm:>+10.3f} {d:>6.2f} {star}")

print("\n━━━ SECTION 5: How to Cut Losers — Feasibility ━━━\n")

# Analyze: what % of losers never went above 0R?
never_positive = [t for t in losers if t['mfe_r'] <= 0.05]
print(f"  Losers that NEVER went positive (MFE<0.05R): {len(never_positive)}/{len(losers)} ({len(never_positive)/len(losers):.0%})")
print(f"    → These CANNOT be saved by early exit. They're dead on arrival.")
print()

# Losers that went positive but then died
went_positive_died = [t for t in losers if t['mfe_r'] > 0.2]
print(f"  Losers that went >0.2R positive then died: {len(went_positive_died)}/{len(losers)} ({len(went_positive_died)/len(losers):.0%})")
for t in went_positive_died:
    print(f"    {t['symbol']:<8} MFE={t['mfe_r']:.2f}R → final {t['pnl_r']:.3f}R | "
          f"Peak-to-trough: {t['mfe_r']+abs(t['mae_r']):.2f}R range | Hold: {t['hold_candles']}c")

# What if we trail at +0.3R?
print(f"\n  ── Trailing stop at +0.3R scenario ──")
trail_level = 0.3
trail_total_r = 0
trail_w, trail_l = 0, 0
for t in trades:
    actual_r = t['pnl_r']
    if actual_r > 0:
        sim_r = actual_r  # winner, keep as-is
        trail_w += 1
    else:
        # Loser: if it hit +0.3R, exit at +0.3R instead of actual
        if t['mfe_r'] >= trail_level:
            sim_r = trail_level
            trail_w += 1  # becomes a winner
        else:
            sim_r = actual_r  # still a loser
            trail_l += 1
    trail_total_r += sim_r

print(f"    +0.3R trail converts {len(went_positive_died)} losers to +{trail_level}R winners")
print(f"    New: {trail_w}W / {trail_l}L = {trail_w}/{trail_w+trail_l} ({trail_w/(trail_w+trail_l):.0%} WR)")
print(f"    Total R: {trail_total_r:+.3f}R (vs actual {sum(t['pnl_r'] for t in trades):.3f}R)")
equity_trail = 100.0
for t in trades:
    actual_r = t['pnl_r']
    if actual_r > 0:
        sim_r = actual_r
    else:
        sim_r = trail_level if t['mfe_r'] >= trail_level else actual_r
    equity_trail *= (1 + 0.01 * sim_r)
print(f"    $100 → ${equity_trail:.2f} at 1% risk | $100 → ${100*(1+0.005*trail_total_r):.2f} at 0.5% risk (approx)")

# What if we trail at +0.2R?
print(f"\n  ── Trailing stop at +0.2R scenario ──")
trail_level = 0.2
trail2_hits = [t for t in losers if t['mfe_r'] >= trail_level]
trail2_total_r = sum(t['pnl_r'] for t in winners) + sum(trail_level for t in trail2_hits) + \
                 sum(t['pnl_r'] for t in losers if t['mfe_r'] < trail_level)
print(f"    Converts {len(trail2_hits)} losers to +{trail_level}R")
print(f"    Total R: {trail2_total_r:+.3f}R")

conn.close()
