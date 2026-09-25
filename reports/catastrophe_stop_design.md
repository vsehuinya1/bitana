# Exchange-resident catastrophe stop: design notes (2026-09-25)

Owner order: "the stop orders". The first pass was blocked by Claude Code's auto-mode safety check (live order code);
the owner then granted permission ("you have permission for the hard stop fix"). Status: **implemented as
`reports/catastrophe_stop.patch`, NOT applied** (see Status and Deploy below). The running bots are untouched.

## Why this matters
- Stops exist only inside the bot. `PositionManager` checks the stop on 5m candle closes and exits at market.
  `OrderManager.place_stop_order()` exists but is never called. After each entry, `main.py` marks the position
  `STOP_PLACED` without placing anything.
- If the bot or VPS goes down with positions open, nothing on Binance limits the loss. The live sizing is 8.75–10%
  of equity per leg, with up to 8 legs open.
- The dormant `place_stop_order()` could not work anyway. Since **2025-12-09**, Binance USDⓈ-M blocks
  `STOP_MARKET` / `TAKE_PROFIT_MARKET` / `STOP` / `TAKE_PROFIT` / `TRAILING_STOP_MARKET` on `POST /fapi/v1/order`,
  with error **-4120 STOP_ORDER_SWITCH_ALGO**. Conditional orders must use the **Algo service**.

## Binance Algo service (from developers.binance.com, fetched 2026-09-25)
- `POST /fapi/v1/algoOrder`: `algoType=CONDITIONAL`, `symbol`, `side`, `type=STOP_MARKET`, `triggerPrice`,
  `workingType` (MARK_PRICE | CONTRACT_PRICE), `priceProtect`, `closePosition=true` (not combinable with
  `quantity`/`reduceOnly`), `clientAlgoId` (`^[\.A-Z\:/a-z0-9_-]{1,36}$`). The response has `algoId`,
  `clientAlgoId`, `algoStatus`, `triggerPrice`, … Weight counts on the order rate limits.
- `DELETE /fapi/v1/algoOrder`: `algoId` or `clientAlgoId`. **Success still carries `"code": "200"`**. The bot's usual
  test (`"code" not in resp` means success) would misread every successful cancel as a failure.
- `GET /fapi/v1/algoOrder`: query one order by `algoId` / `clientAlgoId`. `algoStatus` values seen include NEW,
  TRIGGERING, TRIGGERED, FINISHED, CANCELED.
- `DELETE /fapi/v1/algoOpenOrders?symbol=`: cancel all open algo orders on one symbol.
- `GET /fapi/v1/openAlgoOrders`: the response format wasn't verified, so the design below doesn't depend on it.
- User-data stream event: `ALGO_UPDATE`.

## Design
1. **Level:** `entry ∓ mult × |entry − initial_stop|`, default `mult = 1.5`, with a `MARK_PRICE` trigger,
   `priceProtect=true` and `closePosition=true` (it can only flatten, never open or flip).
   - Cost measured on the paper paths with live exits, close-checked stops, 20 bps:
     | mult | London (217 legs) | NY (325 legs) |
     |---|---|---|
     | 1.25 | fires 0% · Δ 0 | fires 4.6% · Δ −0.011 R/leg |
     | **1.5** | **fires 0% · Δ 0** | **fires 2.5% · Δ −0.008 R/leg** |
     | 2.0 | fires 0% · Δ 0 | fires 1.5% · Δ −0.012 R/leg |
   - Worst case with a dead bot: about −1.5R plus slippage per leg, instead of unbounded.
2. **Entry** (`main.py`, after `add_position`): clear the symbol's algo orders (`DELETE algoOpenOrders`), then place
   the stop. A stale closePosition stop from an earlier position must never apply to the new one. Store
   `{client_algo_id, trigger, algo_id}` in `position.signal_data["catastrophe_stop"]` and persist.
   - On failure: send a Telegram critical alert and keep the position. The bot-side stop still works.
3. **Exit** (`PositionManager._finalize_close`, which all closes pass through, including reconciliation's
   external-close booking): cancel by `clientAlgoId`. If that isn't a success (for example because the stop already
   triggered), sweep the symbol. Never raise out of accounting.
4. **Sync** (at startup after `recover_positions`, then every ~2 min from the reconcile loop): for each bot-managed
   open position older than 60 s, query its stored `clientAlgoId`. If it isn't NEW or TRIGGERING, or there's no id
   (pre-patch positions), clear the symbol and re-place. Rate limit: at most once per 10 min per position.
   Externally managed positions are skipped.
5. **Config:** `execution.catastrophe_stop_mult: 0.0` (OFF by default) and `execution.catastrophe_working_type:
   MARK_PRICE`. To enable, set `catastrophe_stop_mult: 1.5` in the live yaml and restart.
6. **Paper and non-live executors:** no-op defaults in `BaseExecutor`.

## Assumptions and risks
- The account is in one-way mode (`positionSide=BOTH`) and dedicated to the bot. The per-symbol clear would also
  cancel any manual algo orders on that symbol.
- When the stop fires while the bot is alive, the position disappears on the exchange. Reconciliation then books
  it as `external_close`. A follow-up could label it `catastrophe_stop` by checking the stored id.
- Validate on testnet before live: placement parameters, the cancel success shape (`code "200"`), query statuses
  and rate limits.

## Status (2026-09-25 ~18:xxZ): IMPLEMENTED as a patch, NOT applied
- Owner permission: "And you have permission for the hard stop fix." The patch is `reports/catastrophe_stop.patch`
  (8 files, +459 lines, additive only). `git apply --check` is clean against the live tree.
- **Code:**
  - `config/loader.py`: `execution.catastrophe_stop_mult` (default **0.0 = OFF**) and `catastrophe_working_type`.
  - `data/binance_rest.py`: `place_algo_order`, `get_algo_order`, `cancel_algo_order`, `cancel_all_algo_orders`.
  - `execution/base_executor.py`: no-op defaults, so paper mode never places exchange stops.
  - `execution/live_executor.py`: pass-throughs, STOP_MARKET with `closePosition`.
  - `execution/order_manager.py`: `place_catastrophe_stop`, `cancel_catastrophe_stop`, `sync_catastrophe_stops`.
  - `execution/position_manager.py`: `_finalize_close` cancels the stop; it never breaks close accounting.
  - `main.py`: places the stop after each entry; the reconcile loop syncs about every 2 min (first sync right after
    the first reconcile, so it also covers startup).
- **Tests:** `tests/test_catastrophe_stop.py`, 17 new tests, all passing. Full suite 108 passed / 3 failed. The 3
  failures are the same pre-existing `test_execution_preflight` failures as on clean HEAD (91 passed / 3 failed).
  The new tests use a private event loop per call: `asyncio.run()` would clear the default loop and break later
  tests that call `get_event_loop()`.

## Deploy (owner; nothing trades before Wed 16:00 while BTC is neutral)
```
cd /root/bitana
git apply reports/catastrophe_stop.patch
# enable in config/live_burst_ny_asia.yaml, under execution:
#   catastrophe_stop_mult: 1.5
#   catastrophe_working_type: MARK_PRICE
systemctl restart bitana-live-burst-follow   # also activates the London age cap (f2346bf)
systemctl restart bitana-v5-paper            # WLA mirror re-binds the age cap
```
- **Verify:** about 30 s after the restart, look for "Catastrophe stop" lines in the bot log. With no open positions
  nothing is placed until the next entry. After the next entry, the Binance UI should show one STOP_MARKET
  close-position algo order on that symbol at entry ∓ 1.5 × stop distance, and it should disappear after the exit.
- **Rollback:** set `catastrophe_stop_mult: 0.0` and restart the bot (the code stays inert), or
  `git apply -R reports/catastrophe_stop.patch` and restart.
