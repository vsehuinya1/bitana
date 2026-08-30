"""Regression: live read-only liq feed must supply >= CFG.liq_min_lookback days.

2026-08-30 bug: ForceOrderPipeline.LIQ_CACHE_MAX_DAYS was 7 while
CascadeTracker.update() requires CFG.liq_min_lookback = 30 daily rows before it
returns any non-zero state — the live engine's liq_direction_imb / cascade
state was permanently 0.0, so the asia neg_imb gate (min_imb 0.5) could never
pass and burst/cascade gates never opened (0 live asia/burst signals outside
the 2026-08-28 liq storm). Fix: align to 120d (same as tools/v5_forward_test.py).
"""
from datetime import datetime, timedelta, timezone

from data.force_order_pipeline import ForceOrderPipeline
from engines.liq_cluster_engine_v5 import LiqClusterEngineV5

SYM = "TESTUSDT"


def _daily_rows(n_days: int) -> list[dict]:
    """n daily liq rows ending today; closes rise 1%/day; last day short-dominant."""
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(n_days):
        d = (now - timedelta(days=n_days - 1 - i)).strftime("%Y-%m-%d")
        long_liq = 100.0
        short_liq = 1_000_000.0 if i == n_days - 1 else 100.0
        rows.append({
            "date": d,
            "total_liq": long_liq + short_liq,
            "long_liq": long_liq,
            "short_liq": short_liq,
            "close": 100.0 * (1.01 ** i),
        })
    return rows


def _pipeline_with_rows(rows: list[dict]) -> ForceOrderPipeline:
    p = ForceOrderPipeline.__new__(ForceOrderPipeline)  # skip __init__ (no IO)
    p.read_only = True
    p.symbols = {SYM}

    class _FakeConn:
        def execute(self, query, params=()):
            class _Cur:
                def fetchall(self):
                    return rows
            return _Cur()

    p.liq_conn = _FakeConn()
    p.cascade_engine = LiqClusterEngineV5()
    return p


def test_short_feed_returns_zero_state():
    """7 days < liq_min_lookback(30): tracker must return the zero state."""
    p = _pipeline_with_rows(_daily_rows(7))
    p._feed_engine_liq(SYM)
    st = p.cascade_engine._get_state(SYM)
    assert st.liq_direction_imb == 0.0
    assert st.cascade_strength == 0.0
    assert st.ret_5d == 0.0


def test_full_feed_computes_state():
    """120 days (the fix): state must be computed, imb negative, ret_5d positive."""
    p = _pipeline_with_rows(_daily_rows(120))
    p._feed_engine_liq(SYM)
    st = p.cascade_engine._get_state(SYM)
    assert st.liq_direction_imb < 0.0, "last day is short-dominant, imb must be negative"
    assert st.ret_5d > 0.0, "closes rise 1%/day, 5d return must be positive"


def test_live_constant_covers_min_lookback():
    """The constant itself must exceed the tracker's minimum history."""
    from engines.liq_cluster_engine_v5 import CFG
    from data.force_order_pipeline import LIQ_CACHE_MAX_DAYS
    assert LIQ_CACHE_MAX_DAYS >= CFG.liq_min_lookback
