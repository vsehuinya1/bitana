# Scalping research — pre-registered test rules
Rules are written here BEFORE each test runs; results are appended under each test afterwards, never edited.
Costs (all tests): step-1 baseline (`reports/scalping_baseline_2026-10-06.md`). Taker round trip = 10 bp + spread
(BTC/ETH 0.05, BNB 0.15, SOL/XRP/DOGE/LINK ~0.8–1.0) → 10.1 bp for BTC/ETH/BNB, 11 bp for every other coin.
Maker-in/taker-out = 8 bp (reported as sensitivity only, never as the pass criterion for a time-based entry).

## Shortlist revision (2026-10-06)
- Removed F (new-listing fade): overlaps the pump-day fade already found small/unproven (c806bb2), holds > 1h, squeeze risk.
- Demoted E (round numbers) to reserve.
- Added D0: taker-imbalance aftermath on 5m klines (taker-buy volume, 20 coins, 2020-26, in hand) — cheap pre-screen for D.
- Added G: scheduled macro releases (CPI / NFP / FOMC) — σ spikes there, so cost/σ falls 2–4×; needs a release calendar.
- Cross-cutting: every test also reports a high-volatility subset (the only place cost/σ gets small).
Order: B → A → D0 → C → G → D.

## B — Intraday time-series momentum (registered 2026-10-06, before any run)
Data: `edge/k5m/{SYM}.npy`, 20-coin universe, 5m perp bars, 2020-09-01 → 2026-09-24. Entry = open of the first bar of the
trade window, exit = close of its last bar (taker, no stops). Direction = sign of the signal return (0 → no trade).
Cells:
- B1 (paper replication, UTC day): signal 00:00–00:30 UTC → trade 23:30–24:00 UTC, every day.
- B2 (US open, continuation): signal 09:30–10:00 New York (DST-aware) → trade 10:00–11:00 NY, Mon–Fri.
- B3 (US open → close): signal 09:30–10:00 NY → trade 15:30–16:00 NY, Mon–Fri.
- Each cell × {all days, tail days}: tail = |signal| ≥ 80th pct of the coin's previous 60 signal values (no look-ahead).
Statistic: per day, mean net bps across coins that traded → day series; mean and t on days (day-clustered).
Split: discovery 2020-09-01 → 2024-12-31, holdout 2025-01-01 → 2026-09-24.
Placebo: the same signal/trade geometry started at each of the 48 half-hour offsets of the day (UTC grid); report the cell's
discovery gross mean percentile in that distribution.
PASS (all): discovery net mean > 0 with t ≥ 2; holdout net mean > 0; discovery gross ≥ 90th pct of placebo offsets.
6 cells tested → a single discovery t ≈ 2 is expected by chance; holdout is the arbiter.

## A — BTC jump → alt catch-up (registered 2026-10-06, before any run)
Data: perp aggTrades → 1s mid grid (`research_cache/scalp/build_grid1s.py`; mid = (last buy-aggressor px + last
sell-aggressor px)/2), BTC + ETH SOL XRP DOGE 1000PEPE, 2026-07-24 → 2026-10-05 (74 days).
Discovery = 2026-07-24 → 08-29, holdout = 08-30 → 10-05.
Event: BTC 10s log return r_B(t−10s → t) with |r_B| ≥ 5·σ10, σ10 = std of the 360 non-overlapping 10s BTC returns in the
hour before the window. At most one event per 60s (first qualifying second).
Variants: (all) every event; (lag) alt moved less than half as much: sign(r_B)·r_A(same window) < 0.5·|r_B|.
Trade: alt, taker, direction sign(r_B); entry at the mid at t+1s (1s latency), exit at the mid at t+1s+h, h ∈ {10,30,60,300}s.
Net = gross − alt taker round trip (step 1: ETH 10.1, SOL 10.9, XRP 10.7, DOGE 11.2, 1000PEPE 10.9).
Control: BTC's own continuation over the same horizons (is it lead-lag or plain momentum?).
Statistic: pooled over alts; per day = mean net over that day's events; mean and t on days.
PASS: a pooled cell (2 variants × 4 horizons = 8) with discovery net > 0 and t ≥ 2, and holdout net > 0.

### A — RESULT (2026-10-06): FAIL
3,938 BTC-jump events (53/day, median |r_B| 6 bp). Pooled alt catch-up, gross: +0.2 to +1.3 bp at 10s–300s; net −9 to −13 bp,
every cell t ≤ −4 in discovery and holdout. Lag variant no better (gross −0.5 to +1.0). BTC's own continuation: +0.0 to +0.4 bp.
Exploratory (not registered), by jump size: alts move as much as BTC or more WITHIN the same 10s window (BTC 18 bp → alts 23 bp;
BTC 32 → 34). There is no lag left to capture on Binance at 1s resolution; the biggest jumps (n=70) revert after 60s (−5 to −7 bp).

## D0 — Taker-flow burst aftermath on 5m bars (registered 2026-10-06, before any run)
Data: `edge/k5mv/{SYM}.npy` (t o h l c volume taker_buy_volume), 20-coin universe, 2020-09-01 → 2026-09-24.
Flow: F = (2·taker_buy − volume)·close / mean(volume·close over the previous 288 bars) (net taker notional vs 24h average).
Event: F above the 99.5th pct (or below the 0.5th) of the coin's previous 8,640 bars (30 days), AND the bar's return has the
same sign as F (flow that moved price). Max one event per coin per hour.
Trade: direction sign(F), entry at the next bar's open, exit after h ∈ {15, 30, 60} min (taker).
Net = gross − taker round trip (10.1 bp BTC/ETH/BNB, 11 bp others).
Split: discovery 2020-09-01 → 2024-12-31, holdout 2025-01-01 → 2026-09-24. Statistic: per-day mean, t on days.
PASS (follow): discovery net > 0 with t ≥ 2, holdout net > 0.
FADE screen (goes to D with fill-accurate maker test, not a pass): discovery gross ≤ −8 bp with t ≤ −2 and holdout gross < 0.
Also reported: high-volatility subset (previous-day BTC 5m vol in the top 20% of the trailing 250 days).

### B — RESULT (2026-10-06): FAIL (all 6 cells)
Net −9.6 to −22 bp/day-trade, t −2.3 to −9.4 in discovery and holdout. B1 (paper replication) gross +0.2 / −0.05 bp: nothing.
Post-hoc observation (NOT a pass): B2 goes the OTHER way — the US-open half-hour move reverses in the next hour:
tail-day gross −11.3 bp (disc), −7.3 (holdout); placebo pct 8 (stronger reversal than 92% of other half-hour offsets);
high-vol days −15.8 / −5.7. A fade at taker cost (~11 bp) is ≈ break-even before any fill realism.
(Run note: first run was killed after 30 min for speed only; reindex replaced by an exact integer-index equivalent, verified.)

## B2F — US-open fade, out-of-sample on unseen coins (registered 2026-10-06, after B, before this run)
Origin: post-hoc reversal seen in B2. Its only clean test is data B never touched: the 40 `edge/k5m_x` perps
(2023 volume ranks 21–60), 5m perp bars 2023-12-01 → 2026-09-24.
Rule: B2 geometry (signal 09:30–10:00 NY, trade 10:00–11:00 NY, Mon–Fri), direction = −sign(signal); tail = |signal| ≥ 80th
pct of the coin's previous 60 signals; also all days. Cost: taker 12 bp (less liquid coins), maker-in/taker-out 9 bp (sensitivity).
Statistic: per-day mean across coins, t on days. Split reported: 2023-12 → 2025-03 and 2025-04 → 2026-09 (halves).
PASS: tail cell net (12 bp) > 0 with t ≥ 2 over the whole period AND net > 0 in both halves. Otherwise the US-open fade is dropped.

### B2F — RESULT (2026-10-06): FAIL → US-open fade dropped
40 unseen coins, tail days: gross +6.5 bp (t 0.88), net −5.5; half 1 gross −4.2, half 2 +15.9 (t 1.5). All days: gross +4.0 (t 1.0).

## C — Funding-settlement pressure (registered 2026-10-06, before any run)
Data: 5m perp bars for 60 coins (`k5m` 20-coin universe + `k5m_x` 40 coins), funding settlements from `funding_all`
(2023-12-01 → 2026-09-24; settlement times as recorded, so 4h/8h intervals are handled as they occurred).
Signal: f_prev = the previous settled funding rate (known before settlement s). Extreme if |f_prev| ≥ θ, θ ∈ {0.03%, 0.10%}.
Windows (taker, entry/exit at 5m bar opens; no funding cash flows counted):
- PRE  [s−30m, s], direction −sign(f_prev) (the paying side exits before settlement);
- POST30 [s, s+30m] and POST60 [s, s+60m], direction +sign(f_prev) (payers re-enter / receivers exit after settlement).
Cost: 20-coin universe 10.1/11 bp (as before), k5m_x coins 12 bp; maker-in/taker-out (−2.5 to −3 bp) as sensitivity only.
Split: discovery 2023-12-01 → 2025-03-31, holdout 2025-04-01 → 2026-09-24. Statistic: per-day mean, t on days.
PASS: a cell (2 θ × 3 windows = 6) with discovery net > 0 and t ≥ 2, and holdout net > 0.

### C — RESULT (2026-10-06): PASS on the registered rule (θ 0.10%, POST30); POST60 just short in discovery
θ 0.10% POST30: discovery net +18.6 bp (t 2.22, 747 events / 208 days), holdout net +32.1 bp (t 4.61, 1,204 / 272).
θ 0.10% POST60: disc net +20.7 (t 1.96), holdout +42.3 (t 4.79). θ 0.03%: POST gross +5–12 bp, net < 0. PRE: nothing.
82% of θ 0.10% events are NEGATIVE funding → the trade is mostly SHORT right after settlement.
Robustness added after the result (stricter only, `robust_C.py`):
- Placebo offsets (same rule, 30-min windows every half-hour from −8h to +8h): settlement window +30.5 (disc) / +44.1 (hold);
  neighbours −1: −6.6/−2.9, +1: +2.1/+10.4; others scatter ±30 with no pattern. The effect is tied to the settlement instant.
- Concentration: median trade +25 bp, win 60%, trimmed mean +32; top 3 coins (AXS, ARK, CYBER) = 44% of total gross;
  dropping the 10 best days: 38 → 30 bp. Every half-year 2023H2 → 2026H2 positive (+23 to +64 bp day-mean).
Likely mechanism: funding farmers buy just before a large negative-funding settlement and sell right after it.
Open risk: the 5m bar open at s may already be gone in practice (everyone exits at once); spreads on these coins may be wide.

## C-tick — fill realism for C on aggTrades (registered 2026-10-06, before this run)
Events: the 1,951 θ 0.10% settlements (911 coin-days); aggTrades windows [s−5m, s+65m] from data.binance.vision.
Taker entry at s+L, L ∈ {1s, 5s, 30s}: a short sells at the first sell-aggressor trade price at/after s+L (the bid), a long buys at
the first buy-aggressor price (the ask). Exit at s+H (H ∈ {30m, 60m}) on the opposite side the same way. Net = gross − 10 bp fees
(spread is inside the prices). Also reported: the signed move s→s+1s/5s/30s/60s/5m/30m (how much is gone before anyone can act).
PASS: L = 5s, H = 30m: discovery net > 0 with t ≥ 2 (day-clustered) and holdout net > 0.

## C-X — C on unseen coins (registered 2026-10-06, before this run)
Universe: the top-200 coins with 15m perp bars (`edge/k15m`, ~2025-09 → 2026-09) MINUS the 60 coins used in C.
Same rule: |previous settled funding| ≥ 0.10%; trade [s, s+30m] in direction sign(f_prev) (mostly short); entry/exit at
15m bar opens. Cost 12 bp taker (also reported at 20 bp: these are smaller coins with wider spreads).
Statistic: per-day mean, t on days. PASS: net at 12 bp > 0 with t ≥ 2.

### C-X — RESULT (2026-10-06): PASS
145 unseen coins, 15,152 events (2025-09 → 2026-09, 89% negative funding), 393 days. Day-mean net +25.2 bp at 12 bp cost
(t 6.95), +17.2 at 20 bp (t 4.74); median trade +15 bp net at 12 bp; every month gross positive (+12 to +60 bp);
top-3 coins 17% of gross.
TAIL WARNING: C-X 1% of trades ≤ −1,030 bp, 0.1% ≤ −2,718 bp, worst −10,306 bp (SIREN 2026-04-17, a short squeeze;
liquidation at any leverage); losses beyond −300 bp sum to 134% of total gross. C (60 coins): 1% ≤ −519 bp, worst −1,922.
Mean edge survives the tails, but un-stopped sizing is unacceptable: any deployment needs a hard stop, tested at tick level.

### C-tick addendum (before its run): stop variant, REPORTED ONLY (not a pass criterion)
Stop at 200 / 500 bp adverse from entry, filled at the first opposite-side trade at/after the first trade beyond the stop.

### C-tick — RESULT (2026-10-06): FAIL on the registered rule (L = 5s); edge is latency-bound
1,945 / 1,951 events with ticks (3 coin-days 404). L=5s H=30m: discovery net +10.8 bp (t 1.29) ✗, holdout +18.8 (t 2.75).
Signed move after settlement (mean/median bp): 1s 10.6/3.2, 5s 17.7/6.7, 30s 21.6/11.8, 5m 20.0/14.2, 30m 27.3/22.7.
→ ~40% of the 30-min move happens in the first second; the 5m-bar test (+31.6 on these events) overstated what a slow taker gets.
Effective spread at entry: median 2.5 bp, mean 4.4. Stop variant (L=5s): stop 200 bp → disc +11.3 (t 1.44) / hold +13.4
(t 2.58), worst −222 (vs −1,976 without a stop); stop 500 → +7.3 / +17.9.
EXPLORATORY latency curve (after the failure; `explore_C_latency.py`), H=30m, net after 10 bp fees,
event-weighted per trade | day-mean (t):
| entry | discovery | holdout |
|---|---|---|
| s+0 (first trade) | +14.0 \| +21.2 (2.5) | +24.6 \| +32.3 (4.6) |
| s+100 ms | +11.9 \| +19.7 (2.3) | +18.6 \| +28.3 (4.1) |
| s+250 ms | +11.4 \| +18.7 (2.2) | +16.4 \| +26.7 (3.8) |
| s+1 s | +10.7 \| +17.9 (2.1) | +11.8 \| +23.1 (3.3) |
| s+5 s | +2.3 \| +10.8 (1.3) | +6.0 \| +18.8 (2.8) |
| s−1 s (pays the funding, mean 22 bp) | −2.4 \| +7.5 (0.9) | −0.9 \| +12.4 (1.8) |
Entering before settlement does not pay. Server → fapi TCP connect 14 ms, so s+100–250 ms is physically reachable.
Status: real, settlement-specific effect; profitable only with sub-second post-settlement entry + hard stop.
Not yet a pass: every latency figure above was chosen after seeing the data.

## C-X-tick — fixed fast-entry rule on unseen micro-cap events (registered 2026-10-06, before any extraction or run)
Event pool: the 15,152 C-X events (145 coins never used in C/C-tick, 2025-09 → 2026-09, |f_prev| ≥ 0.10%).
Sample: 500 coin-days drawn with numpy default_rng(20261006) from the 3,876 event coin-days; all events on them (1,821).
Rule: direction d = sign(f_prev). Entry = first entry-side aggTrade at/after s+250 ms (short: first sell-aggressor trade = bid;
long: first buy-aggressor = ask). Exit = first exit-side trade at/after s+30m, unless the stop triggers first:
stop when any trade is ≥ 200 bp adverse to entry; exit at the first exit-side trade at/after that trade.
Net = gross − 10 bp fees (spread inside the prices). No funding (entered after s, out before the next settlement).
Statistic: per-day mean net, t on days; also event-weighted mean.
PASS (both): day-mean net > 0 with t ≥ 2, AND event-weighted mean net > 0.
Reported only: no-stop version, entry at s+1s and s+5s, tail quantiles, per-month.

### C-X-tick — RESULT (2026-10-06): PASS
1,821 / 1,821 events with ticks (109 coins, 280 days, 89% short). 15m-bar gross on the same events +39.0.
| version | event-mean net | day-mean net (t) | win | p1 / p5 / median | worst |
|---|---|---|---|---|---|
| RULE: s+250ms, stop 200 | **+21.2** | **+23.8 (2.93)** | 49% | −214 / −211 / −3 | −226 |
| no stop | +18.2 | +27.1 (2.73) | 54% | −969 / −390 / +12 | −10,459 |
| s+1s, stop 200 | +15.2 | +19.1 (2.35) | 47% | | −226 |
| s+5s, stop 200 | +8.2 | +12.3 (1.58) | 46% | | −226 |
Profile: 24% of trades stop out (avg −211 bp), the other 76% average +94 bp; median trade −3 bp (right-skewed payoff).
By month (event-mean net): 9 of 13 positive; worst months 2026-05 −11.9 (n 29), 2026-09 −16.7 (n 100), 2026-03 −8.6, 2025-11 −6.4.
Concurrency (all qualifying coins, 2025-09 → 2026-09): 1,262 events/month over 6,267 settlement times; coins per settlement
time median 2, p90 6, p99 11, max 41 → worst case per settlement = (positions open) × notional × ~2.3% if all stop out.
Unverified by data (needs a live/paper check): our real fill latency at s, Binance's funding-snapshot exactness, stop fills in
a squeeze faster than the tape shows, order acceptance under settlement-time load.

### Paper tracker (started 2026-10-06 15:32 UTC, unit bitana-fsettle-paper)
`research/fsettle_paper.py` simulates the C-X-tick rule on live public trades; reader `research/fsettle_paper_reader.py`.
Validated: replaying 150 recorded events reproduces the backtest exactly (600/600 variant results).
Scope (clarified before any paper event): the comparison with the backtest uses crypto USDT perps only (exchangeInfo
underlyingType COIN), as in C/C-X; Binance's 217 TradFi perps (equities, commodities, FX) are logged but reported
separately as exploratory. Benchmark: rule +21.2 bp/trade net, day-mean +23.8, 24% stop-outs.

## G — Macro releases: CPI, NFP (08:30 NY), FOMC (14:00 NY) (registered 2026-10-07, before any run)
Calendar: `research_cache/scalp/macro_calendar.json` (Fed + BLS archives; 2025 shutdown dates as published).
Data: `edge/k5m` 20-coin universe, 5m perp bars, 2020-09 → 2026-09-24. Release time t0 (DST-aware New York).
Reaction r0 = each coin's return t0 → t0+5m (first bar). Taker entry at t0+5m (bar open), exit after h ∈ {15, 30, 60} min.
Directions: CONT = sign(r0), REV = −sign(r0). Subsets: all events; tail = |r0| ≥ 2·σ5 (coin's 5m return std over the
previous 30 days, no look-ahead). Cells: 2 directions × 3 horizons × 2 subsets = 12.
Cost (spreads widen at releases): 12 bp BTC/ETH/BNB, 13 bp others.
Statistic: per event, mean net across coins → event series; mean and t on events.
Split: discovery 2020-09 → 2023-12, holdout 2024-01 → 2026-09.
Placebo: same rule at the same NY clock time (08:30 or 14:00) on weekdays without any of the three releases; report the
cell's discovery gross vs the placebo gross.
PASS: a cell with discovery net > 0 and t ≥ 2.5 (12 cells), AND holdout net > 0, AND discovery gross above the placebo gross.

### G — RESULT (2026-10-07): FAIL
193 releases (CPI 72, NFP 72, FOMC 49) × 20 coins. No cell reaches discovery t ≥ 2.5: best discovery cells CONT 30m
(all +7.6 net, t 0.68; tail +12.0, t 0.81) → holdout −12.7 / −6.9. REV loses everywhere (t −1.1 to −3.2).
Placebo (same clock time, non-release weekdays): gross −7.7 to +7.7 bp.
Post-hoc (NOT a pass): CPI alone shows continuation, CONT_15m gross +40.2 disc / +10.6 hold, CONT_60m +57.6 / +36.9
(|r0| median 70 bp); NFP and FOMC show nothing. 72 events, 12 new per year: too rare to confirm or to scalp. Dropped.

## E — Round-number levels: bounce (maker) and break (taker) (registered 2026-10-07, before any run)
Data: 1s mid grids (`scalp/grid1s`), BTC ETH SOL XRP DOGE 1000PEPE, 2026-07-24 → 2026-10-05 (74 days);
discovery = first 37 days (→ 08-29), holdout = rest.
Round levels (step): BTC $1,000; ETH $50; SOL $5; XRP $0.05; DOGE $0.005; 1000PEPE $0.0001.
Placebo levels: the same grid shifted by 0.37 and 0.63 step (non-round prices).
Touch event: the mid crosses level L, with no cross of L in the previous 4 h; approach a = +1 from below, −1 from above.
- BOUNCE (maker): limit at L against the approach (−a). Filled only if the mid trades ≥ 2 bp through L within 60 s
  (queue-conservative); fill price L. Exit at mid after h ∈ {60, 300, 900} s. Cost 7 bp + ½ spread (maker in, taker out).
- BREAK (taker): first second within 15 min after the touch when the mid is ≥ 10 bp beyond L in direction a; enter at that
  mid, exit h later. Cost 10 bp + spread.
Statistic: per-day mean net, t on days (pooled coins).
PASS: a round-level cell (2 types × 3 horizons) with discovery net > 0 and t ≥ 2, holdout net > 0, and discovery gross
above the placebo-level gross for the same cell.

### E — RESULT (2026-10-07): FAIL
866 round-level touches (6 coins, 74 days) vs 1,670 placebo-level touches. Every round cell net < 0 (t −1.7 to −11.9);
round levels are not better than non-round ones (bounce gross disc +7.1/+1.8 vs placebo +16.6/+13.7 at 300/900 s).
Bounce limits fill 91% of the time and lose −6 to −7 bp gross within 60 s (adverse selection: fills come when price runs
through); breaks lose −15 to −26 bp net.

## D — Fade forced selling on high-vol days, tick-level fills (registered 2026-10-07, before any extraction or run)
Origin (post-hoc, stated honestly): in D0's high-vol subset, fading SELL bursts gave +21.2 bp gross at 60 m (taker at
next bar open); fading buy bursts −1.3. This test checks fill realism on a fresh sample; it cannot remove the selection.
Events: D0 sell-burst events on high-vol days (`scalp/D0_events.pkl`, highvol & sgn < 0: 5,064 events, 20 coins, 434 days).
Sample: 600 events drawn with numpy default_rng(20261007). Burst bar close = c (event bar open + 5 min).
- TAKER: buy at the first buy-aggressor trade at/after c + 1 s; exit at the first sell-aggressor trade at/after entry + h,
  h ∈ {30, 60} min. Net = gross − 10 bp fees (spread inside the prices).
- MAKER: limit buy at the last trade price before c, placed at c + 1 s; filled only if a trade prints strictly below it
  within 5 min (queue-conservative), fill price = limit; exit as TAKER. Net = gross − 7 bp fees.
Statistic: per-day mean net, t on days; event-weighted mean; periods ≤ 2024 and ≥ 2025 reported separately.
PASS: a 60-min cell (TAKER or MAKER) with event-mean net > 0, day-mean net > 0 with t ≥ 2, and both periods net > 0.

### D — RESULT (2026-10-07): FAIL
600/600 events with ticks (573 coin-days). On this sample the 5m-bar fade (D0 method) still shows +16.4 bp gross at 60 m.
At tick level:

| cell | n | event-mean net | day-mean net (t) | ≤2024 | ≥2025 |
|---|---|---|---|---|---|
| TAKER 30m | 596 | −5.4 | −3.1 (−0.33) | −5.5 | −5.0 |
| TAKER 60m | 583 | +5.1 | +7.8 (0.61) | +12.3 | −12.2 |
| MAKER 30m | 548 | −4.1 | −1.7 (−0.17) | −4.4 | −3.2 |
| MAKER 60m | 536 | +7.5 | +10.7 (0.82) | +14.1 | −9.4 |

Maker fill rate 92%. No 60-min cell reaches t ≥ 2, and both 60-min cells lose in ≥2025. The bar-level edge was mostly
noise plus the pre-2025 period; with real fills and fees it does not survive. Dropped.

## C-pre — pre-settlement leg against the C direction (registered 2026-10-07, before any run)
Idea: before a large-funding settlement, funding farmers hold the side that RECEIVES funding and exit after s. Add a leg
in direction −d (d = sign(f_prev), as in C) from s − X to s + 250 ms: it receives the funding paid at s and is closed by
the same order that opens the C trade (double size), so it costs one extra taker round trip.
Known before this run (stated honestly): explore_C_latency on these same ticks showed that entering the C short just
before s instead of s+250 ms was −1.4 bp per trade after funding, i.e. a −d leg from s−ε to s+250 ms is ≈ +1.4 bp gross.
So X = 10 s is expected to fail; the open question is the drift during the last minutes before s (X = 60 s, 300 s).
Data: `scalp/C_ticks` (robust_C events, |f_prev| ≥ 0.10%, 20-coin universe, 1,948 events with ticks, windows s−5m…s+65m).
Discovery ≤ 2025-03-31, holdout ≥ 2025-04-01 (same split as C-tick).
- Entry: first trade of the −d entry side at/after s − X (d < 0: first buy-aggressor trade = ask), X ∈ {300, 60, 10} s.
- Exit: first trade of C's entry side at/after s + 250 ms (d < 0: first sell-aggressor trade = bid).
- Funding: the rate settled at s, f(s), from the funding history; the leg receives d·f(s) (negative if the sign flipped).
- Net = −d·(exit/entry − 1)·1e4 + d·f(s)·1e4 − 10 bp.
Statistic: per-day mean net, t on days.
PASS: a cell (3 values of X) with discovery day-mean net > 0 and t ≥ 2, and holdout day-mean net > 0.
Reported only: price and funding parts separately; worst event; share of events where f(s) flipped sign vs f_prev.

### C-pre — RESULT (2026-10-07): PASS by the registered rule (X = 60 s), but NOT on unseen data
1,945 events; f(s) sign flipped vs f_prev in 2.2%; funding received d·f(s) mean 22.4 bp (median 12.3).

| X | period | n | price part | funding | net event-mean | net day-mean (t) | worst |
|---|---|---|---|---|---|---|---|
| 300 s | disc | 741 | +7.7 | 16.9 | +14.5 | +4.2 (1.98) | −213 |
| 300 s | hold | 1204 | −10.6 | 25.8 | +5.3 | +2.3 (1.24) | −213 |
| 60 s | disc | 741 | +0.1 | 16.9 | +6.9 | +3.8 (2.49) | −168 |
| 60 s | hold | 1204 | −10.2 | 25.8 | +5.7 | +2.4 (1.92) | −157 |
| 10 s | disc | 741 | −1.8 | 16.9 | +5.0 | +1.7 (1.48) | −60 |
| 10 s | hold | 1204 | −10.7 | 25.8 | +5.1 | +2.2 (2.24) | −76 |

Correction to the "known before" note above: it misread explore_C_latency. Its −1.4 bp was the net of the WHOLE short
entered at s−1 s (after its funding-sign fix), against +14.5 for the short at s+250 ms; so that exploration already implied a
−d leg s−1 s → s+250 ms of about +16 bp gross / +6 net, close to the X = 10 s cell here. These ticks were therefore not
unseen for this question. Diagnostic (post-run): the signed path is flat from s−60 s to s (−1.9 bp mean) and the C move
starts at s (+5.1 at s+250 ms); the leg's profit is the funding minus that early move minus fees. Small: +2–4 bp day-mean.
Next: confirmation on unseen coins (C-pre-X below) before any engine change.

## C-pre-X — confirmation on unseen coins (registered 2026-10-07, before extraction or run)
Events: the C-X-tick sample (`scalp/CX_tick_sample.pkl`, 1,821 events, 109 coins never used in C/C-pre, the coins the live test trades);
ticks re-extracted to `scalp/CX_ticks` (they were deleted for disk). Same leg as C-pre, X = 60 s only (the passing cell):
entry = first −d entry-side trade at/after s−60 s; exit = first C-entry-side trade at/after s+250 ms; + d·f(s); −10 bp.
PASS: day-mean net > 0 with t ≥ 2, and event-mean net > 0. Reported only: X = 10 s and 300 s, price/funding parts, worst.

### C-pre — DATA BUG found after the run (2026-10-07)
Replaying the C ticks through the paper tracker showed 379 events with no −d trade in [s−60 s, s). Cause: extract_C_ticks.py
reads only the settlement day's file, so 00:00 UTC settlements (380 of 1,951 C events, 211 of 1,821 C-X events) have NO
trades before s. For them the C-pre "entry at/after s−X" fell AFTER s while the leg was still credited f(s): the
C-pre RESULT above is contaminated and is withdrawn. Rule unchanged; implementation fixed:
- extract_pre00.py merges [s−5m, s) from the previous day's file into each 00:00 event;
- the entry must be the first −d trade in [s−X, s) (else the event has no pre-leg and is excluded, reported).
C-pre is re-run on the fixed data and reported as "C-pre — RESULT (corrected)". The C, C-X, C-tick and C-X-tick results
do not use trades before s for entries or exits (they only enter at/after s), so they are unaffected.
C-pre-X (not run yet) uses the fixed extraction and the before-s entry rule; criteria unchanged.

### C-pre — RESULT (corrected, 2026-10-07): PASS by the registered rule (X = 60 s); still not on unseen data
Fixed data (00:00 events now have [s−5m, s) from the previous day; 3 previous-day files missing) and entry before s
required. Events with a pre-leg: 300 s 1,945, 60 s 1,942, 10 s 1,814 of 1,945.

| X | period | n | price part | funding | net event-mean | net day-mean (t) | worst |
|---|---|---|---|---|---|---|---|
| 300 s | disc | 741 | +8.3 | 16.9 | +15.1 | +3.8 (1.65) | −213 |
| 300 s | hold | 1204 | −10.2 | 25.8 | +5.6 | +2.8 (1.35) | −557 |
| 60 s | disc | 740 | −0.4 | 16.9 | +6.4 | +4.2 (2.61) | −168 |
| 60 s | hold | 1202 | −10.4 | 25.9 | +5.5 | +1.8 (1.35) | −461 |
| 10 s | disc | 698 | −1.9 | 17.4 | +5.5 | +1.8 (1.51) | −60 |
| 10 s | hold | 1116 | −11.9 | 27.3 | +5.4 | +2.9 (2.76) | −65 |

Nearly unchanged from the contaminated run; worst events are larger (−461 / −557: price moves before s on 00:00 events
that the bug had hidden). Small edge (+2–4 bp day-mean); the decision waits for C-pre-X.

### C-pre-X — RESULT (2026-10-07): FAIL
1,821 unseen-coin events (109 coins), 00:00 windows filled; f(s) flipped sign vs f_prev in 2.2%; funding received
d·f(s) mean 25.1 bp (median 16.0).

| X | | n | price part | funding | net event-mean | net day-mean (t) | median | worst |
|---|---|---|---|---|---|---|---|---|
| 60 s | REGISTERED | 1816 | −12.5 | 25.1 | +2.6 | +1.3 (0.63) | +0.9 | −552 |
| 10 s | reported | 1720 | −12.1 | 26.0 | +4.0 | +2.6 (1.89) | +0.9 | −463 |
| 300 s | reported | 1821 | −15.9 | 25.1 | −0.9 | −0.7 (−0.25) | −0.6 | −1696 |

On micro-caps the price falls further before s, which eats the extra funding. The day-mean t of 0.63 is far below 2.
C-pre is dropped. The paper tracker keeps logging `pre60` (no cost); it is informational only.
