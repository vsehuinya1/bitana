"""Generate comprehensive backtest report."""
import csv, ast
from collections import Counter, defaultdict

print("="*70)
print("V3 LIQ-CLUSTER COMPREHENSIVE BACKTEST REPORT")
print("Period: Jan 1 - Apr 30, 2026 | Real Coinalyze Data")
print("="*70)

layers = ['baseline', 'relax_ret5d', 'quality_scoring', 'vol_targeting',
          'regime_sizing', 'pyramiding', 'correlation_control']
all_trades = {}
for layer in layers:
    try:
        with open(f'/root/bitana/backtest_output/{layer}_trades.csv') as f:
            all_trades[layer] = list(csv.DictReader(f))
    except:
        all_trades[layer] = []

# Comparison table
print("\nCOMPARISON SUMMARY")
print("-"*70)
header = "{:<25} {:>6} {:>6} {:>10} {:>6} {:>8}".format('Layer', 'Trades', 'WR%', 'Total R', 'PF', 'Avg R')
print(header)
print("-"*70)
for layer in layers:
    trades = all_trades.get(layer, [])
    if not trades:
        print("{:<25} NO DATA".format(layer))
        continue
    n = len(trades)
    wins = sum(1 for t in trades if float(t['pnl_r']) > 0)
    tr = sum(float(t['pnl_r']) for t in trades)
    gp = sum(float(t['pnl_r']) for t in trades if float(t['pnl_r']) > 0)
    gl = abs(sum(float(t['pnl_r']) for t in trades if float(t['pnl_r']) < 0))
    pf = gp / gl if gl > 0 else float('inf')
    wr = wins / n * 100
    print("{:<25} {:>6} {:>6.1f} {:>+10.2f} {:>6.3f} {:>+8.4f}".format(layer, n, wr, tr, pf, tr/n))

# Baseline deep dive
print("\n" + "="*70)
print("BASELINE DEEP DIVE (Current V3 Engine)")
print("="*70)
bt = all_trades.get('baseline', [])
if bt:
    print("\nExit Reasons:")
    reasons = Counter(t['exit_reason'] for t in bt)
    for r, n in reasons.most_common():
        avg = sum(float(t['pnl_r']) for t in bt if t['exit_reason']==r)/n
        pct = n/len(bt)*100
        print("  {:<20} {:>4} ({:>3.0f}%)  avg R={:+.3f}".format(r, n, pct, avg))

    print("\nConfirmations:")
    conf_dist = Counter()
    for t in bt:
        try:
            conf = ast.literal_eval(t['conf'])
            cc = sum(1 for v in conf.values() if v)
            conf_dist[cc] += 1
        except: pass
    for c in sorted(conf_dist.keys()):
        n = conf_dist[c]
        trades_c = [t for t in bt if sum(1 for v in ast.literal_eval(t['conf']).values() if v)==c]
        avg = sum(float(t['pnl_r']) for t in trades_c)/n
        wr = sum(1 for t in trades_c if float(t['pnl_r'])>0)/n*100
        print("  {}/6: {:>4} trades, WR={:.1f}%, avg R={:+.3f}".format(c, n, wr, avg))

    print("\nBy Symbol (sorted by total R):")
    sym_data = defaultdict(lambda: {'n':0,'r':0})
    for t in bt:
        sym_data[t['symbol']]['n'] += 1
        sym_data[t['symbol']]['r'] += float(t['pnl_r'])
    for s, d in sorted(sym_data.items(), key=lambda x: x[1]['r'], reverse=True):
        wr = sum(1 for t in bt if t['symbol']==s and float(t['pnl_r'])>0)/d['n']*100
        print("  {:<15} {:>3} trades, WR={:>3.0f}%, total R={:>+7.2f}".format(s, d['n'], wr, d['r']))

    print("\nMonthly:")
    monthly = defaultdict(lambda: {'n':0,'r':0})
    for t in bt:
        m = t['entry_time'][:7]
        monthly[m]['n'] += 1
        monthly[m]['r'] += float(t['pnl_r'])
    for m in sorted(monthly.keys()):
        d = monthly[m]
        wr = sum(1 for t in bt if t['entry_time'][:7]==m and float(t['pnl_r'])>0)/d['n']*100
        print("  {}: {:>3} trades, WR={:.0f}%, total R={:>+7.2f}".format(m, d['n'], wr, d['r']))

    ny = [t for t in bt if t.get('is_ny')=='True']
    non_ny = [t for t in bt if t.get('is_ny')!='True']
    print("\nNY Session: {} trades, WR={:.1f}%, R={:+.2f}".format(len(ny), sum(1 for t in ny if float(t['pnl_r'])>0)/max(len(ny),1)*100, sum(float(t['pnl_r']) for t in ny)))
    print("Non-NY:     {} trades, WR={:.1f}%, R={:+.2f}".format(len(non_ny), sum(1 for t in non_ny if float(t['pnl_r'])>0)/max(len(non_ny),1)*100, sum(float(t['pnl_r']) for t in non_ny)))

    ba = [t for t in bt if t.get('btc_aligned')=='1']
    bna = [t for t in bt if t.get('btc_aligned')!='1']
    print("\nBTC Aligned:     {} trades, WR={:.1f}%, R={:+.2f}".format(len(ba), sum(1 for t in ba if float(t['pnl_r'])>0)/max(len(ba),1)*100, sum(float(t['pnl_r']) for t in ba)))
    print("Non-Aligned:     {} trades, WR={:.1f}%, R={:+.2f}".format(len(bna), sum(1 for t in bna if float(t['pnl_r'])>0)/max(len(bna),1)*100, sum(float(t['pnl_r']) for t in bna)))

    print("\nTop 5 trades:")
    for t in sorted(bt, key=lambda x: float(x['pnl_r']), reverse=True)[:5]:
        print("  {:<15} R={:+.3f}  reason={:<18} hold={:>3}  conf={}/6".format(t['symbol'], float(t['pnl_r']), t['exit_reason'], t['hold'], t['conf_count']))
    print("Worst 5 trades:")
    for t in sorted(bt, key=lambda x: float(x['pnl_r']))[:5]:
        print("  {:<15} R={:+.3f}  reason={:<18} hold={:>3}  conf={}/6".format(t['symbol'], float(t['pnl_r']), t['exit_reason'], t['hold'], t['conf_count']))

    # Key observations
    print("\n" + "="*70)
    print("KEY OBSERVATIONS")
    print("="*70)
    print("""
1. BASELINE IS PROFITABLE: +96R over 4 months, 61.6% WR, PF 1.65
   - This is with REAL Coinalyze data (not proxy)
   - 385 trades across 28 symbols = ~14 trades/symbol over 4 months

2. CASCADE FILTER WORKS: The ret5d_min=-5% filter is CORRECT
   - Relaxing to -10% ADDS trades but LOWERS total R (+88 vs +96)
   - The filter is doing its job - keeping weak cascades out

3. SIZING LAYERS SHOW NO DELTA: Quality scoring, vol targeting,
   regime sizing, pyramiding all show identical results
   - Reason: position management (stops, trails) dominates
   - R/trade is normalized, so sizing changes don't affect R multiples
   - Need to look at dollar PnL, not just R

4. EXIT REASONS: 42% expansion_decay, 38% stop_loss, 18% vol_trail
   - Stop losses are exactly -1R (by design)
   - Winners average +0.88R (expansion_decay) and +1.29R (vol_trail)
   - Struct trail rare (1%) but high avg (+2.36R)

5. CONFIRMATIONS: 4/6 = 79% of trades, 5/6 = 20%, 6/6 = 1%
   - 4/6 has best WR (62.8%) and positive avg R (+0.279)
   - 5/6 has LOWER WR (55.1%) - more confirmations != better
   - 6/6 too few samples (3 trades) to draw conclusions

6. NY SESSION: No meaningful difference vs non-NY
   - NY: 59.9% WR, Non-NY: 62.7% WR
   - Total R split roughly evenly

7. BTC ALIGNMENT: Non-aligned actually slightly BETTER
   - Aligned: 60.7% WR, +45.9R
   - Non-aligned: 63.5% WR, +50.2R
   - BTC alignment multiplier may not be adding value

8. BEST SYMBOLS: ENA (+8.5R best trade), DOT, APT, ZEC, FET
   - WORST: TAO (41 trades, 49% WR, -0.09R avg), BNB (40% WR)

9. MONTHLY: Feb +36R, Mar +41R, Apr +19R
   - Apr shows degradation - possible regime change
""")

    # What to do next
    print("="*70)
    print("RECOMMENDATIONS")
    print("="*70)
    print("""
1. KEEP ret5d_min=-5% - it's filtering correctly
2. BTC alignment 2x multiplier may not help - test without
3. NY session boost not justified by data - skip for now
4. Focus on EXIT optimization - 42% expansion_decay means
   we're leaving money on the table. Consider:
   - Tighter vol trail (currently 2x ATR)
   - Partial TP at 2R instead of letting it decay
5. TAO and BNB underperform - consider removing or reducing size
6. The 5/6 confirmation trades underperform 4/6 - consider
   requiring exactly 4/6 (not 4+) or adding quality weighting
7. Dollar PnL analysis needed - sizing changes may show
   different results in absolute terms
""")
