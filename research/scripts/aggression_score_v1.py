"""
Aggression Score V1 — Composite Variable Builder
=================================================
Builds a continuous aggression score from 10 micro-structure variables:
1. Taker imbalance z-score (aggressive orderflow)
2. Delta persistence (consecutive directional bars)
3. OI acceleration (rate of OI change)
4. Range expansion percentile (current range vs history)
5. Volume concentration (volume in directional bars vs total)
6. Close location value (close position within bar range)
7. Wick rejection (rejection wicks = absorption)
8. Spread expansion (bid-ask spread widening)
9. Velocity of move (price change per unit time)
10. Cascade intensity (liquidation spike magnitude)

Then buckets trades by aggression decile and analyzes expectancy by tier.
"""
import csv, ast, math
import numpy as np
import sqlite3
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path("/root/bitana/backtest_data")
OUTPUT_DIR = Path("/root/bitana/backtest_output")
KLINES_DB = DATA_DIR / "klines_5m.db"
LIQ_DB = DATA_DIR / "coinalyze_liq.db"

ALL_SYMBOLS = [
    "NEARUSDT","ZECUSDT","ADAUSDT","WLDUSDT","UNIUSDT","NMRUSDT","PENDLEUSDT",
    "ARBUSDT","RENDERUSDT","RUNEUSDT","FETUSDT","DOTUSDT","TONUSDT","SOLUSDT",
    "1000LUNCUSDT","ENAUSDT","1000PEPEUSDT","XRPUSDT","FILUSDT","BNBUSDT",
    "TAOUSDT","CHZUSDT","DASHUSDT","QNTUSDT","ICPUSDT","XLMUSDT","APTUSDT","ETHUSDT",
]

# ═══════════════════════════════════════════════════════════════════════
# Load 5m candles for aggression variable computation
# ═══════════════════════════════════════════════════════════════════════

def load_5m_candles(symbol):
    conn = sqlite3.connect(str(KLINES_DB))
    rows = conn.execute(
        "SELECT open_time, close_time, open, high, low, close, volume, taker_buy_volume "
        "FROM klines WHERE symbol=? AND open_time >= ? AND open_time <= ? ORDER BY open_time",
        (symbol, 1767225600000, 1777593599000)
    ).fetchall()
    conn.close()
    if not rows:
        return None
    n = len(rows)
    return {
        "symbol": symbol,
        "ot": np.array([r[0] for r in rows], dtype=np.int64),
        "ct": np.array([r[1] for r in rows], dtype=np.int64),
        "o": np.array([r[2] for r in rows], dtype=np.float64),
        "h": np.array([r[3] for r in rows], dtype=np.float64),
        "l": np.array([r[4] for r in rows], dtype=np.float64),
        "c": np.array([r[5] for r in rows], dtype=np.float64),
        "v": np.array([r[6] for r in rows], dtype=np.float64),
        "tbv": np.array([r[7] for r in rows], dtype=np.float64),
        "n": n,
    }

def load_liq(symbol):
    conn = sqlite3.connect(str(LIQ_DB))
    rows = conn.execute(
        "SELECT timestamp, long_liq, short_liq FROM liquidation_history "
        "WHERE symbol=? ORDER BY timestamp",
        (f"{symbol}_PERP.A",)
    ).fetchall()
    conn.close()
    if not rows:
        return None
    return {
        "t": np.array([r[0] for r in rows], dtype=np.int64),
        "ll": np.array([r[1] for r in rows], dtype=np.float64),
        "sl": np.array([r[2] for r in rows], dtype=np.float64),
        "n": len(rows),
    }

# ═══════════════════════════════════════════════════════════════════════
# Aggression Score Components
# ═══════════════════════════════════════════════════════════════════════

def compute_aggression_variables(candles, liq_data, entry_bar_idx):
    """
    Compute 10 aggression micro-structure variables at entry bar.
    Returns dict of raw values and normalized scores.
    """
    i = entry_bar_idx
    n = candles["n"]
    if i < 60 or i >= n:
        return None

    o, h, l, c, v, tbv = candles["o"], candles["h"], candles["l"], candles["c"], candles["v"], candles["tbv"]

    # ── 1. Taker Imbalance Z-Score ──
    # Measures aggressive orderflow direction
    if tbv[i] > 0 and v[i] > 0:
        taker_sells = v[i] - tbv[i]
        imb_raw = (tbv[i] - taker_sells) / v[i]
        # Z-score vs recent window
        imb_window = []
        for j in range(max(0, i-100), i):
            if tbv[j] > 0 and v[j] > 0:
                ts = v[j] - tbv[j]
                imb_window.append((tbv[j] - ts) / v[j])
        if len(imb_window) > 10:
            imb_z = (imb_raw - np.mean(imb_window)) / max(np.std(imb_window), 1e-12)
        else:
            imb_z = 0.0
    else:
        imb_z = 0.0
        imb_raw = 0.0

    # ── 2. Delta Persistence ──
    # Consecutive bars with same direction (buying/selling pressure)
    delta = 0
    for j in range(i, max(i-10, -1), -1):
        if c[j] > o[j]:  # green bar
            if j == i or delta >= 0:
                delta += 1
            else:
                break
        elif c[j] < o[j]:  # red bar
            if j == i or delta <= 0:
                delta -= 1
            else:
                break
        else:  # doji
            break
    delta_persistence = delta  # positive = buying persistence

    # ── 3. Range Expansion Percentile ──
    # Current bar range vs recent distribution
    cur_range = h[i] - l[i]
    hist_ranges = h[max(0,i-60):i] - l[max(0,i-60):i]
    if len(hist_ranges) > 10 and np.mean(hist_ranges) > 0:
        range_pctile = np.sum(hist_ranges <= cur_range) / len(hist_ranges) * 100
    else:
        range_pctile = 50.0

    # ── 4. Volume Concentration ──
    # Volume in directional bars vs total over lookback
    lookback = 20
    start = max(0, i - lookback)
    total_vol = np.sum(v[start:i+1])
    if total_vol > 0:
        if c[i] > o[i]:  # green bar — buying vol
            dir_vol = sum(v[j] for j in range(start, i+1) if c[j] > o[j])
        else:
            dir_vol = sum(v[j] for j in range(start, i+1) if c[j] < o[j])
        vol_concentration = dir_vol / total_vol * 100
    else:
        vol_concentration = 50.0

    # ── 5. Close Location Value (CLV) ──
    # Where close sits within the bar range
    bar_range = h[i] - l[i]
    if bar_range > 0:
        clv = (c[i] - l[i]) / bar_range  # 0 = at low, 1 = at high
    else:
        clv = 0.5

    # ── 6. Wick Rejection ──
    # Long wicks with close near opposite end = absorption/rejection
    body_top = max(o[i], c[i])
    body_bot = min(o[i], c[i])
    upper_wick = h[i] - body_top
    lower_wick = body_bot - l[i]
    body_size = body_top - body_bot

    if bar_range > 0:
        # Rejection score: large wick opposite to direction + close near extreme
        if c[i] > o[i]:  # green bar — lower wick rejection is bullish
            wick_rejection = lower_wick / bar_range
        else:  # red bar — upper wick rejection is bearish (but we're long, so negative)
            wick_rejection = -upper_wick / bar_range
    else:
        wick_rejection = 0.0

    # ── 7. Velocity of Move ──
    # Price change per bar over recent window
    if i >= 5:
        velocity = (c[i] - c[i-5]) / c[i-5] * 100 if c[i-5] > 0 else 0
    else:
        velocity = 0.0

    # ── 8. Cascade Intensity ──
    # Liquidation spike magnitude from Coinalyze
    cascade_intensity = 0.0
    if liq_data is not None and liq_data["n"] > 0:
        bar_date = datetime.fromtimestamp(candles["ot"][i] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        # Find closest liq data point
        for j in range(liq_data["n"]):
            liq_date = datetime.fromtimestamp(liq_data["t"][j], tz=timezone.utc).strftime("%Y-%m-%d")
            if liq_date == bar_date:
                total_liq = liq_data["ll"][j] + liq_data["sl"][j]
                # Normalize by recent average
                recent_liqs = []
                for k in range(max(0, j-30), j+1):
                    recent_liqs.append(liq_data["ll"][k] + liq_data["sl"][k])
                avg_liq = np.mean(recent_liqs) if recent_liqs else total_liq
                cascade_intensity = total_liq / avg_liq if avg_liq > 0 else 1.0
                break

    # ── 9. OI Acceleration (proxy from price/volume) ──
    # Rising price + rising volume = OI expansion (new positions being opened)
    if i >= 10:
        price_change = (c[i] - c[i-10]) / c[i-10] if c[i-10] > 0 else 0
        vol_change = (np.mean(v[max(0,i-5):i+1]) - np.mean(v[max(0,i-10):i-5])) / max(np.mean(v[max(0,i-10):i-5]), 1e-12)
        oi_acceleration = price_change * vol_change * 1000  # scaled
    else:
        oi_acceleration = 0.0

    # ── 10. Spread Expansion (proxy from wick asymmetry) ──
    # Wide spreads create wick asymmetry
    if bar_range > 0:
        spread_proxy = abs(upper_wick - lower_wick) / bar_range
    else:
        spread_proxy = 0.0

    return {
        "taker_imb_z": float(imb_z),
        "delta_persistence": float(delta_persistence),
        "range_expansion_pctile": float(range_pctile),
        "volume_concentration": float(vol_concentration),
        "clv": float(clv),
        "wick_rejection": float(wick_rejection),
        "velocity": float(velocity),
        "cascade_intensity": float(cascade_intensity),
        "oi_acceleration": float(oi_acceleration),
        "spread_expansion": float(spread_proxy),
    }


def normalize_to_unit(value, method="zscore", percentile=None):
    """Normalize a value to roughly [-1, 1] or [0, 1] range."""
    if method == "zscore":
        # Clip z-score to [-3, 3] then map to [-1, 1]
        return max(-1.0, min(1.0, value / 3.0))
    elif method == "percentile":
        # Map percentile [0, 100] to [0, 1]
        return value / 100.0
    elif method == "sigmoid":
        return 2.0 / (1.0 + math.exp(-value)) - 1.0
    return value


def compute_aggression_score(variables):
    """
    Compute composite aggression score from normalized components.
    Returns score in [0, 1] range (higher = more aggressive).
    """
    if variables is None:
        return 0.5

    # Normalize each component
    scores = {
        # Taker imbalance: positive z = aggressive buying → higher aggression
        "taker_imb": normalize_to_unit(variables["taker_imb_z"], "zscore"),

        # Delta persistence: more consecutive directional bars → higher aggression
        "delta_persist": normalize_to_unit(variables["delta_persistence"], "sigmoid"),

        # Range expansion: higher percentile → more aggressive
        "range_exp": normalize_to_unit(variables["range_expansion_pctile"], "percentile"),

        # Volume concentration: higher = more directional conviction
        "vol_conc": normalize_to_unit(variables["volume_concentration"], "percentile"),

        # CLV: close near high = aggressive (for longs)
        "clv": variables["clv"],  # already [0, 1]

        # Wick rejection: positive = absorption in our direction
        "wick_rej": normalize_to_unit(variables["wick_rejection"], "zscore"),

        # Velocity: faster move = more aggressive
        "velocity": normalize_to_unit(variables["velocity"], "sigmoid"),

        # Cascade intensity: higher liq spike = more aggressive
        "cascade": normalize_to_unit(variables["cascade_intensity"] - 1.0, "sigmoid"),

        # OI acceleration: positive = new positions opening in our direction
        "oi_accel": normalize_to_unit(variables["oi_acceleration"], "zscore"),

        # Spread expansion: wider spreads = more aggressive
        "spread": normalize_to_unit(variables["spread_expansion"], "percentile"),
    }

    # Weighted composite
    weights = {
        "taker_imb": 0.15,
        "delta_persist": 0.10,
        "range_exp": 0.10,
        "vol_conc": 0.10,
        "clv": 0.10,
        "wick_rej": 0.05,
        "velocity": 0.10,
        "cascade": 0.15,
        "oi_accel": 0.10,
        "spread": 0.05,
    }

    composite = sum(scores[k] * weights[k] for k in weights)

    # Map from [-1, 1] to [0, 1]
    return (composite + 1.0) / 2.0


# ═══════════════════════════════════════════════════════════════════════
# Main: Load baseline trades, compute aggression, analyze by decile
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("="*70)
    print("AGGRESSION SCORE V1 — Analysis")
    print("="*70)

    # Load baseline trades
    print("\n[1] Loading baseline trades...")
    with open(OUTPUT_DIR / "baseline_trades.csv") as f:
        trades = list(csv.DictReader(f))
    print(f"  {len(trades)} trades loaded")

    # Load candle data for all symbols
    print("\n[2] Loading candle data...")
    candles_cache = {}
    liq_cache = {}
    for sym in ALL_SYMBOLS:
        candles_cache[sym] = load_5m_candles(sym)
        liq_cache[sym] = load_liq(sym)
        if candles_cache[sym]:
            liq_str = f"{liq_cache[sym]['n']} rows" if liq_cache[sym] else "no liq"
            print(f"  {sym}: {candles_cache[sym]['n']} candles, {liq_str}")

    # Compute aggression score for each trade
    print("\n[3] Computing aggression scores...")
    trades_with_aggression = []
    errors = 0

    for t in trades:
        sym = t["symbol"]
        candles = candles_cache.get(sym)
        liq = liq_cache.get(sym)

        if candles is None:
            errors += 1
            continue

        # Find entry bar index
        entry_time = int(datetime.fromisoformat(t["entry_time"]).timestamp() * 1000)
        entry_idx = np.searchsorted(candles["ct"], entry_time)
        if entry_idx >= candles["n"] or candles["ct"][entry_idx] != entry_time:
            # Try open_time
            entry_idx = np.searchsorted(candles["ot"], entry_time)
            if entry_idx >= candles["n"]:
                errors += 1
                continue

        variables = compute_aggression_variables(candles, liq, entry_idx)
        if variables is None:
            errors += 1
            continue

        score = compute_aggression_score(variables)

        trade_data = {**t, "aggression_score": round(score, 4)}
        trade_data["aggression_vars"] = str(variables)
        trades_with_aggression.append(trade_data)

    print(f"  Computed aggression for {len(trades_with_aggression)}/{len(trades)} trades ({errors} errors)")

    # Save enriched trades
    print("\n[4] Saving enriched trades...")
    if trades_with_aggression:
        fieldnames = list(trades_with_aggression[0].keys())
        with open(OUTPUT_DIR / "baseline_trades_aggression.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for t in trades_with_aggression:
                writer.writerow(t)
        print(f"  Saved to baseline_trades_aggression.csv")

    # ═══════════════════════════════════════════════════════════════════════
    # Bucket by aggression decile and analyze
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[5] Analyzing by aggression decile...")

    if not trades_with_aggression:
        print("  No trades with aggression scores!")
        return

    scores = [t["aggression_score"] for t in trades_with_aggression]
    percentiles = np.percentile(scores, [10, 20, 30, 40, 50, 60, 70, 80, 90])

    def get_decile(score):
        for i, p in enumerate(percentiles):
            if score <= p:
                return i + 1
        return 10

    for t in trades_with_aggression:
        t["aggression_decile"] = get_decile(t["aggression_score"])

    # Analyze by decile
    print("\n" + "="*70)
    print("EXPECTANCY BY AGGRESSION DECILE")
    print("="*70)

    decile_data = defaultdict(lambda: {"trades": [], "scores": []})
    for t in trades_with_aggression:
        d = t["aggression_decile"]
        decile_data[d]["trades"].append(t)
        decile_data[d]["scores"].append(t["aggression_score"])

    print(f"\n{'Decile':<8} {'Score Range':<18} {'Trades':>6} {'WR%':>6} {'Total R':>10} {'Avg R':>8} {'PF':>6} {'Sharpe':>8}")
    print("-"*70)

    for d in range(1, 11):
        td = decile_data[d]
        trades_d = td["trades"]
        n = len(trades_d)
        if n == 0:
            continue

        wins = sum(1 for t in trades_d if float(t["pnl_r"]) > 0)
        total_r = sum(float(t["pnl_r"]) for t in trades_d)
        gp = sum(float(t["pnl_r"]) for t in trades_d if float(t["pnl_r"]) > 0)
        gl = abs(sum(float(t["pnl_r"]) for t in trades_d if float(t["pnl_r"]) < 0))
        pf = gp / gl if gl > 0 else float("inf")
        wr = wins / n * 100
        avg_r = total_r / n
        r_vals = [float(t["pnl_r"]) for t in trades_d]
        sharpe = (np.mean(r_vals) / np.std(r_vals) * math.sqrt(252 * 288 / max(n, 1))) if len(r_vals) > 1 and np.std(r_vals) > 0 else 0
        score_range = f"{min(td['scores']):.3f} - {max(td['scores']):.3f}"

        print(f"D{d:<7} {score_range:<18} {n:>6} {wr:>6.1f} {total_r:>+10.2f} {avg_r:>+8.4f} {pf:>6.3f} {sharpe:>8.2f}")

    # Cumulative analysis
    print("\n" + "="*70)
    print("CUMULATIVE ANALYSIS (Top N deciles)")
    print("="*70)

    for threshold in [10, 9, 8, 7, 6, 5]:
        filtered = [t for t in trades_with_aggression if t["aggression_decile"] >= threshold]
        n = len(filtered)
        if n == 0:
            continue
        wins = sum(1 for t in filtered if float(t["pnl_r"]) > 0)
        total_r = sum(float(t["pnl_r"]) for t in filtered)
        gp = sum(float(t["pnl_r"]) for t in filtered if float(t["pnl_r"]) > 0)
        gl = abs(sum(float(t["pnl_r"]) for t in filtered if float(t["pnl_r"]) < 0))
        pf = gp / gl if gl > 0 else float("inf")
        wr = wins / n * 100
        pct_of_trades = n / len(trades_with_aggression) * 100
        pct_of_r = total_r / sum(float(t["pnl_r"]) for t in trades_with_aggression) * 100 if sum(float(t["pnl_r"]) for t in trades_with_aggression) != 0 else 0

        print(f"D{threshold}-D10: {n:>4} trades ({pct_of_trades:>3.0f}%)  WR={wr:.1f}%  R={total_r:>+7.2f} ({pct_of_r:>+4.0f}%)  PF={pf:.3f}")

    # Bottom vs Top comparison
    print("\n" + "="*70)
    print("BOTTOM 50% vs TOP 50%")
    print("="*70)

    bottom = [t for t in trades_with_aggression if t["aggression_decile"] <= 5]
    top = [t for t in trades_with_aggression if t["aggression_decile"] > 5]

    for label, subset in [("Bottom 50% (D1-D5)", bottom), ("Top 50% (D6-D10)", top)]:
        n = len(subset)
        if n == 0: continue
        wins = sum(1 for t in subset if float(t["pnl_r"]) > 0)
        total_r = sum(float(t["pnl_r"]) for t in subset)
        gp = sum(float(t["pnl_r"]) for t in subset if float(t["pnl_r"]) > 0)
        gl = abs(sum(float(t["pnl_r"]) for t in subset if float(t["pnl_r"]) < 0))
        pf = gp / gl if gl > 0 else float('inf')
        wr = wins / n * 100
        print(f"{label}: {n} trades, WR={wr:.1f}%, Total R={total_r:+.2f}, PF={pf:.3f}, Avg R={total_r/n:+.4f}")

    # Top 20% deep dive
    print("\n" + "="*70)
    print("TOP 20% (D9-D10) DEEP DIVE")
    print("="*70)
    top20 = [t for t in trades_with_aggression if t["aggression_decile"] >= 9]
    if top20:
        # Exit reasons
        reasons = Counter(t["exit_reason"] for t in top20)
        print("\nExit reasons:")
        for r, n in reasons.most_common():
            avg = sum(float(t["pnl_r"]) for t in top20 if t["exit_reason"]==r)/n
            print(f"  {r}: {n} ({n/len(top20)*100:.0f}%) avg R={avg:+.3f}")

        # By symbol
        print("\nBy symbol:")
        sym_r = defaultdict(lambda: {"n":0,"r":0})
        for t in top20:
            sym_r[t["symbol"]]["n"] += 1
            sym_r[t["symbol"]]["r"] += float(t["pnl_r"])
        for s, d in sorted(sym_r.items(), key=lambda x: x[1]["r"], reverse=True):
            wr = sum(1 for t in top20 if t["symbol"]==s and float(t["pnl_r"])>0)/d["n"]*100
            print(f"  {s}: {d['n']} trades, WR={wr:.0f}%, R={d['r']:+.2f}")

        # Top 10 trades
        print("\nTop 10 trades by R:")
        for t in sorted(top20, key=lambda x: float(x["pnl_r"]), reverse=True)[:10]:
            print(f"  {t['symbol']:<15} R={float(t['pnl_r']):+.3f}  score={t['aggression_score']:.3f}  reason={t['exit_reason']}")

    # Component analysis — which variables matter most?
    print("\n" + "="*70)
    print("AGGRESSION COMPONENT ANALYSIS")
    print("="*70)

    # Parse aggression vars for top/bottom trades
    top10 = sorted(trades_with_aggression, key=lambda t: float(t["pnl_r"]), reverse=True)[:50]
    bottom10 = sorted(trades_with_aggression, key=lambda t: float(t["pnl_r"]))[:50]

    component_names = ["taker_imb_z", "delta_persistence", "range_expansion_pctile",
                       "volume_concentration", "clv", "wick_rejection", "velocity",
                       "cascade_intensity", "oi_acceleration", "spread_expansion"]

    print(f"\n{'Component':<25} {'Top 50 Avg':>12} {'Bottom 50 Avg':>14} {'Diff':>10}")
    print("-"*65)

    for comp in component_names:
        top_vals = []
        bottom_vals = []
        for t in top10:
            try:
                vars_dict = ast.literal_eval(t.get("aggression_vars", "{}"))
                if comp in vars_dict:
                    top_vals.append(vars_dict[comp])
            except: pass
        for t in bottom10:
            try:
                vars_dict = ast.literal_eval(t.get("aggression_vars", "{}"))
                if comp in vars_dict:
                    bottom_vals.append(vars_dict[comp])
            except: pass

        if top_vals and bottom_vals:
            top_avg = np.mean(top_vals)
            bot_avg = np.mean(bottom_vals)
            diff = top_avg - bot_avg
            print(f"{comp:<25} {top_avg:>12.4f} {bot_avg:>14.4f} {diff:>+10.4f}")

    # April degradation analysis
    print("\n" + "="*70)
    print("APRIL REGIME DEGRADATION ANALYSIS")
    print("="*70)

    for month in ["2026-02", "2026-03", "2026-04"]:
        month_trades = [t for t in trades_with_aggression if t["entry_time"][:7] == month]
        if not month_trades:
            continue
        n = len(month_trades)
        wins = sum(1 for t in month_trades if float(t["pnl_r"]) > 0)
        total_r = sum(float(t["pnl_r"]) for t in month_trades)
        avg_score = np.mean([t["aggression_score"] for t in month_trades])
        wr = wins / n * 100

        # Decile distribution
        decile_dist = Counter(t["aggression_decile"] for t in month_trades)
        top_pct = sum(1 for t in month_trades if t["aggression_decile"] >= 8) / n * 100

        print(f"\n{month}: {n} trades, WR={wr:.1f}%, R={total_r:+.2f}, Avg Score={avg_score:.3f}, Top20%={top_pct:.0f}%")

        # Exit reasons
        reasons = Counter(t["exit_reason"] for t in month_trades)
        for r, nr in reasons.most_common():
            avg = sum(float(t["pnl_r"]) for t in month_trades if t["exit_reason"]==r)/nr
            print(f"  {r}: {nr} ({nr/n*100:.0f}%) avg R={avg:+.3f}")

    # Save summary
    print("\n" + "="*70)
    print("SUMMARY & RECOMMENDATIONS")
    print("="*70)
    print("""
1. Aggression Score V1 built with 10 micro-structure variables
2. Trucks are bucketed by aggression decile
3. Expectancy by tier reveals which trades to keep/filter

Key questions answered:
- Do top aggression deciles produce most PnL?
- Is the bottom 50% noise?
- Which aggression components matter most?
- What changed in April?

Next steps based on findings:
- Filter out low-aggression trades (likely bottom 30-40%)
- Size proportionally to aggression score
- Focus exit optimization on high-aggression trades
- Investigate April regime change
""")

    print(f"\nAll outputs saved to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
