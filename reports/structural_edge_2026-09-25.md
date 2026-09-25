# Structurally bigger edge: first candidate + how to find more (2026-09-25)

Owner ask: "figure out the structurally bigger edge, or how to find it."

## Why the current system can't get there
- **Edge vs costs:** gross edges are about +0.02–0.09 R/leg and costs 0.07–0.2 R/leg (5m-ATR stops, fixed bps). Before
  costs the edge is smaller than the costs.
- **Correlation:** legs inside a cascade are one bet (48% of leg-R variance is shared within the hour).
- **Evidence:** live E +0.021 R/leg, t by day +0.37. At that size, about 257 trading days are needed to prove it. All
  live trades since Jul-22: −0.028 R/leg.
- **The mechanism that did hold in our own data:** market-wide forced selling bounces (NY broad flushes,
  +0.085/leg). Narrow, symbol-specific flushes don't.

## Candidate: market-capitulation basket buy
- **Rule (fixed before testing):**
  - Universe: 20 liquid USDT-M perps (BTC ETH SOL XRP ADA DOGE LINK AVAX DOT LTC BCH ATOM NEAR UNI FIL ETC TRX BNB XLM AAVE).
  - Each coin's hourly log return is z-scored against its trailing 30-day hourly sd.
  - **Event:** ≥ 75% of coins with z ≤ −3 in the same hour, with a 24h cooldown.
  - **Trade:** buy the equal-weight basket at the next hour's open, hold 24h. Costs 20 bps round trip.
- **In-sample (2022-01 → 2026-09, 121 events, ~26/yr):**
  - net **+1.10%/event**, median +0.76%, hit 58%, **t +2.28**; ordinary 24h +0.05%
  - positive every year: 2022 +0.97, 2023 +1.60, 2024 +1.31, 2025 +1.29, 2026 +0.13 (weak)
  - smooth across settings: thresholds 60–90% × holds 24–48h all positive (+0.5..+1.7%); 70–75% × 24–48h t ≈ 1.9–2.3
  - 40 bps costs: +0.90%
  - BTC above / below its 200d SMA: +1.33% / +0.85%
  - BTC alone is weaker (+0.50%, t 1.5). The alt basket overshoots more.
- **Out-of-sample (2020–21, never seen, frozen rule, 37 events):**
  - net **+2.73%/event**, median +2.97%, hit 62%, t +1.77; ordinary 24h +0.57%
  - Lumpy: 2020 +0.47% (below the control), 2021 +6.91% (13 events); top-5 events ≥ 100% of net.
- **Risks, disclosed:**
  - Top-5 events carry 59% of in-sample net; ex-top-5 +0.48%.
  - Fat left tail: worst 24h −17.6%. The worst intraday basket drawdown was **−49.5%**, almost certainly the
    Oct-2025 flash-crash wicks.
  - The 2025–26 holdout is weaker (+0.73%, t 0.99).
  - About 18 threshold/hold/instrument combinations were looked at in-sample. The OOS run was one frozen test.
- **Why it's structurally bigger:**
  - Each trade targets about 1% against 0.2% costs (5–6×). The current system's costs are larger than its gross edge.
  - One position per market event instead of up to 8 correlated legs.
  - It rests on a mechanism (forced-selling overshoot) and is testable on years of public data, not weeks of shadow data.
- **Sizing implication:** it's a liquidity-provision trade with a fat tail. Size by basket notional (≤ about 0.5×
  equity), with no tight stops (they'd fill at wick lows). Per-event sd is about 5%.

## How to find more edges like it (the method that produced this)
1. **Mechanism first.** Forced flows (liquidations, funding or crowding extremes, OI flushes), illiquidity (weekends,
   session gaps), index or BTC→alt lead-lag. One definition per idea, written down before testing.
2. **Event-level, long history, public data.** Klines back to 2019–20, funding history. Liquidations only go back to
   Jul-2026 in our DB, so use price or volume proxies and confirm on the feed era.
3. **Costs built in; demand gross ≥ 3× costs.** Anything thinner dies in live execution.
4. **Statistics by event or day, never by leg.** Report n events, t, every year, top-5 share, the ex-top mean and the
   worst case.
5. **Discover on one period, test once on another** (e.g. 2022–24 → 2020–21 and 2025–26). No tuning after the
   holdout.
6. **Paper-forward before live.** Wire only on a pre-registered bar, never on the day of a loss.

## Proposed next steps (owner decides)
- **Register** the capitulation basket buy as a prereg (frozen rule above). Build a paper tracker: an hourly event
  detector over the 20 coins plus basket P&L. Expect ~2 events a month.
- **Extend the tests:** 15m resolution (faster entry), actual slippage during our feed-era cascades, and a
  wide disaster-stop variant.
- **Next hypotheses on the same method:** squeeze-follow (the fade lost −1.03%, so continuation is the testable
  flip; OOS on 2020–21 only), funding-extreme reversals, OI-flush capitulation, weekend-gap reversion.

## Tested after registration: squeeze continuation. NOT registered.
The rule was fixed before testing: ≥ 50% of coins at hourly z ≥ +3 in the same hour, 24h cooldown, buy the basket at
the next open, 20 bps.
- 2022–26 (already seen through the fade test): 24h +0.63% (t +1.13), 72h +0.74% (t +1.11). Weak.
- 2020–21 OOS (unseen): 24h **+3.73%** (n=19, t +1.64) vs ordinary +0.57%, but the top-5 events are 120% of net.
  72h +3.27% (t +1.96) vs ordinary +1.72%.
- Read: bull-market momentum (2020–21) rather than a general edge. A small, concentrated OOS and a weak in-sample.
  Any bull-conditional version would have no clean holdout left, so it could only be judged forward. Parked.
