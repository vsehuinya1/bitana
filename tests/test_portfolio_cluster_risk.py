"""
Tests for the per-cluster aggregate-risk cap (2026-08-31, owner order).

PortfolioManager.get_cluster_risk_multiplier: sizing-only cap on the SUM of
remaining risk-to-stop across open positions sharing engine+session+side+
15-min cluster bucket. Legs are sized down to fit; 0 = budget exhausted.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.loader import PortfolioConfig
from core.models import EngineType, Position, PositionState, Side, Signal
from risk.portfolio_manager import PortfolioManager

CFG = PortfolioConfig(max_cluster_positions=3, max_cluster_risk_pct=15.0)
PM = PortfolioManager(CFG)

ENGINE = EngineType.LIQ_BURST_FOLLOW
BUCKET = "2026-08-31T05:15:00+00:00"
SIG_META = {"session": "asia", "cluster_bucket": BUCKET}


def signal(side=Side.SHORT, meta=None):
    return Signal(engine=ENGINE, symbol="ETHUSDT", side=side,
                  entry_price=100.0, stop_price=110.0,  # SHORT: stop above
                  signal_data=dict(SIG_META, **(meta or {})))


def position(symbol="XRPUSDT", side=Side.SHORT, risk_usd=875.0, state=PositionState.MANAGING,
             meta=None, entry=100.0, stop=110.0):
    # SHORT: stop above entry -> risk distance = stop-entry
    return Position(trade_uuid="t-" + symbol, symbol=symbol, side=side, engine=ENGINE,
                    state=state, entry_price=entry, stop_price=stop,
                    quantity=risk_usd / abs(stop - entry),
                    signal_data=dict(SIG_META, **(meta or {})))


EQUITY = 10_000.0  # 8.75% leg = $875 risk


class TestClusterRiskMultiplier:
    def test_off_when_cap_zero(self):
        pm = PortfolioManager(PortfolioConfig(max_cluster_risk_pct=0.0))
        assert pm.get_cluster_risk_multiplier(signal(), [position()], EQUITY, 8.75) == 1.0

    def test_no_open_cluster_risk(self):
        assert PM.get_cluster_risk_multiplier(signal(), [], EQUITY, 8.75) == 1.0

    def test_signal_without_cluster_metadata(self):
        assert PM.get_cluster_risk_multiplier(signal(meta={"cluster_bucket": None}), [position()], EQUITY, 8.75) == 1.0

    def test_second_leg_sized_down_to_cap(self):
        # one open leg at $875 (8.75%) -> remaining 6.25% -> mult 6.25/8.75
        m = PM.get_cluster_risk_multiplier(signal(), [position()], EQUITY, 8.75)
        assert abs(m - (15.0 - 8.75) / 8.75) < 1e-9

    def test_partial_fill_counts_pro_rata(self):
        # half-quantity open leg -> 4.375% open risk -> remaining 10.625% > base
        # 8.75% -> clamped to 1.0 (never sizes UP)
        m = PM.get_cluster_risk_multiplier(signal(), [position(risk_usd=437.5)], EQUITY, 8.75)
        assert m == 1.0

    def test_budget_exhausted_returns_zero(self):
        # two open legs = $1750 (17.5%) > cap -> 0 (entry skipped upstream)
        m = PM.get_cluster_risk_multiplier(signal(), [position(), position(symbol="SOLUSDT")], EQUITY, 8.75)
        assert m == 0.0

    def test_closed_positions_do_not_consume_budget(self):
        m = PM.get_cluster_risk_multiplier(signal(), [position(state=PositionState.CLOSED)], EQUITY, 8.75)
        assert m == 1.0

    def test_other_bucket_does_not_consume_budget(self):
        other = position(meta={"cluster_bucket": "2026-08-31T05:30:00+00:00"})
        assert PM.get_cluster_risk_multiplier(signal(), [other], EQUITY, 8.75) == 1.0

    def test_other_side_does_not_consume_budget(self):
        assert PM.get_cluster_risk_multiplier(signal(), [position(side=Side.LONG, entry=100.0, stop=90.0)], EQUITY, 8.75) == 1.0

    def test_other_engine_does_not_consume_budget(self):
        p = position()
        p.engine = EngineType.SWING_BREAK
        assert PM.get_cluster_risk_multiplier(signal(), [p], EQUITY, 8.75) == 1.0

    def test_trailing_stop_reduces_open_risk(self):
        # opened at $875 risk (qty 87.5, distance 10); stop trails to distance 5
        # -> remaining risk 5 x 87.5 = $437.5 = 4.375% -> remaining 10.625% > base
        # -> clamped to 1.0
        m = PM.get_cluster_risk_multiplier(signal(), [position(entry=100.0, stop=105.0, risk_usd=437.5)], EQUITY, 8.75)
        assert m == 1.0

    def test_leg_risk_floor_still_caps(self):
        # tiny new-leg risk still clamps to 1.0 (no size-up)
        m = PM.get_cluster_risk_multiplier(signal(), [position()], EQUITY, 2.0)
        assert m == 1.0
