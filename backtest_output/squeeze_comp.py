"""
Quick backtest: require_short_squeeze=True vs False
Focused on symbols that have cascade activity to keep runtime manageable.
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
    LiqClusterEngineV5, FLAT_RISK_PCT, DECILE_EXITS, CFG, V5Config
)
from core.models import Candle

KLINES_DB = Path("/root/bitana/backtest_data/klines_5m.db")
LIQ_DB = Path("/root/bitana/backtest_data/coinalyze_liq.db")

# Focus on symbols that actually have liq cascade activity
# (identified from the full run: WIF, IRYS, SIREN, DEXE, BERA, KAITO, ENJ, KITE, BLUAI, NEAR, ARB, ZEC, etc.)
SYMBOLS = [
    "NEARUSDT", "ARBUSDT", "ZECUSDT", "WIFUSDT", "IRYSUSDT",
    "SIRENUSDT", "BERAUSDT", "KAITOUSDT", "ENJUSDT", "KITEUSDT",
    "BLUAIUSDT", "DEXEUSDT", "RUNEUSDT", "EDGEUSDT", "MEGAUSDT",
    "RAVEUSDT", "ONTUSDT", "CRVUSDT", "APTUSDT", "DOTUSDT",
    "FETUSDT", "TAOUSDT", "SOLUSDT", "XRPUSDT", "PENDLEUSDT",
    "DASHUSDT", "WLDUSDT", "TONUSDT", "CHZUSDT", "QNTUSDT",
    "NMRUSDT", "UNIUSDT", "BNBUSDT", "1000LUNCUSDT", "1000PEPEUSDT",
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
        result.append({
            "date": date_str, "long_liq": d["long_liq"],
            "short_liq": d["short_liq"], "total_liq": d["long_liq"] + d["short_liq"],
        })

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


def run_backtest(require_short_squeeze: bool, label: str):
    import engines.liq_cluster_engine_v5 as eng
    old_cfg = eng.CFG
    eng.CFG = V5Config(require_short_squeeze=require_short_squeeze)
    engine = LiqClusterEngineV5()

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

    total_candles = sum(len(v) for v in all_klines.values())
    print(f"[{label}] {total_candles} candles, {len(SYMBOLS)} symbols, {len(trade_dates)} trade days")

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
                        if equity > peak:
                            peak = equity
                        trades.append({
                            "symbol": sym, "side": pos["side"],
                            "entry": pos["entry"], "exit": ep,
                            "pnl": pnl, "pnl_r": pnl_r,
                            "reason": er, "candles": pos["candles"], "decile": pos["decile"],
                        })
                        del open_positions[sym]
                        cooldowns[sym] = 36
                        if pnl_r <= -0.5:
                            stop_cooldowns[sym] = 3
                        continue

                if cooldowns[sym] > 0:
                    cooldowns[sym] -= 1
                    continue
                if stop_cooldowns[sym] > 0:
                    stop_cooldowns[sym] -= 1
                    continue
                if len(open_positions) >= MAX_POSITIONS:
                    continue

                sym_candles = [c for c in all_klines[sym] if c.open_time <= candle.open_time][-200:]
                if len(sym_candles) < 60:
                    continue

                sig = engine.evaluate(sym, sym_candles)
                if sig is None:
                    continue

                risk_amount = equity * FLAT_RISK
                stop_dist = abs(sig.entry_price - sig.stop_price)
                if stop_dist <= 0:
                    continue
                qty = risk_amount / stop_dist
                st = engine._get_state(sym)
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
            trades.append({
                "symbol": sym, "side": pos["side"],
                "entry": pos["entry"], "exit": lp,
                "pnl": pnl, "pnl_r": pnl_r,
                "reason": "end_of_data", "candles": pos["candles"], "decile": pos["decile"],
            })

    eng.CFG = old_cfg
    return trades, equity, peak


def _check_exit(pos, candle):
    if pos["side"] == "long":
        if candle.low <= pos["stop"]:
            return pos["stop"], "stop_loss"
        if candle.close > pos.get("highest", pos["entry"]):
            pos["highest"] = candle.close
        if pos["highest"] - candle.close > 0.03 * pos["entry"] and pos["highest"] > pos["entry"] * 1.005:
            return candle.close, "vol_trail"
    else:
        if candle.high >= pos["stop"]:
            return pos["stop"], "stop_loss"
        if candle.close < pos.get("lowest", pos["entry"]):
            pos["lowest"] = candle.close
        if candle.close - pos["lowest"] > 0.03 * pos["entry"] and pos["lowest"] < pos["entry"] * 0.995:
            return candle.close, "vol_trail"
    return None, None


def _calc_pnl(pos, exit_price):
    if pos["side"] == "long":
        return (exit_price - pos["entry"]) * pos["qty"]
    return (pos["entry"] - exit_price) * pos["qty"]


def analyze(trades, label, equity):
    if not trades:
        print(f"[{label}] No trades"); return None
    wins = [t for t in trades if t["pnl_r"] > 0]
    losses = [t for t in trades if t["pnl_r"] <= 0]
    total_r = sum(t["pnl_r"] for t in trades)
    gp = sum(t["pnl_r"] for t in wins) if wins else 0
    gl = abs(sum(t["pnl_r"] for t in losses)) if losses else 0
    pf = gp / gl if gl > 0 else float("inf")
    wr = len(wins) / len(trades) * 100

    eq_curve = [INITIAL_EQUITY]
    for t in trades:
        eq_curve.append(eq_curve[-1] + t["pnl"])
    peak_eq = eq_curve[0]
    max_dd = 0
    for eq in eq_curve:
        if eq > peak_eq: peak_eq = eq
        dd = (peak_eq - eq) / peak_eq * 100
        if dd > max_dd: max_dd = dd

    by_sym = defaultdict(lambda: {"trades": 0, "wins": 0, "total_r": 0})
    for t in trades:
        by_sym[t["symbol"]]["trades"] += 1
        by_sym[t["symbol"]]["total_r"] += t["pnl_r"]
        if t["pnl_r"] > 0: by_sym[t["symbol"]]["wins"] += 1

    print(f"\n[{label}]")
    print(f"  Trades: {len(trades)} (W:{len(wins)} L:{len(losses)})  WR:{wr:.1f}%")
    print(f"  Total R: {total_r:+.2f}R  PF: {pf:.2f}")
    print(f"  Equity: ${INITIAL_EQUITY:,.0f} → ${equity:,.0f} ({(equity/INITIAL_EQUITY-1)*100:+.1f}%)  MaxDD: {max_dd:.1f}%")
    print(f"  By symbol:")
    for sym, s in sorted(by_sym.items(), key=lambda x: -x[1]["total_r"]):
        wr_s = s["wins"]/s["trades"]*100 if s["trades"] > 0 else 0
        print(f"    {sym:15s}: {s['trades']:3d} tr, {s['total_r']:+.2f}R, {wr_s:.0f}% WR")

    return {"trades": len(trades), "wr": wr, "total_r": total_r, "pf": pf,
            "equity": equity, "max_dd": max_dd, "by_sym": dict(by_sym)}


if __name__ == "__main__":
    print("V5 Backtest: short_squeeze filter comparison")
    print(f"Period: {START_DATE.date()} to {END_DATE.date()}  Symbols: {len(SYMBOLS)}")

    t1, e1, p1 = run_backtest(True, "SHORT_SQ_ONLY")
    s1 = analyze(t1, "SHORT_SQ_ONLY", e1)

    t2, e2, p2 = run_backtest(False, "BOTH_DIRECTIONS")
    s2 = analyze(t2, "BOTH_DIRECTIONS", e2)

    if s1 and s2:
        print(f"\nCOMPARISON:")
        print(f"  Trades: {s1['trades']} → {s2['trades']} ({s2['trades']-s1['trades']:+d})")
        print(f"  WR:     {s1['wr']:.1f}% → {s2['wr']:.1f}% ({s2['wr']-s1['wr']:+.1f}%)")
        print(f"  TotalR: {s1['total_r']:+.2f} → {s2['total_r']:+.2f} ({s2['total_r']-s1['total_r']:+.2f})")
        print(f"  PF:     {s1['pf']:.2f} → {s2['pf']:.2f} ({s2['pf']-s1['pf']:+.2f})")
        print(f"  Equity: ${s1['equity']:,.0f} → ${s2['equity']:,.0f} (${s2['equity']-s1['equity']:+,.0f})")
        print(f"  MaxDD:  {s1['max_dd']:.1f}% → {s2['max_dd']:.1f}% ({s2['max_dd']-s1['max_dd']:+.1f}%)")

        new_syms = set(t["symbol"] for t in t2) - set(t["symbol"] for t in t1)
        if new_syms:
            print(f"\n  New symbols with entries:")
            for sym in sorted(new_syms):
                st = [t for t in t2 if t["symbol"] == sym]
                print(f"    {sym}: {len(st)} trades, {sum(t['pnl_r'] for t in st):+.2f}R")

    for name, tr in [("short_sq", t1), ("both", t2)]:
        if tr:
            with open(f"/root/bitana/backtest_output/squeeze_comp_{name}.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=tr[0].keys())
                w.writeheader()
                w.writerows(tr)
    print("\nCSVs saved.")
