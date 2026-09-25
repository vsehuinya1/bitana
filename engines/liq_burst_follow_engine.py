"""
Liq Burst Follow Engine

Session-aware burst entries mirroring shadow-validated strategies:
  asia  → follow_3h_asia (36-bar time exit, LONG on +imb)
  ny    → burst_follow (6-bar, both sides, TP)
  london → london_burst_fade (6-bar, fade)
  late  → late_fade (6-bar, fade)
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import numpy as np

from config.loader import LiqBurstFollowConfig, SessionBurstRule
from core.logging_setup import get_logger
from core.models import Candle, EngineType, Signal, Side
from engines.liq_cluster_engine_v5 import (
    CFG as V5_CFG,
    SNIPER_ALLOWED_HOURS,
    _atr,
    _ema,
    _z_score,
    _compute_aggression,
    _score_to_decile,
    _is_decile_tradable,
)

logger = get_logger("liq_burst_follow_engine")

SideMode = Literal["follow", "fade"]


@dataclass
class BurstFollowState:
    """Per-symbol state updated from force-order pipeline."""
    cascade_strength: float = 0.0
    liq_direction_imb: float = 0.0
    ret_5d: float = 0.0
    cascade_active: bool = False
    burst_volume_30m: float = 0.0
    burst_events_30m: int = 0
    liq_imbalance_30m: float = 0.0
    last_burst_time: datetime | None = field(default=None)


def _cluster_bucket(bar_time: datetime, window_min: int = 15) -> str:
    minute = (bar_time.minute // window_min) * window_min
    return bar_time.replace(minute=minute, second=0, microsecond=0).isoformat()


def _session(hour: int) -> str:
    if 0 <= hour < 8:
        return "asia"
    if 8 <= hour < 14:
        return "london"
    if 14 <= hour < 22:
        return "ny"
    return "late"


def _bar_session(candles_5m: list[Candle]) -> str:
    """Session of the closing bar for pre-feature counters ("na" if unknown)."""
    if not candles_5m:
        return "na"
    bt = getattr(candles_5m[-1], "close_time", None)
    return _session(bt.hour) if bt is not None else "na"


def _resolve_side(mode: SideMode, imb: float) -> Side | None:
    if mode == "fade":
        return Side.SHORT if imb > 0 else Side.LONG
    return Side.LONG if imb > 0 else Side.SHORT


class LiqBurstFollowEngine:
    """Engine: session-specific burst follow/fade with shadow-aligned exits."""

    # Gate observability (PREREG-OIGATE/DISTCAP): class-level so the main
    # process metrics endpoint can expose cross-symbol counters to the
    # dashboard. Counters reset on process restart.
    gate_stats: dict = {"oi_inflow_gate": 0, "btc_dist_cap": 0, "last_block": None}
    # ENTRY-AUDIT (#5, 2026-09-23): per-(session, reason) skip counters over
    # every evaluate/_matches branch plus the main.py portfolio/sizing skips.
    # Served nested as gate_stats["by_reason"][session][reason] (JSON-safe);
    # _hour_counts holds the since-last-summary deltas for the hourly INFO line.
    gate_stats["by_reason"] = {}
    gate_stats["oi_inflow_dark"] = 0  # gate OFF: bars the gate WOULD have blocked
    _hour_counts: dict[tuple[str, str], int] = defaultdict(int)
    _summary_hour: str | None = None

    @classmethod
    def record_gate(cls, session: str | None, reason: str) -> None:
        """Count one gate outcome. Observability only — never alters a decision."""
        session = session or "na"
        by_session = cls.gate_stats["by_reason"].setdefault(session, {})
        by_session[reason] = by_session.get(reason, 0) + 1
        cls._hour_counts[(session, reason)] += 1

    @classmethod
    def maybe_log_hourly_summary(cls, now: datetime | None = None) -> None:
        """One INFO line per UTC wall-clock hour with the prior hour's deltas."""
        now = now or datetime.now(timezone.utc)
        hour_key = now.strftime("%Y-%m-%dT%H")
        if cls._summary_hour is None:
            cls._summary_hour = hour_key
            return
        if hour_key == cls._summary_hour:
            return
        counts = {
            f"{s}:{r}": n
            for (s, r), n in sorted(cls._hour_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        }
        logger.info(
            "Burst gate hourly summary",
            hour=cls._summary_hour, total=sum(cls._hour_counts.values()), counts=counts,
        )
        cls._hour_counts.clear()
        cls._summary_hour = hour_key

    def __init__(
        self,
        cfg: LiqBurstFollowConfig,
        rest_client=None,  # data.binance_rest.BinanceRestClient (OI gate data)
        cluster_window_min: int = 15,
    ) -> None:
        self.cfg = cfg
        self._rest_client = rest_client
        # 2026-09-25 cascade window: width of signal_data["cluster_bucket"], which the portfolio's
        # max_cluster_positions / max_cluster_risk_pct and the consecutive-loss counter group by.
        # Wired from portfolio.cluster_window_minutes (that key had no consumer before).
        self._cluster_window_min = int(cluster_window_min or 15)
        # PREREG-OIGATE: OI cache cloned from tools/v5_forward_test.py
        # (_refresh_oi_delta) so the live gate computes the SAME
        # oi_delta_30m_pct field the shadow basis was measured on.
        self._oi_cache: dict[str, tuple[float, float]] = {}
        self._oi_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=12))

    async def _oi_delta_30m(self, symbol: str) -> float | None:
        """OI % change vs oldest cached sample. Fail-open: returns None on
        fetch error, <2 samples, or non-positive baseline -> gate allows."""
        if self._rest_client is None:
            return None
        now = time.monotonic()
        cached = self._oi_cache.get(symbol)
        if not cached or now - cached[1] > 300:
            try:
                resp = await self._rest_client.get_open_interest(symbol)
                oi = float(resp.get("openInterest", 0))
                self._oi_cache[symbol] = (oi, now)
                self._oi_history[symbol].append((now, oi))
            except Exception as e:  # noqa: BLE001 — fail-open by design
                logger.debug("OI fetch failed", symbol=symbol, error=str(e))
                return None
        else:
            oi = cached[0]
        hist = self._oi_history[symbol]
        if len(hist) < 2:
            return None
        old_oi = hist[0][1]
        if old_oi <= 0:
            return None
        return round((oi - old_oi) / old_oi * 100.0, 4)

    def _rule_for_session(self, session: str) -> SessionBurstRule | None:
        if self.cfg.session_rules:
            return self.cfg.session_rules.get(session)
        if self.cfg.sessions is not None and session not in self.cfg.sessions:
            return None
        return SessionBurstRule(
            side_mode="follow",
            pos_imb_only=self.cfg.pos_imb_only,
            min_cascade_strength=self.cfg.min_cascade_strength,
            min_vol_z=self.cfg.min_vol_z,
            min_n_confirms=self.cfg.min_n_confirms,
            min_decile=self.cfg.min_decile,
            stop_atr=self.cfg.stop_atr,
            tp_atr=999.0,
            time_bars=self.cfg.time_bars,
            time_exit_only=self.cfg.time_exit_only,
            trail_atr=self.cfg.trail_atr if self.cfg.time_exit_only else None,
            trail_trigger_r=self.cfg.trail_trigger_r if self.cfg.time_exit_only else None,
        )

    def _features(self, candles_5m: list[Candle], st: BurstFollowState) -> dict | None:
        n_needed = max(V5_CFG.range_lookback, V5_CFG.z_lookback, V5_CFG.ema_period * 3)
        if len(candles_5m) < n_needed:
            return None

        closes = np.array([c.close for c in candles_5m])
        highs = np.array([c.high for c in candles_5m])
        lows = np.array([c.low for c in candles_5m])
        volumes = np.array([c.volume for c in candles_5m])
        bar = candles_5m[-1]

        atr = _atr(highs, lows, closes, V5_CFG.atr_period)
        if atr <= 0:
            return None
        ema = _ema(closes, V5_CFG.ema_period)

        if len(highs) > V5_CFG.range_lookback:
            range_high = float(np.max(highs[-(V5_CFG.range_lookback + 1):-1]))
        else:
            range_high = float(np.max(highs[:-1])) if len(highs) > 1 else highs[0]

        vol_z = _z_score(volumes, V5_CFG.z_lookback)

        taker_buys = np.array([c.taker_buy_volume for c in candles_5m])
        if len(taker_buys) >= V5_CFG.z_lookback and np.any(taker_buys[-V5_CFG.z_lookback:] > 0):
            taker_ratios = taker_buys / np.maximum(volumes, 1e-10)
            imb_z = _z_score(taker_ratios, V5_CFG.z_lookback)
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
            "imb": imb_z > V5_CFG.imb_z_threshold,
            "vol": vol_z > V5_CFG.vol_z_threshold,
            "body": body_ratio >= V5_CFG.body_strength_min,
            "impulse": impulse_pct >= V5_CFG.impulse_min_pct,
            "momentum": close > ema,
        }
        n_confirms = sum(1 for v in confirmations.values() if v)

        aggression = _compute_aggression(candles_5m)
        decile = _score_to_decile(aggression)
        decile_ok = _is_decile_tradable(decile, confirmations)
        bd_ok = bd_pct >= -2.0

        bar_time = bar.close_time if getattr(bar, "close_time", None) else datetime.now(timezone.utc)
        if bar_time.tzinfo is None:
            bar_time = bar_time.replace(tzinfo=timezone.utc)
        hour = bar_time.hour
        session = _session(hour)

        return {
            "bar_time": bar_time,
            "hour": hour,
            "session": session,
            "close": close,
            "atr": atr,
            "atr_pct": (atr / close * 100) if close > 0 else 0.0,
            "cascade_strength": st.cascade_strength,
            "liq_direction_imb": st.liq_direction_imb,
            "ret_5d": st.ret_5d,
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
            "v_confirms3": int(bd_ok and n_confirms >= 3 and decile_ok),
            "v_strict": int(
                bd_ok and hour in SNIPER_ALLOWED_HOURS
                and n_confirms >= V5_CFG.min_confirmations and decile_ok
            ),
            "v_allhours": int(bd_ok and n_confirms >= V5_CFG.min_confirmations and decile_ok),
            "burst_volume_30m": st.burst_volume_30m,
            "burst_events_30m": st.burst_events_30m,
            "liq_imbalance_30m": st.liq_imbalance_30m,
        }

    def _matches(
        self, f: dict, rule: SessionBurstRule, symbol: str = "", *, record: bool = True,
    ) -> tuple[bool, Side | None]:
        """Arm-rule match. record=False = read-only probe (would_have_matched on
        OI-blocked bars): identical decision, no skip counters bumped."""
        imb = float(f.get("liq_imbalance_30m", f.get("liq_direction_imb", 0.0)))
        session = f.get("session", "")

        def skip(reason: str, **kw) -> tuple[bool, None]:
            if record:
                type(self).record_gate(session, reason)
            logger.debug("Burst session skip", symbol=symbol, session=session, reason=reason, **kw)
            return False, None

        if rule.min_imb > 0 and abs(imb) < rule.min_imb:
            return skip("imb", imb=round(imb, 4), min_imb=rule.min_imb)
        if rule.pos_imb_only and imb <= 0:
            return skip("pos_imb_only", imb=round(imb, 4))
        if rule.neg_imb_only and imb >= 0:
            return skip("neg_imb_only", imb=round(imb, 4))

        if f["cascade_strength"] < rule.min_cascade_strength:
            return skip(
                "cascade", cascade=f["cascade_strength"], min_cascade=rule.min_cascade_strength,
            )
        if f["vol_z"] < rule.min_vol_z:
            return skip("vol_z", vol_z=round(f["vol_z"], 4), min_vol_z=rule.min_vol_z)
        if f["n_confirms"] < rule.min_n_confirms:
            return skip(
                "n_confirms", n_confirms=f["n_confirms"], min_n_confirms=rule.min_n_confirms,
            )
        if f["decile"] < rule.min_decile:
            return skip("decile", decile=f["decile"], min_decile=rule.min_decile)

        # ENTRY-AUDIT (#3, 2026-09-23): burst_volume/events re-check removed —
        # unreachable. evaluate() already returns on the burst dict below the
        # same cfg floor, and main.py syncs exactly that dict into state (same
        # cfg object, same >= test) before calling evaluate(), so the state
        # copy read here could never fail.

        side = _resolve_side(rule.side_mode, imb)
        if rule.allowed_side and side is not None and side.value != rule.allowed_side:
            return skip("side_pin")
        return side is not None, side

    async def evaluate(
        self,
        symbol: str,
        candles_5m: list[Candle],
        candles_15m: list[Candle],
        candles_1m: list[Candle],
        state: BurstFollowState,
        *,
        burst: dict | None = None,
        btc_regime: str | None = None,
        btc_regime_age_bars: int | None = None,
        btc_regime_dist: float | None = None,
    ) -> Signal | None:
        cls = type(self)
        cls.maybe_log_hourly_summary()
        # ENTRY-AUDIT (#1 i, 2026-09-23): OI DARK. Gate OFF -> sample OI on
        # EVERY bar, before any gate — the shadow's cadence (v5_forward_test
        # _refresh_oi_delta runs per symbol per bar), so the dark delta is the
        # same metric the shadow logs. Gate ON keeps the original lazy
        # sample-at-gate-reach below, so blocking behavior is unchanged.
        dark_oi_delta: float | None = None
        if not self.cfg.oi_inflow_gate_enabled:
            dark_oi_delta = await self._oi_delta_30m(symbol)

        if burst is None:
            cls.record_gate(_bar_session(candles_5m), "no_burst_stats")
            return None
        if float(burst.get("volume_30m", 0.0)) < self.cfg.min_burst_volume_30m:
            cls.record_gate(_bar_session(candles_5m), "burst_floor")
            return None
        if int(burst.get("events_30m", 0)) < self.cfg.min_burst_events_30m:
            cls.record_gate(_bar_session(candles_5m), "burst_floor")
            return None

        f = self._features(candles_5m, state)
        if f is None:
            cls.record_gate(_bar_session(candles_5m), "features_none")
            return None
        session = f["session"]

        rule = self._rule_for_session(f["session"])
        if rule is None:
            cls.record_gate(session, "no_rule")
            return None

        if self.cfg.btc_regime_gate_enabled:
            allowed = rule.allowed_btc_regimes or self.cfg.allowed_btc_regimes
            if btc_regime is None:
                cls.record_gate(session, "regime_unknown")
                logger.debug(
                    "Burst session skip", symbol=symbol, session=f["session"],
                    reason="btc_regime_unknown",
                )
                return None
            if btc_regime not in allowed:
                cls.record_gate(session, "regime")
                logger.debug(
                    "Burst session skip", symbol=symbol, session=f["session"],
                    reason="btc_regime_gate", btc_regime=btc_regime, allowed=allowed,
                )
                return None

        max_age = rule.max_regime_age_bars or self.cfg.max_regime_age_bars
        if max_age is not None:
            if btc_regime_age_bars is None:
                cls.record_gate(session, "regime_age_unknown")
                logger.debug(
                    "Burst session skip", symbol=symbol, session=f["session"],
                    reason="btc_regime_age_unknown",
                )
                return None
            if btc_regime_age_bars > max_age:
                cls.record_gate(session, "regime_age")
                logger.debug(
                    "Burst session skip", symbol=symbol, session=f["session"],
                    reason="btc_regime_age_gate",
                    regime_age_bars=btc_regime_age_bars, max_age_bars=max_age,
                )
                return None

        bar_time = f["bar_time"]
        # 2026-09-01: hour gate resolved via rule.hour_gate_reason — shared
        # with the WLA mirror (research/signal_shadow.py) and the regime-flip
        # notifier so a config edit can never desync the three consumers.
        # Replaces the inline hours/added/excluded logic (added_weekday_
        # regime_hours withdrawn by owner 2026-09-01 with the Tue-neutral-h14
        # wire; replaced by excluded_weekday_regime_hours).
        hour_reason = rule.hour_gate_reason(f["hour"], bar_time.weekday(), btc_regime)
        if hour_reason:
            cls.record_gate(session, hour_reason)  # hour_gate / weekday_*_excluded
            logger.debug(
                "Burst session skip", symbol=symbol, session=f["session"],
                reason=hour_reason, weekday=bar_time.weekday(), hour=f["hour"],
            )
            return None

        # PREREG-ASIA-DISTCAP (2026-09-03): per-arm EMA200-stretch ceiling.
        # Fail-open on missing dist (None -> allow), same convention as the
        # OI gate. Only arms whose rule carries btc_dist_max_pct are gated.
        if (
            rule.btc_dist_max_pct is not None
            and btc_regime_dist is not None
            and btc_regime_dist > rule.btc_dist_max_pct
        ):
            cls.record_gate(session, "btc_dist_cap")
            type(self).gate_stats["btc_dist_cap"] += 1
            type(self).gate_stats["last_block"] = {
                "reason": "btc_dist_cap", "symbol": symbol,
                "session": f["session"], "ts": bar_time.isoformat(),
                "detail": f"dist={btc_regime_dist}% > {rule.btc_dist_max_pct}%",
            }
            logger.info(
                "Burst entry blocked", symbol=symbol, session=f["session"],
                reason="btc_dist_cap", btc_dist_pct=btc_regime_dist,
                max_pct=rule.btc_dist_max_pct,
            )
            return None

        # ASIA-MIDVOL (2026-09-25, patch): per-arm entry-ATR% band; fail CLOSED on missing atr_pct.
        if rule.min_entry_atr_pct is not None or rule.max_entry_atr_pct is not None:
            atr_pct = f.get("atr_pct")
            if (
                not atr_pct
                or (rule.min_entry_atr_pct is not None and atr_pct < rule.min_entry_atr_pct)
                or (rule.max_entry_atr_pct is not None and atr_pct >= rule.max_entry_atr_pct)
            ):
                cls.record_gate(session, "atr_band")
                logger.debug(
                    "Burst session skip", symbol=symbol, session=f["session"], reason="atr_band",
                    atr_pct=atr_pct, lo=rule.min_entry_atr_pct, hi=rule.max_entry_atr_pct,
                )
                return None

        if rule.exclude_weekdays and bar_time.weekday() in rule.exclude_weekdays:
            cls.record_gate(session, "weekday")
            logger.debug(
                "Burst session skip", symbol=symbol, session=f["session"],
                reason="weekday_excluded", weekday=bar_time.weekday(),
            )
            return None

        if state.last_burst_time is not None:
            elapsed = (bar_time - state.last_burst_time).total_seconds()
            if elapsed < self.cfg.dedup_bars * 300:
                cls.record_gate(session, "dedup")
                return None

        # PREREG-OIGATE (2026-09-03): block entries into aggressive OI inflow
        # (oi_delta_30m > +0.5%), both sides. Fail-open on missing data
        # (None -> allow). Placed after all free gates so only near-final
        # candidates pay the OI fetch; before _matches/dedup-mutation so a
        # data hiccup never suppresses the next legit re-entry.
        if self.cfg.oi_inflow_gate_enabled:
            oi_delta = await self._oi_delta_30m(symbol)
            if oi_delta is not None and oi_delta > self.cfg.oi_inflow_max_pct:
                # ENTRY-AUDIT (#1 ii): read-only probe — would the arm rule have
                # matched had the gate not fired? record=False: no counters.
                would_match, _ = self._matches(f, rule, symbol, record=False)
                cls.record_gate(session, "oi_inflow_gate")
                if would_match:
                    cls.record_gate(session, "oi_inflow_gate_would_match")
                type(self).gate_stats["oi_inflow_gate"] += 1
                type(self).gate_stats["last_block"] = {
                    "reason": "oi_inflow_gate", "symbol": symbol,
                    "session": f["session"], "ts": bar_time.isoformat(),
                    "detail": f"oi_delta_30m={oi_delta}% would_have_matched={would_match}",
                }
                logger.info(
                    "Burst entry blocked", symbol=symbol, session=f["session"],
                    reason="oi_inflow_gate", oi_delta_30m_pct=oi_delta,
                    max_pct=self.cfg.oi_inflow_max_pct,
                    would_have_matched=would_match,
                )
                return None
        elif dark_oi_delta is not None and dark_oi_delta > self.cfg.oi_inflow_max_pct:
            # ENTRY-AUDIT (#1 i): gate OFF — log/count the would-block at the
            # gate's position in the chain; never blocks.
            would_match, _ = self._matches(f, rule, symbol, record=False)
            cls.record_gate(session, "oi_inflow_dark")
            if would_match:
                cls.record_gate(session, "oi_inflow_dark_would_match")
            type(self).gate_stats["oi_inflow_dark"] += 1
            logger.info(
                "oi_inflow_dark", symbol=symbol, session=f["session"],
                oi_delta_30m_pct=dark_oi_delta, max_pct=self.cfg.oi_inflow_max_pct,
                would_have_matched=would_match, ts=bar_time.isoformat(),
            )

        matched, side = self._matches(f, rule, symbol)
        if not matched or side is None:
            return None

        state.last_burst_time = bar_time
        cls.record_gate(session, "signal")

        entry = f["close"]
        atr = f["atr"]
        stop_atr = rule.stop_atr
        if rule.regime_stop_atr and btc_regime in rule.regime_stop_atr:
            stop_atr = rule.regime_stop_atr[btc_regime]
        if side == Side.LONG:
            stop_price = entry - stop_atr * atr
        else:
            stop_price = entry + stop_atr * atr

        strategy_name = rule.shadow_strategy or f"{rule.side_mode}_{f['session']}"

        logger.info(
            "Burst session signal",
            symbol=symbol, side=side.value, session=f["session"],
            strategy=strategy_name, side_mode=rule.side_mode,
            decile=f["decile"], cascade=f["cascade_strength"],
            vol_z=f["vol_z"], imb=f["liq_imbalance_30m"],
            time_bars=rule.time_bars, time_exit_only=rule.time_exit_only,
        )

        signal_data = {
            "entry_atr": atr,
            "shadow_strategy": strategy_name,
            "side_mode": rule.side_mode,
            "session": f["session"],
            "cluster_bucket": _cluster_bucket(bar_time, self._cluster_window_min),
            "stop_atr": stop_atr,
            "tp_atr": rule.tp_atr,
            "time_bars": rule.time_bars,
            "time_exit_only": rule.time_exit_only,
            "decile": f["decile"],
            "cascade_strength": f["cascade_strength"],
            "vol_z": f["vol_z"],
            "liq_imbalance": f["liq_imbalance_30m"],
            "n_confirms": f["n_confirms"],
            "btc_trend_state": btc_regime,
        }
        if rule.trail_atr is not None and rule.trail_trigger_r is not None:
            signal_data["trail_atr"] = rule.trail_atr
            signal_data["trail_trigger_r"] = rule.trail_trigger_r

        return Signal(
            trade_uuid=str(uuid.uuid4()),
            engine=EngineType.LIQ_BURST_FOLLOW,
            symbol=symbol,
            side=side,
            entry_price=entry,
            stop_price=stop_price,
            signal_data=signal_data,
        )
