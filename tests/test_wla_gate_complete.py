"""GATE-COMPLETE (2026-09-24): the WLA mirror binds EVERY live-engine gate.

Three guarantees:
1. Structural: every gate attribute the engine reads from its rule/cfg is bound
   in LiveGateSnapshot (or explicitly handled elsewhere). A new engine gate fails
   this test until it is mirrored, which enforces the "a config edit can never
   desync them" contract of SessionBurstRule.hour_gate_reason.
2. Behavioral: on a grid covering every gate, including the previously unbound
   min_n_confirms / min_imb / min_cascade_strength / max_regime_age_bars / burst
   floors at non-default values, the mirror's decision equals the real engine's
   evaluate() decision (engine is the oracle, _features stubbed).
3. Write-time: every shadow row written by the patched writer carries a non-null
   n_confirms (burst mirror, burst research, bar trigger, limit fill), and 0 is
   stored as 0, never as NULL.
"""
from __future__ import annotations

import asyncio
import dataclasses
import itertools
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


@pytest.fixture(scope="module", autouse=True)
def _scrub_env():
    saved = os.environ.pop("API_FOOTBALL_KEY", None)  # pydantic chokes on it
    yield
    if saved is not None:
        os.environ["API_FOOTBALL_KEY"] = saved


# engine gate attribute -> LiveGateSnapshot field that mirrors it
BOUND = {
    "allowed_btc_regimes": "allowed_regimes",
    "exclude_weekdays": "exclude_weekdays",
    "min_decile": "min_decile",
    "btc_dist_max_pct": "btc_dist_max_pct",
    "pos_imb_only": "pos_imb_only",
    "neg_imb_only": "neg_imb_only",
    "allowed_side": "allowed_side",
    "min_vol_z": "min_vol_z",
    "min_imb": "min_imb",
    "min_cascade_strength": "min_cascade_strength",
    "min_n_confirms": "min_n_confirms",
    "max_regime_age_bars": "max_regime_age_bars",
    "min_entry_atr_pct": "min_entry_atr_pct",
    "max_entry_atr_pct": "max_entry_atr_pct",
    "min_burst_volume_30m": "min_burst_volume_30m",
    "min_burst_events_30m": "min_burst_events_30m",
    "oi_inflow_gate_enabled": "oi_gate_enabled",
    "oi_inflow_max_pct": "oi_inflow_max_pct",
}
# engine gate attributes covered outside LiveGateSnapshot
HANDLED_ELSEWHERE = {
    "dedup_bars": "SignalShadow._eval_live_mirror (engine dedup clock)",
    "btc_regime_gate_enabled": "mirror always applies allowed_regimes; asserted True below",
}
GATE_ATTR = re.compile(
    r"(?:rule|self\.cfg)\.((?:min|max)_[a-z0-9_]+|allowed_[a-z_]+|exclude_[a-z_]+|"
    r"pos_imb_only|neg_imb_only|btc_dist_max_pct|dedup_bars|oi_inflow_[a-z_]+|btc_regime_gate_enabled)"
)


def _live():
    import research.signal_shadow as ss
    from config.loader import load_config

    cfg = load_config(ss._LIVE_CONFIG_PATH)
    return ss, cfg.burst_follow


def test_every_engine_gate_is_bound():
    ss, _ = _live()
    src = (REPO / "engines" / "liq_burst_follow_engine.py").read_text()
    engine_gates = set(GATE_ATTR.findall(src))
    unmirrored = engine_gates - set(BOUND) - set(HANDLED_ELSEWHERE)
    assert not unmirrored, f"engine gates with no WLA mirror binding: {sorted(unmirrored)}"
    fields = {f.name for f in dataclasses.fields(ss.LiveGateSnapshot)}
    missing = {v for k, v in BOUND.items() if k in engine_gates} - fields
    assert not missing, f"BOUND points at non-existent snapshot fields: {sorted(missing)}"


def test_regime_gate_assumption():
    # The mirror applies allowed_regimes unconditionally; valid only while the
    # live engine's regime gate is on. Turning it off must come with a mirror change.
    _, bf = _live()
    assert bf.btc_regime_gate_enabled is True


def _open_cell(rule, bf):
    """First (weekday, hour, regime) the arm trades, per the live rule."""
    regimes = list(rule.allowed_btc_regimes or bf.allowed_btc_regimes)
    for regime in regimes:
        for wd in range(5):
            if wd in (rule.exclude_weekdays or []):
                continue
            for hour in range(24):
                if rule.hour_gate_reason(hour, wd, regime) is None:
                    return wd, hour, regime
    raise AssertionError("arm has no open cell")


def _decide(ss, bf, arm, rule_updates, bf_updates, x):
    """(engine_accepts, mirror_reason) for one grid point."""
    from engines.liq_burst_follow_engine import BurstFollowState, LiqBurstFollowEngine

    rule = bf.session_rules[arm].model_copy(update=rule_updates)
    bf2 = bf.model_copy(update={**bf_updates, "session_rules": {**bf.session_rules, arm: rule}})
    snap = ss._snapshot_for(arm, rule, bf2)
    strategy = {v: k for k, v in ss._LIVE_ARM_FOR_STRATEGY.items()}[arm]
    spec = dataclasses.replace(ss._STRATEGY_BY_NAME[strategy], live_gates=snap)
    wd, hour, regime = x["cell"]
    bar_time = datetime(2026, 9, 21, tzinfo=timezone.utc) + timedelta(days=wd, hours=hour, minutes=4, seconds=59)
    f = {
        "bar_time": bar_time, "hour": hour, "session": arm, "close": 100.0, "atr": 1.0,
        "cascade_strength": x["cascade"], "vol_z": x["vol_z"], "n_confirms": x["nc"],
        "decile": x["decile"], "liq_imbalance_30m": x["imb"],
        "burst_volume_30m": x["bvol"], "burst_events_30m": x["bev"],
    }
    engine = LiqBurstFollowEngine(bf2)            # no rest client -> OI fail-open (as the mirror)
    engine._features = lambda candles, st, f=f: dict(f)
    burst = {"volume_30m": x["bvol"], "events_30m": x["bev"], "imbalance_30m": x["imb"]}
    sig = asyncio.run(engine.evaluate("TESTUSDT", [], [], [], BurstFollowState(), burst=burst,
                                      btc_regime=regime, btc_regime_age_bars=x["age"], btc_regime_dist=None))
    shadow = SimpleNamespace(_market_ctx=ss.MarketContext(btc_trend_state=regime, btc_regime_age_bars=x["age"]))
    side = "LONG" if x["imb"] > 0 else "SHORT"
    reason = ss.SignalShadow._live_gate_block_reason(shadow, spec, f, side, x["imb"])
    return sig is not None, reason


@pytest.mark.parametrize("arm", ["london", "ny"])
@pytest.mark.parametrize("rule_updates,bf_updates", [
    ({}, {}),                                                         # live yaml as-is
    ({"min_imb": 0.9}, {}),                                           # NY-IMB90-style raise
    ({"min_n_confirms": 2, "min_cascade_strength": 0.5}, {}),
    ({"max_regime_age_bars": 10}, {"min_burst_volume_30m": 50_000.0, "min_burst_events_30m": 5}),
])
def test_mirror_equals_engine_on_every_gate(arm, rule_updates, bf_updates):
    ss, bf = _live()
    cell = _open_cell(bf.session_rules[arm], bf)
    grid = itertools.product(
        [0.55, 0.85, 0.95],        # imb
        [0, 1, 2],                 # n_confirms
        [0.0, 0.6],                # cascade_strength
        [-0.5, 0.5],               # vol_z
        [1, 3],                    # decile
        [(30_000.0, 4), (60_000.0, 8)],  # burst volume / events
        [None, 5, 12],             # btc_regime_age_bars
    )
    mismatches = []
    for imb, nc, casc, vz, dec, (bvol, bev), age in grid:
        x = dict(cell=cell, imb=imb, nc=nc, cascade=casc, vol_z=vz, decile=dec, bvol=bvol, bev=bev, age=age)
        engine_ok, reason = _decide(ss, bf, arm, rule_updates, bf_updates, x)
        if engine_ok != (reason is None):
            mismatches.append((x, engine_ok, reason))
    assert not mismatches, f"{len(mismatches)} mirror/engine mismatches, first: {mismatches[:3]}"


def test_n_confirms_non_null_on_every_insert(tmp_path):
    import research.signal_shadow as ss

    shadow = ss.SignalShadow(str(tmp_path / "shadow.db"), portfolio=ss.ShadowPortfolioConfig())
    for table in ("shadow_trades", "shadow_pending_entries"):
        cols = {r[1] for r in shadow.conn.execute(f"PRAGMA table_info({table})")}
        assert "n_confirms" in cols, table
    bar_time = datetime(2026, 9, 24, 16, 4, 59, 999000, tzinfo=timezone.utc)

    def f_for(nc):
        return {"bar_time": bar_time, "hour": 16, "session": "ny", "close": 100.0, "atr": 1.0,
                "atr_pct": 1.0, "decile": 1, "vol_z": 1.0, "cascade_strength": 0.3, "impulse_pct": 0.1,
                "v_confirms3": 1, "v_strict": 0, "n_confirms": nc,
                "burst_volume_30m": 60_000.0, "burst_events_30m": 8}

    by_name = ss._STRATEGY_BY_NAME
    # Thu h16 bull = an open NY cell, so n_confirms is the only gate that can bind below
    shadow.set_market_context(ss.MarketContext(btc_trend_state="bull"))
    # burst mirror, burst research, bar trigger, and a limit entry (queued -> filled)
    shadow._maybe_open_shadow_trade(by_name["ny_flush_buy_1h"], "AAAUSDT", f_for(0), "LONG", imb=0.95)
    shadow._maybe_open_shadow_trade(by_name["ny_flush_buy_1h"], "EEEUSDT", f_for(2), "LONG", imb=0.95)
    shadow._maybe_open_shadow_trade(by_name["ny_flush_buy_2h"], "BBBUSDT", f_for(2), "LONG", imb=0.95)
    shadow._maybe_open_shadow_trade(by_name["setup_fade"], "CCCUSDT", f_for(3), "SHORT", imb=0.95)
    shadow._maybe_open_shadow_trade(by_name["ny_flush_buy_4h_limit15"], "DDDUSDT", f_for(1), "LONG", imb=0.95)
    fill_bar = SimpleNamespace(high=100.0, low=90.0, close=95.0, close_time=bar_time + timedelta(minutes=5))
    shadow._process_pending_entries("DDDUSDT", fill_bar)

    rows = dict(shadow.conn.execute("SELECT symbol, n_confirms FROM shadow_trades").fetchall())
    assert set(rows) == {"AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT", "EEEUSDT"}, rows
    assert all(v is not None for v in rows.values()), rows
    assert rows["AAAUSDT"] == 0, "n_confirms=0 must be stored as 0 (NULL means unknown)"
    assert rows["DDDUSDT"] == 1, "limit fill must carry the signal bar's n_confirms"
    # the n_confirms=0 mirror row is a leg live rejects -> WLA must be 0
    wla = dict(shadow.conn.execute(
        "SELECT symbol, would_live_accept FROM shadow_trades WHERE strategy='ny_flush_buy_1h'").fetchall())
    assert wla == {"AAAUSDT": 0, "EEEUSDT": 1}, wla   # control leg (n_confirms=2) proves the cell is open
