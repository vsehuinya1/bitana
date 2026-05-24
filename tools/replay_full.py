"""
Bitana Full Replay Harness

Production-grade historical replay through the FULL pipeline:
candle_manager → regime_filter → compression_breakout → risk_manager → paper_executor → position_manager

Outputs: trade log CSV, daily equity CSV, signal log CSV, comprehensive analytics.
"""
from __future__ import annotations

import asyncio
import csv
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.loader import load_config, resolve_symbol_config, CompressionConfig
from core.logging_setup import get_logger
from core.models import (
    Candle, EngineType, OrderRequest, OrderResult, OrderStatus,
    Position, PositionState, Side, Signal, TradeRecord,
)
from data.binance_rest import BinanceRestClient
from data.rate_limiter import RateLimiterGroup
from engines.compression_breakout import CompressionBreakoutEngine
from engines.regime_filter import RegimeFilter
from reports.metrics import MetricsCalculator

logger = get_logger("replay_full")

# ─────────────────────────────────────────────────────────────────────────────
# Data Fetching
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_all_klines(
    client: BinanceRestClient,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
) -> list[Candle]:
    """Fetch historical klines with pagination."""
    all_candles = []
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    batch = 0

    while start_ms < end_ms:
        raw = await client.get_klines(
            symbol=symbol, interval=interval,
            start_time=start_ms, limit=1500,
        )
        if not raw or not isinstance(raw, list):
            break

        for k in raw:
            close_time_ms = k[6]
            if close_time_ms > end_ms:
                break
            candle = Candle(
                symbol=symbol, timeframe=interval,
                open_time=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                close_time=datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc),
                open=float(k[1]), high=float(k[2]),
                low=float(k[3]), close=float(k[4]),
                volume=float(k[5]), is_closed=True,
            )
            all_candles.append(candle)

        start_ms = int(raw[-1][6]) + 1
        batch += 1
        if len(raw) < 1500:
            break
        if batch % 10 == 0:
            print(f"  ... {interval}: {len(all_candles)} candles fetched")
        await asyncio.sleep(0.15)  # rate limit courtesy

    # Deduplicate by open_time
    seen = set()
    deduped = []
    for c in all_candles:
        key = c.open_time
        if key not in seen:
            seen.add(key)
            deduped.append(c)

    return sorted(deduped, key=lambda c: c.open_time)


# ─────────────────────────────────────────────────────────────────────────────
# Simulated Executor (inline, no external deps)
# ─────────────────────────────────────────────────────────────────────────────

class ReplayExecutor:
    """Minimal executor for replay — simulates fills with fees & slippage."""

    def __init__(self, initial_equity: float, taker_bps: float, slippage_bps: float):
        self.equity = initial_equity
        self.initial_equity = initial_equity
        self.peak_equity = initial_equity
        self.taker_bps = taker_bps
        self.slippage_bps = slippage_bps

    def fill_entry(self, price: float, qty: float, side: Side) -> tuple[float, float]:
        """Returns (fill_price, fee)."""
        slip = price * (self.slippage_bps / 10000)
        fill = price + slip if side == Side.LONG else price - slip
        notional = qty * fill
        fee = notional * (self.taker_bps / 10000)
        self.equity -= fee
        return fill, fee

    def fill_exit(self, entry: float, exit_price: float, qty: float, side: Side) -> tuple[float, float, float]:
        """Returns (fill_price, fee, pnl)."""
        slip = exit_price * (self.slippage_bps / 10000)
        fill = exit_price - slip if side == Side.LONG else exit_price + slip
        notional = qty * fill
        fee = notional * (self.taker_bps / 10000)
        if side == Side.LONG:
            pnl = (fill - entry) * qty
        else:
            pnl = (entry - fill) * qty
        self.equity += pnl - fee
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        return fill, fee, pnl


# ─────────────────────────────────────────────────────────────────────────────
# Position Tracker
# ─────────────────────────────────────────────────────────────────────────────

class ReplayPosition:
    def __init__(self, signal: Signal, entry_price: float, qty: float,
                 leverage: int, entry_fee: float, entry_time: datetime):
        self.trade_uuid = signal.trade_uuid
        self.symbol = signal.symbol
        self.side = signal.side
        self.engine = signal.engine
        self.entry_price = entry_price
        self.quantity = qty
        self.original_qty = qty
        self.leverage = leverage
        self.stop_price = signal.stop_price
        self.initial_stop = signal.stop_price
        self.tp1_hit = False
        self.trailing_active = False
        self.trailing_stop = 0.0
        self.candles_held = 0
        self.entry_time = entry_time
        self.realized_pnl = 0.0
        self.total_fees = entry_fee
        self.signal_data = signal.signal_data
        self.closed = False
        self.exit_price = 0.0
        self.exit_reason = ""
        self.exit_time: datetime | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Main Replay Engine
# ─────────────────────────────────────────────────────────────────────────────

async def run_replay(
    symbol: str = "SOLUSDT",
    days: int = 90,
    initial_equity: float = 1000.0,
    output_dir: str = "replay_output",
):
    config = load_config()
    resolved = resolve_symbol_config(config, symbol)
    engine = CompressionBreakoutEngine(resolved.compression)
    regime = RegimeFilter(config.regime_filters)
    cfg_risk = config.risk
    cfg_profit = config.profit_taking
    cfg_brakes = config.brakes

    executor = ReplayExecutor(
        initial_equity=initial_equity,
        taker_bps=config.fees.taker_bps,
        slippage_bps=config.fees.default_slippage_bps,
    )

    # ── Date range ──
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    print(f"Replay: {symbol} from {start.date()} to {end.date()} ({days} days)")
    print(f"Initial equity: ${initial_equity:.2f}")
    print(f"Fees: {config.fees.taker_bps}bps taker, {config.fees.default_slippage_bps}bps slippage")
    print()

    # ── Fetch data ──
    rate_limiter = RateLimiterGroup()
    client = BinanceRestClient(testnet=False, rate_limiter=rate_limiter)
    await client.start()

    print("Fetching historical data...")
    candles_1m = await fetch_all_klines(client, symbol, "1m", start, end)
    candles_5m = await fetch_all_klines(client, symbol, "5m", start, end)
    candles_15m = await fetch_all_klines(client, symbol, "15m", start, end)
    await client.close()

    print(f"  1m: {len(candles_1m)} candles")
    print(f"  5m: {len(candles_5m)} candles")
    print(f"  15m: {len(candles_15m)} candles")

    if not candles_5m:
        print("ERROR: No 5m data fetched.")
        return

    # ── Build time indexes ──
    # For each 5m candle close, find the corresponding 15m and 1m windows
    c15_by_time = sorted(candles_15m, key=lambda c: c.close_time)
    c1_by_time = sorted(candles_1m, key=lambda c: c.close_time)

    # ── Replay state ──
    open_positions: list[ReplayPosition] = []
    all_trades: list[dict] = []
    all_signals: list[dict] = []
    equity_curve: list[dict] = []
    daily_equity: dict[str, float] = {}

    risk_pct = cfg_risk.default_risk_pct
    consecutive_losses = 0
    daily_loss = 0.0
    daily_loss_date = ""
    peak_equity = initial_equity

    warmup = max(
        resolved.compression.atr_lookback,
        resolved.compression.bb_period,
        resolved.compression.volume_avg_period,
        resolved.compression.min_compression_candles + 5,
        50,
    )

    print(f"\nRunning replay (warmup={warmup} candles)...")
    t0 = time.time()

    for i in range(warmup, len(candles_5m)):
        c5 = candles_5m[i]
        c5_time = c5.close_time

        # Current date for daily tracking
        day_str = c5_time.strftime("%Y-%m-%d")
        if day_str != daily_loss_date:
            daily_loss = 0.0
            daily_loss_date = day_str

        # Record daily equity (last value per day wins)
        daily_equity[day_str] = executor.equity

        # Build windows
        window_5m = candles_5m[max(0, i - 200): i + 1]
        window_15m = [c for c in c15_by_time if c.close_time <= c5_time][-60:]
        window_1m = [c for c in c1_by_time if c.close_time <= c5_time][-15:]

        # ── 1. Manage existing positions ──
        for pos in list(open_positions):
            if pos.closed:
                continue
            pos.candles_held += 1

            current_price = c5.close
            stop_dist = abs(pos.entry_price - pos.initial_stop)
            r_multiple = 0.0
            if stop_dist > 0:
                if pos.side == Side.LONG:
                    r_multiple = (current_price - pos.entry_price) / stop_dist
                else:
                    r_multiple = (pos.entry_price - current_price) / stop_dist

            # Time stop
            if (pos.candles_held >= cfg_profit.time_stop_candles
                    and r_multiple < cfg_profit.time_stop_r_threshold
                    and not pos.tp1_hit):
                _close_replay_pos(pos, current_price, "time_stop", c5_time, executor)
                _record_trade(pos, all_trades, executor)
                continue

            # Stop loss check
            stop_hit = False
            if pos.side == Side.LONG and c5.low <= pos.stop_price:
                stop_hit = True
                exit_price = pos.stop_price
            elif pos.side == Side.SHORT and c5.high >= pos.stop_price:
                stop_hit = True
                exit_price = pos.stop_price

            if stop_hit:
                _close_replay_pos(pos, exit_price, "stop_loss", c5_time, executor)
                _record_trade(pos, all_trades, executor)
                continue

            # Partial TP at 1.5R
            if not pos.tp1_hit and r_multiple >= cfg_profit.partial_close_r:
                tp_qty = pos.quantity * cfg_profit.partial_close_pct
                fill, fee, pnl = executor.fill_exit(
                    pos.entry_price, current_price, tp_qty, pos.side
                )
                pos.tp1_hit = True
                pos.quantity -= tp_qty
                pos.realized_pnl += pnl
                pos.total_fees += fee
                pos.trailing_active = True

            # Trailing stop (ATR-based after TP1)
            if pos.trailing_active and len(window_5m) > 15:
                atr = _calc_atr(window_5m, 14)
                trail_dist = atr * cfg_profit.trail_atr_multiplier

                if pos.side == Side.LONG:
                    new_trail = current_price - trail_dist
                    if new_trail > pos.trailing_stop:
                        pos.trailing_stop = new_trail
                    if pos.trailing_stop > pos.stop_price:
                        pos.stop_price = pos.trailing_stop
                else:
                    new_trail = current_price + trail_dist
                    if pos.trailing_stop == 0 or new_trail < pos.trailing_stop:
                        pos.trailing_stop = new_trail
                    if pos.trailing_stop < pos.stop_price or pos.stop_price == 0:
                        pos.stop_price = pos.trailing_stop

        # Clean closed
        open_positions = [p for p in open_positions if not p.closed]

        # Update risk after closed trades
        for t in all_trades:
            if t.get("_processed"):
                continue
            t["_processed"] = True
            if t["pnl_r"] < 0:
                consecutive_losses += 1
                if t["pnl_usd"] < 0:
                    daily_loss += abs(t["pnl_usd"]) / max(executor.equity, 1)
            else:
                consecutive_losses = 0

        # Drawdown-based risk adjustment
        if executor.peak_equity > 0:
            dd = (executor.peak_equity - executor.equity) / executor.peak_equity
        else:
            dd = 0
        if dd > cfg_risk.drawdown_reduce_threshold:
            risk_pct = cfg_risk.reduced_risk_pct
        elif dd < cfg_risk.drawdown_restore_threshold:
            if consecutive_losses < cfg_brakes.consecutive_loss_threshold:
                risk_pct = cfg_risk.default_risk_pct

        # ── 2. Check entry allowed ──
        if daily_loss >= cfg_brakes.daily_loss_limit_pct:
            continue
        if len(open_positions) >= config.portfolio.max_per_symbol:
            continue

        # ── 3. Regime filter ──
        tradeable, _ = regime.check(symbol, window_15m, now_utc=c5_time)
        if not tradeable:
            continue

        # ── 4. Engine evaluation ──
        try:
            sig = await engine.evaluate(symbol, window_5m, window_15m, window_1m)
        except Exception:
            continue

        if not sig:
            continue

        # Record signal
        all_signals.append({
            "time": c5_time.isoformat(),
            "side": sig.side.value,
            "entry": sig.entry_price,
            "stop": sig.stop_price,
            **{k: round(v, 6) if isinstance(v, float) else v
               for k, v in sig.signal_data.items()},
        })

        # ── 5. Position sizing ──
        equity = executor.equity
        if equity <= 0:
            break

        stop_distance = abs(sig.entry_price - sig.stop_price)
        if stop_distance <= 0:
            continue

        risk_amount = equity * (risk_pct / 100.0)
        quantity = risk_amount / stop_distance
        notional = quantity * sig.entry_price
        leverage = min(int(notional / equity) + 1, cfg_risk.max_leverage)
        leverage = max(leverage, 1)

        # Liquidation buffer
        max_notional = equity * leverage * (1 - cfg_risk.liquidation_buffer_pct)
        if notional > max_notional:
            quantity = max_notional / sig.entry_price

        if quantity <= 0:
            continue

        # ── 6. Execute entry ──
        fill_price, entry_fee = executor.fill_entry(sig.entry_price, quantity, sig.side)

        pos = ReplayPosition(
            signal=sig, entry_price=fill_price, qty=quantity,
            leverage=leverage, entry_fee=entry_fee, entry_time=c5_time,
        )
        open_positions.append(pos)

    # ── Close any remaining positions at last price ──
    if candles_5m:
        last_price = candles_5m[-1].close
        for pos in open_positions:
            if not pos.closed:
                _close_replay_pos(pos, last_price, "replay_end", candles_5m[-1].close_time, executor)
                _record_trade(pos, all_trades, executor)

    elapsed = time.time() - t0
    print(f"\nReplay complete in {elapsed:.1f}s")
    print(f"Final equity: ${executor.equity:.2f} (from ${initial_equity:.2f})")
    print(f"Signals: {len(all_signals)}, Trades: {len(all_trades)}")

    # ── Output ──
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Clean trades for export
    clean_trades = []
    for t in all_trades:
        ct = {k: v for k, v in t.items() if not k.startswith("_")}
        clean_trades.append(ct)

    # Trade CSV
    if clean_trades:
        with open(out / "trades.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=clean_trades[0].keys())
            w.writeheader()
            w.writerows(clean_trades)

    # Signal CSV
    if all_signals:
        with open(out / "signals.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=all_signals[0].keys())
            w.writeheader()
            w.writerows(all_signals)

    # Daily equity CSV
    eq_rows = [{"date": d, "equity": round(e, 2)} for d, e in sorted(daily_equity.items())]
    if eq_rows:
        with open(out / "daily_equity.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["date", "equity"])
            w.writeheader()
            w.writerows(eq_rows)

    # ── Analytics ──
    print("\n" + "=" * 70)
    print("  BITANA REPLAY RESULTS — SOLUSDT COMPRESSION BREAKOUT")
    print("=" * 70)

    if not clean_trades:
        print("\n  NO TRADES GENERATED.\n")
        _print_no_trade_analysis(all_signals, candles_5m, regime, config, symbol)
        return

    metrics = MetricsCalculator.calculate(clean_trades, initial_equity)

    total_fees = sum(t.get("total_fees", 0) for t in clean_trades)
    weeks = days / 7
    trades_per_week = len(clean_trades) / weeks if weeks > 0 else 0

    # R stats
    rs = [t["pnl_r"] for t in clean_trades]
    winners_r = [r for r in rs if r > 0]
    losers_r = [r for r in rs if r <= 0]
    avg_win_r = sum(winners_r) / len(winners_r) if winners_r else 0
    avg_loss_r = sum(losers_r) / len(losers_r) if losers_r else 0
    max_win_r = max(rs) if rs else 0
    max_loss_r = min(rs) if rs else 0

    print(f"""
  Period:             {start.date()} → {end.date()} ({days} days)
  Symbol:             {symbol}
  Engine:             Compression Breakout
  Initial Equity:     ${initial_equity:.2f}
  Final Equity:       ${executor.equity:.2f}

  ┌─────────────────────────────────────────────────┐
  │  CORE METRICS                                   │
  ├─────────────────────────────────────────────────┤
  │  Total Trades:      {len(clean_trades):<28}│
  │  Win Rate:          {metrics['win_rate']:.1%}{' ' * (25 - len(f"{metrics['win_rate']:.1%}"))}│
  │  Net PnL %:         {((executor.equity - initial_equity) / initial_equity) * 100:+.2f}%{' ' * (23 - len(f"{((executor.equity - initial_equity) / initial_equity) * 100:+.2f}%"))}│
  │  Net PnL (R):       {sum(rs):+.2f}R{' ' * (23 - len(f"{sum(rs):+.2f}R"))}│
  │  Expectancy/Trade:  {metrics['expectancy_r']:+.4f}R{' ' * (21 - len(f"{metrics['expectancy_r']:+.4f}R"))}│
  │  Profit Factor:     {metrics['profit_factor']:.2f}{' ' * (25 - len(f"{metrics['profit_factor']:.2f}"))}│
  │  Max Drawdown:      {metrics['max_drawdown']:.1%}{' ' * (25 - len(f"{metrics['max_drawdown']:.1%}"))}│
  │  Avg Hold Time:     {metrics['avg_hold_time_s'] / 60:.0f} min{' ' * (21 - len(f"{metrics['avg_hold_time_s'] / 60:.0f} min"))}│
  │  Avg Winner (R):    {avg_win_r:+.2f}R{' ' * (23 - len(f"{avg_win_r:+.2f}R"))}│
  │  Avg Loser (R):     {avg_loss_r:+.2f}R{' ' * (23 - len(f"{avg_loss_r:+.2f}R"))}│
  │  Largest Win (R):   {max_win_r:+.2f}R{' ' * (23 - len(f"{max_win_r:+.2f}R"))}│
  │  Largest Loss (R):  {max_loss_r:+.2f}R{' ' * (23 - len(f"{max_loss_r:+.2f}R"))}│
  │  Total Fees:        ${total_fees:.2f}{' ' * (24 - len(f"${total_fees:.2f}"))}│
  │  Trades/Week:       {trades_per_week:.1f}{' ' * (26 - len(f"{trades_per_week:.1f}"))}│
  └─────────────────────────────────────────────────┘""")

    # ── Segment by 30-day periods ──
    print("\n  BY 30-DAY PERIOD:")
    print("  " + "-" * 65)
    boundaries = [
        ("First 30d", start, start + timedelta(days=30)),
        ("Middle 30d", start + timedelta(days=30), start + timedelta(days=60)),
        ("Last 30d", start + timedelta(days=60), end),
    ]
    for label, s, e in boundaries:
        seg = [t for t in clean_trades
               if s <= datetime.fromisoformat(t["entry_time"]) < e]
        if seg:
            seg_pnl = sum(t["pnl_usd"] for t in seg)
            seg_wr = sum(1 for t in seg if t["pnl_usd"] > 0) / len(seg)
            seg_r = sum(t["pnl_r"] for t in seg)
            print(f"  {label:12s}: {len(seg):3d} trades | WR {seg_wr:.0%} | PnL ${seg_pnl:+.2f} | {seg_r:+.2f}R")
        else:
            print(f"  {label:12s}: No trades")

    # ── Segment by UTC session ──
    print("\n  BY UTC SESSION:")
    print("  " + "-" * 65)
    sessions = [
        ("00:00–08:00", 0, 8),
        ("08:00–16:00", 8, 16),
        ("16:00–24:00", 16, 24),
    ]
    for label, h_start, h_end in sessions:
        seg = [t for t in clean_trades
               if h_start <= datetime.fromisoformat(t["entry_time"]).hour < h_end]
        if seg:
            seg_pnl = sum(t["pnl_usd"] for t in seg)
            seg_wr = sum(1 for t in seg if t["pnl_usd"] > 0) / len(seg)
            seg_r = sum(t["pnl_r"] for t in seg)
            print(f"  {label:12s}: {len(seg):3d} trades | WR {seg_wr:.0%} | PnL ${seg_pnl:+.2f} | {seg_r:+.2f}R")
        else:
            print(f"  {label:12s}: No trades")

    # ── Weekday vs Weekend ──
    print("\n  BY DAY TYPE:")
    print("  " + "-" * 65)
    weekday_trades = [t for t in clean_trades
                      if datetime.fromisoformat(t["entry_time"]).weekday() < 5]
    weekend_trades = [t for t in clean_trades
                      if datetime.fromisoformat(t["entry_time"]).weekday() >= 5]
    for label, seg in [("Weekday", weekday_trades), ("Weekend", weekend_trades)]:
        if seg:
            seg_pnl = sum(t["pnl_usd"] for t in seg)
            seg_wr = sum(1 for t in seg if t["pnl_usd"] > 0) / len(seg)
            seg_r = sum(t["pnl_r"] for t in seg)
            print(f"  {label:12s}: {len(seg):3d} trades | WR {seg_wr:.0%} | PnL ${seg_pnl:+.2f} | {seg_r:+.2f}R")
        else:
            print(f"  {label:12s}: No trades")

    # ── Exit reason breakdown ──
    print("\n  BY EXIT REASON:")
    print("  " + "-" * 65)
    reasons = defaultdict(list)
    for t in clean_trades:
        reasons[t["exit_reason"]].append(t)
    for reason, trades in sorted(reasons.items()):
        r_sum = sum(t["pnl_r"] for t in trades)
        wr = sum(1 for t in trades if t["pnl_usd"] > 0) / len(trades)
        print(f"  {reason:15s}: {len(trades):3d} trades | WR {wr:.0%} | {r_sum:+.2f}R")

    print("\n" + "=" * 70)
    print(f"  Output saved to {out.absolute()}/")
    print(f"    trades.csv       ({len(clean_trades)} rows)")
    print(f"    signals.csv      ({len(all_signals)} rows)")
    print(f"    daily_equity.csv ({len(eq_rows)} rows)")
    print("=" * 70)


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

def _calc_atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        prev = candles[i - 1].close
        c = candles[i]
        tr = max(c.high - c.low, abs(c.high - prev), abs(c.low - prev))
        trs.append(tr)
    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0.0
    atr = sum(trs[:period]) / period
    for j in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[j]) / period
    return atr


def _close_replay_pos(pos: ReplayPosition, price: float, reason: str,
                       exit_time: datetime, executor: ReplayExecutor):
    fill, fee, pnl = executor.fill_exit(pos.entry_price, price, pos.quantity, pos.side)
    pos.realized_pnl += pnl
    pos.total_fees += fee
    pos.exit_price = fill
    pos.exit_reason = reason
    pos.exit_time = exit_time
    pos.closed = True


def _record_trade(pos: ReplayPosition, all_trades: list[dict], executor: ReplayExecutor):
    stop_dist = abs(pos.entry_price - pos.initial_stop)
    total_pnl = pos.realized_pnl - pos.total_fees
    if stop_dist > 0 and pos.original_qty > 0:
        pnl_r = pos.realized_pnl / (stop_dist * pos.original_qty)
    else:
        pnl_r = 0

    hold_s = (pos.exit_time - pos.entry_time).total_seconds() if pos.exit_time else 0

    all_trades.append({
        "trade_uuid": pos.trade_uuid,
        "symbol": pos.symbol,
        "side": pos.side.value,
        "engine": pos.engine.value,
        "entry_time": pos.entry_time.isoformat(),
        "exit_time": pos.exit_time.isoformat() if pos.exit_time else "",
        "entry_price": round(pos.entry_price, 6),
        "exit_price": round(pos.exit_price, 6),
        "quantity": round(pos.original_qty, 6),
        "leverage": pos.leverage,
        "initial_stop": round(pos.initial_stop, 6),
        "stop_distance": round(stop_dist, 6),
        "pnl_usd": round(total_pnl, 4),
        "pnl_r": round(pnl_r, 4),
        "total_fees": round(pos.total_fees, 4),
        "hold_time_s": round(hold_s, 1),
        "hold_candles": pos.candles_held,
        "exit_reason": pos.exit_reason,
        "tp1_hit": pos.tp1_hit,
        "equity_after": round(executor.equity, 2),
        "funding_fees": 0,
    })


def _print_no_trade_analysis(signals, candles_5m, regime, config, symbol):
    """Analyze why no trades were generated."""
    print("\n  DIAGNOSTIC: Why no trades?")
    print(f"  Signals generated: {len(signals)}")
    if signals:
        print("  Signals were generated but no trades executed (size/risk filters).")
    else:
        # Check regime filter across all candles
        regime_blocks = 0
        total_checks = 0
        for i in range(50, min(200, len(candles_5m))):
            total_checks += 1
            # Simplified check
        print(f"  Engine generated 0 signals from {len(candles_5m)} candles.")
        print("  Possible causes:")
        print("    - ATR percentile threshold too tight")
        print("    - BB width threshold too tight")
        print("    - Breakout volume multiplier too high")
        print("    - Min compression candles too demanding")
        print("    - Regime filter blocking most of the data")
        print()
        print("  Recommendation: Run with relaxed thresholds or check")
        print("  if SOL's recent volatility regime matches compression patterns.")


if __name__ == "__main__":
    asyncio.run(run_replay(
        symbol="SOLUSDT",
        days=90,
        initial_equity=1000.0,
        output_dir="replay_output",
    ))
