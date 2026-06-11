# Research Duo — Phase 5 Entry Quality Report

Generated: 2026-05-31 04:39 UTC

Observational only. No engine modifications, optimization, or recommendations.

## Label Definitions

- **EXPLOSIVE:** pnl_r > 0 AND mfe >= 1.0R within 15 bars
- **EARLY_DEAD:** pnl_r <= 0 AND max mfe in bars 1-10 <= 0.3R
- **SURVIVOR:** pnl_r > 0 AND NOT EXPLOSIVE
- **LATE_DEAD:** pnl_r <= 0 AND max mfe in bars 1-10 > 0.3R

## Cohort: `all_r_path`

**N=170** | EARLY_DEAD: 81, SURVIVOR: 42, LATE_DEAD: 28, EXPLOSIVE: 19

### Confirmation Integrity QA

- confirmation_mismatch_rate: 0.0
- imb_zero_but_confirm_pass: 48
- imb_fallback_rate: 0.36470588235294116

### Feature Comparison (EXPLOSIVE vs EARLY_DEAD)

| Feature | EXP mean/rate | DEAD mean/rate | Cohen's d |
| --- | --- | --- | --- |
| decile | 1.894736842105263 | 2.076923076923077 | -0.08 |
| aggression_score | 64.54678118643459 | 58.938787284408725 | +0.38 |
| cascade_strength | 35.95862021035194 | 87.35952027265773 | -0.13 |
| breakout_distance_pct | -1.0667377405606755 | -1.3624044807254307 | +0.19 |
| imbalance_z | 1.0619789473684211 | 0.8165015384615384 | +0.26 |
| vol_z | 1.0904850928409318 | 0.40176301932804037 | +0.40 |
| confirm_breakout | 21% | 22% | — |
| confirm_imb | 74% | 80% | — |
| confirm_vol | 26% | 21% | — |
| confirm_body | 95% | 95% | — |
| confirm_impulse | 95% | 81% | — |
| confirm_momentum | 89% | 90% | — |

### Earliest Separation — entry (top features)

| Feature | Cohen's d | MI | AUC |
| --- | --- | --- | --- |
| vol_z | +0.40 | 0.000 | 0.607 |
| aggression_score | +0.38 | 0.068 | 0.625 |
| confirm_impulse | +0.36 | 0.070 | 0.566 |
| imbalance_z | +0.26 | 0.077 | 0.566 |
| bd_distance_pct | +0.19 | 0.082 | 0.524 |
| confirm_imb | -0.16 | 0.000 | 0.467 |
| cascade_strength | -0.13 | 0.000 | 0.440 |
| confirm_vol | +0.13 | 0.031 | 0.527 |

### Earliest Separation — bar_3 (top features)

| Feature | Cohen's d | MI | AUC |
| --- | --- | --- | --- |
| unrealized_r_b3 | +2.53 | 0.367 | 0.971 |
| mfe_b3 | +2.29 | 0.204 | 0.847 |
| mfe_velocity_b3 | +2.28 | 0.234 | 0.847 |
| mae_b3 | +1.36 | 0.123 | 0.875 |
| vol_z | +0.40 | 0.000 | 0.607 |
| aggression_score | +0.38 | 0.068 | 0.625 |
| confirm_impulse | +0.36 | 0.030 | 0.566 |
| imbalance_z | +0.26 | 0.077 | 0.566 |

### Earliest Separation — bar_5 (top features)

| Feature | Cohen's d | MI | AUC |
| --- | --- | --- | --- |
| mfe_b5 | +4.73 | 0.429 | 0.988 |
| mfe_velocity_b5 | +3.56 | 0.379 | 0.991 |
| unrealized_r_b5 | +2.78 | 0.371 | 0.992 |
| mae_b5 | +1.61 | 0.246 | 0.930 |
| vol_z | +0.40 | 0.000 | 0.607 |
| aggression_score | +0.38 | 0.068 | 0.625 |
| confirm_impulse | +0.36 | 0.030 | 0.566 |
| imbalance_z | +0.26 | 0.077 | 0.566 |

### Confirmation Stack

| Confirm/combo | N | WR | EXP rate | EARLY_DEAD rate |
| --- | --- | --- | --- | --- |
| breakout | 38 | 42% | 11% | 47% |
| imb | 135 | 35% | 10% | 48% |
| vol | 44 | 45% | 11% | 39% |
| body | 161 | 35% | 11% | 48% |
| impulse | 140 | 38% | 13% | 47% |
| momentum | 153 | 36% | 11% | 48% |
| imb_vol | 13 | 54% | 8% | 31% |
| imb_momentum | 123 | 35% | 11% | 48% |
| vol_breakout | 15 | 47% | 13% | 40% |
| all_six | 3 | 67% | 33% | 33% |

### Breakout Distance Buckets

| BD bucket | N | EXP rate | EARLY_DEAD rate | Mean MFE@10 |
| --- | --- | --- | --- | --- |
| <-2% | 25 | 12% | 36% | 0.37337600000000004 |
| -2to0% | 62 | 19% | 40% | 0.4895806451612903 |
| 0to2% | 21 | 19% | 38% | 0.43552380952380954 |

### Cascade Strength Deciles

| Decile | N | EXP rate | EARLY_DEAD rate |
| --- | --- | --- | --- |
| (0.103, 0.277] | 15 | 13% | 40% |
| (0.277, 0.587] | 14 | 21% | 64% |
| (0.587, 1.097] | 14 | 14% | 50% |
| (1.097, 1.589] | 15 | 13% | 33% |
| (1.589, 2.371] | 14 | 21% | 36% |
| (2.371, 4.659] | 14 | 21% | 43% |
| (4.659, 9.916] | 15 | 0% | 53% |
| (9.916, 22.016] | 14 | 0% | 64% |
| (22.016, 80.324] | 14 | 7% | 29% |
| (80.324, 3312.978] | 15 | 20% | 40% |

### Class Counts by Tier

- {'tier_group': 'experimental', 'EARLY_DEAD': 43, 'EXPLOSIVE': 12, 'LATE_DEAD': 14, 'SURVIVOR': 19}
- {'tier_group': 'proven', 'EARLY_DEAD': 38, 'EXPLOSIVE': 7, 'LATE_DEAD': 14, 'SURVIVOR': 23}

### Answer: What differs at entry? (`all_r_path`)

**Strongest entry separators (|d| >= 0.3):**
- `vol_z`: d=+0.40, AUC=0.6072874493927125
- `aggression_score`: d=+0.38, AUC=0.6251012145748988
- `confirm_impulse`: d=+0.36, AUC=0.5662768031189084

**Present more often / higher in EXPLOSIVE vs EARLY_DEAD:**
- Higher imbalance z (d=+0.26)
- Higher vol z (d=+0.40)

**Cascade:** highest decile explosive rate 20% vs lowest 13%; early_dead highest decile 40% vs lowest 40%.

## Cohort: `post_gate`

**N=55** | EARLY_DEAD: 19, EXPLOSIVE: 13, SURVIVOR: 12, LATE_DEAD: 11

### Confirmation Integrity QA

- confirmation_mismatch_rate: 0.0
- imb_zero_but_confirm_pass: 0
- imb_fallback_rate: 0.0

### Feature Comparison (EXPLOSIVE vs EARLY_DEAD)

| Feature | EXP mean/rate | DEAD mean/rate | Cohen's d |
| --- | --- | --- | --- |
| decile | 2.1538461538461537 | 1.6842105263157894 | +0.28 |
| aggression_score | 65.82021981875265 | 61.63847313546409 | +0.54 |
| cascade_strength | 39.59879387814946 | 182.35691005606125 | -0.24 |
| breakout_distance_pct | -0.6028327325884623 | -0.7145791793335433 | +0.13 |
| imbalance_z | 1.1168153846153848 | 1.4992526315789474 | -0.49 |
| vol_z | 1.4278940739252137 | 0.39599349008226714 | +0.61 |
| confirm_breakout | 31% | 21% | — |
| confirm_imb | 69% | 89% | — |
| confirm_vol | 31% | 16% | — |
| confirm_body | 100% | 100% | — |
| confirm_impulse | 92% | 95% | — |
| confirm_momentum | 100% | 100% | — |

### Earliest Separation — entry (top features)

| Feature | Cohen's d | MI | AUC |
| --- | --- | --- | --- |
| vol_z | +0.61 | 0.008 | 0.668 |
| aggression_score | +0.54 | 0.098 | 0.648 |
| confirm_imb | -0.52 | 0.072 | 0.399 |
| imbalance_z | -0.49 | 0.000 | 0.287 |
| confirm_vol | +0.36 | 0.000 | 0.575 |
| entry_decile | +0.28 | 0.000 | 0.630 |
| cascade_strength | -0.24 | 0.000 | 0.449 |
| confirm_breakout | +0.22 | 0.000 | 0.549 |

### Earliest Separation — bar_3 (top features)

| Feature | Cohen's d | MI | AUC |
| --- | --- | --- | --- |
| unrealized_r_b3 | +2.77 | 0.567 | 0.988 |
| mfe_b3 | +1.71 | 0.307 | 0.866 |
| mfe_velocity_b3 | +1.71 | 0.318 | 0.866 |
| mae_b3 | +1.49 | 0.295 | 0.919 |
| vol_z | +0.61 | 0.008 | 0.668 |
| aggression_score | +0.54 | 0.098 | 0.648 |
| confirm_imb | -0.52 | 0.013 | 0.399 |
| imbalance_z | -0.49 | 0.000 | 0.287 |

### Earliest Separation — bar_5 (top features)

| Feature | Cohen's d | MI | AUC |
| --- | --- | --- | --- |
| mfe_b5 | +3.86 | 0.636 | 1.000 |
| mfe_velocity_b5 | +3.01 | 0.647 | 1.000 |
| unrealized_r_b5 | +2.83 | 0.584 | 0.992 |
| mae_b5 | +1.95 | 0.499 | 0.964 |
| vol_z | +0.61 | 0.008 | 0.668 |
| aggression_score | +0.54 | 0.098 | 0.648 |
| confirm_imb | -0.52 | 0.013 | 0.399 |
| imbalance_z | -0.49 | 0.000 | 0.287 |

### Confirmation Stack

| Confirm/combo | N | WR | EXP rate | EARLY_DEAD rate |
| --- | --- | --- | --- | --- |
| breakout | 14 | 50% | 29% | 29% |
| imb | 44 | 41% | 20% | 39% |
| vol | 15 | 67% | 27% | 20% |
| body | 54 | 44% | 24% | 35% |
| impulse | 51 | 45% | 24% | 35% |
| momentum | 55 | 45% | 24% | 35% |
| imb_vol | 5 | 80% | 20% | 20% |
| imb_momentum | 44 | 41% | 20% | 39% |
| vol_breakout | 6 | 67% | 33% | 17% |
| all_six | 2 | 50% | 50% | 50% |

### Breakout Distance Buckets

| BD bucket | N | EXP rate | EARLY_DEAD rate | Mean MFE@10 |
| --- | --- | --- | --- | --- |
| -2to0% | 39 | 23% | 36% | 0.5600820512820512 |
| 0to2% | 16 | 25% | 31% | 0.49715624999999997 |

### Cascade Strength Deciles

| Decile | N | EXP rate | EARLY_DEAD rate |
| --- | --- | --- | --- |
| (0.103, 0.172] | 6 | 33% | 33% |
| (0.172, 0.451] | 5 | 40% | 60% |
| (0.451, 0.902] | 6 | 33% | 17% |
| (0.902, 1.16] | 5 | 20% | 60% |
| (1.16, 1.558] | 6 | 17% | 17% |
| (1.558, 2.094] | 5 | 40% | 40% |
| (2.094, 3.53] | 5 | 20% | 40% |
| (3.53, 10.808] | 6 | 0% | 33% |
| (10.808, 68.876] | 5 | 0% | 20% |
| (68.876, 3312.978] | 6 | 33% | 33% |

### Class Counts by Tier

- {'tier_group': 'experimental', 'EARLY_DEAD': 10, 'EXPLOSIVE': 9, 'LATE_DEAD': 5, 'SURVIVOR': 5}
- {'tier_group': 'proven', 'EARLY_DEAD': 9, 'EXPLOSIVE': 4, 'LATE_DEAD': 6, 'SURVIVOR': 7}

### Answer: What differs at entry? (`post_gate`)

**Strongest entry separators (|d| >= 0.3):**
- `vol_z`: d=+0.61, AUC=0.6680161943319839
- `aggression_score`: d=+0.54, AUC=0.6477732793522267
- `confirm_imb`: d=-0.52, AUC=0.39878542510121456
- `imbalance_z`: d=-0.49, AUC=0.28744939271255066
- `confirm_vol`: d=+0.36, AUC=0.5748987854251012

**Present more often / higher in EXPLOSIVE vs EARLY_DEAD:**
- Higher vol z (d=+0.61)
- Volume confirmation: EXPLOSIVE 31% vs EARLY_DEAD 16%

**Cascade:** highest decile explosive rate 33% vs lowest 33%; early_dead highest decile 33% vs lowest 33%.

## Data Provenance

- Phase 2 outputs unmodified: `trades_reconstructed.parquet`, `r_path_long.parquet`, `trade_features.parquet`
