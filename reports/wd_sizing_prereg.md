# Wick catcher: market-wide sizing on the engine's own rules: pre-registration

**Status.** Registered 2026-10-09, before any result was computed.
- Owner order: "yes" to "one registered re-test on the engine's own rules (v2 ladder, $104, 5x cap with rejected orders,
  minimum order sizes, worst-moment checks): today's setup vs market-wide tiered sizing on the 6 coins and on the
  20 coins, 3 size multipliers, across 2020–21 / 2022–23 / 2024–26."
- Read-only research. No engine file is touched.

**Background.** In `reports/structural_edge_2026-09-25.md`, market-wide fills earn about 6× more per fill than
coin-specific ones, and sizing up only on market-wide fills gave the best return per unit of drawdown. That study
modelled no leverage cap, no minimum order sizes and not the v2 ladder; this test closes that gap.

## Simulator: `research/wdsize/wdsize.py`
A dollar-level, chronological replay of `wd_engine` v2 as configured (`wd_engine/config.yaml`, `wd_engine/rules.py`).

**Wick bids:**
- Hourly ladder at 5 / 6.5 / 8 × ATR1h under the hour's close, 1/3 R each.
- If a rung's quantity × bid is under Binance's minimum order value, the coin takes a single 5-ATR bid at full R
  (`rules.rung_qty`). Minimum values and lot steps come from the 2026-10-06 exchangeInfo snapshot, applied to
  historical prices.
- One open leg per coin per rung. No bid on a coin within 24h of its stop-out.

**Fills and exits:**
- A rung fills when a 5m low trades ≤ bid − 0.1 ATR (strict, as the research readers). Fill = min(open, bid).
- Take-profit: a trade-through of the pre-wick close. A TP inside the fill bar counts only if a 1m bar after the fill
  minute trades through. 1m data comes from the readers' cache, else the data.binance.vision daily archive, never the API.
- Otherwise the leg exits after 24h at the open.
- Disaster stop: −20R from the entry, checked before the TP in a shared bar.

**BTC-dump add-on:**
- When BTC's fill-bar close is ≤ −1.7% vs the bid-hour close and the leg is still open, buy a second unit at the
  fill-bar close.
- It has the same exits as its rung.

**Perp discount** (unchanged in every design):
- Basis (perp/spot − 1, 5m closes) crosses below −30 bps while BTC's 5m close is ≤ −1% vs 60 min earlier.
- Buy at the next 5m open, at 1R = 3 × ATR1h; exit after 4h.
- One leg per coin. It runs on the 6 core coins only.

**Costs:** the engine's paper models. Wick round trip 0.12%; add-on and discount 0.20%. Funding is ignored (holds
≤ 24h).

**Account rules:**
- Start each period with a $104 wallet and nothing open. $ per R is fixed (no compounding).
- **Margin, at every fill (5x cap):** the new notional / 5 must be ≤ equity − Σ open notional / 5. Equity = wallet +
  unrealized P&L at the fill bar's closes. Otherwise the order is **rejected** (counted).
- Fills inside the same 5m bar are processed shallow rung first, then by coin.
- **Equity floor:** no new entries while the realized wallet is < $60.
- **Worst moment:** equity marked at every 5m bar with every open leg at its coin's low in that bar.
- **Liquidation:** marked equity ≤ 1% of open notional (a conservative maintenance margin).

## Market-wide hour tag
Known when the hourly bids are placed. Same definition as `edge/improve_wd.py`:
- Hourly log return of each of the 20 coins, z-scored by its trailing 720h stdev (≥ 200 hours), shifted 1h.
- The hour is market-wide if ≥ 50% of valid coins have z ≤ −2 and ≥ 16 coins are valid.
- The tag is computed on the 20 coins in every design. A live engine would fetch their hourly klines, all public data.

## Designs
$1 per R base. m = the size multiplier for bids placed right after a market-wide hour. **The add-on stays at the 1×
quantity in every design** (the 2025-10-10 tail lesson).

| design | universe | wick bids, normal hours | wick bids, after a market-wide hour |
|---|---|---|---|
| **A0** today's engine | 6 core (BTC ETH SOL XRP DOGE BNB) | 1× | 1× |
| A2 / A3 / A5 | 6 core | 1× | 2× / 3× / 5× |
| B1 / B2 / B3 / B5 | 6 core + 14 more of the tested 20 | core 1×, the 14 others none | all 20 at 1× / 2× / 3× / 5× |

## Periods
- 2020-10 → 2021-12, 2022 → 2023, 2024 → 2026-09-24.
- Each starts fresh from $104.
- A continuous run from 2020-10 is also reported.

## Pass bar (a design versus A0, in EVERY period)
1. **Total $ > A0's.**
2. **Total $ / |worst marked drawdown $| ≥ A0's.** The worst marked drawdown is the deepest drop of marked equity from
   its running peak.
3. **Never liquidated, and marked equity never below $40.**

Among the designs that pass, the owner picks one. If none pass, today's engine stays.

**Reported, not gated:**
- Rejected orders by type; paused hours.
- Boosted fills and their $.
- Worst day; max gross exposure (× equity).
- $ by year; the continuous run.
- How each design behaves on the six crash days in `edge/crash_mtm.py`.

## Results (2026-10-09): A2, A3, A5 PASS; every 20-coin design (B1–B5) FAILS
- Run: `research/wdsize/wdsize.py` + `wdsize_report.py`; output `research_cache/wdsize/run.log`, `results.txt`,
  `runs.pkl`.
- 1m fill-bar checks: 1,439, all from the cache copy (0 archive, 0 absent).
- **Today's engine (A0) differs from the earlier R estimates.**
  - At $1/R, BTC/ETH/BNB rungs fall under Binance's minimum order, so those coins mostly run single 5-ATR bids.
  - Crash-time fills get margin-rejected at $104 × 5x.
  - 2024–26: +$61, against +$90R in the uncapped R estimate.

| period (from $104) | A0 today | A2 | **A3** | A5 | B1 | B2 | B3 | B5 |
|---|---|---|---|---|---|---|---|---|
| 2020–21 $ | +142 | +157 | **+178** | +219 | +466 | +502 | +569 | +724 |
| 2022–23 $ | +122 | +128 | **+135** | +143 | +130 | +137 | +151 | +115 |
| 2024–26 $ | +61 | +77 | **+84** | +83 | +101 | +138 | +132 | +162 |
| 2024–26 worst marked DD $ | −84 | −91 | **−95** | −100 | −170 | −151 | −174 | −196 |
| 2024–26 min marked equity $ | 61 | 61 | **54** | 51 | 24 | 41 | 32 | 38 |
| 2024–26 rejected orders (wick / add-on / discount) | 5 / 0 / 4 | 10 / 1 / 5 | **11 / 0 / 7** | 17 / 1 / 12 | 50 / 1 / 10 | 107 / 3 / 13 | 149 / 3 / 13 | 187 / 1 / 13 |
| verdict | — | PASS | **PASS** | PASS | FAIL | FAIL | FAIL | FAIL |

**Why the 20-coin designs fail:**
- B1: 2024–26 ratio 0.59 < 0.73 and minimum equity $24.
- B2, B3: 2022–23 ratio 2.37 / 1.97 < 3.11.
- B3: minimum equity $32.
- B5: 2022–23 total below A0's, and minimum equity $27.

**By year, A0 → A3:**

| year | A0 | A3 |
|---|---|---|
| 2021 | +128 | +163 |
| 2022 | +7 | +14 |
| 2023 | +115 | +121 |
| 2024 | +46 | +50 |
| 2025 | +9 | +29 |
| 2026 YTD | +6 | +6 |

2026 gains nothing: market-wide hours have stopped.

**The six crash days, continuous run** (worst marked moment / day-end change):

| design | 2025-10-10 | 2024-01-03 |
|---|---|---|
| A0 | −76 / +24 | −53 / +8 |
| A3 | −94 / +47 | −75 / +19 |
| A5 | −122 / +56 | −101 / +24 |
| B2 | −316 / +123 | |

**Read:**
- A3 gives most of the gain; A5 adds return only in the busy 2020–21, with a deeper tail.
- **Owner's pick pending (A2 / A3 / A5).**
- **Recommendation: A3** = bids at 3× in the hour after a market-wide selloff hour, add-on unchanged. It needs an
  owner-ordered engine change.
