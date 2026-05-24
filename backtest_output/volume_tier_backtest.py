"""
V5 Volume Tier Backtest — tests different daily volume cutoffs.

Volume tiers (based on avg daily vol over last 30 days):
  Tier 1: 20M+ (25 symbols) — current baseline
  Tier 2: 10-19M (2 symbols: RENDER, RUNE)
  Tier 3: 5-9M (0 symbols)
  Tier 4: <5M (2 symbols: NMR, QNT)

Tests:
  A: All symbols (baseline, no volume filter)
  B: 20M+ only (exclude RENDER, RUNE, NMR, QNT)
  C: 10M+ only (exclude NMR, QNT)
  D: 10-19M only (just RENDER, RUNE)
  E: <20M only (RENDER, RUNE, NMR, QNT — small-cap only)
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

ALL_SYMBOLS = [
    "NEARUSDT", "ZECUSDT", "ADAUSDT", "WLDUSDT", "UNIUSDT", "NMRUSDT",
    "PENDLEUSDT", "ARBUSDT", "RENDERUSDT", "RUNEUSDT", "FETUSDT", "DOTUSDT",
    "TONUSDT", "SOLUSDT", "1000LUNCUSDT", "ENAUSDT", "1000PEPEUSDT",
    "XRPUSDT", "FILUSDT", "BNBUSDT", "TAOUSDT", "CHZUSDT", "DASHUSDT",
    "QNTUSDT", "ICPUSDT", "XLMUSDT", "APTUSDT", "ETHUSDT",
]

# Volume tiers (avg daily USD volume over last 30 days)
VOL_TIERS = {
    '20M+': set(),   # will be populated
    '10-19M': set(),
    '5-9M': set(),
    '<5M': set(),
}

# From the analysis above
SYMBOL_VOLS = {
    'BTCUSDT': 9952623804, 'ETHUSDT': 7762248361, 'SOLUSDT': 1633888424,
    'ZECUSDT': 797299754, 'XRPUSDT': 582400618, 'BNBUSDT': 280197185,
    '1000PEPEUSDT': 259778304, 'TONUSDT': 254527229, 'ADAUSDT': 153590682,
    'TAOUSDT': 143868860, 'FILUSDT': 119273600, 'NEARUSDT': 96135608,
    'ENAUSDT': 89062251, '1000LUNCUSDT': 65419368, 'WLDUSDT': 63765514,
    'DASHUSDT': 61974148, 'DOTUSDT': 57986928, 'UNIUSDT': 52613309,
    'ICPUSDT': 44064079, 'APTUSDT': 39291934, 'ARBUSDT': 37143398,
    'XLMUSDT': 36417283, 'CHZUSDT': 34494682, 'PENDLEUSDT': 32747093,
    'FETUSDT': 28940327, 'RENDERUSDT': 19313061, 'RUNEUSDT': 11474140,
    'NMRUSDT': 4689161, 'QNTUSDT': 4421084,
}

for sym, vol in SYMBOL_VOLS.items():
    if sym not in ALL_SYMBOLS:
        continue
    if vol >= 20_000_000:
        VOL_TIERS['20M+'].add(sym)
    elif vol >= 10_000_000:
        VOL_TIERS['10-19M'].add(sym)
    elif vol >= 5_000_000:
        VOL_TIERS['5-9M'].add(sym)
    else:
        VOL_TIERS['<5M'].add(sym)

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
    def __init__(self, name, allowed_symbols):
        self.name = name
        self.allowed_symbols = allowed_symbols
        self.engine = LiqClusterEngineV5()
        self.open_positions = {}
        self.closed_trades = []
        self.equity = 10000.0
        self.peak_equity = self.equity
        self.max_dd = 0.0
        self.trade_count = 0

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
        if sym not in self.allowed_symbols:
            return

        sig = self.engine.evaluate(sym, candles)
        if sig is None:
            return

        st = self.engine._get_state(sym)
        decile = st.decile
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
    print(f"V5 Volume Tier Backtest")
    print(f"Period: {START_DATE.strftime('%Y-%m-%d')} to {END_DATE.strftime('%Y-%m-%d')}")

    # Print tier summary
    for tier, syms in VOL_TIERS.items():
        print(f"  {tier}: {len(syms)} symbols — {', '.join(sorted(syms))}")

    # Define test configurations
    configs = {
        "A: all symbols": ALL_SYMBOLS,
        "B: 20M+ only": sorted(VOL_TIERS['20M+']),
        "C: 10M+ only": sorted(VOL_TIERS['20M+'] | VOL_TIERS['10-19M']),
        "D: 10-19M only": sorted(VOL_TIERS['10-19M']),
        "E: <20M only": sorted(VOL_TIERS['10-19M'] | VOL_TIERS['5-9M'] | VOL_TIERS['<5M']),
    }

    # Pre-load all data
    print("\nLoading data...")
    global all_klines, all_liq, all_closes
    all_klines = {}
    all_liq = {}
    all_closes = {}
    for sym in ALL_SYMBOLS:
        start_ms = int(START_DATE.timestamp() * 1000)
        end_ms = int(END_DATE.timestamp() * 1000)
        all_klines[sym] = load_klines(sym, start_ms, end_ms)
        all_liq[sym] = load_liq_history(sym)
        all_closes[sym] = load_daily_closes(sym)
        print(f"  {sym}: {len(all_klines[sym])} klines", flush=True)

    # Build unified timeline
    print("\nBuilding timeline...")
    timeline = []
    for sym in ALL_SYMBOLS:
        for i, c in enumerate(all_klines[sym]):
            timeline.append((c.close_time, sym, i))
    timeline.sort(key=lambda x: x[0])
    print(f"Total events: {len(timeline)}")

    # Initialize portfolios
    portfolios = {}
    for name, syms in configs.items():
        portfolios[name] = Portfolio(name, set(syms))

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
            eq_str = " | ".join(f"{n.split(':')[0]}: {p.equity:.0f}" for n, p in list(portfolios.items())[:3])
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
                })

    # ── Results ──────────────────────────────────────────────────────

    print(f"\n{'='*100}")
    print("V5 VOLUME TIER BACKTEST RESULTS")
    print(f"{'='*100}")

    print(f"\n{'Config':<25} {'Symbols':>7} {'Trades':>6} {'WR':>6} {'Net R':>10} {'Avg R':>8} {'PF':>6} {'Net $':>10} {'MaxDD':>7} {'Eq Final':>10}")
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
        n_syms = len(p.allowed_symbols)
        print(f"{name:<25} {n_syms:>7} {n:>6} {wr:>5.0f}% {net_r:>+10.4f} {avg_r:>+8.4f} {pf:>6.2f} {net_usd:>+10.0f} {p.max_dd:>6.1f}% {p.equity:>10.0f}")
        results[name] = {"n": n, "wr": wr, "net_r": net_r, "pf": pf, "max_dd": p.max_dd, "equity": p.equity}

    # By exit reason
    print("\n\n=== BY EXIT REASON ===")
    for name, p in portfolios.items():
        if not p.closed_trades:
            continue
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

    # Per-symbol breakdown for small-cap tests
    for name in ["D: 10-19M only", "E: <20M only"]:
        p = portfolios[name]
        if not p.closed_trades:
            continue
        print(f"\n\n=== PER-SYMBOL BREAKDOWN: {name} ===")
        by_sym = defaultdict(lambda: {"w": [], "l": [], "total": 0, "net": 0})
        for t in p.closed_trades:
            s = t["symbol"]
            by_sym[s]["total"] += 1
            by_sym[s]["net"] += t["pnl_r"]
            if t["pnl_r"] > 0:
                by_sym[s]["w"].append(t["pnl_r"])
            elif t["pnl_r"] < 0:
                by_sym[s]["l"].append(t["pnl_r"])
        for s in sorted(by_sym.keys()):
            data = by_sym[s]
            total = data["total"]
            w = data["w"]
            wr = len(w) / total * 100 if total > 0 else 0
            print(f"  {s:<16} {total:>4} trades  WR={wr:>5.0f}%  net={data['net']:>+8.4f}R")

    # Write CSVs
    for name, p in portfolios.items():
        if p.closed_trades:
            fname = name.replace(" ", "_").replace(":", "").replace("<", "lt").replace(">", "gt").replace("-", "_").replace("+", "plus")
            csv_path = OUTPUT_DIR / f"volume_tier_{fname}_trades.csv"
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
