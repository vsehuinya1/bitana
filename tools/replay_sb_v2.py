"""
BTC Swing Break V2 — Regime-Gated 365-Day Replay

Entry: 15m swing high/low break with volume expansion (>1.5× avg)
Trend: 4H EMA20/50 (NEUTRAL = no trade)

Regime Filters (5):
  NONE    — baseline, no extra filter
  ADX     — 4H ADX(14) > 20
  SLOPE   — 4H EMA20 > EMA50 AND EMA20 slope positive (rising over 3 bars)
  DATR    — Daily ATR(14) > 90-day median
  COMBO   — ADX AND SLOPE combined

Sessions (3): ALL / ASIA_EU / ASIA
Risk Modes (3): FIXED / HALF_WEAK / PAUSE_3L

Total: 5 × 3 × 3 = 45 variants
"""
from __future__ import annotations
import bisect, asyncio, csv, math, statistics, sys, time, uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from enum import Enum

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.loader import load_config
from core.logging_setup import get_logger
from core.models import Candle, Side
from data.binance_rest import BinanceRestClient
from data.rate_limiter import RateLimiterGroup

logger = get_logger("replay_sb2")

SYMBOL = "BTCUSDT"
DAYS = 365
IS_DAYS = 240
OOS_DAYS = 125
INITIAL_EQUITY = 1000.0
RISK_PCT = 1.0
MAX_LEV = 10
TIME_STOP = 8
TIME_STOP_R = 0.5

SESSIONS = {"ALL": (0, 24), "ASIA_EU": (0, 16), "ASIA": (0, 8)}

# ─── Data ────────────────────────────────────────────────────────────────────

async def fetch(client, symbol, interval, start, end):
    out = []; ms = int(start.timestamp()*1000); me = int(end.timestamp()*1000); b = 0
    while ms < me:
        raw = await client.get_klines(symbol=symbol, interval=interval, start_time=ms, limit=1500)
        if not raw or not isinstance(raw, list): break
        for k in raw:
            if k[6] > me: break
            out.append(Candle(symbol=symbol, timeframe=interval,
                open_time=datetime.fromtimestamp(k[0]/1000, tz=timezone.utc),
                close_time=datetime.fromtimestamp(k[6]/1000, tz=timezone.utc),
                open=float(k[1]), high=float(k[2]), low=float(k[3]),
                close=float(k[4]), volume=float(k[5]), is_closed=True))
        ms = int(raw[-1][6]) + 1; b += 1
        if len(raw) < 1500: break
        if b % 10 == 0: print(f"  ... {interval}: {len(out)} candles")
        await asyncio.sleep(0.15)
    seen = set(); deduped = []
    for c in out:
        if c.open_time not in seen: seen.add(c.open_time); deduped.append(c)
    return sorted(deduped, key=lambda c: c.open_time)

# ─── Indicators ──────────────────────────────────────────────────────────────

def _ema(v, p):
    if not v: return []
    k = 2.0/(p+1); o = [v[0]]
    for x in v[1:]: o.append(x*k + o[-1]*(1-k))
    return o

def calc_atr_series(candles, period=14):
    if len(candles) < 2: return [0.0]*len(candles)
    trs = [0.0]
    for i in range(1, len(candles)):
        p = candles[i-1].close; c = candles[i]
        trs.append(max(c.high-c.low, abs(c.high-p), abs(c.low-p)))
    atrs = [0.0]*len(candles)
    if len(trs) > period:
        a = sum(trs[1:period+1])/period; atrs[period] = a
        for j in range(period+1, len(trs)):
            a = (a*(period-1)+trs[j])/period; atrs[j] = a
    return atrs

def calc_adx_series(candles, period=14):
    """Returns list of ADX values (one per candle). 0 for warmup."""
    n = len(candles)
    if n < period*2+1: return [0.0]*n
    # +DM, -DM, TR
    pdm = [0.0]; ndm = [0.0]; trs = [0.0]
    for i in range(1, n):
        h = candles[i].high; l = candles[i].low
        ph = candles[i-1].high; pl = candles[i-1].low; pc = candles[i-1].close
        up = h - ph; dn = pl - l
        pdm.append(up if up > dn and up > 0 else 0)
        ndm.append(dn if dn > up and dn > 0 else 0)
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    # Smoothed
    def smooth(vals, p):
        s = [0.0]*(p+1)
        s[p] = sum(vals[1:p+1])
        for i in range(p+1, len(vals)):
            s.append(s[-1] - s[-1]/p + vals[i])
        return s
    s_pdm = smooth(pdm, period)
    s_ndm = smooth(ndm, period)
    s_tr = smooth(trs, period)
    # DI and DX
    dx_vals = [0.0]*n
    for i in range(period, n):
        if i >= len(s_tr) or s_tr[i] == 0: continue
        pdi = 100 * s_pdm[i] / s_tr[i] if i < len(s_pdm) else 0
        ndi = 100 * s_ndm[i] / s_tr[i] if i < len(s_ndm) else 0
        denom = pdi + ndi
        dx_vals[i] = abs(pdi - ndi) / denom * 100 if denom > 0 else 0
    # ADX = smoothed DX
    adx = [0.0]*n
    start_idx = period*2
    if start_idx < n:
        adx[start_idx] = sum(dx_vals[period:start_idx+1]) / (period+1) if start_idx >= period else 0
        for i in range(start_idx+1, n):
            adx[i] = (adx[i-1]*(period-1) + dx_vals[i]) / period
    return adx

# ─── 4H Trend + Regime Filters ──────────────────────────────────────────────

class TrendAndRegime:
    def __init__(self, c4h, c1d):
        closes4h = [c.close for c in c4h]
        self.ema20 = _ema(closes4h, 20)
        self.ema50 = _ema(closes4h, 50)
        self.adx = calc_adx_series(c4h, 14)
        self.c4h = c4h
        self._4h_times = [c.close_time for c in c4h]
        # Daily ATR
        self.datr = calc_atr_series(c1d, 14)
        self.c1d = c1d
        self._1d_times = [c.close_time for c in c1d]
        # 90-day rolling median of daily ATR
        self.datr_medians = [0.0]*len(c1d)
        for i in range(90, len(c1d)):
            window = [a for a in self.datr[max(0,i-90):i] if a > 0]
            if window:
                sw = sorted(window)
                self.datr_medians[i] = sw[len(sw)//2]

    def _4h_idx(self, t):
        idx = bisect.bisect_right(self._4h_times, t) - 1
        return max(0, idx)

    def _1d_idx(self, t):
        idx = bisect.bisect_right(self._1d_times, t) - 1
        return max(0, idx)

    def trend_side(self, t):
        i = self._4h_idx(t)
        if i < 50: return None
        if self.ema20[i] > self.ema50[i]: return Side.LONG
        if self.ema20[i] < self.ema50[i]: return Side.SHORT
        return None

    def adx_strong(self, t):
        i = self._4h_idx(t)
        return self.adx[i] > 20

    def ema_slope_ok(self, t):
        i = self._4h_idx(t)
        if i < 53: return False
        side = self.trend_side(t)
        if side is None: return False
        # EMA20 slope positive over last 3 bars
        if side == Side.LONG:
            return self.ema20[i] > self.ema20[i-1] > self.ema20[i-2] > self.ema20[i-3]
        else:
            return self.ema20[i] < self.ema20[i-1] < self.ema20[i-2] < self.ema20[i-3]

    def datr_above_median(self, t):
        i = self._1d_idx(t)
        if i < 90: return True  # default allow during warmup
        return self.datr[i] > self.datr_medians[i] and self.datr_medians[i] > 0

    def regime_strong(self, t):
        """Combined: ADX strong AND EMA slope OK."""
        return self.adx_strong(t) and self.ema_slope_ok(t)

    def check_regime(self, t, regime_name):
        if regime_name == "NONE": return True
        if regime_name == "ADX": return self.adx_strong(t)
        if regime_name == "SLOPE": return self.ema_slope_ok(t)
        if regime_name == "DATR": return self.datr_above_median(t)
        if regime_name == "COMBO": return self.regime_strong(t)
        return True

# ─── Swing Break Signal ─────────────────────────────────────────────────────

def check_swing_break(window, side, atr):
    if len(window) < 12 or atr <= 0: return None
    curr = window[-1]
    lookback = window[-12:-1]
    vol_avg = sum(c.volume for c in window[-21:-1]) / 20 if len(window) >= 21 else 0
    if vol_avg <= 0: return None
    if curr.volume / vol_avg < 1.5: return None
    if side == Side.LONG:
        sh = max(c.high for c in lookback)
        if curr.close > sh:
            stop = min(c.low for c in lookback[-5:]) - atr * 0.1
            if stop < curr.close:
                return (curr.close, stop)
    else:
        sl = min(c.low for c in lookback)
        if curr.close < sl:
            stop = max(c.high for c in lookback[-5:]) + atr * 0.1
            if stop > curr.close:
                return (curr.close, stop)
    return None

# ─── Executor ────────────────────────────────────────────────────────────────

TAKER = 4.5; SLIP = 2.0

class Ex:
    def __init__(self, eq):
        self.eq = eq; self.init = eq; self.peak = eq
    def entry(self, p, q, s):
        sl = p*(SLIP/10000); f = p+sl if s==Side.LONG else p-sl
        fee = q*f*(TAKER/10000); self.eq -= fee; return f, fee
    def exit(self, ent, p, q, s):
        sl = p*(SLIP/10000); f = p-sl if s==Side.LONG else p+sl
        fee = q*f*(TAKER/10000)
        pnl = (f-ent)*q if s==Side.LONG else (ent-f)*q
        self.eq += pnl-fee
        if self.eq > self.peak: self.peak = self.eq
        return f, fee, pnl

class Pos:
    __slots__ = ['side','ent','qty','oq','lev','stop','istop','tp1',
                 'trail_on','trail','candles','t0','rpnl','fees',
                 'closed','exp','reason','t1']
    def __init__(self, side, fill, qty, lev, fee, t):
        self.side=side; self.ent=fill; self.qty=qty; self.oq=qty
        self.lev=lev; self.stop=0.0; self.istop=0.0
        self.tp1=False; self.trail_on=False; self.trail=0.0
        self.candles=0; self.t0=t; self.rpnl=0.0; self.fees=fee
        self.closed=False; self.exp=0.0; self.reason=""; self.t1=None

def close_p(p, price, reason, t, ex):
    f, fee, pnl = ex.exit(p.ent, price, p.qty, p.side)
    p.rpnl += pnl; p.fees += fee; p.exp = f
    p.reason = reason; p.t1 = t; p.closed = True

def record(p, trades, ex):
    sd = abs(p.ent - p.istop)
    net = p.rpnl - p.fees
    pr = p.rpnl/(sd*p.oq) if sd > 0 and p.oq > 0 else 0
    h = p.t0.hour; sess = "US" if h >= 16 else ("EU" if h >= 8 else "Asia")
    trades.append({
        "side": p.side.value, "entry_time": p.t0.isoformat(),
        "exit_time": p.t1.isoformat() if p.t1 else "",
        "entry_price": round(p.ent,2), "exit_price": round(p.exp,2),
        "qty": round(p.oq,6), "stop_dist": round(sd,2),
        "pnl_usd": round(net,4), "pnl_r": round(pr,4),
        "fees": round(p.fees,4), "hold_candles": p.candles,
        "exit_reason": p.reason, "tp1": p.tp1,
        "equity": round(ex.eq,2), "session": sess,
    })

# ─── Metrics ─────────────────────────────────────────────────────────────────

@dataclass
class M:
    t:int=0; wr:float=0; nr:float=0; pnl:float=0; exp:float=0
    pf:float=0; dd:float=0; tpw:float=0; r2:float=0; sh:float=0
    cal:float=0; cagr:float=0

def metrics(trades, days, eq0):
    m = M(); m.t = len(trades)
    if not trades: return m
    rs = [t["pnl_r"] for t in trades]
    m.nr = sum(rs); m.wr = len([r for r in rs if r>0])/len(rs); m.exp = sum(rs)/len(rs)
    gp = sum(t["pnl_usd"] for t in trades if t["pnl_usd"]>0)
    gl = abs(sum(t["pnl_usd"] for t in trades if t["pnl_usd"]<=0))
    m.pf = gp/gl if gl > 0 else float("inf")
    m.tpw = len(trades)/(days/7)
    eq=[eq0]; pk=eq0; dd=0
    for t in trades:
        eq.append(eq[-1]+t["pnl_usd"])
        if eq[-1]>pk: pk=eq[-1]
        d=(pk-eq[-1])/pk if pk>0 else 0; dd=max(dd,d)
    m.dd=dd; m.pnl=((eq[-1]-eq0)/eq0)*100
    if eq[-1]>0: m.cagr=(((eq[-1]/eq0)**(365/max(days,1)))-1)*100
    else: m.cagr=-100
    m.cal=m.cagr/(m.dd*100) if m.dd>0 else (m.cagr if m.cagr>0 else 0)
    n=len(eq)
    if n>=3:
        xs=list(range(n)); xm=sum(xs)/n; ym=sum(eq)/n
        sxy=sum((x-xm)*(y-ym) for x,y in zip(xs,eq))
        sxx=sum((x-xm)**2 for x in xs); syy=sum((y-ym)**2 for y in eq)
        if sxx>0 and syy>0: m.r2=(sxy/(math.sqrt(sxx)*math.sqrt(syy)))**2
    if len(rs)>=2:
        mu=statistics.mean(rs); sd=statistics.stdev(rs)
        if sd>0: m.sh=(mu/sd)*math.sqrt(len(rs)/(days/365))
    return m

# ─── Replay ──────────────────────────────────────────────────────────────────

def run_variant(regime_name, sess_name, risk_name, c15m, atr15, tar, is_end):
    ex = Ex(INITIAL_EQUITY)
    opens = []; trades = []; eq_curve = []
    sh, se = SESSIONS[sess_name]
    warmup = 60
    closs_24h = []  # timestamps of consecutive losses

    for i in range(warmup, len(c15m)):
        c = c15m[i]; ct = c.close_time
        atr = atr15[i] if atr15[i] > 0 else 0
        window = c15m[max(0,i-50):i+1]

        # manage
        for p in list(opens):
            if p.closed: continue
            p.candles += 1; cp = c.close
            sd = abs(p.ent - p.istop); rm = 0.0
            if sd > 0: rm = (cp-p.ent)/sd if p.side==Side.LONG else (p.ent-cp)/sd
            if p.candles >= TIME_STOP and rm < TIME_STOP_R and not p.tp1:
                close_p(p, cp, "time_stop", ct, ex); record(p, trades, ex)
                eq_curve.append((ct.isoformat(), round(ex.eq,2))); continue
            hit=False; ep=0
            if p.side==Side.LONG and c.low<=p.stop: hit=True; ep=p.stop
            elif p.side==Side.SHORT and c.high>=p.stop: hit=True; ep=p.stop
            if hit:
                close_p(p, ep, "stop_loss", ct, ex); record(p, trades, ex)
                eq_curve.append((ct.isoformat(), round(ex.eq,2))); continue
            if not p.tp1 and rm >= 1.5:
                tq = p.qty*0.5; f,fe,pn=ex.exit(p.ent,cp,tq,p.side)
                p.tp1=True; p.qty-=tq; p.rpnl+=pn; p.fees+=fe; p.trail_on=True
            if p.trail_on and atr > 0:
                td = atr*2.5
                if p.side==Side.LONG:
                    nt=cp-td
                    if nt>p.trail: p.trail=nt
                    if p.trail>p.stop: p.stop=p.trail
                else:
                    nt=cp+td
                    if p.trail==0 or nt<p.trail: p.trail=nt
                    if p.trail<p.stop or p.stop==0: p.stop=p.trail

        opens = [p for p in opens if not p.closed]

        # track consecutive losses for PAUSE_3L
        for t in trades:
            if t.get("_p"): continue
            t["_p"] = True
            if t["pnl_r"] < 0:
                closs_24h.append(datetime.fromisoformat(t["exit_time"]))
            else:
                closs_24h.clear()

        # entry checks
        if len(opens) >= 2: continue
        if ex.eq <= 0: break

        # session
        h = ct.hour
        if sh < se:
            if not (sh <= h < se): continue

        # trend
        side = tar.trend_side(ct)
        if side is None: continue

        # regime
        if not tar.check_regime(ct, regime_name): continue

        # risk mode adjustments
        risk_pct = RISK_PCT
        if risk_name == "HALF_WEAK":
            if not tar.regime_strong(ct):
                risk_pct = RISK_PCT * 0.5
        elif risk_name == "PAUSE_3L":
            # pause if 3+ consecutive losses in last 24h
            cutoff = ct - timedelta(hours=24)
            recent = [t for t in closs_24h if t > cutoff]
            if len(recent) >= 3: continue

        # signal
        sig = check_swing_break(window, side, atr)
        if sig is None: continue
        entry_price, stop_price = sig

        # size
        sdist = abs(entry_price - stop_price)
        if sdist <= 0: continue
        ra = ex.eq * (risk_pct/100.0); qty = ra/sdist
        notional = qty*entry_price
        lev = min(int(notional/ex.eq)+1, MAX_LEV); lev = max(lev,1)
        mn = ex.eq*lev*0.95
        if notional > mn: qty = mn/entry_price
        if qty <= 0: continue

        fill, fee = ex.entry(entry_price, qty, side)
        p = Pos(side, fill, qty, lev, fee, ct)
        p.stop = stop_price; p.istop = stop_price
        opens.append(p)

    if c15m:
        lp = c15m[-1].close
        for p in opens:
            if not p.closed:
                close_p(p, lp, "replay_end", c15m[-1].close_time, ex)
                record(p, trades, ex)
                eq_curve.append((c15m[-1].close_time.isoformat(), round(ex.eq,2)))

    clean = [{k:v for k,v in t.items() if not k.startswith("_")} for t in trades]
    is_t = [t for t in clean if t["entry_time"] < is_end.isoformat()]
    oos_t = [t for t in clean if t["entry_time"] >= is_end.isoformat()]

    label = f"SB_{regime_name}_{sess_name}_{risk_name}"
    return {
        "label": label, "regime": regime_name, "session": sess_name, "risk": risk_name,
        "trades": clean, "eq_curve": eq_curve,
        "full": metrics(clean, DAYS, INITIAL_EQUITY),
        "is": metrics(is_t, IS_DAYS, INITIAL_EQUITY),
        "oos": metrics(oos_t, OOS_DAYS, INITIAL_EQUITY),
    }

# ─── Main ────────────────────────────────────────────────────────────────────

async def main():
    config = load_config()
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=DAYS)
    is_end = start + timedelta(days=IS_DAYS)

    regimes = ["NONE", "ADX", "SLOPE", "DATR", "COMBO"]
    sessions = list(SESSIONS.keys())
    risks = ["FIXED", "HALF_WEAK", "PAUSE_3L"]
    total = len(regimes) * len(sessions) * len(risks)

    print(f"╔{'═'*70}╗")
    print(f"║  BTC SWING BREAK V2 — REGIME-GATED 365-DAY REPLAY                 ║")
    print(f"║  {SYMBOL} | {start.date()} → {end.date()} | {total} variants          ║")
    print(f"║  IS={IS_DAYS}d OOS={OOS_DAYS}d                                          ║")
    print(f"╚{'═'*70}╝")

    print("\n📡  Fetching data...")
    rl = RateLimiterGroup()
    client = BinanceRestClient(testnet=False, rate_limiter=rl)
    await client.start()

    s4h = start - timedelta(days=60)
    s1d = start - timedelta(days=120)
    c15m = await fetch(client, SYMBOL, "15m", start, end)
    print(f"  ✓ 15m: {len(c15m)} candles")
    c4h = await fetch(client, SYMBOL, "4h", s4h, end)
    print(f"  ✓ 4H:  {len(c4h)} candles")
    c1d = await fetch(client, SYMBOL, "1d", s1d, end)
    print(f"  ✓ 1D:  {len(c1d)} candles")
    await client.close()

    if not c15m: print("ERROR: No data."); return

    tar = TrendAndRegime(c4h, c1d)
    atr15 = calc_atr_series(c15m, 14)
    print(f"\n  Regime filters built (4H ADX/EMA, Daily ATR)")

    print(f"\n🔁  Running {total} variants...\n")
    results = []; t0 = time.time(); idx = 0

    for reg in regimes:
        for sess in sessions:
            for risk in risks:
                idx += 1; vt = time.time()
                r = run_variant(reg, sess, risk, c15m, atr15, tar, is_end)
                el = time.time() - vt; results.append(r)
                f = r["full"]
                st = "✅" if f.exp > 0 else "❌"
                print(f"  [{idx:2d}/{total}] {st} {r['label']:<30s} "
                      f"T={f.t:>4d} WR={f.wr:.1%} NetR={f.nr:>+7.2f} "
                      f"Exp={f.exp:>+.4f}R PF={f.pf:.2f} DD={f.dd:.1%} "
                      f"IS={r['is'].t}t OOS={r['oos'].t}t ({el:.1f}s)")

    print(f"\n  Total: {time.time()-t0:.1f}s")

    # output
    out = Path("replay_output/sb_v2")
    out.mkdir(parents=True, exist_ok=True)

    for r in results:
        if r["trades"]:
            with open(out / f"trades_{r['label']}.csv","w",newline="") as f:
                w=csv.DictWriter(f,fieldnames=r["trades"][0].keys()); w.writeheader(); w.writerows(r["trades"])
        if r["eq_curve"]:
            with open(out / f"equity_{r['label']}.csv","w",newline="") as f:
                w=csv.writer(f); w.writerow(["ts","eq"]); w.writerows(r["eq_curve"])

    rows = []
    for r in sorted(results, key=lambda x: x["full"].exp, reverse=True):
        f=r["full"]; i=r["is"]; o=r["oos"]
        rows.append({"variant":r["label"],"regime":r["regime"],"session":r["session"],
                      "risk":r["risk"],"trades":f.t,"wr":round(f.wr,4),
                      "net_r":round(f.nr,2),"pnl_pct":round(f.pnl,2),
                      "pf":round(f.pf,4),"exp_r":round(f.exp,4),
                      "max_dd":round(f.dd,4),"tpw":round(f.tpw,1),
                      "sharpe":round(f.sh,4),"calmar":round(f.cal,4),
                      "is_t":i.t,"is_nr":round(i.nr,2),"is_exp":round(i.exp,4),
                      "oos_t":o.t,"oos_nr":round(o.nr,2),"oos_exp":round(o.exp,4)})
    if rows:
        with open(out/"summary.csv","w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)

    # ── Print ──
    sep = "="*130; thin = "-"*130
    print(f"\n{sep}")
    print("  BTC SWING BREAK V2 — RANKED RESULTS")
    print(f"  {DAYS}d | IS={IS_DAYS}d OOS={OOS_DAYS}d | {total} variants")
    print(sep)

    hdr = f"  {'Variant':<30s} {'T':>4s} {'WR':>6s} {'NetR':>8s} {'Exp/R':>8s} {'PF':>6s} {'MaxDD':>7s} {'Shrp':>6s}"

    for pn, gm in [("FULL", "full"), ("IN-SAMPLE", "is"), ("OUT-OF-SAMPLE", "oos")]:
        print(f"\n  ── {pn} (top 15) ──")
        print(hdr); print(f"  {thin}")
        ranked = sorted(results, key=lambda x: x[gm].exp, reverse=True)
        for r in ranked[:15]:
            m = r[gm]
            st = "✅" if m.exp > 0 else "❌"
            print(f"{st} {r['label']:<30s} {m.t:>4d} {m.wr:>5.1%} {m.nr:>+7.2f}R "
                  f"{m.exp:>+7.4f}R {m.pf:>5.2f} {m.dd:>6.1%} {m.sh:>+5.2f}")

    # Regime comparison (aggregated across sessions/risks)
    print(f"\n{sep}")
    print("  REGIME FILTER COMPARISON (averaged across sessions & risk modes)")
    print(sep)
    for reg in regimes:
        reg_res = [r for r in results if r["regime"] == reg]
        if not reg_res: continue
        avg_exp = statistics.mean(r["full"].exp for r in reg_res)
        avg_dd = statistics.mean(r["full"].dd for r in reg_res)
        avg_t = statistics.mean(r["full"].t for r in reg_res)
        avg_wr = statistics.mean(r["full"].wr for r in reg_res)
        pos_oos = sum(1 for r in reg_res if r["oos"].exp > 0)
        print(f"  {reg:<8s}  AvgT={avg_t:>6.0f}  AvgWR={avg_wr:.1%}  "
              f"AvgExp={avg_exp:>+.4f}R  AvgDD={avg_dd:.1%}  "
              f"OOS+={pos_oos}/{len(reg_res)}")

    # Risk comparison
    print(f"\n{sep}")
    print("  RISK MODE COMPARISON")
    print(sep)
    for risk in risks:
        rr = [r for r in results if r["risk"] == risk]
        if not rr: continue
        avg_exp = statistics.mean(r["full"].exp for r in rr)
        avg_dd = statistics.mean(r["full"].dd for r in rr)
        pos_full = sum(1 for r in rr if r["full"].exp > 0)
        print(f"  {risk:<12s}  AvgExp={avg_exp:>+.4f}R  AvgDD={avg_dd:.1%}  "
              f"Full+={pos_full}/{len(rr)}")

    # Top candidates (positive full OR positive both IS+OOS)
    print(f"\n{sep}")
    print("  TOP CANDIDATES")
    print(sep)
    ranked = sorted(results, key=lambda x: x["full"].exp, reverse=True)
    for rank, r in enumerate(ranked[:5], 1):
        f=r["full"]; i=r["is"]; o=r["oos"]
        ip=i.exp>0; op=o.exp>0
        v="✅ STRONG" if ip and op else "⚠️ PARTIAL" if ip or op else "❌ WEAK"
        print(f"""
  #{rank}  {r['label']}  ({v})
      Full:  {f.t:>4d}t  WR={f.wr:.1%}  NetR={f.nr:>+.2f}R  Exp={f.exp:>+.4f}R  PF={f.pf:.2f}  DD={f.dd:.1%}
      IS:    {i.t:>4d}t  Exp={i.exp:>+.4f}R  |  OOS: {o.t:>4d}t  Exp={o.exp:>+.4f}R""")

    print(f"\n{sep}")

if __name__ == "__main__":
    asyncio.run(main())
