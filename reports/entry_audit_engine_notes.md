# Entry-audit engine patch — notes (2026-09-23)

Patch: `reports/entry_audit_engine.patch` (NOT applied). Base: HEAD `b2e3441`, `git apply --check` clean.
Files: `engines/liq_burst_follow_engine.py` (+118/−47), `main.py` (+37), `execution/order_manager.py` (+5).
`research/signal_shadow.py` and all yaml untouched. Takes effect only on a `bitana-live-burst-follow` restart (owner).

## What it does

**(1) Observability (#5)**
- `LiqBurstFollowEngine.gate_stats["by_reason"][session][reason]` = count since process start.
  Counters are keyed (session, reason) and nested so the JSON stays serializable. Existing keys
  (`oi_inflow_gate`, `btc_dist_cap`, `last_block`) are unchanged.
- evaluate() reasons: `no_burst_stats`, `burst_floor`, `features_none`, `no_rule`, `regime_unknown`, `regime`,
  `regime_age_unknown`, `regime_age`, hour-gate reasons (`hour_gate` / `weekday_hour_excluded` /
  `weekday_regime_hour_excluded`, the rule's own strings), `btc_dist_cap`, `weekday`, `dedup`, `oi_inflow_gate`,
  plus `signal` (a gated match, the denominator).
- _matches() reasons: `imb`, `pos_imb_only`, `neg_imb_only`, `cascade`, `vol_z`, `n_confirms`, `decile`, `side_pin`.
- main.py (burst signals only), counted next to each existing `continue`:
  - portfolio: `max_positions`, `per_symbol`, `duplicate_side`, `cluster_cap`, `portfolio_other`. These come
    from the `can_open` reason-string prefix; `can_open` itself is untouched.
  - `spread`
  - zero size: `cluster_risk_budget` (cluster_mult == 0) or `zero_size`
  - execution soft-skips: `min_notional`, `qty_rounded_zero`, `insufficient_margin`. These use the new
    `order_mgr.last_soft_reject_reason`, which is set right next to the existing `last_soft_reject = True`
    lines. That is the only order_manager change.
- Served on :8082 `/metrics` (HealthServer → `_get_metrics_snapshot`) in two places: inside `oi_gate.stats`
  (existing path, `by_reason` now included) and as a top-level `gate_reasons` key.
- Hourly INFO line `Burst gate hourly summary` (`hour`, `total`, `counts{"session:reason": n}`). It fires on the
  first evaluate() after a UTC wall-clock hour rollover and covers the previous hour's deltas. It does not log per bar.

**(2) OI dark (#1 i+ii)**
- Gate OFF: OI is sampled at the top of every evaluate() (every bar, every symbol, before any gate). This is the
  shadow's cadence: v5_forward_test `_refresh_oi_delta` runs per symbol per bar with the same 300s cache and
  maxlen-12 deque. At the gate's slot in the chain, if delta > `oi_inflow_max_pct`: INFO `oi_inflow_dark`
  (`oi_delta_30m_pct`, `max_pct`, `would_have_matched`, `ts`), and the `oi_inflow_dark` counters go up
  (flat + `by_reason`, and `oi_inflow_dark_would_match` when the arm rule would have matched). It never blocks.
- Gate ON: blocking is unchanged. Sampling is still lazy (at gate-reach), so the blocked set is byte-identical.
  Blocked bars get a read-only `_matches(..., record=False)` probe, and the result is added to the
  `Burst entry blocked` line (`would_have_matched=`), `last_block.detail`, and the `oi_inflow_gate_would_match`
  counter. The `record=False` probe bumps no counters and changes no state.

**(3)** Removed the burst_volume/events re-check in `_matches` (old L279-286). It could never reject:
evaluate() already returns when the burst dict is below `self.cfg` floors, and main.py syncs exactly that dict
into state (same cfg object, same `>=`) before calling evaluate(). main.py is the only caller of evaluate()
(checked repo-wide; the root-level engine copy and `tmp_deploy/` are dead). Because of this, the "burst
re-check" reason requested in (1) does not exist; `burst_floor` counts the live floor in evaluate() instead.

**(4)** No decision logic changed. Dedup order, thresholds, sizing, caps, and the OI-enabled sampling path are
identical.

## Verification
- py_compile: all 3 files OK.
- Parity + cluster suites (`test_wla_gate_parity`, `test_portfolio_cluster_risk`, `test_signal_shadow_portfolio`):
  **21/21 pass**, same as HEAD.
- Full `tests/`: 80 pass / 3 fail, **identical on HEAD and patched**. The 3 failures are in
  `test_execution_preflight.py`: their MagicMock symbol-filters have no numeric `min_notional`
  (`order_manager.py:149` TypeError). This predates the patch and should be fixed separately.
- One-day differential replay (Sep-22 UTC, 288 bars × 10 active symbols, same inputs for HEAD and patch):
  real Binance 5m klines, burst stats from a /tmp copy of `force_orders_paper.db` (byte-for-byte
  `intraday_burst_stats`), live yaml via `config.loader`, regime fixed to bull (Sep-22 live value), and a
  deterministic OI stub on a simulated monotonic clock so the gate actually fires.

  | gate flag | HEAD accepts | patched accepts | identical | OI blocks HEAD / patched |
  |---|---|---|---|---|
  | on (yaml as-is) | 26 | 26 | **yes** (bar, symbol, side, entry, stop, full signal_data) | 70 / 70 |
  | off | 32 | 32 | **yes** | 0 / 0 (111 `oi_inflow_dark` logged) |

  Counter check: `signal` counts add up to the accept count (on: 12 london + 14 ny = 26; off: 14 + 18 = 32).
  The OI values are synthetic, so the blocked-set size and would-match numbers here say nothing about real
  flow. This replay only proves before/after identity.
- Hourly summary checked with an injected clock: silent within the hour, exactly one line at rollover. The
  `/metrics` payload `json.dumps` cleanly. Reason-prefix mapping checked for every `can_open` string.

## Owner notes / caveats
- **Dark-mode latency:** with the gate OFF, every symbol pays one OI REST round-trip per bar (300s cache, weight 1;
  about 10 calls per 5 min) before evaluate() continues. That adds one RTT of entry latency on signal bars,
  which today only candidates reaching the gate pay. Gate ON: no change.
- In dark mode the would-block delta uses per-bar sampling (a window of about 60 min, the shadow's metric).
  With the gate ON it uses lazy sampling (window depends on when the gate was reached). Deltas from the two
  modes are therefore not directly comparable. That is inherent to "enabled = unchanged".
- Counters and `_summary_hour` are class-level and reset on restart, the same as the existing `gate_stats`.
- The yaml flip (`oi_inflow_gate_enabled: false`) is left to the owner as ordered.
