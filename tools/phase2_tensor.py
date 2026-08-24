"""
Phase 2 — Trade Evolution Tensor + Trajectory Clustering
Purely observational. No strategy changes.

Input: r_path table (per-bar MFE/MAE/unrealized_r/vol_trail)
Output:
  A. Trade evolution tensor per trade (hold_bars x 7 features)
  B. Trajectory clustering → identify latent regimes
  C. MAE persistence separation test
  D. Time-to-first-expansion distribution
  E. Post-hoc regime signatures
"""

import sqlite3
import math
import statistics
from collections import defaultdict

# ── DATA LOADING ──────────────────────────────────────────────────

conn = sqlite3.connect("/root/bitana/storage/v5_forward_test.db")
conn.row_factory = sqlite3.Row
teleconn = sqlite3.connect("/root/bitana/storage/v6_telemetry.db")
teleconn.row_factory = sqlite3.Row

# All closed trades with r_path coverage
trades = conn.execute("""
    SELECT t.trade_uuid, t.symbol, t.side, t.pnl_r, t.exit_reason, 
           t.hold_candles, t.aggression, t.decile, t.entry_price, t.exit_price
    FROM trades t
    WHERE t.exit_time IS NOT NULL
    ORDER BY t.entry_time
""").fetchall()
trades = [dict(t) for t in trades]

print(f"Total closed trades: {len(trades)}")

# Load r_path for all trades
trade_tensors = {}
for t in trades:
    bars = teleconn.execute("""
        SELECT bar_index, timestamp, price, unrealized_r, mae_so_far, 
               mfe_so_far, vol_trail_level, struct_trail_level, atr,
               consecutive_red, above_ema, above_range_high
        FROM r_path 
        WHERE trade_uuid = ?
        ORDER BY bar_index
    """, (t['trade_uuid'],)).fetchall()
    bars = [dict(b) for b in bars]
    if bars:
        trade_tensors[t['trade_uuid']] = {
            'meta': t,
            'bars': bars,
        }

print(f"Trades with r_path data: {len(trade_tensors)}")

# ── TENSOR CONSTRUCTION ───────────────────────────────────────────

def build_tensor(trade_data):
    """
    Build trade evolution tensor: (hold_bars x 7 features)
    
    Features per bar:
      1. r_normalized     = unrealized_r / max(abs(mfe), abs(mae), 0.01)  ∈ [-1, 1] roughly
      2. mfe_velocity     = Δmfe_since_last_bar                          (expansion speed)
      3. mae_depth        = mae_so_far                                   (bleed depth, negative)
      4. trail_proximity  = (price - vol_trail) / max(atr, 0.01)        (how close to trail hit)
      5. mfe_acceleration = Δmfe_velocity                                (expansion accel)
      6. time_normalized  = bar_index / total_bars                       ∈ [0, 1]
      7. mae_recovery     = unrealized_r - mae_so_far                    (recovery from worst)
    """
    meta = trade_data['meta']
    bars = trade_data['bars']
    total_bars = meta['hold_candles']
    
    tensor = []
    prev_mfe_vel = 0.0
    
    for i, bar in enumerate(bars):
        bar_idx = bar['bar_index']
        total_range = max(abs(bar['mfe_so_far']), abs(bar['mae_so_far']), 0.01)
        
        # 1. R normalized (where is current PnL within the experienced range)
        r_norm = bar['unrealized_r'] / total_range if total_range > 0 else 0
        
        # 2. MFE velocity (how fast is the ceiling rising)
        if i > 0:
            prev_mfe = bars[i-1]['mfe_so_far']
            mfe_vel = bar['mfe_so_far'] - prev_mfe
        else:
            mfe_vel = bar['mfe_so_far']  # first bar, velocity = absolute MFE
        
        # 3. MAE depth (absolute bleed)
        mae_depth = abs(bar['mae_so_far'])  # positive = deeper in red
        
        # 4. Trail proximity
        trail = bar.get('vol_trail_level', 0) or 0
        atr = bar.get('atr', 0) or 0.01
        trail_prox = (bar['price'] - trail) / max(atr, 0.01)
        
        # 5. MFE acceleration
        mfe_accel = mfe_vel - prev_mfe_vel
        prev_mfe_vel = mfe_vel
        
        # 6. Time normalized
        time_norm = bar_idx / max(total_bars, 1)
        
        # 7. MAE recovery (how much ground recovered from worst point)
        mae_recovery = bar['unrealized_r'] - bar['mae_so_far']
        
        tensor.append({
            'bar': bar_idx,
            'price': bar['price'],
            'unreal_r': bar['unrealized_r'],
            'mfe': bar['mfe_so_far'],
            'mae': bar['mae_so_far'],
            'r_norm': r_norm,
            'mfe_vel': mfe_vel,
            'mae_depth': mae_depth,
            'trail_prox': trail_prox,
            'mfe_accel': mfe_accel,
            'time_norm': time_norm,
            'mae_recovery': mae_recovery,
            'exit_reason': meta['exit_reason'],
            'pnl_r': meta['pnl_r'],
            'is_winner': meta['pnl_r'] > 0,
        })
    
    return tensor

# Build tensors for all trades
tensors = {}
for uuid, data in trade_tensors.items():
    tensors[uuid] = build_tensor(data)

print(f"Tensors built: {len(tensors)}")

# ── SUMMARY STATISTICS PER TRADE ─────────────────────────────────

def summarize_trajectory(tensor):
    """Compute trajectory-level summary statistics."""
    if not tensor:
        return None
    
    n_bars = len(tensor)
    is_winner = tensor[0]['is_winner']
    pnl = tensor[0]['pnl_r']
    reason = tensor[0]['exit_reason']
    
    # MFE trajectory stats
    mfe_values = [t['mfe'] for t in tensor]
    mae_values = [t['mae_depth'] for t in tensor]
    r_norm_values = [t['r_norm'] for t in tensor]
    mfe_vel_values = [t['mfe_vel'] for t in tensor]
    mae_recovery_values = [t['mae_recovery'] for t in tensor]
    
    # Time to first expansion (MFE > threshold)
    time_to_10 = None  # first bar where MFE >= 0.10R
    time_to_20 = None
    time_to_30 = None
    time_to_50 = None
    
    for t in tensor:
        if time_to_10 is None and t['mfe'] >= 0.10:
            time_to_10 = t['bar']
        if time_to_20 is None and t['mfe'] >= 0.20:
            time_to_20 = t['bar']
        if time_to_30 is None and t['mfe'] >= 0.30:
            time_to_30 = t['bar']
        if time_to_50 is None and t['mfe'] >= 0.50:
            time_to_50 = t['bar']
    
    # MFE acceleration pattern
    # Split trajectory into thirds, measure avg MFE velocity in each
    third = max(n_bars // 3, 1)
    mfe_vel_early = statistics.mean(mfe_vel_values[:third]) if len(mfe_vel_values[:third]) > 0 else 0
    mfe_vel_mid = statistics.mean(mfe_vel_values[third:2*third]) if len(mfe_vel_values[third:2*third]) > 0 else 0
    mfe_vel_late = statistics.mean(mfe_vel_values[2*third:]) if len(mfe_vel_values[2*third:]) > 0 else 0
    
    # MAE persistence: how many bars with MAE > 0.3R?
    deep_bleed_bars = sum(1 for t in tensor if t['mae_depth'] > 0.3)
    deep_bleed_pct = deep_bleed_bars / n_bars
    
    # MAE staleness: bars where MAE > 0.3 AND MFE velocity < 0.01
    stale_bars = 0
    for i, t in enumerate(tensor):
        if t['mae_depth'] > 0.3 and t['mfe_vel'] < 0.01:
            stale_bars += 1
    stale_pct = stale_bars / n_bars
    
    # Consecutive deep bleed
    max_consec_deep = 0
    consec = 0
    for t in tensor:
        if t['mae_depth'] > 0.3:
            consec += 1
            max_consec_deep = max(max_consec_deep, consec)
        else:
            consec = 0
    
    # Recovery capacity
    max_recovery = max(mae_recovery_values) if mae_recovery_values else 0
    
    # R-norm shape: avg in first half vs second half
    mid_idx = n_bars // 2
    avg_r_early = statistics.mean(r_norm_values[:mid_idx]) if mid_idx > 0 else 0
    avg_r_late = statistics.mean(r_norm_values[mid_idx:]) if n_bars - mid_idx > 0 else 0
    
    # MFE velocity trend (positive = accelerating, negative = decelerating)
    if mfe_vel_early > 0:
        mfe_trend = (mfe_vel_late - mfe_vel_early) / mfe_vel_early
    else:
        mfe_trend = 0
    
    return {
        'n_bars': n_bars,
        'is_winner': is_winner,
        'pnl_r': pnl,
        'exit_reason': reason,
        'max_mfe': max(mfe_values),
        'max_mae_depth': max(mae_values),
        'avg_mfe_vel': statistics.mean(mfe_vel_values),
        'time_to_10': time_to_10,
        'time_to_20': time_to_20,
        'time_to_30': time_to_30,
        'time_to_50': time_to_50,
        'mfe_vel_early': mfe_vel_early,
        'mfe_vel_mid': mfe_vel_mid,
        'mfe_vel_late': mfe_vel_late,
        'deep_bleed_pct': deep_bleed_pct,
        'stale_pct': stale_pct,
        'max_consec_deep': max_consec_deep,
        'max_recovery': max_recovery,
        'avg_r_early': avg_r_early,
        'avg_r_late': avg_r_late,
        'mfe_trend': mfe_trend,
    }

summaries = {}
for uuid, tensor in tensors.items():
    s = summarize_trajectory(tensor)
    if s:
        summaries[uuid] = {'tensor': tensor, 'summary': s}

print(f"Summaries computed: {len(summaries)}")

# ── TRAJECTORY CLUSTERING (manual distance-based) ─────────────────

# Use summary stats to classify into trajectory types
# Features for clustering: [max_mfe, deep_bleed_pct, stale_pct, mfe_trend, max_consec_deep, time_to_20]

def classify_trajectory(s):
    """Classify trajectory into one of several regime types."""
    max_mfe = s['max_mfe']
    deep_pct = s['deep_bleed_pct']
    stale_pct = s['stale_pct']
    mfe_trend = s['mfe_trend']
    consec_deep = s['max_consec_deep']
    t20 = s['time_to_20']
    n_bars = s['n_bars']
    avg_r_early = s['avg_r_early']
    avg_r_late = s['avg_r_late']
    is_winner = s['is_winner']
    
    # IMMEDIATE REJECTION: went red fast, never recovered
    if deep_pct > 0.7 and max_mfe < 0.3 and consec_deep > 5:
        return 'immediate_rejection'
    
    # CHOP THEN STOP: flat MFE, persistent deep MAE
    if stale_pct > 0.4 and max_mfe < 0.5:
        return 'flat_chop_stop'
    
    # CONVEX RUNNER: strong MFE expansion, positive trend
    if max_mfe > 1.0 and mfe_trend > -0.3 and avg_r_late > 0.2:
        return 'convex_runner'
    
    # DELAYED BREAKOUT: slow start (avg_r_early < 0), strong finish (avg_r_late > 0.3)
    if avg_r_early < 0 and avg_r_late > 0.3 and max_mfe > 0.7:
        return 'delayed_breakout_runner'
    
    # EARLY SPIKE + FADE: MFE peaked early then collapsed
    if t20 is not None and t20 <= 5 and max_mfe > 0.3 and not is_winner:
        return 'early_spike_fade'
    
    # TAIL: everything else
    if is_winner:
        return 'slow_grind_winner'
    else:
        return 'unspecified_loser'

# Classify all trajectories
regime_counts = defaultdict(list)
for uuid, data in summaries.items():
    regime = classify_trajectory(data['summary'])
    regime_counts[regime].append(uuid)

print("\n" + "=" * 80)
print("A. TRAJECTORY CLUSTERING — Regime Identification")
print("=" * 80)

for regime, uuids in sorted(regime_counts.items(), key=lambda x: -len(x[1])):
    wr = sum(1 for u in uuids if summaries[u]['summary']['is_winner']) / len(uuids)
    avg_pnl = statistics.mean(summaries[u]['summary']['pnl_r'] for u in uuids)
    avg_mfe = statistics.mean(summaries[u]['summary']['max_mfe'] for u in uuids)
    avg_bars = statistics.mean(summaries[u]['summary']['n_bars'] for u in uuids)
    reasons = defaultdict(int)
    for u in uuids:
        reasons[summaries[u]['summary']['exit_reason']] += 1
    
    print(f"\n  {regime.upper()} ({len(uuids)} trades, WR={wr:.0%}, avg_pnl={avg_pnl:+.3f}R, avg_MFE={avg_mfe:.2f}R, avg_bars={avg_bars:.0f})")
    print(f"    Exit reasons: {dict(reasons)}")
    sample = uuids[:3]
    for u in sample:
        s = summaries[u]['summary']
        t = tensors[u][0]
        print(f"    {t.get('symbol','?'):<10} pnl={s['pnl_r']:+.3f} max_mfe={s['max_mfe']:.2f} deep_bleed={s['deep_bleed_pct']:.0%} stale={s['stale_pct']:.0%} trend={s['mfe_trend']:+.2f}")

# ── WINNER vs LOSER SEPARATION — MAE PERSISTENCE ─────────────────

print("\n\n" + "=" * 80)
print("B. MAE PERSISTENCE SEPARATION TEST")
print("=" * 80)

winners = [s for s in summaries.values() if s['summary']['is_winner']]
losers = [s for s in summaries.values() if not s['summary']['is_winner']]

features_to_test = [
    ('max_mfe', 'Max MFE (R)'),
    ('max_mae_depth', 'Max MAE Depth (R)'),
    ('deep_bleed_pct', 'Deep Bleed % (bars > 0.3R)'),
    ('stale_pct', 'Stale % (deep MAE + no MFE growth)'),
    ('max_consec_deep', 'Max Consecutive Deep Bleed (bars)'),
    ('mfe_vel_early', 'MFE Velocity (early)'),
    ('mfe_vel_late', 'MFE Velocity (late)'),
    ('mfe_trend', 'MFE Velocity Trend (late/early)'),
    ('avg_r_early', 'Avg R-normalized (early)'),
    ('avg_r_late', 'Avg R-normalized (late)'),
    ('max_recovery', 'Max MAE Recovery (R)'),
]

def cohen_d(g1, g2):
    n1, n2 = len(g1), len(g2)
    if n1 < 2 or n2 < 2:
        return 0
    m1, m2 = sum(g1)/n1, sum(g2)/n2
    v1 = sum((x - m1)**2 for x in g1) / (n1-1)
    v2 = sum((x - m2)**2 for x in g2) / (n2-1)
    pooled = math.sqrt((v1+v2)/2)
    return (m1 - m2) / pooled if pooled > 0 else 0

print(f"\n  {'Feature':<35} {'Winners':>10} {'Losers':>10} {'Delta':>10} {'Cohen d':>8}")
print("  " + "-" * 78)

for feat_key, feat_name in features_to_test:
    w_vals = [s['summary'][feat_key] for s in winners]
    l_vals = [s['summary'][feat_key] for s in losers]
    w_mean = statistics.mean(w_vals)
    l_mean = statistics.mean(l_vals)
    d = cohen_d(w_vals, l_vals)
    star = "***" if abs(d) > 1.0 else "**" if abs(d) > 0.7 else "*" if abs(d) > 0.3 else ""
    print(f"  {feat_name:<35} {w_mean:>10.4f} {l_mean:>10.4f} {w_mean-l_mean:>+10.4f} {d:>7.2f} {star}")

# ── MAE PERSISTENCE KILL RULE SIMULATION ──────────────────────────

print("\n\n" + "=" * 80)
print("C. 'NO-ACCELERATION DEATH' PATTERN — Kill Rule Validation")
print("=" * 80)

# Hypothetical rule: if MAE > 0.5R AND MFE velocity < 0.01 for 3+ consecutive bars → kill
# Measure: at what bar would this have fired for each loser?
# How many winners would it have falsely killed?

kill_stats = {
    'true_kills': [],     # losers correctly killed
    'false_kills': [],   # winners incorrectly killed  
    'missed_losers': [],  # losers that never triggered
    'clean_winners': [],  # winners that never triggered
}

for uuid, data in summaries.items():
    tensor = data['tensor']
    s = data['summary']
    meta = trade_tensors[uuid]['meta']
    
    kill_bar = None
    consec_stale = 0
    
    for t in tensor:
        if t['mae_depth'] > 0.5 and t['mfe_vel'] < 0.01:
            consec_stale += 1
            if consec_stale >= 3 and kill_bar is None:
                kill_bar = t['bar']
                break
        else:
            consec_stale = 0
    
    if s['is_winner']:
        if kill_bar is not None:
            kill_stats['false_kills'].append({
                'symbol': meta['symbol'], 'pnl_r': s['pnl_r'],
                'kill_bar': kill_bar, 'total_bars': s['n_bars'],
                'exit_reason': s['exit_reason']
            })
        else:
            kill_stats['clean_winners'].append(meta['symbol'])
    else:
        if kill_bar is not None:
            kill_stats['true_kills'].append({
                'symbol': meta['symbol'], 'pnl_r': s['pnl_r'],
                'kill_bar': kill_bar, 'total_bars': s['n_bars'],
                'exit_reason': s['exit_reason']
            })
        else:
            kill_stats['missed_losers'].append({
                'symbol': meta['symbol'], 'pnl_r': s['pnl_r'],
                'max_mae': s['max_mae_depth'], 'stale_pct': s['stale_pct'],
                'exit_reason': s['exit_reason']
            })

total_losers = len([s for s in summaries.values() if not s['summary']['is_winner']])
total_winners = len([s for s in summaries.values() if s['summary']['is_winner']])

print(f"\n  Rule: MAE > 0.5R AND MFE velocity < 0.01 for 3+ consecutive bars → kill")
print(f"\n  True kills (losers caught): {len(kill_stats['true_kills'])}/{total_losers} ({len(kill_stats['true_kills'])/max(total_losers,1)*100:.0f}%)")
print(f"  False kills (winners killed): {len(kill_stats['false_kills'])}/{total_winners} ({len(kill_stats['false_kills'])/max(total_winners,1)*100:.0f}%)")
print(f"  Missed losers (never triggered): {len(kill_stats['missed_losers'])}/{total_losers}")
print(f"  Clean winners (never triggered): {len(kill_stats['clean_winners'])}/{total_winners}")

if kill_stats['true_kills']:
    avg_saved = statistics.mean(t['pnl_r'] for t in kill_stats['true_kills'])
    print(f"\n  True kill avg original PnL: {avg_saved:+.3f}R")
    print(f"  (These would have been killed at ~-0.5R instead)")

if kill_stats['false_kills']:
    print(f"\n  FALSE KILLS (winners destroyed):")
    for fk in kill_stats['false_kills']:
        print(f"    {fk['symbol']:<10} was {fk['pnl_r']:+.3f}R winner, killed at bar {fk['kill_bar']}/{fk['total_bars']} ({fk['exit_reason']})")

if kill_stats['missed_losers']:
    print(f"\n  MISSED LOSERS (rule never triggered):")
    for ml in kill_stats['missed_losers'][:8]:
        print(f"    {ml['symbol']:<10} pnl={ml['pnl_r']:+.3f} max_mae={ml['max_mae']:.2f} stale={ml['stale_pct']:.0%} ({ml['exit_reason']})")

# ── TIME-TO-FIRST-EXPANSION DISTRIBUTION ─────────────────────────

print("\n\n" + "=" * 80)
print("D. TIME-TO-FIRST-EXPANSION DISTRIBUTION")
print("=" * 80)

for threshold, label in [(0.10, '0.10R'), (0.20, '0.20R'), (0.30, '0.30R'), (0.50, '0.50R')]:
    key = f'time_to_{int(threshold*100)}'
    
    w_times = [s['summary'][key] for s in winners if s['summary'][key] is not None]
    l_times = [s['summary'][key] for s in losers if s['summary'][key] is not None]
    
    w_median = statistics.median(w_times) if w_times else None
    l_median = statistics.median(l_times) if l_times else None
    
    w_never = sum(1 for s in winners if s['summary'][key] is None)
    l_never = sum(1 for s in losers if s['summary'][key] is None)
    
    print(f"\n  Time to first MFE ≥ {label}:")
    print(f"    Winners: median={w_median:.0f} bars (never reached: {w_never}/{len(winners)})" if w_median else f"    Winners: no data (never: {w_never}/{len(winners)})")
    print(f"    Losers:  median={l_median:.0f} bars (never reached: {l_never}/{len(losers)})" if l_median else f"    Losers:  no data (never: {l_never}/{len(losers)})")
    
    # Distribution buckets
    if w_times:
        buckets_w = defaultdict(int)
        for t in w_times:
            if t <= 3: buckets_w['1-3'] += 1
            elif t <= 5: buckets_w['4-5'] += 1
            elif t <= 10: buckets_w['6-10'] += 1
            elif t <= 20: buckets_w['11-20'] += 1
            else: buckets_w['20+'] += 1
        print(f"    Winner distribution: {dict(sorted(buckets_w.items()))}")
    
    if l_times:
        buckets_l = defaultdict(int)
        for t in l_times:
            if t <= 3: buckets_l['1-3'] += 1
            elif t <= 5: buckets_l['4-5'] += 1
            elif t <= 10: buckets_l['6-10'] += 1
            elif t <= 20: buckets_l['11-20'] += 1
            else: buckets_l['20+'] += 1
        print(f"    Loser distribution:  {dict(sorted(buckets_l.items()))}")

# ── REGIME-SPECIFIC SIGNATURES ────────────────────────────────────

print("\n\n" + "=" * 80)
print("E. REGIME-SPECIFIC TRAJECTORY SIGNATURES")
print("=" * 80)

for regime, uuids in sorted(regime_counts.items(), key=lambda x: -len(x[1])):
    if len(uuids) < 2:
        continue
    
    rs = [summaries[u]['summary'] for u in uuids]
    
    print(f"\n  {regime.upper()} ({len(uuids)} trades)")
    print(f"    avg max MFE:     {statistics.mean(r['max_mfe'] for r in rs):.2f}R")
    print(f"    avg max MAE:     {statistics.mean(r['max_mae_depth'] for r in rs):.2f}R")
    print(f"    avg deep bleed%: {statistics.mean(r['deep_bleed_pct'] for r in rs):.0%}")
    print(f"    avg stale%:      {statistics.mean(r['stale_pct'] for r in rs):.0%}")
    print(f"    avg MFE trend:   {statistics.mean(r['mfe_trend'] for r in rs):+.2f}")
    print(f"    avg bars held:   {statistics.mean(r['n_bars'] for r in rs):.0f}")
    print(f"    avg PnL:         {statistics.mean(r['pnl_r'] for r in rs):+.3f}R")
    
    # Time to expansion
    t20s = [r['time_to_20'] for r in rs if r['time_to_20'] is not None]
    if t20s:
        print(f"    median time to 0.2R: {statistics.median(t20s):.0f} bars")
    
    # Exit reason breakdown
    reasons = defaultdict(int)
    for r in rs:
        reasons[r['exit_reason']] += 1
    print(f"    exit reasons: {dict(reasons)}")

# ── FINAL: WHAT SEPARATES WINNERS — COLD FACTS ───────────────────

print("\n\n" + "=" * 80)
print("F. COLD FACTS: What separates winners from losers")
print("=" * 80)

print("\n  Features with |Cohen's d| > 0.5 (meaningful separation):")
for feat_key, feat_name in features_to_test:
    w_vals = [s['summary'][feat_key] for s in winners]
    l_vals = [s['summary'][feat_key] for s in losers]
    d = cohen_d(w_vals, l_vals)
    if abs(d) > 0.5:
        w_mean = statistics.mean(w_vals)
        l_mean = statistics.mean(l_vals)
        direction = "winners higher" if d > 0 else "losers higher"
        print(f"    {feat_name}: W={w_mean:.3f} L={l_mean:.3f} d={d:+.2f} ({direction})")

print("\n  Features with |Cohen's d| < 0.3 (NO separation):")
for feat_key, feat_name in features_to_test:
    w_vals = [s['summary'][feat_key] for s in winners]
    l_vals = [s['summary'][feat_key] for s in losers]
    d = cohen_d(w_vals, l_vals)
    if abs(d) <= 0.3:
        w_mean = statistics.mean(w_vals)
        l_mean = statistics.mean(l_vals)
        print(f"    {feat_name}: W={w_mean:.3f} L={l_mean:.3f} d={d:+.2f}")

conn.close()
teleconn.close()
