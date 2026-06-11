"""
V6 path backtester — large-sample expectancy for entry/exit research.

Replays the REAL engine (`evaluate` + `manage_position`) over historical 5m klines and
records a bar-by-bar r_path per trade, mirroring live `v6_telemetry.r_path`, so the same
offline exit-rule sims (v6.4.5 fixed-cut, Markov early-cut) apply identically — but on
100+ trades instead of ~15.

Modes (env):
  CAPTURE_ALL=1 — sniper/flow gates OFF → full entry universe (~761 trades) for research
  default       — v6.4.5 deployed gates ON (session, ATR, flow) for live-faithful replay

FIDELITY (be honest):
- OHLCV / vol_z / aggression / decile / ATR / price-path exits = klines-based → HIGH.
- cascade_strength = historical Coinalyze daily liq (NOT live Binance forceOrder WS) →
  the ENTRY SET differs from post-migration live, but decile/exit STRUCTURE is faithful.
- Bar-close R; intrabar fills only modeled on the hard stop. Portfolio max_positions cap
  is NOT applied (per-trade expectancy study), only max_per_symbol=1.
"""
from __future__ import annotations

import bisect
import csv
import os
import sqlite3
import sys
import uuid as uuidlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

# v645 entry research replays v6.4.3 runner exits unless overridden.
if os.environ.get("LEGACY_RUNNER_EXITS", "") == "":
    os.environ["LEGACY_RUNNER_EXITS"] = "1"

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import engines.liq_cluster_engine_v5 as eng  # noqa: E402
from engines.liq_cluster_engine_v5 import LiqClusterEngineV5  # noqa: E402
from core.models import Candle  # noqa: E402

# ── Silence the engine's per-bar logging (floods gigabytes otherwise) ──
class _Silent:
    def __getattr__(self, _):
        return lambda *a, **k: None


eng.logger = _Silent()

CAPTURE_ALL = os.environ.get("CAPTURE_ALL", "0") == "1"
ENTRY_THESIS = os.environ.get("ENTRY_THESIS", "v645")  # v645 | exhaustion | squeeze
if CAPTURE_ALL:
    eng.SNIPER_ALLOWED_HOURS = frozenset(range(24))
    eng.SNIPER_MAX_ATR_PCT = 1e9
    eng.SNIPER_MIN_VOL_Z = -1e9
    eng.SNIPER_MIN_CASCADE = -1.0

KLINES_DB = REPO / "backtest_data" / "klines_5m.db"
LIQ_DB = REPO / "backtest_data" / "coinalyze_liq.db"
FORCE_ORDERS_DB = REPO / "backtest_data" / "force_orders.db"
WS_LIQ_DB = REPO / "storage" / "v5_forward_test.db"
CFG_PATH = REPO / "config" / "v5_forward_test.yaml"
OUT_DIR = REPO / "backtest_output"
# coinalyze | binance_force (REST dead) | ws_cache (live WS daily aggregates from forward-test DB)
LIQ_SOURCE = os.environ.get("LIQ_SOURCE", "coinalyze")
# LIQ_INTRADAY=1 → cascade context from completed days + rolling-24h hourly liq
# (live-computable: identical to WS force-order accumulation; no same-day lookahead)
LIQ_INTRADAY = os.environ.get("LIQ_INTRADAY", "0") == "1"

WARMUP_DAYS = 30
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 5, 21, 23, 59, 59, tzinfo=timezone.utc)
WARMUP_END = START + timedelta(days=WARMUP_DAYS)
PATH_CAP = 300  # max bars recorded per trade (covers runner trail resolution)

# Markov P(confirm by bar 10 | decile, bar), forward-filled across even bars
PD1 = {1: .32, 2: .32, 3: .33, 4: .33, 5: .15, 6: .15, 7: .10, 8: .10, 9: .05}
PD26 = {1: .55, 2: .55, 3: .57, 4: .57, 5: .15, 6: .15, 7: .17, 8: .17, 9: .0}
CONFIRM_R = 0.3
GIVEBACK = 0.75


def _trade_row(pos: dict, sym: str, pnl_r: float, reason: str, bars: int) -> dict:
    return {
        "trade_uuid": pos["uuid"], "symbol": sym, "entry_time": pos["etime"],
        "decile": pos["decile"], "aggression": round(pos["agg"], 1),
        "vol_z": round(pos["vol_z"], 4), "cascade_strength": round(pos["casc"], 4),
        "atr_pct": round(pos["atr_pct"], 4), "hour": pos["hour"],
        "ret_5d": round(pos["ret_5d"], 4), "liq_imb": round(pos["liq_imb"], 4),
        "breakout_distance_pct": round(pos["bd_pct"], 4), "imb_z": round(pos["imb_z"], 4),
        "n_confirmations": pos["n_confirms"],
        "conf_breakout": int(pos["conf"]["breakout"]),
        "conf_imb": int(pos["conf"]["imb"]),
        "conf_vol": int(pos["conf"]["vol"]),
        "conf_body": int(pos["conf"]["body"]),
        "conf_impulse": int(pos["conf"]["impulse"]),
        "conf_momentum": int(pos["conf"]["momentum"]),
        "pnl_r": round(pnl_r, 4), "exit_reason": reason, "bars_held": bars,
    }


def klines_symbols() -> set[str]:
    c = sqlite3.connect(str(KLINES_DB))
    out = {r[0] for r in c.execute("select distinct symbol from klines")}
    c.close()
    return out


def load_klines(sym, s_ms, e_ms):
    c = sqlite3.connect(str(KLINES_DB))
    rows = c.execute(
        "select open_time,close_time,open,high,low,close,volume,taker_buy_volume "
        "from klines where symbol=? and open_time>=? and open_time<=? order by open_time",
        (sym, s_ms, e_ms),
    ).fetchall()
    c.close()
    return [
        Candle(
            symbol=sym, timeframe="5m",
            open_time=datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc),
            close_time=datetime.fromtimestamp(r[1] / 1000, tz=timezone.utc),
            open=r[2], high=r[3], low=r[4], close=r[5],
            volume=r[6], taker_buy_volume=r[7], is_closed=True,
        )
        for r in rows
    ]


def load_liq_coinalyze(sym):
    c = sqlite3.connect(str(LIQ_DB))
    rows = c.execute(
        "select timestamp,long_liq,short_liq from liquidation_history where symbol=? order by timestamp",
        (sym,),
    ).fetchall()
    c.close()
    return rows


def load_liq_binance(sym):
    """Daily long/short liq from backfilled allForceOrders (same side mapping as live WS)."""
    c = sqlite3.connect(str(FORCE_ORDERS_DB))
    rows = c.execute(
        "select event_time_ms, side, volume_usd from force_order_events "
        "where symbol=? order by event_time_ms",
        (sym,),
    ).fetchall()
    c.close()
    daily: dict[str, list[float]] = {}
    for t_ms, side, vol in rows:
        d = datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        bucket = daily.setdefault(d, [0.0, 0.0])
        if side == "SELL":
            bucket[0] += vol
        elif side == "BUY":
            bucket[1] += vol
    out = []
    for d in sorted(daily):
        ll, sl = daily[d]
        ts = int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        out.append((ts, ll, sl))
    return out


def load_liq_ws_cache(sym):
    """Daily liq from live paper bot liq_cache (Binance WS aggregates)."""
    c = sqlite3.connect(str(WS_LIQ_DB))
    rows = c.execute(
        "select date, long_liq, short_liq from liq_cache where symbol=? order by date",
        (sym,),
    ).fetchall()
    c.close()
    out = []
    for d, ll, sl in rows:
        ts = int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        out.append((ts, float(ll), float(sl)))
    return out


def load_liq_merged(sym):
    """Coinalyze for warmup; WS cache overrides overlapping dates (live-aligned)."""
    co = {datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"): (ts, ll, sl)
          for ts, ll, sl in load_liq_coinalyze(sym)}
    ws = {datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"): (ts, ll, sl)
          for ts, ll, sl in load_liq_ws_cache(sym)}
    merged = {**co, **ws}
    return [merged[d] for d in sorted(merged)]


def load_liq(sym):
    if LIQ_SOURCE == "binance_force":
        return load_liq_binance(sym)
    if LIQ_SOURCE == "ws_cache":
        return load_liq_ws_cache(sym)
    if LIQ_SOURCE == "ws_merged":
        return load_liq_merged(sym)
    return load_liq_coinalyze(sym)


def load_liq_hourly(sym):
    """Hourly coinalyze liq as (ts_list, long_cumsum, short_cumsum) for rolling-24h sums."""
    c = sqlite3.connect(str(LIQ_DB))
    rows = c.execute(
        "select ts, long_liq, short_liq from liq_hourly where symbol=? order by ts",
        (sym,),
    ).fetchall()
    c.close()
    ts_list, llc, slc = [], [0.0], [0.0]
    for ts, ll, sl in rows:
        ts_list.append(ts)
        llc.append(llc[-1] + ll)
        slc.append(slc[-1] + sl)
    return ts_list, llc, slc


def load_closes(sym):
    c = sqlite3.connect(str(LIQ_DB))
    try:
        rows = {r[0]: r[1] for r in c.execute(
            "select date,close from daily_closes where symbol=? order by date", (sym,)).fetchall()}
    except Exception:
        rows = {}
    c.close()
    return rows


def build_liq_rows(liq, closes, up_to_ts):
    out = []
    for ts, ll, sl in liq:
        if ts > up_to_ts:
            break
        d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        out.append({"date": d, "total_liq": ll + sl, "long_liq": ll, "short_liq": sl,
                    "close": closes.get(d, 0)})
    return out


def run_capture():
    cfg = yaml.safe_load(open(CFG_PATH))
    configured = cfg["symbols"]["tier_a"] + cfg["symbols"]["tier_b"] + cfg["symbols"].get("tier_c_experimental", [])
    avail = klines_symbols()
    override = os.environ.get("SYMBOL_OVERRIDE", "").strip()
    if override:
        syms = [s.strip() for s in override.split(",") if s.strip() in avail]
    else:
        syms = [s for s in configured if s in avail]
    mode = "CAPTURE_ALL" if CAPTURE_ALL else "V6.4.5_DEPLOYED"
    print(f"mode: {mode} | entry: {ENTRY_THESIS} | liq_source: {LIQ_SOURCE}", flush=True)
    print(f"symbols: {len(syms)} usable of {len(configured)} configured", flush=True)

    engine = LiqClusterEngineV5()
    trades, rpath = [], []
    s_ms, e_ms = int(START.timestamp() * 1000), int(END.timestamp() * 1000)
    # LIQ_LAG_DAYS=1 → only completed liq days visible (live-realistic, no same-day lookahead)
    liq_lag_s = int(os.environ.get("LIQ_LAG_DAYS", "0")) * 86400
    if liq_lag_s:
        print(f"liq_lag_days: {liq_lag_s // 86400} (completed days only)", flush=True)

    for si, sym in enumerate(syms):
        kl = load_klines(sym, s_ms, e_ms)
        if not kl:
            continue
        liq, closes = load_liq(sym), load_closes(sym)
        if LIQ_INTRADAY:
            h_ts, h_llc, h_slc = load_liq_hourly(sym)
            last_hour = -1
        st = engine._get_state(sym)
        pos = None
        seen_days = set()

        for i, c in enumerate(kl):
            buf = kl[max(0, i - 199):i + 1]
            day = c.close_time.strftime("%Y-%m-%d")
            if LIQ_INTRADAY:
                cur_hour = int(c.close_time.timestamp()) // 3600
                if cur_hour != last_hour:
                    last_hour = cur_hour
                    day_start = int(c.close_time.replace(
                        hour=0, minute=0, second=0, microsecond=0).timestamp())
                    rows = build_liq_rows(liq, closes, day_start - 1)
                    h_end = cur_hour * 3600
                    lo = bisect.bisect_left(h_ts, h_end - 86400)
                    hi = bisect.bisect_left(h_ts, h_end)
                    rl, rs = h_llc[hi] - h_llc[lo], h_slc[hi] - h_slc[lo]
                    rows.append({"date": "rolling24h", "total_liq": rl + rs,
                                 "long_liq": rl, "short_liq": rs, "close": c.close})
                    engine._cascades[sym] = eng.CascadeTracker()
                    engine.update_daily_liq(sym, rows)
            elif day not in seen_days:
                seen_days.add(day)
                rows = build_liq_rows(liq, closes, int(c.close_time.timestamp()) - liq_lag_s)
                if rows:
                    engine.update_daily_liq(sym, rows)

            if st.in_trade:
                st.bars_held += 1
                res = engine.manage_position(sym, buf)
                ur = (c.close - pos["entry"]) / pos["rpu"] if pos["rpu"] > 0 else 0.0
                rpath.append((pos["uuid"], st.bars_held, round(st.mfe, 5), round(st.mae, 5), round(ur, 5)))
                if res and res.get("action") == "close":
                    trades.append(_trade_row(pos, sym, res["r"], res["reason"], st.bars_held))
                    pos = None
                elif st.bars_held >= PATH_CAP:
                    trades.append(_trade_row(pos, sym, ur, "path_cap", st.bars_held))
                    st.in_trade = False
                    pos = None
            elif c.close_time >= WARMUP_END:
                if ENTRY_THESIS == "v645":
                    sig = engine.evaluate(sym, buf)
                elif ENTRY_THESIS in ("exhaustion", "squeeze"):
                    from backtest_output.v7_entry_theses import evaluate_thesis as _ev7
                    sig = _ev7(ENTRY_THESIS, engine, sym, buf)
                elif ENTRY_THESIS in ("bar3_proof", "liq_spring", "fail_reclaim"):
                    from backtest_output.v8_entry_theses import evaluate_thesis as _ev8
                    sig = _ev8(ENTRY_THESIS, engine, sym, buf)
                elif ENTRY_THESIS in ("dip_absorption", "squeeze_flow"):
                    from backtest_output.v10_entry_theses import evaluate_thesis as _ev10
                    sig = _ev10(ENTRY_THESIS, engine, sym, buf)
                else:
                    from backtest_output.v8_entry_theses import evaluate_thesis as _ev8
                    sig = _ev8(ENTRY_THESIS, engine, sym, buf)
                if sig is not None:
                    entry, stop = sig.entry_price, sig.stop_price
                    rpu = abs(entry - stop)
                    if rpu <= 0:
                        st.in_trade = False
                        continue
                    sd = sig.signal_data
                    atr = sd.get("atr", 0)
                    conf = sd.get("confirmations", {})
                    pos = {
                        "uuid": str(uuidlib.uuid4()), "entry": entry, "rpu": rpu,
                        "etime": c.close_time.isoformat(), "decile": st.decile, "agg": st.aggression_score,
                        "vol_z": sd.get("vol_z", 0.0), "casc": sd.get("cascade_strength", 0.0),
                        "atr_pct": (atr / entry * 100) if entry > 0 else 0.0, "hour": c.close_time.hour,
                        "ret_5d": sd.get("ret_5d", st.ret_5d),
                        "liq_imb": sd.get("liq_direction_imb", st.liq_direction_imb),
                        "bd_pct": sd.get("breakout_distance_pct", 0.0),
                        "imb_z": sd.get("imb_z", 0.0),
                        "n_confirms": sd.get("n_confirmations", 0),
                        "conf": {k: bool(conf.get(k, False)) for k in
                                 ("breakout", "imb", "vol", "body", "impulse", "momentum")},
                    }
                    rpath.append((pos["uuid"], 0, 0.0, 0.0, 0.0))

        if (si + 1) % 10 == 0:
            print(f"  {si + 1}/{len(syms)} symbols | trades={len(trades)}", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    liq_tag = {"binance_force": "_binance", "ws_cache": "_ws", "ws_merged": "_ws_merged"}.get(LIQ_SOURCE, "")
    mode_tag = "_capture_all" if CAPTURE_ALL else "_v645"
    thesis_tag = f"_{ENTRY_THESIS}" if ENTRY_THESIS != "v645" else ""
    out_tag = os.environ.get("OUT_TAG", "")
    tag = f"{mode_tag}{thesis_tag}{liq_tag}{out_tag}"
    trades_path = OUT_DIR / f"v6_bt_trades{tag}.csv"
    rpath_path = OUT_DIR / f"v6_bt_rpath{tag}.csv"
    if not trades:
        print("\nno trades captured", flush=True)
        return [], []
    if os.environ.get("QUIET", "0") == "1":
        print(f"\n{len(trades)} trades captured (quiet mode, no CSV)", flush=True)
        return trades, rpath
    with open(trades_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
        w.writeheader()
        w.writerows(trades)
    with open(rpath_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["trade_uuid", "bar_index", "mfe_so_far", "mae_so_far", "unrealized_r"])
        w.writerows(rpath)
    print(f"\nsaved {len(trades)} trades, {len(rpath)} r_path rows → {trades_path.name}", flush=True)
    return trades, rpath


# ── Offline exit-rule sims (identical to live-data analysis) ──
def _runner_after(path, first_idx):
    a = path[first_idx:]
    for bar, mfe, ur in a:
        stop = max(mfe - GIVEBACK, 0.0)
        if ur <= stop:
            return stop
    return None


def sim_v645(path, baseline, decile):
    window = [(b, m, u) for (b, m, u) in path if b <= 10]
    if not window:
        return baseline
    if not any(m >= CONFIRM_R for (_, m, _) in window):
        return window[-1][2]
    first = next(idx for idx, (_, m, _) in enumerate(path) if m >= CONFIRM_R)
    r = _runner_after(path, first)
    return r if r is not None else baseline


def sim_markov(path, baseline, decile):
    tbl = PD1 if decile == 1 else (PD26 if decile in (2, 6) else None)
    for idx, (bar, mfe, ur) in enumerate(path):
        if bar < 1:
            continue
        if mfe >= CONFIRM_R:
            first = next(i for i, (_, m, _) in enumerate(path) if m >= CONFIRM_R)
            r = _runner_after(path, first)
            return r if r is not None else baseline
        if tbl is not None and bar in tbl and tbl[bar] < 0.15:
            return ur
        if bar >= 10:
            return ur
    return baseline


def analyze(trades, rpath):
    import pandas as pd
    td = pd.DataFrame(trades)
    paths = {}
    for u, b, m, mae, ur in rpath:
        paths.setdefault(u, []).append((int(b), float(m), float(ur)))
    for u in paths:
        paths[u].sort()

    td["v645"] = [sim_v645(paths.get(u, []), b, d) for u, b, d in zip(td.trade_uuid, td.pnl_r, td.decile)]
    td["markov"] = [sim_markov(paths.get(u, []), b, d) for u, b, d in zip(td.trade_uuid, td.pnl_r, td.decile)]
    td["d26"] = td.decile.isin([2, 6])
    td["flow"] = (td.vol_z > 0) & (td.cascade_strength >= 1.38)
    td["sniper"] = (~td.symbol.isin({"AVAXUSDT", "HYPEUSDT"})) & (td.hour >= 14) & (td.hour < 24) & (td.atr_pct < 0.65)

    def pr(label, m, col):
        if len(m) == 0:
            print(f"{label:44s} N=0")
            return
        print(f"{label:44s} N={len(m):4d} totR={m[col].sum():+8.2f} avg={m[col].mean():+.3f} WR={(m[col] > 0).mean():.0%}")

    print(f"\n{'='*70}\nLARGE-SAMPLE BACKTEST (Jan31–May21, base entries)  N={len(td)}\n{'='*70}")
    print("\n--- baseline exits ---")
    pr("ALL, recorded(v6.4.5 engine)", td, "pnl_r")
    pr("ALL, v6.4.5 offline (sanity)", td, "v645")
    pr("ALL, markov offline", td, "markov")
    print("\n--- decile filter (the 'meat') ---")
    pr("D2+D6, v6.4.5", td[td.d26], "v645")
    pr("D2+D6, markov", td[td.d26], "markov")
    pr("D1 only, v6.4.5", td[td.decile == 1], "v645")
    print("\n--- sniper stack (session+ATR) ---")
    pr("sniper, all deciles, v6.4.5", td[td.sniper], "v645")
    pr("sniper+flow, v6.4.5 (=deployed)", td[td.sniper & td.flow], "v645")
    pr("sniper+D2D6, v6.4.5", td[td.sniper & td.d26], "v645")
    pr("sniper+D2D6, markov", td[td.sniper & td.d26], "markov")
    print("\n--- per-decile (v6.4.5 exit) ---")
    for d in sorted(td.decile.dropna().unique()):
        pr(f"D{int(d)}", td[td.decile == d], "v645")


if __name__ == "__main__":
    tr, rp = run_capture()
    try:
        analyze(tr, rp)
    except Exception as e:
        print(f"analysis error (CSVs saved, run offline): {e}")
