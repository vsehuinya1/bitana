# Before Live — Deployment Log

## 2025-07-06 — Shadow Strategies Wired & Forward-Logging

**Done — wired and live.**

Committed & pushed: `a09cbc7` on `feature/liq-burst-v66`, one file (`research/signal_shadow.py`), three new strategies.

Deployed to VPS: fast-forwarded `bitana-v5-paper` to the new commit. I discovered the VPS had an uncommitted live edit (`_FOLLOW_QUALITY = dict()` — quality floor disabled so everything fires and gets filtered in analysis), so I stashed it, pulled, and reapplied it. It auto-merged cleanly since my additions live in a different part of the file. Verified: both edits present, file parses, service restarted active, history loaded, self-test passed (28 symbols), liquidation WS connected. The only errors in the log are stale `database is locked` lines from Jul 03 — nothing since the restart.

Now forward-logging (parallel, zero live capital):

- **ny_flush_buy_4h / ny_flush_buy_8h** — LONG on NY bursts with `imb ≥ 0.5` (long-liq flush), 4h / 8h time exits
- **asia_pump_short_4h** — SHORT on Asia bursts with `imb ≤ -0.5` (short-liq squeeze), 4h time exit

First trades will appear as qualifying bursts hit the right sessions (NY 14–22 UTC, Asia 0–8 UTC) — it's ~01:30 UTC now, so Asia is the next window. Check progress anytime with:

```bash
ssh root@161.97.185.65 "sqlite3 -column -header /root/bitana/storage/signal_shadow.db \"SELECT strategy, COUNT(*) n, ROUND(SUM(pnl_atr),1) tot, ROUND(AVG(pnl_atr),3) mean FROM shadow_trades WHERE strategy IN ('ny_flush_buy_4h','ny_flush_buy_8h','asia_pump_short_4h') GROUP BY strategy\""
```

---

**Two notes for when you review results:**

1. I intentionally left the quality floor off these three (matching the raw `|imb|≥0.5` snapshots I validated on), which is consistent with the VPS's current disabled-floor setting — so their fires will match my analysis.

2. Give them a week or two before trusting the numbers; the edge is only 2.5 weeks old and adverse excursions on the NY flush-buy run ~4 ATR, so real deployment will need wide stops / small size, not the 10-ATR catastrophic stop these use for pure horizon measurement.

---

## 2025-07-14 — BTC Regime Gate (Mandatory Before Live)

**Finding**: All follow books (Asia short, London long-follow, NY-open long) have **edge only in BTC bear regime** (Jul 1–6). In neutral (Jul 7+), edge decays to ~0 ATR/trade. In bull, `burst_follow` loses –0.82 ATR/trade (18% WR).

**Requirement**: Add `btc_trend_state == 'bear'` gate to live engine for:
- `asia_pump_short_4h` / `asia_pump_short_4h_tsl`
- `ny_flush_buy_4h_open` / `ny_flush_buy_4h_open_tsl`
- `follow_3h_london` / `follow_6h_london`
- `burst_follow`

**Late fade (22–24 UTC, 6h hold)** is the **only regime-independent book** — keep in shadow until 15 session-days, no gate needed.

**Funding trigger**: Either (a) BTC flips back to bear, or (b) late fade proves out at 15+ session-days. **Wallet stays at zero until then.**

---

## 2025-07-19 — Saturday Asia Session Regime Filter (Option)

**Observation** (3 Saturdays in shadow DB, Jul 4–18):

| Date | Regime | Breadth | Cascades | BTC vs EMA | Asia Session Net | `asia_pump_short_4h` |
|------|--------|---------|----------|------------|------------------|----------------------|
| Jul 4 | **bear** | 7.2% | ~30% | −2.9% | −103 ATR | 0 trades (blocked) |
| Jul 11 | **neutral** | 3.6% | ~3% | +0.7% | +56 ATR | −3.7 ATR (5 trades) |
| Jul 18 | **neutral** | **0%** | **0%** | +1.2% | **+32 ATR** | **+12.2 ATR** (4 trades) |

**Pattern**: `asia_pump_short_4h` prints in **neutral + dead-Asia** (breadth ≈ 0, cascades = 0, BTC above EMA, low vol). In noisy-neutral (Jul 11) base 4h chops; TSL/limit15 variants survive. In bear (Jul 4) regime filter blocks it — correctly.

**Statistical reality**: 3 Saturdays = **not a rule**. Need ~15–20 session-days for a regime×session gate with any confidence (binomial: 3/3 wins in "quiet neutral" gives 95% CI lower bound ~29% win rate — useless).

**Option to add before live** (toggleable, default OFF):
```python
# In signal_shadow.py or live engine config
SATURDAY_ASIA_QUIET_NEUTRAL_ONLY = False  # set True to enable
# Quiet neutral = market_breadth_pct < 1.0 AND cascades_active == 0 AND btc_distance_from_ema_pct > 0
# Applies only to: asia_pump_short_4h, asia_pump_short_4h_tsl, asia_pump_short_4h_limit15
# When enabled: strategy fires ONLY on Saturdays (Asia session) meeting quiet-neutral criteria
```

**Recommendation**: Keep OFF for now. Collect 10+ more Saturdays in shadow. If quiet-neutral Saturdays consistently print (+EV, >1 ATR/trade, >55% WR), promote to hard gate. Until then, current bear-gate (mandatory) + TSL/limit15 variants handle the noisy-neutral days.

---

## 2025-07-19 — Saturday NY Session Regime Filter (Option)

**Observation** (3 Saturdays in shadow DB, Jul 4–18):

| Date | Regime | Breadth | Cascades | BTC vs EMA | NY Session Net | `ny_flush_buy_4h_open` | `ny_flush_buy_4h_open_tsl` | `ny_flush_buy` (all) |
|------|--------|---------|----------|------------|----------------|------------------------|----------------------------|----------------------|
| Jul 4 | **bear** | 7.1% | ~25% | −2.1% | **−44 ATR** | 0 trades | 0 trades | 0 trades |
| Jul 11 | **neutral** | 3.6% | 0% | +0.9% | **−49 ATR** | **+12.7 ATR** (5/0) | **+9.7 ATR** (5/0) | −47.5 ATR (42 trades) |
| Jul 18 | **neutral** | **0%** | **0%** | **+1.8%** | **+32 ATR** | **+7.1 ATR** (2/0) | **+4.1 ATR** (3/0) | **+32.2 ATR** (56 trades) |

**Key patterns**:

1. **Jul 4 (bear)**: NY session net −44 ATR. No `ny_flush_buy` fires (bear markets don't generate long-liq flushes on Saturday NY). *Existing mandatory bear-gate blocks correctly.*

2. **Jul 11 (noisy-neutral, breadth 3.6%)**: NY session net **−49 ATR** — worst session of the day. But `ny_flush_buy_4h_open` (open-only, 4h hold) printed **+12.7 ATR on 5 trades (100% WR)**. Base `ny_flush_buy_4h` chopped (−1.25 ATR/trade); TSL (−1.16); limit15 blew up (−6.94); 8h/24h bled. **The "open" variants captured the NY-open flush long cleanly; everything else got chopped.**

3. **Jul 18 (quiet-neutral, breadth 0%, cascades 0%, BTC +1.8% above EMA, ADX 19.3, vol z 2.36)**: NY session net **+32 ATR** (+0.58/trade). Preferred variants:
   - `ny_flush_buy_4h_open`: 2 trades, **+7.1 ATR (100% WR)**
   - `ny_flush_buy_4h_open_tsl`: 3 trades, **+4.1 ATR (100% WR)**
   Other NY strategies on Jul 18: `burst_follow` +1.8 ATR (23 trades), `setup_fade` −1.6 ATR, `setup_follow` +0.4 ATR.

**Combined preferred strategy** (`ny_flush_buy_4h_open` + `ny_flush_buy_4h_open_tsl`) across **2 neutral Saturdays**: **15 trades, +44.8 ATR, 15/0 (100% WR), +2.24 avg ATR/trade**.

**Other NY Saturday strategies** (Jul 11):
- `setup_fade`: +11.4 ATR (40 trades, 55% WR) — **only consistently positive book**
- `setup_follow`: −3.2 ATR
- `burst_follow`: −5.0 ATR
- `follow_6h_all`: +3.5 ATR (6 trades, 67% WR) — small sample

**Statistical reality**: **3 Saturdays (2 neutral) = still noise**. Binomial on 15/15 wins for preferred variants gives 95% CI lower bound ~78% WR — but regime×session interaction needs ~15–20 session-days for any confidence. The "quiet neutral" pattern (breadth ≈ 0, cascades = 0, BTC > EMA) appeared on Jul 18 for both Asia and NY — promising but unproven.

**Option to add before live** (toggleable, default OFF):
```python
# In signal_shadow.py or live engine config
SATURDAY_NY_OPEN_ONLY = False  # set True to enable
# Applies only to: ny_flush_buy_4h_open, ny_flush_buy_4h_open_tsl
# When enabled: strategy fires ONLY on Saturdays (NY session) at session open (14:00 UTC)
# Requires: btc_trend_state == 'neutral' (bear blocked by existing gate; bull untested)
# Optional quiet-neutral filter: market_breadth_pct < 1.0 AND cascade_active == 0 AND btc_distance_from_ema_pct > 0
```

**Recommendation**: 
- **Keep OFF**. 2 neutral Saturdays is meaningless for a gate.
- **But note**: `ny_flush_buy_4h_open` / `4h_open_tsl` are the *only* NY Saturday variants that survived neutral chop on both Jul 11 and Jul 18. They align with the "NY open flush long" thesis — Saturday NY open (14:00 UTC) is a distinct liquidity window (Asian close / London lunch / NY pre-market).
- **Collect 8–10 more Saturdays in shadow**. If `4h_open` variants consistently print (>1 ATR/trade, >55% WR, >3 trades/session) in neutral regime, promote to hard Saturday-NY gate.
- **Current mandatory bear-gate** (Jul 14 entry) already blocks bear Saturdays correctly. No change needed there.