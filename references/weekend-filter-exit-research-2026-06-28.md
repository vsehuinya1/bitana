# Weekend Filter & Exit Strategy Research (2026-06-28)

## Question
Can the shadow system trade profitably on weekends with entry filters + exit management?

## Data
`signal_shadow.db` → `setup_snapshots` (v_confirms3 gate, relaxed Jun 26) + `setup_r_path` (bar-by-bar R-path).
Filtered entries cross-referenced with r_path for deterministic exit simulation.

## Entry Filters (Validated)

Three gates applied at entry time:

1. **Kill NY session** (hour ≥ 16 UTC) — NY is dead (47% WR, +0.042R avg on weekdays)
2. **30-min viability check** (fwd_atr_6 ≥ -0.5) — kill trades in deep red at bar 6
3. **vol_z ≥ 0.5** — entry must have above-average volume confirmation

Source: `references/early-bar-viability-filter-2026-06-27.md` (validated on Thu/Fri 146 trades).

## Exit Strategies Tested

### Fixed ±2R TP/SL
- TP at +2R, SL at -2R, first hit wins
- Simple, locks in spike moves

### TSL (Trailing Stop -2R from peak)
- Trail = peak_r - 2.0, exit when close < trail
- Lets winners run, but gives back heavily in fade environments

### Hybrid (+1R trail after TP activation)
- Once peak_r ≥ 2.0 (TP hit), tighten trail to peak_r - 1.0
- Locks in at least +1R while allowing momentum continuation
- If never hits +2R, SL at -2R applies

## Results

### Saturday June 27 (33 filtered trades of 95)
| Exit | Total R | Avg R |
|------|---------|--------|
| Fixed ±2R | +34.0R | +1.03 |
| TSL (-2R) | +8.6R | +0.26 |
| **Hybrid (+1R)** | **+48.0R** | **+1.45** |

Trade behavior: 61% hit TP then faded (spike-and-fade environment). Institutional flow provides counter-pressure.

### Sunday June 28 (13 filtered trades of 41)
| Exit | Total R | Avg R |
|------|---------|--------|
| Fixed ±2R | +3.3R | +0.26 |
| TSL (-2R) | +0.1R | +0.01 |
| **Hybrid (+1R)** | **+7.0R** | **+0.54** |

Trade behavior: 38% TP-then-fade, 23% never hit either (choppier, fewer runners). Thinner liquidity means fewer setups but less aggressive reversal on the good ones.

### Combined Weekend (46 filtered trades)
| Exit | Total R | Avg R |
|------|---------|--------|
| Fixed ±2R | +37.3R | +0.81 |
| TSL (-2R) | +8.7R | +0.19 |
| **Hybrid (+1R)** | **+55.0R** | **+1.20** |

## Raw (Unfiltered) Comparison
- Saturday raw: 29 shadow_trades rows, +11.3R (includes mirror pairs)
- Sunday raw: 21 unique setups, -12.2R net outcome
- Filters turn weekend from net-negative to strongly positive

## Regime Differences
| Characteristic | Saturday | Sunday |
|---------------|----------|--------|
| Filtered entries | 33 | 13 |
| Fire rate | High | Low |
| TP-then-fade rate | 61% | 38% |
| Trend continuation | 6% | 15% |
| Environment | Spike-and-fade | Sparse runners |
| Best exit | Hybrid (+1R) | Hybrid (+1R) |

## Key Findings
1. **Entry filters work on weekends**: transform -12.2R raw Sunday into +7.0R filtered
2. **Hybrid exit dominates both days**: +55.0R combined vs +37.3R fixed vs +8.7R TSL
3. **TSL (-2R) fails on weekends**: too wide for Saturday chop, too slow for Sunday thinness
4. **Sample size caveat**: 46 trades across 2 weekends. Need 3+ more weekends to validate.
5. **Weekend regime is structurally weaker**: fewer setups, lower follow-through, but filters isolate the few that work

## Recommended Configuration
- **Entry**: Keep existing filters (NY kill + fwd_6 ≥ -0.5 + vol_z ≥ 0.5)
- **Exit**: Hybrid (+1R trail after TP hit) — adaptively tightens on winners
- **Weekend**: No day-of-week filter needed IF entry filters are active. The fwd_6 gate naturally kills weak weekend setups.

## Next Steps
- Validate hybrid exit on weekday data (Thu/Fri 146 trades) to confirm it's not overfit to weekends
- Collect 3+ more weekends before concluding robustness
- Consider whether hybrid trail distance should be regime-dependent (tighter on Sat, wider on Sun) — but this requires real-time regime detection we don't have
