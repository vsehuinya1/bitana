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

## Risk Lab review (2026-09-26, owner-authorized free week; read-only apart from Lab runs)

**What the Lab can test:**
- It has no usable liquidation history in backtests. The long-liquidation-flush trigger never fired, even at the top
  50%, on the 41-day Binance 5m tape. So Bitana-style rules can't be tested there. Our Tardis/6.7y data remains the
  tool for them.
- Price tapes are short: 4h back to 13 Aug 2025, 30m about 6 months, 5m about 6 weeks.

**Floor directory:**
- 58 playbooks, mostly discretionary and self-logged.
- The mechanical ones are BIGROCKS's 12 BTC-long cards and Lucky's f50b.

**Replication** (rules copied from the cards, 5m data 2020-01 → 2026-09, 20 coins, 20 bps, compared against random long
entries with the same exit, t clustered by week including empty weeks):
- **Fib-bounce family** (12h 50/78.6, trail/BE/36-bar, London variant): E ≤ 0 in the authors' own last-30% window
  (2025-02 → 2026-09) on BTC and on the other 19 coins. The earlier profits look like 2021 bull beta. **Fails.**
- **BOS pivot-3 breakout** (4h/6h, EMA200 filter): beats random longs in all 12 variant × period cells, by +0.03 to
  +0.43 R/trade. But week-clustered t is only 0.2–2.3; the best is 4h EMA with a 36-bar exit, +0.24R (t 2.25) in
  2021–25 and +0.15R (t 0.7) in 2025–26. Gains are concentrated in a few trend weeks. **Consistent but unproven** — a
  candidate for a frozen forward paper track, not a wire.
- **Lucky f50b (BTC 30m, NY window, fib 50, 1.5R):**
  - Lab run (Bybit, 30 Mar → 26 Sep 2026): 63 trades, +0.36R, and the sealed last 30% made +0.23R on 22 trades.
  - The Lab's own robustness is fragile: a 3.75-ATR swing gives −0.30R, and dropping the NY window gives +0.08R.
  - Our long-history approximation is negative in every year 2021–2026 (−0.09 to −0.20R at 10 bps). It is not an
    exact match: 110 vs 63 trades on the same window.
  - Lucky's discretionary record: 11 trades, +10.9R, average loser −3.67R. His group's paper record: −1.1R over 13.
  - **Nothing to adopt.**
- **tanuki "0.777"** (152 self-logged trades, +187R): discretionary exits and limit fills. It can't be mechanized or
  verified.

## Tested (2026-09-26): Risk Lab order-flow cards on 6.7 years × 20 coins. All lose after costs.
Data: the Binance 5m archive with taker-buy volume, resampled to 30m. Net taker flow D = (2·taker buy − volume) ×
close, ranked only against the trailing 90 days. Exit as the ΔAGG card: stop 2 ATR, 3R target, breakeven at +1.5R,
100 bars. Control: long every 3h, same exit.

| rule | 2020 | 2021–24 | 2025–26 | net of 20 bps |
|---|---|---|---|---|
| R1 extreme buying (D ≥ p95), the ΔAGG card | −0.038R | −0.102R | −0.132R | loses every period |
| R2 extreme selling (D ≤ p5), flush reversal | −0.021R | −0.106R | −0.158R | loses every period |
| R3 selling absorbed (volume ≥ p90, D < 0, close ≥ open) | −0.056R | −0.056R | −0.105R | loses every period |

- **All rules beat the control** by +0.03 to +0.12R (R3 t 3.9 in 2021–24). But the control itself is −0.17 to −0.19R:
  this exit on 30m bars is sunk by costs. The triggers fire on big bars, where 20 bps is a smaller share of the stop.
- **At 10 bps** they are still mostly negative (−0.01 to −0.09R). BTC alone is negative in every rule and period.
- **The Lab's BRK60+CVD card on BTC 15m** (the Lab's own 6 months): −0.51R over 140 trades, and it broke out of sample.
  The cards' +10–13R forward results are 9–19 trades each, taken long during one bounce week.
- **Read:** at 15–30m, order-flow triggers carry a little timing information but nowhere near their costs. Same
  lesson as NY: short-horizon edges are smaller than execution costs.

## Tested (2026-09-26): Risk Lab HTF setups (wide stops, wins many times costs). Neither holds.
Rules were fixed before running: 20 coins, 2020 → 2026-09, 20 bps. The control replays each trade's own stop % and
target % from 20 random entry times, so it isolates the timing.

**S1 sweep & reclaim** (a bar trades below the prior-20-bar low and closes back above it; stop under the sweep low;
target the 20-bar high; 20 bars):
| | 2020 | 2021–24 | 2025–26 |
|---|---|---|---|
| 4h | −0.10R | −0.15R | −0.22R |
| 1D | −0.11R | +0.05R | +0.25R |

- 4h: no edge over the control.
- 1D: 269 trades in 2025–26 with an edge of +0.37R, but t is only 1.13, the top-5 weeks are 222% of net, and 2020 is
  negative. Weak and concentrated.

**S2 0.777 limit** (buy at 0.777 of a confirmed 3-ATR up leg, stop at 0.886, target the swing high, 50 bars):
| | 2020 | 2021–24 | 2025–26 |
|---|---|---|---|
| 4h | +0.03R | −0.56R | −0.44R |
| 1D | +0.21R | +0.13R | −0.65R |

- The win rate is 6–15% against a target of about 7R, and 1D 2025–26 is t −2.6 versus the control. The discretionary
  record (tanuki, +187R over 152 trades) is not reproduced by the mechanical core.

**Read:** wide stops fix the cost problem, but neither entry times the market better than random. The floor's
mechanical ideas are now exhausted: only PREREG-BREAKOUT-4H survived to paper.

## Tested (2026-09-26): NY with the engine's own filters, on 6.7 years of liquidations. No edge; the lattice hurts.
Same 81 Tardis days × 20 coins. Features are computed with the live engine's functions (`liq_cluster_engine_v5`:
vol_z, the 6 confirmations, the aggression decile) on the 300 5m candles ending at the signal bar. The NY lattice is
replayed with the live BTC regime classifier. Exit: NY (5/5/10 ATR, 1h, 20 bps).

| population | legs | days | net | gross | t (day) |
|---|---|---|---|---|---|
| raw trigger | 4,217 | 80 | −0.105R | +0.008R | −5.9 |
| + engine gates (vol_z ≥ 0, n_confirms ≥ 1) | 3,018 | 80 | −0.093R | +0.010R | −4.5 |
| raw + NY lattice | 340 | 37 | −0.177R | −0.093R | −2.8 |
| **NY as live (gates + lattice)** | 255 | 35 | **−0.189R** | **−0.113R** | −2.5 |

- **NY as live, by period:** 2020–21 −0.23R, 2022–24 −0.25R, 2025–26 +0.001R (57 legs, one day carries the net).
- **Read:** the engine gates pass 34% of triggers but add almost nothing (gross +0.008 → +0.010R). The lattice hours
  were chosen on Aug–Sep 2026 shadow data, and in earlier years they select worse-than-average legs. NY's paper
  profit since August is not supported by the long history.
- **Caveats:** 81 sample days; the stream before 2021-04 is complete, later it is Binance's 1-per-second snapshot
  (same as live); only 35 lattice days.

## Tested (2026-09-26): London's exact live rule on the same 6.7 years. No gross edge.
The live SessionBurstRule is loaded from the yaml (hour gate, bull/bear regimes, weekdays, age cap 17). Exit: 6-ATR
stop, 3-ATR target, 30 min, 20 bps.

| population | legs | days | net | gross |
|---|---|---|---|---|
| raw trigger with the London exit | 5,899 | 80 | −0.102R (t −7.9) | +0.007R |
| **London as live** (engine gates + schedule) | 80 | 11 | −0.084R | +0.010R |

- As live, by period: 2020–21 −0.09R, 2022–24 −0.03R, 2025–26 −0.19R. The in-schedule sample is thin (11 days).
- Row 11 (PREREG-LON-BULL-FADE) has 0 forward legs: the age cap removes the population it measures.

## Tested (2026-09-26): delta-neutral funding carry, 20 coins, 2020-01 → 2026-09. It works, but only in bull regimes.
Rule, fixed before running: long spot + short perp at equal notional. Enter when the trailing-24h funding averages
≥ 15%/yr, exit when it falls below 3%/yr, act 1h after settlement. Costs 0.30% round trip (0.15% maker case), spot vs
perp basis counted. Capital: 20 slots, 1.25× capital per notional.

| | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 YTD | CAGR | maxDD |
|---|---|---|---|---|---|---|---|---|---|
| rule (0.30%) | +11.8% | +33.9% | +0.2% | +2.8% | +7.2% | +0.1% | −0.0% | **+7.7%** | −0.3%* |
| always on | +9.9% | +35.2% | −3.1% | +4.2% | +9.2% | +1.5% | −0.2% | +7.8% | −3.9%* |

- **Decomposition (rule):** funding +52.1%, basis +2.3%, costs −4.1% of capital over 6.7y. 344 trades.
- *Drawdowns are understated: basis is booked at exit, not marked to market daily. The margin of a short perp in a
  pump also needs managing (a unified or portfolio-margin account).
- **Read:** it is a real premium, but a bull-market one. 2022, 2025 and 2026 earned about 0 and it was deployed
  0–3% of the time. Right now (2026-09-26) the majors are at ≤ 11%/yr, the default floor, so there is nothing to
  hold. High-funding names (+70–180%/yr) are illiquid small caps with pump and basis risk.

## Tested (2026-09-26): improving PREREG-BREAKOUT-4H. Pre-specified variants; the chosen one failed its holdout.
Procedure, fixed before running: choose on 2021–24 only (edge ≥ baseline + 0.05R, higher t, n ≥ 40%), highest t
wins, then one check on the 2020 and 2025–26 holdouts. Data: 5m archive with volume resampled to 4h, 20 coins, 20 bps,
control = random longs with the same exit.

| variant | 2020 edge | 2021–24 edge (t) | 2025–26 edge (t) | 2025–26 total / maxDD | worst week (21–24) |
|---|---|---|---|---|---|
| V0 registered | +0.019R | +0.206R (1.92) | +0.104R (0.51) | +26R / −152R | −23R |
| V1 BTC > EMA200 | −0.025R | +0.277R (2.27) | +0.107R (0.47) | +23R / −149R | −23R |
| V2 squeeze (ATR% ≤ 30d median) | −0.032R | +0.227R (1.74) | +0.116R (0.47) | +25R / −104R | −23R |
| V3 volume ≥ 1.5× 20-bar mean | +0.055R | +0.258R (2.04) | **+0.303R (1.03)** | +116R / −112R | −19R |
| V4 caps (3 new/bar, 8 open) | +0.176R | +0.221R (2.01) | +0.094R (0.47) | +13R / −93R | −13R |

- **By the fixed rule:** V1 and V3 qualified on discovery. V1 had the higher t, so it was chosen, and it **failed** the
  2020 holdout. No variant is adopted by the procedure.
- **Disclosed peek:** the runner-up V3 (volume) beats the baseline in both holdouts. Choosing it now is a 2-way
  selection on holdout data, so it can only be a separate forward paper track, never a replacement.
- **Risk finding:** portfolio drawdowns in R are large against totals (2025–26: +26R total with a −152R max DD). Any
  live sizing must be per portfolio (≤ 0.1–0.25% risk per trade, or hard caps like V4, which halves the worst week).

## Tested (2026-09-26): breakout-4H execution, market vs limit entry (5m simulation, 2020 → 2026-09)
Signals are the registered rule's. Costs: market entry 0.10%, maker 0.02%, exit 0.10%. Missed limits count as 0R.

| per signal | 2020 (329) | 2021–24 (1,872) | 2025–26 (718) |
|---|---|---|---|
| A market at next 4h open | +0.305R | +0.285R | +0.061R |
| L1 limit back at the broken level, 24h | +0.157R (fill 75%) | +0.144R (74%) | −0.054R (74%) |
| L2 limit at the signal close, 4h | **+0.328R** (99%) | +0.283R (99%) | **+0.080R** (99%) |

- **L1 is adverse selection.** The 25% it misses are the runners, +1.0 to +1.5R each at market. The fills it gets are
  the breakouts that failed back. Rejected.
- **L2 fills 99% at maker** and saves about +0.02–0.03R per filled trade (the fee difference). Its rare misses are big
  runners (2021–24: 12 signals averaging +3.4R). Net ≈ +0.00 to +0.02R per signal.
- **Read:** execution is worth about +0.02R per trade: a live implementation detail (post at the close, fall back to
  market if unfilled), not an edge lever.

## Tested (2026-09-27): NY bull live winners vs losers → candidate filters on 6.7 years. All fail.
**Live read** (73 NY bull live legs over 12 days, 60 pre-entry features):
- 37% of R variance is between days.
- Only the coin's distance from its 24h high survives FDR (rho +0.36, q 0.08). The sign pattern is "strong coin /
  strong day wins" (green signal bar, taker-buy share, BTC near its 24h high).
- Within-day correlations are about 0, so this is a day effect.
- Every leg traded below its entry within 5 minutes.

**Test** (Tardis 81 days × 20 coins, NY trigger + engine gates, NY exit, 20 bps; rules fixed from the live read; pass
= gross above baseline in all three periods on the NY-hours population):

| NY hours, gross R/leg | 2020–21 | 2022–24 | 2025–26 |
|---|---|---|---|
| baseline | +0.017 | −0.057 | +0.132 (t 3.15, 11 days) |
| A coin within 5% of its 24h high | −0.081 | −0.092 | +0.122 |
| B1 green signal bar | −0.039 | −0.136 | +0.068 |
| B2 wait for a green close (≤ 3 bars) | −0.009 | −0.095 | +0.066 |
| C BTC within 2.5% of its 24h high | −0.041 | −0.154 | +0.104 |

- **All fail, and all are worse than the baseline.** The legs they drop (deep dips, selloff days) pay more before
  costs: dropped by A +0.055, dropped by C +0.052 vs kept −0.018 / −0.020. That is consistent with the capitulation
  mechanism.
- The live "strong day wins" pattern is a small-sample day effect: Sep-23 alone was 20 of the 73 legs.
- **NY bull as live on history:** 130 legs over 12 days, gross −0.152, net −0.235.
- **Quirk (not actionable):** the NY-hours baseline in 2025–26 is gross +0.132 (t 3.15) but net +0.011 after
  20 bps, on 11 days.

## Tested (2026-09-27): five ideas from public trader sources (owner order "Scour the internet… get a few we can test")
Sources and mechanisms are in the owner summary (chat, 2026-09-27). Rules were fixed before each run. 20 coins,
2020-01 → 2026-09, costs included.

**#1 Wick catcher: PASS.**
- Rule: resting limit buy at close(H) − k × ATR1h, live for hour H+1; maker entry; 0.12% round trip.
- **k=5**, sell back at the pre-wick close else 24h, strict fills (price trades ≥ 0.1 ATR through the level):
  - by period: +4.82% (394 fills, t 2.4) / +1.77% (844, t 3.5) / +0.73% (284, t 1.6)
  - every year positive: 2020 +1.2%, 2021 +9.3%, 2022 +1.5%, 2023 +1.2%, 2024 +3.1%, 2025 +0.8%, 2026 +0.7%
  - ex-top-5-days: +1.87 / +1.08 / +0.08%
- **k=8**, 4h hold:
  - by period: +8.15% (117, t 3.2) / +2.62% (260, t 4.3) / +3.85% (61, t 2.3)
  - every year positive
- **Survivorship:** + 12 collapsed/delisted coins (LUNA, FTT, SRM, RAY, ANC, LUNA2, WAVES, BTS, HNT, TOMO, CVC, OMG),
  data cut at delisting, open trades marked at the last price. Dead-coin fills average +3.8% / +4.4% (worst −73.6%).
  The results are unchanged.
- **Portfolio** (2% notional per fill, ≤ 10 open): k=5 CAGR +8.5% (+10.3% with the dead coins), maxDD −3.4%, 74% of
  months positive; k=8 +4.8%, maxDD −0.7%.
- **Caveats:** 2025–26 depends on a few crash days. The short mirror (fading squeeze wicks) loses.

**#4 Perp below spot: PASS (directional).**
- Rule: basis ≤ −30 bps at a 5m close → buy the perp; exit on convergence (median 30 min) or 24h. 6,583
  dislocations, 100% converged within 24h.
- By period: +0.41% (t 2.9) / +0.60% (t 3.2) / +1.05% (t 1.2).
- With a 5-min entry delay: +0.32 / +0.26 / +0.71%.
- 4h exit: +0.95 / +1.09 / +1.86%.
- −50 bps: +1.25 / +2.50 / +6.13%.
- −20 bps fails.
- **Caveats:** 2026 ≈ 0; 2025–26 ex-top-5-days is negative. The hedged arb (long perp / short spot) loses −0.10% after
  4 legs of costs, so the profit is the bounce, not the convergence.

**#3 Single-coin capitulation candle: weak.**
- Market entry loses (−0.04 to −0.12R).
- A limit at the lower third beats the control (+0.10 / +0.29 / +0.26R), but E is −0.055R in 2020–21.

**#5 Quarter-hour opening imbalance: FAIL.** About −0.2%/trade (costs); on-clock ≈ off-clock.

**#2 OI flush + funding reset:** pending the metrics download.

**Read:** the two passes are the same mechanism — providing liquidity to forced perp sellers with patient (limit or
dislocation-triggered) entries. Every market-order "buy the flush" version lost.

## Tested (2026-09-27): wick-catcher risk rules for a live design. Stops hurt; the hourly cap helps.
Registered rule (−5 ATR, strict, sell at the pre-wick close else 24h), 20 coins + 12 collapsed coins (delisting
handled), portfolio 2%/fill, ≤ 10 open.

**In R** (1R = 3 ATR1h):
- no stop: +0.49 / +0.33 / +0.16R per fill, win rate 73 / 66 / 56%
- 3-ATR stop: +0.06 / +0.05 / +0.11R, half the fills stopped
- 5-ATR stop: +0.09 / +0.05 / +0.04R

**Catastrophe stop, per fill (2020–21 / 2022–24 / 2025–26):**

| stop | 2020–21 | 2022–24 | 2025–26 | worst fill | fills stopped |
|---|---|---|---|---|---|
| none | +5.65% | +1.74% | +0.81% | −73.6% | 0 |
| −15% | +1.32% | +1.40% | +0.37% | — | 162 |
| −20% | +2.24% | +1.60% | +0.36% | — | 89 |
| −25% | +3.00% | +1.73% | +0.23% | — | 54 |

- Portfolio maxDD gets **worse** with a stop: −3.7% none vs −6.5 / −7.3 / −7.6%. Liquidation wicks overshoot past −25%
  and recover, so the stop sells the print.

**Hourly cap on new fills, no stop:**

| cap | CAGR | maxDD | positive months |
|---|---|---|---|
| none | +11.0% | −3.7% | — |
| 5/hour | +8.6% | −2.1% | 75% |
| 3/hour | +6.6% | −2.1% | 80% |

**Read (live-design risk rules, to be frozen at promotion):** no price stop of any width; 1–2% notional per fill;
at most 5 new fills per hour (deepest first); at most 10 open. The tail (a collapsing coin, −74%) is bounded by
size: about −1.5% of equity per fill.

## Wick catcher: what else marks the winners? (2026-09-28)
Population: registered wick fills (k=5, strict, TP else 24h), 20 coins. Features and expected directions written before
running; thresholds = discovery (2022–23) medians; selection on discovery only, then 2024–26 holdout once; 2020–21 and
12 collapsed coins as extra checks. Scripts: scratch `wick_features2.py`, `wick_candidates.py`.

**First pass withdrawn.** The first run scored fills on fill-bar data (bar close, OI, BTC, taker share). That bar closes
up to 5 min after the limit fill, so "the fill bar closed high" (+2.9%/fill difference) was mostly the bounce already
showing. Rerun with honest timing: (A) data known before the fill bar; (B) a second entry at the fill-bar close.

**What fails:** heavy volume, deep overshoot, taker selling on the fill bar, OI flush before the fill, BTC already down
before the fill bar, funding, 30-day-low breaks, other coins already filled. A 6-feature pre-fill score is not monotone
on holdout.

**C1: BTC dumping at the fill** (BTC ≤ −1.70% from the bid-hour close to the fill-bar close) → add a second unit at the
fill-bar close (taker, 0.20%), same exits. Add-on net, IN vs OUT:

| period | IN | OUT |
|---|---|---|
| 2020–21 | +6.87% (n=268, win 73%) | +0.45% |
| 2022–23 (discovery) | +1.78% (n=293) | +0.88% |
| 2024 | +3.73% (n=126, win 70%) | −1.81% |
| 2025 | +4.85% (n=62) | −1.54% |
| 2026 | +1.22% (n=62) | −1.05% |
| 12 collapsed coins, all years | +5.75% (n=136), worst −25% | +0.93%, worst −74% |

- Base fills inside C1 (2024–26): +2.78%, win 66%, day-clustered t 2.52. It is the market-wide signature measured in the
  fill hour rather than the bid hour: 271 holdout fills vs 103 on the bid-hour tag.
- Holdout fills the bid-hour tag calls coin-specific: IN +0.97% (n=199, t 1.17) vs OUT −1.48% (n=193).
- The −74% collapse fills were all OUT.

**Single-coin signatures, known before the fill:**
- Low pre-fill taker selling: taker-sell share of the 3 bars before the fill bar ≤ 56.9%. The direction is the opposite
  of my prior; it was picked on discovery.
- Perp discount at the prior bar: basis ≤ −0.062%.

The low-selling side beats the high-selling side in all 5 periods:
- 2020–21: +6.19 vs +4.43%
- 2022–23: +1.80 vs +0.73%
- 2024: +3.73 vs +2.48%
- 2025: +1.11 vs +0.52%
- 2026: +1.33 vs +0.30%

The discount side also beats the no-discount side in all 5 periods. In 2022–23 and 2026 the gap is small.

Coin-specific fills by combination:

| period | both | taker only | discount only | neither |
|---|---|---|---|---|
| 2020–21 | +2.38% (n=11) | +2.73% | +3.65% | +0.88% |
| 2022–23 | +1.69% (n=144) | +1.41% | +0.60% | +0.41% |
| 2024 | +3.05% (n=31, win 74%) | +2.15% | +2.85% | −0.63% |
| 2025–26 | +1.37% (n=50, win 66%) | −0.04% | +0.10% | +0.01% |

- "Neither" is the weakest or near-weakest bucket in every period.
- In 2025–26 only "both" pays.
- 7 of the 8 worst coin-specific fills had heavy pre-fill taker selling.
- Inside C1 the effect compounds: both +2.69% (win 77%, n=149) vs neither +0.51%. Outside C1 both ≈ neither (+0.37 / +0.24%).

**Read:** single-coin wicks pay when the perp is at a discount and the drop came without aggressive market selling.
Aggressive one-sided selling into a lone coin looks informed and keeps going. The biggest separator is still the whole
market (BTC) falling at the fill. The day-clustered t-stats are modest: 1.2–2.5. These are candidates for report-only
lines or tiered sizing, not proven tiers.

## Wick catcher: tiered sizing vs the original (2026-09-28)
Setup:
- Script: scratch `wick_tier_sizing.py`. Designs fixed before running.
- Fills: 20 coins + 12 collapsed coins. Collapsed coins have no taker/spot data, so their signs count as "one".
- Sizing: size = % of realized equity per fill; P&L is realized at exit.
- Limits: at most 10 open positions and 5 new fills per hour. No exchange leverage cap is modelled; "max gross" = peak
  open notional ÷ equity.

Tier results (base fill):

| tier | 2020–23 | 2024–26 |
|---|---|---|
| market | +11.1% (n=186, win 90%) | +6.6% (n=119, win 83%) |
| btc | +1.5% (n=616) | +1.8% (n=225) |
| both | +0.7% (n=52) | +0.9% (n=27) |
| one | +1.4% (n=269), worst −74% (dead coin) | 0.0% (n=116) |
| neither | +1.0% (n=123) | −0.8% (n=82, win 39%) |

**Holdout 2024-01 → 2026-09** (tiers fitted on 2022–23):

| design | per fill (market / btc+add / both / one / neither) | CAGR | maxDD | worst day | best week | 2024 / 2025 / 2026 | max gross |
|---|---|---|---|---|---|---|---|
| O1 original | 2% flat | +5.5% | −1.9% | −1.4% | +2% | +10 / +3 / +2% | 0.2× |
| O2 original aggressive | 40% flat | +162% | −36% | −24% | +54% | +429 / +71 / +55% | 3.6× |
| O3 market only | 50 / 0 / 0 / 0 / 0 | +130% | −3.6% | −3.6% | +42% | +318 / +114 / +9% | 2.5× |
| T0 tiered small | 10 / 6+6 / 4 / 2 / 0 | +29% | −8.1% | −8.1% | +12% | +59 / +16 / +10% | 0.9× |
| T1 tiered medium | 50 / 30+30 / 20 / 10 / 0 | +222% | −34% | −34% | +62% | +716 / +93 / +57% | 4.6× |
| T2 tiered high | 60 / 40+40 / 30 / 15 / 0 | +309% | −43% | −43% | +78% | +1167 / +108 / +79% | 6.0× |

**Full 2020-01 → 2026-09** (partly in-sample):

| design | CAGR | maxDD |
|---|---|---|
| O1 | +8.5% | −2.1% |
| O2 | +331% | −38% |
| O3 | +142% | −14% |
| T0 | +47% | −9.8% |
| T1 | +431% | −48%, worst day −45% |
| T2 | +655% | −64% |

Weeks ≥ +100%: none in the holdout for any design; full period T1 1, T2 2.

**Read:**
- The market-wide tier carries the risk-adjusted value: O3 gives +130%/yr at −3.6% DD.
- The lower tiers add return but multiply drawdown about 10×. T1 over O3: +92 pts/yr for −30 pts DD.
- Tiering beats flat sizing at equal drawdown: T1 vs O2 is +222% vs +162% at about −35%.
- Big sizes assume maker fills of 30–60% of equity in a crash wick. That is only realistic for a small account.

**Follow-up T3: market 50% + BTC-dump 30% + 30% add-on, single-coin tiers skipped** (POST-HOC: chosen after the table
above, owner order "Yes"):

| design | holdout CAGR | holdout maxDD | 2024 / 2025 / 2026 | full CAGR | full maxDD |
|---|---|---|---|---|---|
| T3 | +200% | −34% (worst day −34%) | +675 / +91 / +38% | +395% | −48% |

- T3 has the same drawdown as T1, with about 22 pts/yr less return. Dropping the single-coin tiers removes return, not risk.
- The −34% day is 2025-10-10. Five BTC-dump fills at 20:55Z, in the first minutes of the crash, lost 13–25% per unit,
  doubled by the add-on. The bid-hour market-wide tag missed that hour, because 19:00 was not a selloff hour.
- Read: the BTC-dump tier carries the tail. Its base mean is +1.8% vs +6.6% for market-wide; its worst fill is −25%.
  Large size belongs on market-wide fills only. The BTC-dump tier and its add-on belong at small size.

## Tested (2026-09-30): internet-sourced improvements for the other tracks (owner order "1,3,6 and 8 first … Go")
Sources:
- Zarattini/Pagani/Barbon 2025, "Catching Crypto Trends": trailing stop at the channel midpoint, multi-lookback ensemble,
  volatility targeting.
- Chandelier exit.
- AdaptiveTrend 2026: long/short.
- arXiv 2601.06084: funding aligned with the 4h context.
- K33 / short-squeeze setups: negative funding.
- Pendle/Boros and OneKey: Hyperliquid vs Binance funding.

Every rule was fixed before running. Selection used discovery only (breakout 2021–24; capitulation 2022–24; discount
2022–23), and each holdout was checked once. Scripts (scratch): `bo4_exits_shorts.py`, `bo4_context.py`,
`capit_variants.py`, `discount_btc.py`, `carry_hl.py`.

**#1 Breakout trailing exit: FAIL.** Registered entries; control = random longs with the same exit.

| exit | 2020 edge / total | 2021–24 edge (t) / total | 2025–26 edge (t) / total / maxDD | hold |
|---|---|---|---|---|
| X0 registered (36 bars) | +0.023R / +127R | +0.207R (1.92) / +665R | +0.087R (0.45) / +29R / −152R | 22 bars |
| X1 chandelier 3 ATR (≤ 180 bars) | +0.067R / +76R | +0.130R (1.99) / +302R | +0.098R (0.67) / +20R / −131R | 15 |
| X2 channel midpoint 20 bars (≤ 180) | +0.108R / +62R | +0.113R (2.11) / +310R | +0.106R (0.88) / +59R / −116R | 10 |

- On 4h bars both trails are tighter than the 6-day time exit, so they cut winners and halve total R.
- X2 has the higher t in every period and a smaller 2025–26 drawdown. The fixed rule required total R ≥ baseline, so it
  is not adopted. The paper's trail works on daily bars with long lookbacks; a 4h version is a different thing.

**#3 Short mirror (break below a swing low, close < EMA200): FAIL.**
- Discovery t is +1.10 for all signals and +1.23 for BTC < EMA200 only; the bar was 1.5.
- It pays only in 2022 (+251R). 2023–25 lose (−41 / −23 / −47R).
- Long + short: total R +821 → +972R, but maxDD −152 → −180R and 2025 turns negative. Funding adds almost nothing.

**#6 Capitulation basket variants: FAIL; the registered basket stays best.**
- Discovery 2022–24 (79 events): registered +1.01% (t 1.57).
  - most-sold half +0.85%; z-weighted +0.99%
  - recover-or-48h +0.04%; 48h hold +0.60%
  - limit bid at −1 ATR: +0.15% on committed capital, 31% fill rate (+1.39% per filled coin)
- Holdouts:
  - 48h is better only in 2025–26 (+1.14% vs +0.72%) and worse in 2020–21 (+1.88% vs +2.75%).
  - Limit fills pay more per coin (+5.55% in 2020–21), but about 70% of coins never fill.

**#8 Perp discount only when BTC is falling: PASS (all three conditions).**

| IN vs OUT (mean per trade, 4h exit) | 2020–21 | 2022–23 (discovery) | 2024–26 |
|---|---|---|---|
| BTC 1h ≤ −0.37% (discovery median) | +2.15% vs +0.16% | +1.12% vs +0.25% | +5.64% vs −0.03% |
| BTC 1h ≤ −1.0% (fixed) | +3.02% vs +0.23% | +1.44% vs +0.29% | +7.68% (n=167, t 1.80) vs −0.00% |
| last hour market-wide selloff | +5.65% vs +0.51% | +1.34% vs +0.58% | +9.80% vs +0.15% |

- Coin-specific discounts earn about 0 in every holdout. The whole edge is in forced selling across the market.
- It also holds BTC-hedged: 2024–26 IN +4.1% vs OUT −0.04%.
- 2025 IN is +16.8% on 41 trades (concentrated). **2026 YTD IN is +0.07% on 78 trades, flat.**

**#2 Longer-timeframe confirmation: FAIL.** A 20-day or 60-day closing high raises the discovery edge (+0.38 / +0.47R) but
keeps under 40% of the trades, and both fail the holdouts. The 60-day version loses −0.26R edge in 2025–26.

**#4 Funding / OI context: FAIL by the rule. One near-miss:**
- **Breakouts with funding ≤ 0** (F1, the short-squeeze setup) beat the plain rule's edge in all three periods:
  - 2020: +0.273 vs +0.023R
  - 2021–24: +0.282 vs +0.207R
  - 2025–26: +0.412 vs +0.087R. 2025–26 total is +54R vs +29R, with maxDD −19R vs −152R.
- It keeps only 13% of the trades and discovery t is 1.45 against a bar of > 1.92, so it is not selected.
- Funding above the median fails 2020. OI rising or falling: no.

**#5 Portfolio risk (max 6 open; volatility-target weights): FAIL.** Neither improves return/drawdown on discovery.
Max 6 open halves the 2025–26 drawdown (−80R vs −152R) at the same total R.

**#7 Hyperliquid alongside Binance (funding only, 2023-06 → 2026-09; HL public funding history, 20 coins).**

| design | total on capital (3.3y) | 2023 | 2024 | 2025 | 2026 YTD | trades |
|---|---|---|---|---|---|---|
| 7a perp-perp spread (short the higher venue, 0.40%) | −12.6% | +2.7% | −2.4% | −7.6% | −5.4% | 2,016 |
| 7b carry, short on the better venue (0.30%) | **+20.5%** | +1.6% | +14.9% | +3.8% | +0.2% | 312 |
| 7b0 registered carry, Binance only | +8.3% | −0.0% | +8.0% | +0.4% | −0.1% | 117 |

- **7a FAILS.** The spread captures +0.24%/trade of funding against 0.40% of costs, and spreads close within about 4 days.
- **7b FAILS the fixed rule on 2026 only.** It beats Binance-only by +3.4 pts in 2025, but by only +0.3 pts in 2026 (bar ≥ 1 pt).
- 7b is 2.5× the registered carry over 3.3 years; 276 of its 312 trades are shorted on Hyperliquid.
- Nothing is earned in 2026 on either venue.
- Not modelled: the price gap between Binance spot and the HL perp (a cross-venue hedge), and HL margin in USDC.

## Tested (2026-09-30): wick catcher × order-book depth (owner order "Do it")
Data:
- Binance USDT-M `bookDepth` daily archive (public, from 2023): ~30s snapshots of cumulative notional at ±1–5% of mid.
- 1,893 of 1,903 coin-days downloaded.

Fills: the registered wick rule. Design:
- Features read from the last snapshot before the fill bar, with expected directions written before running.
- Selection on 2023 (451 fills). Holdout 2024-01 → 2026-09 (517 fills), tested once.
- Script (scratch): `wick_book.py`.

| feature | 2023 (all / coin-specific diff) | 2024–26 holdout (all / coin-specific diff) | verdict |
|---|---|---|---|
| D1 bid depth within 1% ≤ 0.895 × its 24h median (thin) | +1.37 / +1.32 pts | +1.65 / +0.92 pts | **confirms** |
| D2 bid depth within 5%, thin | +0.60 / +0.56 | +2.10 / +0.95 | **confirms** |
| D3 bid 1% depth down ≥ 11% over 60 min (pulled) | +0.72 / +0.89 | +0.88 / +0.52 | **confirms** (corr 0.98 with D1: the same signal) |
| D4 bid share within 1%, higher | −0.91 / −1.07 | −1.62 / −0.87 | reversed in all four (disclosed): bid-heavy books did worse |
| D5 asks shrinking over 60 min | +1.11 / +0.98 | +0.93 / −0.01 | fails the holdout |
| D6 bids refill after the fill bar (add-on) | −1.45 / −1.26 | −0.67 / −0.79 | reversed (disclosed) |

- **Read:** wicks into a thinned bid book (bids pulled: a liquidity vacuum) snap back. Wicks that eat through a thick,
  bid-heavy book keep going.
- **By year (thin vs not):**
  - 2023: +1.85% vs +0.52%
  - 2024: +3.96% vs +0.95%
  - **2025: +0.18% vs +1.54% (reversed)**
  - 2026: +1.18% vs +0.25%
- **Where it works:** inside the BTC-dump tier (not market-wide), thin books averaged +1.88% (n=308, t 2.59) vs +0.37%
  (n=140). Market-wide: +6.52% vs +4.83%.
- **Where it doesn't:** pure single-coin fills (no market-wide selloff, no BTC dump): +0.27% vs +0.28%; holdout −0.37% vs
  −0.19%. It sharpens the market-driven tiers; it does not rescue single-coin wicks.
- **Availability:** the archive is published daily, so a paper reader can tag fills a day later. Live use needs our own
  depth stream (Binance websocket, free).

## Tested (2026-09-30): Bitana's NY rule on 15m and 1h (owner question)
Same raw NY trigger (30m liquidation cluster, long imbalance ≥ +0.5), same 81 Tardis days × 20 coins. Only the trade's
timeframe changes: entry at the next 5m / 15m / 1h bar, stop 5 ATR of that timeframe, exit after 12 of its bars
(1h / 3h / 12h), 20 bps. The control is random entries with the same exit. Script (scratch): `ny_timeframes.py`.

| trade TF | net R | gross R | cost R | random entries net | edge vs random (t) |
|---|---|---|---|---|---|
| 5m (live) | −0.124 | +0.009 | 0.133 | −0.154 | −0.003R (−0.18) |
| 15m | −0.053 | +0.024 | 0.076 | −0.062 | −0.003R (−0.12) |
| 1h | +0.007 | +0.044 | 0.037 | +0.007 | −0.004R (−0.11) |

- A higher timeframe cuts the cost drag (wider stops, so 20 bps is a smaller share of R). The trigger still adds nothing
  over random entries at any timeframe.
- At 1h the rule earns exactly what random longs earn (+0.007R), which is just the sample's drift.
- NY hours at 1h: −0.058R. Broad clusters (≥ 5 coins) are negative at every timeframe.
- Read: the problem is the signal, not the timeframe. The hourly edges that do hold (capitulation basket, wick
  catcher's market-wide tier) need a far more extreme, market-wide trigger than a $20k liquidation cluster.

**Follow-up: 1m (owner question).** Same trigger, now evaluated at every 1m close. Data: Binance 1m archive for the
same 81 days × 20 coins. Script (scratch): `ny_1m.py`.

| variant | net R | gross R | cost R | edge vs random (t) |
|---|---|---|---|---|
| A 1m trade (stop 5 ATR of 1m, 12-min exit) | −0.302 | +0.004 | 0.306 | −0.004R (−0.47) |
| B fast entry (next 1m open), live 5m stop, 60-min exit | −0.130 | +0.008 | 0.139 | −0.002R (−0.13) |
| C live (next 5m open), same stop/exit | −0.130 | +0.003 | 0.133 | −0.007R (−0.45) |

- 1m more than doubles the cost drag (0.31R per trade) with no gain in gross.
- Entering up to 4 minutes earlier doesn't help: there is no bounce in the first minutes for the 5m entry to miss.

## Tested (2026-09-30): levers that could add information to Bitana's NY trigger (owner order "Test")
Same 81 Tardis days × 20 coins, raw NY trigger, NY exit (5 ATR(5m) stop, 1h, 20 bps). Every filter is compared with
random entries that meet the SAME condition ("matched"): does the liquidation trigger add anything beyond the filter?
The limit-entry lever is compared with the same bid placed at random 15-min marks. Pass: edge vs matched ≥ +0.05R,
t(day) ≥ 2, net > 0, positive in every period. About 12 variants were looked at. Script (scratch): `ny_levers.py`.

| lever | legs | net R | vs matched (t) | periods 2020–21 / 2022–24 / 2025–26 | verdict |
|---|---|---|---|---|---|
| base trigger (market buy) | 4,233 | −0.124 | −0.003R (−0.18) | −0.005 / −0.012 / +0.005 | fail |
| L1a extreme size (coin's top 0.2%) | 67 | −0.048 | +0.015R (+0.19) | +0.026 / n=2 / +0.013 | fail |
| L1b price stretch ≥ 4 ATR in 30m | 671 | −0.110 | −0.015R (−0.33) | −0.058 / −0.012 / +0.019 | fail |
| L2a BTC falling ≥ 1%/1h | 694 | −0.075 | +0.029R (+0.71) | +0.062 / +0.022 / −0.065 | fail |
| L2b perp discount ≤ −0.10% | 214 | −0.060 | +0.010R (+0.29) | −0.126 / +0.037 / +0.056 | fail |
| L4 thin book (2023+) | 617 | −0.105 | −0.017R (−0.39) | — / −0.047 / +0.006 | fail |
| stretch + BTC falling | 344 | −0.113 | −0.018R (−0.28) | +0.006 / −0.033 / −0.058 | fail |
| **L3 limit bid −2 ATR, 30 min** (fill 23%) | 1,109 fills | **+0.038** | **+0.067R (+2.83)** | +0.049 / +0.060 / +0.079 | **PASS** |
| **L3 limit bid −3 ATR** (fill 11%) | 594 fills | **+0.095** | **+0.095R (+2.61)** | +0.089 / +0.115 / +0.066 | **PASS** |
| **L3 −2 ATR & BTC falling** (fill 27%) | 198 fills | **+0.141** | **+0.094R (+2.39)** | +0.152 / +0.047 / +0.084 | **PASS** |

- **Filters add nothing:** with each condition applied, the liquidation trigger does no better than a random buy under
  the same condition.
- **Execution does:**
  - A bid 2–3 ATR(5m) below the trigger close, live 30 min, sells back at the trigger price (else 1h, no stop, 0.12%).
  - It beats the same bid placed at random times, in every period.
  - The trigger tells you when a flush will overshoot into a resting bid and revert. Market-buying after it gets no
    discount.
- It is the wick catcher at 5m scale, triggered by liquidations.
- Per fill it is small: about +0.1–0.2% net at ATR(5m) ≈ 0.3% (1R = 5 ATR ≈ 1.5%). Fill rates 11–27%.
- Caveats:
  - 81 sample days.
  - Fills assume a strict 0.1 ATR trade-through on 5m bars; queue and latency are not modelled.
  - The 3 passes are one family (limit entry). Consistent across periods, but not independent tests.

## CORRECTION (2026-09-30): the fill bar's own high was counted as the take-profit
The Bitana limit-bid lever (above) and the wick catcher both let the TP hit inside the fill bar. On 5m OHLC the order of
the high and the low inside that bar is unknown, so this can book a profit before the position existed. Scripts
(scratch): `tp_bar_check.py`, `ny_limit_1m.py`.

**Bitana limit bid, resolved on 1m bars** (fill = first 1m bar through the bid; TP only from the next minute):

| variant | fills | net R | random bids | edge (t) | 2020–21 / 2022–24 / 2025–26 net |
|---|---|---|---|---|---|
| bid −2 ATR | 1,066 | −0.052 | −0.114 | +0.062R (1.95) | +0.023 / −0.073 / −0.077 |
| bid −3 ATR | 575 | −0.008 | −0.094 | +0.086R (1.81) | +0.071 / −0.005 / −0.051 |

- **The "PASS" above is withdrawn.** The trigger still beats random bids in every period, but the trade loses after
  costs from 2022 on.
- In 21–31% of fills, the fill bar's own high had reached the TP. Not registered.

**Wick catcher (registered reader has the same optimistic TP):** 8% of all fills are ambiguous; 24% of market-wide fills.
Conservative bound (TP only from the 5m bar after the fill) vs registered:
- all fills: +2.08% vs +2.36%/fill; 2025–26 +0.39% vs +0.73%
- market-wide: +8.00% (win 79%) vs +9.26% (win 88%); 2024–26 +4.07% vs +6.07%
- coin-specific: +1.02% vs +1.13%

The truth is between the two, since some wicks do recover inside the bar. 1m data can resolve it. Every wick number in
this report (tiers, sizing) carries this optimism; the market-wide tier stays strongly positive under the conservative
bound.

## Wick catcher re-run with the fill bar resolved on 1m (2026-09-30, owner order "Fix")
The reader of record now resolves a TP inside the fill bar on 1m bars (`_tp_after_fill_1m`); the 2024 basis is
re-frozen at +2.53%/fill (was +3.11%). In 2024, 34 fills had the TP level inside the fill bar and only 1 was confirmed
after the fill minute: the old rule was booking almost entirely fake exits.

Research re-run (20 coins; sizing also + 12 collapsed coins). 78 of 1,519 fills change exit:

| 2024–26 | before | 1m-corrected |
|---|---|---|
| market-wide fills | +6.07%/fill, win 83% | **+4.07%, win 68%** |
| coin-specific fills | +0.73% | +0.67% |
| BTC-dump add-on, IN vs OUT | +3.73…+4.85% vs −1.1…−1.8% (by year) | +3.17% vs −0.71% |
| single-coin both signs vs neither | +3.05% / +1.37% vs −0.63% / +0.01% | +2.01% vs −0.31% |
| thin bid book vs not | +2.46% vs +0.81% | +1.99% vs +0.40% (2025 still reversed) |

Every signature still separates in the same direction. Sizing at 1m (2024–26 holdout; full period in brackets):
- 2% flat: +3.6%/yr, maxDD −2.5% (+6.9%/yr)
- market-wide only 50%: **+79%/yr, maxDD −10%** (+100%/yr, −35%)
- tiered small: +21%/yr, −11.5%
- tiered medium: +113%/yr, maxDD **−54%** (+298%/yr, −54%)
- market + BTC-dump only: +105%/yr, −54%

The fake exits had hidden the 2025-10-10 crash damage. Tiered medium/high now has a −54% / −70% worst day. Market-wide
only stays the best return per unit of drawdown.

## Scorecard (2026-09-25)
| idea | status |
|---|---|
| Market-capitulation basket buy | **registered** (PREREG-CAPITULATION-BASKET), paper-tracked; executable with realistic fills |
| Squeeze continuation | parked (bull-market momentum; small, concentrated OOS) |
| Funding extremes | no independent edge (crowded longs = momentum; crowded-shorts buy overlaps capitulation) |
| OI flushes | no edge; adds nothing to capitulation |
| Weekend-gap reversion | no edge (sign flips by period) |
| NY flush-buy mechanism, 6.7y liquidations (raw trigger) | no gross edge (+0.005–0.009R, same as random; −0.10 to −0.12R after costs) |
| Risk Lab floor: fib-bounce family, Lucky f50b | fail on long history / fragile |
| Risk Lab floor: 4h BOS-above-EMA200 breakout (20 coins) | consistent small edge vs random longs, unproven (t ≤ 2.3); forward-paper candidate |
| Risk Lab order-flow cards (extreme buying / selling / absorbed, 30m) | lose after costs every period (−0.02 to −0.16R); beat a cost-sunk control only |
| Risk Lab HTF: sweep & reclaim (4h/1D), 0.777 limit pullback (4h/1D) | no timing edge; 4h loses every period, 1D weak/concentrated or negative |
| NY with engine filters + lattice (6.7y, Tardis) | no edge: gross −0.11R/leg, net −0.19R; lattice worse than raw |
| London as live (6.7y, Tardis) | no gross edge (+0.01R); net −0.08R, thin in-schedule sample |
| Funding carry (delta-neutral, 20 coins) | works: CAGR +7.7%, tiny DD, but ~0 in 2022/2025/2026; idle now |
| Breakout-4H variants (market / squeeze / volume / caps) | procedure's pick (market filter) failed 2020 holdout; volume filter passed both holdouts (runner-up, disclosed peek) |
| Breakout-4H limit entry | retest limit loses the runners (reject); limit at close ≈ +0.02R/trade (execution detail) |
| NY live W/L candidates (shallow dip, green bar, confirmation, BTC strong) | all fail on 6.7y; dropped legs pay more (live pattern = day effect) |
| Wick catcher (limit bids −5/−8 ATR1h) | PASS all periods & years, survives strict fills + dead coins; paper candidate |
| Perp-below-spot buy (basis ≤ −30 bps) | PASS all periods (2026 ≈ 0); paper candidate |
| Coin capitulation candle / quarter-hour flow | weak / fail |
| NY crash-hour wait gate | not supported (bias-free: waiting ≈ buying at the alarm ≈ −0.03R/leg; the first cut was hindsight) |
| Wick winner signatures (2026-09-28) | BTC dumping at the fill (add-on) holds all periods + dead coins; single-coin: perp discount + no aggressive pre-fill selling (all periods, modest t); fill-bar 'bought back' was a timing artifact |
| Breakout trailing exit / short mirror / 20-60d confirmation / risk caps (2026-09-30) | fail the fixed rules (trails halve R on 4h; shorts pay only in 2022) |
| Breakout with funding <= 0 (2026-09-30) | near-miss: higher edge in all 3 periods, 13% of trades, t below bar; not selected |
| Capitulation basket variants (weights, exits, limit entry) (2026-09-30) | fail; registered basket stays best |
| Perp discount only when BTC is falling (2026-09-30) | **PASS** all periods; coin-specific discounts ~0; 2026 YTD flat |
| Carry with Hyperliquid (2026-09-30) | perp-perp spread fails; better-venue carry 2.5x Binance-only but fails the 2026 bar |
| Wick catcher x order-book depth (2026-09-30) | thin/pulled bid book before the fill PASSES (2023 -> 2024-26); works inside the BTC-dump tier, not for pure single-coin wicks; 2025 reversed |
| Bitana NY rule on 15m / 1h (2026-09-30) | costs fall, but the trigger = random entries at every timeframe; no edge |
| Bitana NY rule on 1m / faster entry (2026-09-30) | 1m: cost 0.31R/trade, net -0.30R; entering 1-4 min earlier = same as live |
| Bitana trigger levers (2026-09-30) | filters add nothing; limit bid beats random bids but LOSES after costs once the fill bar is resolved on 1m (PASS withdrawn) |
| In-bar TP correction (2026-09-30) | wick catcher optimistic by ~0.3%/fill (market-wide ~1.3 pts); to be resolved on 1m |
| Wick catcher 1m re-run (2026-09-30) | reader fixed + basis re-frozen (+2.53%); market-wide 2024-26 +4.1% (68% win); signatures hold; big tiered sizing now -54% DD |
