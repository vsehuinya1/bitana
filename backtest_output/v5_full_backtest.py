"""
V5 Full Backtest: Jan 1 – May 20, 2026 (all 28 symbols, all 10 deciles).
Optimized: uses index tracking instead of filtering candle lists.
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


def run_backtest():
    print(f"V5 Full Backtest: {START_DATE.strftime('%Y-%m-%d')} to {END_DATE.strftime('%Y-%m-%d')}")
    print(f"Warmup: {WARMUP_DAYS} days (trades start {WARMUP_END.strftime('%Y-%m-%d')})")
    print(f"Symbols: {len(SYMBOLS)}")
    print()

    # Pre-load all data
    print("Loading data...")
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

    # Build per-symbol index tracking
    # For each symbol, track which candle index we're at
    sym_indices = {sym: 0 for sym in SYMBOLS}
    sym_candle_lists = {sym: [] for sym in SYMBOLS}  # rolling buffer of last 200

    # Build unified timeline: sorted list of (close_time, symbol)
    print("\nBuilding timeline...")
    timeline = []
    for sym in SYMBOLS:
        for i, c in enumerate(all_klines[sym]):
            timeline.append((c.close_time, sym, i))
    timeline.sort(key=lambda x: x[0])
    print(f"Total events: {len(timeline)}")

    # Run simulation
    print("\nRunning simulation...")
    engine = LiqClusterEngineV5()

    open_positions = {}
    closed_trades = []
    equity = 10000.0
    peak_equity = equity
    max_dd = 0.0

    taker_bps = 4.5
    slip_bps = 2.0

    last_liq_update = set()
    event_count = 0
    trade_count = 0

    for close_time, sym, candle_idx in timeline:
        event_count += 1

        # Update rolling candle buffer for this symbol
        sym_candle_lists[sym] = all_klines[sym][max(0, candle_idx-199):candle_idx+1]

        # Update liq context once per day per symbol
        day_str = close_time.strftime("%Y-%m-%d")
        liq_key = f"{sym}_{day_str}"
        if liq_key not in last_liq_update:
            last_liq_update.add(liq_key)
            day_ts = int(close_time.timestamp())
            daily_rows = build_liq_rows(all_liq[sym], all_closes[sym], day_ts)
            if daily_rows:
                engine.update_daily_liq(sym, daily_rows)

        candles = sym_candle_lists[sym]

        # Manage existing position
        if sym in open_positions:
            pos = open_positions[sym]
            pos["candles_held"] += 1

            result = engine.manage_position(sym, candles)
            if result and result["action"] == "close":
                exit_price = result["exit_price"]
                entry_price = pos["entry_price"]
                qty = pos["quantity"]
                risk_per_unit = pos["risk_per_unit"]

                slip = exit_price * (slip_bps / 10000)
                fill_price = exit_price - slip
                fee = qty * fill_price * (taker_bps / 10000)

                pnl = (fill_price - entry_price) * qty - fee
                pnl_r = pnl / (risk_per_unit * qty) if risk_per_unit > 0 and qty > 0 else 0

                equity += pnl
                if equity > peak_equity:
                    peak_equity = equity
                dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
                if dd > max_dd:
                    max_dd = dd

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
                    "equity_after": round(equity, 2),
                }
                closed_trades.append(trade)
                trade_count += 1

                st = engine._get_state(sym)
                st.in_trade = False

                del open_positions[sym]

        # Check for new entry
        elif close_time >= WARMUP_END:
            sig = engine.evaluate(sym, candles)
            if sig is not None:
                st = engine._get_state(sym)
                decile = st.decile
                risk_pct = FLAT_RISK_PCT

                entry_price = sig.entry_price
                stop_price = sig.stop_price
                risk_per_unit = abs(entry_price - stop_price)
                if risk_per_unit <= 0:
                    continue

                risk_amount = equity * risk_pct
                qty = risk_amount / risk_per_unit

                slip = entry_price * (slip_bps / 10000)
                fill_price = entry_price + slip
                fee = qty * fill_price * (taker_bps / 10000)
                equity -= fee

                open_positions[sym] = {
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

        if event_count % 100000 == 0:
            print(f"  {close_time.strftime('%Y-%m-%d %H:%M')} | events: {event_count} | trades: {trade_count} | eq: {equity:.0f}", flush=True)

    # Close remaining positions
    for sym, pos in list(open_positions.items()):
        last_candle = all_klines[sym][-1] if all_klines[sym] else None
        if last_candle:
            exit_price = last_candle.close
            entry_price = pos["entry_price"]
            qty = pos["quantity"]
            risk_per_unit = pos["risk_per_unit"]
            slip = exit_price * (slip_bps / 10000)
            fill_price = exit_price - slip
            fee = qty * fill_price * (taker_bps / 10000)
            pnl = (fill_price - entry_price) * qty - fee
            pnl_r = pnl / (risk_per_unit * qty) if risk_per_unit > 0 and qty > 0 else 0
            equity += pnl
            closed_trades.append({
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
                "equity_after": round(equity, 2),
            })

    # ── Results ──
    print(f"\n{'='*60}")
    print(f"V5 FULL BACKTEST RESULTS (Jan 1 – May 20, 2026)")
    print(f"{'='*60}")

    n = len(closed_trades)
    wins = [t for t in closed_trades if t["pnl_r"] > 0]
    losses = [t for t in closed_trades if t["pnl_r"] < 0]
    wr = len(wins) / max(n, 1) * 100
    total_r = sum(t["pnl_r"] for t in closed_trades)
    gw = sum(t["pnl_r"] for t in wins)
    gl = abs(sum(t["pnl_r"] for t in losses))
    pf = gw / gl if gl > 0 else float("inf")

    print(f"\nTotal trades: {n}")
    print(f"Win rate: {wr:.1f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Total R: {total_r:+.2f}")
    print(f"Profit factor: {pf:.2f}")
    print(f"Avg R/trade: {total_r/max(n,1):+.3f}")
    print(f"Max DD: {max_dd:.1f}%")
    print(f"Final equity: {equity:.2f} (from $10,000)")
    print(f"ROI: {(equity/10000-1)*100:+.1f}%")

    # By decile
    print(f"\n{'='*60}")
    print("BY DECILE:")
    print(f"{'Decile':>6} {'Trades':>6} {'WR%':>6} {'Total R':>8} {'PF':>6} {'Avg R':>7}")
    for d in range(1, 11):
        dt = [t for t in closed_trades if t["decile"] == d]
        if not dt:
            continue
        dn = len(dt)
        dw = len([t for t in dt if t["pnl_r"] > 0])
        dwr = dw / dn * 100
        dr = sum(t["pnl_r"] for t in dt)
        dgw = sum(t["pnl_r"] for t in dt if t["pnl_r"] > 0)
        dgl = abs(sum(t["pnl_r"] for t in dt if t["pnl_r"] < 0))
        dpf = dgw / dgl if dgl > 0 else float("inf")
        dar = dr / dn
        print(f"{'D'+str(d):>6} {dn:>6} {dwr:>6.1f} {dr:>+8.2f} {dpf:>6.2f} {dar:>+7.3f}")

    # By exit reason
    print(f"\nBY EXIT REASON:")
    reasons = defaultdict(lambda: {"n": 0, "r": 0})
    for t in closed_trades:
        reasons[t["exit_reason"]]["n"] += 1
        reasons[t["exit_reason"]]["r"] += t["pnl_r"]
    for r, s in sorted(reasons.items(), key=lambda x: abs(x[1]["r"]), reverse=True):
        print(f"  {r}: {s['n']} trades, {s['r']:+.2f}R")

    # By symbol
    print(f"\nBY SYMBOL (top 10 by |R|):")
    sym_stats = defaultdict(lambda: {"n": 0, "r": 0})
    for t in closed_trades:
        sym_stats[t["symbol"]]["n"] += 1
        sym_stats[t["symbol"]]["r"] += t["pnl_r"]
    for sym, s in sorted(sym_stats.items(), key=lambda x: abs(x[1]["r"]), reverse=True)[:10]:
        print(f"  {sym}: {s['n']} trades, {s['r']:+.2f}R")

    # Monthly breakdown
    print(f"\nBY MONTH:")
    monthly = defaultdict(lambda: {"n": 0, "r": 0})
    for t in closed_trades:
        month = t["entry_time"][:7]
        monthly[month]["n"] += 1
        monthly[month]["r"] += t["pnl_r"]
    for m in sorted(monthly.keys()):
        s = monthly[m]
        print(f"  {m}: {s['n']} trades, {s['r']:+.2f}R")

    # May-only breakdown
    print(f"\nMAY ONLY (May 1-20):")
    may_trades = [t for t in closed_trades if t["entry_time"] >= "2026-05-01"]
    if may_trades:
        mn = len(may_trades)
        mw = len([t for t in may_trades if t["pnl_r"] > 0])
        mwr = mw / mn * 100
        mr = sum(t["pnl_r"] for t in may_trades)
        print(f"  Trades: {mn} | WR: {mwr:.1f}% | R: {mr:+.2f}")
        # By day in May
        daily = defaultdict(lambda: {"n": 0, "r": 0})
        for t in may_trades:
            day = t["entry_time"][:10]
            daily[day]["n"] += 1
            daily[day]["r"] += t["pnl_r"]
        print(f"\n  Daily breakdown:")
        for d in sorted(daily.keys()):
            s = daily[d]
            emoji = "✅" if s["r"] > 0 else "❌" if s["r"] < 0 else "➖"
            print(f"    {d}: {s['n']} trades {s['r']:+.2f}R {emoji}")
    else:
        print("  No trades in May")

    # Save CSV
    csv_path = Path("backtest_output/v5_full_backtest_trades.csv")
    with open(csv_path, "w", newline="") as f:
        if closed_trades:
            writer = csv.DictWriter(f, fieldnames=closed_trades[0].keys())
            writer.writeheader()
            writer.writerows(closed_trades)
    print(f"\nTrades saved to {csv_path}")


if __name__ == "__main__":
    run_backtest()
