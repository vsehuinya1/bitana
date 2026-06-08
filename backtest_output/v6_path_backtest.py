"""
V6 path backtester — large-sample expectancy for entry/exit research.

Replays the REAL engine (`evaluate` + `manage_position`) over historical 5m klines and
records a bar-by-bar r_path per trade, mirroring live `v6_telemetry.r_path`, so the same
offline exit-rule sims (v6.4.5 fixed-cut, Markov early-cut) apply identically — but on
100+ trades instead of ~15.

Sniper entry gates (session 14-24, ATR<0.65, flow vol_z/cascade) are DISABLED at entry so
the full base-strategy entry universe is captured; those filters are applied OFFLINE for
slicing. Decile filter (D1/D2 need imb|vol), BD gate, 4/6 confirms, cascade gate stay ON.

FIDELITY (be honest):
- OHLCV / vol_z / aggression / decile / ATR / price-path exits = klines-based → HIGH.
- cascade_strength = historical Coinalyze daily liq (NOT live Binance forceOrder WS) →
  the ENTRY SET differs from post-migration live, but decile/exit STRUCTURE is faithful.
- Bar-close R; intrabar fills only modeled on the hard stop. Portfolio max_positions cap
  is NOT applied (per-trade expectancy study), only max_per_symbol=1.
"""
from __future__ import annotations

import csv
import sqlite3
import sys
import uuid as uuidlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

import engines.liq_cluster_engine_v5 as eng  # noqa: E402
from engines.liq_cluster_engine_v5 import LiqClusterEngineV5  # noqa: E402
from core.models import Candle  # noqa: E402

# ── Disable sniper entry gates for capture (applied offline instead) ──
eng.SNIPER_ALLOWED_HOURS = frozenset(range(24))
eng.SNIPER_MAX_ATR_PCT = 1e9
eng.SNIPER_MIN_VOL_Z = -1e9
eng.SNIPER_MIN_CASCADE = -1.0

KLINES_DB = Path("/root/bitana/backtest_data/klines_5m.db")
LIQ_DB = Path("/root/bitana/backtest_data/coinalyze_liq.db")
CFG_PATH = Path("/root/bitana/config/v5_forward_test.yaml")
OUT_DIR = Path("/root/bitana/backtest_output")

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


def load_liq(sym):
    c = sqlite3.connect(str(LIQ_DB))
    rows = c.execute(
        "select timestamp,long_liq,short_liq from liquidation_history where symbol=? order by timestamp",
        (sym,),
    ).fetchall()
    c.close()
    return rows


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
    syms = [s for s in configured if s in avail]
    print(f"symbols: {len(syms)} usable of {len(configured)} configured", flush=True)

    engine = LiqClusterEngineV5()
    trades, rpath = [], []
    s_ms, e_ms = int(START.timestamp() * 1000), int(END.timestamp() * 1000)

    for si, sym in enumerate(syms):
        kl = load_klines(sym, s_ms, e_ms)
        if not kl:
            continue
        liq, closes = load_liq(sym), load_closes(sym)
        st = engine._get_state(sym)
        pos = None
        seen_days = set()

        for i, c in enumerate(kl):
            buf = kl[max(0, i - 199):i + 1]
            day = c.close_time.strftime("%Y-%m-%d")
            if day not in seen_days:
                seen_days.add(day)
                rows = build_liq_rows(liq, closes, int(c.close_time.timestamp()))
                if rows:
                    engine.update_daily_liq(sym, rows)

            if st.in_trade:
                st.bars_held += 1
                res = engine.manage_position(sym, buf)
                ur = (c.close - pos["entry"]) / pos["rpu"] if pos["rpu"] > 0 else 0.0
                rpath.append((pos["uuid"], st.bars_held, round(st.mfe, 5), round(st.mae, 5), round(ur, 5)))
                if res and res.get("action") == "close":
                    trades.append({
                        "trade_uuid": pos["uuid"], "symbol": sym, "entry_time": pos["etime"],
                        "decile": pos["decile"], "aggression": round(pos["agg"], 1),
                        "vol_z": round(pos["vol_z"], 4), "cascade_strength": round(pos["casc"], 4),
                        "atr_pct": round(pos["atr_pct"], 4), "hour": pos["hour"],
                        "pnl_r": round(res["r"], 4), "exit_reason": res["reason"], "bars_held": st.bars_held,
                    })
                    pos = None
                elif st.bars_held >= PATH_CAP:
                    trades.append({
                        "trade_uuid": pos["uuid"], "symbol": sym, "entry_time": pos["etime"],
                        "decile": pos["decile"], "aggression": round(pos["agg"], 1),
                        "vol_z": round(pos["vol_z"], 4), "cascade_strength": round(pos["casc"], 4),
                        "atr_pct": round(pos["atr_pct"], 4), "hour": pos["hour"],
                        "pnl_r": round(ur, 4), "exit_reason": "path_cap", "bars_held": st.bars_held,
                    })
                    st.in_trade = False
                    pos = None
            elif c.close_time >= WARMUP_END:
                sig = engine.evaluate(sym, buf)
                if sig is not None:
                    entry, stop = sig.entry_price, sig.stop_price
                    rpu = abs(entry - stop)
                    if rpu <= 0:
                        st.in_trade = False
                        continue
                    sd = sig.signal_data
                    atr = sd.get("atr", 0)
                    pos = {
                        "uuid": str(uuidlib.uuid4()), "entry": entry, "rpu": rpu,
                        "etime": c.close_time.isoformat(), "decile": st.decile, "agg": st.aggression_score,
                        "vol_z": sd.get("vol_z", 0.0), "casc": sd.get("cascade_strength", 0.0),
                        "atr_pct": (atr / entry * 100) if entry > 0 else 0.0, "hour": c.close_time.hour,
                    }
                    rpath.append((pos["uuid"], 0, 0.0, 0.0, 0.0))

        if (si + 1) % 10 == 0:
            print(f"  {si + 1}/{len(syms)} symbols | trades={len(trades)}", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "v6_bt_trades.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
        w.writeheader()
        w.writerows(trades)
    with open(OUT_DIR / "v6_bt_rpath.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["trade_uuid", "bar_index", "mfe_so_far", "mae_so_far", "unrealized_r"])
        w.writerows(rpath)
    print(f"\nsaved {len(trades)} trades, {len(rpath)} r_path rows", flush=True)
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
