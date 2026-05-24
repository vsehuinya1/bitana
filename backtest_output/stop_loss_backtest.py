"""
V5 Stop Loss Backtest — ATR multiplier × Vol-z entry skip.

Tests 16 combinations:
  ATR multipliers: 2.5 (baseline), 3.0, 3.5, 4.0
  Vol-z skip thresholds: 0 (none), 0.5, 1.0, 1.5

Each combo runs as a separate virtual portfolio.
"""
import sqlite3
import numpy as np
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import csv
import json
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from engines.liq_cluster_engine_v5 import (
    LiqClusterEngineV5, FLAT_RISK_PCT, DECILE_EXITS, CFG
)
from core.models import Candle

KLINES_DB = Path("/root/bitana/backtest_data/klines_5m.db")
LIQ_DB = Path("/root/bitana/backtest_data/coinalyze_liq.db")
OUTPUT_DIR = Path("/root/bitana/backtest_output")

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
TAKER_BPS = 4.5
SLIP_BPS = 2.0

# ── Parameter grid ──────────────────────────────────────────────────

ATR_MULTS = [2.5, 3.0, 3.5, 4.0]
VOL_Z_SKIPS = [0.0, 0.5, 1.0, 1.5]  # skip entry if vol_z < threshold (0 = no skip)

# ── Data loading ────────────────────────────────────────────────────

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
    conn = sqlite3.connect(str(LIQ_DB))
    cur = conn.cursor()
    cur.execute(
        "SELECT timestamp, long_liq, short_liq FROM liquidation_history "
        "WHERE symbol=? ORDER BY timestamp", (symbol,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def load_daily_closes(symbol):
    conn = sqlite3.connect(str(LIQ_DB))
    cur = conn.cursor()
    try:
        cur.execute("SELECT date, close FROM daily_closes WHERE symbol=? ORDER BY date", (symbol,))
        rows = {r[0]: r[1] for r in cur.fetchall()}
    except Exception:
        rows = {}
    conn.close()
    return rows

def build_liq_rows(liq_history, daily_closes, up_to_ts):
    rows = []
    for ts, ll, sl in liq_history:
        if ts > up_to_ts:
            break
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d")
        rows.append({
            "date": date_str, "total_liq": ll + sl,
            "long_liq": ll, "short_liq": sl,
            "close": daily_closes.get(date_str, 0),
        })
    return rows

# ── Portfolio state ─────────────────────────────────────────────────

class Portfolio:
    def __init__(self, name, atr_mult, vol_z_skip):
        self.name = name
        self.atr_mult = atr_mult
        self.vol_z_skip = vol_z_skip
        self.engine = LiqClusterEngineV5()
        self.open_positions = {}
        self.closed_trades = []
        self.equity = 10000.0
        self.peak_equity = self.equity
        self.max_dd = 0.0
        self.trade_count = 0
        self.skip_count = 0  # trades skipped due to vol_z

    def update_liq(self, sym, close_time, liq_data, closes_data):
        day_ts = int(close_time.timestamp())
        daily_rows = build_liq_rows(liq_data, closes_data, day_ts)
        if daily_rows:
            self.engine.update_daily_liq(sym, daily_rows)

    def manage_positions(self, sym, candles, close_time):
        if sym not in self.open_positions:
            return
        pos = self.open_positions[sym]
        pos["candles_held"] += 1

        result = self.engine.manage_position(sym, candles)
        if result and result["action"] == "close":
            exit_price = result["exit_price"]
            entry_price = pos["entry_price"]
            qty = pos["quantity"]
            risk_per_unit = pos["risk_per_unit"]

            slip = exit_price * (SLIP_BPS / 10000)
            fill_price = exit_price - slip
            fee = qty * fill_price * (TAKER_BPS / 10000)
            pnl = (fill_price - entry_price) * qty - fee
            pnl_r = pnl / (risk_per_unit * qty) if risk_per_unit > 0 and qty > 0 else 0

            self.equity += pnl
            if self.equity > self.peak_equity:
                self.peak_equity = self.equity
            dd = (self.peak_equity - self.equity) / self.peak_equity * 100 if self.peak_equity > 0 else 0
            if dd > self.max_dd:
                self.max_dd = dd

            trade = {
                "symbol": sym,
                "entry_time": pos["entry_time"].isoformat(),
                "exit_time": close_time.isoformat(),
                "entry_price": entry_price,
                "exit_price": fill_price,
                "pnl_r": round(pnl_r, 4),
                "pnl_usd": round(pnl, 2),
                "exit_reason": result["reason"],
                "decile": pos["decile"],
                "aggression": round(pos["aggression"], 1),
                "candles_held": pos["candles_held"],
                "mae": round(result.get("mae", 0), 4),
                "mfe": round(result.get("mfe", 0), 4),
                "risk_pct": pos["risk_pct"],
                "entry_atr": pos.get("entry_atr", 0),
                "equity_after": round(self.equity, 2),
                "atr_mult": self.atr_mult,
                "vol_z_at_entry": pos.get("vol_z", 0),
            }
            self.closed_trades.append(trade)
            self.trade_count += 1

            st = self.engine._get_state(sym)
            st.in_trade = False
            del self.open_positions[sym]

    def check_entry(self, sym, candles, close_time):
        if close_time < WARMUP_END:
            return
        if sym in self.open_positions:
            return

        sig = self.engine.evaluate(sym, candles)
        if sig is None:
            return

        vol_z = sig.signal_data.get("vol_z", 0)

        # Vol-z skip filter
        if self.vol_z_skip > 0 and vol_z < self.vol_z_skip:
            self.skip_count += 1
            return

        st = self.engine._get_state(sym)
        decile = st.decile
        risk_pct = FLAT_RISK_PCT

        entry_price = sig.entry_price
        atr = sig.signal_data.get("atr", 0)
        # Recompute stop with our ATR multiplier (engine uses CFG.initial_stop_atr which is frozen)
        stop_price = entry_price - atr * self.atr_mult
        risk_per_unit = abs(entry_price - stop_price)
        if risk_per_unit <= 0:
            return

        risk_amount = self.equity * risk_pct
        qty = risk_amount / risk_per_unit

        slip = entry_price * (SLIP_BPS / 10000)
        fill_price = entry_price + slip
        fee = qty * fill_price * (TAKER_BPS / 10000)
        self.equity -= fee

        self.open_positions[sym] = {
            "entry_price": fill_price,
            "stop_price": stop_price,
            "risk_per_unit": risk_per_unit,
            "quantity": qty,
            "decile": decile,
            "aggression": st.aggression_score,
            "risk_pct": risk_pct,
            "entry_time": close_time,
            "entry_atr": atr,
            "candles_held": 0,
            "vol_z": vol_z,
        }

        # Sync engine state to match our custom stop
        st.risk_per_unit = risk_per_unit
        st.entry_price = fill_price


# ── Main ────────────────────────────────────────────────────────────

def run():
    print(f"V5 Stop Loss Backtest")
    print(f"Period: {START_DATE.strftime('%Y-%m-%d')} to {END_DATE.strftime('%Y-%m-%d')}")
    print(f"ATR multipliers: {ATR_MULTS}")
    print(f"Vol-z skips: {VOL_Z_SKIPS}")
    print(f"Combinations: {len(ATR_MULTS) * len(VOL_Z_SKIPS)}\n")

    # Pre-load all data
    print("Loading data...")
    global all_klines, all_liq, all_closes
    all_klines = {}
    all_liq = {}
    all_closes = {}
    for sym in SYMBOLS:
        start_ms = int(START_DATE.timestamp() * 1000)
        end_ms = int(END_DATE.timestamp() * 1000)
        all_klines[sym] = load_klines(sym, start_ms, end_ms)
        all_liq[sym] = load_liq_history(sym)
        all_closes[sym] = load_daily_closes(sym)
        print(f"  {sym}: {len(all_klines[sym])} klines", flush=True)

    # Build unified timeline
    print("\nBuilding timeline...")
    timeline = []
    for sym in SYMBOLS:
        for i, c in enumerate(all_klines[sym]):
            timeline.append((c.close_time, sym, i))
    timeline.sort(key=lambda x: x[0])
    print(f"Total events: {len(timeline)}")

    # Initialize portfolios
    portfolios = {}
    for atr in ATR_MULTS:
        for vz in VOL_Z_SKIPS:
            name = f"ATR{atr}_VZ{vz}"
            portfolios[name] = Portfolio(name, atr, vz)

    # Track liq updates per portfolio
    liq_updated = {name: set() for name in portfolios}

    # Run simulation
    print("\nRunning simulation...")
    event_count = 0

    for close_time, sym, candle_idx in timeline:
        event_count += 1
        candles = all_klines[sym][max(0, candle_idx - 199):candle_idx + 1]

        day_str = close_time.strftime("%Y-%m-%d")
        for name, p in portfolios.items():
            liq_key = f"{sym}_{day_str}"
            if liq_key not in liq_updated[name]:
                liq_updated[name].add(liq_key)
                p.update_liq(sym, close_time, all_liq[sym], all_closes[sym])

        for name, p in portfolios.items():
            p.manage_positions(sym, candles, close_time)

        for name, p in portfolios.items():
            p.check_entry(sym, candles, close_time)

        if event_count % 100000 == 0:
            eq_str = " | ".join(f"{n}: {p.equity:.0f}" for n, p in list(portfolios.items())[:4])
            print(f"  {close_time.strftime('%Y-%m-%d %H:%M')} | events: {event_count} | {eq_str}...", flush=True)

    # Close remaining positions
    for name, p in portfolios.items():
        for sym, pos in list(p.open_positions.items()):
            last_candle = all_klines[sym][-1] if all_klines[sym] else None
            if last_candle:
                exit_price = last_candle.close
                entry_price = pos["entry_price"]
                qty = pos["quantity"]
                risk_per_unit = pos["risk_per_unit"]
                slip = exit_price * (SLIP_BPS / 10000)
                fill_price = exit_price - slip
                fee = qty * fill_price * (TAKER_BPS / 10000)
                pnl = (fill_price - entry_price) * qty - fee
                pnl_r = pnl / (risk_per_unit * qty) if risk_per_unit > 0 and qty > 0 else 0
                p.equity += pnl
                p.closed_trades.append({
                    "symbol": sym,
                    "entry_time": pos["entry_time"].isoformat(),
                    "exit_time": END_DATE.isoformat(),
                    "entry_price": entry_price,
                    "exit_price": fill_price,
                    "pnl_r": round(pnl_r, 4),
                    "pnl_usd": round(pnl, 2),
                    "exit_reason": "end_of_backtest",
                    "decile": pos["decile"],
                    "aggression": round(pos["aggression"], 1),
                    "candles_held": pos["candles_held"],
                    "mae": 0, "mfe": 0,
                    "risk_pct": pos["risk_pct"],
                    "entry_atr": pos.get("entry_atr", 0),
                    "equity_after": round(p.equity, 2),
                    "atr_mult": p.atr_mult,
                    "vol_z_at_entry": pos.get("vol_z", 0),
                })

    # ── Results ──────────────────────────────────────────────────────

    print(f"\n{'='*110}")
    print("V5 STOP LOSS BACKTEST RESULTS")
    print(f"{'='*110}")

    # Full results table
    print(f"\n{'Config':<20} {'Trades':>6} {'Skips':>6} {'WR':>6} {'Net R':>10} {'Avg R':>8} {'PF':>6} {'Net $':>10} {'MaxDD':>7} {'Eq Final':>10}")
    print("-" * 110)

    results = {}
    for name, p in sorted(portfolios.items()):
        n = len(p.closed_trades)
        wins = [t for t in p.closed_trades if t["pnl_r"] > 0]
        losses = [t for t in p.closed_trades if t["pnl_r"] < 0]
        wr = len(wins) / max(n, 1) * 100
        net_r = sum(t["pnl_r"] for t in p.closed_trades)
        avg_r = net_r / max(n, 1)
        gross_win = sum(t["pnl_r"] for t in wins)
        gross_loss = abs(sum(t["pnl_r"] for t in losses))
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
        net_usd = sum(t["pnl_usd"] for t in p.closed_trades)
        print(f"{name:<20} {n:>6} {p.skip_count:>6} {wr:>5.0f}% {net_r:>+10.4f} {avg_r:>+8.4f} {pf:>6.2f} {net_usd:>+10.0f} {p.max_dd:>6.1f}% {p.equity:>10.0f}")
        results[name] = {
            "n": n, "skips": p.skip_count, "wr": wr, "net_r": net_r,
            "avg_r": avg_r, "pf": pf, "net_usd": net_usd, "max_dd": p.max_dd,
            "equity": p.equity, "wins": len(wins), "losses": len(losses),
        }

    # Best by net R
    best = max(results.items(), key=lambda x: x[1]["net_r"])
    best_r = best[1]
    print(f"\n★ Best net R: {best[0]} → {best_r['net_r']:+.4f}R ({best_r['n']} trades, {best_r['wr']:.0f}% WR, PF {best_r['pf']:.2f})")

    # Best by PF
    best_pf = max(results.items(), key=lambda x: x[1]["pf"] if x[1]["pf"] != float("inf") else 0)
    best_pf_r = best_pf[1]
    print(f"★ Best PF: {best_pf[0]} → {best_pf_r['pf']:.2f} ({best_pf_r['n']} trades, {best_pf_r['net_r']:+.4f}R)")

    # Best by max DD
    best_dd = min(results.items(), key=lambda x: x[1]["max_dd"])
    best_dd_r = best_dd[1]
    print(f"★ Best MaxDD: {best_dd[0]} → {best_dd_r['max_dd']:.1f}% ({best_dd_r['net_r']:+.4f}R)")

    # Stop loss analysis for each config
    print("\n\n=== STOP LOSS ANALYSIS ===")
    for name, p in sorted(portfolios.items()):
        stops = [t for t in p.closed_trades if t["exit_reason"] == "stop_loss"]
        trails = [t for t in p.closed_trades if "trail" in t["exit_reason"]]
        timeouts = [t for t in p.closed_trades if t["exit_reason"] == "time_stop"]
        ends = [t for t in p.closed_trades if t["exit_reason"] == "end_of_backtest"]

        stop_net = sum(t["pnl_r"] for t in stops)
        trail_net = sum(t["pnl_r"] for t in trails)
        timeout_net = sum(t["pnl_r"] for t in timeouts)
        end_net = sum(t["pnl_r"] for t in ends)

        avg_stop_r = stop_net / len(stops) if stops else 0
        avg_trail_r = trail_net / len(trails) if trails else 0

        print(f"\n  {name}:")
        print(f"    stop_loss:  {len(stops):>4} trades  net={stop_net:>+8.4f}R  avg={avg_stop_r:+.4f}R")
        print(f"    vol_trail:  {len([t for t in trails if t['exit_reason']=='vol_trail']):>4} trades  net={sum(t['pnl_r'] for t in trails if t['exit_reason']=='vol_trail'):>+8.4f}R")
        print(f"    struct_trail: {len([t for t in trails if t['exit_reason']=='struct_trail']):>4} trades  net={sum(t['pnl_r'] for t in trails if t['exit_reason']=='struct_trail'):>+8.4f}R")
        print(f"    time_stop:  {len(timeouts):>4} trades  net={timeout_net:>+8.4f}R")
        print(f"    end:        {len(ends):>4} trades  net={end_net:>+8.4f}R")

    # ATR multiplier comparison (no vol skip)
    print("\n\n=== ATR MULTIPLIER COMPARISON (no vol skip) ===")
    print(f"\n{'ATR Mult':<12} {'Trades':>6} {'WR':>6} {'Net R':>10} {'PF':>6} {'Stop Loss R':>12} {'Trail R':>10} {'MaxDD':>7}")
    print("-" * 80)
    for atr in ATR_MULTS:
        name = f"ATR{atr}_VZ0.0"
        if name in results:
            r = results[name]
            p = portfolios[name]
            stops = [t for t in p.closed_trades if t["exit_reason"] == "stop_loss"]
            trails = [t for t in p.closed_trades if "trail" in t["exit_reason"]]
            stop_net = sum(t["pnl_r"] for t in stops)
            trail_net = sum(t["pnl_r"] for t in trails)
            print(f"x{atr:<10} {r['n']:>6} {r['wr']:>5.0f}% {r['net_r']:>+10.4f} {r['pf']:>6.2f} {stop_net:>+12.4f} {trail_net:>+10.4f} {r['max_dd']:>6.1f}%")

    # Vol skip comparison (ATR 2.5)
    print("\n\n=== VOL-Z SKIP COMPARISON (ATR x2.5) ===")
    print(f"\n{'VZ Skip':<12} {'Trades':>6} {'Skips':>6} {'WR':>6} {'Net R':>10} {'PF':>6} {'Stop Loss R':>12} {'Trail R':>10} {'MaxDD':>7}")
    print("-" * 85)
    for vz in VOL_Z_SKIPS:
        name = f"ATR2.5_VZ{vz}"
        if name in results:
            r = results[name]
            p = portfolios[name]
            stops = [t for t in p.closed_trades if t["exit_reason"] == "stop_loss"]
            trails = [t for t in p.closed_trades if "trail" in t["exit_reason"]]
            stop_net = sum(t["pnl_r"] for t in stops)
            trail_net = sum(t["pnl_r"] for t in trails)
            print(f"≥{vz:<10} {r['n']:>6} {r['skips']:>6} {r['wr']:>5.0f}% {r['net_r']:>+10.4f} {r['pf']:>6.2f} {stop_net:>+12.4f} {trail_net:>+10.4f} {r['max_dd']:>6.1f}%")

    # Write CSVs
    for name, p in portfolios.items():
        if p.closed_trades:
            csv_path = OUTPUT_DIR / f"stoploss_{name}_trades.csv"
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=p.closed_trades[0].keys())
                writer.writeheader()
                for t in p.closed_trades:
                    writer.writerow(t)

    # Summary CSV
    summary_path = OUTPUT_DIR / "stop_loss_summary.csv"
    with open(summary_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["config", "atr_mult", "vol_z_skip", "trades", "skips", "wr", "net_r", "avg_r", "pf", "net_usd", "max_dd", "equity_final"])
        for name, p in sorted(portfolios.items()):
            r = results[name]
            writer.writerow([name, p.atr_mult, p.vol_z_skip, r["n"], r["skips"], f"{r['wr']:.1f}", f"{r['net_r']:.4f}", f"{r['avg_r']:.4f}", f"{r['pf']:.2f}", f"{r['net_usd']:.0f}", f"{r['max_dd']:.1f}", f"{r['equity']:.0f}"])

    print(f"\nWrote summary to {summary_path}")
    print(f"{'='*110}")
    print("DONE")


if __name__ == "__main__":
    run()
