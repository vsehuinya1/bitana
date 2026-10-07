# Scalping research plan: working hypotheses

Started 2026-10-07 at the owner's request. This is a living document, updated as results come in. It is separate from
the frozen `research/RESEARCH_PLAN.md`. Rules and full results of every test live in `reports/scalping_prereg.md`.
Cost baseline: `reports/scalping_baseline_2026-10-06.md` (VIP0 taker round trip ≈ 10–11 bp).

Method (unchanged): rules are written before each run, then tested on a discovery and a holdout period. Placebo or random
controls, day-clustered t, realistic fills and fees, no look-ahead. Failures are reported plainly, and post-hoc findings
are labelled. Paper trading comes before live, and live only on the owner's order.

Only one setup has passed so far: **C, the funding-settlement trade.** Right after a settlement whose previous funding was
|f_prev| ≥ 0.10%, trade in direction sign(f_prev): enter at s + 250 ms with a 2% stop, and exit after 30 min. Backtest on
unseen coins (C-X-tick, 1,821 events): +21.2 bp/trade net, day-mean +23.8 (t 2.93), 24% stop-outs, worst −226 bp.

---

## 1. Small edge or not enough proof (active)

### H2 — C holds up forward (paper)
- **Claim.** The C-X-tick rule earns about +20 bp/trade net on live public data.
- **Evidence.** 17 clean crypto paper events (2026-10-06 20:00 → 10-07 08:00): rule +11.1 bp event-mean, 18% stopped.
  That is too few to judge in either direction.
- **Gap.** n. At about 30 events/day, 150 events take about 5 days.
- **Next / kill (proposed, for the owner to confirm).** Checkpoint at 150 clean crypto events. Continue if the
  event-mean net is > 0 and the day-mean t is ≥ 1.5. Stop the live test if the event-mean is ≤ 0.

### H3 — C survives real execution (live, $6, one trade at a time)
- **Claim.** Real fills match paper: arrival after s (no funding charged), slippage within the 50 bp IOC cap, the stop
  placed every time, and stop slippage close to the paper's.
- **Evidence.** One trade (API3 2026-10-07 08:00): filled 449 ms after s, no funding, price = paper. The stop failed
  because of an engine bug (avgPrice 0 in the order response). It was fixed and the engine restarted at 08:07.
- **Gap.** Everything beyond n = 1, in particular the stop path, live-vs-paper slippage and IOC misses.
- **Next / kill.** Compare live and paper per event after 30 trades (about 1 week). Halt on any repeat of an
  execution failure. The test ends at 14 days or 200 trades.

### H4 — Faster entry (closer to Binance, e.g. a Tokyo VPS)
- **Claim.** Most of the move happens in the first second (backtest: L1s +15.2, L5s +8.2, s+250 ms +21.2), so earlier
  entry pays.
- **Evidence.** The latency curve was found post-hoc on the C ticks, and only s + 250 ms was confirmed on unseen coins.
  Entry at s + 0–100 ms competes with the fastest players, and the tape cannot show queue position.
- **Gap.** The paper tracker logs L0, L1s and L5s next to the rule, which gives a forward latency curve.
- **Next / kill.** Decide on a VPS only after the paper shows L0 > rule by ≥ 5 bp over ≥ 150 events and H3 is clean.

### H5 — Tails and the stop
- **Claim.** A 2% stop limits the squeeze tail without killing the edge.
- **Evidence.** Backtest: 24% stop-outs, worst −226 bp (the stop gapped). Without a stop, worst −103% on 15-min bars.
- **Gap.** Real stop-fill slippage on micro-caps right after settlement.
- **Next.** Measure on live stops (H3). No change to the stop level without a registered test.

### H6 — Picking one event per settlement (max_open 1)
- **Claim.** Taking the largest |f_prev| when several coins qualify is at least as good as the average event.
- **Evidence.** None yet. The live test always takes rank 1; the backtest pooled all events.
- **Next.** A cheap backtest on the C-X-tick events: rank-1 vs the others (register first).

### H7 — Capacity: does C scale beyond $6?
- **Claim.** The edge survives at $100–1,000 per trade.
- **Evidence.** None. The events are mostly micro-caps, and the first seconds after s are thin.
- **Next.** Backtest proxy: traded volume in [s + 250 ms, s + 5 s] vs order size, and the price walk for a given size.
  This matters before any size increase.

### H8 — CPI continuation (parked)
- Post-hoc only (from G): CONT_60m gross +57.6 discovery / +36.9 holdout. 72 events, 12 per year: too rare to confirm or
  to trade. Parked. Revisit only with a new, independent sample.

---

## 2. Untested ideas (queue)

| ID | Idea | Data | Cost | Note |
|---|---|---|---|---|
| C-TradFi | Rule C on Binance's 217 equity/commodity/FX perps | archive download, recent listings | ~3–4 h | Paper already logs them separately (exploratory) |
| C-θ | Funding threshold 0.05% instead of 0.10% | bars + ticks | ~3 h | Needs a fresh holdout (θ was chosen on this data) |

---

## 3. Closed (failed): do not retest without new data or a new idea

| Test | Idea | Result |
|---|---|---|
| Step 1 | Anything under ~1 min, market making at VIP0 | Cost wall: fees + adverse selection exceed the move |
| A | BTC → alt lead-lag | Alts move with BTC within 10 s |
| B / B2F | Intraday momentum, US-open fade | FAIL in discovery, and B2F on unseen coins |
| D0 | 5-min taker-flow follow | FAIL; the fade-after-sell-burst lead was killed by D |
| D | Fade sell bursts on high-vol days, tick fills | Best cell t 0.82, negative in ≥ 2025 |
| E | Round-number bounce/break | Every cell net < 0; round levels no better than random |
| G | Macro releases (CPI/NFP/FOMC) | No cell reaches t ≥ 2.5; CPI lead parked (H8) |
| C-tick (L = 5 s) | C with a 5 s entry delay | Discovery t 1.29: too slow |
| C-pre / C-pre-X | Hold the funding-receiving side from s − 60 s into settlement | Passed on seen coins (t 2.61), failed on unseen coins: +1.3 bp day-mean, t 0.63. Paper `pre60` logged for information only |
