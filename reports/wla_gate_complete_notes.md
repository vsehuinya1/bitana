# WLA gate-complete patch: review notes (2026-09-24)

Patch: `reports/wla_gate_complete.patch` (NOT applied). Base: HEAD `27ad781`, `git apply --check` clean.
Files: `research/signal_shadow.py` (+114/−28) and a new `tests/test_wla_gate_complete.py` (202 lines).
Live engine, yaml and loader are untouched. Takes effect on a **bitana-v5-paper restart only**; the live bot is
unaffected.

## What it changes
1. **Binds every live-engine gate in the WLA mirror** (`LiveGateSnapshot` + `_live_gate_block_reason`), checked in
   engine order:
   - `min_n_confirms`: **ACTIVE**. Live is 1, the strategy spec is 0. Since Aug-21, 19.5% of London and 29% of NY
     WLA=1 rows had n_confirms=0; live has 0 of 186 such legs.
   - `min_imb`, `min_cascade_strength`, `max_regime_age_bars` (rule value, else the cfg fallback; unknown age fails
     **closed**, as in the engine), and the cfg burst floors `min_burst_volume_30m` / `min_burst_events_30m`.
     These are latent: equal to the live values today.
   - The burst stats ride in `f` (set in `on_intraday_burst`, the same keys as the engine's features).
   - Unknown `n_confirms` or unknown burst stats **fail closed** (`n_confirms_unknown` / `burst_unknown`). They're
     unreachable for mirror strategies; they exist so the gap can't silently re-open.
2. **Writes `n_confirms` at insert** to `shadow_trades` (new column, added by the existing migration) and to
   `shadow_pending_entries`, so limit fills carry the signal bar's value. NULL means pre-patch/unknown; 0 is stored
   as 0.
3. **Refactor:** `_snapshot_for(arm, rule, bf)` is split out of `_load_live_gate_snapshots` so tests can bind
   modified rules. Output is identical.

## Tests (`tests/test_wla_gate_complete.py`, 11 cases)
- **Structural:** every gate attribute the engine reads (`rule.`/`self.cfg.` min_*/max_*/allowed_*/exclude_*/side/
  dist/oi/dedup/regime-gate) must be bound in `LiveGateSnapshot` or listed as handled elsewhere. A new engine gate
  fails until it is mirrored. This is the enforceable form of loader.py's "a config edit can never desync them" claim.
- **Behavioral:** the real `LiqBurstFollowEngine.evaluate()` (with `_features` stubbed) is the oracle.
  - Grid: imb × n_confirms × cascade × vol_z × decile × burst floor × regime age, 432 points per arm × 4 rule
    variants (live as-is; min_imb 0.9; n_confirms 2 + cascade 0.5; age 10 + floors 50k/5).
  - The mirror decision must equal the engine decision at every point.
  - Every mirror reason fires somewhere in the grid: burst_floor, regime_age(_unknown), imb, cascade, vol_z,
    n_confirms, pass.
- **Write-time:** burst-mirror, burst-research, bar-trigger and limit-fill rows all have non-null `n_confirms`.
  0 is stored as 0. An n_confirms=0 NY mirror row gets WLA=0 while the n_confirms=2 control in the same open cell
  gets WLA=1.
- **Results:**
  - Patched: new 11/11. Parity + portfolio + cluster + new: 32/32. Full `tests/`: 91 pass / 3 fail, where the 3 are
    the pre-existing `test_execution_preflight` MagicMock-`min_notional` failures, identical on HEAD (80 pass / 3 fail).
  - **Negative control:** the new tests against the committed mirror: 10 of 11 FAIL (only the regime-gate assumption
    passes), so the tests detect the gap.

## Deploy (owner applies, or delegates)
1. `git apply reports/wla_gate_complete.patch`, then run `pytest tests/test_wla_gate_complete.py
   tests/test_wla_gate_parity.py`.
2. Restart **bitana-v5-paper** outside bar-boundary windows (not at xx:x4:30–xx:x5:30 or xx:x9:30–xx:x0:30).
   The restart resets the in-memory dedup clocks, as any restart does.
3. Record the restart timestamp as **MIRROR CUT-OVER 3 (gate-complete)**. WLA=1 rows from that point exclude
   n_confirms=0 legs, so expect about 20–30% fewer WLA=1 mirror rows. Era-split all WLA reads there (after cut-over
   1 = parity 12:36:54Z and cut-over 2 = vol_z 13:21:26Z, both Sep-23).

## Post-deploy verification (the "non-null on every post-patch row" assertion, on a /tmp copy)
```sql
-- expect zero rows (limit strategies: allow ≤36 bars after restart for pre-patch pending fills)
SELECT strategy, trigger, COUNT(*) FROM shadow_trades
WHERE created_at >= :restart_epoch AND n_confirms IS NULL
  AND NOT (strategy LIKE '%limit15%' AND created_at < :restart_epoch + 36*300)
GROUP BY 1, 2;
-- expect zero rows: no WLA=1 live-mirror row with n_confirms = 0 after cut-over
SELECT strategy, COUNT(*) FROM shadow_trades
WHERE created_at >= :restart_epoch AND would_live_accept = 1 AND n_confirms = 0
  AND strategy IN ('burst_follow', 'ny_flush_buy_1h', 'asia_pump_short_4h')
GROUP BY 1;
```

## Read-procedure amendment (paste-ready; confirm text)
**READ-PROC-GATECOMPLETE (2026-09-24):** every live-arm read that uses `would_live_accept=1`, or bars derived from
the WLA book, must be gate-complete at read time until MIRROR CUT-OVER 3 has covered the whole read window.
- **Pre-parity mirror rows (< 2026-09-23T12:36:54Z):** apply `n_confirms ≥ 1` via the exact `burst_snapshots` join
  (symbol + bar_time). Coverage is 100% for `burst_follow` and `ny_flush_buy_1h`.
- **Parity-era, pre-cut-over-3 mirror rows:** rows with no snapshot row are the **unknown group** (about 21–27
  WLA=1 rows today). Report it as its own line (n / E / top-day). It **never passes a bar**. Bars are computed on
  the known group only. NULL ≠ 0.
- **Post-cut-over-3 rows:** use the `n_confirms` column directly. WLA is already gate-complete.
- **Sensitivity (disclose at read):** the filter moves NY materially (E +0.030 → +0.043) and London barely
  (+0.010 → +0.012). NY-VOLZ-OFF's verdict is the exposed read.
- **Bar-trigger rows** (setup_*/v65_*) are out of scope: they join `setup_snapshots` 100% with n_confirms ≥ 3, and
  no live arm mirrors them.

## Caveats (disclosed, not blocking)
- **Historical WLA=1 rows stay as stamped at write time.** The patch doesn't rewrite history, so the read-procedure
  filter stays mandatory for pre-cut-over reads.
- **`min_cascade_strength` binds the threshold, not the value.** Shadow's `cascade_strength` comes from a different
  daily-liq source than live's (corr 0.75 on same-bar twins). Harmless at today's 0.0. If the threshold is ever
  raised, cascade decisions will diverge on value, and that needs its own parity item.
- **The mirror binds the global `burst_follow` block.** The live engine uses the per-symbol resolved cfg. There are
  no per-symbol burst_follow overrides today (`symbols.defaults` is risk_pct only), so they're equal.
- **Paper floor = live floor** (20000 / 3 / dedup 3), so binding the burst floor is behavior-neutral today.
- **Stale comment rider (owner, cosmetic, no restart):** the yaml's "min_n_confirms near-vacuous / reject rate
  unmeasured" annotation is now measured and wrong. It binds 18–29% of the mirror book.
