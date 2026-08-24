# Daily Hypothesis Tracker — Bitana V5/V6

**Purpose:** Record testable hypotheses with clear success/failure criteria. Updated daily from shadow/live data. Each entry gets ✅ CONFIRMED, ❌ DISPROVED, or 🔄 INCONCLUSIVE.

---

## Format
```
### [DATE] Hypothesis: <one-line claim>
- **Criteria**: <measurable threshold, e.g. "avg ATR > 0.5 over n≥20">
- **Source**: <shadow DB query / live trade / backtest>
- **Status**: ✅ CONFIRMED | ❌ DISPROVED | 🔄 INCONCLUSIVE
- **Evidence**: <numbers: n, avg, WR, date range>
- **Action**: <what changes: deploy / kill / retune / wait>
```

---

## Active Hypotheses (Updated Daily)

### 2026-08-21 Hypothesis: London bull `burst_follow` LONG (h8-13, 10 ATR SL / 3 ATR TP / 30-min time) has live edge > +0.2 R/trade
- **Criteria**: G0 forward-test. After 30 live-accepted: net/trade ≥ +0.2R AND no top-day > 40% of live P&L. Kill otherwise.
- **Source**: `signal_shadow.db` closed set, h8-13, bull, LONG: +32.0R/83, +0.385/trade, WR 64%, 6/8 days pos, Aug OOS +0.29R/trade. Exit mix 92% time / 8% TP / 0 stops.
- **Status**: 🔄 INCONCLUSIVE (wired live Aug 21)
- **Evidence**: Known fragility declared up front: Jul 16 = 57% of shadow P&L; would_live_accept subset n=27 −6.0R (thin, Jul23+). `london_burst_fade` rejected (edge 100% in 30 TP hits; time-exits −32.7R; h08+Jul15 double concentration). `follow_3h/6h_london`: zero bull trades.
- **Action**: Wired to `live_burst_ny_asia.yaml` as `london` rule (bull only, pos_imb ≥ 0.5, no weekends, min_decile 1 — d1 is best bucket). Review at 30 accepted or Sep 21, whichever first.

### 2026-08-21 Hypothesis: NY bull hours [14,16,17,19] beats [14,15,17]
- **Criteria**: h15 removal costs nothing (it was −1.5R avg, 0/3 days pos); h16 addition ≥ 0 over n≥10 live-accepted. h17 stays under existing kill criteria. h19: if live-accepted n≥10 with net/trade < 0 → drop h19 (revert to [14,16,17]).
- **Source**: `signal_shadow.db` ny_flush_buy_4h bull LONG by hour: h14 +0.92/30, h15 −1.50/5 (0/3 days), h16 +1.18/6 (2/2 days), h17 +5.25/10 (Aug19 = 110% of total), h18 −0.02/12, h19 +13.4R/9 (+1.49R avg, 4/5 days).
- **Status**: 🔄 INCONCLUSIVE (wired live Aug 21)
- **Evidence**: h15 negative on every day it fired. h16 positive both days but all-July data. h18 flat → excluded. h19 consistent but thin (n=9) — promoted same day on user decision; accepted risk: Jul concentration similar to h16/h17 fragility.
- **Action**: `regime_hours.bull: [14,16,17,19]` live (Aug 21 later — h19 promoted from watchlist same day). Neutral window unchanged [16,17] @ 10 ATR; bull @ 8 ATR.

### 2026-07-26 Hypothesis: `late_fade` (weekend neutral, 6h time-exit) has live edge > +0.5 ATR/trade
- **Criteria**: Live-filtered (D1,2,5-9; D1/2 need v_confirms3=1) avg PnL > +0.5 ATR over n≥20 trades
- **Source**: `signal_shadow.db` weekend neutral late session
- **Status**: 🔄 INCONCLUSIVE
- **Evidence**: n=9, avg +0.77 ATR, WR 67%, total +6.91 ATR (Jul 11, 12, 19, 25). One D1 outlier (+3.0 ATR) drives PnL. Tonight Jul 25: ETHUSDT LONG −1.70 ATR (D1, no confirms).
- **Action**: Wait for n≥30 (3+ more weekends). Do NOT add to live config yet.

### 2026-07-25 Hypothesis: `fade_6h_late` weekend neutral is a D10 strategy — dead under live decile filter
- **Criteria**: Live-filtered expectancy ≤ 0
- **Source**: `signal_shadow.db` weekend neutral late session
- **Status**: ✅ CONFIRMED
- **Evidence**: Raw n=28, +2.41 ATR, 75% WR. Live-filtered n=4, **−1.12 ATR**, 25% WR. Entire edge was D10 (dropped by live engine `TRADE_DECILES = {1,2,5,6,7,8,9}`).
- **Action**: Never deploy `fade_6h_late` live. Archive.

### 2026-07-25 Hypothesis: BTC regime persistence — neutral holds through weekend
- **Criteria**: `btc_trend_state` remains "neutral" Fri 14:00 UTC → Mon 00:00 UTC
- **Source**: `btc_regime` logs, `signal_shadow.db` btc_trend_state
- **Status**: 🔄 INCONCLUSIVE (weekend in progress)
- **Evidence**: Fri 09:31 UTC — neutral, dist_pct 0.21%, bars 249 (9+ days). Regime changes rare on weekends without catalyst.
- **Action**: Monitor. If regime flips bull → Asia gate blocks, NY gate passes. If bear → both pass.

### 2026-07-24 Hypothesis: NY `ny_flush_buy_4h` (pos_imb≥0.5, LONG, 4h time-exit) works in neutral/bear regime
- **Criteria**: Live trades (paper) avg R > 0 over first 20 trades
- **Source**: Live `bitana-live-burst.db`, config `live_burst_ny_asia.yaml`
- **Status**: 🔄 INCONCLUSIVE
- **Evidence**: Jul 24 NY — 3 trades filled (NEAR LONG, WLD LONG x2). Too early. Shadow shows Asia `neg_imb` SHORT edge in neutral (+0.78 ATR, n=71). NY shadow data limited.
- **Action**: Track live NY trades. Need 20+ trades for signal.

### 2026-07-24 Hypothesis: Monday NY session is toxic for burst follow (long flush)
- **Criteria**: Monday NY live PnL < −2R over 4+ weeks
- **Source**: Fable analysis Jul 15 (2 weeks): Monday NY −48.4 ATR
- **Status**: 🔄 INCONCLUSIVE
- **Evidence**: Only 2 Mondays in live data (Jul 7, 14). Both negative. Small sample.
- **Action**: Keep Monday NY blackout in config (`exclude_weekdays: [0]`). Re-evaluate at n≥10 Mondays.

### 2026-07-23 Hypothesis: TSL (trailing stop) underperforms 4h time-exit for burst follow
- **Criteria**: TSL avg R < time-exit avg R across sessions
- **Source**: Fable Jul 15 analysis (571 trades Jul 8-15)
- **Status**: ✅ CONFIRMED
- **Evidence**: Asia TSL +6.1 vs time-exit +53.7 ATR. NY TSL +0.8 vs time-exit +8.9 ATR. TSL kills winners early.
- **Action**: Config uses 4h time-exit only. No TSL.

---

## Resolved / Archived

### 2026-07-15 Hypothesis: Bear gate (BTC regime bear) improves Asia short edge
- **Criteria**: Asia short WR higher in bear vs neutral
- **Status**: ❌ DISPROVED
- **Evidence**: BTC flipped bull Jul 15. Asia edge persisted in **neutral** (+0.78 ATR, n=71). Bear gate was wrong filter.
- **Action**: Retune gates — Asia: neutral+bull, NY: neutral+bear. Implemented in `live_burst_ny_asia.yaml`.

### 2026-07-15 Hypothesis: `would_live_accept` shadow field correctly predicts live acceptance
- **Criteria**: `would_live_accept=1` trades match live filter pass rate
- **Status**: ❌ DISPROVED
- **Evidence**: Field checks shadow book, not live order book. Broken — ignores live decile filter and v_confirms3.
- **Action**: Ignore `would_live_accept`. Use manual decile/v_confirms3 filter in queries.

---

## Tracking Template (Copy for New Entries)

```
### [YYYY-MM-DD] Hypothesis: <claim>
- **Criteria**: <measurable>
- **Source**: <data source>
- **Status**: 🔄 INCONCLUSIVE
- **Evidence**: <numbers>
- **Action**: <next step>
```

---

## Daily Update Protocol (Run at 06:00 UTC via Cron)

1. Pull latest shadow trades from `signal_shadow.db` (last 24h + weekend accumulation)
2. Pull live trades from `bitana-live-burst.db` (last session)
3. Update each active hypothesis: Status, Evidence, Action
4. Add new hypotheses from overnight observations
5. Archive resolved (✅/❌) to bottom
6. Commit to git: `git add references/daily_hypothesis_tracker.md && git commit -m "daily hypothesis update $(date -u +%F)"`

---

## Quick Reference: Live Filters (Hardcoded in `liq_cluster_engine_v5.py:92`)

```python
TRADE_DECILES = {1, 2, 5, 6, 7, 8, 9}  # Drops D3, D4, D10
# D1/D2 require v_confirms3=1 (imb OR vol confirmation)
```

**Always apply these filters to shadow queries before claiming "live edge."**

---

## Current Live Config: `live_burst_ny_asia.yaml`

| Session | Engine | Side | Imb Gate | BTC Gate | Exit |
|---------|--------|------|----------|----------|------|
| Asia (00-08) | `asia_pump_sell_4h` | SHORT | neg_imb ≤ -0.5 | neutral only | 4h time |
| London (08-14) | `burst_follow` | LONG | pos_imb ≥ 0.5 | bull only (G0 Aug 21) | 30-min time / TP 3 ATR / SL 10 ATR |
| NY (14-22) | `ny_flush_buy_4h` | LONG | pos_imb ≥ 0.5 | neutral [16,17] / bull [14,16,17,19] @ 8 ATR | 4h time |
| Late | — | — | — | — | — |

**Max positions**: 3 total, 1 per symbol | **Risk**: 0.5%/trade | **Portfolio DD limit**: 35% | **Daily loss limit**: 20%