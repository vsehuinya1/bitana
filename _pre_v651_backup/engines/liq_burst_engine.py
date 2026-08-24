"""v66 Liq-Burst Continuation — live-computable, no daily look-ahead.

Thesis: when an hourly liquidation bucket is >= share_min of trailing-24h total
AND dominated by one side, price continues in the squeeze direction for several hours.

Validated (Jan–May 2026, 28 symbols, 60/40 OOS):
  short_dom >= 35% -> LONG | stop 3 ATR | hold 8h | OOS +0.30R/trade (n=171)

This is intentionally separate from liq_cluster_engine_v5 (reversal/cascade).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from core.models import Candle, EngineType, Side, Signal

STRATEGY_VERSION = "v66_burst"


@dataclass(frozen=True)
class BurstConfig:
    share_min: float = 0.35          # hour liq / trailing 24h
    dir_dom: float = 0.70            # long_liq or short_liq fraction
    min_trail_usd: float = 100_000.0
    stop_atr: float = 3.0
    hold_bars: int = 96              # 8h on 5m
    atr_period: int = 14
    allowed_hours: frozenset[int] = frozenset(range(24))
    dedup_hours: int = 4             # min gap between entries per symbol


@dataclass
class HourBucket:
    hour_ts: int = 0                 # UTC hour start
    long_liq: float = 0.0
    short_liq: float = 0.0

    @property
    def total(self) -> float:
        return self.long_liq + self.short_liq


@dataclass
class SymbolBurstState:
    buckets: list[HourBucket] = field(default_factory=list)
    last_entry_hour: int = -10**12
    in_trade: bool = False
    entry_price: float = 0.0
    stop_price: float = 0.0
    risk_per_unit: float = 0.0
    bars_held: int = 0
    entry_hour: int = -1
    burst_share: float = 0.0
    burst_direction: str = ""


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, n: int) -> float:
    if len(closes) < n + 1:
        return 0.0
    pc = np.roll(closes, 1)
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - pc), np.abs(lows - pc)))
    tr[0] = highs[0] - lows[0]
    return float(np.mean(tr[-n:]))


class LiqBurstEngine:
    """Hourly liq burst detector + simple continuation position manager."""

    def __init__(self, cfg: BurstConfig | None = None):
        self.cfg = cfg or BurstConfig()
        self._states: dict[str, SymbolBurstState] = {}

    def _st(self, symbol: str) -> SymbolBurstState:
        if symbol not in self._states:
            self._states[symbol] = SymbolBurstState()
        return self._states[symbol]

    def add_liq_usd(self, symbol: str, ts_ms: int, side: str, volume_usd: float) -> None:
        """Ingest one force-order event (side SELL = long liq, BUY = short liq)."""
        hour_ts = (ts_ms // 1000 // 3600) * 3600
        st = self._st(symbol)
        if not st.buckets or st.buckets[-1].hour_ts != hour_ts:
            st.buckets.append(HourBucket(hour_ts=hour_ts))
        b = st.buckets[-1]
        if side == "SELL":
            b.long_liq += volume_usd
        elif side == "BUY":
            b.short_liq += volume_usd
        # trim to ~48h of buckets
        if len(st.buckets) > 48:
            st.buckets = st.buckets[-48:]

    def seed_hourly(self, symbol: str, rows: list[tuple[int, float, float]]) -> None:
        """Bootstrap from DB: list of (hour_ts, long_liq, short_liq)."""
        st = self._st(symbol)
        st.buckets = [HourBucket(hour_ts=t, long_liq=ll, short_liq=sl) for t, ll, sl in rows]

    def trailing_24h(self, symbol: str, up_to_hour: int) -> tuple[float, float, float]:
        """Return (total, long, short) for the 24h window ending at up_to_hour (exclusive)."""
        st = self._st(symbol)
        lo = up_to_hour - 86400
        tot = ll = sl = 0.0
        for b in st.buckets:
            if lo <= b.hour_ts < up_to_hour:
                tot += b.total
                ll += b.long_liq
                sl += b.short_liq
        return tot, ll, sl

    def classify_burst(self, bucket: HourBucket, trail_total: float) -> Optional[tuple[str, float]]:
        if trail_total < self.cfg.min_trail_usd or bucket.total <= 0:
            return None
        share = bucket.total / trail_total
        if share < self.cfg.share_min:
            return None
        frac_l = bucket.long_liq / bucket.total
        if frac_l >= self.cfg.dir_dom:
            return "long_dom", share
        if frac_l <= 1.0 - self.cfg.dir_dom:
            return "short_dom", share
        return None

    def evaluate(
        self,
        symbol: str,
        candles_5m: list[Candle],
        *,
        closed_hour: int | None = None,
    ) -> Optional[Signal]:
        """Check for burst on the hour that just closed; enter next bar."""
        if len(candles_5m) < self.cfg.atr_period + 2:
            return None
        st = self._st(symbol)
        if st.in_trade:
            return None

        bar = candles_5m[-1]
        hour_end = closed_hour
        if hour_end is None:
            hour_end = int(bar.close_time.timestamp()) // 3600 * 3600
        hour_start = hour_end - 3600

        if hour_start - st.last_entry_hour < self.cfg.dedup_hours * 3600:
            return None

        entry_hour = bar.close_time.hour
        if entry_hour not in self.cfg.allowed_hours:
            return None

        bucket = next((b for b in st.buckets if b.hour_ts == hour_start), None)
        if bucket is None:
            return None

        trail, _, _ = self.trailing_24h(symbol, hour_end)
        burst = self.classify_burst(bucket, trail)
        if burst is None:
            return None

        direction, share = burst
        # v66 validated: short_dom burst -> LONG continuation
        if direction != "short_dom":
            return None

        closes = np.array([c.close for c in candles_5m])
        highs = np.array([c.high for c in candles_5m])
        lows = np.array([c.low for c in candles_5m])
        atr = _atr(highs, lows, closes, self.cfg.atr_period)
        if atr <= 0:
            return None

        entry = float(bar.close)
        stop = entry - self.cfg.stop_atr * atr
        rpu = entry - stop
        if rpu <= 0:
            return None

        st.last_entry_hour = hour_start
        return Signal(
            symbol=symbol,
            engine=EngineType.LIQ_CLUSTER,
            side=Side.LONG,
            entry_price=entry,
            stop_price=stop,
            trade_uuid=f"{symbol}_{int(bar.close_time.timestamp())}",
            signal_data={
                "strategy_version": STRATEGY_VERSION,
                "burst_direction": direction,
                "burst_share": round(share, 4),
                "trail_24h_usd": round(trail, 2),
                "atr": atr,
                "stop_atr": self.cfg.stop_atr,
            },
        )

    def on_entry(self, symbol: str, entry_price: float, stop_price: float, hour: int, share: float, direction: str):
        st = self._st(symbol)
        st.in_trade = True
        st.entry_price = entry_price
        st.stop_price = stop_price
        st.risk_per_unit = abs(entry_price - stop_price)
        st.bars_held = 0
        st.entry_hour = hour
        st.burst_share = share
        st.burst_direction = direction

    def manage_position(self, symbol: str, candles_5m: list[Candle]) -> Optional[dict]:
        st = self._st(symbol)
        if not st.in_trade or not candles_5m:
            return None

        st.bars_held += 1
        bar = candles_5m[-1]
        sd = st.risk_per_unit

        if bar.low <= st.stop_price:
            pnl_r = (st.stop_price - st.entry_price) / sd if sd > 0 else 0.0
            st.in_trade = False
            return {"action": "close", "exit_price": st.stop_price, "reason": "stop", "r": pnl_r}

        if st.bars_held >= self.cfg.hold_bars:
            pnl_r = (bar.close - st.entry_price) / sd if sd > 0 else 0.0
            st.in_trade = False
            return {"action": "close", "exit_price": bar.close, "reason": "time_8h", "r": pnl_r}

        return None
