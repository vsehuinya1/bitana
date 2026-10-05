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

## Macro-cycle split (2026-10-02, owner question: "do we separate known bull markets?")
Definition fixed before looking, from BTC daily closes:
- **bull** = close > SMA200 and SMA200 rising over 30 days; **bear** = both the other way; **transition** = anything else.
- **young bull** = a bull day within 180 days of the last bear day.
- Today: **young bull** since 2026-09-10 (BTC +19.6% over SMA200, SMA200 +2.7% in 30 days, last bear day 2026-08-18).
- Past young-bull spells: 2020-05 → 10, 2021-10 → 12, 2023-02 → 07, 2024-11 → 2025-03.

Script (scratch): `macro_split.py`.

| NY raw trigger (Tardis 81 days), gross R | days | trigger | random | edge (t) | net after costs |
|---|---|---|---|---|---|
| young bull | 17 | +0.028 | +0.013 | +0.015 (+0.43) | −0.116 |
| bull | 26 | +0.020 | +0.038 | −0.018 (−0.74) | −0.111 |
| transition | 15 | −0.002 | +0.001 | −0.002 (−0.07) | −0.114 |
| bear | 20 | −0.012 | −0.016 | +0.004 (+0.15) | −0.157 |

| wick catcher (1m-resolved), net per fill | n | all | market-wide | coin-specific |
|---|---|---|---|---|
| young bull | 409 | +2.24% (win 67%) | +7.39% (58) | +1.39% |
| bull | 485 | +2.38% | +4.75% (114) | +1.65% |
| transition | 259 | +3.62% | +21.3% (40) | +0.38% |
| bear | 324 | +0.88% | +2.00% (15) | +0.83% |

- **Read:** the NY trigger has no edge in any macro state, young bull included. The wick catcher pays in every state:
  most in transitions (crash-and-recover), least in bear.
- Breakout and carry are the bull-dependent tracks: breakout earned in 2021 and 2023–24, carry in 2020–21 and 2024.

## Breakout-4H (+VOL): restrict to the best coins, or to macro bull? (2026-10-02, owner question)
Coins ranked by edge vs random longs on DISCOVERY 2021–24 only; top-k fixed; holdouts 2020 and 2025-01 → 2026-09 checked
once. Macro state as the macro split above. Script (scratch): `bo4_pairs_macro.py`.

| book | coins (from 2021–24) | 2020 edge: top vs rest | 2025–26 edge: top vs rest (total R) | verdict |
|---|---|---|---|---|
| plain TOP5 | DOGE FIL AVAX ETC SOL | −0.149 (n=44) vs +0.037 | +0.213 (+11R) vs +0.046 (+17R) | FAIL (2020) |
| plain TOP10 | + DOT ETH AAVE TRX BNB | +0.074 (n=157) vs −0.010 | +0.174 (+42R) vs +0.001 (−13R) | PASS |
| volume TOP5 | DOGE ETH AVAX XLM XRP | +0.344 (n=55) vs +0.107 | **+0.850 (+95R)** vs +0.168 (+34R) | PASS |
| volume TOP10 | + BTC BNB TRX ETC LINK | +0.234 (n=136) vs +0.057 | +0.504 (+113R) vs +0.170 (+17R) | PASS |

- **Macro bull-only filter:** plain breakout PASSES. Bull-side edge beats transition + bear in all three periods:
  2020 +0.086 vs −0.245; 2021–24 +0.205 vs +0.155; 2025–26 +0.146 vs +0.021. The volume version FAILS (2020 +0.153 vs
  +0.172).
- Young bull alone is mixed: +0.23R edge in 2020, −0.05R in 2025 (n=103, E −0.43R).
- **Read:** coin choice persists out of sample. Coins that broke out well in 2021–24 kept doing so in 2020 and 2025–26,
  on both books; DOGE and AVAX top both lists.
- Caveats:
  - 4 coin-selection tests were run and 3 passed.
  - Holdout samples per coin are small (44–231 trades).
  - The forward paper tracks stay on all 20 coins unless the owner changes them.

## NY "degen" study: Kelly, tighter stops, targets (2026-10-02, owner ask). No positive variant.
Data:
- 107 live NY legs with 1m paths from entry.
- Out of sample: the same raw trigger on the 6.7y Tardis days, 1m bars.

Grid: stop 1/2/3/5 ATR (pessimistic intrabar), TP none/2/4 ATR, hold 30/60/240 min, 0.20% round trip.
Script (scratch): `ny_degen.py`.
- **Live paths:**
  - ATR(5m) ≈ 0.50% of price, so fees cost **0.40 ATR per trade**. A 1-ATR stop pays 0.4R in fees every time.
  - Winners' 1h adverse move: median 0.81 ATR (p90 2.2). Losers' median 1.96 ATR (p90 5.1).
  - 57% of legs go ≥ 1 ATR against, 33% ≥ 2, 16% ≥ 3, 7% ≥ 5. Tight stops cut winners, not just losers.
- **In-sample (live legs):** no combination is positive in R. The best is +0.02%/trade (3 ATR stop, 2 ATR TP, 4h);
  live-like (5 ATR, 60m) is −0.03R.
- **Out of sample (6.7y, NY hours, 775 legs):** every combination is negative.
  - The best is −0.107R (5 ATR, 30 min).
  - The in-sample "best" (3/2/240) is −0.22R; live-like is −0.108R.
  - All hours (3,628 legs): the best is −0.108R.
- **Kelly is negative** (−88% "of equity"), meaning the right size is zero. Monte Carlo, one year at live NY frequency
  (600 legs): fixed 10% or 25% risk per leg loses 90%+ in 100% of runs.
- **Read:** leverage and tighter stops amplify the edge you have, and here it is negative. The aggressive version that
  does have an edge is the wick catcher's market-wide tier: 2024–26 +4.1%/fill, win 68% (1m-corrected). Market-only at
  50% per fill gave +79%/yr with maxDD −10% (2024–26 holdout).

## Post-crash BASE BREAKOUT on every perp listed since 2023 (2026-10-02, owner order "Test"; the owner's VELVET-type setup)
Universe: 667 USDT-M perps first listed 2023-01 → 2026-09, including 106 delisted (survivorship-free; monthly archive for
removed symbols). 492 of them were at some point ≥ 70% below their all-time high.
- **Signal:** daily close above the prior 20-day high, while ≥ 70% below the ATH, listing age ≥ 30 days. V1 adds volume
  ≥ 2× the 20-day mean.
- **Trade:** next-day open, 0.30% round trip.
- **Control:** random entries on the same coins while they meet the same ≥ 70%-down condition.
- Script (scratch): `base_breakout.py`.

| variant / exit | 2023–24 mean (median, win) | 2025–26 mean (median, win) | 2025–26 edge vs random | verdict |
|---|---|---|---|---|
| V0, stop under base, 20d | −0.3% (−7.4%, 35%) | +0.7% (−5.9%, 38%), n=2,056 | +1.6%, t 0.19 | fail |
| V0, 2 ATR, 20d | −3.1% | −0.6% (−11.0%, 29%) | +0.7% | fail |
| V0, base stop + 10d-low trail | −1.3% | −2.8% (−8.8%, 27%) | −1.4% | fail |
| V0, base stop + prior-breakdown target | +0.0% | +0.9% (−12.6%, 41%) | +2.1%, t 0.31 | fail |
| V1 (volume), stop under base, 20d | +3.7% (n=143) | −0.7% (−8.3%, 35%), n=1,612 | +0.2% | fail |
| V1, 2 ATR / trail / target | −4.3% / +0.2% / −0.5% | −1.8% / −3.5% / +0.1% | −0.5 / −2.2 / +1.4% | fail |

- **Read:** mechanically, breakouts in coins ≥ 70% down are coin flips with a fat right tail. Means sit around 0, medians
  are −6% to −13%, and win rates are 25–43%. Most fail; a few big winners pay for them.
- VELVET's own volume-breakout signal on 2026-08-12 lost −58%.
- Delisted coins' trades average −3 to −10%.
- The owner's discretionary selection (clean base, trendline, context) is not captured by this rule and is untested. A
  journal of his picks would show whether it beats this base rate.

## Past-month scan (2026-09-02 → 10-02) and an alt-breadth filter (2026-10-02, owner question)
Scripts (scratch): `month_scan.py` and an inline breadth split.
- **Month:** 517 trading perps; median 30-day return +19.1%; 72 up > 50%, 21 doubled. A broad alt rally (young bull).
- **Top 12 (hindsight):** BULLA +370%, QNT +286%, MOVR +273%, AKE +257%, BR +248%, BTW +238%, PHA +198%, LSK +196%,
  MUBARAK +190%, ONE +189%, NEAR +155%, ARX +131%.
  - The 4h breakout caught most of them: QNT +21.5R (+37.5R with volume), LSK +15.6R, PHA +13.8R, MUBARAK +12.5R.
  - The daily base breakout caught BULLA +155%, MOVR +220%, PHA +202%, ONE +98%.
- **Denominator, same month, all 517 perps:**
  - 4h breakout: 1,876 trades, mean +0.21R, median −1.02R, win 37%.
  - 4h breakout + volume: 1,184 trades, mean +0.11R.
  - Base breakout: 259 trades, mean +10.0%, median +4.3%, win 61%. A good month because everything rose.
- **VELVET's big-R trades** (4h breakout + volume): June 2026, +267% (+40R) and +809% (+54R) as it ran from 0.105 to 1.32.
  All 16 VELVET trades: +90R from 4 wins and 12 losses.
- **Breadth filter test** (base breakout, trades taken only when the median 30-day return of all perps > +10%, or > 0%):
  - 2023–24: IN beats OUT (+6.4% vs −2.2%).
  - 2025–26: REVERSED (−13.9% vs +2.3%, win 14%). Bought into hot breadth, breakouts are late in the rally.
  - No stable rule identifies the good months in advance.

## 4h breakout on ALL perps, 2023 → 2026-10 (2026-10-02, owner order "Yes, I need it")
Universe: 817 USDT-M perps with ≥ 300 4h bars, including delisted (137 delisted coins traded). 20 majors vs 782 OTHERS.
- **Rule:** registered (pivot-3 cross, close > EMA200, 2 ATR stop, 36 bars); costs 0.20% majors / 0.30% others.
- **Controls:** random longs, and trend-matched random longs (close > EMA200).
- **Periods:** discovery 2023–24, holdout 2025–26. Script (scratch): `bo4_allperps.py`.

| book | discovery E / edge vs random (t) | holdout E / edge vs random (t) | holdout total | verdict |
|---|---|---|---|---|
| OTHERS, BO | +0.063R / +0.064R (0.55), n=10,943 | **−0.116R / −0.009R** (−0.11), n=17,263 | **−1,997R** | FAIL |
| OTHERS, BO+VOL | −0.012R / −0.011R | **−0.147R / −0.040R** | **−1,671R** | FAIL |
| MAJORS, BO | +0.267R / +0.144R (0.93) | +0.070R / +0.136R (0.71) | +64R | (reference) |
| MAJORS, BO+VOL | +0.370R / +0.248R (1.32) | +0.205R / +0.271R (1.03) | +115R | (reference) |

OTHERS by year (BO): 2023 +233R, 2024 +453R, **2025 −1,290R, 2026 −707R**.

**By liquidity tier** (coin median daily quote volume; a full-history label, so mildly forward-looking):

| tier | BO edge, discovery → holdout | BO+VOL edge, discovery → holdout | trades |
|---|---|---|---|
| > $100M | +0.218 → +0.091R | +0.224 → +0.177R (vs trend +0.241) | 1,215 / 769 |
| $20–100M | about +0.08 in both periods | about +0.08 in both periods | — |
| < $20M (most coins) | +0.046 → −0.031R | −0.048 → −0.073R | 22,064 BO |

- **Read:**
  - The long tail of illiquid alts is where breakouts bleed: winners like SOON and MOVR exist, but 3 of 4 trades lose
    −1R and costs are higher.
  - Liquid alts (> $100M/day) behave like the majors: a small positive edge in both periods (t about 1).
- Delisted coins: 16% of OTHERS trades, mean −0.08 to −0.16R.
- All-alts breakouts are also impractical: 115 positions open at once on average (p95 282).

## Weekly chart patterns on liquid perps, 2020 → 2026 (2026-10-02, owner: "can't we filter them? Retests? Patterns")
Universe: 657 perps with full daily history (incl. delisted), liquid at the signal (trailing 28-day mean ≥ $20M/day).
- **Patterns** (mechanical, from confirmed weekly pivots): falling WEDGE (310), TRIangle (303), DouBLe bottom (117),
  long BASE (261).
- **Controls:** the plain 26-week-high breakout B26 (504) and random liquid weeks (7,177).
- **Entries:** breakout vs retest. **Exits:** stop or 26 weeks; measured-move target; 50-day-low trail. 0.30%.
- **Periods:** discovery 2020–23, holdout 2024–26. Script (scratch): `patterns_weekly.py`.

Mean per trade, breakout entry, stop or 26 weeks:

| | discovery 2020–23 | holdout 2024–26 |
|---|---|---|
| random liquid alt, held 26 weeks | +7.2% | **−19.5%** |
| plain 26-week breakout | +28.4% (t 2.34) | −31.2% |
| WEDGE | +9.6% (win 24%) | −12.9% (win 20%) |
| TRIangle | +3.0% | −23.9% (win 12%) |
| DouBLe bottom | +32.6% (n=35) | −31.1% |
| BASE | +36.8% (t 1.56) | −35.5% (win 12%) |

- **No pattern passes**, with any entry or exit:
  - Measured-move targets raise win rates to 42–75% with means about 0 or negative.
  - Retests fill 62–89% and do not fix it.
- **Market regime dominates:** the same rules made +28–37% in the 2020–21 alt bull and lost 20–35% in 2024–26.
- **By BTC macro state at the signal** (B26 / BASE / TRI):
  - young bull −29% / −37% / −29%
  - bull +2% / +11% / −10%
  - transition +12% / +42% / +36%
  - bear −6% / −16% / −23%

  Few episodes per state; the 2024-11 → 2025-03 young bull was an alt top.
- **Filters:** volume, RSI > 50 and RSI divergence are inconsistent across periods, except WEDGE + weekly RSI bullish
  divergence, where IN beats OUT in both periods (+42.9% vs −0.7%; −8.8% vs −15.1%; n = 34 / 59). It is still negative
  in the holdout: +73.5% in transitions, +14.6% bull, about −11% in bear / young bull.

## 4h patterns + RSI divergence (2026-10-03, owner: "200 pairs, past year, not delisted: WR, total R...")
Past year: 200 most liquid TRADING perps (survivorship, by request), signals 2025-10-03 → 2026-09-25.
Out-of-sample check: 805 perps including delisted, liquid at the signal (≥ $14M/day), 2023-01 → 2025-09.
- **Patterns:** from confirmed 4h pivots (pivot-3): falling wedge, triangle, double bottom (lows within 3%), tight base.
- **Divergence:** price lower low with RSI14 higher low at the two pattern lows (impossible for triangles by construction).
- **Trade:** entry next 4h open, stop at the pattern low (1R), 0.30% round trip.
- **Exits:** stop or 7 days; or measured-move target (max 14 days).
- Scripts (scratch): `patterns_4h.py`, `patterns_4h_oos.py`.

Stop or 7 days:

| set | past year: n, WR, avg R, total R, PF | 2023–25 out of sample: n, WR, avg R, total R, PF |
|---|---|---|
| WEDGE + divergence | 149, 26%, +0.21, +31R, 1.25 (ex-HEI −0.11R, −16R) | 291, 35%, **+0.33**, +95R, 1.51 |
| WEDGE no divergence | 308, 21%, −0.30, −93R | 830, 29%, −0.01, −4R |
| DOUBLE BOTTOM + divergence | **77, 47%, +0.24, +18R, 1.68**, max DD −7R, longest losing run 5 | **235, 41%, +0.17, +39R, 1.32** |
| DOUBLE BOTTOM no divergence | 460, 32%, −0.06, −28R | 1,321, 39%, +0.00, +5R |
| TRIANGLE | 377, 26%, +0.12, +45R (top-3 trades +92R) | 1,119, 35%, +0.08, +95R |
| BASE | 653, 36%, +0.04, +23R | — |
| RANDOM (stop = 30-bar low) | 32,934, 29%, +0.08 (fat-tailed) | 83,732, 31%, −0.08 |

- **Measured-move exit:** past year DOUBLE BOTTOM + divergence had WR 74%, +0.24R. It FAILS out of sample (−0.12R).
  Wedge and triangle versions are negative.
- **Read:**
  - Divergence separates both wedges and double bottoms in both windows: about +0.2–0.5R over the same pattern
    without divergence.
  - DOUBLE BOTTOM + divergence with a 7-day exit is positive in both windows and not carried by one coin. Small n,
    t ≈ 1.3.
  - WEDGE + divergence is positive in both windows, but the past year's +31R is one trade (HEI +47R).
  - Fat-tailed: win rates 26–47%.

**Follow-up: the same patterns on 1h and 15m** (same 200 coins and year, same bar counts, price thresholds scaled by
√time, 0.30% costs, exit = stop or 42 bars). Script (scratch): `patterns_ltf.py`.

| set (stop or 42 bars) | 4h | 1h | 15m |
|---|---|---|---|
| DOUBLE BOTTOM + divergence | n=77, WR 47%, **+0.24R**, +18R | n=348, WR 34%, −0.13R, −45R | n=1,233, WR 37%, −0.13R, −158R |
| WEDGE + divergence | n=149, WR 26%, +0.21R, +31R (one trade) | n=523, WR 27%, −0.26R, −137R | n=1,753, WR 28%, −0.29R, −510R |
| TRIANGLE | +0.12R, +45R | +0.01R, +9R | −0.23R, −1,182R |
| ALL PATTERNS | 2,024 trades, −0.00R, −4R | 8,474, −0.08R, −704R | 31,806, −0.19R, −6,185R |
| RANDOM | +0.08R | −0.16R | −0.36R |

- Measured-move exits are negative on both lower timeframes.
- **Read:** lower timeframes are worse. Stops get tighter, so the fixed 0.30% costs eat more of each R. The divergence
  filter stops separating (on 1h, DBL with divergence is worse than without). Only 4h holds anything.

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
| Macro-cycle split (2026-10-02) | today = young bull (since 09-10); NY trigger has no edge in any macro state; wick pays in all, best in transitions |
| Breakout coin selection / macro filter (2026-10-02) | top-10 coins (from 2021-24) beat the rest in both holdouts on both books; volume top-5 strongest; bull-only helps plain, not volume |
| NY degen sizing / stops (2026-10-02) | all stop/TP/hold variants negative out of sample (best -0.107R); Kelly < 0; any big fixed size ruins |
| Post-crash base breakout, 667 perps since 2023 (2026-10-02) | no edge: means ~0, medians -6..-13%, win 25-43%, edge vs random within +-2% |
| Past-month scan + alt-breadth filter (2026-10-02) | Sep alt rally: 4h BO across 517 perps +0.21R, base BO +10%; breadth filter flips sign 2023-24 vs 2025-26 |
| 4h breakout on all 817 perps (2026-10-02) | alts FAIL (2025-26 -2,000R); illiquid tail bleeds; liquid alts > $100M/day ~ majors (+0.1-0.2R edge, t ~1) |
| Weekly chart patterns, liquid perps 2020-26 (2026-10-02) | no pattern / entry / exit passes; alt regime dominates (+28% 2020-23 vs -31% 2024-26); wedge+RSI-div best relative, still negative in holdout |
| 4h patterns + RSI divergence (2026-10-03) | double bottom + divergence, 7-day exit: +0.24R (past yr, n=77) and +0.17R (2023-25 OOS, n=235); divergence separates wedges/DBL in both windows; measured-move exit fails OOS |
| Patterns + divergence on 1h / 15m (2026-10-03) | all negative (1h all patterns -704R, 15m -6,185R); divergence stops separating; only 4h holds |

## Weekly triple bottom: buy the third touch vs wait for the neckline (2026-10-03, owner: "Check whether CLOUSD really has a triple bottom and if it's a good setup")
- **Rules (fixed before running; script `edge/triple_touch.py`):**
  - Data: all USDT-M perps from listing incl. delisted, weekly bars.
  - Setup: the two lowest weekly swing lows of the past 52 weeks, ≥ 6 weeks apart, within 8%, each followed by a bounce
    of ≥ 1.5×. A third week then trades back into the zone and closes no more than 8% below it.
  - Neckline = the lower of the two bounce highs.
  - Entry A: buy the third touch, stop 3% under the lows.
  - Entry B: first weekly close above the neckline.
  - Exits: X1 = stop or 26 weeks; X2 = stop or target (A: the neckline; B: measured move). Cost 0.30%.
  - Control: random weekly entries on the same coins.
  - v2 picks the two lowest swing lows. v1 used the last two swing lows, which misses CLO (higher lows in Jul/Aug);
    v2 was fixed before either result was read.
- **CLO qualifies:**
  - Lows 0.0530 (wk 02-02) and 0.0550 (wk 05-18); third touch 0.0510 (wk 09-21, weekly close 0.0613).
  - Neckline 0.185 (+190% from 0.063). 93% below its high.
- **A, buy the third touch, X1:**
  - 2024–26: n=255, WR 4%, −0.63R, PF 0.34, 90% stopped out.
  - Random entries on the same coins and years: −0.61R. The setup adds nothing.
  - All years: n=344, +0.06R, positive only because of 2023 (34 trades, +199R; alt bull).
  - CLO-like subset (≥ 70% below the high, < $20M/day): n=199, −0.01R.
- **A with the neckline target (X2):** 2024–26 n=260, WR 17%, −0.38R.
- **B, confirmed breakout, measured-move target (X2):**
  - n=71, WR 69%, +0.21R, PF 1.84; 2024–26 n=42, +0.08R, PF 1.26.
  - Only 18% of third touches ever confirm.
- **v1 (last two swing lows):** same picture. A 2024–26 n=69, −0.75R, 94% stopped; B n=12–15.
- **Read:** buying a third touch is a lottery ticket (about 1 win in 9; the top 5% of trades carry all the profit), and
  it has lost since 2024. Only waiting for the neckline break held up, and that sample is small.

## Wick catcher + perp discount: losses in R units, for sizing a live engine (2026-10-03, owner: "$5 risk per position, or whatever minimum Binance allows")
- **Method:** 1R = 3 × the coin's hourly ATR at entry (the paper tracks' reporting unit). Neither strategy has a stop.
  - Wick: the 1,519 1m-corrected research fills (20 coins, 2020-10..2026-09). R rebuilt from 5m data; 2024 check n=233
    +2.53% = the frozen basis.
  - Discount: the reader's own `simulate()` on the research 5m perp + spot data. 2024 check n=142 +4.20% (basis 143 /
    +4.15%).
  - Script: `edge/wd_tail_R.py`.
- **First forward week (from 09-27 10:00Z):**
  - Wick: TRX +1.46R and LINK +1.63R.
  - Discount: ATOM +0.19R and TRX +0.47R.
  - 4 of 4 won, +3.75R. Far below the formal reads (wick n ≥ 60 / 10 days; discount n ≥ 80 / 10 days).
- **Wick, 2024–26:**
  - n=516, WR 59%, +0.13R/fill, +67R. 2025: −3R.
  - Market-wide-hour fills: n=103, +0.58R. Coin-specific: n=413, +0.02R.
  - Worst fill −6.7R; 3.9% of fills worse than −3R.
  - Worst day 2024-04-12: 19 of 20 coins filled and kept falling, −47.6R. The market-wide flag did not fire (the crash came
    in the bid hour, not before it).
  - Months positive 58%; top-5 days = 149% of net.
- **Discount, 2024–26:**
  - n=512, WR 57%, +0.28R/trade, +143R; 2026 YTD −26R.
  - Worst trade −2.45R (all years −2.85R); worst day −3.9R (all years −14.3R).
  - Months positive 41%; top-5 days = 110% of net.
- **Combined 2024–26:** +210R, max drawdown −84R, worst day −50.5R; about 7 trades a week; up to 20 positions open at
  once per strategy.
- **Binance minimum order size** (MIN_NOTIONAL $5–50) is not the constraint. The smallest uniform $ per R that clears it
  on all 20 coins today is about $0.8 (LTC: $20 minimum, 1R = 4.1%).
- **At $5 per R:**
  - Position value $97–757 per coin (median about $130); about $4.2k for all 20 resting wick bids.
  - History: +$1,050 over 2024–26, max drawdown −$420, worst day −$252.

## Disaster stop for the wick catcher + perp discount (2026-10-03, owner: "Disaster stop sounds like a safe idea")
- **Rules (fixed before running; `edge/disaster_stop.py`):**
  - The readers' rules plus a stop-market k R below entry (1R = 3 × ATR1h), k ∈ {3, 4, 5, 6, 8, 10}.
  - 0.2% extra slippage on every stop exit.
  - Pessimistic: a wick fill bar that reaches the stop counts as stopped; a stop and the target in one bar → the stop.
  - No-stop check: wick 2024 n=233 +2.53% = basis.
- **Wick catcher, 2024–26:**
  - No stop: +67R, max drawdown −79R.
  - −3R: −35R. −4R: −24R. −5R: −34R. −6R: −27R (DD −149R). −8R: +5R (DD −115R). −10R: +30R (DD −104R).
  - Every stop level made both the result and the drawdown worse.
  - Why:
    - The bids sit in wicks that often extend before they recover: of the trades stopped at −6R, 92% would have finished
      above the stop exit (average without the stop −2.7R vs −6.5R at the stop).
    - A stopped coin frees its slot and the next hourly bid catches the same falling coin again: worst day −68R at −6R
      vs −47.6R without a stop.
- **Perp discount (4h hold):**
  - Stops at −5R or further never trigger.
  - −3R costs 17R over 2024–26 (+143 → +126R).
- **Decision basis:**
  - Worst trade without a stop on these 20 coins in 6 years: −8.8R.
  - A catastrophe stop at −12R would never have triggered, so it has no historical cost. It caps a collapsing coin
    (LUNA-type, not in this universe's history) at about −12R per leg.
  - Pair it with a 24h no-new-entry cooldown on a coin after a stop-out.

### CORRECTION (2026-10-03, same day): the −12R stop does trigger
- **Error:** "−12R never triggered in 6 years" used the worst FINAL trade (−8.8R), not the intra-trade low.
- **Max adverse excursion (MAE)** on the 20 coins, 2020–26, no stop (`edge/mae.py`):
  - Wick: median −1.05R, p99.9 −14.2R, worst −16.6R (FIL 2022-12-16, finished −8.8R). 5 trades went below −12R, 4 of
    them on 2025-10-10 (XRP −14.4R / −53%, ADA −13.9R / −62%, AVAX −13.8R, XLM −12.3R); they finished at −1.6 to −3.0R.
  - Discount: worst −6.0R.
- **Engine rules 2024–26 on the 20 coins:**
  - Wick with a −12R stop: +26.5R, DD −106R (4 stops, all 2025-10-10 20:55).
  - Wick with no stop: +65.6R, DD −79R.
  - Discount: unchanged.
- **Fix:** the engine default is now `stop_r: 20`, beyond every intra-trade low seen. It stays a catastrophe-only cap of
  about $20 per leg at $1/R.

## More coins for the wick/discount engine? (2026-10-03, owner: "Should this engine be tracking more pairs?")
- **Rules (fixed before running; `edge/expand_test.py` v2):**
  - B = the next 40 USDT perps by mean daily quote volume over their 2023 trading days (≥ 60 days; known before the
    test). Delisted and migrated coins kept (MATIC, FTM, EOS, TOMO, RNDR, SXP, …).
  - Engine rules incl. −12R stop + 24h cooldown. Entries 2024-01..2026-09; delisted coins exit at their last price.
  - PASS: B's avg R > 0 in both 2024 and 2025 per strategy, AND the A+B return/drawdown ≥ A's.
  - v2 fix: v1 divided by a zero R unit on flat post-delisting bars; the discount now skips dead-market bars.
- **Wick:**
  - A (current 20): n=515, +0.052R, +27R.
  - B: n=785, −0.050R, −39R; 2025 −0.64R/trade; worst day −139.5R.
  - A+B: max DD −320R vs −106R. FAIL.
- **Discount:**
  - A: n=512, +0.278R, +142R, return/DD 5.4.
  - B: n=25,707, −0.041R, −1,067R. On thinner coins the perp trades ≥ 30 bps under spot routinely, so the signal is
    noise, not forced selling.
  - B ≥ $100M/day: n=2,533, +0.029R. Post-hoc subset, tiny, not proposed.
  - FAIL.
- **Portfolio:** A +169R, DD −84R vs A+B −937R, DD −1,320R.
- **Verdict:** keep the 20 coins.

## Improving the wick/discount engine: what public sources suggest, tested (2026-10-04, owner: "find how to improve it. How do others trade them? What improves it?")
- **Sources:**
  - Liquidation-cascade guides: ladder bids in thirds, quick 2–4% take-profits, crowded longs (L/S > 1.5), weekend
    cascades.
  - arXiv 2608.21888: short-horizon reversal peaks at about 15 min and is gone by 4h; stronger after bigger, flow-driven
    moves; book depth and time of day don't matter.
  - Hyperliquid HLP: liquidity providers earn most on the worst days.
  - Also tested: earlier passes not yet in the engine.
- **Rules (fixed before running; `edge/improve_wd.py`):**
  - 20 coins, 2020-10..2026-09; the engine baseline; one change at a time.
  - PASS: total R / |max DD R| ≥ baseline in every period (2020–21 / 2022–23 / 2024–26) and total R > 0 in each.

| Variant | Total R (by period) | Max DD R | Total / DD | Verdict |
|---|---|---|---|---|
| Wick baseline (k5, TP or 24h) | +167 / +145 / +66 | −54 / −42 / −79 | 3.1 / 3.5 / 0.8 | — |
| Ladder k5 / 6.5 / 8, ⅓ R each | +149 / +140 / +73 | −29 / −20 / −51 | 5.2 / 7.2 / 1.4 | **PASS** |
| + BTC-dump add-on (2nd unit at the fill-bar close when BTC ≤ −1.7% vs the bid hour) | +327 / +295 / +169 | −80 / −53 / −92 | 4.1 / 5.6 / 1.9 | **PASS** |
| Ladder k4 / 5 / 6 | +147 / +121 / +20 | | 2.6 / 2.8 / 0.2 | fail |
| Time exit 4h / 8h | 4h: +138 / +122 / +125 | | 4h: 3.9 / 2.2 / 2.9; 8h: 6.1 / 2.4 / 3.2 | fail (2022–23) |
| TP at half the retrace / fixed +3% | | | | fail |
| Weekend only / weekday only | | | | fail |
| Crowded longs (L/S ≥ 1.5) | | | | fail |
| Market-wide fills only | | | 59.6 / 0.8 / 6.9 | fail (2022–23, n=29) |
| Cap 5 fills/hour, 10 open | | | 2.8 / 3.1 / 0.8 | fail |
| Discount baseline (−30 bps, 4h) | +239 / +123 / +143 | −17 / −12 / −27 | 13.8 / 10.0 / 5.4 | — |
| Discount only when BTC 1h ≤ −1% | +186 / +107 / +166 | −13 / −4 / −6 | 14.0 / 25.1 / 27.3 | **PASS** (not falling: 2024–26 −23R) |
| Discount hold 15m / 30m / 1h / 2h | | | | all worse (the 15-min academic reversal does not carry over) |
| Discount −50 bps | +185 / +81 / +152 | | 11.4 / 13.1 / 53.5 | fail (2020–21) |
| Discount crowded longs | | | | fail |

- **Combination (post-hoc, disclosed; tested once; `edge/improve_combo.py`):** wick ladder + add-on + discount
  BTC-falling vs the current engine, both strategies at the same $ per R.
  - Total: +471 / +389 / +346R vs +406 / +268 / +209R.
  - Max DD: −33 / −20 / −68R vs −41 / −37 / −84R.
  - Worst day −38R vs −50.5R; 11.5 vs 17.3 trades a week.
  - Every year positive (2026 YTD +26R vs −1R).
- **Live constraint:** at $1/R a ⅓-R rung is below Binance's minimum order on BTC, LINK, LTC, BCH and ETC ($20–50
  minimums). Either size those rungs at the minimum (a slightly larger R there) or run about $3/R.

## Run the original wd engine alongside v2? (2026-10-04, owner: "keep the quiet-week (original) setup run concurrently? It got almost 4R")
- **Rule (fixed before running; `edge/both_versions.py`):** BOTH (v1 + v2 positions together) is better only if its
  total R / |max DD R| >= v2's in every period, since v2 can always be sized up to the same total.

| | Total R (2020–21 / 2022–23 / 2024–26) | Max DD R | Total / DD | Worst day |
|---|---|---|---|---|
| v2 alone | +471 / +389 / +346 | −33 / −20 / −69 | 14.4 / 19.4 / 5.0 | −38R |
| v1 alone | +406 / +268 / +209 | −41 / −37 / −84 | 9.9 / 7.3 / 2.5 | −51R |
| BOTH | +876 / +658 / +554 | −74 / −53 / −151 | 11.9 / 12.3 / 3.7 | −89R |

- **Verdict:** BOTH fails in every period. For more profit, raise v2's $ per R instead. v2 at $1.60/R ≈ BOTH's 2024–26
  total (+$553) with a max DD of −$110 vs −$151 and a worst day of −$61 vs −$89.
- **Stress vs quiet days:** where the money comes from.
  - Stress days = a market-wide selloff hour or a BTC-dump fill. They occur on ~105–120 days a year; BTC-dump add-on
    days run 12–29 a year.
  - v2: +1,094R on stress days vs +111R on other days.
  - v1: +630R vs +254R; since 2024, other days made only +19R.
  - The forward paper week's +3.76R (4 trades) was quiet-day trading, which has earned little since 2024.
- **By year, v2 vs v1:**
  - 2020: +147 vs +111. 2021: +324 vs +295. 2022: +42 vs +45. 2023: +347 vs +223.
  - 2024: +203 vs +153. 2025: +116 vs +58. 2026: +26 vs −1.

## wd engine by alt-vs-BTC regime, and switching versions (2026-10-04, owner shows the TOTAL3ES/BTC monthly Bollinger squeeze: "which version are we going with?")
- **Regime (`edge/altseason_split.py`):** per calendar month, the 18 alts (ex BTC, ETH) equal-weight return minus BTC's.
  Labels are contemporaneous.

| Months | v1 original | v2 improved |
|---|---|---|
| Strong alt season (alts beat BTC by > +10%, 19 months) | +466R (+24.5/month) | +426R (+22.4/month) |
| Alts beat BTC by 0..+10% (17 months) | +120R | +125R |
| BTC leads (alts lag, 44 months) | +294R (+6.7/month) | +654R (+14.9/month) |

- **What drives the difference:**
  - The single 5-ATR bid at full R: +296R in strong alt months vs +15R in BTC-led months.
  - The ladder: +202R vs +108R.
  - Discount trades without BTC falling: +55R vs −17R.
  - v1 is the alt-season specialist; v2 is the crash / BTC-led specialist.
- **Switching (rule fixed before running):** v1 in a month after alts beat BTC, else v2; PASS = total/DD ≥ v2 alone in
  every period.
  - 1-month signal: 10.3 / 9.1 / 3.4 vs v2 14.4 / 19.4 / 5.0. Fail.
  - 3-month (secondary): 13.2 / 8.5 / 3.5. Fail.
- **Verdict:** keep v2.
  - If the squeeze breaks toward alts, v1 earns ~10% more in those months.
  - If it breaks toward BTC, v2 earns ~2.2× more.
  - A Bollinger squeeze predicts a big move, not its direction.

## wd engine on $100: intra-crash drawdown and a 6-coin test universe (2026-10-05, owner: "Can we start it with $100?" / "limit the number of pairs while we test?")
- **Method:** mark-to-market at 5m bar lows across all open legs on the six worst crash days (`edge/crash_mtm.py`,
  `edge/subset_mtm.py`). Conservative: assumes every coin's low lands in the same 5m bar. v2 rules, R at $1/R = dollars.
- **All 20 coins:**

| Day | Worst moment | End of day |
|---|---|---|
| 2025-10-10 | −203R | +133R |
| 2024-01-03 | −114R | +80R |
| 2021-09-07 | −104R | −13R |
| 2026-01-18 | −75R | −29R |
| 2024-04-12 | −72R | −8R |
| 2023-08-17 | −65R | +118R |

  - v1 on 2025-10-10: −167R / +55R.
  - The closed-trade drawdown (−66 to −84R) understates the account risk: on cross margin the intra-crash low is what
    liquidates.
- **Top-N by trailing-12-month volume** (BTC ETH SOL XRP DOGE BNB ADA NEAR LINK AVAX …), since 2024 / worst moment:
  - Top 4: +56R / −43R. Top 5: +73R / −56R. **Top 6: +90R / −61R** (closed-trade DD −15R).
  - Top 8: +132R / −79R. Top 10: +181R / −106R. All 20: +380R / −203R (this run; the earlier v2 total was +346R).
- **Set for the $100 test:**
  - Universe = top 6. At $1/R the worst historical moment leaves ~$39.
  - Leverage 20× (cross-margin liquidation follows the maintenance margin; 20× keeps free margin so crash-time rungs
    are not rejected).
  - Equity floor $60 on the REALIZED wallet balance (an unrealized-equity floor would pause entries at every crash low).

## Pump precursors for a "pump engine" (2026-10-05, owner: "I'm sure there's some sort of give away before a coin goes to the moon" — PUMPBTC, VELVET, BOB)
- **Data and rules (fixed before running; `edge/pump_test.py`):**
  - 880 Binance USDT-M perps incl. delisted, daily bars 2024-01 → 2026-09.
  - Funding: the public monthly archive (daily sums).
  - Signals at the day-t close: S1 flush (≤ −20% on ≥ 10× volume); S2 funding ≥ +1%/day; S3 funding ≤ −1%/day;
    S4 coil break (10d volume ≤ 0.5× the 60d mean, then a ≥ 3× volume day at +5..15%); S5 pump day (≥ +30% on
    ≥ 5× volume), chase and fade.
  - Entry at the next open; stop under the signal low or 20% (fade: +30%); 7-day exit; 0.40% cost.
  - PUMP7 = +50% within 7 days vs the base rate of same-tier coin-days.
  - PASS = lift ≥ 2× and a positive trade beating random entries in 2024 AND 2025–26, holdout t ≥ 1.5.

| Signal | 2025–26: +50% within 7d (base ~4%) | Lift | Trade net | Win | Verdict |
|---|---|---|---|---|---|
| S1 flush | 19.2% (n=104) | 4.7× | −6.35% | 12% | fail (2024 n=9) |
| S2 funding ≥ +1% | 22.2% (n=45) | 5.4× | −7.19% (median −20%) | 11% | fail |
| S3 funding ≤ −1% | 10.4% (n=1,156) | 2.5× | −4.30% | 24% | fail (2024 lift 1.4×) |
| S4 coil break | 6.1% (n=1,036) | 1.5× | −1.37% | 29% | fail |
| S5 pump day, chase | 26.0% (n=670) | 6.2× | −2.41% (median −20%) | 22% | fail |
| S5 pump day, fade | — | — | +1.62% (median +9.3%) | 55% | fail (2024 +0.15%; holdout t 1.39) |

- **Read:** the "tells" are real predictors of a violent move. The odds of +50% in a week rise 2.5–6×, but drops come
  just as often and usually first, so a stopped long loses (win rates 11–30%).
- **The owner's examples:**
  - PUMPBTC and BOB both flushed 2026-10-01 (−29% / −22% on 18–21× volume, funding to 1–5%/day).
  - That falls after the test window (exits need 7 days of data); VELVET pumped and then fell.
- **Verdict:** no pump engine on these rules. Fading pump days is the only positive line, small and unproven, and
  short-squeeze risk applies.

## Expected ROI: main wd engine (6 coins) + proposed second pot (14 coins, max 2 open), $100 each at $1/R (2026-10-05)
- **Source:** `edge/roi_by_year.py`, v2 rules. The second pot = single 5-ATR bid + BTC-dump add-on + discount only when
  BTC falls, max 2 trades open.

| Year | Main (6 coins) | Second pot (14 coins) | Both ($200) |
|---|---|---|---|
| 2020 (Oct–Dec only) | +$44 | +$20 | +$64 |
| 2021 | +$97 | +$77 | +$174 |
| 2022 | +$34 | −$17 | +$17 |
| 2023 | +$130 | +$49 | +$179 |
| 2024 | +$57 | +$12 | +$69 |
| 2025 | +$22 | −$2 | +$20 |
| 2026 (to Sep 20) | +$11 | +$12 | +$23 |

- **Since 2024, annualized:** main ≈ +33%/yr on $100; second pot ≈ +8%/yr; both ≈ +20%/yr on $200. Earlier years were
  stronger; the trend is down.
- **Risk at $1/R:**
  - Main: closed-trade max DD −$15; worst crash moment −$61 (2025-10-10).
  - Second pot: closed-trade DD −$23; worst crash moment about −$56 (estimate: 2 positions + add-ons).
