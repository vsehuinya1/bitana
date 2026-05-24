"""
V5 D3 Exclusion Backtest — baseline (ATR x2.5, no vol skip) with D3 decile excluded.

Runs two portfolios:
  A: Baseline — all deciles (ATR x2.5, no vol skip)
  B: No D3 — same but skip entries in decile 3

This measures the actual impact including capital redeployment into other deciles.
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


class Portfolio:
    def __init__(self, name, skip_deciles=None):
        self.name = name
        self.skip_deciles = skip_deciles or set()
        self.engine = LiqClusterEngineV5()
        self.open_positions = {}
        self.closed_trades = []
        self.equity = 10000.0
        self.peak_equity = self.equity
        self.max_dd = 0.0
        self.trade_count = 0
        self.skip_count = 0

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

        st = self.engine._get_state(sym)
        decile = st.decile

        if decile in self.skip_deciles:
            self.skip_count += 1
            return

        risk_pct = FLAT_RISK_PCT
        entry_price = sig.entry_price
        stop_price = sig.stop_price
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
            "entry_atr": sig.signal_data.get("atr", 0),
            "candles_held": 0,
        }


def run():
    print(f"V5 D3 Exclusion Backtest")
    print(f"Period: {START_DATE.strftime('%Y-%m-%d')} to {END_DATE.strftime('%Y-%m-%d')}")
    print(f"ATR x{CFG.initial_stop_atr} | No vol skip\n")

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
    portfolios = {
        "A: baseline (all deciles)": Portfolio("A: baseline (all deciles)"),
        "B: no D3": Portfolio("B: no D3", skip_deciles={3}),
    }

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
            eq_str = " | ".join(f"{n.split(':')[0]}: {p.equity:.0f}" for n, p in portfolios.items())
            print(f"  {close_time.strftime('%Y-%m-%d %H:%M')} | events: {event_count} | {eq_str}", flush=True)

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
                })

    # ── Results ──────────────────────────────────────────────────────

    print(f"\n{'='*100}")
    print("V5 D3 EXCLUSION BACKTEST RESULTS")
    print(f"{'='*100}")

    print(f"\n{'Config':<30} {'Trades':>6} {'Skips':>6} {'WR':>6} {'Net R':>10} {'Avg R':>8} {'PF':>6} {'Net $':>10} {'MaxDD':>7} {'Eq Final':>10}")
    print("-" * 100)

    results = {}
    for name, p in portfolios.items():
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
        print(f"{name:<30} {n:>6} {p.skip_count:>6} {wr:>5.0f}% {net_r:>+10.4f} {avg_r:>+8.4f} {pf:>6.2f} {net_usd:>+10.0f} {p.max_dd:>6.1f}% {p.equity:>10.0f}")
        results[name] = {
            "n": n, "skips": p.skip_count, "wr": wr, "net_r": net_r,
            "avg_r": avg_r, "pf": pf, "net_usd": net_usd, "max_dd": p.max_dd,
            "equity": p.equity, "wins": len(wins), "losses": len(losses),
        }

    # Delta
    base = results["A: baseline (all deciles)"]
    no_d3 = results["B: no D3"]
    print(f"\n{'Delta (B - A):':<30} {no_d3['n']-base['n']:>+6} {'':>6} {no_d3['wr']-base['wr']:>+5.1f}% {no_d3['net_r']-base['net_r']:>+10.4f} {no_d3['avg_r']-base['avg_r']:>+8.4f} {no_d3['pf']-base['pf']:>+6.2f} {no_d3['net_usd']-base['net_usd']:>+10.0f} {no_d3['max_dd']-base['max_dd']:>+6.1f}%")

    # By decile comparison
    print("\n\n=== BY DECILE ===")
    for name, p in portfolios.items():
        print(f"\n  {name}:")
        by_decile = defaultdict(lambda: {"w": [], "l": [], "total": 0, "net": 0})
        for t in p.closed_trades:
            d = t["decile"]
            by_decile[d]["total"] += 1
            by_decile[d]["net"] += t["pnl_r"]
            if t["pnl_r"] > 0:
                by_decile[d]["w"].append(t["pnl_r"])
            elif t["pnl_r"] < 0:
                by_decile[d]["l"].append(t["pnl_r"])
        for d in sorted(by_decile.keys()):
            data = by_decile[d]
            total = data["total"]
            if total == 0:
                continue
            w = data["w"]
            wr = len(w) / total * 100
            print(f"    D{d}: {total:>4} trades  WR={wr:>5.0f}%  net={data['net']:>+8.4f}R")

    # By exit reason
    print("\n\n=== BY EXIT REASON ===")
    for name, p in portfolios.items():
        print(f"\n  {name}:")
        by_reason = defaultdict(lambda: {"w": [], "l": [], "total": 0, "net": 0})
        for t in p.closed_trades:
            r = t["exit_reason"]
            by_reason[r]["total"] += 1
            by_reason[r]["net"] += t["pnl_r"]
            if t["pnl_r"] > 0:
                by_reason[r]["w"].append(t["pnl_r"])
            elif t["pnl_r"] < 0:
                by_reason[r]["l"].append(t["pnl_r"])
        for r in sorted(by_reason.keys()):
            data = by_reason[r]
            total = data["total"]
            if total == 0:
                continue
            w = data["w"]
            wr = len(w) / total * 100
            print(f"    {r:<16} {total:>4} trades  WR={wr:>5.0f}%  net={data['net']:>+8.4f}R")

    # Equity curve comparison (sampled)
    print("\n\n=== EQUITY CURVE SNAPSHOTS ===")
    base_trades = portfolios["A: baseline (all deciles)"].closed_trades
    no_d3_trades = portfolios["B: no D3"].closed_trades

    # Build equity curves
    def equity_curve(trades):
        eq = [10000.0]
        for t in trades:
            eq.append(t["equity_after"])
        return eq

    base_eq = equity_curve(base_trades)
    no_d3_eq = equity_curve(no_d3_trades)

    # Sample at 20 points
    n_points = min(len(base_eq), len(no_d3_eq))
    step = max(1, n_points // 20)
    print(f"\n{'Trade #':>8} {'Baseline Eq':>14} {'No D3 Eq':>14} {'Delta':>10}")
    print("-" * 50)
    for i in range(0, n_points, step):
        delta = no_d3_eq[i] - base_eq[i] if i < len(no_d3_eq) else 0
        print(f"{i:>8} {base_eq[i]:>14.0f} {no_d3_eq[i] if i < len(no_d3_eq) else 0:>14.0f} {delta:>+10.0f}")
    # Final
    print(f"{'FINAL':>8} {base_eq[-1]:>14.0f} {no_d3_eq[-1]:>14.0f} {no_d3_eq[-1]-base_eq[-1]:>+10.0f}")

    # Write CSVs
    for name, p in portfolios.items():
        if p.closed_trades:
            fname = name.replace(" ", "_").replace(":", "").replace("(", "").replace(")", "")
            csv_path = OUTPUT_DIR / f"d3_exclusion_{fname}_trades.csv"
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=p.closed_trades[0].keys())
                writer.writeheader()
                for t in p.closed_trades:
                    writer.writerow(t)
            print(f"\nWrote {len(p.closed_trades)} trades to {csv_path.name}")

    print(f"\n{'='*100}")
    print("DONE")


if __name__ == "__main__":
    run()
