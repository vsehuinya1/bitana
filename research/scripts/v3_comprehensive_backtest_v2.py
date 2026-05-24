"""
V3 Liq-Cluster Comprehensive Backtest — Optimized
===================================================
Pre-computes all signals in bulk, then replays position management.
Runs ~50x faster than the per-bar loop approach.
"""
import csv
import math
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

# ═══════════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════════

ALL_SYMBOLS = [
    "NEARUSDT", "ZECUSDT", "ADAUSDT", "WLDUSDT", "UNIUSDT",
    "NMRUSDT", "PENDLEUSDT", "ARBUSDT", "RENDERUSDT", "RUNEUSDT",
    "FETUSDT", "DOTUSDT", "TONUSDT", "SOLUSDT", "1000LUNCUSDT",
    "ENAUSDT", "1000PEPEUSDT", "XRPUSDT", "FILUSDT", "BNBUSDT",
    "TAOUSDT", "CHZUSDT", "DASHUSDT", "QNTUSDT", "ICPUSDT",
    "XLMUSDT", "APTUSDT", "ETHUSDT",
]
BTC_SYMBOL = "BTCUSDT"

class V3Config:
    liq_lookback = 90
    liq_percentile = 0.90
    liq_min_lookback = 30
    liq_window = 2
    require_short_squeeze = True
    ret5d_min = -5.0

    range_lookback = 60
    imb_z_threshold = 2.0
    vol_z_threshold = 3.0
    body_strength_min = 0.60
    impulse_min_pct = 0.30
    ema_period = 20
    z_lookback = 100
    min_confirmations = 4

    cooldown_bars = 36
    no_reentry_after_stop = True

    atr_period = 14
    initial_stop_atr = 2.5

    vol_trail_atr = 2.0
    struct_lookback = 12
    decay_threshold = 0.30
    partial_r = 2.5
    partial_fraction = 0.50
    max_hold_bars = 288

CFG = V3Config()

BASE_RISK_PCT = 2.0
BTC_ALIGNED_PCT = 4.0
MAX_LEVERAGE = 10
MAX_POSITIONS = 10
MAX_PER_SYMBOL = 1
INITIAL_EQUITY = 10000.0
TAKER_BPS = 4.5
SLIP_BPS = 2.0

JAN1_MS = 1767225600000
APR30_MS = 1777593599000

DATA_DIR = Path("/root/bitana/backtest_data")
OUTPUT_DIR = Path("/root/bitana/backtest_output")
KLINES_DB = DATA_DIR / "klines_5m.db"

# ═══════════════════════════════════════════════════════════════════════
# Numpy helpers
# ═══════════════════════════════════════════════════════════════════════

def _ema_np(values, span):
    if len(values) < 2:
        return values[-1] if len(values) else 0.0
    alpha = 2.0 / (span + 1)
    ema = values[0]
    for v in values[1:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema


def _atr_np(highs, lows, closes, period):
    if len(highs) < 2:
        return highs[0] - lows[0] if len(highs) else 0.0
    tr = np.empty(len(highs))
    tr[0] = highs[0] - lows[0]
    for i in range(1, len(highs)):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    return _ema_np(tr, period)


def _z_score_np(values, lookback):
    if len(values) < 20:
        return 0.0
    window = values[-lookback:] if len(values) >= lookback else values
    mean = np.mean(window)
    std = np.std(window)
    if std < 1e-12:
        return 0.0
    return (values[-1] - mean) / std


# ═══════════════════════════════════════════════════════════════════════
# Data loading
# ═══════════════════════════════════════════════════════════════════════

def load_candles_from_db(symbol):
    conn = sqlite3.connect(str(KLINES_DB))
    rows = conn.execute(
        "SELECT open_time, close_time, open, high, low, close, volume, taker_buy_volume "
        "FROM klines WHERE symbol=? AND open_time >= ? AND open_time <= ? ORDER BY open_time",
        (symbol, JAN1_MS, APR30_MS)
    ).fetchall()
    conn.close()

    if not rows:
        return None

    n = len(rows)
    result = {
        "symbol": symbol,
        "open_time": np.array([r[0] for r in rows], dtype=np.int64),
        "close_time": np.array([r[1] for r in rows], dtype=np.int64),
        "open": np.array([r[2] for r in rows], dtype=np.float64),
        "high": np.array([r[3] for r in rows], dtype=np.float64),
        "low": np.array([r[4] for r in rows], dtype=np.float64),
        "close": np.array([r[5] for r in rows], dtype=np.float64),
        "volume": np.array([r[6] for r in rows], dtype=np.float64),
        "taker_buy_volume": np.array([r[7] for r in rows], dtype=np.float64),
        "n": n,
    }
    return result


# ═══════════════════════════════════════════════════════════════════════
# Liquidation proxy builder
# ═══════════════════════════════════════════════════════════════════════

def build_daily_liq_proxy(candles):
    """Build daily liquidation proxy from 5m candles."""
    daily = {}
    for i in range(candles["n"]):
        dt = datetime.fromtimestamp(candles["open_time"][i] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        if dt not in daily:
            daily[dt] = {"highs": [], "lows": [], "closes": [], "volumes": [], "wick_score": 0.0}
        d = daily[dt]
        d["highs"].append(candles["high"][i])
        d["lows"].append(candles["low"][i])
        d["closes"].append(candles["close"][i])
        d["volumes"].append(candles["volume"][i])
        body_top = max(candles["open"][i], candles["close"][i])
        body_bot = min(candles["open"][i], candles["close"][i])
        lower_wick = body_bot - candles["low"][i]
        upper_wick = candles["high"][i] - body_top
        d["wick_score"] += (lower_wick - upper_wick)

    daily_rows = []
    for dt, d in sorted(daily.items()):
        if not d["closes"]:
            continue
        close = d["closes"][-1]
        total_vol = sum(d["volumes"])
        avg_vol = total_vol / max(len(d["volumes"]), 1)
        total_range = sum(h - l for h, l in zip(d["highs"], d["lows"]))
        range_factor = total_range / max(close, 0.001) * 100
        wick_factor = abs(d["wick_score"]) / max(close, 0.001) * 100
        total_liq = total_vol * range_factor * (1 + wick_factor / 100)

        if d["wick_score"] > 0:
            short_liq = total_liq * 0.6
            long_liq = total_liq * 0.4
        else:
            short_liq = total_liq * 0.4
            long_liq = total_liq * 0.6

        daily_rows.append({
            "date": dt, "total_liq": total_liq,
            "long_liq": long_liq, "short_liq": short_liq,
            "close": close,
        })
    return daily_rows


# ═══════════════════════════════════════════════════════════════════════
# Cascade tracker
# ═══════════════════════════════════════════════════════════════════════

class CascadeTracker:
    def __init__(self):
        self._liq_history = deque(maxlen=100)

    def update(self, daily_rows):
        for row in daily_rows:
            self._liq_history.append(row)
        if len(self._liq_history) < CFG.liq_min_lookback:
            return False, 0.0, 0.0, 0.0

        liqs = [r["total_liq"] for r in self._liq_history]
        lookback = liqs[-CFG.liq_lookback:] if len(liqs) >= CFG.liq_lookback else liqs
        p90 = np.percentile(lookback, CFG.liq_percentile * 100)
        if p90 <= 0:
            return False, 0.0, 0.0, 0.0

        cascade_active = any(liqs[-(i + 1)] > p90 for i in range(min(CFG.liq_window + 1, len(liqs))))
        strength = liqs[-1] / p90 if p90 > 0 else 0

        last = self._liq_history[-1]
        total = last.get("total_liq", 0)
        imb = (last.get("long_liq", 0) - last.get("short_liq", 0)) / total if total > 0 else 0.0

        closes_hist = [r.get("close", 0) for r in self._liq_history]
        if len(closes_hist) >= 6 and closes_hist[-6] > 0:
            ret_5d = ((closes_hist[-1] / closes_hist[-6]) - 1) * 100
        else:
            ret_5d = 0.0

        if CFG.require_short_squeeze and imb >= 0:
            cascade_active = False
        if CFG.ret5d_min is not None and ret_5d <= CFG.ret5d_min:
            cascade_active = False

        return cascade_active, strength, imb, ret_5d


# ═══════════════════════════════════════════════════════════════════════
# Pre-compute all signals for a symbol
# ═══════════════════════════════════════════════════════════════════════

def precompute_signals(candles, daily_rows, btc_candles):
    """
    Pre-compute all entry signals for a symbol across the entire period.
    Returns list of signal dicts with entry_bar_idx for each signal.
    """
    n = candles["n"]
    if n < 200:
        return [], {}

    # Build cascade state per bar
    cascade_tracker = CascadeTracker()
    cascade_active_per_bar = np.zeros(n, dtype=bool)
    cascade_strength_per_bar = np.zeros(n)
    cascade_imb_per_bar = np.zeros(n)
    cascade_ret5d_per_bar = np.zeros(n)

    # Map daily rows to bar indices
    current_cascade = False
    for i in range(n):
        # Check if we need to update cascade (new day)
        bar_date = datetime.fromtimestamp(candles["open_time"][i] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        # Find daily rows up to this date
        relevant_rows = [r for r in daily_rows if r["date"] <= bar_date]
        if relevant_rows:
            ca, st, imb, r5 = cascade_tracker.update(relevant_rows)
            # Only update once per day
            if i == 0 or datetime.fromtimestamp(candles["open_time"][i-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d") != bar_date:
                current_cascade = ca
        cascade_active_per_bar[i] = current_cascade
        cascade_strength_per_bar[i] = cascade_tracker._liq_history[-1]["total_liq"] / np.percentile(
            [r["total_liq"] for r in cascade_tracker._liq_history][-CFG.liq_lookback:],
            CFG.liq_percentile * 100
        ) if cascade_tracker._liq_history and current_cascade else 0

    # Pre-compute rolling indicators using numpy
    closes = candles["close"]
    highs = candles["high"]
    lows = candles["low"]
    volumes = candles["volume"]
    taker_buys = candles["taker_buy_volume"]
    opens = candles["open"]

    # ATR
    tr = np.maximum(highs[1:] - lows[1:],
                    np.maximum(np.abs(highs[1:] - closes[:-1]),
                               np.abs(lows[1:] - closes[:-1])))
    atr = np.zeros(n)
    atr[0] = tr[0] if len(tr) > 0 else 0
    alpha_atr = 2.0 / (CFG.atr_period + 1)
    for i in range(1, n):
        if i - 1 < len(tr):
            atr[i] = alpha_atr * tr[i - 1] + (1 - alpha_atr) * atr[i - 1]
        else:
            atr[i] = atr[i - 1]

    # EMA
    ema = np.zeros(n)
    ema[0] = closes[0]
    alpha_ema = 2.0 / (CFG.ema_period + 1)
    for i in range(1, n):
        ema[i] = alpha_ema * closes[i] + (1 - alpha_ema) * ema[i - 1]

    # Rolling range high (excluding current bar)
    range_lookback = CFG.range_lookback
    range_high = np.zeros(n)
    for i in range(range_lookback + 1, n):
        range_high[i] = np.max(highs[i - range_lookback - 1:i - 1])

    # Volume z-score
    vol_z = np.zeros(n)
    for i in range(CFG.z_lookback, n):
        window = volumes[i - CFG.z_lookback:i]
        mean = np.mean(window)
        std = np.std(window)
        vol_z[i] = (volumes[i] - mean) / std if std > 1e-12 else 0

    # Taker imbalance z-score
    has_taker = taker_buys[-1] > 0
    imb_z = np.zeros(n)
    if has_taker:
        taker_sells = volumes - taker_buys
        totals = taker_buys + taker_sells
        safe_totals = np.where(totals > 0, totals, 1.0)
        imb_raw = (taker_buys - taker_sells) / safe_totals
        for i in range(CFG.z_lookback, n):
            window = imb_raw[i - CFG.z_lookback:i]
            mean = np.mean(window)
            std = np.std(window)
            imb_z[i] = (imb_raw[i] - mean) / std if std > 1e-12 else 0

    # Body strength
    candle_range = highs - lows
    candle_body = np.abs(closes - opens)
    body_strength = np.where(candle_range > 0, candle_body / candle_range, 0)

    # Bar return %
    bar_return_pct = np.where(opens > 0, (closes - opens) / opens * 100, 0)

    # BTC alignment per bar
    btc_aligned = np.zeros(n, dtype=bool)
    if btc_candles is not None and btc_candles["n"] > 21:
        btc_closes = btc_candles["close"]
        btc_ema20 = np.zeros(btc_candles["n"])
        btc_ema20[0] = btc_closes[0]
        alpha = 2.0 / 21
        for i in range(1, btc_candles["n"]):
            btc_ema20[i] = alpha * btc_closes[i] + (1 - alpha) * btc_ema20[i - 1]

        # Map BTC alignment to our bars by time
        btc_times = btc_candles["close_time"]
        for i in range(n):
            # Find BTC bar closest to our bar's close time
            our_time = candles["close_time"][i]
            idx = np.searchsorted(btc_times, our_time)
            if idx >= btc_candles["n"]:
                idx = btc_candles["n"] - 1
            if idx >= 20:
                btc_aligned[i] = btc_closes[idx] > btc_ema20[idx] and btc_closes[idx] > btc_closes[max(0, idx - 12)]

    # Find all signal bars
    min_confirmations = CFG.min_confirmations
    n_needed = max(CFG.range_lookback, CFG.z_lookback, CFG.ema_period * 3)

    signals = []
    cooldown_counter = 0
    stopped_in_window = False
    last_cascade = False

    for i in range(n_needed, n):
        if cooldown_counter > 0:
            cooldown_counter -= 1
            continue
        if CFG.no_reentry_after_stop and stopped_in_window:
            continue
        if not cascade_active_per_bar[i]:
            if last_cascade and not cascade_active_per_bar[i]:
                stopped_in_window = False
            last_cascade = cascade_active_per_bar[i]
            continue

        # Check confirmations
        conf_breakout = closes[i] > range_high[i]
        conf_imb = imb_z[i] > CFG.imb_z_threshold if has_taker else False
        conf_vol = vol_z[i] > CFG.vol_z_threshold
        conf_body = body_strength[i] > CFG.body_strength_min
        conf_impulse = bar_return_pct[i] > CFG.impulse_min_pct
        conf_momentum = closes[i] > ema[i]

        confirmations = {
            "breakout": bool(conf_breakout),
            "imb": bool(conf_imb),
            "vol": bool(conf_vol),
            "body": bool(conf_body),
            "impulse": bool(conf_impulse),
            "momentum": bool(conf_momentum),
        }
        confirm_count = sum(1 for v in confirmations.values() if v)

        if confirm_count < min_confirmations:
            continue

        entry_price = closes[i]
        stop_distance = atr[i] * CFG.initial_stop_atr
        stop_price = entry_price - stop_distance

        sig = {
            "trade_uuid": str(uuid.uuid4()),
            "symbol": candles["symbol"],
            "entry_bar_idx": i,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "risk_distance": stop_distance,
            "atr": atr[i],
            "confirmations": confirmations,
            "confirm_count": confirm_count,
            "cascade_strength": cascade_strength_per_bar[i],
            "liq_direction_imb": cascade_imb_per_bar[i],
            "ret_5d": cascade_ret5d_per_bar[i],
            "imb_z": round(float(imb_z[i]), 2),
            "vol_z": round(float(vol_z[i]), 2),
            "body_strength": round(float(body_strength[i]), 2),
            "bar_return_pct": round(float(bar_return_pct[i]), 3),
            "close_time": int(candles["close_time"][i]),
            "btc_aligned": bool(btc_aligned[i]),
        }
        signals.append(sig)
        cooldown_counter = CFG.cooldown_bars

    return signals, {"atr": atr, "closes": closes, "highs": highs, "lows": lows}


# ═══════════════════════════════════════════════════════════════════════
# Quality score
# ═══════════════════════════════════════════════════════════════════════

def compute_quality_score(sig):
    cs = sig.get("cascade_strength", 0)
    vz = sig.get("vol_z", 0)
    imb_z = sig.get("imb_z", 0)
    brp = sig.get("bar_return_pct", 0)
    bs = sig.get("body_strength", 0)

    liq_score = min(cs / 3.0, 1.0)
    vol_score = min(vz / 6.0, 1.0)
    imb_score = min(abs(imb_z) / 4.0, 1.0)
    impulse_score = min(brp / 2.0, 1.0)
    body_score = bs
    btc_score = 1.0 if sig.get("btc_aligned") else 0.0

    score = (liq_score * 0.30 + vol_score * 0.20 + impulse_score * 0.15 +
             imb_score * 0.15 + btc_score * 0.10 + body_score * 0.10)
    return round(score, 4)


def is_ny_session(close_time_ms):
    dt = datetime.fromtimestamp(close_time_ms / 1000, tz=timezone.utc)
    return 13 <= dt.hour < 22


# ═══════════════════════════════════════════════════════════════════════
# Position management replay
# ═══════════════════════════════════════════════════════════════════════

def replay_positions(all_signals, candles_data, btc_candles, config):
    """
    Replay position management given pre-computed signals.
    config: dict with sizing options
    """
    equity = INITIAL_EQUITY
    peak_equity = equity
    open_positions = {}  # symbol -> pos dict
    closed_trades = []
    atr_history = defaultdict(list)

    sectors = {
        "L1": ["NEARUSDT", "ADAUSDT", "DOTUSDT", "APTUSDT", "ICPUSDT", "XLMUSDT", "TONUSDT"],
        "L2/ARB": ["ARBUSDT", "RENDERUSDT"],
        "AI": ["FETUSDT", "TAOUSDT", "WLDUSDT"],
        "MEME": ["1000PEPEUSDT", "1000LUNCUSDT"],
        "DEFI": ["UNIUSDT", "RUNEUSDT", "PENDLEUSDT"],
        "OTHER": ["ZECUSDT", "NMRUSDT", "SOLUSDT", "ENAUSDT", "XRPUSDT", "FILUSDT",
                  "BNBUSDT", "CHZUSDT", "DASHUSDT", "QNTUSDT", "ETHUSDT"],
    }
    sym_to_sector = {}
    for sector, syms in sectors.items():
        for s in syms:
            sym_to_sector[s] = sector

    # Sort all signals by time
    all_signals_sorted = sorted(all_signals, key=lambda s: s["close_time"])
    signal_idx = 0

    # Get unified timeline from all symbols
    all_close_times = set()
    for sym, c in candles_data.items():
        if c is not None:
            for i in range(c["n"]):
                all_close_times.add(c["close_time"][i])
    sorted_times = sorted(all_close_times)

    for t_idx, close_time in enumerate(sorted_times):
        # Find which symbols have a candle at this time
        for sym, c in candles_data.items():
            if c is None:
                continue
            # Find bar index for this close_time
            idx = np.searchsorted(c["close_time"], close_time)
            if idx >= c["n"] or c["close_time"][idx] != close_time:
                continue

            # Manage existing position
            if sym in open_positions:
                pos = open_positions[sym]
                pos["bars_held"] += 1
                price = c["close"][idx]
                high = c["high"][idx]
                low = c["low"][idx]

                if high > pos["best_price"]:
                    pos["best_price"] = high

                sd = pos["risk_per_unit"]
                current_r = (price - pos["entry_price"]) / sd if sd > 0 else 0
                low_r = (low - pos["entry_price"]) / sd if sd > 0 else 0
                high_r = (high - pos["entry_price"]) / sd if sd > 0 else 0

                if low_r < pos["mae"]:
                    pos["mae"] = low_r
                if high_r > pos["mfe"]:
                    pos["mfe"] = high_r

                # ATR for trails
                atr_window = min(50, idx + 1)
                if atr_window > 1:
                    h = c["high"][idx - atr_window + 1:idx + 1]
                    l = c["low"][idx - atr_window + 1:idx + 1]
                    cl = c["close"][idx - atr_window + 1:idx + 1]
                    atr_val = _atr_np(h, l, cl, CFG.atr_period)
                else:
                    atr_val = 0

                exit_reason = None
                exit_price = None

                # Stop loss
                stop_price = pos["entry_price"] - sd
                if low <= stop_price:
                    exit_reason = "stop_loss"
                    exit_price = stop_price

                # Partial TP
                elif not pos["partial_taken"] and high_r >= CFG.partial_r:
                    pos["partial_taken"] = True
                    pos["quantity"] *= (1 - CFG.partial_fraction)
                    pos["rpnl"] += (high - pos["entry_price"]) * pos["orig_quantity"] * CFG.partial_fraction

                # Vol trail
                if exit_reason is None and atr_val > 0:
                    new_vol_trail = price - atr_val * CFG.vol_trail_atr
                    if new_vol_trail > pos["vol_trail"]:
                        pos["vol_trail"] = new_vol_trail
                    if pos["vol_trail"] > pos["entry_price"] and low <= pos["vol_trail"]:
                        exit_reason = "vol_trail"
                        exit_price = pos["vol_trail"]

                # Struct trail
                if exit_reason is None and idx >= CFG.struct_lookback:
                    swing_low = np.min(c["low"][idx - CFG.struct_lookback + 1:idx + 1])
                    if swing_low > pos["struct_trail"]:
                        pos["struct_trail"] = swing_low
                    if pos["struct_trail"] > pos["entry_price"] and low <= pos["struct_trail"]:
                        exit_reason = "struct_trail"
                        exit_price = pos["struct_trail"]

                # Expansion decay
                if exit_reason is None and pos["bars_held"] > 6 and current_r > 0.5:
                    peak_r = (pos["best_price"] - pos["entry_price"]) / sd if sd > 0 else 0
                    if peak_r > 0 and (current_r / peak_r) < (1 - CFG.decay_threshold):
                        exit_reason = "expansion_decay"
                        exit_price = price

                # Time stop
                if exit_reason is None and pos["bars_held"] >= CFG.max_hold_bars:
                    exit_reason = "time_stop"
                    exit_price = price

                if exit_reason:
                    fill_slip = exit_price * (SLIP_BPS / 10000)
                    fill = exit_price - fill_slip
                    fee = pos["quantity"] * fill * (TAKER_BPS / 10000)
                    pnl = (fill - pos["entry_price"]) * pos["quantity"]
                    equity += pnl - fee
                    if equity > peak_equity:
                        peak_equity = equity

                    pnl_r = (exit_price - pos["entry_price"]) / sd if sd > 0 else 0
                    net_pnl = pnl - fee + pos.get("rpnl", 0)

                    trade_record = {
                        "trade_uuid": pos["trade_uuid"],
                        "symbol": sym,
                        "side": "LONG",
                        "entry_time": pos["entry_time"],
                        "exit_time": datetime.fromtimestamp(close_time / 1000, tz=timezone.utc).isoformat(),
                        "entry_price": round(pos["entry_price"], 6),
                        "exit_price": round(fill, 6),
                        "quantity": round(pos["orig_quantity"], 6),
                        "leverage": pos["leverage"],
                        "stop_dist": round(sd, 6),
                        "pnl_usd": round(net_pnl, 4),
                        "pnl_r": round(pnl_r, 4),
                        "fees": round(pos.get("fees", 0) + fee, 4),
                        "hold_candles": pos["bars_held"],
                        "exit_reason": exit_reason,
                        "tp1_hit": 1 if pos["partial_taken"] else 0,
                        "equity_after": round(equity, 2),
                        "btc_aligned": pos.get("btc_aligned", 0),
                        "confirmations": str(pos.get("confirmations", "")),
                        "confirm_count": pos.get("confirm_count", 0),
                        "mae": round(pos["mae"], 4),
                        "mfe": round(pos["mfe"], 4),
                        "quality_score": pos.get("quality_score", 0),
                        "is_ny": pos.get("is_ny", False),
                        "pyramid_adds": pos.get("pyramid_adds", 0),
                        "sector": sym_to_sector.get(sym, "OTHER"),
                        "label": config.get("label", "unknown"),
                    }
                    closed_trades.append(trade_record)
                    del open_positions[sym]

        # Check for new signals at this time
        while signal_idx < len(all_signals_sorted) and all_signals_sorted[signal_idx]["close_time"] <= close_time:
            sig = all_signals_sorted[signal_idx]
            sym = sig["symbol"]

            if sym in open_positions or len(open_positions) >= MAX_POSITIONS:
                signal_idx += 1
                continue

            # Correlation control
            if config.get("use_correlation_control"):
                sector = sym_to_sector.get(sym, "OTHER")
                sector_count = sum(1 for p in open_positions.values() if p.get("sector") == sector)
                if sector_count >= config.get("correlation_cap", 3):
                    signal_idx += 1
                    continue

            # Quality scoring
            quality_score = compute_quality_score(sig) if config.get("use_quality_scoring") else 0.5

            # Risk sizing
            risk_pct = BTC_ALIGNED_PCT if sig.get("btc_aligned") else BASE_RISK_PCT

            # Vol targeting
            if config.get("use_vol_targeting") and len(atr_history[sym]) >= 20:
                median_atr = np.median(atr_history[sym][-100:])
                current_atr = sig.get("atr", 0)
                if current_atr > 0:
                    vol_ratio = median_atr / current_atr
                    vol_ratio = max(0.5, min(2.0, vol_ratio))
                    risk_pct *= vol_ratio

            # Quality adjustment
            if config.get("use_quality_scoring"):
                quality_mult = 0.25 + quality_score * 1.5
                risk_pct *= quality_mult

            # NY boost
            ny_boost = 1.0
            if is_ny_session(sig["close_time"]):
                ny_boost = 1.5
                if config.get("use_pyramiding"):
                    ny_boost = 2.0

            # Regime sizing
            if config.get("use_regime_sizing") and len(atr_history[sym]) >= 20:
                median_atr = np.median(atr_history[sym][-100:])
                current_atr = sig.get("atr", 0)
                if median_atr > 0 and current_atr > 0:
                    regime_ratio = current_atr / median_atr
                    if regime_ratio > 1.5:
                        ny_boost *= min(1.5, regime_ratio / 1.5)
                    elif regime_ratio < 0.5:
                        ny_boost *= 0.5

            risk_pct *= ny_boost
            risk_pct = min(risk_pct, 8.0)

            sd = sig["risk_distance"]
            if sd <= 0:
                signal_idx += 1
                continue

            ra = equity * (risk_pct / 100.0)
            qty = ra / sd
            notional = qty * sig["entry_price"]
            lev = min(int(notional / equity) + 1, MAX_LEVERAGE)
            lev = max(lev, 1)
            max_notional = equity * lev * 0.95
            if notional > max_notional:
                qty = max_notional / sig["entry_price"]
            if qty <= 0:
                signal_idx += 1
                continue

            fill_price = sig["entry_price"] * (1 + SLIP_BPS / 10000)
            fee = qty * fill_price * (TAKER_BPS / 10000)
            equity -= fee

            pos = {
                "trade_uuid": sig["trade_uuid"],
                "symbol": sym,
                "entry_price": fill_price,
                "orig_quantity": qty,
                "quantity": qty,
                "leverage": lev,
                "stop_price": sig["stop_price"],
                "init_stop": sig["stop_price"],
                "risk_per_unit": sd,
                "partial_taken": False,
                "best_price": fill_price,
                "vol_trail": 0.0,
                "struct_trail": 0.0,
                "mae": 0.0,
                "mfe": 0.0,
                "bars_held": 0,
                "rpnl": 0.0,
                "fees": fee,
                "btc_aligned": 1 if sig.get("btc_aligned") else 0,
                "confirmations": str(sig["confirmations"]),
                "confirm_count": sig["confirm_count"],
                "quality_score": quality_score,
                "is_ny": is_ny_session(sig["close_time"]),
                "pyramid_adds": 0,
                "sector": sym_to_sector.get(sym, "OTHER"),
                "entry_time": datetime.fromtimestamp(sig["close_time"] / 1000, tz=timezone.utc).isoformat(),
            }
            open_positions[sym] = pos
            atr_history[sym].append(sig.get("atr", 0))
            signal_idx += 1

    return closed_trades, equity


# ═══════════════════════════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════════════════════════

def compute_metrics(trades, label):
    if not trades:
        return {"label": label, "trades": 0}

    n = len(trades)
    wins = [t for t in trades if t["pnl_r"] > 0]
    losses = [t for t in trades if t["pnl_r"] <= 0]
    total_r = sum(t["pnl_r"] for t in trades)
    wr = len(wins) / n * 100

    gross_profit = sum(t["pnl_r"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["pnl_r"] for t in losses)) if losses else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    r_values = [t["pnl_r"] for t in trades]
    sharpe = (np.mean(r_values) / np.std(r_values) * math.sqrt(252 * 288)) if len(r_values) > 1 and np.std(r_values) > 0 else 0

    ny_trades = [t for t in trades if t.get("is_ny")]
    btc_al = [t for t in trades if t.get("btc_aligned")]

    return {
        "label": label,
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(wr, 1),
        "total_r": round(total_r, 4),
        "avg_r": round(total_r / n, 4),
        "profit_factor": round(pf, 3),
        "avg_win_r": round(sum(t["pnl_r"] for t in wins) / len(wins), 4) if wins else 0,
        "avg_loss_r": round(sum(t["pnl_r"] for t in losses) / len(losses), 4) if losses else 0,
        "sharpe": round(sharpe, 3),
        "ny_trades": len(ny_trades),
        "ny_wr": round(sum(1 for t in ny_trades if t["pnl_r"] > 0) / len(ny_trades) * 100, 1) if ny_trades else 0,
        "ny_total_r": round(sum(t["pnl_r"] for t in ny_trades), 4),
        "btc_aligned_trades": len(btc_al),
        "btc_aligned_wr": round(sum(1 for t in btc_al if t["pnl_r"] > 0) / len(btc_al) * 100, 1) if btc_al else 0,
        "btc_aligned_total_r": round(sum(t["pnl_r"] for t in btc_al), 4),
    }


def save_trades_csv(trades, filepath):
    if not trades:
        return
    fieldnames = list(trades[0].keys())
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in trades:
            writer.writerow(t)


def save_metrics_csv(all_metrics, filepath):
    rows = []
    for m in all_metrics:
        row = {k: v for k, v in m.items() if not isinstance(v, dict)}
        rows.append(row)
    if rows:
        fieldnames = list(rows[0].keys())
        with open(filepath, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    start_time = time.time()
    print("=" * 70)
    print("V3 LIQ-CLUSTER COMPREHENSIVE BACKTEST (Optimized)")
    print("Jan 1 – Apr 30, 2026 | All approved pairs")
    print("=" * 70)

    # Load all candle data
    print("\n[1/4] Loading candle data...")
    candles_data = {}
    for sym in [BTC_SYMBOL] + ALL_SYMBOLS:
        c = load_candles_from_db(sym)
        candles_data[sym] = c
        if c is not None:
            print(f"  {sym}: {c['n']} candles")
        else:
            print(f"  {sym}: NO DATA")

    btc_candles = candles_data[BTC_SYMBOL]

    # Build daily liq proxy and pre-compute signals
    print("\n[2/4] Pre-computing signals for all symbols...")
    all_signals_by_config = {}

    for layer_name, layer_config in [
        ("baseline", {"ret5d_min": -5.0}),
        ("relax_ret5d", {"ret5d_min": -10.0}),
    ]:
        CFG.ret5d_min = layer_config["ret5d_min"]
        signals = []
        for sym in ALL_SYMBOLS:
            c = candles_data[sym]
            if c is None:
                continue
            daily_rows = build_daily_liq_proxy(c)
            sym_signals, _ = precompute_signals(c, daily_rows, btc_candles)
            signals.extend(sym_signals)
            print(f"  {sym} ({layer_name}): {len(sym_signals)} signals")
        all_signals_by_config[layer_name] = signals
        print(f"  Total signals ({layer_name}): {len(signals)}")

    # Run backtest layers
    print("\n[3/4] Running backtest layers...")
    all_metrics = []

    layers = [
        ("baseline", {"ret5d_min": -5.0, "use_quality_scoring": False, "use_vol_targeting": False,
                       "use_regime_sizing": False, "use_pyramiding": False, "use_correlation_control": False}),
        ("relax_ret5d", {"ret5d_min": -10.0, "use_quality_scoring": False, "use_vol_targeting": False,
                          "use_regime_sizing": False, "use_pyramiding": False, "use_correlation_control": False}),
        ("restore_imbalance", {"ret5d_min": -10.0, "use_quality_scoring": False, "use_vol_targeting": False,
                                "use_regime_sizing": False, "use_pyramiding": False, "use_correlation_control": False}),
        ("quality_scoring", {"ret5d_min": -10.0, "use_quality_scoring": True, "use_vol_targeting": False,
                              "use_regime_sizing": False, "use_pyramiding": False, "use_correlation_control": False}),
        ("vol_targeting", {"ret5d_min": -10.0, "use_quality_scoring": True, "use_vol_targeting": True,
                            "use_regime_sizing": False, "use_pyramiding": False, "use_correlation_control": False}),
        ("regime_sizing", {"ret5d_min": -10.0, "use_quality_scoring": True, "use_vol_targeting": True,
                            "use_regime_sizing": True, "use_pyramiding": False, "use_correlation_control": False}),
        ("pyramiding", {"ret5d_min": -10.0, "use_quality_scoring": True, "use_vol_targeting": True,
                         "use_regime_sizing": True, "use_pyramiding": True, "use_correlation_control": False}),
        ("correlation_control", {"ret5d_min": -10.0, "use_quality_scoring": True, "use_vol_targeting": True,
                                  "use_regime_sizing": True, "use_pyramiding": True, "use_correlation_control": True}),
    ]

    for layer_name, layer_config in layers:
        print(f"\n--- {layer_name} ---")
        CFG.ret5d_min = layer_config["ret5d_min"]

        # Get signals for this ret5d config
        if layer_config["ret5d_min"] == -5.0:
            signals = all_signals_by_config.get("baseline", [])
        else:
            signals = all_signals_by_config.get("relax_ret5d", [])

        config = {"label": layer_name, **layer_config}
        trades, equity = replay_positions(signals, candles_data, btc_candles, config)
        metrics = compute_metrics(trades, layer_name)
        all_metrics.append(metrics)
        save_trades_csv(trades, OUTPUT_DIR / f"{layer_name}_trades.csv")

        elapsed = time.time() - start_time
        print(f"  {len(trades)} trades, WR={metrics.get('win_rate', 0)}%, "
              f"Total R={metrics.get('total_r', 0)}, PF={metrics.get('profit_factor', 0)}, "
              f"elapsed={elapsed:.0f}s")

    # Save comparison
    print("\n[4/4] Saving comparison report...")
    save_metrics_csv(all_metrics, OUTPUT_DIR / "comparison_report.csv")

    # Print summary
    print("\n" + "=" * 70)
    print("COMPARISON SUMMARY")
    print("=" * 70)
    print(f"{'Layer':<25} {'Trades':>6} {'WR%':>6} {'Total R':>10} {'PF':>6} {'Sharpe':>8}")
    print("-" * 70)
    for m in all_metrics:
        print(f"{m['label']:<25} {m.get('trades', 0):>6} {m.get('win_rate', 0):>6} "
              f"{m.get('total_r', 0):>10} {m.get('profit_factor', 0):>6} {m.get('sharpe', 0):>8}")

    elapsed = time.time() - start_time
    print(f"\nTotal time: {elapsed:.0f}s")
    print(f"Outputs in: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
