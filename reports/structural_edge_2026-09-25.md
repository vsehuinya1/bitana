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

## Tested: funding extremes. No independent edge.
Rules were fixed before testing. Market-wide 8h funding (20 coins) at its trailing 180d p95 / p5, plus per-coin
funding z ≥ 2.5 vs the coin's own 180d. Fade the crowd, hold 24/72h, 20 bps, funding carry counted. 2020–21 / 2022–24 /
2025–26 reported separately.
- **Crowded longs → short (contrarian): loses.** Market 72h −1.51% (t −2.20, negative in all 3 periods). Per coin
  −2.03% per trade (t −2.65). The +0.26–0.55% of funding collected doesn't cover the rally.
  **Crowded longs are a momentum signal; fading them loses.**
- **Crowded shorts → buy:** market-wide inconsistent (24h 2020–21 −1.05 / 2022–24 +1.01 / 2025–26 +0.14%). Per coin
  +0.64% (t +2.98 by day), but that is carried by 2020–21 (+6.3% vs +1.7% drift). 2022–24 excess ≈ +0.2%, 2025–26 ≈ +0.9%
  (t ≈ 1.4). 36% of legs fall within 72h after a capitulation event, which the capitulation basket already captures.

## Tested: OI flushes (owner's DBs). No edge, and no help to the capitulation basket.
Data: `/root/hermes_lab/data/coinalyze_oi.db`, daily OI (in coins) for BTC/ETH/SOL/XRP, 2023-05 → 2026-02. Hourly OI
(`oi_live.db`, `coinalyze_oi.db`) spans only 4–7 months.
- **Standalone** (4-coin OI z ≤ −2 & price down, next-day entry): 17 events. 1d −0.83% (t −0.91); 3d +0.44% vs ordinary +0.47%.
- **Capitulation events split by same-day OI change** (median −2.5%; mechanism check only, not tradable):
  deleveraging (mean OI −7.8%) +1.46% (n=33) vs OI held/rose +1.29% (n=33). OI adds nothing to the price-breadth rule.

## Mechanism check for PREREG-CAPITULATION-BASKET (Coinalyze hourly liquidations, 28 coins, 2025-12 → 2026-05)
- All 11 capitulation events in the window were long-liquidation cascades: 9× to 2,546× the median hour's long
  liquidations, 8/11 above the 92nd percentile, long:short liquidations 8:1 to 7,216:1.
- The price-breadth rule identifies forced selling without needing a liquidation feed.

## Execution realism for PREREG-CAPITULATION-BASKET (in-sample events, 2022-01 → 2026-09)
- **Entry delay** (24h hold from entry): 0h +1.10% (t 2.28) → 1h +0.83% → 2h +0.76% → 4h +0.39% (t 0.83). The edge is
  the immediate rebound, so entry must be automated at the hour close.
- **Basket:** 20 coins +1.10% (20 bps) · **5 majors (BTC ETH SOL XRP BNB) +1.08%** (15 bps, t 2.27, worst −24.3%) · BTC only
  +0.50%. The 5-major basket matches the return and fills far more easily in a crash.
- **Disaster stop** (basket, hourly lows): −10% +0.47% (12 hits) · −15% +0.89% · **−20% +0.98% (2 hits)** · none +1.10%.
  Tight stops sell the bottom. Only a wide −20% catastrophe stop is cheap.
- **First-minutes slippage** (1m klines, all 121 events × 20 coins):
  - buying evenly over 5 minutes vs the 1h open: mean −3.0 bps, median −3.0, p90 +46
  - worst case (first-minute high): mean +27, median +16, p90 +66
  - 5 majors: 5-min mean −1.4 bps (p90 +48)
  - **Net with the 5-min fill and 25 bps costs: +1.08%/event (t 2.25).** The edge survives realistic execution.
- **Live design implied (for the promote decision):** automated 5-min entry after the event hour closes, 5 majors,
  notional sizing, −20% basket catastrophe stop, 24h hold.

## Tested: weekend-gap reversion. No edge (the sign flips by period).
Rule fixed before testing: weekend move = Fri 21:00 → Sun 22:00 UTC (CME close → reopen), BTC and the 20-coin basket.
Continuous test = correlation with the next 24h/72h; event test = |z| ≥ 2 vs the trailing 26 weekends, faded from Sun 22:00.
- **Continuous:** BTC r24 2020–21 −0.19 · **2022–24 +0.37** · 2025–26 −0.29 (all −0.03). Basket −0.22 / **+0.26** / −0.27
  (all −0.07). Reversion in two periods, continuation in the middle one. Regime-dependent, so not tradable ex ante.
- **Events:** only 23 in 6.7 years. The BTC fade loses at 72h (−3.78%, t −2.98): big weekend moves tend to continue. The
  basket 24h +1.16% (t 0.71) is inconsistent by period.

## Tested: NY "wait out the crash hour" gate (owner "Test it"). Not supported; the first cut was hindsight-biased.
Question: should NY flush-buys skip entries while a market-wide crash is under way, and wait for the crash hour to close?
Real-time alarm, fixed before testing: at each 5m close, take each coin's move since the hour open and scale it by the
coin's trailing hourly sd × √(elapsed/60). The alarm fires at the first close where ≥ 75% of the 20 coins are at
z ≤ −3 (≥ 16 coins from 2022, ≥ 8 in 2020–21).
- **First cut, biased (not used):** it looked only at the 158 hours that *ended* as capitulation events. Waiting looked
  far better (+1.97%/event, t 7.0). But an event is defined by the hour's close, so selecting on it guarantees that
  prices kept falling after a mid-hour alarm.
- **Bias-free:** every hour where the alarm fired, 2020-01 → 2026-09-24. That is 856 hours on 552 days (127 a year),
  median alarm minute 15, and **77% of those hours bounced before the close**. Simulated NY-style legs on all 20 coins
  (5-ATR intrabar stop, 1h time exit, 20 bps), with t computed over days:

  | entry | R/leg | t |
  |---|---|---|
  | first 5m after the alarm | −0.026 | −1.41 |
  | any 5m until the hour closes (what a wait gate blocks) | −0.032 | −2.82 |
  | next hour's open (waiting) | −0.029 | −2.74 |

  - Same three entries with a 10-ATR stop: −0.005 / −0.013 / −0.014.
  - NY hours only (13–20 UTC, 387 alarms): −0.026 / −0.043 / −0.040.
  - By period (any 5m): 2020–21 +0.002, 2022–24 −0.053, 2025–26 −0.030.
  - Hours that ended as events: −0.52R right after the alarm. Hours that bounced: +0.09R. Only hindsight separates the
    two.
- **Sep 23:** the alarm fired at 14:15. At 14:10 only 40% of coins were down. The three legs that got stopped out
  entered at 14:10, before the alarm. A gate would have blocked the 14:16–14:51 legs (+1.60R and −0.60R), so it would
  have cost **−1.00R** that day.
- **Capitulation basket timing (same data):** buying the basket at the alarm, one basket at a time with a 24h cooldown,
  gives 468 trades at +0.50% (t 1.88): 2022–24 +0.28%, 2025–26 +0.02%, worst −40.3% (2020-03-12). The registered rule
  waits for the hour close: +1.10% (t 2.28), worst −17.6%. This confirms the registered design.
- **Caveats:**
  - The simulation buys all 20 coins, not NY's signal picks.
  - The universe is today's survivors (no LUNA or FTT).
- **Verdict:** no NY wait gate. On a day like Sep 23 the 60-min cascade cap blocks the same re-entries. On average those
  legs are about −0.03R, so the cap is a cheap exposure limit, not an edge filter. Keep it.

## Tested (2026-09-26): NY flush-buy mechanism on 6.7 years of liquidations. No gross edge.
Data: the free first-of-month days on Tardis.dev (Binance USDT-M liquidations), 2020-01 → 2026-09. That is 81 days and
1,537 symbol-days on the 20-coin universe, with 5m OHLC from the Binance archive.

Trigger, fixed before running: NY live floors (30m liquidation notional ≥ $20k, ≥ 3 events) plus long-liquidation
imbalance ≥ +0.5, then go LONG at the next 5m open, one leg per symbol. Exit as NY: 5 × ATR(14, 5m) intrabar stop, 1h
time exit, 20 bps. Control: the same exit from every 15 minutes of the same days and coins. The engine's vol_z,
n_confirms and decile gates are not replicated (5m volume and the aggression score were not in this data).

| population | legs | net R | gross R | cost R | t (days) |
|---|---|---|---|---|---|
| control (buy every 15 min) | 146,858 | −0.154 | +0.012 | 0.166 | −11.8 |
| trigger, all hours | 4,233 | −0.124 | +0.009 | 0.133 | −6.2 |
| trigger, 14–20 UTC Tue–Fri | 878 | −0.101 | +0.005 | 0.106 | −2.9 |

- **Broad vs narrow:** broad flushes (≥ 5 of 20 coins in the same 15m) −0.100 vs narrow −0.133.
- **By period:** negative in every one: 2020–21 −0.053, 2022–24 −0.146, 2025–26 −0.158.
- **Read:** the trigger's gross return equals buying at random. It only looks better than the control because it
  enters when volatility is high, so the fixed 20 bps is a smaller share of the stop. Any NY profit therefore has to
  come from the unreplicated gates (fitted on about 5 weeks) or from the recent market. This matches live since
  Jul-22: −0.028 R/leg.

## Scorecard (2026-09-25)
| idea | status |
|---|---|
| Market-capitulation basket buy | **registered** (PREREG-CAPITULATION-BASKET), paper-tracked; executable with realistic fills |
| Squeeze continuation | parked (bull-market momentum; small, concentrated OOS) |
| Funding extremes | no independent edge (crowded longs = momentum; crowded-shorts buy overlaps capitulation) |
| OI flushes | no edge; adds nothing to capitulation |
| Weekend-gap reversion | no edge (sign flips by period) |
| NY flush-buy mechanism, 6.7y liquidations (raw trigger) | no gross edge (+0.005–0.009R, same as random; −0.10 to −0.12R after costs) |
| NY crash-hour wait gate | not supported (bias-free: waiting ≈ buying at the alarm ≈ −0.03R/leg; the first cut was hindsight) |
