"""
Master data collection orchestrator.

Collects all data types for all configured symbols.
Designed for single-command full dataset build with smart resumption.
"""
import sys
import time
import argparse
from loguru import logger

# Add research root to path
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.data.collectors.binance_ohlcv import collect_all_ohlcv, collect_ohlcv
from research.data.collectors.binance_funding import collect_all_funding, collect_funding
from research.data.collectors.binance_oi import collect_all_binance_oi
from research.data.collectors.coinalyze_oi import collect_all_oi as collect_all_coinalyze_oi
from research.data.collectors.coinalyze_liq import collect_all_liquidations
from research.data.catalog import print_catalog
from research.config.settings import (
    PRIMARY_SYMBOLS,
    CONTROL_SYMBOLS,
    SECONDARY_SYMBOLS,
    ALL_SYMBOLS,
    OHLCV_TIMEFRAMES,
)


def collect_symbol_group(
    symbols: list[str],
    group_name: str,
    skip_ohlcv: bool = False,
    skip_funding: bool = False,
    skip_oi: bool = False,
    skip_liq: bool = False,
    ohlcv_timeframes: list[str] | None = None,
):
    """Collect all data for a group of symbols."""
    logger.info(f"\n{'='*60}")
    logger.info(f"COLLECTING: {group_name}")
    logger.info(f"Symbols: {symbols}")
    logger.info(f"{'='*60}\n")

    if not skip_ohlcv:
        logger.info("── OHLCV ──")
        tfs = ohlcv_timeframes or OHLCV_TIMEFRAMES
        for symbol in symbols:
            for tf in tfs:
                try:
                    logger.info(f"  {symbol} {tf}...")
                    collect_ohlcv(symbol, tf)
                except Exception as e:
                    logger.error(f"  FAILED {symbol} {tf}: {e}")

    if not skip_funding:
        logger.info("── Funding Rates ──")
        for symbol in symbols:
            try:
                logger.info(f"  {symbol}...")
                collect_funding(symbol)
            except Exception as e:
                logger.error(f"  FAILED {symbol}: {e}")

    if not skip_oi:
        logger.info("── Open Interest (Coinalyze) ──")
        try:
            collect_all_coinalyze_oi(symbols)
        except Exception as e:
            logger.error(f"  FAILED Coinalyze OI: {e}")

        logger.info("── Open Interest (Binance 30-day) ──")
        try:
            from research.data.collectors.binance_oi import collect_binance_oi
            for symbol in symbols:
                for period in ["5m", "1h"]:
                    try:
                        collect_binance_oi(symbol, period)
                    except Exception as e:
                        logger.error(f"  FAILED Binance OI {symbol} {period}: {e}")
                    time.sleep(0.5)
        except Exception as e:
            logger.error(f"  FAILED Binance OI: {e}")

    if not skip_liq:
        logger.info("── Liquidations (Coinalyze) ──")
        try:
            collect_all_liquidations(symbols)
        except Exception as e:
            logger.error(f"  FAILED liquidations: {e}")


def main():
    parser = argparse.ArgumentParser(description="Collect research data")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="Specific symbols to collect (default: all)")
    parser.add_argument("--group", choices=["primary", "control", "secondary", "all"],
                        default="all", help="Symbol group to collect")
    parser.add_argument("--type", choices=["ohlcv", "funding", "oi", "liq", "all"],
                        default="all", help="Data type to collect")
    parser.add_argument("--timeframes", nargs="+", default=None,
                        help="OHLCV timeframes (default: 1m 5m 15m 1h)")
    parser.add_argument("--catalog", action="store_true",
                        help="Print data catalog and exit")

    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")

    if args.catalog:
        print_catalog()
        return

    # Determine symbols
    if args.symbols:
        symbols = args.symbols
    elif args.group == "primary":
        symbols = PRIMARY_SYMBOLS
    elif args.group == "control":
        symbols = CONTROL_SYMBOLS
    elif args.group == "secondary":
        symbols = SECONDARY_SYMBOLS
    else:
        symbols = ALL_SYMBOLS

    # Determine skips
    skip_ohlcv = args.type not in ("all", "ohlcv")
    skip_funding = args.type not in ("all", "funding")
    skip_oi = args.type not in ("all", "oi")
    skip_liq = args.type not in ("all", "liq")

    start = time.time()

    collect_symbol_group(
        symbols=symbols,
        group_name=args.group or "custom",
        skip_ohlcv=skip_ohlcv,
        skip_funding=skip_funding,
        skip_oi=skip_oi,
        skip_liq=skip_liq,
        ohlcv_timeframes=args.timeframes,
    )

    elapsed = time.time() - start
    logger.info(f"\n{'='*60}")
    logger.info(f"COLLECTION COMPLETE in {elapsed/60:.1f} minutes")
    logger.info(f"{'='*60}\n")

    print_catalog()


if __name__ == "__main__":
    main()
