# PREREG-EVENT-WINDOW-EXPANSION
**Registered**: 2026-08-29T14:30Z (this session)
**Owner**: Martin
**Status**: REGISTERED — forward-only from 2026-08-30T00:00Z

---

## Claim Tested
Event-window expansion (trading event flushes post-release) has positive expectancy for specific event/window combinations, contradicting the blanket GRINDVETO lesson (EV≈0). Direction is **expansion** (trade the flush), not suppression.

---

## Frozen Rule (from in-sample analysis)
**Enable LONG entries for ny_flush_buy_4h and burst_follow during these windows:**

| Event | Window | Book(s) | In-Sample E (R) | In-Sample n | In-Sample WR |
|-------|--------|---------|-----------------|-------------|--------------|
| Jackson Hole | post-6h | ny_flush_buy_4h (live-accept) | +0.399 | 6 | 83% |
| FOMC | post-6h | ny_flush_buy_4h (full-closed) | +0.176 | 17 | 71% |
| FOMC | pre-6h | ny_flush_buy_4h (full-closed) | +0.081 | 9 | 78% |
| CPI | post-6h | ny_flush_buy_4h (full-closed) | +0.125 | 4 | 50% |

**Veto (maintained)**: CPI/NFP post-6h and post6-24h — mildly negative (−0.02 to −0.04R), no trade.

**No change to**: GRINDVETO (BTC-12h-return ∈ (−2%, 0%) veto for LONG flush arms) — separate regime layer, remains frozen.

---

## Forward-Only Period
- **Start**: 2026-08-30T00:00Z (next Sunday loop)
- **Counts-only until**: 2026-09-13 (2 weeks)
- **First R-read**: 2026-09-13
- **Formal call**: 2026-09-27
- **One extension max**: 2026-10-11

---

## Data Source
- Shadow feed: `shadow_trades` table, `status='closed'`, `pnl_atr/stop_atr` canonical R
- Books: `ny_flush_buy_4h` (primary), `burst_follow` (secondary)
- Filter: `would_live_accept=1` for live-mirrored stats; full-closed for population stats
- Event anchors: `NFP`, `CPI`, `FOMC`, `JACKSON_HOLE` from `/tmp/anchors.json` (updated each Sunday loop)

---

## Metrics
Per-event, per-window, per-book:
- `n` (trade count)
- `E` = mean(pnl_atr/stop_atr)
- `WR` = % trades with pnl_atr > 0
- `ΣR` = sum of R

---

## Floors (per event/window/book cell)
- `n ≥ 15` AND `days ≥ 5` in BOTH full-closed AND live-accept scopes
- Both scopes must show `E > 0`

---

## Promotion Criteria (any cell meeting ALL)
1. **Grind-cell equivalent**: post-6h cell E ≤ −0.30R → **kill that event/window** (no re-reg)
2. **Healthy cell**: up-cell (post-6h for JH/FOMC) E ≥ +0.30R AND (up−grind) delta ≥ +0.50R/tr
3. **Survival**: live-accept n ≥ 30 AND days ≥ 5 with E > 0

→ Propose live wiring (owner gate required)

---

## Kill Criteria (any cell)
- Grind-cell E > 0 (no toxic cell to exploit)
- Up-cell starves (n < 30 in live-accept by formal call)
- Sign flip between full-closed and live-accept scopes

→ Drop, log, no re-registration of same claim

---

## Cross-Cluster Loss-Streak Interaction (Item 1 fix deployed)
Per-bucket streak tracking now live (2026-08-29T14:14Z). Event-window trades will have their own `cluster_bucket` (15-min windows). A bad event batch no longer triggers global brake. This pre-reg assumes the fix is active.

---

## Owner Decision: REGISTERED
Per owner instruction "Both" — this pre-reg is now formally registered.