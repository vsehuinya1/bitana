"""
Phase 2 Operational: State Machine + Single Exit Rule
No strategy changes. Observational only.

Three actionable classes:
  EXPLOSIVE  = convex runners (early expansion + acceleration)
  SURVIVOR   = slow grinders + delayed breakouts (survival edge, long hold)
  DEAD       = immediate rejection + flat chop + unspecified (kill candidates)

State machine per trade:
  IGNITION → EXPANSION → CONFIRMATION → [DECAY] → EXIT

Single rule to validate:
  MAE-recovery-based kill (only for DEAD class identification)
"""

import sqlite3
import math
import statistics
from collections import defaultdict

conn = sqlite3.connect("/root/bitana/storage/v5_forward_test.db")
conn.row_factory = sqlite3.Row
teleconn = sqlite3.connect("/root/bitana/storage/v6_telemetry.db")
teleconn.row_factory = sqlite3.Row

# Load all trades with r_path
trades = conn.execute("""
    SELECT trade_uuid, symbol, side, pnl_r, exit_reason, hold_candles, 
           aggression, decile, entry_price, exit_price
    FROM trades 
    WHERE exit_time IS NOT NULL
    ORDER BY entry_time
""").fetchall()
trades = [dict(t) for t in trades]

trade_data = {}
for t in trades:
    bars = teleconn.execute("""
        SELECT bar_index, price, unrealized_r, mae_so_far, mfe_so_far,
               vol_trail_level, atr, consecutive_red
        FROM r_path WHERE trade_uuid=? ORDER BY bar_index
    """, (t['trade_uuid'],)).fetchall()
    if bars:
        trade_data[t['trade_uuid']] = {
            'meta': t,
            'bars': [dict(b) for b in bars]
        }

print(f"Trades with r_path: {len(trade_data)}")

# ── STEP 1: Collapse 7 regimes → 3 actionable classes ────────────

def classify_actionable(bars, meta):
    """
    Collapse into 3 classes based on trajectory shape.
    
    EXPLOSIVE: MFE > 1.0R within 20 bars, positive acceleration
    SURVIVOR:  Winner with MFE < 1.0R OR winner with > 20 bars to 1R
    DEAD:      Loser (any type)
    
    For DEAD, subclassify by kill timing:
      EARLY_DEAD:   MFE never exceeds 0.3R in first 10 bars
      LATE_DEAD:    MFE > 0.3R at some point but still lost
      FADE_DEAD:    MFE > 0.5R then collapsed
    """
    is_winner = meta['pnl_r'] > 0
    max_mfe = max(b['mfe_so_far'] for b in bars)
    
    # Time to key thresholds
    t_10 = t_20 = t_30 = t_50 = None
    for b in bars:
        if t_10 is None and b['mfe_so_far'] >= 0.10: t_10 = b['bar_index']
        if t_20 is None and b['mfe_so_far'] >= 0.20: t_20 = b['bar_index']
        if t_30 is None and b['mfe_so_far'] >= 0.30: t_30 = b['bar_index']
        if t_50 is None and b['mfe_so_far'] >= 0.50: t_50 = b['bar_index']
    
    # MFE velocity in first 20 bars
    early_bars = [b for b in bars if b['bar_index'] <= 20]
    if len(early_bars) >= 2:
        mfe_vel_early = (early_bars[-1]['mfe_so_far'] - early_bars[0]['mfe_so_far']) / len(early_bars)
    else:
        mfe_vel_early = 0
    
    if is_winner:
        if max_mfe >= 1.0 and t_30 is not None and t_30 <= 15 and mfe_vel_early > 0.03:
            return 'EXPLOSIVE'
        else:
            return 'SURVIVOR'
    else:
        # Loser subclassification
        if max_mfe >= 0.50 and t_50 is not None and t_50 <= 10:
            return 'FADE_DEAD'
        elif t_30 is None or (t_30 is not None and t_30 > 20):
            return 'EARLY_DEAD'
        else:
            return 'LATE_DEAD'

classes = defaultdict(list)
for uuid, data in trade_data.items():
    cls = classify_actionable(data['bars'], data['meta'])
    classes[cls].append(uuid)

print("\n" + "=" * 70)
print("STEP 1: 3 ACTIONABLE CLASSES")
print("=" * 70)

for cls in ['EXPLOSIVE', 'SURVIVOR', 'EARLY_DEAD', 'LATE_DEAD', 'FADE_DEAD']:
    uuids = classes.get(cls, [])
    if not uuids:
        continue
    wr = sum(1 for u in uuids if trade_data[u]['meta']['pnl_r'] > 0) / len(uuids)
    avg_pnl = statistics.mean(trade_data[u]['meta']['pnl_r'] for u in uuids)
    avg_bars = statistics.mean(trade_data[u]['meta']['hold_candles'] for u in uuids)
    avg_mfe = statistics.mean(max(b['mfe_so_far'] for b in trade_data[u]['bars']) for u in uuids)
    
    reasons = defaultdict(int)
    for u in uuids:
        reasons[trade_data[u]['meta']['exit_reason']] += 1
    
    print(f"\n  {cls} ({len(uuids)} trades, WR={wr:.0%}, avg_pnl={avg_pnl:+.3f}R, avg_MFE={avg_mfe:.2f}R, avg_bars={avg_bars:.0f})")
    print(f"    Exit reasons: {dict(reasons)}")

# ── STEP 2: State Transition Logic ───────────────────────────────

print("\n\n" + "=" * 70)
print("STEP 2: STATE TRANSITION LOGIC")
print("=" * 70)

def compute_state(bar_idx, bars_so_far, meta):
    """
    State machine per bar.
    
    States:
      IGNITION:     bars 1-3. Too early to classify.
      EXPANSION:    MFE growing, velocity > threshold.
      CONFIRMATION: MFE > 0.3R and still expanding or holding.
      DECAY:        MFE flat/declining, MAE deepening.
      EXIT:         Terminal.
    
    Transitions:
      IGNITION → EXPANSION:   MFE velocity > 0.02 within first 3 bars
      IGNITION → DECAY:       MAE > 0.3R within first 3 bars
      EXPANSION → CONFIRMATION: MFE > 0.3R
      EXPANSION → DECAY:      MFE velocity < 0 for 3+ bars AND MAE > 0.3R
      CONFIRMATION → DECAY:   MFE declining for 5+ bars AND MAE > 0.4R
      DECAY → EXIT:           MAE > 0.6R AND MFE velocity < 0.01 for 3+ bars
    """
    if bar_idx <= 3:
        return 'IGNITION'
    
    recent = bars_so_far[-10:]  # last 10 bars
    mfe_vals = [b['mfe_so_far'] for b in recent]
    mae_vals = [abs(b['mae_so_far']) for b in recent]
    
    # MFE velocity (last 5 bars)
    if len(mfe_vals) >= 5:
        mfe_vel = (mfe_vals[-1] - mfe_vals[-5]) / 5
    else:
        mfe_vel = (mfe_vals[-1] - mfe_vals[0]) / max(len(mfe_vals), 1)
    
    current_mfe = mfe_vals[-1]
    current_mae = mae_vals[-1]
    
    # MFE trend (declining?)
    mfe_declining = all(mfe_vals[i] >= mfe_vals[i+1] for i in range(len(mfe_vals)-5, len(mfe_vals)-1)) if len(mfe_vals) >= 6 else False
    
    if current_mfe > 0.30 and mfe_vel > -0.01:
        return 'CONFIRMATION'
    elif mfe_vel > 0.02 and current_mae < 0.4:
        return 'EXPANSION'
    elif current_mae > 0.5 and mfe_vel < 0.01:
        return 'DECAY'
    elif mfe_declining and current_mae > 0.4:
        return 'DECAY'
    else:
        return 'EXPANSION'  # default for bars 4+ that don't meet other criteria

# Trace state transitions for each trade
print("\n  State transition frequencies (all trades):\n")

state_transitions = defaultdict(lambda: defaultdict(int))
state_durations = defaultdict(list)
exit_by_state = defaultdict(lambda: defaultdict(int))

for uuid, data in trade_data.items():
    bars = data['bars']
    meta = data['meta']
    
    prev_state = 'ENTRY'
    state_start = 0
    
    for i, bar in enumerate(bars):
        state = compute_state(bar['bar_index'], bars[:i+1], meta)
        
        if state != prev_state:
            state_transitions[prev_state][state] += 1
            state_durations[prev_state].append(bar['bar_index'] - state_start)
            prev_state = state
            state_start = bar['bar_index']
    
    # Final state at exit
    exit_by_state[prev_state][meta['exit_reason']] += 1

print(f"  {'From State':<15} {'To State':<15} {'Count':>6}")
print("  " + "-" * 40)
for from_s in ['ENTRY', 'IGNITION', 'EXPANSION', 'CONFIRMATION', 'DECAY']:
    for to_s, cnt in sorted(state_transitions[from_s].items(), key=lambda x: -x[1]):
        print(f"  {from_s:<15} {to_s:<15} {cnt:>6}")

print(f"\n  State durations (bars):")
for state in ['IGNITION', 'EXPANSION', 'CONFIRMATION', 'DECAY']:
    durs = state_durations.get(state, [])
    if durs:
        print(f"    {state}: median={statistics.median(durs):.0f} avg={statistics.mean(durs):.0f} n={len(durs)}")

print(f"\n  Exit reason by final state:")
for state in ['EXPANSION', 'CONFIRMATION', 'DECAY']:
    exits = exit_by_state.get(state, {})
    if exits:
        print(f"    {state}: {dict(exits)}")

# ── STEP 3: Single MAE-Recovery Exit Rule ────────────────────────

print("\n\n" + "=" * 70)
print("STEP 3: SINGLE EXIT RULE — MAE Recovery + Staleness")
print("=" * 70)

# Rule: Kill trade if ALL of:
#   1. MAE > 0.5R (deep drawdown)
#   2. MFE velocity < 0.01 for 3+ bars (no expansion)
#   3. MAE recovery < 0.3R (hasn't recovered meaningful ground)
#   4. Bar >= 5 (not just ignition noise)
#
# This targets: DEAD trades that bleed and don't recover
# This preserves: EXPLOSIVE (they expand) and SURVIVOR (they recover)

rule_results = {
    'true_kills': [], 'false_kills': [], 'missed': [], 'clean': []
}

for uuid, data in trade_data.items():
    bars = data['bars']
    meta = data['meta']
    cls = classify_actionable(bars, meta)
    
    kill_bar = None
    consec_stale = 0
    kill_reason = None
    
    for i, bar in enumerate(bars):
        if bar['bar_index'] < 5:
            continue
        
        mae = abs(bar['mae_so_far'])
        mfe = bar['mfe_so_far']
        unreal = bar['unrealized_r']
        recovery = unreal - bar['mae_so_far']  # how much recovered from worst
        
        # MFE velocity (last 3 bars)
        if i >= 2:
            mfe_vel = (bars[i]['mfe_so_far'] - bars[i-2]['mfe_so_far']) / 3
        else:
            mfe_vel = 0
        
        if mae > 0.5 and mfe_vel < 0.01:
            consec_stale += 1
        else:
            consec_stale = 0
        
        if mae > 0.5 and consec_stale >= 3 and recovery < 0.3:
            kill_bar = bar['bar_index']
            kill_reason = f"mae={mae:.2f} vel={mfe_vel:.4f} rec={recovery:.2f}"
            break
    
    result = {
        'uuid': uuid, 'symbol': meta['symbol'], 'pnl_r': meta['pnl_r'],
        'cls': cls, 'kill_bar': kill_bar, 'kill_reason': kill_reason,
        'exit_reason': meta['exit_reason'], 'hold_candles': meta['hold_candles']
    }
    
    if meta['pnl_r'] > 0:
        if kill_bar is not None:
            rule_results['false_kills'].append(result)
        else:
            rule_results['clean'].append(result)
    else:
        if kill_bar is not None:
            rule_results['true_kills'].append(result)
        else:
            rule_results['missed'].append(result)

total_w = len(rule_results['false_kills']) + len(rule_results['clean'])
total_l = len(rule_results['true_kills']) + len(rule_results['missed'])

print(f"\n  Rule: MAE > 0.5R + MFE vel < 0.01 for 3+ bars + recovery < 0.3R, after bar 5")
print(f"\n  True kills (losers caught):  {len(rule_results['true_kills'])}/{total_l} ({len(rule_results['true_kills'])/max(total_l,1)*100:.0f}%)")
print(f"  False kills (winners killed): {len(rule_results['false_kills'])}/{total_w} ({len(rule_results['false_kills'])/max(total_w,1)*100:.0f}%)")
print(f"  Missed losers:               {len(rule_results['missed'])}/{total_l}")
print(f"  Clean winners:               {len(rule_results['clean'])}/{total_w}")

# By class
print(f"\n  Breakdown by actionable class:")
for cls in ['EXPLOSIVE', 'SURVIVOR', 'EARLY_DEAD', 'LATE_DEAD', 'FADE_DEAD']:
    cls_all = [r for r in rule_results['true_kills'] + rule_results['false_kills'] + rule_results['missed'] + rule_results['clean'] if r['cls'] == cls]
    cls_tk = [r for r in rule_results['true_kills'] if r['cls'] == cls]
    cls_fk = [r for r in rule_results['false_kills'] if r['cls'] == cls]
    cls_ms = [r for r in rule_results['missed'] if r['cls'] == cls]
    cls_cl = [r for r in rule_results['clean'] if r['cls'] == cls]
    
    if cls_all:
        print(f"    {cls:<15} total={len(cls_all):>3}  TK={len(cls_tk):>3} FK={len(cls_fk):>3} MS={len(cls_ms):>3} CL={len(cls_cl):>3}")

# False kill detail
if rule_results['false_kills']:
    print(f"\n  FALSE KILLS (winners destroyed):")
    for fk in sorted(rule_results['false_kills'], key=lambda x: -x['pnl_r']):
        print(f"    {fk['symbol']:<10} pnl={fk['pnl_r']:+.3f}R cls={fk['cls']:<12} killed@bar{fk['kill_bar']:>3}/{fk['hold_candles']:<4} was:{fk['exit_reason']}")

# Missed loser detail  
if rule_results['missed']:
    print(f"\n  MISSED LOSERS (rule never triggered) — sample:")
    for ml in sorted(rule_results['missed'], key=lambda x: x['pnl_r'])[:10]:
        print(f"    {ml['symbol']:<10} pnl={ml['pnl_r']:+.3f}R cls={ml['cls']:<12} hold={ml['hold_candles']:>4} was:{ml['exit_reason']}")

# ── STEP 4: Expectancy Impact ─────────────────────────────────────

print("\n\n" + "=" * 70)
print("STEP 4: EXPECTANCY IMPACT (simulated)")
print("=" * 70)

# Original expectancy
all_pnls = [trade_data[u]['meta']['pnl_r'] for u in trade_data]
orig_wr = sum(1 for p in all_pnls if p > 0) / len(all_pnls)
orig_avg_win = statistics.mean([p for p in all_pnls if p > 0])
orig_avg_loss = statistics.mean([p for p in all_pnls if p <= 0])
orig_exp = orig_wr * orig_avg_win + (1 - orig_wr) * orig_avg_loss
orig_total = sum(all_pnls)

# With rule: killed trades exit at -0.5R (partial loss, not full stop)
KILL_PNL = -0.5

sim_pnls = []
for uuid in trade_data:
    meta = trade_data[uuid]['meta']
    pnl = meta['pnl_r']
    
    # Check if this trade would be killed
    killed = False
    consec_stale = 0
    for i, bar in enumerate(trade_data[uuid]['bars']):
        if bar['bar_index'] < 5:
            continue
        mae = abs(bar['mae_so_far'])
        if i >= 2:
            mfe_vel = (trade_data[uuid]['bars'][i]['mfe_so_far'] - trade_data[uuid]['bars'][i-2]['mfe_so_far']) / 3
        else:
            mfe_vel = 0
        recovery = bar['unrealized_r'] - bar['mae_so_far']
        
        if mae > 0.5 and mfe_vel < 0.01:
            consec_stale += 1
        else:
            consec_stale = 0
        
        if mae > 0.5 and consec_stale >= 3 and recovery < 0.3:
            sim_pnls.append(KILL_PNL)
            killed = True
            break
    
    if not killed:
        sim_pnls.append(pnl)

sim_wr = sum(1 for p in sim_pnls if p > 0) / len(sim_pnls)
sim_avg_win = statistics.mean([p for p in sim_pnls if p > 0])
sim_avg_loss = statistics.mean([p for p in sim_pnls if p <= 0])
sim_exp = sim_wr * sim_avg_win + (1 - sim_wr) * sim_avg_loss
sim_total = sum(sim_pnls)

print(f"\n  {'Metric':<25} {'Original':>12} {'With Rule':>12} {'Delta':>10}")
print("  " + "-" * 62)
print(f"  {'Win Rate':<25} {orig_wr:>11.1%} {sim_wr:>11.1%} {sim_wr-orig_wr:>+9.1%}")
print(f"  {'Avg Win (R)':<25} {orig_avg_win:>12.3f} {sim_avg_win:>12.3f} {sim_avg_win-orig_avg_win:>+10.3f}")
print(f"  {'Avg Loss (R)':<25} {orig_avg_loss:>12.3f} {sim_avg_loss:>12.3f} {sim_avg_loss-orig_avg_loss:>+10.3f}")
print(f"  {'Expectancy/trade (R)':<25} {orig_exp:>12.4f} {sim_exp:>12.4f} {sim_exp-orig_exp:>+10.4f}")
print(f"  {'Total R (all trades)':<25} {orig_total:>12.2f} {sim_total:>12.2f} {sim_total-orig_total:>+10.2f}")
print(f"  {'Trades killed':<25} {'—':>12} {len(rule_results['true_kills'])+len(rule_results['false_kills']):>12} {'—':>10}")

# Compounding @ 0.5% risk
eq_orig = 100.0
for p in all_pnls:
    eq_orig *= (1 + 0.005 * p)

eq_sim = 100.0
for p in sim_pnls:
    eq_sim *= (1 + 0.005 * p)

print(f"\n  Compounding @ 0.5% risk:")
print(f"    Original: $100 → ${eq_orig:.2f} ({((eq_orig/100)-1)*100:+.1f}%)")
print(f"    With rule: $100 → ${eq_sim:.2f} ({((eq_sim/100)-1)*100:+.1f}%)")

# ── STEP 5: Forward-Test Specification ───────────────────────────

print("\n\n" + "=" * 70)
print("STEP 5: FORWARD-TEST SPECIFICATION")
print("=" * 70)

print(f"""
  RULE: MAE-Recovery Kill
  ─────────────────────
  IF bar >= 5
    AND MAE > 0.5R
    AND MFE velocity < 0.01 for 3+ consecutive bars
    AND MAE recovery < 0.3R
  THEN kill at market (est. -0.5R)
  
  HARD CONSTRAINTS:
    - Never fires before bar 5
    - Never fires if MFE is still expanding (vel >= 0.01)
    - Never fires if trade has recovered > 0.3R from trough
    - Only fires after 3 consecutive stale bars (not 1-bar spike)
  
  EXPECTED (in-sample):
    Catches {len(rule_results['true_kills'])}/{total_l} losers ({len(rule_results['true_kills'])/max(total_l,1)*100:.0f}%)
    Kills {len(rule_results['false_kills'])}/{total_w} winners ({len(rule_results['false_kills'])/max(total_w,1)*100:.0f}%)
    Net expectancy: {orig_exp:+.4f}R → {sim_exp:+.4f}R ({sim_exp-orig_exp:+.4f}R)
  
  FORWARD TEST:
    - Run on next 30-50 live trades
    - Measure: does actual expectancy increase?
    - Kill threshold: if rule kills > 25% of winners in forward sample, abort
    - Success: if forward expectancy > 0 AND WR > 45%
  
  KNOWN RISKS:
    - False kills include SURVIVOR class (slow grinders that dip deep then recover)
    - EARLY_DEAD class may not always reach MAE > 0.5R before stop_loss
    - FADE_DEAD class may trigger too late (after most of the loss already taken)
    - Sample size: 151 trades. Forward test needs 30+ for statistical validity.
""")

conn.close()
teleconn.close()
