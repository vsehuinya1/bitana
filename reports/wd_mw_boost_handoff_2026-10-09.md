# wd_engine: market-wide boost (wick bids at 3x after a market-wide selloff hour): handoff

**For the wick-catcher custodian chat.**
- Built 2026-10-09 by session bitana-d9 on the owner's order ("If you can do it cleanly with notes for the other to
  catch up - then yes").
- **Status: committed, tests pass, NOT deployed.** The running engine (started 2026-10-08 04:47Z) still runs the old
  code.

## What changed
- **Each hour (`refresh_mw`, in parallel with `refresh_atr`):**
  - Reads ~722 closed 1h klines for the 20 coins in `mw_coins`.
  - Computes `rules.market_wide()` for the hour that just closed.
- **The tag (identical to the research tag of record `edge/improve_wd.py` MKT):**
  - Per coin: the last hourly log return divided by the stdev (ddof 1) of the 720 hourly returns before it.
  - The hour is market-wide if ≥ 50% of valid coins have z ≤ −2 and ≥ 16 coins are valid.
- **In a market-wide hour** (`wick_mw_mult: 3`, `wick_mw_mult` × `r_usd`):
  - Every wick rung is sized at 3× `r_usd` (`rung_qty`); the min-notional ladder/single-bid rule is unchanged.
  - Legs record `r_usd=3` and `signal.mult=3`.
  - Telegram: "market-wide selloff hour (n/20 …): N wick bids at 3x ($3/R) this hour; add-ons stay 1x".
- **BTC-dump add-on of a boosted rung:** quantity = rung qty / 3 (floored to the market step, at least min notional),
  `r_usd=1`. It carries the crash tail, so it stays 1×.
- **Fail-safe:** any error reading the klines → the hour is not boosted. Bids are never blocked.
- **Logs:** "wick bids placed" now carries `mult`, `mw_down`, `mw_valid`. The start message names the boost.
- **Unchanged:** universe (6 coins), $1/R, ladder, stop −20R, TP, 24h exit, discount, equity floor, leverage.
- **Files:** `wd_engine/rules.py` (+`market_wide`, `MW_*` constants), `wd_engine/engine.py`, `wd_engine/config.yaml`
  (+`wick_mw_mult`, `mw_coins`), `tests/test_wd_engine_mw.py`.
- **API load:** +20 klines calls/hour, limit 723 (weight 5 each = 100/hour).

## Why
- Registered test `reports/wd_sizing_prereg.md`, Results.
- Method: a dollar-level replay of this engine with $104, the 5x cap with rejections, min notionals and 1m fill bars.
- Design A3 passed in every period:

| period | today's engine | A3 |
|---|---|---|
| 2020–21 | +$142 | +$178 |
| 2022–23 | +$122 | +$135 |
| 2024–26 | +$61 | +$84 |

- 2024–26 worst marked drawdown: −$95 vs −$84 today.
- 2026 YTD gains nothing: no market-wide hours.
- 20-coin designs failed: their crash tails exceed $104.

## Tests (`venv/bin/python -m pytest tests/test_wd_engine_mw.py tests/test_wd_engine_rules.py`: 14 passed)
- `market_wide` equals the research MKT on every market-wide hour since 2023-06, plus 300 random other hours
  (flag, n_down, n_valid).
- Offline engine run (fake market + DryBroker):
  - boosted rungs are exactly 3× the quantity, only after a market-wide hour;
  - the add-on of a boosted rung is 1×;
  - a kline failure → no boost.

## Deploy (owner-ordered change; a safe window is needed)
1. **Wait for no OPEN/ENTERING legs** (`data/wd_engine_status.json`).
   - At build time 4 were open: ETH ×2, BNB ×2, filled 2026-10-08 ~15:3x–15:5xZ.
   - Their 24h time exits fall around 15:30–16:00Z 10-09 unless the TP hits first.
2. `systemctl restart bitana-wd-engine`.
   - **The owner's call.** At build time the auto-mode classifier blocked bitana-d9 ("Production Deploy").
   - So the restart happens only on the owner's explicit order, to whichever chat he chooses.
   - No chat restarts it on another chat's request.
3. **Verify:**
   - the unit is active;
   - the log has "started (live) … market-wide boost 3x (20-coin tag) …";
   - no errors; the user stream is connected;
   - the next hour has "wick bids placed … mult=1.0 mw_down=… mw_valid=20" (`mw_valid` should be 20).
4. **First boosted hour:**
   - the Telegram note arrives;
   - Binance open algo orders show about 3× the quantities (read-only GET);
   - watch for "triggered bid rejected" (margin at 5x on $104 is expected on big crash hours; the replay counted 11
     rejections in 2024–26);
   - the add-on quantity is 1×.
5. **Rollback:** `wick_mw_mult: 1` (or null) + restart, or `git revert` of the commit.

## Not included (still proposals only)
- The add-on latency fix from audit #1 (`reports/wd_audit_2026-10-08.md`): run add-ons before the hourly bid refresh
  and drop the 6 s wait, about −0.07R per add-on today. **Not ordered; not in this commit.**
