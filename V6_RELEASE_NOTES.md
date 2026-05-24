# V6 Release Notes

## V6.1 (2026-05-23)
- Reverted aggression score mapping from [-3,+3] to [-2,+2] (original frozen params)
  - V6.0 widening caused D1 flood: 61% of trades were D1, net -5.44R
  - Reverted D1 to 20 trades at -0.257R avg → breakeven
- Added `min_cascade_imb = 0.30` — filters liquidation cascades with neutral imbalance
  - NEAR (imb=0.05-0.07): filtered out, NEAR churn eliminated
  - SOL/BNB/DASH (imb=0.86-0.99): pass through

## V6.2 (2026-05-24)
- Reject D3 (added to D4/D10 rejection list)
  - 6 D3 trades: -3.91R total, none with MFE > 0.61R
  - All were -1R stop-losses or time-stops near zero
  - Without D3: 44 trades, +1.69R, WR 66%
- D1-D2 require directional confirmation
  - Must pass imb_z OR vol_z (taker aggression or volume surge)
  - Pure momentum-breakout entries no longer qualify for low-aggression deciles
- Early-cut dead trades
  - At decay_start_bar: if R < -0.3 AND MFE < 0.3 → exit immediately
  - Stops 5-8 trades/50 from bleeding from -0.5R to -0.9R with zero recovery
  - Expected savings: ~+1.5R per 50 trades (+0.03R/trade expectancy)
## V6_VERSION_NOTES
git add V6_RELEASE_NOTES.md && git commit -m "Add V6 release notes" && git tag v6.2-docs && echo "DONE"
