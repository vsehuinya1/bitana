"""
Bitana Replay V2.1 — Parametric Sweep with IS/OOS Validation

Fetches data ONCE (1m, 5m, 15m, 4H), then runs 12 parameter combinations:
  ATR pctl (15th, 10th)  ×  Vol mult (1.8×, 2.0×)  ×  Stop width (0%, +10%, +20%)

All variants share:
  - min_compression_candles = 10
  - 4H EMA trend filter (EMA20 vs EMA50)  — NEUTRAL = NO TRADE
  - Time stop = 6 candles / +0.5R threshold

Enhancements over V2:
  - 60-day in-sample / 30-day out-of-sample split
  - Minimum 15 trades filter
  - Equity curve CSV + smoothness stats (R², Sharpe)
  - Session breakdown by UTC block (Asia/EU/US)
  - Top-3 cross-sample validation report
"""
from __future__ import annotations
import bisect

import asyncio
import copy
import csv
import itertools
import math
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.loader import load_config, resolve_symbol_config, CompressionConfig
from core.logging_setup import get_logger
from core.models import Candle, EngineType, Side, Signal
from data.binance_rest import BinanceRestClient
from data.rate_limiter import RateLimiterGroup
from engines.compression_breakout import CompressionBreakoutEngine
from engines.regime_filter import RegimeFilter

logger = get_logger("replay_v2")

SYMBOL = "SOLUSDT"
DAYS = 90
IS_DAYS = 60   # in-sample
OOS_DAYS = 30  # out-of-sample
INITIAL_EQUITY = 1000.0
MIN_TRADES = 15  # minimum trades to qualify

# UTC session blocks
SESSIONS = {
    "Asia":   (0, 8),   # 00:00–07:59 UTC
    "Europe": (8, 16),  # 08:00–15:59 UTC
    "US":     (16, 24), # 16:00–23:59 UTC
}

# ─────────────────────────────────────────────────────────────────────────────
# Parameter Grid
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class VariantParams:
    label: str
    atr_percentile: float
    breakout_volume_mult: float
    stop_width_pct: float  # 0.0 = baseline, 0.10 = +10%, 0.20 = +20%
    min_compression_candles: int = 10
    time_stop_candles: int = 6
    time_stop_r_threshold: float = 0.5


def build_param_grid() -> list[VariantParams]:
    atr_pctls = [15.0, 10.0]
    vol_mults = [1.8, 2.0]
    stop_widths = [("base", 0.0), ("+10%", 0.10), ("+20%", 0.20)]

    grid = []
    for atr, vol, (sw_label, sw_pct) in itertools.product(atr_pctls, vol_mults, stop_widths):
        label = f"ATR{int(atr)}_VOL{vol:.1f}_STOP{sw_label}"
        grid.append(VariantParams(
            label=label,
            atr_percentile=atr,
            breakout_volume_mult=vol,
            stop_width_pct=sw_pct,
        ))
    return grid


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
        await asyncio.sleep(0.15)

    seen = set()
    deduped = []
    for c in all_candles:
        key = c.open_time
        if key not in seen:
            seen.add(key)
            deduped.append(c)

    return sorted(deduped, key=lambda c: c.open_time)


# ─────────────────────────────────────────────────────────────────────────────
# 4H Trend Filter — EMA(20) vs EMA(50) on 4H candles
# NEUTRAL = NO TRADE (explicit safeguard)
# ─────────────────────────────────────────────────────────────────────────────

def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


class TrendFilter4H:
    """Precomputes 4H trend state for fast lookup during replay."""

    def __init__(self, candles_4h: list[Candle]):
        closes = [c.close for c in candles_4h]
        self.ema20 = _ema(closes, 20)
        self.ema50 = _ema(closes, 50)
        self._trend_at: dict[datetime, str] = {}
        for i, c in enumerate(candles_4h):
            if i < 50:
                self._trend_at[c.close_time] = "NEUTRAL"
            elif self.ema20[i] > self.ema50[i]:
                self._trend_at[c.close_time] = "BULLISH"
            elif self.ema20[i] < self.ema50[i]:
                self._trend_at[c.close_time] = "BEARISH"
            else:
                self._trend_at[c.close_time] = "NEUTRAL"
        self._times = sorted(self._trend_at.keys())

    def get_trend(self, at_time: datetime) -> str:
        best = None
        for t in self._times:
            if t <= at_time:
                best = t
            else:
                break
        if best is None:
            return "NEUTRAL"
        return self._trend_at[best]

    def signal_aligned(self, side: Side, at_time: datetime) -> bool:
        """NEUTRAL = always False (no trade). Only BULLISH+LONG or BEARISH+SHORT."""
        trend = self.get_trend(at_time)
        if trend == "NEUTRAL":
            return False
        if side == Side.LONG and trend == "BULLISH":
            return True
        if side == Side.SHORT and trend == "BEARISH":
            return True
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Replay Executor & Position
# ─────────────────────────────────────────────────────────────────────────────

class ReplayExecutor:
    def __init__(self, initial_equity: float, taker_bps: float, slippage_bps: float):
        self.equity = initial_equity
        self.initial_equity = initial_equity
        self.peak_equity = initial_equity
        self.taker_bps = taker_bps
        self.slippage_bps = slippage_bps

    def fill_entry(self, price: float, qty: float, side: Side) -> tuple[float, float]:
        slip = price * (self.slippage_bps / 10000)
        fill = price + slip if side == Side.LONG else price - slip
        notional = qty * fill
        fee = notional * (self.taker_bps / 10000)
        self.equity -= fee
        return fill, fee

    def fill_exit(self, entry: float, exit_price: float, qty: float, side: Side) -> tuple[float, float, float]:
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
# Core helpers
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
    entry_hour = pos.entry_time.hour if pos.entry_time else 0
    session = "US"
    for sname, (sh, eh) in SESSIONS.items():
        if sh <= entry_hour < eh:
            session = sname
            break

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
        "session": session,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Variant result + per-sample metrics
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SampleMetrics:
    """Stats for a single sample period (IS or OOS)."""
    label: str = ""
    total_trades: int = 0
    win_rate: float = 0.0
    net_r: float = 0.0
    net_pnl_pct: float = 0.0
    expectancy_r: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    trades_per_week: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    cagr: float = 0.0
    calmar: float = 0.0
    # smoothness
    equity_r_squared: float = 0.0
    equity_sharpe: float = 0.0
    # session breakdown
    session_trades: dict = field(default_factory=dict)
    session_wr: dict = field(default_factory=dict)
    session_net_r: dict = field(default_factory=dict)


def compute_metrics(trades: list[dict], days: int, initial_eq: float) -> SampleMetrics:
    m = SampleMetrics()
    m.total_trades = len(trades)
    if not trades:
        return m

    rs = [t["pnl_r"] for t in trades]
    m.net_r = sum(rs)
    winners = [r for r in rs if r > 0]
    losers = [r for r in rs if r <= 0]
    m.win_rate = len(winners) / len(rs)
    m.expectancy_r = sum(rs) / len(rs)
    m.avg_win_r = sum(winners) / len(winners) if winners else 0
    m.avg_loss_r = sum(losers) / len(losers) if losers else 0

    pnls = [t["pnl_usd"] for t in trades]
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p <= 0))
    m.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    m.trades_per_week = len(trades) / (days / 7)

    # Equity curve + max drawdown
    equity_curve = [initial_eq]
    running = initial_eq
    peak = running
    max_dd = 0
    for t in trades:
        running += t["pnl_usd"]
        equity_curve.append(running)
        if running > peak:
            peak = running
        dd = (peak - running) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
    m.max_drawdown = max_dd
    m.net_pnl_pct = ((running - initial_eq) / initial_eq) * 100

    # CAGR
    if running > 0:
        total_return = running / initial_eq
        m.cagr = (total_return ** (365.0 / max(days, 1)) - 1) * 100
    else:
        m.cagr = -100.0

    # Calmar
    if m.max_drawdown > 0:
        m.calmar = m.cagr / (m.max_drawdown * 100)
    else:
        m.calmar = m.cagr if m.cagr > 0 else 0

    # Smoothness: R² of equity curve (linear fit)
    n = len(equity_curve)
    if n >= 3:
        xs = list(range(n))
        x_mean = sum(xs) / n
        y_mean = sum(equity_curve) / n
        ss_xy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, equity_curve))
        ss_xx = sum((x - x_mean) ** 2 for x in xs)
        ss_yy = sum((y - y_mean) ** 2 for y in equity_curve)
        if ss_xx > 0 and ss_yy > 0:
            r = ss_xy / (math.sqrt(ss_xx) * math.sqrt(ss_yy))
            m.equity_r_squared = r * r
        else:
            m.equity_r_squared = 0.0

    # Sharpe of trade returns (annualized)
    if len(rs) >= 2:
        mean_r = statistics.mean(rs)
        std_r = statistics.stdev(rs)
        if std_r > 0:
            trades_per_year = len(rs) / (days / 365)
            m.equity_sharpe = (mean_r / std_r) * math.sqrt(trades_per_year)

    # Session breakdown
    for sname in SESSIONS:
        sess_trades = [t for t in trades if t.get("session") == sname]
        m.session_trades[sname] = len(sess_trades)
        if sess_trades:
            sess_rs = [t["pnl_r"] for t in sess_trades]
            sess_wins = [r for r in sess_rs if r > 0]
            m.session_wr[sname] = len(sess_wins) / len(sess_rs) if sess_rs else 0
            m.session_net_r[sname] = sum(sess_rs)
        else:
            m.session_wr[sname] = 0
            m.session_net_r[sname] = 0

    return m


@dataclass
class VariantResult:
    label: str
    params: VariantParams
    trades: list[dict] = field(default_factory=list)
    equity_curve: list[tuple[str, float]] = field(default_factory=list)
    full: SampleMetrics = field(default_factory=SampleMetrics)
    insample: SampleMetrics = field(default_factory=SampleMetrics)
    outsample: SampleMetrics = field(default_factory=SampleMetrics)
    qualified: bool = False  # passes MIN_TRADES filter


# ─────────────────────────────────────────────────────────────────────────────
# Single variant replay
# ─────────────────────────────────────────────────────────────────────────────

async def run_single_variant(
    params: VariantParams,
    candles_5m: list[Candle],
    candles_15m: list[Candle],
    candles_1m: list[Candle],
    trend_filter: TrendFilter4H,
    config,
    is_end: datetime,
) -> VariantResult:
    """Run full replay for one parameter variant."""

    resolved = resolve_symbol_config(config, SYMBOL)
    comp_cfg = resolved.compression.model_copy()
    comp_cfg.atr_percentile_threshold = params.atr_percentile
    comp_cfg.breakout_volume_multiplier = params.breakout_volume_mult
    comp_cfg.min_compression_candles = params.min_compression_candles

    engine = CompressionBreakoutEngine(comp_cfg)
    regime = RegimeFilter(config.regime_filters)
    cfg_risk = config.risk
    cfg_profit = config.profit_taking
    cfg_brakes = config.brakes

    executor = ReplayExecutor(
        initial_equity=INITIAL_EQUITY,
        taker_bps=config.fees.taker_bps,
        slippage_bps=config.fees.default_slippage_bps,
    )

    # Pre-sorted with extracted times for bisect
    c15_by_time = sorted(candles_15m, key=lambda c: c.close_time)
    c1_by_time = sorted(candles_1m, key=lambda c: c.close_time)
    c15_times = [c.close_time for c in c15_by_time]
    c1_times = [c.close_time for c in c1_by_time]

    # State
    open_positions: list[ReplayPosition] = []
    all_trades: list[dict] = []
    equity_curve: list[tuple[str, float]] = []
    risk_pct = cfg_risk.default_risk_pct
    consecutive_losses = 0
    daily_loss = 0.0
    daily_loss_date = ""
    peak_equity = INITIAL_EQUITY

    warmup = max(
        comp_cfg.atr_lookback,
        comp_cfg.bb_period,
        comp_cfg.volume_avg_period,
        comp_cfg.min_compression_candles + 5,
        50,
    )

    for i in range(warmup, len(candles_5m)):
        c5 = candles_5m[i]
        c5_time = c5.close_time

        day_str = c5_time.strftime("%Y-%m-%d")
        if day_str != daily_loss_date:
            daily_loss = 0.0
            daily_loss_date = day_str

        # Build windows — bisect O(log n)
        window_5m = candles_5m[max(0, i - 200): i + 1]
        idx_15 = bisect.bisect_right(c15_times, c5_time)
        window_15m = c15_by_time[max(0, idx_15 - 60): idx_15]
        idx_1 = bisect.bisect_right(c1_times, c5_time)
        window_1m = c1_by_time[max(0, idx_1 - 15): idx_1]

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

            # Time stop: 6 candles unless +0.5R reached
            if (pos.candles_held >= params.time_stop_candles
                    and r_multiple < params.time_stop_r_threshold
                    and not pos.tp1_hit):
                _close_replay_pos(pos, current_price, "time_stop", c5_time, executor)
                _record_trade(pos, all_trades, executor)
                equity_curve.append((c5_time.isoformat(), round(executor.equity, 2)))
                continue

            # Stop loss
            stop_hit = False
            exit_price = 0.0
            if pos.side == Side.LONG and c5.low <= pos.stop_price:
                stop_hit = True
                exit_price = pos.stop_price
            elif pos.side == Side.SHORT and c5.high >= pos.stop_price:
                stop_hit = True
                exit_price = pos.stop_price

            if stop_hit:
                _close_replay_pos(pos, exit_price, "stop_loss", c5_time, executor)
                _record_trade(pos, all_trades, executor)
                equity_curve.append((c5_time.isoformat(), round(executor.equity, 2)))
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

            # Trailing stop
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

        # Update risk
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

        if executor.peak_equity > 0:
            dd = (executor.peak_equity - executor.equity) / executor.peak_equity
        else:
            dd = 0
        if dd > cfg_risk.drawdown_reduce_threshold:
            risk_pct = cfg_risk.reduced_risk_pct
        elif dd < cfg_risk.drawdown_restore_threshold:
            if consecutive_losses < cfg_brakes.consecutive_loss_threshold:
                risk_pct = cfg_risk.default_risk_pct

        # ── 2. Entry checks ──
        if daily_loss >= cfg_brakes.daily_loss_limit_pct:
            continue
        if len(open_positions) >= config.portfolio.max_per_symbol:
            continue

        # Regime filter
        tradeable, _ = regime.check(SYMBOL, window_15m, now_utc=c5_time)
        if not tradeable:
            continue

        # Engine evaluation
        try:
            sig = await engine.evaluate(SYMBOL, window_5m, window_15m, window_1m)
        except Exception:
            continue

        if not sig:
            continue

        # 4H trend filter — NEUTRAL = no trade
        if not trend_filter.signal_aligned(sig.side, c5_time):
            continue

        # Widen stop if needed
        if params.stop_width_pct > 0:
            stop_dist_orig = abs(sig.entry_price - sig.stop_price)
            extra = stop_dist_orig * params.stop_width_pct
            if sig.side == Side.LONG:
                sig.stop_price -= extra
            else:
                sig.stop_price += extra

        # Position sizing
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

        max_notional = equity * leverage * (1 - cfg_risk.liquidation_buffer_pct)
        if notional > max_notional:
            quantity = max_notional / sig.entry_price

        if quantity <= 0:
            continue

        # Execute entry
        fill_price, entry_fee = executor.fill_entry(sig.entry_price, quantity, sig.side)

        pos = ReplayPosition(
            signal=sig, entry_price=fill_price, qty=quantity,
            leverage=leverage, entry_fee=entry_fee, entry_time=c5_time,
        )
        if params.stop_width_pct > 0:
            pos.stop_price = sig.stop_price
            pos.initial_stop = sig.stop_price
        open_positions.append(pos)

    # Close remaining
    if candles_5m:
        last_price = candles_5m[-1].close
        for pos in open_positions:
            if not pos.closed:
                _close_replay_pos(pos, last_price, "replay_end",
                                  candles_5m[-1].close_time, executor)
                _record_trade(pos, all_trades, executor)
                equity_curve.append((candles_5m[-1].close_time.isoformat(), round(executor.equity, 2)))

    # ── Build result with IS/OOS split ──
    clean_trades = [{k: v for k, v in t.items() if not k.startswith("_")} for t in all_trades]

    is_trades = [t for t in clean_trades if t["entry_time"] < is_end.isoformat()]
    oos_trades = [t for t in clean_trades if t["entry_time"] >= is_end.isoformat()]

    result = VariantResult(label=params.label, params=params, trades=clean_trades)
    result.equity_curve = equity_curve
    result.full = compute_metrics(clean_trades, DAYS, INITIAL_EQUITY)
    result.insample = compute_metrics(is_trades, IS_DAYS, INITIAL_EQUITY)
    result.outsample = compute_metrics(oos_trades, OOS_DAYS, INITIAL_EQUITY)
    result.qualified = result.full.total_trades >= MIN_TRADES

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_metric_row(label: str, m: SampleMetrics) -> str:
    return (f"  {label:<28s} {m.total_trades:>6d} {m.win_rate:>5.1%} {m.net_r:>+7.2f}R "
            f"{m.net_pnl_pct:>+7.2f}% {m.profit_factor:>5.2f} {m.expectancy_r:>+7.4f}R "
            f"{m.max_drawdown:>6.1%} {m.trades_per_week:>5.1f} "
            f"{m.equity_r_squared:>5.3f} {m.equity_sharpe:>+6.2f} {m.calmar:>+6.2f}")


def print_ranked_results(results: list[VariantResult]):
    sep = "=" * 130
    thin = "-" * 130

    print(f"\n{sep}")
    print("  BITANA REPLAY V2.1 — SOLUSDT COMPRESSION BREAKOUT PARAMETER SWEEP")
    print(f"  Period: {DAYS}d (IS={IS_DAYS}d + OOS={OOS_DAYS}d) | "
          f"Initial: ${INITIAL_EQUITY:.0f} | Variants: {len(results)} | "
          f"Min trades: {MIN_TRADES}")
    print(f"  Shared: min_compression=10, 4H_trend=ON (NEUTRAL=no-trade), "
          f"time_stop=6c/+0.5R")
    print(sep)

    # ── Full-period overview ──
    header = (f"  {'Variant':<28s} {'Trades':>6s} {'WR':>6s} {'NetR':>8s} "
              f"{'PnL%':>8s} {'PF':>6s} {'Exp/R':>8s} {'MaxDD':>7s} "
              f"{'T/wk':>5s} {'R²':>5s} {'Sharpe':>6s} {'Calmar':>7s}")
    print(f"\n  ── FULL PERIOD ({DAYS}d) ──")
    print(header)
    print(f"  {thin}")

    sorted_full = sorted(results, key=lambda x: x.full.calmar, reverse=True)
    for r in sorted_full:
        q = "✓" if r.qualified else "✗"
        print(f"{q}" + _fmt_metric_row(r.label, r.full))

    # ── In-Sample ──
    print(f"\n  ── IN-SAMPLE ({IS_DAYS}d) ──")
    print(header)
    print(f"  {thin}")
    for r in sorted_full:
        q = "✓" if r.qualified else "✗"
        print(f"{q}" + _fmt_metric_row(r.label, r.insample))

    # ── Out-of-Sample ──
    print(f"\n  ── OUT-OF-SAMPLE ({OOS_DAYS}d) ──")
    print(header)
    print(f"  {thin}")
    for r in sorted_full:
        q = "✓" if r.qualified else "✗"
        print(f"{q}" + _fmt_metric_row(r.label, r.outsample))

    # ── Session Breakdown ──
    print(f"\n{sep}")
    print("  SESSION BREAKDOWN (Full Period)")
    print(sep)
    sess_header = f"  {'Variant':<28s} {'Asia T':>6s} {'Asia WR':>7s} {'Asia R':>7s} {'EU T':>6s} {'EU WR':>7s} {'EU R':>7s} {'US T':>6s} {'US WR':>7s} {'US R':>7s}"
    print(sess_header)
    print(f"  {thin}")
    for r in sorted_full:
        m = r.full
        print(f"  {r.label:<28s} "
              f"{m.session_trades.get('Asia', 0):>6d} {m.session_wr.get('Asia', 0):>6.1%} {m.session_net_r.get('Asia', 0):>+6.2f}R "
              f"{m.session_trades.get('Europe', 0):>6d} {m.session_wr.get('Europe', 0):>6.1%} {m.session_net_r.get('Europe', 0):>+6.2f}R "
              f"{m.session_trades.get('US', 0):>6d} {m.session_wr.get('US', 0):>6.1%} {m.session_net_r.get('US', 0):>+6.2f}R")

    # ── Rankings ──
    qualified = [r for r in results if r.qualified]
    print(f"\n{sep}")
    print(f"  RANKINGS (qualified: {len(qualified)}/{len(results)} with ≥{MIN_TRADES} trades)")
    print(sep)

    if not qualified:
        print("\n  ⚠️  No variants passed the minimum trade filter.\n")
    else:
        rankings = [
            ("Net R", sorted(qualified, key=lambda x: x.full.net_r, reverse=True)),
            ("Max Drawdown (lowest)", sorted(qualified, key=lambda x: x.full.max_drawdown)),
            ("Profit Factor", sorted(qualified, key=lambda x: x.full.profit_factor, reverse=True)),
            ("Expectancy/R", sorted(qualified, key=lambda x: x.full.expectancy_r, reverse=True)),
            ("Trades/Week", sorted(qualified, key=lambda x: x.full.trades_per_week, reverse=True)),
        ]

        for title, ranked in rankings:
            print(f"\n  {title}:")
            for i, r in enumerate(ranked[:5], 1):
                val = ""
                if "Net R" in title:
                    val = f"{r.full.net_r:+.2f}R"
                elif "Drawdown" in title:
                    val = f"{r.full.max_drawdown:.1%}"
                elif "Profit" in title:
                    val = f"{r.full.profit_factor:.2f}"
                elif "Expectancy" in title:
                    val = f"{r.full.expectancy_r:+.4f}R"
                elif "Trades" in title:
                    val = f"{r.full.trades_per_week:.1f}/wk"
                print(f"    {i}. {r.label:<28s}  {val}")

    # ── Top 3 Cross-Sample Validation ──
    print(f"\n{sep}")
    print("  TOP 3 CROSS-SAMPLE VALIDATION")
    print(sep)

    if not qualified:
        print("\n  No qualified variants to validate.\n")
    else:
        # Score: rank IS calmar + rank OOS calmar, lowest total = best
        is_sorted = sorted(qualified, key=lambda x: x.insample.calmar, reverse=True)
        oos_sorted = sorted(qualified, key=lambda x: x.outsample.calmar, reverse=True)

        is_rank = {r.label: i for i, r in enumerate(is_sorted, 1)}
        oos_rank = {r.label: i for i, r in enumerate(oos_sorted, 1)}

        cross_scores = []
        for r in qualified:
            score = is_rank[r.label] + oos_rank[r.label]
            cross_scores.append((score, r))
        cross_scores.sort(key=lambda x: x[0])

        for rank_i, (score, r) in enumerate(cross_scores[:3], 1):
            is_m = r.insample
            oos_m = r.outsample
            strong_is = is_m.expectancy_r > 0
            strong_oos = oos_m.expectancy_r > 0

            verdict = "✅ STRONG" if strong_is and strong_oos else "⚠️ PARTIAL" if strong_is or strong_oos else "❌ WEAK"

            print(f"""
  #{rank_i}  {r.label}  ({verdict})
      Cross-rank score: {score} (IS #{is_rank[r.label]} + OOS #{oos_rank[r.label]})

      {'Metric':<16s} {'IS (' + str(IS_DAYS) + 'd)':>12s} {'OOS (' + str(OOS_DAYS) + 'd)':>12s}
      {'─' * 42}
      {'Trades':<16s} {is_m.total_trades:>12d} {oos_m.total_trades:>12d}
      {'Win Rate':<16s} {is_m.win_rate:>11.1%} {oos_m.win_rate:>11.1%}
      {'Net R':<16s} {is_m.net_r:>+11.2f}R {oos_m.net_r:>+11.2f}R
      {'Expectancy/R':<16s} {is_m.expectancy_r:>+11.4f}R {oos_m.expectancy_r:>+11.4f}R
      {'PF':<16s} {is_m.profit_factor:>12.2f} {oos_m.profit_factor:>12.2f}
      {'Max DD':<16s} {is_m.max_drawdown:>11.1%} {oos_m.max_drawdown:>11.1%}
      {'R²':<16s} {is_m.equity_r_squared:>12.3f} {oos_m.equity_r_squared:>12.3f}
      {'Sharpe':<16s} {is_m.equity_sharpe:>+11.2f} {oos_m.equity_sharpe:>+11.2f}
      {'Calmar':<16s} {is_m.calmar:>+11.2f} {oos_m.calmar:>+11.2f}""")

    # ── Production recommendation ──
    print(f"\n{sep}")
    print("  PRODUCTION CANDIDATE RECOMMENDATION")
    print(sep)

    positive = [r for r in qualified if r.full.expectancy_r > 0]
    cross_positive = [r for r in qualified if r.insample.expectancy_r > 0 and r.outsample.expectancy_r > 0]

    if cross_positive:
        best = max(cross_positive, key=lambda x: x.full.calmar)
        print(f"""
  ✅  BEST CANDIDATE: {best.label}

      Full {DAYS}d:     {best.full.net_r:+.2f}R | Calmar {best.full.calmar:+.2f} | WR {best.full.win_rate:.1%}
      IS {IS_DAYS}d:      {best.insample.net_r:+.2f}R | Calmar {best.insample.calmar:+.2f} | WR {best.insample.win_rate:.1%}
      OOS {OOS_DAYS}d:     {best.outsample.net_r:+.2f}R | Calmar {best.outsample.calmar:+.2f} | WR {best.outsample.win_rate:.1%}

      ATR Pctl: {best.params.atr_percentile:.0f}th | Vol Mult: {best.params.breakout_volume_mult:.1f}x | Stop: {"baseline" if best.params.stop_width_pct == 0 else f"+{best.params.stop_width_pct:.0%}"}
""")
    elif positive:
        best = max(positive, key=lambda x: x.full.calmar)
        print(f"""
  ⚠️  PARTIAL CANDIDATE: {best.label}
      Positive full-period but NOT confirmed in both IS and OOS.

      Full {DAYS}d:     {best.full.net_r:+.2f}R | Calmar {best.full.calmar:+.2f}
      IS {IS_DAYS}d:      {best.insample.net_r:+.2f}R | Expectancy {best.insample.expectancy_r:+.4f}R
      OOS {OOS_DAYS}d:     {best.outsample.net_r:+.2f}R | Expectancy {best.outsample.expectancy_r:+.4f}R
""")
    else:
        least_bad = max(results, key=lambda x: x.full.calmar)
        print(f"""
  ❌  NO POSITIVE EXPECTANCY VARIANT FOUND

      Least-bad: {least_bad.label}
      Net R: {least_bad.full.net_r:+.2f}R | CAGR: {least_bad.full.cagr:+.1f}% | MaxDD: {least_bad.full.max_drawdown:.1%}
      Win Rate: {least_bad.full.win_rate:.1%} | Expectancy: {least_bad.full.expectancy_r:+.4f}R

      VERDICT: Compression Breakout on SOL does not show positive edge
               even with tighter filters and 4H trend alignment.
               Consider: Phase 2 squeeze engine, or BTC-only operation.
""")

    print(sep)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    config = load_config()
    grid = build_param_grid()

    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=DAYS)
    is_end = start + timedelta(days=IS_DAYS)  # boundary between IS and OOS

    print(f"╔{'═' * 70}╗")
    print(f"║  BITANA REPLAY V2.1 — PARAMETER SWEEP + IS/OOS                     ║")
    print(f"║  {SYMBOL} | {start.date()} → {end.date()} ({DAYS} days)              ║")
    print(f"║  IS: {start.date()} → {is_end.date()} | OOS: {is_end.date()} → {end.date()}       ║")
    print(f"║  Variants: {len(grid)} | Equity: ${INITIAL_EQUITY:.0f} | Min trades: {MIN_TRADES}           ║")
    print(f"╚{'═' * 70}╝")

    # ── Fetch all data ONCE ──
    print("\n📡  Fetching historical data (one-time)...")
    rate_limiter = RateLimiterGroup()
    client = BinanceRestClient(testnet=False, rate_limiter=rate_limiter)
    await client.start()

    start_4h = start - timedelta(days=60)

    candles_1m = await fetch_all_klines(client, SYMBOL, "1m", start, end)
    print(f"  ✓ 1m:  {len(candles_1m)} candles")
    candles_5m = await fetch_all_klines(client, SYMBOL, "5m", start, end)
    print(f"  ✓ 5m:  {len(candles_5m)} candles")
    candles_15m = await fetch_all_klines(client, SYMBOL, "15m", start, end)
    print(f"  ✓ 15m: {len(candles_15m)} candles")
    candles_4h = await fetch_all_klines(client, SYMBOL, "4h", start_4h, end)
    print(f"  ✓ 4H:  {len(candles_4h)} candles")

    await client.close()

    if not candles_5m:
        print("ERROR: No 5m data fetched. Aborting.")
        return

    trend_filter = TrendFilter4H(candles_4h)
    print(f"\n  4H trend filter built ({len(candles_4h)} candles, EMA20/50)")
    print(f"  NEUTRAL trend = NO TRADE (explicit safeguard)")

    # ── Run all variants ──
    print(f"\n🔁  Running {len(grid)} variants...\n")
    results: list[VariantResult] = []
    t0 = time.time()

    for idx, params in enumerate(grid, 1):
        vt0 = time.time()
        result = await run_single_variant(
            params, candles_5m, candles_15m, candles_1m, trend_filter, config, is_end
        )
        elapsed = time.time() - vt0
        results.append(result)

        q = "✓" if result.qualified else "✗"
        status = "✅" if result.full.expectancy_r > 0 else "❌"
        print(f"  [{idx:2d}/{len(grid)}] {status} {q} {params.label:<28s} "
              f"Trades={result.full.total_trades:>3d}  PnL={result.full.net_pnl_pct:>+7.2f}%  "
              f"NetR={result.full.net_r:>+7.2f}  PF={result.full.profit_factor:>5.2f}  "
              f"IS={result.insample.total_trades}t  OOS={result.outsample.total_trades}t  "
              f"({elapsed:.1f}s)")

    total_time = time.time() - t0
    print(f"\n  Total sweep time: {total_time:.1f}s")

    # ── Output CSVs ──
    out_dir = Path("replay_output/v2")
    out_dir.mkdir(parents=True, exist_ok=True)

    for r in results:
        # Trade CSV
        if r.trades:
            fname = out_dir / f"trades_{r.label}.csv"
            with open(fname, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=r.trades[0].keys())
                w.writeheader()
                w.writerows(r.trades)

        # Equity curve CSV
        if r.equity_curve:
            fname = out_dir / f"equity_{r.label}.csv"
            with open(fname, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["timestamp", "equity"])
                w.writerows(r.equity_curve)

    # Summary CSV
    summary_rows = []
    for r in sorted(results, key=lambda x: x.full.calmar, reverse=True):
        summary_rows.append({
            "rank": 0,
            "variant": r.label,
            "qualified": r.qualified,
            "full_trades": r.full.total_trades,
            "full_wr": round(r.full.win_rate, 4),
            "full_net_r": round(r.full.net_r, 2),
            "full_pnl_pct": round(r.full.net_pnl_pct, 2),
            "full_pf": round(r.full.profit_factor, 4),
            "full_exp_r": round(r.full.expectancy_r, 4),
            "full_max_dd": round(r.full.max_drawdown, 4),
            "full_tpw": round(r.full.trades_per_week, 1),
            "full_r2": round(r.full.equity_r_squared, 4),
            "full_sharpe": round(r.full.equity_sharpe, 4),
            "full_calmar": round(r.full.calmar, 4),
            "is_trades": r.insample.total_trades,
            "is_net_r": round(r.insample.net_r, 2),
            "is_exp_r": round(r.insample.expectancy_r, 4),
            "is_calmar": round(r.insample.calmar, 4),
            "oos_trades": r.outsample.total_trades,
            "oos_net_r": round(r.outsample.net_r, 2),
            "oos_exp_r": round(r.outsample.expectancy_r, 4),
            "oos_calmar": round(r.outsample.calmar, 4),
            "atr_pctl": r.params.atr_percentile,
            "vol_mult": r.params.breakout_volume_mult,
            "stop_width": r.params.stop_width_pct,
        })
    for i, row in enumerate(summary_rows, 1):
        row["rank"] = i

    if summary_rows:
        with open(out_dir / "sweep_summary.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=summary_rows[0].keys())
            w.writeheader()
            w.writerows(summary_rows)

    eq_count = len([r for r in results if r.equity_curve])
    print(f"\n  📁 Output: {out_dir.absolute()}/")
    print(f"     sweep_summary.csv  ({len(summary_rows)} rows)")
    print(f"     trades_*.csv       ({len([r for r in results if r.trades])} files)")
    print(f"     equity_*.csv       ({eq_count} files)")

    # ── Print ranked results ──
    print_ranked_results(results)


if __name__ == "__main__":
    asyncio.run(main())
