# V5 System Improvement Analysis

## Current State
- **Engine**: LiqClusterEngineV5 — liq-cluster cascade → 4/6 confirmation → per-decile exits
- **Symbols**: 35 (21 tier_a + 14 tier_b)
- **Config**: D1-D3,D5-D9 | flat 4% | vol-targeting | no BTC alignment | require_short_squeeze=False
- **Live**: 11 trades, +0.62R, 63.6% WR, all NEAR
- **Backtest** (283 trades, Jan-May 2026): +118.77R, 60.8% WR, PF 2.15

## Improvement Opportunities (Evidence-Based)

### 1. EXCLUDE D3 (HIGH CONFIDENCE ✅)
**Evidence**: D3 is the only decile with negative expectancy
- D3: 42 trades, -4.37R, 48% WR, avg -0.104R/trade
- D7 is also weak: 25 trades, +1.66R, 52% WR, avg +0.066R/trade
- D1/D2/D5/D8/D9 are the strong deciles

**Expected impact**: Removing D3 should improve PF and reduce losing trades.
Removing D3+D7 should further clean up the tail.

**Status**: Backtest running to confirm exact numbers.

### 2. INCREASE CONSECUTIVE STOPS TOLERANCE (MEDIUM CONFIDENCE)
**Evidence**: Max consecutive loss streak is 7, but system pauses after 3
- 3 consecutive stops → pause until new cascade (stop_cooldown=999999)
- With 7-loss streaks occurring naturally, the pause is cutting off recovery
- Increasing to 5 would allow more natural variance while still catching true degradation

**Expected impact**: Fewer missed recovery trades after bad streaks.

**Status**: Backtest running.

### 3. TIGHTEN RET5D_MIN (MEDIUM CONFIDENCE)
**Evidence**: ret5d_min=-5% allows entries on symbols with strong 5d downtrends
- Many stop_losses come from entries where price is already falling
- Setting to 0% would filter out downtrending symbols
- Risk: might filter out valid reversal setups

**Expected impact**: Fewer stop_losses, higher WR, possibly fewer total trades.

**Status**: Backtest running.

### 4. LONGER COOLDOWN (LOW CONFIDENCE)
**Evidence**: 36 candles (3h) cooldown after exit
- Live data shows NEAR getting re-entered within hours after stop_loss
- 72 candles (6h) would reduce chop exposure
- Risk: might miss fast recovery moves

**Expected impact**: Fewer re-entries into choppy ranges.

**Status**: Backtest running.

### 5. COINALYZE 429 RATE LIMITING (HIGH CONFIDENCE ✅)
**Evidence**: Startup hits 429s on multiple symbols even with batching
- Current: batches of 5 with 5s inter-batch delay
- Still gets rate-limited on 3-4 symbols per batch
- Solution: increase inter-batch delay to 15s, reduce batch size to 3

**Expected impact**: Reliable startup without 429 retries.

### 6. SYMBOL EXCLUSION BASED ON LIVE PERFORMANCE (MEDIUM CONFIDENCE)
**Evidence**: Some symbols consistently lose in backtest
- ADA: 7 trades, -3.47R, 29% WR
- RENDER: 5 trades, -3.03R, 40% WR
- ETH: 5 trades, -2.17R, 40% WR
- WLD: 17 trades, -2.81R, 41% WR
- These 4 symbols alone lost -11.48R over 5 months

**Expected impact**: Removing 4 worst symbols could improve net R by ~10R/5mo.

### 7. STRUCT_TRAIL UNDERUTILIZATION (OBSERVATION)
**Evidence**: Only 1 of 283 backtest trades exited via struct_trail
- struct_trail uses swing low lookback (decile-specific: 8-48 candles)
- The vol_trail (3% of entry) is catching almost everything
- struct_trail might need tuning or the vol_trail is simply better for this market

**Status**: Needs investigation.

### 8. EXIT REASON DISTRIBUTION
**Evidence**: 66% vol_trail (91% WR), 33% stop_loss (0% WR)
- 94 stop_losses at avg -1.089R each = -102.35R total
- If we could convert even 10% of stop_losses to vol_trail exits, that's +10R
- This is where the real money is: better entries that don't hit stops

## Backtest in Progress
Running 8 configurations on top 15 symbols:
1. BASELINE (current)
2. NO_D3
3. NO_D3_D7
4. STOPS_5
5. RET5D_0
6. COOLDOWN_72
7. COMBO (NO_D3 + STOPS_5 + RET5D_0)
8. COMBO (NO_D3_D7 + STOPS_5 + RET5D_0)

Results will be in backtest_output/param_sweep_results.txt
