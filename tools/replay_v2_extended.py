"""
Bitana Extended Replay — BTCUSDT ATR10_VOL2.0 × 3 Session Filters

Validates the most promising compression breakout candidate over 180 days
with session-filtered variants to determine if the edge is real.

Variants:
  1. ALL   — no session restriction
  2. ASIA  — entries only 00:00–08:00 UTC
  3. ASIA_EU — entries only 00:00–16:00 UTC

Fixed params:
  ATR pctl=10th, vol_mult=2.0×, stop=baseline, min_compression=10
  4H EMA20/50 trend filter, time_stop=6c/+0.5R

IS/OOS split: first 120d IS, last 60d OOS.
"""
from __future__ import annotations
import bisect
import asyncio
import csv
import math
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.loader import load_config, resolve_symbol_config
from core.logging_setup import get_logger
from core.models import Candle, EngineType, Side, Signal
from data.binance_rest import BinanceRestClient
from data.rate_limiter import RateLimiterGroup
from engines.compression_breakout import CompressionBreakoutEngine
from engines.regime_filter import RegimeFilter

logger = get_logger("replay_ext")

SYMBOL = "BTCUSDT"
DAYS = 180
IS_DAYS = 120
OOS_DAYS = 60
INITIAL_EQUITY = 1000.0

# Fixed params
ATR_PCTL = 10.0
VOL_MULT = 2.0
STOP_WIDTH = 0.0
MIN_COMP = 10
TIME_STOP_CANDLES = 6
TIME_STOP_R = 0.5

SESSION_FILTERS = {
    "ALL":     (0, 24),
    "ASIA":    (0, 8),
    "ASIA_EU": (0, 16),
}

# ─────────────────────────────────────────────────────────────────────────────
# Data Fetching
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_all_klines(client, symbol, interval, start, end):
    all_candles = []
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    batch = 0
    while start_ms < end_ms:
        raw = await client.get_klines(symbol=symbol, interval=interval,
                                       start_time=start_ms, limit=1500)
        if not raw or not isinstance(raw, list):
            break
        for k in raw:
            if k[6] > end_ms:
                break
            all_candles.append(Candle(
                symbol=symbol, timeframe=interval,
                open_time=datetime.fromtimestamp(k[0]/1000, tz=timezone.utc),
                close_time=datetime.fromtimestamp(k[6]/1000, tz=timezone.utc),
                open=float(k[1]), high=float(k[2]),
                low=float(k[3]), close=float(k[4]),
                volume=float(k[5]), is_closed=True,
            ))
        start_ms = int(raw[-1][6]) + 1
        batch += 1
        if len(raw) < 1500:
            break
        if batch % 10 == 0:
            print(f"  ... {interval}: {len(all_candles)} candles")
        await asyncio.sleep(0.15)
    seen = set()
    deduped = []
    for c in all_candles:
        if c.open_time not in seen:
            seen.add(c.open_time)
            deduped.append(c)
    return sorted(deduped, key=lambda c: c.open_time)

# ─────────────────────────────────────────────────────────────────────────────
# 4H Trend Filter
# ─────────────────────────────────────────────────────────────────────────────

def _ema(values, period):
    if not values: return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out

class TrendFilter4H:
    def __init__(self, candles_4h):
        closes = [c.close for c in candles_4h]
        self.ema20 = _ema(closes, 20)
        self.ema50 = _ema(closes, 50)
        self._trend_at = {}
        for i, c in enumerate(candles_4h):
            if i < 50:
                self._trend_at[c.close_time] = "NEUTRAL"
            elif self.ema20[i] > self.ema50[i]:
                self._trend_at[c.close_time] = "BULLISH"
            elif self.ema20[i] < self.ema50[i]:
                self._trend_at[c.close_time] = "BEARISH"
            else:
                self._trend_at[c.close_time] = "NEUTRAL"
        self._times = sorted(self._trend_at.keys())

    def get_trend(self, at_time):
        best = None
        for t in self._times:
            if t <= at_time:
                best = t
            else:
                break
        return self._trend_at.get(best, "NEUTRAL") if best else "NEUTRAL"

    def signal_aligned(self, side, at_time):
        trend = self.get_trend(at_time)
        if trend == "NEUTRAL":
            return False
        if side == Side.LONG and trend == "BULLISH":
            return True
        if side == Side.SHORT and trend == "BEARISH":
            return True
        return False

# ─────────────────────────────────────────────────────────────────────────────
# Executor & Position
# ─────────────────────────────────────────────────────────────────────────────

class Executor:
    def __init__(self, eq, taker_bps, slip_bps):
        self.equity = eq
        self.initial = eq
        self.peak = eq
        self.taker_bps = taker_bps
        self.slip_bps = slip_bps

    def fill_entry(self, price, qty, side):
        slip = price * (self.slip_bps / 10000)
        fill = price + slip if side == Side.LONG else price - slip
        fee = qty * fill * (self.taker_bps / 10000)
        self.equity -= fee
        return fill, fee

    def fill_exit(self, entry, price, qty, side):
        slip = price * (self.slip_bps / 10000)
        fill = price - slip if side == Side.LONG else price + slip
        fee = qty * fill * (self.taker_bps / 10000)
        pnl = (fill - entry) * qty if side == Side.LONG else (entry - fill) * qty
        self.equity += pnl - fee
        if self.equity > self.peak:
            self.peak = self.equity
        return fill, fee, pnl

class Pos:
    def __init__(self, sig, fill, qty, lev, fee, t):
        self.uuid = sig.trade_uuid
        self.symbol = sig.symbol
        self.side = sig.side
        self.engine = sig.engine
        self.entry = fill
        self.qty = qty
        self.orig_qty = qty
        self.lev = lev
        self.stop = sig.stop_price
        self.init_stop = sig.stop_price
        self.tp1 = False
        self.trail_active = False
        self.trail_stop = 0.0
        self.candles = 0
        self.entry_time = t
        self.rpnl = 0.0
        self.fees = fee
        self.sig_data = sig.signal_data
        self.closed = False
        self.exit_price = 0.0
        self.exit_reason = ""
        self.exit_time = None

def _calc_atr(candles, period=14):
    if len(candles) < period + 1: return 0.0
    trs = []
    for i in range(1, len(candles)):
        prev = candles[i-1].close; c = candles[i]
        trs.append(max(c.high - c.low, abs(c.high - prev), abs(c.low - prev)))
    if len(trs) < period:
        return sum(trs)/len(trs) if trs else 0.0
    atr = sum(trs[:period])/period
    for j in range(period, len(trs)):
        atr = (atr*(period-1)+trs[j])/period
    return atr

def _close_pos(pos, price, reason, t, ex):
    fill, fee, pnl = ex.fill_exit(pos.entry, price, pos.qty, pos.side)
    pos.rpnl += pnl; pos.fees += fee
    pos.exit_price = fill; pos.exit_reason = reason
    pos.exit_time = t; pos.closed = True

def _record(pos, trades, ex):
    sd = abs(pos.entry - pos.init_stop)
    pnl = pos.rpnl - pos.fees
    pnl_r = pos.rpnl / (sd * pos.orig_qty) if sd > 0 and pos.orig_qty > 0 else 0
    hs = (pos.exit_time - pos.entry_time).total_seconds() if pos.exit_time else 0
    h = pos.entry_time.hour
    sess = "US"
    if 0 <= h < 8: sess = "Asia"
    elif 8 <= h < 16: sess = "Europe"
    trades.append({
        "uuid": pos.uuid, "symbol": pos.symbol, "side": pos.side.value,
        "entry_time": pos.entry_time.isoformat(),
        "exit_time": pos.exit_time.isoformat() if pos.exit_time else "",
        "entry_price": round(pos.entry, 2), "exit_price": round(pos.exit_price, 2),
        "qty": round(pos.orig_qty, 6), "leverage": pos.lev,
        "init_stop": round(pos.init_stop, 2), "stop_dist": round(sd, 2),
        "pnl_usd": round(pnl, 4), "pnl_r": round(pnl_r, 4),
        "fees": round(pos.fees, 4), "hold_s": round(hs, 1),
        "hold_candles": pos.candles, "exit_reason": pos.exit_reason,
        "tp1": pos.tp1, "equity": round(ex.equity, 2), "session": sess,
    })

# ─────────────────────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Metrics:
    label: str = ""
    trades: int = 0
    wr: float = 0.0
    net_r: float = 0.0
    pnl_pct: float = 0.0
    exp_r: float = 0.0
    pf: float = 0.0
    max_dd: float = 0.0
    tpw: float = 0.0
    r2: float = 0.0
    sharpe: float = 0.0
    calmar: float = 0.0
    cagr: float = 0.0

def calc_metrics(trades, days, eq0):
    m = Metrics()
    m.trades = len(trades)
    if not trades: return m
    rs = [t["pnl_r"] for t in trades]
    m.net_r = sum(rs)
    w = [r for r in rs if r > 0]; l = [r for r in rs if r <= 0]
    m.wr = len(w)/len(rs)
    m.exp_r = sum(rs)/len(rs)
    gp = sum(t["pnl_usd"] for t in trades if t["pnl_usd"] > 0)
    gl = abs(sum(t["pnl_usd"] for t in trades if t["pnl_usd"] <= 0))
    m.pf = gp/gl if gl > 0 else float("inf")
    m.tpw = len(trades)/(days/7)
    eq = [eq0]
    pk = eq0; mdd = 0
    for t in trades:
        eq.append(eq[-1] + t["pnl_usd"])
        if eq[-1] > pk: pk = eq[-1]
        dd = (pk - eq[-1])/pk if pk > 0 else 0
        mdd = max(mdd, dd)
    m.max_dd = mdd
    m.pnl_pct = ((eq[-1] - eq0)/eq0)*100
    if eq[-1] > 0:
        m.cagr = (((eq[-1]/eq0)**(365/max(days,1)))-1)*100
    else:
        m.cagr = -100
    m.calmar = m.cagr/(m.max_dd*100) if m.max_dd > 0 else (m.cagr if m.cagr > 0 else 0)
    n = len(eq)
    if n >= 3:
        xs = list(range(n)); xm = sum(xs)/n; ym = sum(eq)/n
        sxy = sum((x-xm)*(y-ym) for x,y in zip(xs,eq))
        sxx = sum((x-xm)**2 for x in xs)
        syy = sum((y-ym)**2 for y in eq)
        if sxx > 0 and syy > 0:
            m.r2 = (sxy/(math.sqrt(sxx)*math.sqrt(syy)))**2
    if len(rs) >= 2:
        mu = statistics.mean(rs); sd = statistics.stdev(rs)
        if sd > 0:
            m.sharpe = (mu/sd)*math.sqrt(len(rs)/(days/365))
    return m

# ─────────────────────────────────────────────────────────────────────────────
# Single variant replay
# ─────────────────────────────────────────────────────────────────────────────

async def run_variant(
    sess_name, sess_range, c5m, c15m, c1m, trend, config, is_end
):
    resolved = resolve_symbol_config(config, SYMBOL)
    comp = resolved.compression.model_copy()
    comp.atr_percentile_threshold = ATR_PCTL
    comp.breakout_volume_multiplier = VOL_MULT
    comp.min_compression_candles = MIN_COMP

    engine = CompressionBreakoutEngine(comp)
    regime = RegimeFilter(config.regime_filters)
    risk_cfg = config.risk
    prof_cfg = config.profit_taking
    brk_cfg = config.brakes

    ex = Executor(INITIAL_EQUITY, config.fees.taker_bps, config.fees.default_slippage_bps)

    c15s = sorted(c15m, key=lambda c: c.close_time)
    c1s = sorted(c1m, key=lambda c: c.close_time)
    c15t = [c.close_time for c in c15s]
    c1t = [c.close_time for c in c1s]

    opens = []
    trades = []
    eq_curve = []
    risk_pct = risk_cfg.default_risk_pct
    closs = 0
    dloss = 0.0
    ddate = ""

    warmup = max(comp.atr_lookback, comp.bb_period, comp.volume_avg_period, MIN_COMP+5, 50)
    sh, se = sess_range

    for i in range(warmup, len(c5m)):
        c5 = c5m[i]
        ct = c5.close_time
        ds = ct.strftime("%Y-%m-%d")
        if ds != ddate:
            dloss = 0.0; ddate = ds

        w5 = c5m[max(0,i-200):i+1]
        i15 = bisect.bisect_right(c15t, ct)
        w15 = c15s[max(0,i15-60):i15]
        i1 = bisect.bisect_right(c1t, ct)
        w1 = c1s[max(0,i1-15):i1]

        # manage positions
        for p in list(opens):
            if p.closed: continue
            p.candles += 1
            cp = c5.close
            sd = abs(p.entry - p.init_stop)
            rm = 0.0
            if sd > 0:
                rm = (cp - p.entry)/sd if p.side == Side.LONG else (p.entry - cp)/sd

            if p.candles >= TIME_STOP_CANDLES and rm < TIME_STOP_R and not p.tp1:
                _close_pos(p, cp, "time_stop", ct, ex)
                _record(p, trades, ex)
                eq_curve.append((ct.isoformat(), round(ex.equity, 2)))
                continue

            sh_hit = False; ep = 0.0
            if p.side == Side.LONG and c5.low <= p.stop:
                sh_hit = True; ep = p.stop
            elif p.side == Side.SHORT and c5.high >= p.stop:
                sh_hit = True; ep = p.stop
            if sh_hit:
                _close_pos(p, ep, "stop_loss", ct, ex)
                _record(p, trades, ex)
                eq_curve.append((ct.isoformat(), round(ex.equity, 2)))
                continue

            if not p.tp1 and rm >= prof_cfg.partial_close_r:
                tq = p.qty * prof_cfg.partial_close_pct
                fl, fe, pn = ex.fill_exit(p.entry, cp, tq, p.side)
                p.tp1 = True; p.qty -= tq; p.rpnl += pn; p.fees += fe
                p.trail_active = True

            if p.trail_active and len(w5) > 15:
                atr = _calc_atr(w5, 14)
                td = atr * prof_cfg.trail_atr_multiplier
                if p.side == Side.LONG:
                    nt = cp - td
                    if nt > p.trail_stop: p.trail_stop = nt
                    if p.trail_stop > p.stop: p.stop = p.trail_stop
                else:
                    nt = cp + td
                    if p.trail_stop == 0 or nt < p.trail_stop: p.trail_stop = nt
                    if p.trail_stop < p.stop or p.stop == 0: p.stop = p.trail_stop

        opens = [p for p in opens if not p.closed]

        for t in trades:
            if t.get("_p"): continue
            t["_p"] = True
            if t["pnl_r"] < 0:
                closs += 1
                if t["pnl_usd"] < 0:
                    dloss += abs(t["pnl_usd"])/max(ex.equity, 1)
            else:
                closs = 0

        if ex.peak > 0:
            dd = (ex.peak - ex.equity)/ex.peak
        else:
            dd = 0
        if dd > risk_cfg.drawdown_reduce_threshold:
            risk_pct = risk_cfg.reduced_risk_pct
        elif dd < risk_cfg.drawdown_restore_threshold:
            if closs < brk_cfg.consecutive_loss_threshold:
                risk_pct = risk_cfg.default_risk_pct

        # entry checks
        if dloss >= brk_cfg.daily_loss_limit_pct: continue
        if len(opens) >= config.portfolio.max_per_symbol: continue

        # session filter
        entry_hour = ct.hour
        if sh < se:
            if not (sh <= entry_hour < se): continue
        else:
            if not (entry_hour >= sh or entry_hour < se): continue

        tradeable, _ = regime.check(SYMBOL, w15, now_utc=ct)
        if not tradeable: continue

        try:
            sig = await engine.evaluate(SYMBOL, w5, w15, w1)
        except Exception:
            continue
        if not sig: continue

        if not trend.signal_aligned(sig.side, ct): continue

        equity = ex.equity
        if equity <= 0: break
        sdist = abs(sig.entry_price - sig.stop_price)
        if sdist <= 0: continue

        ra = equity * (risk_pct/100.0)
        qty = ra / sdist
        notional = qty * sig.entry_price
        lev = min(int(notional/equity)+1, risk_cfg.max_leverage)
        lev = max(lev, 1)
        mn = equity * lev * (1 - risk_cfg.liquidation_buffer_pct)
        if notional > mn: qty = mn / sig.entry_price
        if qty <= 0: continue

        fill, fee = ex.fill_entry(sig.entry_price, qty, sig.side)
        p = Pos(sig, fill, qty, lev, fee, ct)
        opens.append(p)

    if c5m:
        lp = c5m[-1].close
        for p in opens:
            if not p.closed:
                _close_pos(p, lp, "replay_end", c5m[-1].close_time, ex)
                _record(p, trades, ex)
                eq_curve.append((c5m[-1].close_time.isoformat(), round(ex.equity, 2)))

    clean = [{k:v for k,v in t.items() if not k.startswith("_")} for t in trades]
    is_trades = [t for t in clean if t["entry_time"] < is_end.isoformat()]
    oos_trades = [t for t in clean if t["entry_time"] >= is_end.isoformat()]

    return {
        "name": sess_name,
        "trades": clean,
        "eq_curve": eq_curve,
        "full": calc_metrics(clean, DAYS, INITIAL_EQUITY),
        "is": calc_metrics(is_trades, IS_DAYS, INITIAL_EQUITY),
        "oos": calc_metrics(oos_trades, OOS_DAYS, INITIAL_EQUITY),
        "final_eq": ex.equity,
    }

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    config = load_config()

    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=DAYS)
    is_end = start + timedelta(days=IS_DAYS)

    print(f"╔{'═'*70}╗")
    print(f"║  BTCUSDT EXTENDED REPLAY — ATR10 VOL2.0 BASELINE                   ║")
    print(f"║  {start.date()} → {end.date()} ({DAYS}d) | IS={IS_DAYS}d OOS={OOS_DAYS}d        ║")
    print(f"║  Sessions: ALL / ASIA / ASIA_EU                                    ║")
    print(f"╚{'═'*70}╝")

    print("\n📡  Fetching historical data...")
    rl = RateLimiterGroup()
    client = BinanceRestClient(testnet=False, rate_limiter=rl)
    await client.start()

    start_4h = start - timedelta(days=60)
    c1m = await fetch_all_klines(client, SYMBOL, "1m", start, end)
    print(f"  ✓ 1m:  {len(c1m)} candles")
    c5m = await fetch_all_klines(client, SYMBOL, "5m", start, end)
    print(f"  ✓ 5m:  {len(c5m)} candles")
    c15m = await fetch_all_klines(client, SYMBOL, "15m", start, end)
    print(f"  ✓ 15m: {len(c15m)} candles")
    c4h = await fetch_all_klines(client, SYMBOL, "4h", start_4h, end)
    print(f"  ✓ 4H:  {len(c4h)} candles")
    await client.close()

    if not c5m:
        print("ERROR: No 5m data. Aborting.")
        return

    trend = TrendFilter4H(c4h)
    print(f"\n  4H trend filter built ({len(c4h)} candles)")

    print(f"\n🔁  Running 3 session variants...\n")
    results = []
    t0 = time.time()

    for sn, sr in SESSION_FILTERS.items():
        vt = time.time()
        r = await run_variant(sn, sr, c5m, c15m, c1m, trend, config, is_end)
        el = time.time() - vt
        results.append(r)
        f = r["full"]
        status = "✅" if f.exp_r > 0 else "❌"
        print(f"  {status} {sn:<10s}  Trades={f.trades:>3d}  WR={f.wr:.1%}  "
              f"NetR={f.net_r:>+7.2f}  Exp={f.exp_r:>+.4f}R  "
              f"PF={f.pf:.2f}  MaxDD={f.max_dd:.1%}  "
              f"IS={r['is'].trades}t  OOS={r['oos'].trades}t  ({el:.1f}s)")

    print(f"\n  Total: {time.time()-t0:.1f}s")

    # Output
    out = Path("replay_output/v2_btc_ext")
    out.mkdir(parents=True, exist_ok=True)

    for r in results:
        if r["trades"]:
            with open(out / f"trades_{r['name']}.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=r["trades"][0].keys())
                w.writeheader(); w.writerows(r["trades"])
        if r["eq_curve"]:
            with open(out / f"equity_{r['name']}.csv", "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["timestamp","equity"])
                w.writerows(r["eq_curve"])

    # Summary
    sep = "=" * 110
    thin = "-" * 110
    print(f"\n{sep}")
    print("  BTCUSDT EXTENDED REPLAY — ATR10_VOL2.0_STOPbase × 3 SESSIONS")
    print(f"  {start.date()} → {end.date()} ({DAYS}d) | IS={IS_DAYS}d OOS={OOS_DAYS}d")
    print(sep)

    hdr = f"  {'Session':<12s} {'Trades':>6s} {'WR':>6s} {'NetR':>8s} {'PnL%':>8s} {'PF':>6s} {'Exp/R':>8s} {'MaxDD':>7s} {'T/wk':>5s} {'R²':>5s} {'Sharpe':>7s} {'CAGR':>8s} {'Calmar':>7s}"
    for period_name, get_m in [("FULL", lambda r: r["full"]), ("IN-SAMPLE", lambda r: r["is"]), ("OUT-OF-SAMPLE", lambda r: r["oos"])]:
        print(f"\n  ── {period_name} ──")
        print(hdr)
        print(f"  {thin}")
        for r in results:
            m = get_m(r)
            print(f"  {r['name']:<12s} {m.trades:>6d} {m.wr:>5.1%} {m.net_r:>+7.2f}R "
                  f"{m.pnl_pct:>+7.2f}% {m.pf:>5.2f} {m.exp_r:>+7.4f}R "
                  f"{m.max_dd:>6.1%} {m.tpw:>5.1f} {m.r2:>5.3f} {m.sharpe:>+6.2f} "
                  f"{m.cagr:>+7.1f}% {m.calmar:>+6.2f}")

    # Session trade breakdown
    print(f"\n{sep}")
    print("  SESSION DISTRIBUTION OF TRADES (FULL)")
    print(sep)
    for r in results:
        asia = [t for t in r["trades"] if t.get("session") == "Asia"]
        eu = [t for t in r["trades"] if t.get("session") == "Europe"]
        us = [t for t in r["trades"] if t.get("session") == "US"]
        def sr(ts):
            if not ts: return 0, 0, 0
            rs = [t["pnl_r"] for t in ts]
            wins = [r for r in rs if r > 0]
            return len(ts), len(wins)/len(ts) if ts else 0, sum(rs)
        an, aw, ar = sr(asia)
        en, ew, er = sr(eu)
        un, uw, ur = sr(us)
        print(f"  {r['name']:<12s}  Asia: {an:>3d}t {aw:>5.1%} {ar:>+6.2f}R  |  "
              f"EU: {en:>3d}t {ew:>5.1%} {er:>+6.2f}R  |  "
              f"US: {un:>3d}t {uw:>5.1%} {ur:>+6.2f}R")

    # Verdict
    print(f"\n{sep}")
    print("  VERDICT")
    print(sep)
    best = max(results, key=lambda r: r["full"].exp_r)
    bf = best["full"]
    bi = best["is"]
    bo = best["oos"]
    if bf.exp_r > 0 and bi.exp_r > 0 and bo.exp_r > 0:
        print(f"""
  ✅  EDGE CONFIRMED: {best['name']}

      Full:  {bf.net_r:+.2f}R | Exp {bf.exp_r:+.4f}R | WR {bf.wr:.1%} | PF {bf.pf:.2f} | MaxDD {bf.max_dd:.1%}
      IS:    {bi.net_r:+.2f}R | Exp {bi.exp_r:+.4f}R | Trades {bi.trades}
      OOS:   {bo.net_r:+.2f}R | Exp {bo.exp_r:+.4f}R | Trades {bo.trades}

      RECOMMENDATION: Deploy with careful position sizing.
""")
    elif bf.exp_r > 0:
        print(f"""
  ⚠️  PARTIAL EDGE: {best['name']}

      Full:  {bf.net_r:+.2f}R | Exp {bf.exp_r:+.4f}R | WR {bf.wr:.1%} | PF {bf.pf:.2f} | MaxDD {bf.max_dd:.1%}
      IS:    {bi.net_r:+.2f}R | Exp {bi.exp_r:+.4f}R
      OOS:   {bo.net_r:+.2f}R | Exp {bo.exp_r:+.4f}R

      Edge exists in aggregate but not confirmed in both periods.
      Paper-trade to accumulate more data before deploying.
""")
    else:
        print(f"""
  ❌  NO EDGE

      Best: {best['name']}
      Full:  {bf.net_r:+.2f}R | Exp {bf.exp_r:+.4f}R | WR {bf.wr:.1%}
      IS:    {bi.net_r:+.2f}R | OOS: {bo.net_r:+.2f}R

      Compression Breakout on BTC does not show positive edge at 180d scale.
""")
    print(sep)

if __name__ == "__main__":
    asyncio.run(main())
