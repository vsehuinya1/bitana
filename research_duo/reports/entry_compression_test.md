# Entry Compression Test

Single measurement: does a latent participation axis separate EXPLOSIVE vs EARLY_DEAD at entry?
Read-only. No threshold tuning. Pre-registered features only.

## Re-run trigger

Re-run when **post_gate closed trades ≥ 100** (currently **82**, need **+18**).

```bash
PYTHONPATH=. python3 -m research_duo compression
```

Do not expand analytics until this threshold is hit. Current post_gate window is short; treat verdicts below as directional only.

## Primary verdict (`post_gate`): **INCONCLUSIVE**

Separation present but weak or single-feature dominated; sample too small for firm call.

N=50 (EXPLOSIVE=18, EARLY_DEAD=32)

| Axis | Cohen's d | AUC | Overlap |
| --- | --- | --- | --- |
| pca1 | +0.08 | 0.528 | 57% |
| logistic | +0.45 | 0.611 | 84% |
| vol_z | +0.17 | 0.547 | — |

PC1 variance explained: 56.6%

**PC1 loadings:**
- `vol_z`: +0.552
- `aggression_score`: +0.504
- `confirm_vol`: +0.495
- `bd_distance_pct`: +0.403
- `confirm_impulse`: -0.182
- `confirm_momentum`: +0.000

## Confirmatory (`all_r_path`)

**INCONCLUSIVE** — N=79 (EXP=24, DEAD=55); logistic AUC=0.598, vol_z AUC=0.530, d_logistic=+0.33

## Expectancy & Kelly (`post_gate`)

Kelly: `f* = (p·b − q) / b` where `b = avg_win/avg_loss`, `p = WR`. Assumes 1R fixed risk per trade. Half-Kelly shown for sizing sanity.

| Cohort | N | WR | R/day | Avg R | Kelly | ½ Kelly |
| --- | --- | --- | --- | --- | --- | --- |
| All post_gate | 82 | 41.5% | -1.77R | -0.108R | 0.0% | 0.0% |
| High vol_z (≥0.25) | 41 | 43.9% | -0.82R | -0.091R | 0.0% | 0.0% |
| Low vol_z (<0.25) | 41 | 39.0% | -1.02R | -0.125R | 0.0% | 0.0% |
| Oracle: EXP+SURV only | 34 | 100.0% | +7.57R | +1.008R | — | — |
| Oracle: drop EARLY_DEAD | 50 | 68.0% | +4.45R | +0.415R | 41.2% | 20.6% |
| Projection: modest filter * | — | 57.5% | +1.50R | +0.197R | 19.6% | 9.8% |
| Projection: strong filter * | — | 67.5% | +4.00R | +0.388R | 38.5% | 19.3% |
| Projection: oracle ceiling * | — | 100.0% | +9.50R | +1.008R | — | — |

\* Projections use observed post_gate win/loss asymmetry (avg win +1.008R, avg loss +0.899R) with illustrative WR/R/day — not realized.

**Eyes on the ball:** negative Kelly = no edge at that WR/payoff. Do not size from 3-day stats or projections until N≥100 re-run confirms.

## Out-of-sample entry-score filter (`post_gate`)

Train on earliest 60% (entry-time order), test on most recent 40%. No future labels used. Standardization fit on train only.

OOS test set: N=33 over 2.9d. OOS separability AUC = 0.402.

| Strategy | N | WR | total R | R/day |
| --- | --- | --- | --- | --- |
| Test: take all | 33 | 33% | -9.78R | -3.37R |
| Test: top 50% by entry score | 16 | 25% | -6.54R | -2.26R |
| Test: top 33% by entry score | 10 | 30% | -2.92R | -1.01R |
| Test: ORACLE drop EARLY_DEAD * | 16 | 69% | +5.52R | +1.90R |

\* Oracle uses post-hoc labels (not tradeable) — shown only as the ceiling.

**Read:** if 'top X%' R/day clears 'take all' AND approaches the oracle, the entry score has live-usable signal. If 'top X%' ≈ 'take all', the score is noise out-of-sample — do not filter on it.
