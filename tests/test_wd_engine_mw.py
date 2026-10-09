"""Market-wide boost (owner order 2026-10-09; reports/wd_sizing_prereg.md design A3): rules.market_wide must equal the
research tag of record (research_cache/edge/improve_wd.py MKT) on the archive data, and the engine must place boosted
bids only after a market-wide hour, with the BTC-dump add-on at the 1x quantity. Offline: fake market + dry broker."""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from wd_engine import rules  # noqa: E402
from wd_engine.engine import Engine  # noqa: E402
from wd_engine.exchange import DryBroker, Filters  # noqa: E402

COINS20 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT", "LTCUSDT",
           "BCHUSDT", "ATOMUSDT", "NEARUSDT", "UNIUSDT", "FILUSDT", "ETCUSDT", "TRXUSDT", "BNBUSDT", "XLMUSDT", "AAVEUSDT"]
K5M = os.path.join(ROOT, "research_cache", "edge", "k5m")
UTC = timezone.utc


def test_market_wide_matches_research():
    if not all(os.path.exists(os.path.join(K5M, f"{s}.npy")) for s in COINS20):
        pytest.skip("no research 5m archive")
    hc = {}
    for s in COINS20:
        a = np.load(os.path.join(K5M, f"{s}.npy"))
        df = pd.DataFrame(a[:, 1:5], columns=["o", "h", "l", "c"], index=pd.to_datetime(a[:, 0], unit="ms", utc=True))
        hc[s] = df[~df.index.duplicated()].sort_index().c.resample("h").last()
    Hc = pd.DataFrame(hc).loc["2023-06-01":]
    lr = np.log(Hc).diff()
    z = lr / lr.rolling(720, min_periods=200).std().shift(1)                  # improve_wd.py, verbatim
    valid = z.notna().sum(axis=1)
    mkt = ((z <= -2).sum(axis=1) >= 0.5 * valid) & (valid >= 16)
    full = Hc.notna().all(axis=1).rolling(723).sum() == 723                   # whole window present for every coin
    pos = mkt[mkt & full].index
    rng = np.random.default_rng(7)
    neg = mkt[~mkt & full].index
    hours = list(pos) + list(neg[rng.choice(len(neg), size=min(300, len(neg)), replace=False)])
    assert len(pos) >= 20
    for h in hours:
        i = Hc.index.get_loc(h)
        closes = {s: Hc[s].iloc[i - 722:i + 1].tolist() for s in COINS20}
        flag, n_down, n_valid = rules.market_wide(closes)
        assert flag == bool(mkt[h]), (h, n_down, n_valid)
        assert n_valid == int(valid[h]) and n_down == int((z.loc[h] <= -2).sum())


# ----------------------------------------------------------------------------------------------- engine, offline
class FakeMarket:
    """Hourly bars ending at the hour that just closed; +/-0.25% bar ranges (ATR ~0.5% of price); `crash` coins drop 3%
    in the last hour (z << -2)."""

    def __init__(self, price: dict, crash: set):
        self.price, self.crash = price, crash
        self.last_h = int((datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)).timestamp() * 1000)
        self.closes = {}
        for s in COINS20:
            p0 = price.get(s, 10.0)
            r = 0.0005 * np.sin(np.arange(730))                                    # deterministic, hourly sd ~0.035%
            r[-1] = -0.03 if s in crash else 0.0003
            self.closes[s] = list(p0 * np.exp(np.cumsum(r) - np.cumsum(r)[-1]))   # last close = p0

    async def klines(self, symbol, interval, limit, spot=False):
        c = self.closes[symbol][-limit:]
        t0 = self.last_h - 3600000 * (len(c) - 1)
        return [(t0 + 3600000 * i, x, x * 1.0025, x * 0.9975, x) for i, x in enumerate(c)]

    async def last_price(self, symbol):
        return self.closes[symbol][-1]


def _engine(tmp_path, crash: set):
    cfg = dict(mode="dry", universe=["TRXUSDT", "BTCUSDT"], r_usd=1.0, stop_r=20, db=str(tmp_path / "t.db"),
               strategies={"wick": True, "discount": False}, wick_ladder=[5.0, 6.5, 8.0], wick_addon_btc_dump=-0.017,
               discount_btc_fall=-0.01, cooldown_h=24, max_open_legs=140, pause_file=str(tmp_path / "PAUSE"),
               status_file=str(tmp_path / "st.json"), telegram=False, wick_mw_mult=3, mw_coins=COINS20)
    eng = Engine(cfg)
    eng.market = FakeMarket({"TRXUSDT": 0.33, "BTCUSDT": 60000.0}, crash)
    eng.broker = DryBroker(eng.market)
    eng.filters = {"TRXUSDT": Filters("0.00001", "1", "1", 5.0), "BTCUSDT": Filters("0.1", "0.001", "0.001", 50.0)}
    return eng


def _bids(eng):
    return {(x["symbol"], x["rung"]): x for x in eng.store.live_legs() if x["state"] == "BID"}


def test_engine_boosts_only_after_market_wide_hour(tmp_path):
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    calm = _engine(tmp_path / "a", crash=set()); asyncio.run(calm.on_hour(hour))
    boom = _engine(tmp_path / "b", crash=set(COINS20[:12])); asyncio.run(boom.on_hour(hour))
    assert calm.mw[0] is False and boom.mw == (True, 12, 20)
    b0, b1 = _bids(calm), _bids(boom)
    # TRX: ladder in both; each rung 3x the quantity, r_usd 3, signal carries the multiplier
    for k in (5.0, 6.5, 8.0):
        q0, q1 = Decimal(b0[("TRXUSDT", k)]["qty"]), Decimal(b1[("TRXUSDT", k)]["qty"])
        assert q1 >= 3 * q0 - 2 and q1 <= 3 * q0 + 2
        assert b1[("TRXUSDT", k)]["r_usd"] == 3.0 and b0[("TRXUSDT", k)]["r_usd"] == 1.0
        assert '"mult": 3.0' in b1[("TRXUSDT", k)]["signal"]
    # BTC (1/3-R rungs under the $50 minimum here): single 5-ATR bid in both hours, the boosted one at 3x $/R
    assert set(k for s, k in b0 if s == "BTCUSDT") == {5.0} and b0[("BTCUSDT", 5.0)]["r_usd"] == 1.0
    assert b1[("BTCUSDT", 5.0)]["r_usd"] == 3.0
    assert Decimal(b1[("BTCUSDT", 5.0)]["qty"]) >= Decimal(b0[("BTCUSDT", 5.0)]["qty"])


def test_boosted_rung_addon_stays_1x(tmp_path):
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    eng = _engine(tmp_path, crash=set(COINS20[:12])); asyncio.run(eng.on_hour(hour))
    leg = _bids(eng)[("TRXUSDT", 5.0)]
    bar_close = datetime.now(UTC).replace(second=0, microsecond=0)
    fb_open = int((bar_close - timedelta(minutes=5)).timestamp() * 1000)
    sig = json.loads(leg["signal"]); sig["btc_close_h"] = 100.0; sig = json.dumps(sig)
    eng.store.update(leg["id"], state="OPEN", entry=0.32, addon="pending", addon_at=bar_close.isoformat(), signal=sig,
                     expires=(bar_close + timedelta(hours=24)).isoformat())
    asyncio.run(eng.addons(bar_close, {fb_open: 98.0}))                     # BTC -2% vs the bid-hour close -> add-on
    add = [x for x in eng.store.live_legs() if x["parent"] == leg["id"]]
    assert len(add) == 1
    assert Decimal(add[0]["qty"]) == rules.floor_to(Decimal(leg["qty"]) / 3, "1")
    assert add[0]["r_usd"] == pytest.approx(1.0)


def test_market_wide_fail_safe(tmp_path):
    eng = _engine(tmp_path, crash=set(COINS20))

    async def broken(*a, **k):
        raise OSError("down")
    eng.market.klines = broken
    asyncio.run(eng.refresh_mw())
    assert eng.mw == (False, 0, 0)
