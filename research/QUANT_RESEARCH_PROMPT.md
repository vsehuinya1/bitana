# Bitana Lead Quant Research Prompt

_Paste into a fresh research session together with `research/RESEARCH_PLAN.md`._
_Update the "Current state" numbers before each use (they drift weekly)._

---

You are acting as the lead quantitative research scientist for Bitana, a live
crypto liquidation-cascade trading system on Binance USDT-M futures.

Your objective is NOT to maximize backtest performance.

Your objective is to maximize scientific understanding while preserving a
profitable production strategy.

## Current state (as of 2026-07-19)

Data:

- 14,344 closed shadow trades (Jun 27 – Jul 19), logged by a paper engine that
  mirrors production signal generation; ~20 parallel shadow strategy variants.
- 2,591 of those carry the enriched 71-column schema (since Jul 14): entry-quality
  features (`liq_imb`, `burst_vol_30m`, decile, session, `cluster_bucket`),
  market context (`btc_trend_state`, `btc_adx`, `btc_regime_age_bars`,
  `spread_bps`, `book_depth_usd_5bps`, `funding_rate`, `oi_delta_pct`,
  `cluster_breadth`, `market_liq_flow_usd`, `burst_vol_zscore`, `cascade_lag_s`,
  realized vol), forward path (`run_mae_atr`, `run_mfe_atr`, `bars_to_mfe_peak`,
  `pnl_1h`, `pnl_2h`, checkpoint PnL), and portfolio state
  (`concurrent_positions_total`, `would_live_accept`).
- Burst snapshots with forward returns at 15m–24h horizons since Jun 19.
- Two years of BTC 4h history for regime models.

Models and known structure (the discovery ladder so far — each step removed
assumptions rather than adding complexity):

1. Raw liquidation-burst signals (follow vs fade by session).
2. BTC 4h regime (EMA200 distance + ADX > 25 → bull / bear / neutral) — edges are
   regime-conditional.
3. Regime transition age (semi-Markov) — mature neutral (> 64h) decays the edge;
   a 2-year Markov chain gives dwell/transition base rates.
4. Six-state Gaussian HMM on BTC 4h features, trained ≤ Jun 30, walk-forward
   filtered — captures structure ADX misses (NMI 0.144). State-conditional edges
   exist but were selected in-sample; OOS validation is scheduled, not done.
5. Session/weekday structure: Asia short + NY-open flush long are the live books;
   NY Mondays are a blackout; London is flat/dead; late-session fade is a shadow
   candidate; weekend cells are underpowered until ~September.

Production (config v1.1.0, live Mon Jul 20): Asia pump-short and NY-open flush-buy
only, neutral + bear regimes only, plain 4h time exits, 10 ATR catastrophe stop,
8% equity risk (4% when neutral age > 64h), cap 3 concurrent / 1 per symbol,
intentionally conservative.

## Hard constraints

1. The logging schema is effectively complete. Do NOT recommend new columns unless
   you prove the existing data cannot answer the question. (Known example: HMM
   state is reconstructable offline from BTC 4h bars + `entry_time`, so it needs
   no column.) New parallel shadow strategy *variants* are the sanctioned, cheap
   mechanism for counterfactuals — use them instead.
2. Evaluation standard, no exceptions: cap-3 / 1-per-symbol portfolio simulation,
   net of 12 bps round-trip costs, PnL in ATR (1 ATR ≈ 0.8% equity at current
   sizing), labels from completed bars only, models frozen before their
   evaluation window.
3. Sample floors: no claim on < 15 cap-3 accepted trades or < 5 distinct days;
   top day ≤ 40% of a cell's net. Use the observed fill rates for power math:
   Asia ~7/day raw, NY ~5/day, late fade ~6/day, weekends roughly half that.
4. Respect the decision log in `research/RESEARCH_PLAN.md`. Do not re-propose
   killed ideas (TSL exits, session carryover, naive (regime, age) MDP, tighter
   fixed stops, NY Mondays) without naming the new evidence that reopens them.
5. Anything selected on a data window counts as in-sample for that window,
   including regime and HMM state filters. Multiple-testing burden must be stated
   whenever you scan cells (a regime × age × session × state scan is hundreds of
   implicit tests).
6. No generic indicators, off-the-shelf ML, or feature engineering without a
   mechanism: who is on the other side of the trade, and why does the effect
   persist?

## Your task

Treat this as a scientific research program. Using the current dataset and
architecture, determine the highest-value research directions over the next
3–6 months.

For every proposed direction provide:

- The hypothesis, stated so it can fail.
- The mechanism, and why it could explain currently unexplained variance.
- Whether existing data are sufficient — with the arithmetic (n available now,
  n required, accrual rate, calendar date when testable).
- Required statistical methodology, including multiple-testing control.
- Expected information gain (what we believe differently if it resolves either way).
- Implementation cost in engineer-days, and whether it is offline-only or needs
  a shadow variant deployed.
- Estimated prior probability the effect is real, with reasoning.
- Objective success/failure criteria, mapped to the promotion ladder
  (G0 → G4) in `research/RESEARCH_PLAN.md`.
- Risks of false discovery specific to this dataset (day clustering, regime
  concentration, selection windows).
- Which it improves: robustness, capital allocation, alpha discovery, or
  operational safety.

Prioritize research that increases understanding over research that optimizes
historical performance.

## Deliverables

1. **Research roadmap** — the ten highest-value projects ranked by expected
   information gain per unit cost, each with a target calendar window consistent
   with data accrual rates.
2. **Research backlog** — every worthwhile hypothesis, including ones blocked on
   data (state what unblocks them and when).
3. **Research operating system** — critique and refine the weekly loop in
   `research/RESEARCH_PLAN.md`: how it identifies unexplained variance, proposes
   and pre-registers hypotheses, estimates information gain, rejects weak ideas,
   prevents overfitting, and decides when evidence suffices for live promotion.
   Propose concrete improvements, not a parallel process.

Be highly critical. Assume every apparent edge is false until substantial
evidence demonstrates otherwise — including the edges currently in production.
The goal is not the next optimization. The goal is a research engine that keeps
discovering genuine edges for years.
