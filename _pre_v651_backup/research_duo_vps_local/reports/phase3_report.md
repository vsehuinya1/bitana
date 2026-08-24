# Research Duo — Phase 3 Observational Report

Generated: 2026-05-30 20:55 UTC

This report is purely observational. No strategy modifications or recommendations.

## 1. Cluster Stability

| Ablation | Features remaining | ARI vs full k=3 k-means | Features removed |
| --- | --- | --- | --- |
| drop_entry_context | 13 | 0.276 | 9 |
| drop_path_dynamics | 9 | 0.405 | 13 |
| drop_post_gate_flags | 20 | 0.276 | 2 |

### Silhouette scores

| Algorithm | Silhouette |
| --- | --- |
| agg_k3 | 0.173 |
| agg_k5 | 0.188 |
| agg_k7 | 0.179 |
| kmeans_k3 | 0.177 |
| kmeans_k5 | 0.135 |
| kmeans_k7 | 0.203 |

Trades clustered: 50
Features used: 22

## 2. Early Prediction Accuracy (bar 10 cutoff)

**Label:** EXPLOSIVE — pnl_r > 0 AND mfe_so_far >= 1.0R within bar 15
**Features:** bars 1–10 only

| Model | Accuracy | Precision | Recall | F1 | ROC-AUC |
| --- | --- | --- | --- | --- | --- |
| logistic_regression | 0.970 | 1.000 | 0.833 | 0.909 | 1.0 |
| random_forest | 0.939 | 1.000 | 0.667 | 0.800 | 1.0 |

Samples: 107 (positive: 19, negative: 88)

## 3. Feature Importance Ranking

| Rank | Feature | Importance |
| --- | --- | --- |
| 1 | unrealized_r_bar10 | 0.2472 |
| 2 | mfe_so_far_bar10 | 0.2202 |
| 3 | delta_r_mean_early | 0.1656 |
| 4 | mae_so_far_bar10 | 0.0870 |
| 5 | imbalance_z | 0.0746 |
| 6 | bd_distance_pct | 0.0494 |
| 7 | mfe_velocity_bar10 | 0.0388 |
| 8 | cascade_strength | 0.0387 |
| 9 | delta_r_std_early | 0.0317 |
| 10 | mae_velocity_bar10 | 0.0185 |
| 11 | bars_observed | 0.0154 |
| 12 | confirmations_count | 0.0066 |
| 13 | decile | 0.0061 |
| 14 | imb_fallback_flag | 0.0000 |

## 4. Pathway Separability

| Pathway | N | WR | Expectancy | Cohen's d vs complement |
| --- | --- | --- | --- | --- |
| MFE>=0.3R within 10 bars | 66 | 57.6% | +0.155R | 0.81 |
| mae_recovery>=1.0R (post-hoc) | 70 | 68.6% | +0.271R | 1.15 |

### Separability on observables

| Pathway | d(pnl_r) | d(max_mfe) | d(max_mae) |
| --- | --- | --- | --- |
| confirmed_mfe_0_3r_within_10_bars | 0.8112868612796621 | 1.4852938143052765 | None |
| high_recovery_gt_1r_posthoc | 1.1505632573199132 | 1.846513713719348 | None |

## Data Provenance

- Inputs: `r_path_long.parquet`, `trade_tensor.parquet`, `trade_features.parquet` (Phase 2, unmodified)
- Phase 2 outputs were not modified by this analysis.
