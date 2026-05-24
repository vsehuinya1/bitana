"""
V5 Volume Tier Backtest — Extended with 5-19M volume symbols.

Tests the same configs as before PLUS:
  F: 5-19M only (symbols in the 5-19M volume range with liq data)
  G: 5M+ (all symbols with liq data, i.e. 20M+ + 10-19M + 5-19M)
  H: 5-19M + 10-19M (all sub-20M symbols with liq data)
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

# All symbols we have klines for
ALL_SYMBOLS = [
    "NEARUSDT", "ZECUSDT", "ADAUSDT", "WLDUSDT", "UNIUSDT", "NMRUSDT",
    "PENDLEUSDT", "ARBUSDT", "RENDERUSDT", "RUNEUSDT", "FETUSDT", "DOTUSDT",
    "TONUSDT", "SOLUSDT", "1000LUNCUSDT", "ENAUSDT", "1000PEPEUSDT",
    "XRPUSDT", "FILUSDT", "BNBUSDT", "TAOUSDT", "CHZUSDT", "DASHUSDT",
    "QNTUSDT", "ICPUSDT", "XLMUSDT", "APTUSDT", "ETHUSDT",
    # 5-19M symbols (downloaded)
    "1000BONKUSDT", "RAVEUSDT", "MEGAUSDT", "EDGEUSDT", "BERAUSDT",
    "IRYSUSDT", "CFGUSDT", "KITEUSDT", "AIAUSDT", "WIFUSDT", "AVNTUSDT", "HBARUSDT",
    "MITOUSDT", "MUSDT", "COINUSDT", "NEIROUSDT", "TRUTHUSDT", "BASEDUSDT", "ONTUSDT",
    "FOGOUSDT", "OPENUSDT", "FFUSDT", "CRVUSDT", "SAGAUSDT", "ENJUSDT", "KAITOUSDT",
    "SEIUSDT", "MONUSDT", "APRUSDT", "GIGGLEUSDT", "BLUAIUSDT", "SAPIENUSDT", "LDOUSDT",
    "SIRENUSDT", "SPYUSDT", "QUSDT", "STORJUSDT", "DYDXUSDT", "PYTHUSDT", "OPGUSDT",
    "AXSUSDT", "MRVLUSDT", "DRAMUSDT", "AINUSDT", "PLUMEUSDT", "ZAMAUSDT", "DUSKUSDT",
    "PIPPINUSDT", "DYMUSDT", "ORCAUSDT", "WUSDT", "MORPHOUSDT", "BOMEUSDT", "CGPTUSDT",
    "ETHFIUSDT", "HYPERUSDT", "COPPERUSDT", "BROCCOLIF3BUSDT", "AKTUSDT", "PIEVERSEUSDT",
    "TRBUSDT", "JSTUSDT", "ZROUSDT", "AEROUSDT", "POLUSDT", "LAYERUSDT", "ZBTUSDT",
    "ZKPUSDT", "IOUSDT", "RKLBUSDT", "CFXUSDT", "UAIUSDT", "ATUSDT", "APEUSDT",
    "ROBOUSDT", "LITEUSDT", "GALAUSDT", "EIGENUSDT", "KAIAUSDT", "IPUSDT", "TSTUSDT",
    "PLTRUSDT", "GWEIUSDT", "XPTUSDT", "PROMUSDT", "ARUSDT", "CAKEUSDT",
    "SANDUSDT", "1000FLOKIUSDT", "TAUSDT", "SPXUSDT", "USUSDT", "DEEPUSDT", "ENSUSDT",
    "HUMAUSDT", "GRASSUSDT", "GENIUSUSDT", "SOONUSDT", "HOODUSDT", "RAYSOLUSDT", "SPKUSDT",
    "STABLEUSDT", "MAGMAUSDT", "ASRUSDT", "SYRUPUSDT", "SUSDT", "INXUSDT", "TAGUSDT",
    "XVGUSDT", "DEXEUSDT", "ZKUSDT",
]

# Volume tiers (avg daily USD volume)
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
    # 5-19M symbols
    '1000BONKUSDT': 19630148, 'RAVEUSDT': 19055330, 'MEGAUSDT': 18720101,
    'EDGEUSDT': 18390621, 'BERAUSDT': 18329033, 'IRYSUSDT': 18007489,
    'CFGUSDT': 17777324, 'KITEUSDT': 17571053, 'AIAUSDT': 17280712,
    'WIFUSDT': 17027333, 'AVNTUSDT': 16979090, 'HBARUSDT': 16948245,
    'MITOUSDT': 16244331, 'MUSDT': 15975941, 'COINUSDT': 15743932,
    'NEIROUSDT': 15608373, 'TRUTHUSDT': 15192324, 'BASEDUSDT': 15081978,
    'ONTUSDT': 14262372, 'FOGOUSDT': 14217303, 'OPENUSDT': 14156430,
    'FFUSDT': 13565773, 'CRVUSDT': 13507426, 'SAGAUSDT': 13474568,
    'ENJUSDT': 13242577, 'KAITOUSDT': 12995634, 'SEIUSDT': 12967074,
    'MONUSDT': 12819863, 'APRUSDT': 12571043, 'GIGGLEUSDT': 11798048,
    'BLUAIUSDT': 11511261, 'SAPIENUSDT': 11268932, 'LDOUSDT': 11202050,
    'SIRENUSDT': 11135902, 'SPYUSDT': 10869979, 'QUSDT': 10803291,
    'STORJUSDT': 10720887, 'DYDXUSDT': 10675069, 'PYTHUSDT': 10538958,
    'OPGUSDT': 10426834, 'AXSUSDT': 10198581, 'MRVLUSDT': 10004420,
    'DRAMUSDT': 9907569, 'AINUSDT': 9884566, 'PLUMEUSDT': 9847922,
    'ZAMAUSDT': 9837374, 'DUSKUSDT': 9597280, 'PIPPINUSDT': 9565365,
    'DYMUSDT': 9504737, 'ORCAUSDT': 9502017, 'WUSDT': 9295832,
    'MORPHOUSDT': 9179953, 'BOMEUSDT': 8959643, 'CGPTUSDT': 8919996,
    'ETHFIUSDT': 8751689, 'HYPERUSDT': 8630734, 'COPPERUSDT': 8480809,
    'BROCCOLIF3BUSDT': 8269980, 'AKTUSDT': 8196447, 'PIEVERSEUSDT': 8158174,
    'TRBUSDT': 8131460, 'JSTUSDT': 8077878, 'ZROUSDT': 7970682,
    'AEROUSDT': 7908216, 'POLUSDT': 7900979, 'LAYERUSDT': 7838303,
    'ZBTUSDT': 7791297, 'ZKPUSDT': 7762151, 'IOUSDT': 7720259,
    'RKLBUSDT': 7612395, 'CFXUSDT': 7592503, 'UAIUSDT': 7558280,
    'ATUSDT': 7558156, 'APEUSDT': 7553349, 'ROBOUSDT': 7519499,
    'LITEUSDT': 7516203, 'GALAUSDT': 7409615, 'EIGENUSDT': 7373740,
    'KAIAUSDT': 7324371, 'IPUSDT': 7133144, 'TSTUSDT': 7117790,
    'PLTRUSDT': 7086048, 'GWEIUSDT': 6814736, 'XPTUSDT': 6666447,
    'PROMUSDT': 6610017, 'ARUSDT': 6579042, 'CAKEUSDT': 6558368,
    'SANDUSDT': 6502752, '1000FLOKIUSDT': 6499693, 'TAUSDT': 6456180,
    'SPXUSDT': 6441586, 'USUSDT': 5972147, 'DEEPUSDT': 5900468,
    'ENSUSDT': 5855145, 'HUMAUSDT': 5845528, 'GRASSUSDT': 5766168,
    'GENIUSUSDT': 5666611, 'SOONUSDT': 5654250, 'HOODUSDT': 5630321,
    'RAYSOLUSDT': 5554947, 'SPKUSDT': 5544481, 'STABLEUSDT': 5538167,
    'MAGMAUSDT': 5532769, 'ASRUSDT': 5484273, 'SYRUPUSDT': 5299823,
    'SUSDT': 5236171, 'INXUSDT': 5190958, 'TAGUSDT': 5164368,
    'XVGUSDT': 5080292, 'DEXEUSDT': 5032579, 'ZKUSDT': 5031259,
}

WARMUP_DAYS = 30
START_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
END_DATE = datetime(2026, 5, 20, 23, 59, 59, tzinfo=timezone.utc)
WARMUP_END = START_DATE + timedelta(days=WARMUP_DAYS)
TAKER_BPS = 4.5
SLIP_BPS = 2.0


def get_volume_tier(symbol):
    vol = SYMBOL_VOLS.get(symbol, 0)
    if vol >= 20_000_000:
        return '20M+'
    elif vol >= 10_000_000:
        return '10-19M'
    elif vol >= 5_000_000:
        return '5-9M'
    else:
        return '<5M'


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
    print(f"V5 Volume Tier Backtest — Extended (with 5-19M symbols)")
    print(f"Period: {START_DATE.strftime('%Y-%m-%d')} to {END_DATE.strftime('%Y-%m-%d')}")

    # Determine which symbols have liq data
    conn = sqlite3.connect(str(LIQ_DB))
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT symbol FROM liquidation_history")
    liq_symbols = {r[0] for r in cur.fetchall()}
    conn.close()

    # Filter to symbols that have both klines and liq data
    usable_symbols = [s for s in ALL_SYMBOLS if s in liq_symbols]
    print(f"Symbols with both klines + liq data: {len(usable_symbols)}")

    # Build volume tier sets from usable symbols
    tier_20M = set()
    tier_10_19M = set()
    tier_5_9M = set()
    tier_lt5M = set()
    for s in usable_symbols:
        tier = get_volume_tier(s)
        if tier == '20M+':
            tier_20M.add(s)
        elif tier == '10-19M':
            tier_10_19M.add(s)
        elif tier == '5-9M':
            tier_5_9M.add(s)
        else:
            tier_lt5M.add(s)

    print(f"  20M+: {len(tier_20M)} symbols")
    print(f"  10-19M: {len(tier_10_19M)} symbols")
    print(f"  5-9M: {len(tier_5_9M)} symbols")
    print(f"  <5M: {len(tier_lt5M)} symbols")

    # Define test configurations
    configs = {
        "A: all usable": sorted(usable_symbols),
        "B: 20M+ only": sorted(tier_20M),
        "C: 10M+ only": sorted(tier_20M | tier_10_19M),
        "D: 10-19M only": sorted(tier_10_19M),
        "E: <20M only": sorted(tier_10_19M | tier_5_9M | tier_lt5M),
        "F: 5-9M only": sorted(tier_5_9M),
        "G: 5M+ only": sorted(tier_20M | tier_10_19M | tier_5_9M),
        "H: 5-19M + 10-19M": sorted(tier_10_19M | tier_5_9M),
    }

    # Pre-load all data
    print("\nLoading data...")
    global all_klines, all_liq, all_closes
    all_klines = {}
    all_liq = {}
    all_closes = {}
    for sym in usable_symbols:
        start_ms = int(START_DATE.timestamp() * 1000)
        end_ms = int(END_DATE.timestamp() * 1000)
        all_klines[sym] = load_klines(sym, start_ms, end_ms)
        all_liq[sym] = load_liq_history(sym)
        all_closes[sym] = load_daily_closes(sym)
        print(f"  {sym}: {len(all_klines[sym])} klines, {len(all_liq[sym])} liq records", flush=True)

    # Build unified timeline
    print("\nBuilding timeline...")
    timeline = []
    for sym in usable_symbols:
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
    print("V5 VOLUME TIER BACKTEST RESULTS (Extended)")
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

    # Per-symbol breakdown for 5-9M and 5-19M tests
    for name in ["F: 5-9M only", "H: 5-19M + 10-19M", "E: <20M only"]:
        if name not in portfolios:
            continue
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
            tier = get_volume_tier(s)
            print(f"  {s:<20} {total:>4} trades  WR={wr:>5.0f}%  net={data['net']:>+8.4f}R  [{tier}]")

    # Write CSVs
    for name, p in portfolios.items():
        if p.closed_trades:
            fname = name.replace(" ", "_").replace(":", "").replace("<", "lt").replace(">", "gt").replace("-", "_").replace("+", "plus")
            csv_path = OUTPUT_DIR / f"vol_tier_ext_{fname}_trades.csv"
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
