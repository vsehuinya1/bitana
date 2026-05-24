"""
Backtest sweep: test 3 fixes for choppy-market D1 stop losses.
Uses same data as v5_full_backtest (Jan 1 - May 20 2026, 28 symbols).
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
    FLAT_RISK_PCT, DECILE_EXITS, CFG, V5Config, CascadeTracker
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
            open_time=datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc),
            close_time=datetime.fromtimestamp(r[6] / 1000, tz=timezone.utc),
            open=float(r[2]), high=float(r[3]),
            low=float(r[4]), close=float(r[5]),
            volume=float(r[7]), taker_buy_volume=float(r[7]),
            is_closed=True,
        ))
    return candles


def load_liq(symbol):
    conn = sqlite3.connect(str(LIQ_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM liq_history WHERE symbol=? ORDER BY date", (symbol,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def run_backtest(config_overrides, label):
    """Run full backtest with optional config overrides."""
    # Apply config overrides
    original = {}
    for k, v in config_overrides.items():
        setattr(CFG, k, v)
        original[k] = getattr(CFG, k)
    
    try:
        engine = LiqClusterEngineV5()
        equity = INITIAL_EQUITY
        peak = equity
        trades = []
        daily_snapshots = {}
        
        # Load history for all symbols
        start_ms = int(START_DATE.timestamp() * 1000)
        end_ms = int(END_DATE.timestamp() * 1000)
        warmup_ms = int(WARMUP_END.timestamp() * 1000)
        
        print(f"\n{'='*60}")
        print(f"Running: {label}")
        print(f"Config: {config_overrides}")
        
        # Load liq data for all symbols
        liq_data = {}
        for sym in SYMBOLS:
            liq_data[sym] = load_liq(sym)
        
        # Load klines
        print("Loading klines...")
        all_klines = {}
        for sym in SYMBOLS:
            klines = load_klines(sym, start_ms, end_ms)
            all_klines[sym] = klines
        print(f"Loaded klines for {len(all_klines)} symbols")
        
        # Warmup period: load cascade trackers
        for sym in SYMBOLS:
            warmup_klines = [k for k in all_klines[sym] if k.open_time < WARMUP_END]
            if warmup_klines:
                # Feed warmup candles to build cascade
                min_date = (WARMUP_END - timedelta(days=120)).strftime("%Y-%m-%d")
                # Use liq data for warmup
                sym_liq = [l for l in liq_data.get(sym, []) if l.get("date", "") >= min_date]
                if sym_liq:
                    engine._cascades[sym] = CascadeTracker()
                    engine.update_daily_liq(sym, sym_liq)
        
        # Main loop: process each 5m candle
        print("Processing candles...")
        # Get unique timestamps
        all_timestamps = set()
        for sym in SYMBOLS:
            for k in all_klines[sym]:
                if k.open_time >= WARMUP_END:
                    all_timestamps.add(k.close_time)
        
        sorted_timestamps = sorted(all_timestamps)
        print(f"Processing {len(sorted_timestamps)} 5m candles...")
        
        # Track positions
        open_positions = {}
        consecutive_stops = defaultdict(int)
        
        for ts in sorted_timestamps:
            # Count candles per symbol up to this timestamp
            for sym in SYMBOLS:
                sym_klines = [k for k in all_klines[sym] if k.close_time <= ts]
                if not sym_klines:
                    continue
                
                latest_candle = sym_klines[-1]
                
                # Update liq data if new day
                current_date = ts.strftime("%Y-%m-%d")
                sym_liq = [l for l in liq_data.get(sym, []) if l.get("date", "") <= current_date]
                
                # Manage existing positions
                if sym in open_positions:
                    p = open_positions[sym]
                    engine._get_state(sym).bars_held = p["candles_held"]
                    result = engine.manage_position(sym, sym_klines)
                    p["candles_held"] += 1
                    
                    if result and result.get("action") == "close":
                        exit_price = result.get("exit_price", latest_candle.close)
                        sd = abs(p["entry_price"] - p["init_stop"])
                        pnl_r = (exit_price - p["entry_price"]) / sd if sd > 0 else 0
                        pnl_usd = (exit_price - p["entry_price"]) * p["quantity"]
                        fee = exit_price * p["quantity"] * 0.0004
                        net_pnl = pnl_usd - fee
                        equity += net_pnl
                        
                        if equity > peak:
                            peak = equity
                        
                        is_stop = "stop" in result.get("exit_reason", "")
                        trades.append({
                            "symbol": sym,
                            "decile": p["decile"],
                            "entry_price": p["entry_price"],
                            "exit_price": exit_price,
                            "pnl_r": pnl_r,
                            "pnl_usd": net_pnl,
                            "exit_reason": result.get("exit_reason", ""),
                            "hold_candles": p["candles_held"],
                            "aggression": p["aggression"],
                            "is_stop": is_stop,
                        })
                        
                        if is_stop:
                            consecutive_stops[sym] += 1
                        else:
                            consecutive_stops[sym] = 0
                        
                        del open_positions[sym]
                
                # Check entry
                state = engine._get_state(sym)
                if sym in open_positions:
                    continue
                if state.cooldown > 0:
                    continue
                if not state.cascade_active:
                    continue
                if state.stop_cooldown > 0:
                    continue
                if consecutive_stops.get(sym, 0) >= 3:
                    continue
                
                sig = engine.evaluate(sym, sym_klines)
                if sig is None:
                    continue
                
                # Execute entry
                agg = sig.signal_data.get("aggression_score", 0)
                decile = sig.signal_data.get("decile", 5)
                risk_pct = sig.signal_data.get("risk_pct", 0.04)
                stop_price = sig.stop_price
                entry_price = sig.entry_price
                
                sd = abs(entry_price - stop_price)
                if sd <= 0:
                    continue
                
                ra = equity * risk_pct
                qty = ra / sd
                notional = qty * entry_price
                lev = min(int(notional / equity) + 1, 20)
                lev = max(lev, 1)
                
                open_positions[sym] = {
                    "entry_price": entry_price,
                    "init_stop": stop_price,
                    "quantity": qty,
                    "decile": decile,
                    "aggression": agg,
                    "candles_held": 0,
                }
        
        # Calculate stats
        n = len(trades)
        wins = [t for t in trades if t["pnl_r"] > 0]
        losses = [t for t in trades if t["pnl_r"] <= 0]
        stops = [t for t in trades if t["is_stop"]]
        total_r = sum(t["pnl_r"] for t in trades)
        win_r = sum(t["pnl_r"] for t in wins)
        loss_r = sum(t["pnl_r"] for t in losses)
        wr = len(wins) / n * 100 if n else 0
        avg_win = np.mean([t["pnl_r"] for t in wins]) if wins else 0
        avg_loss = np.mean([t["pnl_r"] for t in losses]) if losses else 0
        
        max_dd = 0
        peak_equity = INITIAL_EQUITY
        running_equity = INITIAL_EQUITY
        for t in trades:
            running_equity += t["pnl_usd"]
            if running_equity > peak_equity:
                peak_equity = running_equity
            dd = (peak_equity - running_equity) / peak_equity * 100
            if dd > max_dd:
                max_dd = dd
        
        # D1-specific stats
        d1_trades = [t for t in trades if t["decile"] == 1]
        d1_wins = [t for t in d1_trades if t["pnl_r"] > 0]
        d1_wr = len(d1_wins) / len(d1_trades) * 100 if d1_trades else 0
        d1_net_r = sum(t["pnl_r"] for t in d1_trades)
        d1_stops = [t for t in d1_trades if t["is_stop"]]
        
        result = {
            "label": label,
            "trades": n,
            "wr": wr,
            "net_r": total_r,
            "win_r": win_r,
            "loss_r": loss_r,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "max_dd": max_dd,
            "pf": win_r / abs(loss_r) if loss_r else 0,
            "d1_trades": len(d1_trades),
            "d1_wr": d1_wr,
            "d1_net_r": d1_net_r,
            "d1_stop_pct": len(d1_stops) / len(d1_trades) * 100 if d1_trades else 0,
            "stops_total": len(stops),
            "stop_pct": len(stops) / n * 100 if n else 0,
        }
        
        print(f"  Trades: {n}, WR: {wr:.0f}%, Net R: {total_r:+.1f}, PF: {result['pf']:.2f}, MaxDD: {max_dd:.1f}%")
        print(f"  D1: {len(d1_trades)}t WR={d1_wr:.0f}% NetR={d1_net_r:+.1f} Stop%={result['d1_stop_pct']:.0f}%")
        
        return result
    finally:
        # Restore config
        for k, v in original.items():
            setattr(CFG, k, v)


# Run all 3 variations
results = []

# Baseline (current)
results.append(run_backtest({}, "Baseline (current)"))

# A: Raise min_cascade_strength to 0.25
results.append(run_backtest({"min_cascade_strength": 0.25}, "A: min_cascade_strength=0.25"))

# B: Aggression gate — block below 55 (simulated by adjusting score_to_decile)
# We'll handle this differently - skip for now

# C: Widen D1 stop from 2.5 to 3.0 ATR
results.append(run_backtest({"initial_stop_atr": 3.0}, "C: D1 stop 3.0 ATR (all deciles)"))

# D: min_cascade_strength=0.25 + stop=3.0
results.append(run_backtest({"min_cascade_strength": 0.25, "initial_stop_atr": 3.0}, "D: cascade=0.25 + stop=3.0"))

# Summary
print("\n\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
print(f"{'Config':>30} | {'Trades':>6} | {'WR':>4} | {'NetR':>7} | {'PF':>4} | {'MaxDD':>5} | {'D1_WR':>5} | {'D1_NetR':>7} | {'Stops%':>6}")
print("-" * 95)
for r in results:
    print(f"{r['label']:>30} | {r['trades']:>6} | {r['wr']:>3.0f}% | {r['net_r']:>+6.1f} | {r['pf']:>4.2f} | {r['max_dd']:>4.1f}% | {r['d1_wr']:>4.0f}% | {r['d1_net_r']:>+6.1f} | {r['stop_pct']:>5.0f}%")
