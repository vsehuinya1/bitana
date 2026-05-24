"""
Replay Runner — Historical candle replay through production engines.

CLI tool for research, debugging, and threshold tuning.
Feeds closed candles chronologically through the production engine pipeline.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.loader import load_config, resolve_symbol_config
from core.logging_setup import setup_logging, get_logger
from core.models import Candle, Side
from data.binance_rest import BinanceRestClient
from data.candle_manager import CandleManager
from data.rate_limiter import RateLimiterGroup
from engines.compression_breakout import CompressionBreakoutEngine
from engines.regime_filter import RegimeFilter
from risk.risk_manager import RiskManager

logger = get_logger("replay")


async def fetch_klines(
    client: BinanceRestClient,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
) -> list[Candle]:
    """Fetch historical klines from Binance."""
    all_candles = []
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    while start_ms < end_ms:
        raw = await client.get_klines(
            symbol=symbol, interval=interval,
            start_time=start_ms, limit=1500,
        )
        if not raw:
            break

        for k in raw:
            candle = Candle(
                symbol=symbol,
                timeframe=interval,
                open_time=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                close_time=datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
                is_closed=True,
            )
            all_candles.append(candle)

        start_ms = int(raw[-1][6]) + 1  # next after last close time
        if len(raw) < 1500:
            break

    logger.info(f"Fetched {len(all_candles)} {interval} candles for {symbol}")
    return all_candles


async def run_replay(
    symbol: str,
    start: datetime,
    end: datetime,
    engine_name: str = "compression",
    output_csv: str = "replay_results.csv",
) -> None:
    """Replay historical data through engines."""
    config = load_config()
    setup_logging(level="INFO")

    rate_limiter = RateLimiterGroup()
    client = BinanceRestClient(
        testnet=False,  # use mainnet for historical data
        rate_limiter=rate_limiter,
    )
    await client.start()

    # Fetch data
    print(f"Fetching {symbol} data from {start} to {end}...")
    candles_5m = await fetch_klines(client, symbol, "5m", start, end)
    candles_15m = await fetch_klines(client, symbol, "15m", start, end)
    candles_1m = await fetch_klines(client, symbol, "1m", start, end)

    await client.close()

    if not candles_5m:
        print("No data fetched.")
        return

    # Setup engine
    resolved = resolve_symbol_config(config, symbol)
    engine = CompressionBreakoutEngine(resolved.compression)
    regime = RegimeFilter(config.regime_filters)
    risk_mgr = RiskManager(config)
    risk_mgr.update_equity(1000.0)

    # Replay
    signals = []
    total_processed = 0

    # Build 1m index by close_time for quick lookup
    m1_by_time = {}
    for c in candles_1m:
        key = c.close_time.strftime("%Y%m%d%H%M")
        m1_by_time[key] = c

    for i in range(50, len(candles_5m)):
        window_5m = candles_5m[max(0, i - 200) : i + 1]
        # Find corresponding 15m candles
        c5_time = candles_5m[i].close_time
        window_15m = [c for c in candles_15m if c.close_time <= c5_time][-50:]
        # Find recent 1m candles
        window_1m = [c for c in candles_1m if c.close_time <= c5_time][-10:]

        # Regime check
        tradeable, _ = regime.check(symbol, window_15m)
        if not tradeable:
            continue

        # Engine evaluation
        try:
            sig = await engine.evaluate(symbol, window_5m, window_15m, window_1m)
            if sig:
                signals.append({
                    "time": candles_5m[i].close_time.isoformat(),
                    "side": sig.side.value,
                    "entry": sig.entry_price,
                    "stop": sig.stop_price,
                    "risk_dist": sig.risk_distance,
                    **sig.signal_data,
                })
        except Exception as e:
            logger.error(f"Engine error at candle {i}: {e}")

        total_processed += 1

    # Output results
    print(f"\nProcessed {total_processed} candles")
    print(f"Signals generated: {len(signals)}")

    if signals:
        output_path = Path(output_csv)
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=signals[0].keys())
            writer.writeheader()
            writer.writerows(signals)
        print(f"Results saved to {output_path}")

        print("\nSignal Summary:")
        longs = sum(1 for s in signals if s["side"] == "LONG")
        shorts = sum(1 for s in signals if s["side"] == "SHORT")
        print(f"  Long: {longs}, Short: {shorts}")


def main():
    parser = argparse.ArgumentParser(description="Bitana Replay Runner")
    parser.add_argument("--symbol", required=True, help="Symbol to replay")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--engine", default="compression", help="Engine name")
    parser.add_argument("--output", default="replay_results.csv", help="Output CSV")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    asyncio.run(run_replay(
        symbol=args.symbol, start=start, end=end,
        engine_name=args.engine, output_csv=args.output,
    ))


if __name__ == "__main__":
    main()
