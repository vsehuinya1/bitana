"""WLA-SYMBOLS (2026-09-26): the live-mirror book only admits symbols the live bot trades (symbols.active).

Before: untracked shadow symbols went WLA=1 and held cluster/concurrency slots, crowding out live symbols the bot
actually filled (Sep-25 London b13:45: PEPE, XRP, TAO held the 3 slots; live filled SOL, stamped WLA=0).
"""
from __future__ import annotations

import research.signal_shadow as ss


def _shadow(tmp_path):
    return ss.SignalShadow(str(tmp_path / "shadow.db"), portfolio=ss.ShadowPortfolioConfig(live_max_concurrent=8))


def test_bound_from_the_live_yaml():
    ss._load_live_gate_snapshots()
    assert ss._LIVE_SYMBOLS and "ETHUSDT" in ss._LIVE_SYMBOLS


def test_untracked_symbol_never_live_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "_LIVE_SYMBOLS", frozenset({"SOLUSDT", "XRPUSDT"}))
    sh = _shadow(tmp_path)
    assert sh._would_live_accept("burst_follow", "PEPEUSDT", "LONG", "london", "2026-09-25T13:45:00+00:00") == 0
    assert sh._would_live_accept("burst_follow", "SOLUSDT", "LONG", "london", "2026-09-25T13:45:00+00:00") == 1


def test_untracked_rows_cannot_crowd_out(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "_LIVE_SYMBOLS", frozenset({"SOLUSDT", "XRPUSDT"}))
    monkeypatch.setattr(ss, "_LIVE_CLUSTER_WINDOW_MIN", 15)
    sh = _shadow(tmp_path)
    for sym in ("1000PEPEUSDT", "XRPUSDT", "TAOUSDT"):
        ok = sh._would_live_accept("burst_follow", sym, "LONG", "london", "2026-09-25T13:45:00+00:00")
        sh.conn.execute("INSERT INTO shadow_trades (strategy, symbol, side, session, cluster_bucket, status, "
                        "would_live_accept) VALUES ('burst_follow', ?, 'LONG', 'london', "
                        "'2026-09-25T13:45:00+00:00', 'open', ?)", (sym, ok))
    sh.conn.commit()
    assert sh._would_live_accept("burst_follow", "SOLUSDT", "LONG", "london", "2026-09-25T13:45:00+00:00") == 1


def test_research_strategies_unfiltered(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "_LIVE_SYMBOLS", frozenset({"SOLUSDT"}))
    sh = _shadow(tmp_path)
    assert sh._would_live_accept("ny_flush_buy_4h", "PEPEUSDT", "LONG", "ny", "2026-09-25T14:00:00+00:00") == 1
