"""ASIA-MIDVOL entry-ATR% band (2026-09-25, patch): engine gate == WLA mirror gate.

A rule with min/max_entry_atr_pct accepts min <= atr_pct < max and rejects everything else, including a missing
atr_pct (fail closed). With no bounds set, the gate is inert. The engine and the mirror must agree on every point.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

BAND = {"min_entry_atr_pct": 0.3, "max_entry_atr_pct": 0.5}
POINTS = [None, 0.0, 0.2, 0.3, 0.45, 0.4999, 0.5, 0.7]


def _run(coro):
    loop = asyncio.new_event_loop()   # private loop: asyncio.run() would clear the default loop for later tests
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _live():
    os.environ.pop("API_FOOTBALL_KEY", None)
    import research.signal_shadow as ss
    from config.loader import load_config
    return ss, load_config(ss._LIVE_CONFIG_PATH).burst_follow


def _open_cell(rule, bf):
    for regime in list(rule.allowed_btc_regimes or bf.allowed_btc_regimes):
        for wd in range(5):
            if wd in (rule.exclude_weekdays or []):
                continue
            for hour in range(24):
                if rule.hour_gate_reason(hour, wd, regime) is None:
                    return wd, hour, regime
    raise AssertionError("arm has no open cell")


def _decide(ss, bf, arm, rule_updates, atr_pct):
    from engines.liq_burst_follow_engine import BurstFollowState, LiqBurstFollowEngine
    rule = bf.session_rules[arm].model_copy(update=rule_updates)
    bf2 = bf.model_copy(update={"session_rules": {**bf.session_rules, arm: rule}})
    snap = ss._snapshot_for(arm, rule, bf2)
    strategy = {v: k for k, v in ss._LIVE_ARM_FOR_STRATEGY.items()}[arm]
    spec = dataclasses.replace(ss._STRATEGY_BY_NAME[strategy], live_gates=snap)
    wd, hour, regime = _open_cell(rule, bf2)
    bar_time = datetime(2026, 9, 21, tzinfo=timezone.utc) + timedelta(days=wd, hours=hour, minutes=4, seconds=59)
    imb = 0.95 if arm != "asia" else -0.95
    f = {"bar_time": bar_time, "hour": hour, "session": arm, "close": 100.0, "atr": 1.0,
         "cascade_strength": 0.6, "vol_z": 0.5, "n_confirms": 3, "decile": 3, "liq_imbalance_30m": imb,
         "burst_volume_30m": 1e9, "burst_events_30m": 1000}
    if atr_pct is not None:
        f["atr_pct"] = atr_pct
    engine = LiqBurstFollowEngine(bf2)
    engine._features = lambda candles, st, f=f: dict(f)
    burst = {"volume_30m": 1e9, "events_30m": 1000, "imbalance_30m": imb}
    sig = _run(engine.evaluate("TESTUSDT", [], [], [], BurstFollowState(), burst=burst,
                               btc_regime=regime, btc_regime_age_bars=1, btc_regime_dist=None))
    shadow = SimpleNamespace(_market_ctx=ss.MarketContext(btc_trend_state=regime, btc_regime_age_bars=1))
    reason = ss.SignalShadow._live_gate_block_reason(shadow, spec, f, "LONG" if imb > 0 else "SHORT", imb)
    return sig is not None, reason


@pytest.mark.parametrize("arm", ["london", "ny"])
def test_band_engine_equals_mirror(arm):
    ss, bf = _live()
    for ap in POINTS:
        engine_ok, reason = _decide(ss, bf, arm, BAND, ap)
        assert engine_ok == (reason is None), (arm, ap, engine_ok, reason)
        inside = ap is not None and 0.3 <= ap < 0.5
        assert engine_ok == inside, (arm, ap, engine_ok)
        if not inside:
            assert reason == "atr_band", (arm, ap, reason)


@pytest.mark.parametrize("arm", ["london", "ny"])
def test_no_band_is_inert(arm):
    ss, bf = _live()
    for ap in POINTS:
        engine_ok, reason = _decide(ss, bf, arm, {}, ap)
        assert engine_ok and reason is None, (arm, ap, engine_ok, reason)


def test_loader_accepts_band_fields():
    _, bf = _live()
    rule = bf.session_rules["ny"].model_copy(update=BAND)
    assert (rule.min_entry_atr_pct, rule.max_entry_atr_pct) == (0.3, 0.5)
    assert bf.session_rules["ny"].min_entry_atr_pct is None      # live yaml: no band anywhere
