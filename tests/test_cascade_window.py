"""Cascade window (2026-09-25): portfolio.cluster_window_minutes now sets the cluster-bucket width.

The engine stamps signal_data["cluster_bucket"] at the configured width, so max_cluster_positions /
max_cluster_risk_pct cap a whole cascade instead of a 15-min slice of it. The WLA mirror re-buckets live-mirror
book caps to the same width, while its stored 15-min cluster_bucket column is left unchanged for research.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import research.signal_shadow as ss
from engines.liq_burst_follow_engine import BurstFollowState, LiqBurstFollowEngine, _cluster_bucket


def _run(coro):
    loop = asyncio.new_event_loop()   # private loop: asyncio.run() would clear the default loop for later tests
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_bucket_floors_to_the_window():
    t = datetime(2026, 9, 25, 13, 47, 59, tzinfo=timezone.utc)
    assert _cluster_bucket(t) == "2026-09-25T13:45:00+00:00"
    assert _cluster_bucket(t, 60) == "2026-09-25T13:00:00+00:00"


def _signal(window):
    os.environ.pop("API_FOOTBALL_KEY", None)
    from config.loader import load_config
    bf = load_config(ss._LIVE_CONFIG_PATH).burst_follow
    rule = bf.session_rules["ny"]
    for wd in range(5):
        if wd in (rule.exclude_weekdays or []):
            continue
        hours = [h for h in range(24) if rule.hour_gate_reason(h, wd, "neutral") is None]
        if hours:
            break
    bar_time = datetime(2026, 9, 21, tzinfo=timezone.utc) + timedelta(days=wd, hours=hours[0], minutes=34, seconds=59)
    f = {"bar_time": bar_time, "hour": hours[0], "session": "ny", "close": 100.0, "atr": 1.0, "atr_pct": 1.0,
         "cascade_strength": 0.6, "vol_z": 0.5, "n_confirms": 3, "decile": 3, "liq_imbalance_30m": 0.95,
         "burst_volume_30m": 1e9, "burst_events_30m": 1000}
    engine = LiqBurstFollowEngine(bf, cluster_window_min=window)
    engine._features = lambda candles, st, f=f: dict(f)
    burst = {"volume_30m": 1e9, "events_30m": 1000, "imbalance_30m": 0.95}
    return _run(engine.evaluate("TESTUSDT", [], [], [], BurstFollowState(), burst=burst,
                                btc_regime="neutral", btc_regime_age_bars=1, btc_regime_dist=None))


def test_engine_stamps_the_configured_window():
    s15, s60 = _signal(15), _signal(60)
    assert s15 is not None and s60 is not None
    assert s15.signal_data["cluster_bucket"].endswith(":30:00+00:00")   # xx:34 -> 15-min slice
    assert s60.signal_data["cluster_bucket"].endswith(":00:00+00:00")   # xx:34 -> the hour


def test_rebucket():
    assert ss._rebucket("2026-09-25T13:45:00+00:00", 60) == "2026-09-25T13:00:00+00:00"
    assert ss._rebucket("2026-09-25T13:45:00+00:00", 15) == "2026-09-25T13:45:00+00:00"
    assert ss._rebucket(None, 60) is None


def test_mirror_book_cap_counts_the_whole_cascade(tmp_path, monkeypatch):
    shadow = ss.SignalShadow(str(tmp_path / "shadow.db"), portfolio=ss.ShadowPortfolioConfig(live_max_concurrent=8))
    rows = [("AAAUSDT", "2026-09-25T13:00:00+00:00"), ("BBBUSDT", "2026-09-25T13:15:00+00:00"),
            ("CCCUSDT", "2026-09-25T13:30:00+00:00")]
    for sym, bucket in rows:
        shadow.conn.execute(
            "INSERT INTO shadow_trades (strategy, symbol, side, session, cluster_bucket, status, would_live_accept) "
            "VALUES ('ny_flush_buy_1h', ?, 'LONG', 'ny', ?, 'open', 1)", (sym, bucket))
    shadow.conn.commit()
    monkeypatch.setattr(ss, "_LIVE_CLUSTER_WINDOW_MIN", 15)
    assert shadow._would_live_accept("ny_flush_buy_1h", "DDDUSDT", "LONG", "ny", "2026-09-25T13:45:00+00:00") == 1
    monkeypatch.setattr(ss, "_LIVE_CLUSTER_WINDOW_MIN", 60)
    assert shadow._would_live_accept("ny_flush_buy_1h", "DDDUSDT", "LONG", "ny", "2026-09-25T13:45:00+00:00") == 0
    assert shadow._would_live_accept("ny_flush_buy_1h", "DDDUSDT", "LONG", "ny", "2026-09-25T14:00:00+00:00") == 1
    # research strategies keep their own 15-min book regardless of the live window
    assert shadow._would_live_accept("ny_flush_buy_2h", "DDDUSDT", "LONG", "ny", "2026-09-25T13:45:00+00:00") == 1
