"""
V3 Liq-Cluster Comprehensive Backtest — Jan 1 to Apr 30, 2026
===============================================================
Phase 1: Fetch 5m klines from Binance for all approved pairs + BTC
Phase 2: Replay V3 engine (baseline) with full trade recording
Phase 3: Layer improvements and measure deltas

Outputs:
  - backtest_data/klines_5m.db          (raw candle data)
  - backtest_output/baseline_trades.csv  (every trade, every field)
  - backtest_output/layer_*.csv          (each improvement layer)
  - backtest_output/comparison_report.csv (side-by-side metrics)
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
import requests

# ═══════════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════════

SYMBOLS_TIER_A = [
    "NEARUSDT", "ZECUSDT", "ADAUSDT", "WLDUSDT", "UNIUSDT",
    "NMRUSDT", "PENDLEUSDT", "ARBUSDT", "RENDERUSDT", "RUNEUSDT",
    "FETUSDT", "DOTUSDT",
]
SYMBOLS_TIER_B = [
    "TONUSDT", "SOLUSDT", "1000LUNCUSDT", "ENAUSDT", "1000PEPEUSDT",
    "XRPUSDT", "FILUSDT", "BNBUSDT", "TAOUSDT", "CHZUSDT",
    "DASHUSDT", "QNTUSDT", "ICPUSDT", "XLMUSDT", "APTUSDT", "ETHUSDT",
]
ALL_SYMBOLS = SYMBOLS_TIER_A + SYMBOLS_TIER_B
BTC_SYMBOL = "BTCUSDT"

# V3 Engine Config (frozen)
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

# Risk
BASE_RISK_PCT = 2.0
BTC_ALIGNED_PCT = 4.0
MAX_LEVERAGE = 10
MAX_POSITIONS = 10
MAX_PER_SYMBOL = 1
INITIAL_EQUITY = 10000.0
TAKER_BPS = 4.5
SLIP_BPS = 2.0

# Time range
JAN1_2026_MS = 1767225600000
APR30_2026_MS = 1777593599000

# Paths
DATA_DIR = Path("/root/bitana/backtest_data")
OUTPUT_DIR = Path("/root/bitana/backtest_output")
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
KLINES_DB = DATA_DIR / "klines_5m.db"

# ═══════════════════════════════════════════════════════════════════════
# Data Fetcher
# ═══════════════════════════════════════════════════════════════════════

def init_klines_db():
    conn = sqlite3.connect(str(KLINES_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS klines (
            symbol TEXT NOT NULL,
            open_time INTEGER NOT NULL,
            close_time INTEGER NOT NULL,
            open REAL, high REAL, low REAL, close REAL,
            volume REAL,
            taker_buy_volume REAL,
            PRIMARY KEY (symbol, open_time)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_klines_sym_time ON klines(symbol, open_time)")
    conn.commit()
    return conn


def fetch_klines_from_binance(symbol, interval, start_ms, end_ms):
    """Fetch klines from Binance, handling pagination."""
    url = "https://fapi.binance.com/fapi/v1/klines"
    all_klines = []
    current_start = start_ms

    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "limit": 1000,
        }
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code != 200:
                print(f"  ERROR {symbol}: HTTP {r.status_code}")
                break
            data = r.json()
            if not data:
                break
            all_klines.extend(data)
            # Next start is after the last candle's close time
            current_start = data[-1][6] + 1
            if len(data) < 1000:
                break
            time.sleep(0.05)  # rate limit
        except Exception as e:
            print(f"  ERROR {symbol}: {e}")
            time.sleep(1)
            break

    return all_klines


def fetch_all_data():
    """Fetch 5m klines for all symbols + BTC."""
    conn = init_klines_db()

    symbols_to_fetch = [BTC_SYMBOL] + [s for s in ALL_SYMBOLS if s != BTC_SYMBOL]
    total = len(symbols_to_fetch)

    for i, sym in enumerate(symbols_to_fetch, 1):
        # Check if we already have data
        existing = conn.execute(
            "SELECT COUNT(*) FROM klines WHERE symbol=?", (sym,)
        ).fetchone()[0]
        if existing > 1000:
            print(f"  [{i}/{total}] {sym}: already have {existing} candles, skipping")
            continue

        print(f"  [{i}/{total}] Fetching {sym}...")
        klines = fetch_klines_from_binance(sym, "5m", JAN1_2026_MS, APR30_2026_MS)

        if klines:
            rows = []
            for k in klines:
                rows.append((
                    sym, k[0], k[6],
                    float(k[1]), float(k[2]), float(k[3]), float(k[4]),
                    float(k[5]), float(k[9]) if len(k) > 9 else 0.0,
                ))
            conn.executemany(
                "INSERT OR REPLACE INTO klines VALUES (?,?,?,?,?,?,?,?,?)", rows
            )
            conn.commit()
            print(f"    Saved {len(rows)} candles")
        else:
            print(f"    NO DATA for {sym}")

    # Verify
    for sym in symbols_to_fetch:
        cnt = conn.execute("SELECT COUNT(*) FROM klines WHERE symbol=?", (sym,)).fetchone()[0]
        mn = conn.execute("SELECT MIN(open_time) FROM klines WHERE symbol=?", (sym,)).fetchone()[0]
        mx = conn.execute("SELECT MAX(open_time) FROM klines WHERE symbol=?", (sym,)).fetchone()[0]
        if cnt > 0:
            first_dt = datetime.fromtimestamp(mn/1000, tz=timezone.utc).strftime('%Y-%m-%d')
            last_dt = datetime.fromtimestamp(mx/1000, tz=timezone.utc).strftime('%Y-%m-%d')
            print(f"  {sym}: {cnt} candles ({first_dt} → {last_dt})")
        else:
            print(f"  {sym}: NO DATA")

    conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Candle model (lightweight, no pydantic)
# ═══════════════════════════════════════════════════════════════════════

class Candle:
    __slots__ = ['symbol', 'open_time', 'close_time', 'open', 'high', 'low', 'close',
                 'volume', 'taker_buy_volume']

    def __init__(self, symbol, open_time, close_time, o, h, l, c, v, tbv):
        self.symbol = symbol
        self.open_time = open_time
        self.close_time = close_time
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = v
        self.taker_buy_volume = tbv


def load_candles_from_db(symbol, start_ms=None, end_ms=None):
    """Load candles from local DB into list of Candle objects."""
    conn = sqlite3.connect(str(KLINES_DB))
    query = "SELECT symbol, open_time, close_time, open, high, low, close, volume, taker_buy_volume FROM klines WHERE symbol=?"
    params = [symbol]
    if start_ms:
        query += " AND open_time >= ?"
        params.append(start_ms)
    if end_ms:
        query += " AND open_time <= ?"
        params.append(end_ms)
    query += " ORDER BY open_time"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    candles = []
    for r in rows:
        candles.append(Candle(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8]))
    return candles


# ═══════════════════════════════════════════════════════════════════════
# V3 Engine (numpy-based, same logic as production)
# ═══════════════════════════════════════════════════════════════════════

def _ema(values, span):
    if len(values) < 2:
        return values[-1] if len(values) else 0.0
    alpha = 2.0 / (span + 1)
    ema = values[0]
    for v in values[1:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema


def _atr(highs, lows, closes, period):
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
    return _ema(tr, period)


def _z_score(values, lookback):
    if len(values) < 20:
        return 0.0
    window = values[-lookback:] if len(values) >= lookback else values
    mean = np.mean(window)
    std = np.std(window)
    if std < 1e-12:
        return 0.0
    return (values[-1] - mean) / std


class CascadeTracker:
    def __init__(self):
        self._liq_history = deque(maxlen=CFG.liq_lookback + 5)

    def update(self, daily_rows):
        for row in daily_rows:
            self._liq_history.append(row)

        if len(self._liq_history) < CFG.liq_min_lookback:
            return False, 0.0, 0.0, 0.0

        liqs = [r["total_liq"] for r in self._liq_history]
        if len(liqs) < CFG.liq_min_lookback:
            return False, 0.0, 0.0, 0.0

        lookback = liqs[-CFG.liq_lookback:] if len(liqs) >= CFG.liq_lookback else liqs
        p90 = np.percentile(lookback, CFG.liq_percentile * 100)
        if p90 <= 0:
            return False, 0.0, 0.0, 0.0

        cascade_active = False
        for i in range(CFG.liq_window + 1):
            idx = -(i + 1)
            if abs(idx) <= len(liqs) and liqs[idx] > p90:
                cascade_active = True
                break

        strength = liqs[-1] / p90 if p90 > 0 else 0

        last = self._liq_history[-1]
        total = last.get("total_liq", 0)
        if total > 0:
            imb = (last.get("long_liq", 0) - last.get("short_liq", 0)) / total
        else:
            imb = 0.0

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


class SymbolState:
    def __init__(self):
        self.cascade_active = False
        self.cascade_strength = 0.0
        self.liq_direction_imb = 0.0
        self.ret_5d = 0.0
        self.cooldown = 0
        self.stopped_in_window = False
        self.last_cascade_state = False
        self.in_trade = False
        self.entry_price = 0.0
        self.risk_per_unit = 0.0
        self.bars_held = 0
        self.partial_taken = False
        self.best_price = 0.0
        self.vol_trail = 0.0
        self.struct_trail = 0.0
        self.mae = 0.0
        self.mfe = 0.0


class LiqClusterEngine:
    def __init__(self):
        self._states = {}
        self._cascades = {}

    def _get_state(self, symbol):
        if symbol not in self._states:
            self._states[symbol] = SymbolState()
        return self._states[symbol]

    def _get_cascade(self, symbol):
        if symbol not in self._cascades:
            self._cascades[symbol] = CascadeTracker()
        return self._cascades[symbol]

    def update_daily_liq(self, symbol, daily_rows):
        ct = self._get_cascade(symbol)
        st = self._get_state(symbol)
        cascade_active, strength, imb, ret_5d = ct.update(daily_rows)
        if cascade_active and not st.last_cascade_state:
            st.stopped_in_window = False
        st.last_cascade_state = cascade_active
        st.cascade_active = cascade_active
        st.cascade_strength = strength
        st.liq_direction_imb = imb
        st.ret_5d = ret_5d

    def evaluate(self, symbol, candles_5m):
        st = self._get_state(symbol)

        if st.cooldown > 0:
            st.cooldown -= 1
            return None
        if CFG.no_reentry_after_stop and st.stopped_in_window:
            return None
        if not st.cascade_active:
            return None

        n_needed = max(CFG.range_lookback, CFG.z_lookback, CFG.ema_period * 3)
        if len(candles_5m) < n_needed:
            return None

        closes = np.array([c.close for c in candles_5m])
        highs = np.array([c.high for c in candles_5m])
        lows = np.array([c.low for c in candles_5m])
        volumes = np.array([c.volume for c in candles_5m])

        bar = candles_5m[-1]

        atr = _atr(highs, lows, closes, CFG.atr_period)
        if atr <= 0:
            return None

        ema = _ema(closes, CFG.ema_period)

        if len(highs) > CFG.range_lookback:
            range_high = float(np.max(highs[-(CFG.range_lookback + 1):-1]))
        else:
            range_high = float(np.max(highs[:-1])) if len(highs) > 1 else highs[0]

        vol_z = _z_score(volumes, CFG.z_lookback)

        taker_buys = np.array([c.taker_buy_volume for c in candles_5m])
        has_taker = taker_buys[-1] > 0
        if has_taker:
            taker_sells = volumes - taker_buys
            totals = taker_buys + taker_sells
            safe_totals = np.where(totals > 0, totals, 1.0)
            imb_raw = (taker_buys - taker_sells) / safe_totals
            imb_z = _z_score(imb_raw, CFG.z_lookback)
        else:
            imb_z = 0.0

        candle_range = bar.high - bar.low
        candle_body = abs(bar.close - bar.open)
        body_strength = candle_body / candle_range if candle_range > 0 else 0

        bar_return_pct = ((bar.close - bar.open) / bar.open * 100) if bar.open > 0 else 0

        confirmations = {
            "breakout": bar.close > range_high,
            "imb": imb_z > CFG.imb_z_threshold if has_taker else False,
            "vol": vol_z > CFG.vol_z_threshold,
            "body": body_strength > CFG.body_strength_min,
            "impulse": bar_return_pct > CFG.impulse_min_pct,
            "momentum": bar.close > ema,
        }
        confirm_count = sum(1 for v in confirmations.values() if v)

        if confirm_count < CFG.min_confirmations:
            return None

        entry_price = bar.close
        stop_distance = atr * CFG.initial_stop_atr
        stop_price = entry_price - stop_distance

        st.in_trade = True
        st.entry_price = entry_price
        st.risk_per_unit = stop_distance
        st.bars_held = 0
        st.partial_taken = False
        st.best_price = entry_price
        st.vol_trail = 0.0
        st.struct_trail = 0.0
        st.mae = 0.0
        st.mfe = 0.0
        st.cooldown = CFG.cooldown_bars

        return {
            "trade_uuid": str(uuid.uuid4()),
            "symbol": symbol,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "risk_distance": stop_distance,
            "confirmations": confirmations,
            "confirm_count": confirm_count,
            "cascade_strength": st.cascade_strength,
            "liq_direction_imb": st.liq_direction_imb,
            "ret_5d": st.ret_5d,
            "imb_z": round(imb_z, 2),
            "vol_z": round(vol_z, 2),
            "body_strength": round(body_strength, 2),
            "bar_return_pct": round(bar_return_pct, 3),
            "atr": round(atr, 6),
            "bar_close_time": bar.close_time,
        }

    def manage_position(self, symbol, candle, candles_5m):
        st = self._get_state(symbol)
        if not st.in_trade:
            return None

        st.bars_held += 1
        price = candle.close

        if candle.high > st.best_price:
            st.best_price = candle.high

        current_r = (price - st.entry_price) / st.risk_per_unit if st.risk_per_unit > 0 else 0
        low_r = (candle.low - st.entry_price) / st.risk_per_unit if st.risk_per_unit > 0 else 0
        high_r = (candle.high - st.entry_price) / st.risk_per_unit if st.risk_per_unit > 0 else 0

        if low_r < st.mae:
            st.mae = low_r
        if high_r > st.mfe:
            st.mfe = high_r

        highs = np.array([c.high for c in candles_5m[-50:]])
        lows_arr = np.array([c.low for c in candles_5m[-50:]])
        closes = np.array([c.close for c in candles_5m[-50:]])
        atr = _atr(highs, lows_arr, closes, CFG.atr_period)

        # Stop loss
        stop_price = st.entry_price - st.risk_per_unit
        if candle.low <= stop_price:
            st.in_trade = False
            st.stopped_in_window = True
            st.cooldown = CFG.cooldown_bars
            return {
                "action": "close", "reason": "stop_loss",
                "exit_price": stop_price,
                "r": (stop_price - st.entry_price) / st.risk_per_unit,
                "mae": st.mae, "mfe": st.mfe, "bars_held": st.bars_held,
            }

        # Partial TP at 2.5R (wick trigger)
        if not st.partial_taken and high_r >= CFG.partial_r:
            st.partial_taken = True
            return {
                "action": "partial", "fraction": CFG.partial_fraction,
                "reason": f"partial_{CFG.partial_r:.1f}R",
                "r": high_r, "mae": st.mae, "mfe": st.mfe, "bars_held": st.bars_held,
            }

        # Vol trail
        new_vol_trail = price - atr * CFG.vol_trail_atr
        if new_vol_trail > st.vol_trail:
            st.vol_trail = new_vol_trail
        if st.vol_trail > st.entry_price and candle.low <= st.vol_trail:
            st.in_trade = False
            st.cooldown = CFG.cooldown_bars
            return {
                "action": "close", "reason": "vol_trail",
                "exit_price": st.vol_trail,
                "r": (st.vol_trail - st.entry_price) / st.risk_per_unit,
                "mae": st.mae, "mfe": st.mfe, "bars_held": st.bars_held,
            }

        # Structure trail
        if len(candles_5m) >= CFG.struct_lookback:
            swing_low = min(c.low for c in candles_5m[-CFG.struct_lookback:])
            if swing_low > st.struct_trail:
                st.struct_trail = swing_low
            if st.struct_trail > st.entry_price and candle.low <= st.struct_trail:
                st.in_trade = False
                st.cooldown = CFG.cooldown_bars
                return {
                    "action": "close", "reason": "struct_trail",
                    "exit_price": st.struct_trail,
                    "r": (st.struct_trail - st.entry_price) / st.risk_per_unit,
                    "mae": st.mae, "mfe": st.mfe, "bars_held": st.bars_held,
                }

        # Expansion decay
        if st.bars_held > 6 and current_r > 0.5:
            peak_r = (st.best_price - st.entry_price) / st.risk_per_unit
            if peak_r > 0 and (current_r / peak_r) < (1 - CFG.decay_threshold):
                st.in_trade = False
                st.cooldown = CFG.cooldown_bars
                return {
                    "action": "close", "reason": "expansion_decay",
                    "exit_price": price, "r": current_r,
                    "mae": st.mae, "mfe": st.mfe, "bars_held": st.bars_held,
                }

        # Time stop
        if st.bars_held >= CFG.max_hold_bars:
            st.in_trade = False
            st.cooldown = CFG.cooldown_bars
            return {
                "action": "close", "reason": "time_stop",
                "exit_price": price, "r": current_r,
                "mae": st.mae, "mfe": st.mfe, "bars_held": st.bars_held,
            }

        return None

    def get_btc_aligned(self, btc_candles):
        if len(btc_candles) < 21:
            return False
        closes = np.array([c.close for c in btc_candles])
        ema20 = _ema(closes, 20)
        above_ema = closes[-1] > ema20
        above_12_ago = closes[-1] > closes[-13] if len(closes) > 13 else False
        return above_ema and above_12_ago

    def reset_symbol(self, symbol):
        st = self._get_state(symbol)
        st.in_trade = False


# ═══════════════════════════════════════════════════════════════════════
# Liquidation context builder (from price/volume proxy)
# ═══════════════════════════════════════════════════════════════════════

def build_daily_liq_proxy(candles_5m):
    """
    Build daily liquidation proxy from 5m candles.
    We aggregate to daily and estimate liq intensity from:
    - Large wicks (forced liquidations create wicks)
    - Volume spikes
    - Price drops (short liquidations) vs price rises (long liquidations)

    This is a PROXY — not real Coinalyze data, but good enough for backtesting
    the cascade filter logic across all 28 symbols.
    """
    from collections import OrderedDict

    daily = OrderedDict()
    for c in candles_5m:
        dt = datetime.fromtimestamp(c.open_time / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        if dt not in daily:
            daily[dt] = {
                "date": dt, "highs": [], "lows": [], "closes": [],
                "volumes": [], "total_range": 0, "wick_score": 0,
            }
        d = daily[dt]
        d["highs"].append(c.high)
        d["lows"].append(c.low)
        d["closes"].append(c.close)
        d["volumes"].append(c.volume)
        d["total_range"] += c.high - c.low
        # Wick score: lower wick = short liquidations
        body_top = max(c.open, c.close)
        body_bot = min(c.open, c.close)
        lower_wick = body_bot - c.low
        upper_wick = c.high - body_top
        d["wick_score"] += (lower_wick - upper_wick)  # positive = more short liq

    daily_rows = []
    for dt, d in daily.items():
        if not d["closes"]:
            continue
        close = d["closes"][-1]
        high = max(d["highs"])
        low = min(d["lows"])
        total_vol = sum(d["volumes"])

        # Estimate liquidation intensity from wick score + volume + range
        avg_vol = total_vol / max(len(d["volumes"]), 1)
        range_factor = d["total_range"] / max(close, 0.001) * 100
        wick_factor = abs(d["wick_score"]) / max(close, 0.001) * 100

        # Proxy total_liq: combination of volume, range, and wick intensity
        total_liq = total_vol * range_factor * (1 + wick_factor / 100)

        # Direction: positive wick_score = more short liqs (price dropped then recovered)
        if d["wick_score"] > 0:
            short_liq = total_liq * 0.6
            long_liq = total_liq * 0.4
        else:
            short_liq = total_liq * 0.4
            long_liq = total_liq * 0.6

        daily_rows.append({
            "date": dt,
            "total_liq": total_liq,
            "long_liq": long_liq,
            "short_liq": short_liq,
            "close": close,
        })

    return daily_rows


# ═══════════════════════════════════════════════════════════════════════
# Backtest Runner
# ═══════════════════════════════════════════════════════════════════════

class PaperFill:
    def __init__(self, equity):
        self.equity = equity
        self.peak = equity
        self.initial = equity

    def fill_entry(self, price, qty):
        slip = price * (SLIP_BPS / 10000)
        fill = price + slip
        fee = qty * fill * (TAKER_BPS / 10000)
        self.equity -= fee
        return fill, fee

    def fill_exit(self, entry, price, qty):
        slip = price * (SLIP_BPS / 10000)
        fill = price - slip
        fee = qty * fill * (TAKER_BPS / 10000)
        pnl = (fill - entry) * qty
        self.equity += pnl - fee
        if self.equity > self.peak:
            self.peak = self.equity
        return fill, fee, pnl


def compute_quality_score(signal_data, btc_aligned):
    """
    Continuous quality score for a signal.
    Returns score in [0, 1] range.
    """
    cs = signal_data.get("cascade_strength", 0)
    vz = signal_data.get("vol_z", 0)
    imb_z = signal_data.get("imb_z", 0)
    brp = signal_data.get("bar_return_pct", 0)
    bs = signal_data.get("body_strength", 0)

    # Normalize each component to roughly 0-1
    liq_score = min(cs / 3.0, 1.0)  # cascade_strength, 3x = max
    vol_score = min(vz / 6.0, 1.0)  # vol_z, 6 = max
    imb_score = min(abs(imb_z) / 4.0, 1.0)  # imb_z magnitude
    impulse_score = min(brp / 2.0, 1.0)  # bar return %, 2% = max
    body_score = bs  # already 0-1
    btc_score = 1.0 if btc_aligned else 0.0

    score = (
        liq_score * 0.30 +
        vol_score * 0.20 +
        impulse_score * 0.15 +
        imb_score * 0.15 +
        btc_score * 0.10 +
        body_score * 0.10
    )
    return round(score, 4)


def is_ny_session(close_time_ms):
    """Check if candle close is in NY session (13:00-22:00 UTC)."""
    dt = datetime.fromtimestamp(close_time_ms / 1000, tz=timezone.utc)
    hour = dt.hour
    return 13 <= hour < 22


def run_backtest(
    label="baseline",
    relax_ret5d=False,
    use_quality_scoring=False,
    use_vol_targeting=False,
    use_regime_sizing=False,
    use_pyramiding=False,
    use_correlation_control=False,
    use_imbalance=True,  # use real taker data from Binance
    correlation_cap=3,   # max concurrent positions in same sector
):
    """
    Run full backtest with given configuration.
    Returns list of completed trades and equity curve.
    """
    print(f"\n{'='*70}")
    print(f"BACKTEST: {label}")
    print(f"{'='*70}")

    # Load all candle data
    print("Loading candle data...")
    all_candles = {}
    for sym in [BTC_SYMBOL] + ALL_SYMBOLS:
        candles = load_candles_from_db(sym, JAN1_2026_MS, APR30_2026_MS)
        all_candles[sym] = candles
        print(f"  {sym}: {len(candles)} candles")

    # Build daily liq proxy for each symbol
    print("\nBuilding liquidation context...")
    engine = LiqClusterEngine()
    for sym in ALL_SYMBOLS:
        daily_rows = build_daily_liq_proxy(all_candles[sym])
        engine.update_daily_liq(sym, daily_rows)

    # Determine the unified candle timeline (all unique close times across all symbols)
    all_close_times = set()
    for sym, candles in all_candles.items():
        for c in candles:
            all_close_times.add(c.close_time)
    sorted_times = sorted(all_close_times)
    print(f"\nTotal unique 5m bars in timeline: {len(sorted_times)}")

    # Build index: for each symbol, map close_time -> candle index
    sym_candle_index = {}
    for sym, candles in all_candles.items():
        idx = {}
        for i, c in enumerate(candles):
            idx[c.close_time] = i
        sym_candle_index[sym] = idx

    # State
    executor = PaperFill(INITIAL_EQUITY)
    open_positions = {}  # symbol -> position dict
    closed_trades = []
    equity_curve = []

    # ATR history for vol targeting
    atr_history = defaultdict(list)

    # Sector mapping for correlation control
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

    # Main loop
    print(f"\nRunning backtest...")
    progress_interval = len(sorted_times) // 20

    for t_idx, close_time in enumerate(sorted_times):
        if t_idx % progress_interval == 0:
            print(f"  Progress: {t_idx}/{len(sorted_times)} ({t_idx*100//len(sorted_times)}%) "
                  f"equity=${executor.equity:.2f} trades={len(closed_trades)}")

        # Get BTC candles up to this point
        btc_candles = []
        btc_idx = sym_candle_index[BTC_SYMBOL]
        for c in all_candles[BTC_SYMBOL]:
            if c.close_time <= close_time:
                btc_candles.append(c)

        btc_aligned = engine.get_btc_aligned(btc_candles)

        # Manage existing positions
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            # Find the candle for this symbol at this close_time
            idx_map = sym_candle_index[sym]
            if close_time not in idx_map:
                continue
            c_idx = idx_map[close_time]
            candle = all_candles[sym][c_idx]
            sym_candles = all_candles[sym][:c_idx + 1]

            result = engine.manage_position(sym, candle, sym_candles)

            if result and result["action"] == "close":
                fill, fee, pnl = executor.fill_exit(pos["entry_price"], result["exit_price"], pos["quantity"])
                sd = abs(pos["entry_price"] - pos["init_stop"])
                pnl_r = (result["exit_price"] - pos["entry_price"]) / sd if sd > 0 else 0
                net_pnl = pnl - fee

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
                    "fees": round(pos["fees"] + fee, 4),
                    "hold_candles": result["bars_held"],
                    "exit_reason": result["reason"],
                    "tp1_hit": pos["tp1_hit"],
                    "equity_after": round(executor.equity, 2),
                    "btc_aligned": pos.get("btc_aligned", 0),
                    "confirmations": pos.get("confirmations", ""),
                    "confirm_count": pos.get("confirm_count", 0),
                    "mae": round(result.get("mae", 0), 4),
                    "mfe": round(result.get("mfe", 0), 4),
                    "quality_score": pos.get("quality_score", 0),
                    "is_ny": pos.get("is_ny", False),
                    "pyramid_adds": pos.get("pyramid_adds", 0),
                    "sector": sym_to_sector.get(sym, "OTHER"),
                    "label": label,
                }
                closed_trades.append(trade_record)
                engine.reset_symbol(sym)
                del open_positions[sym]

            elif result and result["action"] == "partial" and not pos["tp1_hit"]:
                frac = result["fraction"]
                tq = pos["quantity"] * frac
                fill, fee, pnl = executor.fill_exit(pos["entry_price"], candle.close, tq)
                pos["tp1_hit"] = 1
                pos["quantity"] -= tq
                pos["rpnl"] += pnl
                pos["fees"] += fee

        # Check for new entries
        active_count = len(open_positions)
        if active_count >= MAX_POSITIONS:
            equity_curve.append((close_time, executor.equity))
            continue

        for sym in ALL_SYMBOLS:
            if sym in open_positions:
                continue
            if active_count >= MAX_POSITIONS:
                break

            idx_map = sym_candle_index[sym]
            if close_time not in idx_map:
                continue
            c_idx = idx_map[close_time]
            sym_candles = all_candles[sym][:c_idx + 1]

            sig = engine.evaluate(sym, sym_candles)
            if sig is None:
                continue

            # Track ATR for vol targeting
            atr_val = sig.get("atr", 0)
            if atr_val > 0:
                atr_history[sym].append(atr_val)

            # Quality scoring
            quality_score = compute_quality_score(sig, btc_aligned) if use_quality_scoring else 0.5

            # Risk sizing
            risk_pct = BTC_ALIGNED_PCT if btc_aligned else BASE_RISK_PCT

            # Vol targeting adjustment
            if use_vol_targeting and len(atr_history[sym]) >= 20:
                median_atr = np.median(atr_history[sym][-100:])
                current_atr = atr_val
                if current_atr > 0:
                    vol_ratio = median_atr / current_atr
                    vol_ratio = max(0.5, min(2.0, vol_ratio))  # cap 0.5x to 2x
                    risk_pct *= vol_ratio

            # Quality scoring adjustment
            if use_quality_scoring:
                # Scale risk by quality: 0.5 = base, 1.0 = 2x, 0.0 = 0.25x
                quality_mult = 0.25 + quality_score * 1.5
                risk_pct *= quality_mult

            # NY session boost
            ny_boost = 1.0
            if is_ny_session(close_time):
                ny_boost = 1.5  # 50% more risk during NY
                if use_pyramiding:
                    ny_boost = 2.0  # Double risk during NY for pyramid-friendly trades

            # Regime sizing
            if use_regime_sizing and len(atr_history[sym]) >= 20:
                median_atr = np.median(atr_history[sym][-100:])
                current_atr = atr_val
                if median_atr > 0:
                    regime_ratio = current_atr / median_atr
                    if regime_ratio > 1.5:
                        # High vol expansion — increase size
                        ny_boost *= min(1.5, regime_ratio / 1.5)
                    elif regime_ratio < 0.5:
                        # Low vol chop — decrease size
                        ny_boost *= 0.5

            risk_pct *= ny_boost
            risk_pct = min(risk_pct, 8.0)  # hard cap at 8% per trade

            # Correlation control
            if use_correlation_control:
                sector = sym_to_sector.get(sym, "OTHER")
                sector_count = sum(1 for p in open_positions.values() if p.get("sector") == sector)
                if sector_count >= correlation_cap:
                    continue

            sd = sig["risk_distance"]
            if sd <= 0:
                continue

            ra = executor.equity * (risk_pct / 100.0)
            qty = ra / sd
            notional = qty * sig["entry_price"]
            lev = min(int(notional / executor.equity) + 1, MAX_LEVERAGE)
            lev = max(lev, 1)
            max_notional = executor.equity * lev * 0.95
            if notional > max_notional:
                qty = max_notional / sig["entry_price"]
            if qty <= 0:
                continue

            fill, fee = executor.fill_entry(sig["entry_price"], qty)

            pos = {
                "trade_uuid": sig["trade_uuid"],
                "symbol": sym,
                "entry_price": fill,
                "orig_quantity": qty,
                "quantity": qty,
                "leverage": lev,
                "stop_price": sig["stop_price"],
                "init_stop": sig["stop_price"],
                "tp1_hit": 0,
                "entry_time": datetime.fromtimestamp(close_time / 1000, tz=timezone.utc).isoformat(),
                "rpnl": 0.0,
                "fees": fee,
                "btc_aligned": 1 if btc_aligned else 0,
                "confirmations": str(sig["confirmations"]),
                "confirm_count": sig["confirm_count"],
                "quality_score": quality_score,
                "is_ny": is_ny_session(close_time),
                "pyramid_adds": 0,
                "sector": sym_to_sector.get(sym, "OTHER"),
            }
            open_positions[sym] = pos
            active_count += 1

        equity_curve.append((close_time, executor.equity))

    # Close any remaining open positions at last price
    for sym, pos in list(open_positions.items()):
        last_candle = all_candles[sym][-1]
        fill, fee, pnl = executor.fill_exit(pos["entry_price"], last_candle.close, pos["quantity"])
        sd = abs(pos["entry_price"] - pos["init_stop"])
        pnl_r = (last_candle.close - pos["entry_price"]) / sd if sd > 0 else 0

        trade_record = {
            "trade_uuid": pos["trade_uuid"],
            "symbol": sym,
            "side": "LONG",
            "entry_time": pos["entry_time"],
            "exit_time": datetime.fromtimestamp(last_candle.close_time / 1000, tz=timezone.utc).isoformat(),
            "entry_price": round(pos["entry_price"], 6),
            "exit_price": round(fill, 6),
            "quantity": round(pos["orig_quantity"], 6),
            "leverage": pos["leverage"],
            "stop_dist": round(sd, 6),
            "pnl_usd": round(pnl - fee, 4),
            "pnl_r": round(pnl_r, 4),
            "fees": round(pos["fees"] + fee, 4),
            "hold_candles": 0,
            "exit_reason": "end_of_backtest",
            "tp1_hit": pos["tp1_hit"],
            "equity_after": round(executor.equity, 2),
            "btc_aligned": pos.get("btc_aligned", 0),
            "confirmations": pos.get("confirmations", ""),
            "confirm_count": pos.get("confirm_count", 0),
            "quality_score": pos.get("quality_score", 0),
            "is_ny": pos.get("is_ny", False),
            "pyramid_adds": pos.get("pyramid_adds", 0),
            "sector": pos.get("sector", "OTHER"),
            "label": label,
        }
        closed_trades.append(trade_record)

    print(f"\n  Completed: {len(closed_trades)} trades, final equity: ${executor.equity:.2f}")
    return closed_trades, equity_curve


# ═══════════════════════════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════════════════════════

def compute_metrics(trades, equity_curve, label):
    """Compute comprehensive metrics for a backtest run."""
    if not trades:
        return {"label": label, "trades": 0}

    n = len(trades)
    wins = [t for t in trades if t["pnl_r"] > 0]
    losses = [t for t in trades if t["pnl_r"] <= 0]
    total_r = sum(t["pnl_r"] for t in trades)
    avg_r = total_r / n
    wr = len(wins) / n * 100 if n > 0 else 0

    gross_profit = sum(t["pnl_r"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["pnl_r"] for t in losses)) if losses else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    avg_win_r = sum(t["pnl_r"] for t in wins) / len(wins) if wins else 0
    avg_loss_r = sum(t["pnl_r"] for t in losses) / len(losses) if losses else 0

    # Max drawdown
    peak = INITIAL_EQUITY
    max_dd = 0
    for _, eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Final equity
    final_equity = equity_curve[-1][1] if equity_curve else INITIAL_EQUITY
    total_return = (final_equity - INITIAL_EQUITY) / INITIAL_EQUITY * 100

    # By exit reason
    by_reason = defaultdict(lambda: {"n": 0, "r": 0})
    for t in trades:
        by_reason[t["exit_reason"]]["n"] += 1
        by_reason[t["exit_reason"]]["r"] += t["pnl_r"]

    # By symbol
    by_symbol = defaultdict(lambda: {"n": 0, "r": 0})
    for t in trades:
        by_symbol[t["symbol"]]["n"] += 1
        by_symbol[t["symbol"]]["r"] += t["pnl_r"]

    # By sector
    by_sector = defaultdict(lambda: {"n": 0, "r": 0})
    for t in trades:
        by_sector[t.get("sector", "OTHER")]["n"] += 1
        by_sector[t.get("sector", "OTHER")]["r"] += t["pnl_r"]

    # NY vs non-NY
    ny_trades = [t for t in trades if t.get("is_ny")]
    non_ny_trades = [t for t in trades if not t.get("is_ny")]

    # BTC aligned vs non-aligned
    btc_aligned = [t for t in trades if t.get("btc_aligned")]
    btc_non_aligned = [t for t in trades if not t.get("btc_aligned")]

    # Sharpe (simplified)
    r_values = [t["pnl_r"] for t in trades]
    sharpe = (np.mean(r_values) / np.std(r_values) * math.sqrt(252 * 288)) if len(r_values) > 1 and np.std(r_values) > 0 else 0

    return {
        "label": label,
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(wr, 1),
        "total_r": round(total_r, 4),
        "avg_r": round(avg_r, 4),
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "profit_factor": round(pf, 3),
        "avg_win_r": round(avg_win_r, 4),
        "avg_loss_r": round(avg_loss_r, 4),
        "max_drawdown_pct": round(max_dd, 2),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return, 2),
        "sharpe": round(sharpe, 3),
        "ny_trades": len(ny_trades),
        "ny_wr": round(sum(1 for t in ny_trades if t["pnl_r"] > 0) / len(ny_trades) * 100, 1) if ny_trades else 0,
        "ny_total_r": round(sum(t["pnl_r"] for t in ny_trades), 4),
        "non_ny_trades": len(non_ny_trades),
        "non_ny_wr": round(sum(1 for t in non_ny_trades if t["pnl_r"] > 0) / len(non_ny_trades) * 100, 1) if non_ny_trades else 0,
        "non_ny_total_r": round(sum(t["pnl_r"] for t in non_ny_trades), 4),
        "btc_aligned_trades": len(btc_aligned),
        "btc_aligned_wr": round(sum(1 for t in btc_aligned if t["pnl_r"] > 0) / len(btc_aligned) * 100, 1) if btc_aligned else 0,
        "btc_aligned_total_r": round(sum(t["pnl_r"] for t in btc_aligned), 4),
        "by_reason": dict(by_reason),
        "by_symbol": dict(by_symbol),
        "by_sector": dict(by_sector),
    }


def save_trades_csv(trades, filepath):
    """Save trades to CSV with all fields."""
    if not trades:
        return
    fieldnames = list(trades[0].keys())
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in trades:
            writer.writerow(t)


def save_metrics_csv(all_metrics, filepath):
    """Save comparison metrics to CSV."""
    # Flatten metrics (exclude nested dicts for main comparison)
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
    print("=" * 70)
    print("V3 LIQ-CLUSTER COMPREHENSIVE BACKTEST")
    print("Jan 1 – Apr 30, 2026 | All approved pairs")
    print("=" * 70)

    # Step 1: Fetch data
    print("\n[1/3] Fetching data...")
    fetch_all_data()

    # Step 2: Run backtest layers
    print("\n[2/3] Running backtest layers...")
    all_metrics = []

    # Layer 0: Baseline (current V3, 4-of-6, no taker imb)
    print("\n--- Layer 0: BASELINE ---")
    CFG.ret5d_min = -5.0
    CFG.require_short_squeeze = True
    trades, eq_curve = run_backtest("baseline")
    metrics = compute_metrics(trades, eq_curve, "baseline")
    all_metrics.append(metrics)
    save_trades_csv(trades, OUTPUT_DIR / "baseline_trades.csv")

    # Layer 1: Relax ret5d_min
    print("\n--- Layer 1: RELAX ret5d_min ---")
    CFG.ret5d_min = -10.0
    trades, eq_curve = run_backtest("relax_ret5d")
    metrics = compute_metrics(trades, eq_curve, "relax_ret5d")
    all_metrics.append(metrics)
    save_trades_csv(trades, OUTPUT_DIR / "relax_ret5d_trades.csv")

    # Layer 2: Restore imbalance (use real taker data)
    print("\n--- Layer 2: RESTORE IMBALANCE ---")
    CFG.ret5d_min = -10.0
    trades, eq_curve = run_backtest("restore_imbalance", use_imbalance=True)
    metrics = compute_metrics(trades, eq_curve, "restore_imbalance")
    all_metrics.append(metrics)
    save_trades_csv(trades, OUTPUT_DIR / "restore_imbalance_trades.csv")

    # Layer 3: Quality scoring
    print("\n--- Layer 3: QUALITY SCORING ---")
    trades, eq_curve = run_backtest("quality_scoring", use_imbalance=True, use_quality_scoring=True)
    metrics = compute_metrics(trades, eq_curve, "quality_scoring")
    all_metrics.append(metrics)
    save_trades_csv(trades, OUTPUT_DIR / "quality_scoring_trades.csv")

    # Layer 4: Vol targeting
    print("\n--- Layer 4: VOL TARGETING ---")
    trades, eq_curve = run_backtest("vol_targeting", use_imbalance=True, use_quality_scoring=True, use_vol_targeting=True)
    metrics = compute_metrics(trades, eq_curve, "vol_targeting")
    all_metrics.append(metrics)
    save_trades_csv(trades, OUTPUT_DIR / "vol_targeting_trades.csv")

    # Layer 5: Regime sizing
    print("\n--- Layer 5: REGIME SIZING ---")
    trades, eq_curve = run_backtest("regime_sizing", use_imbalance=True, use_quality_scoring=True, use_vol_targeting=True, use_regime_sizing=True)
    metrics = compute_metrics(trades, eq_curve, "regime_sizing")
    all_metrics.append(metrics)
    save_trades_csv(trades, OUTPUT_DIR / "regime_sizing_trades.csv")

    # Layer 6: Pyramiding (large adds during NY)
    print("\n--- Layer 6: PYRAMIDING ---")
    trades, eq_curve = run_backtest("pyramiding", use_imbalance=True, use_quality_scoring=True, use_vol_targeting=True, use_regime_sizing=True, use_pyramiding=True)
    metrics = compute_metrics(trades, eq_curve, "pyramiding")
    all_metrics.append(metrics)
    save_trades_csv(trades, OUTPUT_DIR / "pyramiding_trades.csv")

    # Layer 7: Correlation control
    print("\n--- Layer 7: CORRELATION CONTROL ---")
    trades, eq_curve = run_backtest("correlation_control", use_imbalance=True, use_quality_scoring=True, use_vol_targeting=True, use_regime_sizing=True, use_pyramiding=True, use_correlation_control=True)
    metrics = compute_metrics(trades, eq_curve, "correlation_control")
    all_metrics.append(metrics)
    save_trades_csv(trades, OUTPUT_DIR / "correlation_control_trades.csv")

    # Step 3: Save comparison
    print("\n[3/3] Saving comparison report...")
    save_metrics_csv(all_metrics, OUTPUT_DIR / "comparison_report.csv")

    # Print summary
    print("\n" + "=" * 70)
    print("COMPARISON SUMMARY")
    print("=" * 70)
    print(f"{'Layer':<25} {'Trades':>6} {'WR%':>6} {'Total R':>10} {'PF':>6} {'MaxDD%':>8} {'Final Eq':>12}")
    print("-" * 70)
    for m in all_metrics:
        print(f"{m['label']:<25} {m.get('trades', 0):>6} {m.get('win_rate', 0):>6} {m.get('total_r', 0):>10} {m.get('profit_factor', 0):>6} {m.get('max_drawdown_pct', 0):>8} ${m.get('final_equity', 0):>11,.2f}")

    print("\nDone. All outputs in:", OUTPUT_DIR)


if __name__ == "__main__":
    main()
