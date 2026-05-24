"""
Backtest: require_short_squeeze=True vs False
Tests the impact of allowing long-squeeze cascades (not just short squeezes).
Uses the same V5 engine, data, and parameters — only toggles one flag.
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
    FLAT_RISK_PCT, DECILE_EXITS, CFG, V5Config
)
from core.models import Candle

KLINES_DB = Path("/root/bitana/backtest_data/klines_5m.db")
LIQ_DB = Path("/root/bitana/backtest_data/coinalyze_liq.db")

# Use the 35 symbols from v5 config
SYMBOLS = [
    "RUNEUSDT", "BLUAIUSDT", "BERAUSDT", "SIRENUSDT", "EDGEUSDT",
    "MEGAUSDT", "KITEUSDT", "ONTUSDT", "KAITOUSDT", "RAVEUSDT",
    "ENJUSDT", "CRVUSDT", "IRYSUSDT", "DEXEUSDT", "WIFUSDT",
    "ARBUSDT", "APTUSDT", "DOTUSDT", "FETUSDT", "TAOUSDT",
    "NEARUSDT", "SOLUSDT", "XRPUSDT", "PENDLEUSDT", "DASHUSDT",
    "WLDUSDT", "TONUSDT", "CHZUSDT", "QNTUSDT", "NMRUSDT",
    "UNIUSDT", "BNBUSDT", "1000LUNCUSDT", "ZECUSDT", "1000PEPEUSDT",
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
    candles = []
    for r in rows:
        candles.append(Candle(
            symbol=symbol, timeframe="5m",
            open_time=datetime.fromtimestamp(r[0]/1000, tz=timezone.utc),
            close_time=datetime.fromtimestamp(r[1]/1000, tz=timezone.utc),
            open=r[2], high=r[3], low=r[4], close=r[5],
            volume=r[6], taker_buy_volume=r[7], is_closed=True,
        ))
    return candles


def load_liq_history(symbol):
    """Load liq history and aggregate to daily. Handles both raw and _PERP.A symbol names."""
    conn = sqlite3.connect(str(LIQ_DB))
    cur = conn.cursor()
    # Try with and without _PERP.A suffix
    for sym_try in [symbol, f"{symbol}_PERP.A"]:
        cur.execute(
            "SELECT timestamp, long_liq, short_liq FROM liquidation_history "
            "WHERE symbol=? ORDER BY timestamp",
            (sym_try,)
        )
        rows = cur.fetchall()
        if rows:
            break
    conn.close()

    if not rows:
        return []

    # Aggregate to daily
    daily = defaultdict(lambda: {"long_liq": 0.0, "short_liq": 0.0})
    for ts, ll, sl in rows:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        daily[dt]["long_liq"] += ll
        daily[dt]["short_liq"] += sl

    result = []
    for date_str in sorted(daily.keys()):
        d = daily[date_str]
        result.append({
            "date": date_str,
            "long_liq": d["long_liq"],
            "short_liq": d["short_liq"],
            "total_liq": d["long_liq"] + d["short_liq"],
        })

    # Merge with daily closes
    conn = sqlite3.connect(str(LIQ_DB))
    cur = conn.cursor()
    for sym_try in [symbol, f"{symbol}_PERP.A"]:
        cur.execute(
            "SELECT date, close FROM daily_closes WHERE symbol=? ORDER BY date",
            (sym_try,)
        )
        rows = cur.fetchall()
        if rows:
            break
    conn.close()

    close_map = {r[0]: r[1] for r in rows}
    for r in result:
        r["close"] = close_map.get(r["date"], 0)

    return result


def run_backtest(require_short_squeeze: bool, label: str):
    """Run full V5 backtest with given squeeze setting."""
    # Override the module-level CFG by replacing the attribute
    # V5Config is frozen, so we replace the whole CFG object
    import engines.liq_cluster_engine_v5 as eng
    old_cfg = eng.CFG
    eng.CFG = V5Config(require_short_squeeze=require_short_squeeze)

    # Also need to re-create the engine so it picks up the new CFG
    engine = LiqClusterEngineV5()

    equity = INITIAL_EQUITY
    peak = equity
    trades = []
    open_positions = {}
    cooldowns = defaultdict(int)
    stop_cooldowns = defaultdict(int)

    # Load all data
    all_klines = {}
    all_liq = {}
    start_ms = int(START_DATE.timestamp() * 1000)
    end_ms = int(END_DATE.timestamp() * 1000)

    for sym in SYMBOLS:
        all_klines[sym] = load_klines(sym, start_ms, end_ms)
        all_liq[sym] = load_liq_history(sym)

    # Build daily schedule
    all_dates = set()
    for sym in SYMBOLS:
        for row in all_liq[sym]:
            all_dates.add(row["date"])
    sorted_dates = sorted(all_dates)
    warmup_date_set = set(d for d in sorted_dates
                          if datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc) <= WARMUP_END)
    trade_dates = [d for d in sorted_dates if d not in warmup_date_set]

    # Index candles by (sym, date)
    candles_by_sym_date = defaultdict(lambda: defaultdict(list))
    for sym in SYMBOLS:
        for c in all_klines.get(sym, []):
            dt = c.open_time.strftime("%Y-%m-%d")
            candles_by_sym_date[sym][dt].append(c)

    total_candles = sum(len(v) for v in all_klines.values())
    print(f"[{label}] Loaded {total_candles} candles across {len(SYMBOLS)} symbols")
    print(f"[{label}] Trade period: {trade_dates[0]} to {trade_dates[-1]} ({len(trade_dates)} days)")

    # Warmup: feed liq data
    for date_str in sorted_dates:
        if date_str in warmup_date_set:
            for sym in SYMBOLS:
                sym_liq = [r for r in all_liq[sym] if r["date"] == date_str]
                if sym_liq:
                    engine.update_daily_liq(sym, sym_liq)

    # Trading period
    for date_str in trade_dates:
        # Update liq context
        for sym in SYMBOLS:
            sym_liq = [r for r in all_liq[sym] if r["date"] == date_str]
            if sym_liq:
                engine.update_daily_liq(sym, sym_liq)

        # Process candles
        for sym in SYMBOLS:
            day_candles = candles_by_sym_date[sym].get(date_str, [])

            for candle in day_candles:
                # Manage existing positions
                if sym in open_positions:
                    pos = open_positions[sym]
                    pos["candles"] = pos.get("candles", 0) + 1

                    exit_price, exit_reason = _check_exit(pos, candle)
                    if exit_price is not None:
                        pnl = _calc_pnl(pos, exit_price)
                        pnl_r = pnl / (INITIAL_EQUITY * FLAT_RISK)
                        equity += pnl
                        if equity > peak:
                            peak = equity
                        trades.append({
                            "symbol": sym,
                            "side": pos["side"],
                            "entry": pos["entry"],
                            "exit": exit_price,
                            "pnl": pnl,
                            "pnl_r": pnl_r,
                            "reason": exit_reason,
                            "candles": pos["candles"],
                            "decile": pos["decile"],
                        })
                        del open_positions[sym]
                        cooldowns[sym] = 36
                        if pnl_r <= -0.5:
                            stop_cooldowns[sym] = 3
                        continue

                # Skip if in cooldown
                if cooldowns[sym] > 0:
                    cooldowns[sym] -= 1
                    continue
                if stop_cooldowns[sym] > 0:
                    stop_cooldowns[sym] -= 1
                    continue

                # Position limits
                if len(open_positions) >= MAX_POSITIONS:
                    continue

                # Get candle history
                sym_candles = [c for c in all_klines[sym] if c.open_time <= candle.open_time][-200:]
                if len(sym_candles) < 60:
                    continue

                sig = engine.evaluate(sym, sym_candles)
                if sig is None:
                    continue

                # Enter
                risk_amount = equity * FLAT_RISK
                stop_dist = abs(sig.entry_price - sig.stop_price)
                if stop_dist <= 0:
                    continue
                qty = risk_amount / stop_dist

                st = engine._get_state(sym)
                open_positions[sym] = {
                    "side": sig.side.value,
                    "entry": sig.entry_price,
                    "stop": sig.stop_price,
                    "qty": qty,
                    "candles": 0,
                    "decile": st.decile,
                    "highest": sig.entry_price,
                    "lowest": sig.entry_price,
                }

    # Close remaining at last price
    for sym, pos in open_positions.items():
        last_candles = all_klines.get(sym, [])
        if last_candles:
            last_price = last_candles[-1].close
            pnl = _calc_pnl(pos, last_price)
            pnl_r = pnl / (INITIAL_EQUITY * FLAT_RISK)
            equity += pnl
            trades.append({
                "symbol": sym, "side": pos["side"],
                "entry": pos["entry"], "exit": last_price,
                "pnl": pnl, "pnl_r": pnl_r,
                "reason": "end_of_data", "candles": pos["candles"], "decile": pos["decile"],
            })

    # Restore original CFG
    eng.CFG = old_cfg

    return trades, equity, peak


def _check_exit(pos, candle):
    """Stop loss + simplified vol trail."""
    dec = pos["decile"]
    exits = DECILE_EXITS.get(dec, DECILE_EXITS[5])

    if pos["side"] == "long":
        if candle.low <= pos["stop"]:
            return pos["stop"], "stop_loss"
        if candle.close > pos.get("highest", pos["entry"]):
            pos["highest"] = candle.close
        # Trail: highest - 3% (simplified from ATR)
        trail_dist = 0.03 * pos["entry"]
        if pos["highest"] - candle.close > trail_dist and pos["highest"] > pos["entry"] * 1.005:
            return candle.close, "vol_trail"
    else:
        if candle.high >= pos["stop"]:
            return pos["stop"], "stop_loss"
        if candle.close < pos.get("lowest", pos["entry"]):
            pos["lowest"] = candle.close
        trail_dist = 0.03 * pos["entry"]
        if candle.close - pos["lowest"] > trail_dist and pos["lowest"] < pos["entry"] * 0.995:
            return candle.close, "vol_trail"

    return None, None


def _calc_pnl(pos, exit_price):
    if pos["side"] == "long":
        return (exit_price - pos["entry"]) * pos["qty"]
    return (pos["entry"] - exit_price) * pos["qty"]


def analyze_trades(trades, label, equity, peak):
    if not trades:
        print(f"\n[{label}] No trades!")
        return None

    wins = [t for t in trades if t["pnl_r"] > 0]
    losses = [t for t in trades if t["pnl_r"] <= 0]
    total_r = sum(t["pnl_r"] for t in trades)
    gross_profit = sum(t["pnl_r"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["pnl_r"] for t in losses)) if losses else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    wr = len(wins) / len(trades) * 100

    # Max DD from equity curve
    equity_curve = [INITIAL_EQUITY]
    for t in trades:
        equity_curve.append(equity_curve[-1] + t["pnl"])
    peak_eq = equity_curve[0]
    max_dd = 0
    for eq in equity_curve:
        if eq > peak_eq:
            peak_eq = eq
        dd = (peak_eq - eq) / peak_eq * 100
        if dd > max_dd:
            max_dd = dd

    by_sym = defaultdict(lambda: {"trades": 0, "wins": 0, "total_r": 0})
    for t in trades:
        by_sym[t["symbol"]]["trades"] += 1
        by_sym[t["symbol"]]["total_r"] += t["pnl_r"]
        if t["pnl_r"] > 0:
            by_sym[t["symbol"]]["wins"] += 1

    print(f"\n{'='*60}")
    print(f"[{label}]")
    print(f"{'='*60}")
    print(f"Trades: {len(trades)} (W:{len(wins)} L:{len(losses)})")
    print(f"Win Rate: {wr:.1f}%")
    print(f"Total R: {total_r:+.2f}R")
    print(f"Profit Factor: {pf:.2f}")
    print(f"Equity: ${INITIAL_EQUITY:,.0f} → ${equity:,.0f} ({(equity/INITIAL_EQUITY-1)*100:+.1f}%)")
    print(f"Max DD: {max_dd:.1f}%")
    print(f"\nBy symbol:")
    for sym, stats in sorted(by_sym.items(), key=lambda x: -x[1]["total_r"]):
        wr_s = stats["wins"]/stats["trades"]*100 if stats["trades"] > 0 else 0
        print(f"  {sym:15s}: {stats['trades']:3d} trades, {stats['total_r']:+.2f}R, {wr_s:.0f}% WR")

    return {
        "label": label, "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "wr": wr, "total_r": total_r, "pf": pf, "equity": equity, "max_dd": max_dd,
        "by_sym": dict(by_sym),
    }


if __name__ == "__main__":
    print("=" * 60)
    print("V5 Backtest: require_short_squeeze comparison")
    print(f"Period: {START_DATE.date()} to {END_DATE.date()}")
    print(f"Symbols: {len(SYMBOLS)}")
    print("=" * 60)

    trades_true, eq_true, peak_true = run_backtest(True, "SHORT_SQ_ONLY")
    stats_true = analyze_trades(trades_true, "SHORT_SQ_ONLY", eq_true, peak_true)

    trades_false, eq_false, peak_false = run_backtest(False, "BOTH_DIRECTIONS")
    stats_false = analyze_trades(trades_false, "BOTH_DIRECTIONS", eq_false, peak_false)

    if stats_true and stats_false:
        print(f"\n{'='*60}")
        print("COMPARISON")
        print(f"{'='*60}")
        print(f"{'Metric':<25} {'SHORT_SQ_ONLY':>15} {'BOTH':>15} {'Delta':>10}")
        print(f"{'-'*65}")
        print(f"{'Trades':<25} {stats_true['trades']:>15} {stats_false['trades']:>15} {stats_false['trades']-stats_true['trades']:>+10}")
        print(f"{'Win Rate':<25} {stats_true['wr']:>14.1f}% {stats_false['wr']:>14.1f}% {stats_false['wr']-stats_true['wr']:>+9.1f}%")
        print(f"{'Total R':<25} {stats_true['total_r']:>+15.2f} {stats_false['total_r']:>+15.2f} {stats_false['total_r']-stats_true['total_r']:>+10.2f}")
        print(f"{'Profit Factor':<25} {stats_true['pf']:>15.2f} {stats_false['pf']:>15.2f} {stats_false['pf']-stats_true['pf']:>+10.2f}")
        print(f"{'Final Equity':<25} ${stats_true['equity']:>14,.0f} ${stats_false['equity']:>14,.0f} ${stats_false['equity']-stats_true['equity']:>+10,.0f}")
        print(f"{'Max DD':<25} {stats_true['max_dd']:>14.1f}% {stats_false['max_dd']:>14.1f}% {stats_false['max_dd']-stats_true['max_dd']:>+9.1f}%")

        syms_true = set(t["symbol"] for t in trades_true)
        syms_false = set(t["symbol"] for t in trades_false)
        new_syms = syms_false - syms_true
        if new_syms:
            print(f"\nNew symbols with entries (BOTH mode):")
            for sym in sorted(new_syms):
                sym_trades = [t for t in trades_false if t["symbol"] == sym]
                total_r = sum(t["pnl_r"] for t in sym_trades)
                wins = len([t for t in sym_trades if t["pnl_r"] > 0])
                print(f"  {sym}: {len(sym_trades)} trades, {total_r:+.2f}R, {wins}/{len(sym_trades)} W")

    for label, trades in [("short_sq_only", trades_true), ("both_directions", trades_false)]:
        if trades:
            path = f"/root/bitana/backtest_output/squeeze_comparison_{label}.csv"
            with open(path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=trades[0].keys())
                writer.writeheader()
                writer.writerows(trades)
    print(f"\nTrade CSVs saved to backtest_output/")
