# Exit-Rule Simulator (read-only)

Replays each trade's bar-by-bar r_path against alternative exit rules. Baseline = actual realized pnl_r. Bar-close approximation; intrabar fills not modeled. Observational only — confirm via live shadow-exit logging before any engine change.

## Cohort `post_gate` (N=122)

| Exit rule | N | WR | total R | R/day | uplift vs actual |
| --- | --- | --- | --- | --- | --- |
| baseline (actual) | 122 | 32% | -31.93R | -5.25R |  |
| breakeven_after_0.5R | 122 | 29% | -24.36R | -4.01R | +7.57R |
| breakeven_after_1.0R | 122 | 31% | -32.27R | -5.31R | -0.34R |
| trail_0.75R | 122 | 34% | -28.18R | -4.63R | +3.75R |
| trail_1.0R | 122 | 32% | -32.87R | -5.41R | -0.94R |
| partial50_at_1.0R | 122 | 33% | -33.52R | -5.51R | -1.59R |
| confirm10_cut_only | 122 | 34% | -22.11R | -3.64R | +9.82R |
| confirm10_cut_trail0.75 | 122 | 33% | -6.65R | -1.09R | +25.28R |
| confirm10_cut_trail1.25 | 122 | 31% | -10.91R | -1.79R | +21.02R |
| confirm8_cut_trail1.0 | 122 | 30% | -7.64R | -1.26R | +24.28R |

### Out-of-sample (`post_gate`)

Best rule on earliest 73 trades: **confirm10_cut_trail0.75**. Applied to most recent 49:

| | N | WR | R/day | total R |
| --- | --- | --- | --- | --- |
| Actual exits | 49 | 18% | -16.39R | -23.10R |
| **confirm10_cut_trail0.75** | 49 | 20% | -6.54R | -9.22R |

**OOS uplift: +13.89R (+9.85R/day).**

_Confirm with live shadow logging before changing the engine._

## Cohort `all_r_path` (N=237)

| Exit rule | N | WR | total R | R/day | uplift vs actual |
| --- | --- | --- | --- | --- | --- |
| baseline (actual) | 237 | 32% | -75.74R | -7.91R |  |
| breakeven_after_0.5R | 237 | 26% | -65.71R | -6.86R | +10.02R |
| breakeven_after_1.0R | 237 | 31% | -75.86R | -7.92R | -0.12R |
| trail_0.75R | 237 | 33% | -71.21R | -7.43R | +4.53R |
| trail_1.0R | 237 | 32% | -76.68R | -8.00R | -0.94R |
| partial50_at_1.0R | 237 | 32% | -74.00R | -7.72R | +1.74R |
| confirm10_cut_only | 237 | 28% | -52.65R | -5.50R | +23.08R |
| confirm10_cut_trail0.75 | 237 | 25% | -23.82R | -2.49R | +51.92R |
| confirm10_cut_trail1.25 | 237 | 23% | -29.11R | -3.04R | +46.63R |
| confirm8_cut_trail1.0 | 237 | 25% | -26.16R | -2.73R | +49.58R |

### Out-of-sample (`all_r_path`)

Best rule on earliest 142 trades: **confirm10_cut_trail0.75**. Applied to most recent 95:

| | N | WR | R/day | total R |
| --- | --- | --- | --- | --- |
| Actual exits | 95 | 28% | -5.63R | -27.58R |
| **confirm10_cut_trail0.75** | 95 | 31% | -2.40R | -11.77R |

**OOS uplift: +15.81R (+3.23R/day).**

_Confirm with live shadow logging before changing the engine._

