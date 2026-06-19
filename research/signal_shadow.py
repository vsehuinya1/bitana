"""Logging-only signal shadow.

Turns the live (now healthy) candle + liquidation pipe into research samples
WITHOUT touching live equity. On every liquidation-cascade bar it snapshots the
exact features the v6.5 engine sees, flags which nested gate variants WOULD fire,
and tracks forward ATR-normalised returns + MFE/MAE so any rule, stop, target, or
direction can be evaluated offline.

Design notes
------------
- Side-effect free: never calls engine.evaluate() (that mutates live state) and
  never acquires the runner's engine lock. on_bar() is fully synchronous — safe
  to call from _on_5m_close, which already holds the lock.
- Restart-safe forward tracking: open snapshots live in the DB and are updated
  in place each bar, so a watchdog restart does not lose accumulated MFE/MAE.
- Variants nest: strict ⊂ all_hours ⊂ confirms3 ⊂ loose. We snapshot every
  cascade-active bar; variant flags on each row let us filter any rule family
  offline without re-running live code.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from core.logging_setup import get_logger
from engines.liq_cluster_engine_v5 import (
    CFG,
    SNIPER_ALLOWED_HOURS,
    SymbolState,
    _atr,
    _compute_aggression,
    _ema,
    _is_decile_tradable,
    _score_to_decile,
    _z_score,
)

logger = get_logger("signal_shadow")

DB_PATH = Path("storage/signal_shadow.db")

# Forward horizons in 5m bars: 15m, 30m, 1h, 2h, 4h, 8h.
HORIZONS = (3, 6, 12, 24, 48, 96)
MAX_H = HORIZONS[-1]

# Catch-all candidate floor and de-duplication window (bars).
LOOSE_MIN_CONFIRMS = 2
DEDUP_BARS = 3


def _session(hour: int) -> str:
    if 0 <= hour < 8:
        return "asia"
    if 8 <= hour < 14:
        return "london"
    if 14 <= hour < 22:
        return "ny"
    return "late"


class SignalShadow:
    def __init__(self, db_path: Path = DB_PATH):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_db()
        self._last_snap_bar: dict[str, datetime] = {}
        self._last_burst_bar: dict[str, datetime] = {}
        self._writes = 0
        # Forward windows of snapshots still open at shutdown last run never get a
        # final bar; mark them so analysis can exclude truncated paths.
        self.conn.execute(
            "UPDATE snapshots SET status='orphaned' WHERE status='open' "
            "AND created_at < ?",
            ((datetime.now(timezone.utc).timestamp() - MAX_H * 300),),
        )
        self.conn.execute(
            "UPDATE burst_snapshots SET status='orphaned' WHERE status='open' "
            "AND created_at < ?",
            ((datetime.now(timezone.utc).timestamp() - MAX_H * 300),),
        )
        self.conn.commit()

    def _init_db(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bar_time TEXT, symbol TEXT, hour INT, session TEXT,
                close REAL, atr REAL, atr_pct REAL,
                cascade_strength REAL, liq_direction_imb REAL, ret_5d REAL,
                vol_z REAL, imb_z REAL, breakout_distance_pct REAL,
                body_ratio REAL, impulse_pct REAL, above_ema INT, breakout INT,
                n_confirms INT, decile INT, aggression REAL,
                v_strict INT, v_allhours INT, v_confirms3 INT, v_loose INT,
                status TEXT DEFAULT 'open', bars_tracked INT DEFAULT 0,
                fwd_atr_3 REAL, fwd_atr_6 REAL, fwd_atr_12 REAL,
                fwd_atr_24 REAL, fwd_atr_48 REAL, fwd_atr_96 REAL,
                mfe_atr REAL DEFAULT 0, mae_atr REAL DEFAULT 0,
                created_at REAL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sym_status ON snapshots(symbol, status)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS burst_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bar_time TEXT, symbol TEXT, hour INT, session TEXT,
                close REAL, atr REAL, atr_pct REAL,
                burst_volume_15m REAL, burst_volume_30m REAL, burst_volume_60m REAL,
                burst_events_15m INT, burst_events_30m INT, burst_events_60m INT,
                long_liq_30m REAL, short_liq_30m REAL, liq_imbalance_30m REAL,
                max_order_usd_30m REAL,
                cascade_strength REAL, liq_direction_imb REAL, ret_5d REAL,
                vol_z REAL, imb_z REAL, breakout_distance_pct REAL,
                body_ratio REAL, impulse_pct REAL, above_ema INT, breakout INT,
                n_confirms INT, decile INT, aggression REAL,
                status TEXT DEFAULT 'open', bars_tracked INT DEFAULT 0,
                fwd_atr_3 REAL, fwd_atr_6 REAL, fwd_atr_12 REAL,
                fwd_atr_24 REAL, fwd_atr_48 REAL, fwd_atr_96 REAL,
                mfe_atr REAL DEFAULT 0, mae_atr REAL DEFAULT 0,
                created_at REAL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_burst_sym_status ON burst_snapshots(symbol, status)"
        )
        self.conn.commit()

    def _features(self, candles_5m: list, st: SymbolState) -> dict | None:
        """Pure feature extraction mirroring engine.evaluate() — no side effects."""
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
        if len(taker_buys) >= CFG.z_lookback and np.any(taker_buys[-CFG.z_lookback:] > 0):
            taker_ratios = taker_buys / np.maximum(volumes, 1e-10)
            imb_z = _z_score(taker_ratios, CFG.z_lookback)
        else:
            imb_z = 0.0

        close = float(closes[-1])
        body = abs(close - bar.open)
        total_range = highs[-1] - lows[-1]
        body_ratio = (body / total_range) if total_range > 0 else 0.0
        impulse_pct = abs(close - bar.open) / bar.open * 100 if bar.open else 0.0
        bd_abs = close - range_high
        bd_pct = (bd_abs / range_high * 100) if range_high > 0 else 0.0

        confirmations = {
            "breakout": close > range_high,
            "imb": imb_z > CFG.imb_z_threshold,
            "vol": vol_z > CFG.vol_z_threshold,
            "body": body_ratio >= CFG.body_strength_min,
            "impulse": impulse_pct >= CFG.impulse_min_pct,
            "momentum": close > ema,
        }
        n_confirms = sum(1 for v in confirmations.values() if v)

        aggression = _compute_aggression(candles_5m)
        decile = _score_to_decile(aggression)

        bar_time = bar.close_time if getattr(bar, "close_time", None) else datetime.now(timezone.utc)
        if bar_time.tzinfo is None:
            bar_time = bar_time.replace(tzinfo=timezone.utc)
        hour = bar_time.hour

        decile_ok = _is_decile_tradable(decile, confirmations)
        bd_ok = bd_pct >= -2.0

        return {
            "bar_time": bar_time,
            "hour": hour,
            "close": close,
            "atr": atr,
            "atr_pct": (atr / close * 100) if close > 0 else 0.0,
            "cascade_strength": float(st.cascade_strength),
            "liq_direction_imb": float(st.liq_direction_imb),
            "ret_5d": float(st.ret_5d),
            "vol_z": float(vol_z),
            "imb_z": float(imb_z),
            "breakout_distance_pct": float(bd_pct),
            "body_ratio": float(body_ratio),
            "impulse_pct": float(impulse_pct),
            "above_ema": int(confirmations["momentum"]),
            "breakout": int(confirmations["breakout"]),
            "n_confirms": int(n_confirms),
            "decile": int(decile),
            "aggression": float(aggression),
            # Variant flags (nested). All require cascade gating handled by caller.
            "v_strict": int(bd_ok and hour in SNIPER_ALLOWED_HOURS
                            and n_confirms >= CFG.min_confirmations and decile_ok),
            "v_allhours": int(bd_ok and n_confirms >= CFG.min_confirmations and decile_ok),
            "v_confirms3": int(bd_ok and n_confirms >= 3 and decile_ok),
            "v_loose": int(n_confirms >= LOOSE_MIN_CONFIRMS),
        }

    def _advance_open(self, table: str, symbol: str, high: float, low: float, close: float):
        """Advance forward windows for one shadow table."""
        if table not in {"snapshots", "burst_snapshots"}:
            raise ValueError(f"unsupported shadow table: {table}")
        rows = self.conn.execute(
            "SELECT id, close, atr, bars_tracked, mfe_atr, mae_atr "
            f"FROM {table} WHERE symbol=? AND status='open'",
            (symbol,),
        ).fetchall()
        for r in rows:
            atr = r["atr"]
            if atr <= 0:
                self.conn.execute(f"UPDATE {table} SET status='done' WHERE id=?", (r["id"],))
                continue
            entry = r["close"]
            nb = r["bars_tracked"] + 1
            fav = (high - entry) / atr
            adv = (low - entry) / atr
            mfe = max(r["mfe_atr"], fav)
            mae = min(r["mae_atr"], adv)
            sets = ["bars_tracked=?", "mfe_atr=?", "mae_atr=?"]
            vals: list = [nb, mfe, mae]
            if nb in HORIZONS:
                sets.append(f"fwd_atr_{nb}=?")
                vals.append((close - entry) / atr)
            if nb >= MAX_H:
                sets.append("status='done'")
            vals.append(r["id"])
            self.conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id=?", vals)

    def on_bar(self, symbol: str, candles_5m: list, st: SymbolState):
        """Advance open forward-windows for this symbol, then maybe snapshot.

        Must stay synchronous and exception-light; caller wraps in try/except.
        """
        if not candles_5m:
            return
        bar = candles_5m[-1]
        self._advance_open("snapshots", symbol, bar.high, bar.low, bar.close)

        # 2) Snapshot every cascade-active bar. We deliberately do NOT apply the
        #    strategy's confirmation OR cascade-strength floors here — those are the
        #    very thresholds we want to evaluate. cascade_strength and confirm-count
        #    are stored as features/flags so any threshold is testable offline.
        if not st.cascade_active:
            self._maybe_commit()
            return

        f = self._features(candles_5m, st)
        if f is None:
            self._maybe_commit()
            return

        # De-dup: at most one new snapshot per symbol per DEDUP_BARS window.
        last = self._last_snap_bar.get(symbol)
        if last is not None and (f["bar_time"] - last).total_seconds() < DEDUP_BARS * 300:
            self._maybe_commit()
            return
        self._last_snap_bar[symbol] = f["bar_time"]

        self.conn.execute(
            """
            INSERT INTO snapshots (
                bar_time, symbol, hour, session, close, atr, atr_pct,
                cascade_strength, liq_direction_imb, ret_5d,
                vol_z, imb_z, breakout_distance_pct,
                body_ratio, impulse_pct, above_ema, breakout,
                n_confirms, decile, aggression,
                v_strict, v_allhours, v_confirms3, v_loose,
                created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f["bar_time"].isoformat(), symbol, f["hour"], _session(f["hour"]),
                f["close"], f["atr"], f["atr_pct"],
                f["cascade_strength"], f["liq_direction_imb"], f["ret_5d"],
                f["vol_z"], f["imb_z"], f["breakout_distance_pct"],
                f["body_ratio"], f["impulse_pct"], f["above_ema"], f["breakout"],
                f["n_confirms"], f["decile"], f["aggression"],
                f["v_strict"], f["v_allhours"], f["v_confirms3"], f["v_loose"],
                datetime.now(timezone.utc).timestamp(),
            ),
        )
        self._writes += 1
        self._maybe_commit(force=True)

    def on_intraday_burst(
        self,
        symbol: str,
        candles_5m: list,
        st: SymbolState,
        burst: dict,
        *,
        min_volume_usd: float = 20_000.0,
        min_events: int = 3,
        dedup_bars: int = DEDUP_BARS,
    ):
        """Snapshot force-order intraday bursts, independent of daily cascade state."""
        if not candles_5m:
            return
        bar = candles_5m[-1]
        self._advance_open("burst_snapshots", symbol, bar.high, bar.low, bar.close)

        vol_30m = float(burst.get("volume_30m", 0.0))
        events_30m = int(burst.get("events_30m", 0))
        if vol_30m < min_volume_usd or events_30m < min_events:
            self._maybe_commit()
            return

        f = self._features(candles_5m, st)
        if f is None:
            self._maybe_commit()
            return

        last = self._last_burst_bar.get(symbol)
        if last is not None and (f["bar_time"] - last).total_seconds() < dedup_bars * 300:
            self._maybe_commit()
            return
        self._last_burst_bar[symbol] = f["bar_time"]

        self.conn.execute(
            """
            INSERT INTO burst_snapshots (
                bar_time, symbol, hour, session, close, atr, atr_pct,
                burst_volume_15m, burst_volume_30m, burst_volume_60m,
                burst_events_15m, burst_events_30m, burst_events_60m,
                long_liq_30m, short_liq_30m, liq_imbalance_30m, max_order_usd_30m,
                cascade_strength, liq_direction_imb, ret_5d,
                vol_z, imb_z, breakout_distance_pct,
                body_ratio, impulse_pct, above_ema, breakout,
                n_confirms, decile, aggression,
                created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f["bar_time"].isoformat(), symbol, f["hour"], _session(f["hour"]),
                f["close"], f["atr"], f["atr_pct"],
                float(burst.get("volume_15m", 0.0)),
                vol_30m,
                float(burst.get("volume_60m", 0.0)),
                int(burst.get("events_15m", 0)),
                events_30m,
                int(burst.get("events_60m", 0)),
                float(burst.get("long_liq_30m", 0.0)),
                float(burst.get("short_liq_30m", 0.0)),
                float(burst.get("imbalance_30m", 0.0)),
                float(burst.get("max_order_usd_30m", 0.0)),
                f["cascade_strength"], f["liq_direction_imb"], f["ret_5d"],
                f["vol_z"], f["imb_z"], f["breakout_distance_pct"],
                f["body_ratio"], f["impulse_pct"], f["above_ema"], f["breakout"],
                f["n_confirms"], f["decile"], f["aggression"],
                datetime.now(timezone.utc).timestamp(),
            ),
        )
        self._writes += 1
        self._maybe_commit(force=True)

    def _maybe_commit(self, force: bool = False):
        self._writes += 1
        if force or self._writes >= 20:
            self.conn.commit()
            self._writes = 0

    def close(self):
        """Best-effort flush for graceful runner shutdown."""
        try:
            self.conn.commit()
        finally:
            self.conn.close()
