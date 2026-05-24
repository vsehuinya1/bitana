"""
V5 Confirmation Filter Backtest — Single pass, 4 filter variations.

Tests confirmation filter variations:
  A: Baseline — current 4/6 (all trades)
  B: Require vol OR imb
  C: Require vol AND imb
  D: Require breakout AND (vol OR imb)

Each filter runs as a separate "virtual portfolio" with its own equity tracking.
All filters share the same entry signals but skip trades that don't pass their filter.
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
    LiqClusterEngineV5, _compute_aggression, _score_to_decile,
    FLAT_RISK_PCT, DECILE_EXITS, CFG
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

# ── Filter definitions ──────────────────────────────────────────────

def filter_a(confirmations):
    """Baseline: all trades pass (4/6 already enforced by engine)."""
    return True

def filter_b(confirmations):
    """Require vol OR imb."""
    return confirmations.get("vol", False) or confirmations.get("imb", False)

def filter_c(confirmations):
    """Require vol AND imb."""
    return confirmations.get("vol", False) and confirmations.get("imb", False)

def filter_d(confirmations):
    """Require breakout AND at least one of {vol, imb}."""
    return confirmations.get("breakout", False) and (
        confirmations.get("vol", False) or confirmations.get("imb", False)
    )

FILTERS = {
    "A: baseline": filter_a,
    "B: vol OR imb": filter_b,
    "C: vol AND imb": filter_c,
    "D: breakout+(vol|imb)": filter_d,
}

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
        "WHERE symbol=? ORDER BY timestamp",
        (symbol,)
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
            "date": date_str,
            "total_liq": ll + sl,
            "long_liq": ll,
            "short_liq": sl,
            "close": daily_closes.get(date_str, 0),
        })
    return rows

# ── Per-filter state ────────────────────────────────────────────────

class FilterState:
    def __init__(self, name, filter_fn):
        self.name = name
        self.filter_fn = filter_fn
        self.engine = LiqClusterEngineV5()
        self.open_positions = {}
        self.closed_trades = []
        self.equity = 10000.0
        self.peak_equity = self.equity
        self.max_dd = 0.0
        self.trade_count = 0

    def update_liq(self, sym, close_time):
        day_ts = int(close_time.timestamp())
        daily_rows = build_liq_rows(all_liq[sym], all_closes[sym], day_ts)
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
                "confirmations": pos.get("confirmations", {}),
                "vol_z": pos.get("vol_z", 0),
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

        confirmations = sig.signal_data.get("confirmations", {})

        # Apply filter
        if not self.filter_fn(confirmations):
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
            "confirmations": confirmations,
            "vol_z": sig.signal_data.get("vol_z", 0),
        }


# ── Main ────────────────────────────────────────────────────────────

TAKER_BPS = 4.5
SLIP_BPS = 2.0

def run():
    print(f"V5 Confirmation Filter Backtest")
    print(f"Period: {START_DATE.strftime('%Y-%m-%d')} to {END_DATE.strftime('%Y-%m-%d')}")
    print(f"Warmup: {WARMUP_DAYS} days | Symbols: {len(SYMBOLS)}")
    print(f"Filters: {list(FILTERS.keys())}\n")

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
        print(f"  {sym}: {len(all_klines[sym])} klines, {len(all_liq[sym])} liq rows", flush=True)

    # Build unified timeline
    print("\nBuilding timeline...")
    timeline = []
    for sym in SYMBOLS:
        for i, c in enumerate(all_klines[sym]):
            timeline.append((c.close_time, sym, i))
    timeline.sort(key=lambda x: x[0])
    print(f"Total events: {len(timeline)}")

    # Initialize filter states
    filter_states = {name: FilterState(name, fn) for name, fn in FILTERS.items()}

    # Track which symbols have been liq-updated for each filter
    liq_updated = {name: set() for name in FILTERS}

    # Run simulation
    print("\nRunning simulation...")
    event_count = 0

    for close_time, sym, candle_idx in timeline:
        event_count += 1

        # Rolling candle buffer
        candles = all_klines[sym][max(0, candle_idx - 199):candle_idx + 1]

        # Update liq context once per day per symbol (shared across filters)
        day_str = close_time.strftime("%Y-%m-%d")
        for name, fs in filter_states.items():
            liq_key = f"{sym}_{day_str}"
            if liq_key not in liq_updated[name]:
                liq_updated[name].add(liq_key)
                fs.update_liq(sym, close_time)

        # Manage existing positions
        for name, fs in filter_states.items():
            fs.manage_positions(sym, candles, close_time)

        # Check for new entry
        for name, fs in filter_states.items():
            fs.check_entry(sym, candles, close_time)

        if event_count % 100000 == 0:
            eq_str = " | ".join(f"{n.split(':')[0]}: {fs.equity:.0f}" for n, fs in filter_states.items())
            print(f"  {close_time.strftime('%Y-%m-%d %H:%M')} | events: {event_count} | {eq_str}", flush=True)

    # Close remaining positions
    for name, fs in filter_states.items():
        for sym, pos in list(fs.open_positions.items()):
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
                fs.equity += pnl
                fs.closed_trades.append({
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
                    "equity_after": round(fs.equity, 2),
                    "confirmations": pos.get("confirmations", {}),
                    "vol_z": pos.get("vol_z", 0),
                })

    # ── Results ──────────────────────────────────────────────────────

    OUTPUT_DIR = Path("/root/bitana/backtest_output")

    print(f"\n{'='*100}")
    print("V5 CONFIRMATION FILTER BACKTEST RESULTS")
    print(f"{'='*100}")

    # Summary comparison
    print(f"\n{'Filter':<25} {'Trades':>6} {'WR':>6} {'Net R':>10} {'Avg R':>8} {'PF':>6} {'Net $':>10} {'MaxDD':>7} {'Eq Final':>10}")
    print("-" * 100)

    for name, fs in filter_states.items():
        n = len(fs.closed_trades)
        wins = [t for t in fs.closed_trades if t["pnl_r"] > 0]
        losses = [t for t in fs.closed_trades if t["pnl_r"] < 0]
        wr = len(wins) / max(n, 1) * 100
        net_r = sum(t["pnl_r"] for t in fs.closed_trades)
        avg_r = net_r / max(n, 1)
        gross_win = sum(t["pnl_r"] for t in wins)
        gross_loss = abs(sum(t["pnl_r"] for t in losses))
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
        net_usd = sum(t["pnl_usd"] for t in fs.closed_trades)
        print(f"{name:<25} {n:>6} {wr:>5.0f}% {net_r:>+10.4f} {avg_r:>+8.4f} {pf:>6.2f} {net_usd:>+10.0f} {fs.max_dd:>6.1f}% {fs.equity:>10.0f}")

    # Trade reduction
    baseline_n = len(filter_states["A: baseline"].closed_trades)
    print(f"\nTrade reduction vs baseline:")
    for name, fs in filter_states.items():
        if name == "A: baseline":
            continue
        pct = (1 - len(fs.closed_trades) / baseline_n) * 100
        print(f"  {name}: -{pct:.0f}% ({baseline_n} → {len(fs.closed_trades)} trades)")

    # By exit reason
    print("\n\n=== BY EXIT REASON ===")
    for name, fs in filter_states.items():
        print(f"\n  {name}:")
        by_reason = defaultdict(lambda: {"w": [], "l": []})
        for t in fs.closed_trades:
            if t["pnl_r"] > 0:
                by_reason[t["exit_reason"]]["w"].append(t["pnl_r"])
            elif t["pnl_r"] < 0:
                by_reason[t["exit_reason"]]["l"].append(t["pnl_r"])
        for reason in sorted(by_reason.keys()):
            data = by_reason[reason]
            w, l = data["w"], data["l"]
            total = len(w) + len(l)
            if total == 0:
                continue
            wr = len(w) / total * 100
            net = sum(w) + sum(l)
            print(f"    {reason:<16} {total:>4} trades  WR={wr:>5.0f}%  net={net:>+8.4f}R")

    # By decile
    print("\n\n=== BY DECILE ===")
    for name, fs in filter_states.items():
        print(f"\n  {name}:")
        by_decile = defaultdict(lambda: {"w": [], "l": [], "total": 0, "net": 0})
        for t in fs.closed_trades:
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

    # Confirmation pattern analysis (baseline only)
    print("\n\n=== CONFIRMATION PATTERN ANALYSIS (baseline) ===")
    pattern_stats = defaultdict(lambda: {"w": [], "l": [], "total": 0, "net": 0})
    for t in filter_states["A: baseline"].closed_trades:
        c = t.get("confirmations", {})
        if not c:
            continue
        has_vol = c.get("vol", False)
        has_imb = c.get("imb", False)
        n_conf = sum(1 for v in c.values() if v)

        if has_vol and has_imb:
            cat = "vol+imb"
        elif has_vol:
            cat = "vol only"
        elif has_imb:
            cat = "imb only"
        else:
            cat = "neither"

        for key in [cat, f"{n_conf}/6"]:
            pattern_stats[key]["total"] += 1
            pattern_stats[key]["net"] += t["pnl_r"]
            if t["pnl_r"] > 0:
                pattern_stats[key]["w"].append(t["pnl_r"])
            elif t["pnl_r"] < 0:
                pattern_stats[key]["l"].append(t["pnl_r"])

    print(f"\n  {'Pattern':<15} {'Trades':>6} {'WR':>6} {'Net R':>10} {'Avg R':>8} {'PF':>6}")
    print("  " + "-" * 55)
    for cat in ["vol+imb", "vol only", "imb only", "neither", "4/6", "5/6", "6/6"]:
        data = pattern_stats.get(cat)
        if not data or data["total"] == 0:
            continue
        w, l = data["w"], data["l"]
        total = data["total"]
        wr = len(w) / total * 100
        net = data["net"]
        avg = net / total
        gross_w = sum(w)
        gross_l = abs(sum(l))
        pf = gross_w / gross_l if gross_l > 0 else float("inf")
        print(f"  {cat:<15} {total:>6} {wr:>5.0f}% {net:>+10.4f} {avg:>+8.4f} {pf:>6.2f}")

    # Write CSV for each filter
    for name, fs in filter_states.items():
        fname = name.replace(" ", "_").replace(":", "").replace("|", "OR").replace("+", "AND").replace("(", "").replace(")", "")
        csv_path = OUTPUT_DIR / f"confirm_filter_{fname}_trades.csv"
        if fs.closed_trades:
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fs.closed_trades[0].keys())
                writer.writeheader()
                for t in fs.closed_trades:
                    t_copy = dict(t)
                    t_copy["confirmations"] = json.dumps(t.get("confirmations", {}))
                    writer.writerow(t_copy)
            print(f"\nWrote {len(fs.closed_trades)} trades to {csv_path.name}")

    print(f"\n{'='*100}")
    print("DONE")


if __name__ == "__main__":
    run()
