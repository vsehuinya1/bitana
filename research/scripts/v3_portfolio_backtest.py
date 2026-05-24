"""
V3 Liq-Cluster Multi-Symbol Portfolio Backtest — FAST VERSION
Uses concurrent requests, 90-day lookback, production engine.
"""
import sys, os, time, json, math
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, '/root/bitana')
sys.path.insert(0, '/root/bitana/engines')
from liq_cluster_engine import LiqClusterEngine, CascadeTracker, V3Config

COINALYZE_KEY = "be291954-992e-489d-8ab5-5d34a0dfcf41"
BINANCE_BASE  = "https://fapi.binance.com"

SYMBOLS = [
    "NEARUSDT","ZECUSDT","ADAUSDT","WLDUSDT","UNIUSDT","NMRUSDT",
    "PENDLEUSDT","ARBUSDT","RENDERUSDT","RUNEUSDT","FETUSDT","DOTUSDT",
    "TONUSDT","SOLUSDT","1000LUNCUSDT","ENAUSDT","1000PEPEUSDT",
    "XRPUSDT","FILUSDT","BNBUSDT","TAOUSDT","CHZUSDT","DASHUSDT",
    "QNTUSDT","ICPUSDT","XLMUSDT","APTUSDT","ETHUSDT",
]

INITIAL_EQUITY = 10000.0
MAX_POSITIONS = 10
BASE_RISK_PCT = 2.0
MAX_LEVERAGE = 10
TAKER_BPS = 4.5
SLIP_BPS = 2.0
LOOKBACK_DAYS = 90  # 90 days of 5m data — enough for signal generation

OUT_DIR = Path("/root/bitana/research/output/reports")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Concurrent data fetching ────────────────────────────────────────────────

def fetch_coinalyze_daily(symbol, days=400):
    ca_sym = f"{symbol}_PERP.A"
    now = int(time.time())
    fr = now - days * 86400
    try:
        # Use list of tuples to preserve 'from' param name (not 'from_')
        params = [("symbols", ca_sym), ("interval", "daily"),
                  ("from", str(fr)), ("to", str(now)), ("api_key", COINALYZE_KEY)]
        resp = requests.get("https://api.coinalyze.net/v1/liquidation-history",
                            params=params, timeout=15)
        if resp.status_code != 200:
            return symbol, []
        data = resp.json()
        if not isinstance(data, list) or not data:
            return symbol, []
        rows = []
        for h in data[0].get("history", []):
            dt = datetime.fromtimestamp(h["t"], tz=timezone.utc).strftime("%Y-%m-%d")
            rows.append({"date": dt, "total_liq": h.get("l",0)+h.get("s",0),
                         "long_liq": h.get("l",0), "short_liq": h.get("s",0)})
        return symbol, rows
    except:
        return symbol, []

def fetch_binance_5m_fast(symbol, days=90):
    """Fetch 5m klines — single request with large limit."""
    try:
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=days)
        resp = requests.get(
            f"{BINANCE_BASE}/fapi/v1/klines",
            params=dict(symbol=symbol, interval="5m",
                        startTime=int(start_dt.timestamp()*1000),
                        endTime=int(end_dt.timestamp()*1000),
                        limit=1500),
            timeout=15,
        )
        if resp.status_code != 200:
            return symbol, pd.DataFrame()
        klines = resp.json()
        if not klines:
            return symbol, pd.DataFrame()
        df = pd.DataFrame(klines, columns=[
            "open_time","open","high","low","close","volume","close_time",
            "quote_vol","trades","taker_buy_base","taker_buy_quote","ignore"])
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        for c in ["open","high","low","close","volume"]:
            df[c] = df[c].astype(float)
        return symbol, df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    except:
        return symbol, pd.DataFrame()

def fetch_binance_daily(symbol, days=400):
    try:
        resp = requests.get(f"{BINANCE_BASE}/fapi/v1/klines",
            params=dict(symbol=symbol, interval="1d", limit=days), timeout=15)
        if resp.status_code != 200:
            return symbol, {}
        return symbol, {datetime.fromtimestamp(k[0]/1000, tz=timezone.utc).strftime("%Y-%m-%d"): float(k[4])
                        for k in resp.json()}
    except:
        return symbol, {}

# ── Candle wrapper ──────────────────────────────────────────────────────────

class SimpleCandle:
    def __init__(self, row, sym):
        self.symbol = sym
        self.timeframe = "5m"
        self.open_time = row["open_time"]
        self.close_time = row["open_time"] + timedelta(minutes=5)
        self.open = float(row["open"])
        self.high = float(row["high"])
        self.low = float(row["low"])
        self.close = float(row["close"])
        self.volume = float(row["volume"])
        self.is_closed = True

def make_candles(df, sym):
    return [SimpleCandle(row.to_dict(), sym) for _, row in df.iterrows()]

# ── Per-Symbol Backtest ─────────────────────────────────────────────────────

def backtest_symbol(sym, df_5m, liq_rows, daily_closes):
    engine = LiqClusterEngine()
    for row in liq_rows:
        row["close"] = daily_closes.get(row["date"], 0)

    liq_by_date = {}
    running = []
    for row in sorted(liq_rows, key=lambda r: r["date"]):
        running.append(row)
        liq_by_date[row["date"]] = list(running)

    trades = []
    current_date = None
    entry_times = {}

    for idx in range(len(df_5m)):
        row = df_5m.iloc[idx]
        bar_date = row["open_time"].strftime("%Y-%m-%d")

        if bar_date != current_date:
            current_date = bar_date
            if bar_date in liq_by_date:
                engine.update_daily_liq(sym, liq_by_date[bar_date])

        sub = df_5m.iloc[:idx+1]
        candles = make_candles(sub, sym)

        if len(candles) < 300:
            continue

        st = engine._get_state(sym)
        if st.in_trade:
            result = engine.manage_position(sym, candles[-1], candles)
            if result:
                trades.append({
                    "symbol": sym,
                    "entry_price": st.entry_price,
                    "exit_price": result.get("exit_price", candles[-1].close),
                    "entry_time": entry_times.get(sym, row["open_time"]),
                    "exit_time": row["open_time"],
                    "pnl_r": result.get("r", 0),
                    "exit_reason": result["reason"],
                    "mae": result.get("mae", 0),
                    "mfe": result.get("mfe", 0),
                    "bars_held": result.get("bars_held", 0),
                    "cascade_strength": st.cascade_strength,
                    "vol_z": 0,
                    "atr_at_entry": st.risk_per_unit / V3Config().initial_stop_atr if st.risk_per_unit else 0,
                    "confirm_count": 0, "body_strength": 0, "bar_return_pct": 0,
                })
                entry_times.pop(sym, None)
                continue

        sig = engine.evaluate(sym, candles)
        if sig and sig.risk_distance > 0:
            entry_times[sym] = row["open_time"]
            trades.append({
                "symbol": sym,
                "entry_price": sig.entry_price,
                "exit_price": 0,
                "entry_time": row["open_time"],
                "exit_time": None,
                "pnl_r": 0,
                "exit_reason": "open",
                "mae": 0, "mfe": 0, "bars_held": 0,
                "cascade_strength": sig.signal_data.get("cascade_strength", 0),
                "vol_z": sig.signal_data.get("vol_z", 0),
                "atr_at_entry": sig.signal_data.get("atr", 0),
                "confirm_count": sig.signal_data.get("confirm_count", 0),
                "body_strength": sig.signal_data.get("body_strength", 0),
                "bar_return_pct": sig.signal_data.get("bar_return_pct", 0),
            })

    return trades

# ── Portfolio Simulator ─────────────────────────────────────────────────────

def simulate_portfolio(all_trades, sizing="flat"):
    equity = INITIAL_EQUITY
    peak = equity
    closed = []

    sym_trades_map = defaultdict(list)
    for t in all_trades:
        sym_trades_map[t["symbol"]].append(t)

    for sym, sym_trades in sym_trades_map.items():
        for key in ["vol_z", "cascade_strength"]:
            vals = [t.get(key, 0) or 0 for t in sym_trades]
            min_v, max_v = min(vals), max(vals)
            rng = max_v - min_v if max_v > min_v else 1
            for t in sym_trades:
                t[f"{key}_norm"] = (t.get(key, 0) or 0 - min_v) / rng
        for t in sym_trades:
            t["composite"] = (
                t.get("vol_z_norm", 0.5) * 0.35 +
                t.get("cascade_strength_norm", 0.5) * 0.35 +
                (t.get("confirm_count", 0) / 5.0) * 0.30
            )

    sym_quality = {}
    for sym, sym_trades in sym_trades_map.items():
        n = len(sym_trades)
        if n < 2:
            continue
        wr = sum(1 for t in sym_trades if t.get("pnl_r", 0) > 0) / n
        wins = sum(t["pnl_r"] for t in sym_trades if t.get("pnl_r", 0) > 0)
        losses = abs(sum(t["pnl_r"] for t in sym_trades if t.get("pnl_r", 0) < 0))
        pf = wins / losses if losses > 0 else 1.0
        r_cum = pd.Series([t["pnl_r"] for t in sym_trades]).cumsum()
        max_dd = (r_cum.cummax() - r_cum).max()
        quality = pf * math.log(n + 1) * wr / (1 + max_dd)
        sym_quality[sym] = {"n": n, "wr": wr, "pf": pf, "max_dd": max_dd, "quality": quality}

    if sym_quality:
        qs = [v["quality"] for v in sym_quality.values()]
        q_min, q_max = min(qs), max(qs)
        rng = q_max - q_min if q_max > min(qs) else 1
        for sym in sym_quality:
            sym_quality[sym]["quality_norm"] = (sym_quality[sym]["quality"] - q_min) / rng

    sym_median_atr = {}
    for sym, sym_trades in sym_trades_map.items():
        atrs = [t.get("atr_at_entry", 0) or 0 for t in sym_trades if t.get("atr_at_entry", 0) > 0]
        sym_median_atr[sym] = float(np.median(atrs)) if atrs else 0

    sorted_trades = sorted(all_trades, key=lambda t: t.get("entry_time", datetime.min.replace(tzinfo=timezone.utc)))

    for trade in sorted_trades:
        sym = trade["symbol"]
        pnl_r = trade.get("pnl_r", 0)
        entry_price = trade.get("entry_price", 0)
        atr = trade.get("atr_at_entry", 0) or 0

        if entry_price <= 0 or trade.get("exit_reason") == "open":
            continue

        if sizing == "flat":
            risk_pct = BASE_RISK_PCT
        elif sizing == "rank_weight":
            comp = trade.get("composite", 0.5)
            risk_pct = BASE_RISK_PCT * (0.5 + 1.5 * comp)
        elif sizing == "vol_target":
            med_atr = sym_median_atr.get(sym, atr)
            ratio = (med_atr / atr) if (med_atr > 0 and atr > 0) else 1.0
            ratio = max(0.5, min(2.0, ratio))
            risk_pct = BASE_RISK_PCT * ratio
        elif sizing == "bayesian":
            q = sym_quality.get(sym, {}).get("quality_norm", 0.5)
            risk_pct = BASE_RISK_PCT * (0.5 + 2.0 * q)
        else:
            risk_pct = BASE_RISK_PCT

        stop_dist = atr * V3Config().initial_stop_atr
        if stop_dist <= 0:
            continue

        risk_amt = equity * (risk_pct / 100.0)
        qty = risk_amt / stop_dist
        notional = qty * entry_price
        lev = min(int(notional / equity) + 1, MAX_LEVERAGE) if equity > 0 else 1
        lev = max(lev, 1)
        max_not = equity * lev * 0.95
        if notional > max_not:
            qty = max_not / entry_price
        if qty <= 0:
            continue

        exit_price = trade.get("exit_price", entry_price)
        pnl = (exit_price - entry_price) * qty
        fees = qty * entry_price * (TAKER_BPS / 10000) + qty * exit_price * (TAKER_BPS / 10000)
        slip = qty * entry_price * (SLIP_BPS / 10000) + qty * exit_price * (SLIP_BPS / 10000)
        net_pnl = pnl - fees - slip

        equity += net_pnl
        if equity > peak:
            peak = equity

        closed.append({
            "symbol": sym, "entry_time": trade.get("entry_time"), "exit_time": trade.get("exit_time"),
            "pnl_r": pnl_r, "pnl_usd": net_pnl, "risk_pct": risk_pct,
            "exit_reason": trade.get("exit_reason", ""), "equity_after": equity,
        })

    n = len(closed)
    if n == 0:
        return {"n": 0, "pf": 0, "wr": 0, "net_pnl": 0, "max_dd": 0, "sharpe": 0, "final_equity": INITIAL_EQUITY, "trades": []}

    wins = [t for t in closed if t["pnl_usd"] > 0]
    losses = [t for t in closed if t["pnl_usd"] <= 0]
    gross_win = sum(t["pnl_usd"] for t in wins)
    gross_loss = abs(sum(t["pnl_usd"] for t in losses))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    wr = len(wins) / n * 100
    net_pnl = sum(t["pnl_usd"] for t in closed)

    eq_curve = pd.Series([t["equity_after"] for t in closed])
    rolling_max = eq_curve.cummax()
    max_dd = float(((rolling_max - eq_curve) / rolling_max * 100).max()) if len(eq_curve) > 0 else 0

    returns = eq_curve.pct_change().dropna()
    sharpe = float(returns.mean() / returns.std() * math.sqrt(252)) if returns.std() > 0 else 0

    return {"n": n, "pf": pf, "wr": wr, "net_pnl": net_pnl, "max_dd": max_dd, "sharpe": sharpe, "final_equity": equity, "trades": closed}

# ── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("V3 LIQ-CLUSTER MULTI-SYMBOL PORTFOLIO BACKTEST (FAST)")
    print(f"  {len(SYMBOLS)} symbols | {INITIAL_EQUITY} equity | {LOOKBACK_DAYS}d lookback")
    print("=" * 60)

    # ── Step 1: Fetch data concurrently ──────────────────────────────────
    print(f"\n[1] Fetching data for {len(SYMBOLS)} symbols (concurrent)...")
    sym_data = {}

    with ThreadPoolExecutor(max_workers=6) as executor:
        # Submit all Coinalyze requests
        coinalyze_futures = {executor.submit(fetch_coinalyze_daily, sym, 400): sym for sym in SYMBOLS}
        # Submit all Binance daily requests
        daily_futures = {executor.submit(fetch_binance_daily, sym, 400): sym for sym in SYMBOLS}
        # Submit all Binance 5m requests
        binance_futures = {executor.submit(fetch_binance_5m_fast, sym, LOOKBACK_DAYS): sym for sym in SYMBOLS}

        # Collect Coinalyze results
        for future in as_completed(coinalyze_futures):
            sym, liq = future.result()
            if sym not in sym_data:
                sym_data[sym] = {}
            sym_data[sym]["liq"] = liq

        # Collect daily results
        for future in as_completed(daily_futures):
            sym, daily = future.result()
            if sym not in sym_data:
                sym_data[sym] = {}
            sym_data[sym]["daily"] = daily

        # Collect 5m results
        for future in as_completed(binance_futures):
            sym, df = future.result()
            if sym not in sym_data:
                sym_data[sym] = {}
            sym_data[sym]["df"] = df

    # Filter to symbols with all data
    loaded = {sym: d for sym, d in sym_data.items()
              if d.get("df") is not None and not d["df"].empty and d.get("liq")}
    print(f"  Loaded: {len(loaded)}/{len(SYMBOLS)} symbols")

    # ── Step 2: Per-symbol backtests ────────────────────────────────────
    print(f"\n[2] Running per-symbol backtests...")
    all_trades = []
    for sym, data in loaded.items():
        for row in data["liq"]:
            row["close"] = data["daily"].get(row["date"], 0)
        trades = backtest_symbol(sym, data["df"], data["liq"], data["daily"])
        all_trades.extend(trades)
        print(f"  {sym}: {len(trades)} trades")

    print(f"\n  Total raw trades: {len(all_trades)}")

    if not all_trades:
        print("  NO TRADES — check data quality")
        return

    # ── Step 3: Portfolio simulation ────────────────────────────────────
    print(f"\n[3] Portfolio simulation (4 sizing variants)...")
    results = {}
    for sizing in ["flat", "rank_weight", "vol_target", "bayesian"]:
        results[sizing] = simulate_portfolio(all_trades, sizing)
        r = results[sizing]
        print(f"  {sizing:<15} n={r['n']:>3}  PF={r['pf']:>5.2f}  WR={r['wr']:>5.1f}%  "
              f"Net=${r['net_pnl']:>8.2f}  MaxDD={r['max_dd']:>5.1f}%  Sharpe={r['sharpe']:>5.2f}")

    # ── Step 4: Report ──────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("RESULTS SUMMARY")
    print(f"{'='*60}")
    print(f"\n{'Method':<15} {'Trades':>6} {'PF':>6} {'WR%':>6} {'Net$':>10} {'MaxDD%':>8} {'Sharpe':>7} {'FinalEq':>10}")
    print("-" * 75)
    for sizing, r in results.items():
        print(f"{sizing:<15} {r['n']:>6} {r['pf']:>6.2f} {r['wr']:>6.1f} ${r['net_pnl']:>9.2f} {r['max_dd']:>7.1f}% {r['sharpe']:>7.2f} ${r['final_equity']:>9.2f}")

    # Improvement vs baseline
    flat_r = results.get("flat", {})
    print(f"\n{'='*60}")
    print("IMPROVEMENT VS FLAT")
    print(f"{'='*60}")
    for method, r in results.items():
        if method == "flat" or flat_r.get("n", 0) == 0:
            continue
        print(f"  {method:<15} PF {r['pf']-flat_r['pf']:>+.2f}  Sharpe {r['sharpe']-flat_r['sharpe']:>+.2f}  "
              f"MaxDD {r['max_dd']-flat_r['max_dd']:>+.1f}%  PnL ${r['net_pnl']-flat_r['net_pnl']:>+9.2f}")

    # Per-symbol
    print(f"\n{'='*60}")
    print("PER-SYMBOL BREAKDOWN")
    print(f"{'='*60}")
    trades_df = pd.DataFrame(all_trades)
    print(f"{'Symbol':<15} {'N':>4} {'PF':>6} {'WR%':>6} {'NetR':>8} {'AvgR':>7} {'Cascade':>8} {'VolZ':>7}")
    print("-" * 70)
    for sym in sorted(trades_df["symbol"].unique()):
        st = trades_df[trades_df["symbol"] == sym]
        n = len(st)
        wr = (st["pnl_r"] > 0).mean() * 100
        wins = st[st["pnl_r"] > 0]["pnl_r"].sum()
        losses = abs(st[st["pnl_r"] < 0]["pnl_r"].sum())
        pf = wins / losses if losses > 0 else float("inf")
        print(f"  {sym:<13} {n:>4} {pf:>6.2f} {wr:>5.1f}% {st['pnl_r'].sum():>7.2f}R {st['pnl_r'].mean():>6.3f}R "
              f"{st['cascade_strength'].mean():>7.2f} {st['vol_z'].mean():>6.2f}")

    # Exit reasons
    print(f"\n{'='*60}")
    print("EXIT REASONS")
    print(f"{'='*60}")
    for reason in trades_df["exit_reason"].value_counts().index:
        rt = trades_df[trades_df["exit_reason"] == reason]
        n = len(rt)
        wr = (rt["pnl_r"] > 0).mean() * 100
        print(f"  {reason:<20} {n:>4}t  WR {wr:>5.1f}%  Net {rt['pnl_r'].sum():>6.2f}R")

    # Winners vs Losers
    print(f"\n{'='*60}")
    print("WINNERS vs LOSERS (signal features)")
    print(f"{'='*60}")
    winners = trades_df[trades_df["pnl_r"] > 0]
    losers = trades_df[trades_df["pnl_r"] <= 0]
    for col in ["cascade_strength", "vol_z", "body_strength", "bar_return_pct", "atr_at_entry"]:
        if col in trades_df.columns:
            w = winners[col].mean() if len(winners) > 0 else 0
            l = losers[col].mean() if len(losers) > 0 else 0
            pooled = np.sqrt((winners[col].var() + losers[col].var()) / 2) if len(winners) > 1 and len(losers) > 1 else 1
            d = (w - l) / pooled if pooled > 0 else 0
            print(f"  {col:<20} W={w:>8.3f}  L={l:>8.3f}  d={d:>+.2f}")

    # Save
    out = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbols": list(loaded.keys()),
        "n_symbols": len(loaded),
        "n_trades": len(all_trades),
        "results": {k: {kk: vv for kk, vv in v.items() if kk != "trades"} for k, v in results.items()},
    }
    with open(OUT / "portfolio_backtest_results.json", "w") as f:
        json.dump(out, f, indent=2, default=str)
    trades_df.to_parquet(OUT / "portfolio_backtest_trades.parquet", index=False)
    print(f"\n  Saved: {OUT}/portfolio_backtest_results.json")

if __name__ == "__main__":
    main()
