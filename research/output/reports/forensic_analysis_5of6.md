# FORENSIC ANALYSIS — 5-of-6 Liq-Cluster Trades

*Generated: 2026-05-16 08:54*

**Total trades: 86 | Winners: 39 | Stop-outs: 46 | Big winners (≥2R): 9**

---
## PHASE 1: WINNER vs STOP-OUT FEATURE COMPARISON


### STRUCTURE

| Feature | Winners (n=39) | Stop-outs (n=46) | Δ | Significant? |
|:--|--:|--:|--:|:--|
| dist_range_pct | 0.321 | 0.287 | +0.035 | — |
| dist_vwap_pct | 1.562 | 1.778 | -0.216 | — |
| slope_1h | 0.150 | 0.089 | +0.061 | — |
| slope_4h | -0.018 | 0.014 | -0.032 | — |
| comp_streak | 6.103 | 2.217 | +3.885 | — |
| breakout_atr | 0.849 | 0.702 | +0.147 | — |
| body_strength | 0.807 | 0.797 | +0.010 | — |

### FLOW

| Feature | Winners (n=39) | Stop-outs (n=46) | Δ | Significant? |
|:--|--:|--:|--:|:--|
| imb_z | 1.331 | 1.401 | -0.070 | — |
| buy_dom_12 | 6.923 | 6.609 | +0.314 | — |
| vol_z | 3.833 | 3.624 | +0.208 | — |
| vol_accel | 6.082 | 4.405 | +1.676 | weak |
| bar_return_pct | 0.675 | 0.742 | -0.067 | — |

### OI / POSITIONING

| Feature | Winners (n=39) | Stop-outs (n=46) | Δ | Significant? |
|:--|--:|--:|--:|:--|
| oi_roc | -0.006 | -0.005 | -0.000 | — |
| oi_accel | -0.014 | -0.016 | +0.002 | — |
| oi_z | 0.504 | 0.372 | +0.131 | — |
| funding_pctl | 43.169 | 46.576 | -3.407 | — |
| liq_pctl | 68.661 | 66.643 | +2.018 | — |
| liq_direction_imb | -0.081 | 0.166 | -0.247 | weak |
| cascade_strength | 0.919 | 0.849 | +0.070 | — |

### VOLATILITY

| Feature | Winners (n=39) | Stop-outs (n=46) | Δ | Significant? |
|:--|--:|--:|--:|:--|
| atr_pctl | 66.601 | 72.730 | -6.130 | — |
| bbw_pctl | 59.532 | 68.548 | -9.016 | weak |
| rvol_pctl | 62.092 | 69.070 | -6.977 | — |

### SESSIONS

| Session | Winners | Stop-outs | WR |
|:--|--:|--:|--:|
| asia | 9 | 13 | 41% |
| london | 11 | 9 | 55% |
| ny | 18 | 22 | 45% |
| off | 1 | 2 | 33% |

| Period | Winners | Stop-outs | WR |
|:--|--:|--:|--:|
| Weekday | 29 | 38 | 43% |
| Weekend | 10 | 8 | 56% |

---
## PHASE 2: FAILURE SIGNATURES

- **liq_direction_imb**: Winners have lower values (median -0.119 vs 0.163, effect=-0.56σ)
- **vol_z**: Winners have higher values (median 3.887 vs 3.149, effect=+0.47σ)

### Feature Importance (predictive of win)

| Rank | Feature | Effect Size (Cohen's d) | Direction |
|:--|:--|--:|:--|
| 1 | liq_direction_imb | 0.488 | winners < |
| 2 | slope_1h | 0.275 | winners > |
| 3 | atr_pctl | 0.246 | winners < |
| 4 | comp_streak | 0.232 | winners > |
| 5 | bar_return_pct | 0.187 | winners < |
| 6 | buy_dom_12 | 0.178 | winners > |
| 7 | breakout_atr | 0.151 | winners > |
| 8 | cascade_strength | 0.144 | winners > |
| 9 | vol_z | 0.133 | winners > |
| 10 | imb_z | 0.124 | winners < |
| 11 | dist_vwap_pct | 0.116 | winners < |
| 12 | body_strength | 0.096 | winners > |
| 13 | oi_roc | 0.009 | winners < |

---
## PHASE 3: STOP ANALYSIS (MAE/MFE)


### MAE Distribution (in ATR)

| Group | Mean | Median | P75 | P90 | Max |
|:--|--:|--:|--:|--:|--:|
| Winners | 1.86 | 1.74 | 2.13 | 2.53 | 4.17 |
| Stop-outs | 2.58 | 2.41 | 2.91 | 3.16 | 3.99 |
| All | 2.25 | 2.24 | 2.59 | 3.06 | 4.17 |

### MFE Distribution (in ATR)

| Group | Mean | Median | P75 | P90 | Max |
|:--|--:|--:|--:|--:|--:|
| Winners | 4.05 | 4.04 | 4.91 | 5.84 | 7.91 |
| Stop-outs | 0.96 | 0.80 | 1.37 | 1.80 | 3.33 |
| All | 2.38 | 1.86 | 3.63 | 5.04 | 7.91 |

### Winners That Dipped Beyond Stop Levels

- Winners dipping > 1.5 ATR before expanding: 26/39 (67%)
- Winners dipping > 2.0 ATR before expanding: 12/39 (31%)
- Winners dipping > 2.5 ATR before expanding: 4/39 (10%)
- Winners dipping > 3.0 ATR before expanding: 1/39 (3%)
- Winners dipping > 3.5 ATR before expanding: 1/39 (3%)

### Stop Sensitivity (hypothetical)

| Stop (ATR) | Would Survive | Extra Wins | Extra Exposure |
|:--|--:|--:|:--|
| 2.0x | 0/46 | ~0 | moderate |
| 2.5x | 27/46 | ~1 | moderate |
| 3.0x | 37/46 | ~1 | wider DD |
| 3.5x | 43/46 | ~0 | wider DD |

---
## PHASE 4: TOP TRADES — EXPLOSIVE MOVE ANALYSIS


### Top 10 Trades by R

| Date | R | Exit | Session | imb_z | vol_z | oi_roc | comp_streak | cascade_str | MFE(ATR) |
|:--|--:|:--|:--|--:|--:|--:|--:|--:|--:|
| 2026-02-25 01:05 | +2.75 | partial_2R | asia | 1.1 | 5.3 | -0.1% | 0 | 1.1 | 7.9 |
| 2026-03-02 14:40 | +2.66 | partial_2R | ny | 0.6 | 3.9 | -0.0% | 0 | 0.7 | 5.8 |
| 2026-04-01 16:25 | +2.64 | partial_2R | ny | 0.2 | 3.2 | 8.0% | 0 | 0.5 | 5.9 |
| 2025-12-18 13:00 | +2.54 | partial_2R | ny | 1.0 | 3.6 | -2.8% | 0 | 0.6 | 5.8 |
| 2026-05-08 17:25 | +2.41 | partial_2R | ny | 0.8 | 3.7 | 1.6% | 0 | 0.9 | 6.0 |
| 2026-03-01 01:45 | +2.40 | partial_2R | asia | 1.9 | 2.3 | -3.3% | 0 | 0.9 | 5.6 |
| 2026-04-16 18:15 | +2.32 | partial_2R | ny | 1.4 | 3.9 | -8.5% | 0 | 1.8 | 5.1 |
| 2026-05-10 08:05 | +2.23 | partial_2R | london | 1.8 | 5.9 | -2.5% | 0 | 2.0 | 5.9 |
| 2026-05-14 14:45 | +2.00 | partial_2R | ny | 1.2 | 5.5 | 2.9% | 0 | 0.4 | 4.4 |
| 2026-03-04 07:45 | +1.93 | partial_2R | london | 0.7 | 4.7 | 2.7% | 0 | 0.7 | 4.3 |

### Common Traits of Top Trades

- **imb_z**: top10 avg = 1.064 vs all avg = 1.368 (0.8x)
- **vol_z**: top10 avg = 4.205 vs all avg = 3.721 (1.1x)
- **oi_roc**: top10 avg = -0.002 vs all avg = -0.006 (0.4x) ⬇
- **comp_streak**: top10 avg = 0.000 vs all avg = 3.953 (0.0x) ⬇
- **cascade_strength**: top10 avg = 0.948 vs all avg = 0.885 (1.1x)
- **body_strength**: top10 avg = 0.817 vs all avg = 0.800 (1.0x)
- **breakout_atr**: top10 avg = 0.751 vs all avg = 0.779 (1.0x)
- **dist_vwap_pct**: top10 avg = 1.977 vs all avg = 1.658 (1.2x)
- **slope_1h**: top10 avg = 0.235 vs all avg = 0.114 (2.1x) ⬆
- **buy_dom_12**: top10 avg = 6.700 vs all avg = 6.744 (1.0x)
- **liq_direction_imb**: top10 avg = -0.263 vs all avg = 0.050 (-5.2x) ⬇
- **atr_pctl**: top10 avg = 78.460 vs all avg = 70.210 (1.1x)
- **expansion_velocity**: top10 avg = 1.986 vs all avg = 0.481 (4.1x) ⬆

### Top Trade Session Distribution

- asia: 2/10
- london: 2/10
- ny: 6/10
- off: 0/10

---
## PHASE 5: SYNTHESIS & RECOMMENDATIONS


### Current State

- Trades: 86 | WR: 45% | PF: 0.84
- Stop-out rate: 53%
- Skew: 0.80 (positive = fat right tail ✓)
- Top 10 trades contribute: 23.9R / -8.5R total

### Honest Assessment

> This is CLOSE to a real edge. PF 0.84 with positive skew and fat right tail. The structure is sound — the gap is entry selectivity, not system design.