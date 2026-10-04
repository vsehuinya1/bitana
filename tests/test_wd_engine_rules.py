"""wd_engine.rules must reproduce the paper readers of record (research/wick_catcher_reader.py, perp_discount_reader.py)
on the cached forward klines (logs/paper_cache, public data); plus sizing/rounding and the dry broker's fill logic."""
import os
import sys
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.append(os.path.join(ROOT, "research"))
from wd_engine import rules  # noqa: E402
from wd_engine.exchange import DryBroker  # noqa: E402

CACHE = os.path.join(ROOT, "logs", "paper_cache")
COINS = ["BTCUSDT", "TRXUSDT", "LINKUSDT", "ATOMUSDT", "ADAUSDT"]


def _cached(market, sym):
    fn = os.path.join(CACHE, f"{market}_{sym}_5m_v2.pkl")
    if not os.path.exists(fn):
        pytest.skip(f"no cache {fn}")
    return pd.read_pickle(fn)


@pytest.mark.parametrize("sym", COINS)
def test_wick_levels_match_reader(sym):
    df = _cached("perp", sym).iloc[-6000:]
    H = df.resample("h").agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
    tr = np.maximum(H.h, H.c.shift(1)) - np.minimum(H.l, H.c.shift(1))
    atr_reader = tr.rolling(14).mean()                                   # wick_catcher_reader.simulate
    bars = list(H[["o", "h", "l", "c"]].itertuples(index=False, name=None))
    n = 0
    for i in range(15, len(bars)):
        a = rules.atr1h(bars[i - 15:i + 1])
        assert a == pytest.approx(atr_reader.iloc[i], rel=1e-12)
        L = rules.wick_bid(bars[i][3], a)
        if atr_reader.iloc[i] / H.c.iloc[i] < 0.001:
            assert L is None
        else:
            assert L == pytest.approx(H.c.iloc[i] - 5 * atr_reader.iloc[i], rel=1e-12)
        n += 1
    assert n > 100


def test_wick_forward_fills_have_engine_levels():
    import wick_catcher_reader as wcr
    for sym in COINS:
        df = _cached("perp", sym)
        for r in wcr.simulate(df, wcr.FORWARD_FROM, sym=sym):
            H = df.resample("h").agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
            hr = r["t"].floor("h") - pd.Timedelta(hours=1)                 # the bid hour
            bars = list(H.loc[:hr, ["o", "h", "l", "c"]].itertuples(index=False, name=None))[-15:]
            a = rules.atr1h(bars)
            assert a == pytest.approx(r["r_unit"] * r["fill"] / 3, rel=1e-9)
            assert bars[-1][3] == pytest.approx(r["ref"])
            assert r["fill"] <= rules.wick_bid(r["ref"], a) + 1e-12


def test_discount_signals_match_reader():
    import perp_discount_reader as pdr
    found = 0
    for sym in ["TRXUSDT", "ATOMUSDT", "BTCUSDT", "XRPUSDT"]:
        p, sp = _cached("perp", sym), _cached("spot", sym)
        j = p[["c"]].join(sp[["c"]].rename(columns={"c": "sc"}), how="inner")
        b = (j.c / j.sc - 1).values
        mine = [j.index[i + 1] for i in range(1, len(b) - 1) if rules.disc_signal(b[i - 1], b[i])]
        theirs = [r["t"] for r in pdr.simulate(p, sp, j.index[0])]
        assert set(theirs) <= set(mine)                                     # reader also applies one-position-per-coin
        found += len(theirs)
    assert found >= 1


def test_sizing_and_rounding():
    q = rules.qty_for_r(1.0, 0.004, "1")                                    # TRX-like: ATR 0.004 -> 1R = 0.012/unit
    assert q == Decimal("83") and float(q) * 3 * 0.004 <= 1.0
    assert rules.floor_to(13.31789, "0.001") == Decimal("13.317")
    assert rules.ceil_to(13.31711, "0.001") == Decimal("13.318")
    assert rules.fmt_dec(Decimal("83.000")) == "83" and rules.fmt_dec(Decimal("0.0100")) == "0.01"
    assert rules.stop_price(100.0, 1.0, 12) == pytest.approx(64.0)
    assert rules.disc_signal(-0.002, -0.003) and not rules.disc_signal(-0.004, -0.005) and not rules.disc_signal(None, -0.01)


def test_dry_broker_fill_rules():
    br = DryBroker(market=None)
    from wd_engine.exchange import _DryOrder
    br.orders = {"b": _DryOrder("X", "algo", "TAKE_PROFIT", "BUY", 1, 10.0, 10.0),
                 "s": _DryOrder("X", "algo", "STOP_MARKET", "SELL", 1, 8.0, None),
                 "t": _DryOrder("X", "algo", "TAKE_PROFIT_MARKET", "SELL", 1, 12.0, None, min_bar_ms=10**15)}
    assert br.on_bar("X", 1, 10.5, 10.6, 9.9, 10.2) == ["b"] and br.orders["b"].avg == 10.0
    assert br.on_bar("X", 2, 7.5, 7.9, 7.0, 7.2) == ["s"] and br.orders["s"].avg == 7.5     # gap through the stop: open
    assert br.on_bar("X", 3, 12.5, 13.0, 12.4, 12.8) == []                                  # bar before min_bar_ms ignored


def test_v2_rules():
    # ladder rungs: depth k x ATR; v1 default stays k=5
    assert rules.wick_bid(100.0, 1.0) == 95.0 and rules.wick_bid(100.0, 1.0, 8.0) == 92.0
    # 1/3 R rung: TRX-like ATR 0.004 at $1/R -> 27 units; LINK-like at $20 minimum -> bumped to the minimum
    q, b = rules.rung_qty(1.0, 1 / 3, 0.004, "1", 0.33, 5.0)
    assert q == Decimal("27") and not b
    q, b = rules.rung_qty(1.0, 1 / 3, 0.17, "0.01", 13.0, 20.0)
    assert b and float(q) * 13.0 >= 20.0 and q == Decimal("1.54")
    # add-on: BTC -1.7% from the bid-hour close to the fill-bar close; discount filter: BTC -1% over 60 min
    assert rules.btc_dump(100.0, 98.3) and not rules.btc_dump(100.0, 98.4) and not rules.btc_dump(None, 90.0)
    assert rules.btc_falling(99.0, 100.0) and not rules.btc_falling(99.1, 100.0) and not rules.btc_falling(99.0, None)
