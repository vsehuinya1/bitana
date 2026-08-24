# Research Duo — Phase 4 OOS Validation Report

Generated: 2026-05-31 04:25 UTC

Observational only. No strategy modifications, tuning, or recommendations.

## Primary Question

> Does `MFE >= 0.3R within 10 bars` retain positive expectancy on unseen trades
> when evaluated strictly by entry_time order?

**Cohort:** 170 closed trades with r_path (post_gate_only=False)

**Walk-forward folds:** 12 | **Final holdout:** 15 trades

## 1. Pathway Validation (IS vs OOS vs Holdout)

| Pathway | IS N | IS WR | IS Exp | OOS N | OOS WR | OOS Exp | Hold N | Hold Exp | Exp drift |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mfe_0_3r_within_10 | 61 | 54.1% | +0.106R | 56 | 55.4% | +0.081R | 5 | +0.760R | -0.025R |
| mfe_0_5r_within_10 | 41 | 65.9% | +0.373R | 37 | 67.6% | +0.334R | 5 | +0.760R | -0.039R |

### Cohen's d drift (IS vs pooled OOS)

| Pathway | IS d | OOS d | d drift |
| --- | --- | --- | --- |
| mfe_0_3r_within_10 | 0.74 | 0.95 | +0.21 |
| mfe_0_5r_within_10 | 1.08 | 1.22 | +0.14 |

### Per-fold pathway detail (0.3R)

| Fold | N | WR | Exp |  |
| --- | --- | --- | --- | --- |
| 0 | 1 | 100.0% | +0.369R |  ⚠ underpowered |
| 1 | 6 | 66.7% | +0.034R |  |
| 2 | 2 | 0.0% | -0.527R |  ⚠ underpowered |
| 3 | 6 | 16.7% | -0.818R |  |
| 4 | 4 | 25.0% | -0.631R |  ⚠ underpowered |
| 5 | 5 | 60.0% | -0.078R |  |
| 6 | 7 | 71.4% | +0.229R |  |
| 7 | 4 | 75.0% | +0.778R |  ⚠ underpowered |
| 8 | 3 | 100.0% | +0.792R |  ⚠ underpowered |
| 9 | 6 | 33.3% | -0.403R |  |
| 10 | 7 | 71.4% | +0.353R |  |
| 11 | 5 | 60.0% | +1.139R |  |

## 2. Early Separability (OOS, no tuning)

**Target:** pathway_mfe_0.3r_within_10_bars
**Model:** LogisticRegression(max_iter=1000, random_state=fixed)

| Bar cutoff | N | Positives | OOS accuracy | OOS AUC | Folds |
| --- | --- | --- | --- | --- | --- |
| 3 | 170 | 66 | 0.7755555555555556 | 0.8492380952380951 | 5 |
| 5 | 170 | 66 | 0.8355555555555556 | 0.9519047619047619 | 5 |
| 7 | 170 | 66 | 0.8777777777777779 | 0.970952380952381 | 5 |
| 10 | 170 | 66 | 0.8777777777777779 | 1.0 | 5 |

### Leakage audit

- Features at bar N use **only bars 1..N** from r_path
- Labels use **full-trade** `bars_to_*r_mfe` (post-hoc pathway membership)
- Walk-forward: **no random shuffle**, ordered by `entry_time`
- Final holdout trades **never appear** in any training fold

## 3. Sample Size Caveats

- Folds with N < 8 validation trades are flagged underpowered
- Pathway cells with N < 5 are flagged underpowered
- Bootstrap CIs are reported for expectancy where N permits

## Data Provenance

- Phase 2 outputs read-only: `r_path_long.parquet`, `trade_features.parquet`
- Split spec: `experiments/oos_splits.yaml`
