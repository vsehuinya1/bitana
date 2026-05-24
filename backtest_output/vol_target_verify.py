"""
Vol-Targeting Backest Verification
Replay V5 trades with vol-targeted sizing vs flat 4% to verify Gemini's 4x claim.
"""
import csv
import numpy as np
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from engines.liq_cluster_engine_v5 import (
    LiqClusterEngineV5, _compute_aggression, _score_to_decile,
    DECILE_EXITS, CFG, TRADE_DECILES
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

# Vol-targeting parameters (from Gemini's V5.1)
BASE_RISK_PCT = 0.04
TARGET_ATR_PCT = 2.0
MAX_RISK_PCT = 0.12
MIN_RISK_PCT = 0.01


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
            "date": date_str,
            "total_liq": ll + sl,
            "long_liq": ll,
            "short_liq": sl,
            "close": daily_closes.get(date_str, 0),
        })
    return rows


def _atr(highs, lows, closes, period=14):
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    if len(trs) < period:
        return np.mean(trs) if trs else 0.0
    return np.mean(trs[-period:])


def run_backtest(sizing_model, label):
    """
    sizing_model: "flat4" | "vol_target" | "half_kelly"
    """
    print(f"\n{'='*60}")
    print(f"Running: {label}")
    print(f"{'='*60}")

    # Pre-load all data
    all_klines = {}
    all_liq = {}
    all_closes = {}
    for sym in SYMBOLS:
        start_ms = int(START_DATE.timestamp() * 1000)
        end_ms = int(END_DATE.timestamp() * 1000)
        all_klines[sym] = load_klines(sym, start_ms, end_ms)
        all_liq[sym] = load_liq_history(sym)
        all_closes[sym] = load_daily_closes(sym)

    # Build timeline
    timeline = []
    for sym in SYMBOLS:
        for i, c in enumerate(all_klines[sym]):
            timeline.append((c.close_time, sym, i))
    timeline.sort(key=lambda x: x[0])

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

    for close_time, sym, candle_idx in timeline:
        event_count += 1
        sym_candles = all_klines[sym][max(0, candle_idx-199):candle_idx+1]

        # Update liq context
        day_str = close_time.strftime("%Y-%m-%d")
        liq_key = f"{sym}_{day_str}"
        if liq_key not in last_liq_update:
            last_liq_update.add(liq_key)
            day_ts = int(close_time.timestamp())
            daily_rows = build_liq_rows(all_liq[sym], all_closes[sym], day_ts)
            if daily_rows:
                engine.update_daily_liq(sym, daily_rows)

        # Manage existing position
        if sym in open_positions:
            pos = open_positions[sym]
            pos["candles_held"] += 1
            result = engine.manage_position(sym, sym_candles)

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

                closed_trades.append({
                    "symbol": sym, "entry_time": pos["entry_time"].isoformat(),
                    "exit_time": close_time.isoformat(),
                    "entry_price": entry_price, "exit_price": fill_price,
                    "pnl_r": round(pnl_r, 4), "exit_reason": result["reason"],
                    "decile": pos["decile"], "aggression": round(pos["aggression"], 1),
                    "candles_held": pos["candles_held"],
                    "risk_pct": pos["risk_pct"], "equity_after": round(equity, 2),
                })

                st = engine._get_state(sym)
                st.in_trade = False
                del open_positions[sym]

        # Check for new entry
        elif close_time >= WARMUP_END:
            sig = engine.evaluate(sym, sym_candles)
            if sig is not None:
                st = engine._get_state(sym)
                decile = st.decile
                if decile not in TRADE_DECILES:
                    continue

                # Compute ATR for vol-targeting
                closes = np.array([c.close for c in sym_candles])
                highs = np.array([c.high for c in sym_candles])
                lows = np.array([c.low for c in sym_candles])
                atr = _atr(highs, lows, closes, CFG.atr_period)
                entry_price = closes[-1]
                atr_pct = (atr / entry_price) * 100 if entry_price > 0 else 0

                # Determine risk_pct based on sizing model
                if sizing_model == "flat4":
                    risk_pct = 0.04
                elif sizing_model == "vol_target":
                    if atr_pct > 0:
                        risk_pct = BASE_RISK_PCT * (TARGET_ATR_PCT / atr_pct)
                        risk_pct = max(MIN_RISK_PCT, min(MAX_RISK_PCT, risk_pct))
                    else:
                        risk_pct = BASE_RISK_PCT
                elif sizing_model == "half_kelly":
                    hk = {1: 0.009, 2: 0.110, 3: 0.100, 5: 0.184, 6: 0.053, 7: 0.051, 8: 0.160, 9: 0.235}
                    risk_pct = hk.get(decile, 0.04)
                else:
                    risk_pct = 0.04

                stop_price = entry_price - atr * CFG.initial_stop_atr
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
                    "entry_price": fill_price, "stop_price": stop_price,
                    "risk_per_unit": risk_per_unit, "quantity": qty,
                    "decile": decile, "aggression": st.aggression_score,
                    "risk_pct": risk_pct, "entry_time": close_time,
                    "candles_held": 0,
                }

        if event_count % 200000 == 0:
            print(f"  {close_time.strftime('%Y-%m-%d %H:%M')} | events: {event_count} | trades: {len(closed_trades)} | eq: {equity:.0f}", flush=True)

    # Close remaining
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
                "symbol": sym, "entry_time": pos["entry_time"].isoformat(),
                "exit_time": END_DATE.isoformat(),
                "entry_price": entry_price, "exit_price": fill_price,
                "pnl_r": round(pnl_r, 4), "exit_reason": "end_of_backtest",
                "decile": pos["decile"], "aggression": round(pos["aggression"], 1),
                "candles_held": pos["candles_held"],
                "risk_pct": pos["risk_pct"], "equity_after": round(equity, 2),
            })

    # Results
    n = len(closed_trades)
    wins = [t for t in closed_trades if t["pnl_r"] > 0]
    losses = [t for t in closed_trades if t["pnl_r"] < 0]
    wr = len(wins) / max(n, 1) * 100
    total_r = sum(t["pnl_r"] for t in closed_trades)
    gw = sum(t["pnl_r"] for t in wins)
    gl = abs(sum(t["pnl_r"] for t in losses))
    pf = gw / gl if gl > 0 else float("inf")

    print(f"\n{label} RESULTS:")
    print(f"  Trades: {n} ({len(wins)}W / {len(losses)}L)")
    print(f"  WR: {wr:.1f}%")
    print(f"  Total R: {total_r:+.2f}")
    print(f"  PF: {pf:.2f}")
    print(f"  Avg R/trade: {total_r/max(n,1):+.3f}")
    print(f"  Max DD: {max_dd:.1f}%")
    print(f"  Final equity: ${equity:,.2f}")

    # By decile
    print(f"\n  By decile:")
    for d in sorted(set(int(t["decile"]) for t in closed_trades)):
        dt = [t for t in closed_trades if int(t["decile"]) == d]
        if not dt:
            continue
        dn = len(dt)
        dr = sum(t["pnl_r"] for t in dt)
        dw = len([t for t in dt if t["pnl_r"] > 0])
        dgw = sum(t["pnl_r"] for t in dt if t["pnl_r"] > 0)
        dgl = abs(sum(t["pnl_r"] for t in dt if t["pnl_r"] < 0))
        dpf = dgw / dgl if dgl > 0 else float("inf")
        print(f"    D{d}: {dn:3d}t {dr:+7.2f}R WR={dw/dn*100:.0f}% PF={dpf:.2f} avg={dr/dn:+.3f}")

    # Monthly
    print(f"\n  Monthly:")
    monthly = defaultdict(lambda: {"n": 0, "r": 0})
    for t in closed_trades:
        month = t["entry_time"][:7]
        monthly[month]["n"] += 1
        monthly[month]["r"] += t["pnl_r"]
    for m in sorted(monthly.keys()):
        s = monthly[m]
        print(f"    {m}: {s['n']:2d}t {s['r']:+7.2f}R")

    # ATR distribution for vol-target
    if sizing_model == "vol_target":
        atr_vals = [float(t.get("risk_pct", 0.04)) for t in closed_trades]
        print(f"\n  Risk distribution:")
        print(f"    Min: {min(atr_vals):.1%}, Max: {max(atr_vals):.1%}, Avg: {sum(atr_vals)/len(atr_vals):.1%}")
        buckets = defaultdict(int)
        for t in closed_trades:
            rp = t["risk_pct"]
            if rp <= 0.02: buckets["1-2%"] += 1
            elif rp <= 0.04: buckets["2-4%"] += 1
            elif rp <= 0.06: buckets["4-6%"] += 1
            elif rp <= 0.08: buckets["6-8%"] += 1
            elif rp <= 0.10: buckets["8-10%"] += 1
            else: buckets["10%+"] += 1
        for b in sorted(buckets.keys()):
            print(f"    {b}: {buckets[b]} trades")

    return {
        "label": label, "trades": n, "wr": wr, "total_r": total_r,
        "pf": pf, "max_dd": max_dd, "equity": equity
    }


if __name__ == "__main__":
    print("VOL-TARGETING BACKTEST VERIFICATION")
    print("=" * 60)
    print(f"Period: {START_DATE.strftime('%Y-%m-%d')} to {END_DATE.strftime('%Y-%m-%d')}")
    print(f"Symbols: {len(SYMBOLS)}")
    print(f"Deciles: {sorted(TRADE_DECILES)}")
    print(f"Vol-targeting: BASE={BASE_RISK_PCT:.0%}, TARGET_ATR={TARGET_ATR_PCT}%, CAP={MAX_RISK_PCT:.0%}, FLOOR={MIN_RISK_PCT:.0%}")

    results = []

    # Model 1: Flat 4% (baseline)
    r1 = run_backtest("flat4", "Flat 4% (Baseline)")
    results.append(r1)

    # Model 2: Vol-Targeting
    r2 = run_backtest("vol_target", "Vol-Targeting (2.0% ATR)")
    results.append(r2)

    # Model 3: Half-Kelly per decile
    r3 = run_backtest("half_kelly", "Per-Decile Half-Kelly")
    results.append(r3)

    # Summary comparison
    print(f"\n{'='*60}")
    print("COMPARISON SUMMARY:")
    print(f"{'Model':<30} {'Trades':>6} {'WR%':>6} {'Total R':>8} {'PF':>6} {'MaxDD':>7} {'Equity':>12}")
    for r in results:
        print(f"{r['label']:<30} {r['trades']:>6} {r['wr']:>5.1f}% {r['total_r']:>+8.2f} {r['pf']:>6.2f} {r['max_dd']:>6.1f}% ${r['equity']:>11,.0f}")

    # Vol-targeting improvement
    if results[0]["total_r"] != 0:
        improvement = (results[1]["total_r"] / results[0]["total_r"] - 1) * 100
        print(f"\nVol-Targeting vs Flat 4%: {improvement:+.1f}% change in Total R")
        print(f"Gemini claimed: +300% (4x). This test: {improvement:+.1f}%")
