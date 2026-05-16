"""
Bitana — BTC Trend Continuation Engine Replay

3 Entry Strategies × 3 Session Filters = 9 Variants
365-day BTCUSDT replay with IS/OOS split.

Entry Strategies:
  PB  — 15m Pullback to EMA20 reclaim
  SB  — 15m Swing break with volume expansion
  ACC — ATR contraction after impulse, then continuation

Shared:
  4H EMA20/50 trend filter (NEUTRAL = no trade)
  1R stop, partial 1.5R, trail remainder
  Time stop = 8 candles unless +0.5R

Session Filters:
  ALL     — 00:00–24:00 UTC
  ASIA_EU — 00:00–16:00 UTC
  EU      — 08:00–16:00 UTC
"""
from __future__ import annotations
import bisect, asyncio, csv, math, statistics, sys, time, uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from enum import Enum

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.loader import load_config, resolve_symbol_config
from core.logging_setup import get_logger
from core.models import Candle, Side
from data.binance_rest import BinanceRestClient
from data.rate_limiter import RateLimiterGroup

logger = get_logger("replay_tc")

SYMBOL = "BTCUSDT"
DAYS = 365
IS_DAYS = 240
OOS_DAYS = 125
INITIAL_EQUITY = 1000.0
TIME_STOP_CANDLES = 8
TIME_STOP_R = 0.5
RISK_PCT = 1.0  # 1% risk per trade
MAX_LEVERAGE = 10

SESSIONS = {"ALL": (0, 24), "ASIA_EU": (0, 16), "EU": (8, 16)}

class EntryType(str, Enum):
    PB = "PB"    # Pullback reclaim
    SB = "SB"    # Swing break
    ACC = "ACC"  # ATR contraction continuation

# ─────────────────────────────────────────────────────────────────────────────
# Data Fetching
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_klines(client, symbol, interval, start, end):
    all_c = []
    ms_s = int(start.timestamp() * 1000)
    ms_e = int(end.timestamp() * 1000)
    batch = 0
    while ms_s < ms_e:
        raw = await client.get_klines(symbol=symbol, interval=interval,
                                       start_time=ms_s, limit=1500)
        if not raw or not isinstance(raw, list): break
        for k in raw:
            if k[6] > ms_e: break
            all_c.append(Candle(
                symbol=symbol, timeframe=interval,
                open_time=datetime.fromtimestamp(k[0]/1000, tz=timezone.utc),
                close_time=datetime.fromtimestamp(k[6]/1000, tz=timezone.utc),
                open=float(k[1]), high=float(k[2]),
                low=float(k[3]), close=float(k[4]),
                volume=float(k[5]), is_closed=True,
            ))
        ms_s = int(raw[-1][6]) + 1
        batch += 1
        if len(raw) < 1500: break
        if batch % 10 == 0:
            print(f"  ... {interval}: {len(all_c)} candles")
        await asyncio.sleep(0.15)
    seen = set(); out = []
    for c in all_c:
        if c.open_time not in seen:
            seen.add(c.open_time); out.append(c)
    return sorted(out, key=lambda c: c.open_time)

# ─────────────────────────────────────────────────────────────────────────────
# 4H Trend Filter
# ─────────────────────────────────────────────────────────────────────────────

def _ema(vals, p):
    if not vals: return []
    k = 2.0/(p+1); o = [vals[0]]
    for v in vals[1:]: o.append(v*k + o[-1]*(1-k))
    return o

class TrendFilter4H:
    def __init__(self, c4h):
        closes = [c.close for c in c4h]
        e20 = _ema(closes, 20); e50 = _ema(closes, 50)
        self._map = {}
        for i, c in enumerate(c4h):
            if i < 50: self._map[c.close_time] = "NEUTRAL"
            elif e20[i] > e50[i]: self._map[c.close_time] = "BULLISH"
            elif e20[i] < e50[i]: self._map[c.close_time] = "BEARISH"
            else: self._map[c.close_time] = "NEUTRAL"
        self._times = sorted(self._map.keys())

    def trend_at(self, t):
        best = None
        for tt in self._times:
            if tt <= t: best = tt
            else: break
        return self._map.get(best, "NEUTRAL") if best else "NEUTRAL"

    def allowed_side(self, t):
        tr = self.trend_at(t)
        if tr == "BULLISH": return Side.LONG
        if tr == "BEARISH": return Side.SHORT
        return None

# ─────────────────────────────────────────────────────────────────────────────
# Technical helpers on 15m candles
# ─────────────────────────────────────────────────────────────────────────────

def calc_ema_series(candles, period):
    closes = [c.close for c in candles]
    return _ema(closes, period)

def calc_atr(candles, period=14):
    if len(candles) < period + 1: return 0.0
    trs = []
    for i in range(1, len(candles)):
        p = candles[i-1].close; c = candles[i]
        trs.append(max(c.high - c.low, abs(c.high - p), abs(c.low - p)))
    if len(trs) < period: return sum(trs)/len(trs) if trs else 0.0
    a = sum(trs[:period])/period
    for j in range(period, len(trs)): a = (a*(period-1)+trs[j])/period
    return a

def calc_atr_series(candles, period=14):
    """Returns ATR value for each candle index (0 for first `period` candles)."""
    if len(candles) < period + 1: return [0.0] * len(candles)
    trs = [0.0]
    for i in range(1, len(candles)):
        p = candles[i-1].close; c = candles[i]
        trs.append(max(c.high - c.low, abs(c.high - p), abs(c.low - p)))
    atrs = [0.0] * len(candles)
    if len(trs) > period:
        a = sum(trs[1:period+1]) / period
        atrs[period] = a
        for j in range(period+1, len(trs)):
            a = (a*(period-1)+trs[j])/period
            atrs[j] = a
    return atrs

def swing_high(candles, lookback=10):
    if len(candles) < lookback: return None
    return max(c.high for c in candles[-lookback:])

def swing_low(candles, lookback=10):
    if len(candles) < lookback: return None
    return min(c.low for c in candles[-lookback:])

def avg_volume(candles, period=20):
    if len(candles) < period: return 0.0
    return sum(c.volume for c in candles[-period:]) / period

# ─────────────────────────────────────────────────────────────────────────────
# Signal generation — 3 entry types on 15m candles
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TCSignal:
    side: Side
    entry_price: float
    stop_price: float
    entry_type: EntryType
    atr: float = 0.0

def check_pullback_reclaim(window: list[Candle], side: Side, ema20: list[float], atr: float) -> TCSignal | None:
    """PB: Price pulled back to EMA20 (within 0.3 ATR), then candle closes back past EMA20."""
    if len(window) < 5 or len(ema20) < len(window): return None
    curr = window[-1]
    prev = window[-2]
    ema_now = ema20[-1]
    ema_prev = ema20[-2]
    tolerance = atr * 0.3

    if side == Side.LONG:
        # Previous candle touched or dipped below EMA20
        touched = prev.low <= ema_prev + tolerance
        # Current candle closes back above EMA20
        reclaimed = curr.close > ema_now
        if touched and reclaimed:
            # Stop below pullback low (lowest low of last 4 candles)
            stop = min(c.low for c in window[-4:]) - atr * 0.1
            if stop < curr.close:
                return TCSignal(Side.LONG, curr.close, stop, EntryType.PB, atr)
    else:
        touched = prev.high >= ema_prev - tolerance
        reclaimed = curr.close < ema_now
        if touched and reclaimed:
            stop = max(c.high for c in window[-4:]) + atr * 0.1
            if stop > curr.close:
                return TCSignal(Side.SHORT, curr.close, stop, EntryType.PB, atr)
    return None

def check_swing_break(window: list[Candle], side: Side, atr: float) -> TCSignal | None:
    """SB: Break of prior 15m swing high/low with volume expansion (>1.5× avg)."""
    if len(window) < 12: return None
    curr = window[-1]
    lookback = window[-12:-1]  # prior 11 candles, excluding current
    vol_avg = avg_volume(window[:-1], 20)
    if vol_avg <= 0: return None
    vol_mult = curr.volume / vol_avg

    if vol_mult < 1.5: return None

    if side == Side.LONG:
        sh = max(c.high for c in lookback)
        if curr.close > sh:
            stop = min(c.low for c in lookback[-5:]) - atr * 0.1
            if stop < curr.close:
                return TCSignal(Side.LONG, curr.close, stop, EntryType.SB, atr)
    else:
        sl = min(c.low for c in lookback)
        if curr.close < sl:
            stop = max(c.high for c in lookback[-5:]) + atr * 0.1
            if stop > curr.close:
                return TCSignal(Side.SHORT, curr.close, stop, EntryType.SB, atr)
    return None

def check_atr_contraction(window: list[Candle], atr_series: list[float], side: Side, atr: float) -> TCSignal | None:
    """ACC: ATR contraction after trend impulse, then continuation break."""
    if len(window) < 25 or len(atr_series) < len(window): return None
    curr = window[-1]

    # Check for prior impulse: net move in last 10-20 candles > 2× current ATR
    impulse_window = window[-20:-5]
    if not impulse_window: return None
    if side == Side.LONG:
        impulse_move = impulse_window[-1].close - impulse_window[0].close
    else:
        impulse_move = impulse_window[0].close - impulse_window[-1].close
    if impulse_move < atr * 1.5: return None

    # Check contraction: current ATR < 40th percentile of last 50 ATR values
    recent_atrs = [a for a in atr_series[-50:] if a > 0]
    if len(recent_atrs) < 20: return None
    current_atr = atr_series[-1]
    if current_atr <= 0: return None
    sorted_atrs = sorted(recent_atrs)
    pctl_40 = sorted_atrs[int(len(sorted_atrs) * 0.4)]
    if current_atr > pctl_40: return None

    # Continuation break: price breaks above/below last 5 candle range
    range_candles = window[-6:-1]
    if side == Side.LONG:
        range_high = max(c.high for c in range_candles)
        if curr.close > range_high:
            stop = min(c.low for c in range_candles) - atr * 0.1
            if stop < curr.close:
                return TCSignal(Side.LONG, curr.close, stop, EntryType.ACC, atr)
    else:
        range_low = min(c.low for c in range_candles)
        if curr.close < range_low:
            stop = max(c.high for c in range_candles) + atr * 0.1
            if stop > curr.close:
                return TCSignal(Side.SHORT, curr.close, stop, EntryType.ACC, atr)
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Executor & Position (reused from V2.1)
# ─────────────────────────────────────────────────────────────────────────────

TAKER_BPS = 4.5
SLIP_BPS = 2.0

class Executor:
    def __init__(self, eq):
        self.equity = eq; self.initial = eq; self.peak = eq
    def fill_entry(self, price, qty, side):
        slip = price * (SLIP_BPS/10000)
        fill = price + slip if side == Side.LONG else price - slip
        fee = qty * fill * (TAKER_BPS/10000)
        self.equity -= fee
        return fill, fee
    def fill_exit(self, entry, price, qty, side):
        slip = price * (SLIP_BPS/10000)
        fill = price - slip if side == Side.LONG else price + slip
        fee = qty * fill * (TAKER_BPS/10000)
        pnl = (fill - entry)*qty if side == Side.LONG else (entry - fill)*qty
        self.equity += pnl - fee
        if self.equity > self.peak: self.peak = self.equity
        return fill, fee, pnl

class Position:
    def __init__(self, sig: TCSignal, fill, qty, lev, fee, t):
        self.id = str(uuid.uuid4())[:8]
        self.side = sig.side
        self.entry_type = sig.entry_type
        self.entry = fill
        self.qty = qty; self.orig_qty = qty; self.lev = lev
        self.stop = sig.stop_price; self.init_stop = sig.stop_price
        self.tp1 = False; self.trail_active = False; self.trail_stop = 0.0
        self.candles = 0; self.entry_time = t
        self.rpnl = 0.0; self.fees = fee
        self.closed = False; self.exit_price = 0.0
        self.exit_reason = ""; self.exit_time = None

def close_pos(p, price, reason, t, ex):
    fill, fee, pnl = ex.fill_exit(p.entry, price, p.qty, p.side)
    p.rpnl += pnl; p.fees += fee
    p.exit_price = fill; p.exit_reason = reason
    p.exit_time = t; p.closed = True

def record_trade(p, trades, ex):
    sd = abs(p.entry - p.init_stop)
    pnl = p.rpnl - p.fees
    pnl_r = p.rpnl/(sd * p.orig_qty) if sd > 0 and p.orig_qty > 0 else 0
    hs = (p.exit_time - p.entry_time).total_seconds() if p.exit_time else 0
    h = p.entry_time.hour
    sess = "US" if h >= 16 else ("Europe" if h >= 8 else "Asia")
    trades.append({
        "id": p.id, "side": p.side.value, "entry_type": p.entry_type.value,
        "entry_time": p.entry_time.isoformat(),
        "exit_time": p.exit_time.isoformat() if p.exit_time else "",
        "entry_price": round(p.entry, 2), "exit_price": round(p.exit_price, 2),
        "qty": round(p.orig_qty, 6), "leverage": p.lev,
        "init_stop": round(p.init_stop, 2), "stop_dist": round(sd, 2),
        "pnl_usd": round(pnl, 4), "pnl_r": round(pnl_r, 4),
        "fees": round(p.fees, 4), "hold_candles": p.candles,
        "exit_reason": p.exit_reason, "tp1": p.tp1,
        "equity": round(ex.equity, 2), "session": sess,
    })

# ─────────────────────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Metrics:
    label: str = ""; trades: int = 0; wr: float = 0.0
    net_r: float = 0.0; pnl_pct: float = 0.0; exp_r: float = 0.0
    pf: float = 0.0; max_dd: float = 0.0; tpw: float = 0.0
    r2: float = 0.0; sharpe: float = 0.0; calmar: float = 0.0; cagr: float = 0.0

def calc_metrics(trades, days, eq0):
    m = Metrics(); m.trades = len(trades)
    if not trades: return m
    rs = [t["pnl_r"] for t in trades]
    m.net_r = sum(rs)
    m.wr = len([r for r in rs if r > 0])/len(rs)
    m.exp_r = sum(rs)/len(rs)
    gp = sum(t["pnl_usd"] for t in trades if t["pnl_usd"] > 0)
    gl = abs(sum(t["pnl_usd"] for t in trades if t["pnl_usd"] <= 0))
    m.pf = gp/gl if gl > 0 else float("inf")
    m.tpw = len(trades)/(days/7)
    eq = [eq0]; pk = eq0; mdd = 0
    for t in trades:
        eq.append(eq[-1]+t["pnl_usd"])
        if eq[-1] > pk: pk = eq[-1]
        dd = (pk-eq[-1])/pk if pk > 0 else 0
        mdd = max(mdd, dd)
    m.max_dd = mdd
    m.pnl_pct = ((eq[-1]-eq0)/eq0)*100
    if eq[-1] > 0: m.cagr = (((eq[-1]/eq0)**(365/max(days,1)))-1)*100
    else: m.cagr = -100
    m.calmar = m.cagr/(m.max_dd*100) if m.max_dd > 0 else (m.cagr if m.cagr > 0 else 0)
    n = len(eq)
    if n >= 3:
        xs = list(range(n)); xm = sum(xs)/n; ym = sum(eq)/n
        sxy = sum((x-xm)*(y-ym) for x,y in zip(xs,eq))
        sxx = sum((x-xm)**2 for x in xs); syy = sum((y-ym)**2 for y in eq)
        if sxx > 0 and syy > 0: m.r2 = (sxy/(math.sqrt(sxx)*math.sqrt(syy)))**2
    if len(rs) >= 2:
        mu = statistics.mean(rs); sd = statistics.stdev(rs)
        if sd > 0: m.sharpe = (mu/sd)*math.sqrt(len(rs)/(days/365))
    return m

# ─────────────────────────────────────────────────────────────────────────────
# Core replay — runs on 15m candles
# ─────────────────────────────────────────────────────────────────────────────

async def run_variant(entry_type: EntryType, sess_name: str, sess_range: tuple,
                      c15m: list[Candle], trend: TrendFilter4H, is_end: datetime):
    """Run one entry_type × session variant."""
    ex = Executor(INITIAL_EQUITY)
    opens: list[Position] = []
    trades: list[dict] = []
    eq_curve: list[tuple] = []
    sh, se = sess_range

    # Precompute 15m EMA20 and ATR14 series
    ema20_series = calc_ema_series(c15m, 20)
    atr_series = calc_atr_series(c15m, 14)

    warmup = 60  # need enough candles for indicators

    for i in range(warmup, len(c15m)):
        c = c15m[i]
        ct = c.close_time
        window = c15m[max(0, i-50):i+1]
        ema_window = ema20_series[max(0, i-50):i+1]
        atr_window = atr_series[max(0, i-50):i+1]
        atr = atr_series[i] if atr_series[i] > 0 else calc_atr(window, 14)

        # ── Manage positions ──
        for p in list(opens):
            if p.closed: continue
            p.candles += 1
            cp = c.close
            sd = abs(p.entry - p.init_stop)
            rm = 0.0
            if sd > 0:
                rm = (cp - p.entry)/sd if p.side == Side.LONG else (p.entry - cp)/sd

            # Time stop
            if p.candles >= TIME_STOP_CANDLES and rm < TIME_STOP_R and not p.tp1:
                close_pos(p, cp, "time_stop", ct, ex)
                record_trade(p, trades, ex)
                eq_curve.append((ct.isoformat(), round(ex.equity, 2)))
                continue

            # Stop loss
            hit = False; ep = 0.0
            if p.side == Side.LONG and c.low <= p.stop: hit = True; ep = p.stop
            elif p.side == Side.SHORT and c.high >= p.stop: hit = True; ep = p.stop
            if hit:
                close_pos(p, ep, "stop_loss", ct, ex)
                record_trade(p, trades, ex)
                eq_curve.append((ct.isoformat(), round(ex.equity, 2)))
                continue

            # Partial TP at 1.5R
            if not p.tp1 and rm >= 1.5:
                tq = p.qty * 0.5
                fl, fe, pn = ex.fill_exit(p.entry, cp, tq, p.side)
                p.tp1 = True; p.qty -= tq; p.rpnl += pn; p.fees += fe
                p.trail_active = True

            # Trail
            if p.trail_active and atr > 0:
                td = atr * 2.5
                if p.side == Side.LONG:
                    nt = cp - td
                    if nt > p.trail_stop: p.trail_stop = nt
                    if p.trail_stop > p.stop: p.stop = p.trail_stop
                else:
                    nt = cp + td
                    if p.trail_stop == 0 or nt < p.trail_stop: p.trail_stop = nt
                    if p.trail_stop < p.stop or p.stop == 0: p.stop = p.trail_stop

        opens = [p for p in opens if not p.closed]

        # ── Entry checks ──
        if len(opens) >= 2: continue  # max 2 concurrent positions
        if ex.equity <= 0: break

        # Session filter
        h = ct.hour
        if sh < se:
            if not (sh <= h < se): continue
        # ALL passes everything

        # 4H trend
        allowed = trend.allowed_side(ct)
        if allowed is None: continue

        # Generate signal based on entry type
        sig = None
        if entry_type == EntryType.PB:
            sig = check_pullback_reclaim(window, allowed, ema_window, atr)
        elif entry_type == EntryType.SB:
            sig = check_swing_break(window, allowed, atr)
        elif entry_type == EntryType.ACC:
            sig = check_atr_contraction(window, atr_window, allowed, atr)

        if sig is None: continue

        # Position sizing
        sdist = abs(sig.entry_price - sig.stop_price)
        if sdist <= 0: continue
        ra = ex.equity * (RISK_PCT / 100.0)
        qty = ra / sdist
        notional = qty * sig.entry_price
        lev = min(int(notional / ex.equity) + 1, MAX_LEVERAGE)
        lev = max(lev, 1)
        mn = ex.equity * lev * 0.95
        if notional > mn: qty = mn / sig.entry_price
        if qty <= 0: continue

        fill, fee = ex.fill_entry(sig.entry_price, qty, sig.side)
        p = Position(sig, fill, qty, lev, fee, ct)
        opens.append(p)

    # Close remaining
    if c15m:
        lp = c15m[-1].close
        for p in opens:
            if not p.closed:
                close_pos(p, lp, "replay_end", c15m[-1].close_time, ex)
                record_trade(p, trades, ex)
                eq_curve.append((c15m[-1].close_time.isoformat(), round(ex.equity, 2)))

    clean = [{k:v for k,v in t.items() if not k.startswith("_")} for t in trades]
    is_t = [t for t in clean if t["entry_time"] < is_end.isoformat()]
    oos_t = [t for t in clean if t["entry_time"] >= is_end.isoformat()]

    return {
        "name": f"{entry_type.value}_{sess_name}",
        "entry_type": entry_type.value,
        "session": sess_name,
        "trades": clean,
        "eq_curve": eq_curve,
        "full": calc_metrics(clean, DAYS, INITIAL_EQUITY),
        "is": calc_metrics(is_t, IS_DAYS, INITIAL_EQUITY),
        "oos": calc_metrics(oos_t, OOS_DAYS, INITIAL_EQUITY),
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
    print(f"║  BTC TREND CONTINUATION ENGINE — 365-DAY REPLAY                    ║")
    print(f"║  {SYMBOL} | {start.date()} → {end.date()}                          ║")
    print(f"║  IS={IS_DAYS}d OOS={OOS_DAYS}d | 3 entries × 3 sessions = 9 variants       ║")
    print(f"╚{'═'*70}╝")

    print("\n📡  Fetching data...")
    rl = RateLimiterGroup()
    client = BinanceRestClient(testnet=False, rate_limiter=rl)
    await client.start()

    start_4h = start - timedelta(days=60)
    c15m = await fetch_klines(client, SYMBOL, "15m", start, end)
    print(f"  ✓ 15m: {len(c15m)} candles")
    c4h = await fetch_klines(client, SYMBOL, "4h", start_4h, end)
    print(f"  ✓ 4H:  {len(c4h)} candles")
    await client.close()

    if not c15m:
        print("ERROR: No data. Aborting."); return

    trend = TrendFilter4H(c4h)
    print(f"\n  4H trend filter built ({len(c4h)} candles)")

    print(f"\n🔁  Running 9 variants...\n")
    results = []
    t0 = time.time()

    for et in EntryType:
        for sn, sr in SESSIONS.items():
            vt = time.time()
            r = await run_variant(et, sn, sr, c15m, trend, is_end)
            el = time.time() - vt
            results.append(r)
            f = r["full"]
            st = "✅" if f.exp_r > 0 else "❌"
            print(f"  {st} {r['name']:<14s}  T={f.trades:>3d}  WR={f.wr:.1%}  "
                  f"NetR={f.net_r:>+7.2f}  Exp={f.exp_r:>+.4f}R  PF={f.pf:.2f}  "
                  f"DD={f.max_dd:.1%}  IS={r['is'].trades}t  OOS={r['oos'].trades}t  ({el:.1f}s)")

    print(f"\n  Total: {time.time()-t0:.1f}s")

    # Output
    out = Path("replay_output/trend_cont")
    out.mkdir(parents=True, exist_ok=True)

    for r in results:
        if r["trades"]:
            with open(out / f"trades_{r['name']}.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=r["trades"][0].keys())
                w.writeheader(); w.writerows(r["trades"])
        if r["eq_curve"]:
            with open(out / f"equity_{r['name']}.csv", "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["timestamp","equity"]); w.writerows(r["eq_curve"])

    # Summary CSV
    rows = []
    for r in sorted(results, key=lambda x: x["full"].exp_r, reverse=True):
        f = r["full"]; i = r["is"]; o = r["oos"]
        rows.append({
            "variant": r["name"], "entry_type": r["entry_type"], "session": r["session"],
            "trades": f.trades, "wr": round(f.wr,4), "net_r": round(f.net_r,2),
            "pnl_pct": round(f.pnl_pct,2), "pf": round(f.pf,4), "exp_r": round(f.exp_r,4),
            "max_dd": round(f.max_dd,4), "tpw": round(f.tpw,1),
            "r2": round(f.r2,4), "sharpe": round(f.sharpe,4), "calmar": round(f.calmar,4),
            "is_trades": i.trades, "is_net_r": round(i.net_r,2), "is_exp_r": round(i.exp_r,4),
            "oos_trades": o.trades, "oos_net_r": round(o.net_r,2), "oos_exp_r": round(o.exp_r,4),
        })
    if rows:
        with open(out / "summary.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader(); w.writerows(rows)

    # ── Print results ──
    sep = "=" * 120
    thin = "-" * 120

    print(f"\n{sep}")
    print("  BTC TREND CONTINUATION ENGINE — RANKED RESULTS")
    print(f"  {start.date()} → {end.date()} ({DAYS}d) | IS={IS_DAYS}d OOS={OOS_DAYS}d")
    print(sep)

    hdr = f"  {'Variant':<14s} {'T':>4s} {'WR':>6s} {'NetR':>8s} {'PnL%':>8s} {'PF':>6s} {'Exp/R':>8s} {'MaxDD':>7s} {'T/wk':>5s} {'R²':>5s} {'Shrp':>6s} {'Calmr':>6s}"

    for pn, gm in [("FULL", "full"), ("IN-SAMPLE", "is"), ("OUT-OF-SAMPLE", "oos")]:
        print(f"\n  ── {pn} ──")
        print(hdr); print(f"  {thin}")
        for r in sorted(results, key=lambda x: x[gm].exp_r, reverse=True):
            m = r[gm]
            st = "✅" if m.exp_r > 0 else "❌"
            print(f"{st} {r['name']:<14s} {m.trades:>4d} {m.wr:>5.1%} {m.net_r:>+7.2f}R "
                  f"{m.pnl_pct:>+7.2f}% {m.pf:>5.2f} {m.exp_r:>+7.4f}R "
                  f"{m.max_dd:>6.1%} {m.tpw:>5.1f} {m.r2:>5.3f} {m.sharpe:>+5.2f} {m.calmar:>+5.2f}")

    # Session breakdown per entry type
    print(f"\n{sep}")
    print("  SESSION × ENTRY TYPE HEATMAP")
    print(sep)
    print(f"  {'Entry':<6s} {'Session':<10s} {'Asia T':>6s} {'Asia R':>8s} {'EU T':>6s} {'EU R':>8s} {'US T':>6s} {'US R':>8s}")
    print(f"  {thin}")
    for r in results:
        ts = r["trades"]
        asia = [t for t in ts if t.get("session") == "Asia"]
        eu = [t for t in ts if t.get("session") == "Europe"]
        us = [t for t in ts if t.get("session") == "US"]
        def sr(tl): return len(tl), sum(t["pnl_r"] for t in tl) if tl else 0
        an,ar = sr(asia); en,er = sr(eu); un,ur = sr(us)
        print(f"  {r['entry_type']:<6s} {r['session']:<10s} {an:>6d} {ar:>+7.2f}R {en:>6d} {er:>+7.2f}R {un:>6d} {ur:>+7.2f}R")

    # Top candidates
    print(f"\n{sep}")
    print("  TOP CANDIDATES (by full-period expectancy)")
    print(sep)
    ranked = sorted(results, key=lambda x: x["full"].exp_r, reverse=True)
    for rank, r in enumerate(ranked[:3], 1):
        f = r["full"]; i = r["is"]; o = r["oos"]
        is_pos = i.exp_r > 0; oos_pos = o.exp_r > 0
        verdict = "✅ STRONG" if is_pos and oos_pos else "⚠️ PARTIAL" if is_pos or oos_pos else "❌ WEAK"
        print(f"""
  #{rank}  {r['name']}  ({verdict})
      Full:   {f.trades:>3d}t  WR={f.wr:.1%}  NetR={f.net_r:>+.2f}R  Exp={f.exp_r:>+.4f}R  PF={f.pf:.2f}  DD={f.max_dd:.1%}
      IS:     {i.trades:>3d}t  WR={i.wr:.1%}  NetR={i.net_r:>+.2f}R  Exp={i.exp_r:>+.4f}R
      OOS:    {o.trades:>3d}t  WR={o.wr:.1%}  NetR={o.net_r:>+.2f}R  Exp={o.exp_r:>+.4f}R""")

    # Comparison with compression breakout
    print(f"\n{sep}")
    print("  vs COMPRESSION BREAKOUT BASELINE")
    print(sep)
    best = ranked[0]
    bf = best["full"]
    print(f"""
  Compression BBO (BTC 180d):    Exp=−0.044R  WR=30.8%  PF=0.32  DD=12.8%
  Trend Continuation best:       Exp={bf.exp_r:>+.4f}R  WR={bf.wr:.1%}  PF={bf.pf:.2f}  DD={bf.max_dd:.1%}
  Improvement: Exp {bf.exp_r - (-0.044):>+.4f}R  WR {(bf.wr - 0.308)*100:>+.1f}pp
""")

    print(sep)

if __name__ == "__main__":
    asyncio.run(main())
