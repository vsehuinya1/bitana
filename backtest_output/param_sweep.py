"""
Backtest: Test specific parameter changes one at a time.
Uses the v5_full_backtest infrastructure with targeted overrides.
"""
import sqlite3
import numpy as np
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import csv
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from engines.liq_cluster_engine_v5 import (
    LiqClusterEngineV5, _compute_aggression, _score_to_decile,
    FLAT_RISK_PCT, DECILE_EXITS, CFG, V5Config, TRADE_DECILES
)
from core.models import Candle

KLINES_DB = Path("/root/bitana/backtest_data/klines_5m.db")
LIQ_DB = Path("/root/bitana/backtest_data/coinalyze_liq.db")

SYMBOLS = [
    "NEARUSDT", "ZECUSDT", "ADAUSDT", "WLDUSDT", "UNIUSDT", "NMRUSDT",
    "PENDLEUSDT", "ARBUSDT", "RENDERUSDT", "RUNEUSDT", "FETUSDT", "DOTUSDT",
    "TONUSDT", "SOLUSDT", "1000LUNCUSDT", "ENAUSDT", "1000PEPEUSDT",
    "XRPUSDT", "FILUSDT", "BNBUSDT", "TAOUSDT", "CHZUSDT", "DASHUSDT",
    "QNTUSDT", "ICPUSDT", "XLMUSDT", "APTUSDT", "ETHUSDT",
]

WARMUP_DAYS = 30
START_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
END_DATE = datetime(2026, 5, 20, 23, 59, 59, tzinfo=timezone.utc)
WARMUP_END = START_DATE + timedelta(days=WARMUP_DAYS)
INITIAL_EQUITY = 10000.0
FLAT_RISK = 0.04
MAX_POSITIONS = 15
MAX_PER_SYMBOL = 1


def load_klines(symbol, start_ms, end_ms):
    conn = sqlite3.connect(str(KLINES_DB))
    cur = conn.cursor()
    cur.execute(
        "SELECT open_time, close_time, open, high, low, close, volume, taker_buy_volume "
        "FROM klines WHERE symbol=? AND open_time >= ? AND open_time <= ? ORDER BY open_time",
        (symbol, start_ms, end_ms)
    )
    rows = cur.fetchall()
    conn.close()
    return [Candle(
        symbol=symbol, timeframe="5m",
        open_time=datetime.fromtimestamp(r[0]/1000, tz=timezone.utc),
        close_time=datetime.fromtimestamp(r[1]/1000, tz=timezone.utc),
        open=r[2], high=r[3], low=r[4], close=r[5],
        volume=r[6], taker_buy_volume=r[7], is_closed=True,
    ) for r in rows]


def load_liq_history(symbol):
    conn = sqlite3.connect(str(LIQ_DB))
    cur = conn.cursor()
    for sym_try in [symbol, f"{symbol}_PERP.A"]:
        cur.execute(
            "SELECT timestamp, long_liq, short_liq FROM liquidation_history "
            "WHERE symbol=? ORDER BY timestamp", (sym_try,)
        )
        rows = cur.fetchall()
        if rows:
            break
    conn.close()
    if not rows:
        return []
    daily = defaultdict(lambda: {"long_liq": 0.0, "short_liq": 0.0})
    for ts, ll, sl in rows:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        daily[dt]["long_liq"] += ll
        daily[dt]["short_liq"] += sl
    result = []
    for date_str in sorted(daily.keys()):
        d = daily[date_str]
        result.append({"date": date_str, "long_liq": d["long_liq"], "short_liq": d["short_liq"], "total_liq": d["long_liq"] + d["short_liq"]})
    conn = sqlite3.connect(str(LIQ_DB))
    cur = conn.cursor()
    for sym_try in [symbol, f"{symbol}_PERP.A"]:
        cur.execute("SELECT date, close FROM daily_closes WHERE symbol=? ORDER BY date", (sym_try,))
        rows = cur.fetchall()
        if rows:
            break
    conn.close()
    close_map = {r[0]: r[1] for r in rows}
    for r in result:
        r["close"] = close_map.get(r["date"], 0)
    return result


def run_backtest(label, trade_deciles=None, max_consecutive_stops=None, ret5d_min=None, cooldown_bars=None):
    """Run backtest with optional parameter overrides."""
    import engines.liq_cluster_engine_v5 as eng
    old_cfg = eng.CFG
    kwargs = {}
    if trade_deciles is not None:
        kwargs['trade_deciles'] = trade_deciles  # not directly in V5Config, handled separately
    if max_consecutive_stops is not None:
        eng.CFG = V5Config(
            require_short_squeeze=False,
            max_consecutive_stops=max_consecutive_stops,
            ret5d_min=ret5d_min if ret5d_min is not None else eng.CFG.ret5d_min,
            cooldown_bars=cooldown_bars if cooldown_bars is not None else eng.CFG.cooldown_bars,
        )
    engine = LiqClusterEngineV5()

    effective_deciles = trade_deciles if trade_deciles is not None else TRADE_DECILES

    equity = INITIAL_EQUITY
    peak = equity
    trades = []
    open_positions = {}
    cooldowns = defaultdict(int)
    stop_cooldowns = defaultdict(int)

    all_klines = {}
    all_liq = {}
    start_ms = int(START_DATE.timestamp() * 1000)
    end_ms = int(END_DATE.timestamp() * 1000)

    for sym in SYMBOLS:
        all_klines[sym] = load_klines(sym, start_ms, end_ms)
        all_liq[sym] = load_liq_history(sym)

    all_dates = set()
    for sym in SYMBOLS:
        for row in all_liq[sym]:
            all_dates.add(row["date"])
    sorted_dates = sorted(all_dates)
    warmup_date_set = set(d for d in sorted_dates
                          if datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc) <= WARMUP_END)
    trade_dates = [d for d in sorted_dates if d not in warmup_date_set]

    candles_by_sym_date = defaultdict(lambda: defaultdict(list))
    for sym in SYMBOLS:
        for c in all_klines.get(sym, []):
            candles_by_sym_date[sym][c.open_time.strftime("%Y-%m-%d")].append(c)

    # Warmup
    for date_str in sorted_dates:
        if date_str in warmup_date_set:
            for sym in SYMBOLS:
                sym_liq = [r for r in all_liq[sym] if r["date"] == date_str]
                if sym_liq:
                    engine.update_daily_liq(sym, sym_liq)

    # Trading
    for date_str in trade_dates:
        for sym in SYMBOLS:
            sym_liq = [r for r in all_liq[sym] if r["date"] == date_str]
            if sym_liq:
                engine.update_daily_liq(sym, sym_liq)

        for sym in SYMBOLS:
            for candle in candles_by_sym_date[sym].get(date_str, []):
                # Manage positions
                if sym in open_positions:
                    pos = open_positions[sym]
                    pos["candles"] = pos.get("candles", 0) + 1
                    ep, er = _check_exit(pos, candle)
                    if ep is not None:
                        pnl = _calc_pnl(pos, ep)
                        pnl_r = pnl / (INITIAL_EQUITY * FLAT_RISK)
                        equity += pnl
                        if equity > peak: peak = equity
                        trades.append({
                            "symbol": sym, "side": pos["side"],
                            "entry": pos["entry"], "exit": ep,
                            "pnl": pnl, "pnl_r": pnl_r,
                            "reason": er, "candles": pos["candles"], "decile": pos["decile"],
                        })
                        del open_positions[sym]
                        cooldowns[sym] = cooldown_bars if cooldown_bars is not None else 36
                        if pnl_r <= -0.5:
                            stop_cooldowns[sym] = 3
                        continue

                if cooldowns[sym] > 0: cooldowns[sym] -= 1; continue
                if stop_cooldowns[sym] > 0: stop_cooldowns[sym] -= 1; continue
                if len(open_positions) >= MAX_POSITIONS: continue

                sym_candles = [c for c in all_klines[sym] if c.open_time <= candle.open_time][-200:]
                if len(sym_candles) < 60: continue

                sig = engine.evaluate(sym, sym_candles)
                if sig is None: continue

                # Check decile filter
                st = engine._get_state(sym)
                if st.decile not in effective_deciles: continue

                risk_amount = equity * FLAT_RISK
                stop_dist = abs(sig.entry_price - sig.stop_price)
                if stop_dist <= 0: continue
                qty = risk_amount / stop_dist

                open_positions[sym] = {
                    "side": sig.side.value, "entry": sig.entry_price,
                    "stop": sig.stop_price, "qty": qty, "candles": 0,
                    "decile": st.decile, "highest": sig.entry_price, "lowest": sig.entry_price,
                }

    for sym, pos in open_positions.items():
        last = all_klines.get(sym, [])
        if last:
            lp = last[-1].close
            pnl = _calc_pnl(pos, lp)
            pnl_r = pnl / (INITIAL_EQUITY * FLAT_RISK)
            equity += pnl
            trades.append({"symbol": sym, "side": pos["side"], "entry": pos["entry"], "exit": lp,
                          "pnl": pnl, "pnl_r": pnl_r, "reason": "end_of_data", "candles": pos["candles"], "decile": pos["decile"]})

    eng.CFG = old_cfg
    return trades, equity, peak


def _check_exit(pos, candle):
    if pos["side"] == "long":
        if candle.low <= pos["stop"]: return pos["stop"], "stop_loss"
        if candle.close > pos.get("highest", pos["entry"]): pos["highest"] = candle.close
        if pos["highest"] - candle.close > 0.03 * pos["entry"] and pos["highest"] > pos["entry"] * 1.005:
            return candle.close, "vol_trail"
    else:
        if candle.high >= pos["stop"]: return pos["stop"], "stop_loss"
        if candle.close < pos.get("lowest", pos["entry"]): pos["lowest"] = candle.close
        if candle.close - pos["lowest"] > 0.03 * pos["entry"] and pos["lowest"] < pos["entry"] * 0.995:
            return candle.close, "vol_trail"
    return None, None


def _calc_pnl(pos, exit_price):
    if pos["side"] == "long": return (exit_price - pos["entry"]) * pos["qty"]
    return (pos["entry"] - exit_price) * pos["qty"]


def analyze(trades, label, equity):
    if not trades: print(f"[{label}] No trades"); return None
    wins = [t for t in trades if t["pnl_r"] > 0]
    losses = [t for t in trades if t["pnl_r"] <= 0]
    total_r = sum(t["pnl_r"] for t in trades)
    gp = sum(t["pnl_r"] for t in wins) if wins else 0
    gl = abs(sum(t["pnl_r"] for t in losses)) if losses else 0
    pf = gp / gl if gl > 0 else float("inf")
    wr = len(wins) / len(trades) * 100
    eq_curve = [INITIAL_EQUITY]
    for t in trades: eq_curve.append(eq_curve[-1] + t["pnl"])
    peak_eq = eq_curve[0]; max_dd = 0
    for eq in eq_curve:
        if eq > peak_eq: peak_eq = eq
        dd = (peak_eq - eq) / peak_eq * 100
        if dd > max_dd: max_dd = dd

    print(f"[{label}] Trades:{len(trades)} W:{len(wins)} L:{len(losses)} WR:{wr:.1f}% R:{total_r:+.2f} PF:{pf:.2f} Eq:${equity:,.0f} DD:{max_dd:.1f}%")
    return {"trades": len(trades), "wr": wr, "total_r": total_r, "pf": pf, "equity": equity, "max_dd": max_dd}


if __name__ == "__main__":
    print("V5 Parameter Sweep — Jan 1 to May 20, 2026\n")

    # Baseline (current settings)
    t1, e1, p1 = run_backtest("BASELINE (D1-D3,D5-D9 | sq=False | stops=3)")
    s1 = analyze(t1, "BASELINE", e1)

    # Test 1: Exclude D3 (negative expectancy)
    t2, e2, p2 = run_backtest("NO_D3 (D1-D2,D5-D9)", trade_deciles={1,2,5,6,7,8,9})
    s2 = analyze(t2, "NO_D3", e2)

    # Test 2: Exclude D3 + D7 (D7 also weak: +0.066R avg)
    t3, e3, p3 = run_backtest("NO_D3_D7 (D1-D2,D5-D6,D8-D9)", trade_deciles={1,2,5,6,8,9})
    s3 = analyze(t3, "NO_D3_D7", e3)

    # Test 3: Increase consecutive stops tolerance (3→5)
    t4, e4, p4 = run_backtest("STOPS_5 (max_consecutive=5)", max_consecutive_stops=5)
    s4 = analyze(t4, "STOPS_5", e4)

    # Test 4: Tighter ret5d_min (-5% → 0%)
    t5, e5, p5 = run_backtest("RET5D_0 (ret5d_min=0)", ret5d_min=0.0)
    s5 = analyze(t5, "RET5D_0", e5)

    # Test 5: Longer cooldown (36→72 candles = 6h)
    t6, e6, p6 = run_backtest("COOLDOWN_72", cooldown_bars=72)
    s6 = analyze(t6, "COOLDOWN_72", e6)

    # Test 6: Best combo (NO_D3 + STOPS_5 + RET5D_0)
    t7, e7, p7 = run_backtest("COMBO (NO_D3+STOPS_5+RET5D_0)", trade_deciles={1,2,5,6,7,8,9}, max_consecutive_stops=5, ret5d_min=0.0)
    s7 = analyze(t7, "COMBO", e7)

    # Summary comparison
    print(f"\n{'='*80}")
    print(f"{'CONFIG':<30} {'TR':>4} {'WR%':>6} {'TOTAL_R':>8} {'PF':>6} {'EQUITY':>12} {'DD%':>6}")
    print(f"{'-'*80}")
    for s in [s1,s2,s3,s4,s5,s6,s7]:
        if s:
            print(f"{s.get('label','?'):<30} {s['trades']:>4} {s['wr']:>5.1f}% {s['total_r']:>+8.2f} {s['pf']:>6.2f} ${s['equity']:>11,.0f} {s['max_dd']:>5.1f}%")
