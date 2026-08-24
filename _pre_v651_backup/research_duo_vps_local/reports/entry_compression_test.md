# Entry Compression Test

Single measurement: does a latent participation axis separate EXPLOSIVE vs EARLY_DEAD at entry?
Read-only. No threshold tuning. Pre-registered features only.

## Re-run trigger

Re-run when **post_gate closed trades ≥ 100** (currently **122**, need **+0**).

```bash
PYTHONPATH=. python3 -m research_duo compression
```

Do not expand analytics until this threshold is hit. Current post_gate window is short; treat verdicts below as directional only.

## Primary verdict (`post_gate`): **KILLED**

Logistic axis AUC < 0.58 — no meaningful EXPLOSIVE vs EARLY_DEAD separation.

N=79 (EXPLOSIVE=20, EARLY_DEAD=59)

| Axis | Cohen's d | AUC | Overlap |
| --- | --- | --- | --- |
| pca1 | +0.19 | 0.549 | 82% |
| logistic | +0.37 | 0.579 | 76% |
| vol_z | +0.23 | 0.551 | — |

PC1 variance explained: 46.5%

**PC1 loadings:**
- `vol_z`: +0.558
- `confirm_vol`: +0.506
- `aggression_score`: +0.475
- `bd_distance_pct`: +0.403
- `confirm_impulse`: -0.193
- `confirm_momentum`: -0.086

## Confirmatory (`all_r_path`)

**KILLED** — N=108 (EXP=26, DEAD=82); logistic AUC=0.556, vol_z AUC=0.521, d_logistic=+0.25

## Expectancy & Kelly (`post_gate`)

Kelly: `f* = (p·b − q) / b` where `b = avg_win/avg_loss`, `p = WR`. Assumes 1R fixed risk per trade. Half-Kelly shown for sizing sanity.

| Cohort | N | WR | R/day | Avg R | Kelly | ½ Kelly |
| --- | --- | --- | --- | --- | --- | --- |
| All post_gate | 122 | 32.0% | -5.25R | -0.262R | 0.0% | 0.0% |
| High vol_z (≥0.09) | 61 | 37.7% | -1.38R | -0.125R | 0.0% | 0.0% |
| Low vol_z (<0.09) | 61 | 26.2% | -4.00R | -0.398R | 0.0% | 0.0% |
| Oracle: EXP+SURV only | 39 | 100.0% | +7.07R | +1.007R | — | — |
| Oracle: drop EARLY_DEAD | 63 | 61.9% | +3.95R | +0.359R | 35.7% | 17.8% |
| Projection: modest filter * | — | 57.5% | +1.50R | +0.214R | 21.3% | 10.6% |
| Projection: strong filter * | — | 67.5% | +4.00R | +0.401R | 39.8% | 19.9% |
| Projection: oracle ceiling * | — | 100.0% | +9.50R | +1.007R | — | — |

\* Projections use observed post_gate win/loss asymmetry (avg win +1.007R, avg loss +0.858R) with illustrative WR/R/day — not realized.

**Eyes on the ball:** negative Kelly = no edge at that WR/payoff. Do not size from 3-day stats or projections until N≥100 re-run confirms.

## Out-of-sample entry-score filter (`post_gate`)

Train on earliest 60% (entry-time order), test on most recent 40%. No future labels used. Standardization fit on train only.

OOS test set: N=49 over 1.4d. OOS separability AUC = 0.460.

| Strategy | N | WR | total R | R/day |
| --- | --- | --- | --- | --- |
| Test: take all | 49 | 18% | -23.10R | -16.39R |
| Test: top 50% by entry score | 24 | 17% | -12.65R | -8.97R |
| Test: top 33% by entry score | 16 | 6% | -10.08R | -7.15R |
| Test: ORACLE drop EARLY_DEAD * | 20 | 45% | +3.88R | +2.75R |

\* Oracle uses post-hoc labels (not tradeable) — shown only as the ceiling.

**Read:** if 'top X%' R/day clears 'take all' AND approaches the oracle, the entry score has live-usable signal. If 'top X%' ≈ 'take all', the score is noise out-of-sample — do not filter on it.
