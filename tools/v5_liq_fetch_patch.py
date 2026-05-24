"""
Patch for v5_forward_test.py — Staggered Coinalyze liq fetch.

PROBLEM: All 34 symbols fetch sequentially. One 429 on an early symbol
blocks the entire loop, starving late symbols (NEAR, ZEC, PEPE, etc.).

FIX: Batch symbols into groups of 5, fetch batches concurrently with
asyncio.gather, with inter-batch delays. Per-symbol 429 handling stays
independent so one rate-limited symbol doesn't block others.

Apply: Replace _update_liq_context() in v5_forward_test.py (lines 442-509)
with the method below, and add _fetch_single_liq() helper.
"""


async def _fetch_single_liq(self, sym: str, ca_sym: str, fr: int, now: int):
    """Fetch liq data for a single symbol with independent retry logic."""
    for attempt in range(5):
        try:
            resp = requests.get(
                "https://api.coinalyze.net/v1/liquidation-history",
                params={
                    "symbols": ca_sym,
                    "interval": "daily",
                    "from": fr,
                    "to": now,
                    "api_key": self.ca_api_key,
                },
                timeout=20,
            )
            if resp.status_code == 429:
                wait = (attempt + 1) * 15
                logger.warning("Coinalyze rate limit", symbol=sym, attempt=attempt + 1, wait_s=wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code != 200:
                logger.warning("Coinalyze error", symbol=sym, status=resp.status_code)
                await asyncio.sleep(5)
                continue
            return resp.json()
        except Exception as e:
            logger.error("Liq fetch error", symbol=sym, error=str(e), attempt=attempt + 1)
            await asyncio.sleep(5)

    logger.warning("Liq fetch failed after retries", symbol=sym)
    return None


async def _update_liq_context(self):
    """Fetch Coinalyze liq data in staggered batches to avoid 429 cascades."""
    logger.info("Fetching Coinalyze liq data (staggered)...")
    now = int(time.time())
    fr = now - 120 * 86400

    BATCH_SIZE = 5
    symbols = self.symbols
    total_done = 0
    total_failed = 0

    for batch_start in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[batch_start: batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(symbols) + BATCH_SIZE - 1) // BATCH_SIZE
        logger.info(
            f"Liq batch {batch_num}/{total_batches}: {','.join(batch)}"
        )

        # Fetch batch concurrently
        tasks = []
        for sym in batch:
            ca_sym = f"{sym}_PERP.A"
            tasks.append(self._fetch_single_liq(sym, ca_sym, fr, now))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        for sym, data in zip(batch, results):
            if isinstance(data, Exception):
                logger.error("Liq fetch exception", symbol=sym, error=str(data))
                total_failed += 1
                continue
            if data is None:
                total_failed += 1
                continue

            if not isinstance(data, list) or not data:
                total_done += 1
                continue

            history = data[0].get("history", [])
            if not history:
                total_done += 1
                continue

            daily_closes = await self._get_daily_closes(sym)

            daily_rows = []
            for h in history:
                dt_str = datetime.fromtimestamp(h["t"], tz=timezone.utc).strftime("%Y-%m-%d")
                daily_rows.append({
                    "date": dt_str,
                    "total_liq": h.get("l", 0) + h.get("s", 0),
                    "long_liq": h.get("l", 0),
                    "short_liq": h.get("s", 0),
                    "close": daily_closes.get(dt_str, 0),
                })

            self.engine.update_daily_liq(sym, daily_rows)
            total_done += 1

        # Inter-batch delay (skip after last batch)
        if batch_start + BATCH_SIZE < len(symbols):
            await asyncio.sleep(5)

    self._last_liq_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    self.db.set_state("last_liq_date", self._last_liq_date)

    n_active = sum(1 for s in self.symbols if self.engine._get_state(s).cascade_active)
    logger.info(
        f"Liq context updated: {n_active}/{len(self.symbols)} cascades active "
        f"({total_done} fetched, {total_failed} failed)"
    )
