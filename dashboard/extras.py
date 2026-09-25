"""Dashboard v3 data (2026-09-25): honest per-arm performance, Today strip, regime health, gate funnel,
position enrichment, ops health, research board.

Everything here is read-only and failure-isolated: each block catches its own errors, so one failing source
(Binance unreachable, pm2 missing, log rotated) degrades that panel only. Sources:
- live DB via the dashboard's existing read-only connection (trades, positions, risk_state)
- the live yaml (arm definitions, to tell current-era arms from retired ones)
- the bot's /metrics payload the dashboard already proxies (gate_reasons, arms, regime)
- the bot log tail (hourly gate summaries)
- public Binance REST (BTC 4h klines for ADX; mark prices for open positions)
- file mtimes only for the shadow / force-order DBs (CLAUDE.md: never open them from here)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE_YAML = ROOT / "config" / "live_burst_ny_asia.yaml"
BOT_LOG = ROOT / "logs" / "bitana-live-burst.log"
SURVIVAL_JSON = Path(__file__).resolve().parent / "regime_survival.json"
BOARD_JSON = Path(__file__).resolve().parent / "research_board.json"
HERMES_JOBS = Path("/root/.hermes/cron/jobs.json")
UNITS = ["bitana-live-burst-follow", "bitana-v5-paper", "bitana-dashboard", "hermes-gateway"]
FRESHNESS = {
    "shadow writer": ROOT / "storage" / "signal_shadow.db-wal",
    "force-order feed": ROOT / "storage" / "force_orders_paper.db-wal",
    "live bot log": BOT_LOG,
    "paper log": ROOT / "logs" / "v5_forward_test.log",
}
SESSION_END_H = {"asia": 8, "london": 14, "ny": 22, "late": 24}
_cache: dict = {}


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception as e:  # noqa: BLE001 - a panel must never break the page
        val = {"error": f"{type(e).__name__}: {e}"[:200]}
    _cache[key] = (time.time(), val)
    return val


def _http_json(url, timeout=6):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


# ---------------------------------------------------------------- arms (current vs retired)
def _arm_defs():
    import yaml
    with open(LIVE_YAML) as fh:
        bf = (yaml.safe_load(fh) or {}).get("burst_follow", {})
    defs = {}
    for arm, rule in (bf.get("session_rules") or {}).items():
        stops = {float(rule.get("stop_atr", 10.0))} | {float(v) for v in (rule.get("regime_stop_atr") or {}).values()}
        defs[rule.get("shadow_strategy")] = {"arm": arm, "stops": stops}
    return defs


def _arm_key(t, defs):
    """(group label, is_current) for one trade row."""
    if t["engine"] != "LIQ_BURST_FOLLOW":
        return f"{t['engine'].lower()} (not live)", False
    sd = t["sd"]
    strat, stop = sd.get("shadow_strategy") or "?", float(sd.get("stop_atr") or 0)
    d = defs.get(strat)
    if d and stop in d["stops"]:
        return d["arm"], True
    if d:
        return f"{d['arm']} · SL{stop:g} era (not live)", False
    return f"{strat} (not live)", False


def performance(conn):
    rows = [dict(r) for r in conn.execute(
        "SELECT timestamp, engine, symbol, side, pnl_r, pnl_usd, hold_time_s, exit_reason, signal_data FROM trades "
        "ORDER BY timestamp ASC")]
    defs = _arm_defs()
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    groups = defaultdict(list)
    for t in rows:
        try:
            t["sd"] = json.loads(t["signal_data"] or "{}")
        except ValueError:
            t["sd"] = {}
        t["exit"] = datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00"))
        t["group"], t["current"] = _arm_key(t, defs)
        groups[(t["group"], t["current"])].append(t)

    def stats(ts):
        rs = [x["pnl_r"] for x in ts]
        days = {x["exit"].strftime("%Y-%m-%d") for x in ts}
        wins, losses = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
        cut7, cut30 = now - timedelta(days=7), now - timedelta(days=30)
        return {"n": len(rs), "days": len(days), "sum_r": round(sum(rs), 2), "e_r": round(sum(rs) / len(rs), 4),
                "pf": round(wins / losses, 2) if losses > 0 else None, "wr": round(100 * sum(r > 0 for r in rs) / len(rs), 1),
                "r_7d": round(sum(x["pnl_r"] for x in ts if x["exit"] >= cut7), 2),
                "r_30d": round(sum(x["pnl_r"] for x in ts if x["exit"] >= cut30), 2),
                "usd": round(sum(x["pnl_usd"] for x in ts), 2),
                "first": min(days), "last": max(days)}

    arms = [{"group": g, "current": cur, **stats(ts)} for (g, cur), ts in groups.items()]
    arms.sort(key=lambda a: (a["current"], a["last"], a["n"]), reverse=True)
    cur_trades = [t for t in rows if t["current"]]
    curves, cum = {}, defaultdict(float)
    for t in cur_trades:
        cum[t["group"]] += t["pnl_r"]
        curves.setdefault(t["group"], []).append({"t": t["timestamp"], "v": round(cum[t["group"]], 3)})
    total, peak, under = 0.0, 0.0, []
    for t in cur_trades:
        total += t["pnl_r"]
        peak = max(peak, total)
        under.append({"t": t["timestamp"], "v": round(total - peak, 3)})
    tod = [t for t in rows if t["exit"].strftime("%Y-%m-%d") == today]
    return {
        "arms": arms,
        "current_total": stats(cur_trades) if cur_trades else None,
        "curves": curves,
        "underwater": under,
        "max_dd_r": round(min((u["v"] for u in under), default=0.0), 2),
        "today": {"legs": len(tod), "r": round(sum(t["pnl_r"] for t in tod), 2),
                  "usd": round(sum(t["pnl_usd"] for t in tod), 2),
                  "wins": sum(t["pnl_r"] > 0 for t in tod)},
        "trade_meta": {t["timestamp"]: {"arm": t["group"], "current": t["current"],
                                        "hour": (t["exit"] - timedelta(seconds=t["hold_time_s"] or 0)).hour,
                                        "hold_min": round((t["hold_time_s"] or 0) / 60)} for t in rows[-60:]},
    }


# ---------------------------------------------------------------- regime health
def _regime_series():
    sys.path.insert(0, str(ROOT))
    os.environ.pop("API_FOOTBALL_KEY", None)
    from core.models import Candle
    from engines.btc_regime import compute_regime_age_bars, compute_regime_snapshot
    raw = _http_json("https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=4h&limit=300")
    raw = raw[:-1]  # drop the forming bar, as the live refresh does
    C = [Candle(symbol="BTCUSDT", timeframe="4h", open_time=datetime.fromtimestamp(k[0] / 1000, timezone.utc),
                close_time=datetime.fromtimestamp(k[6] / 1000, timezone.utc), open=float(k[1]), high=float(k[2]),
                low=float(k[3]), close=float(k[4]), volume=float(k[5])) for k in raw]
    pts = []
    for i in range(len(C) - 30, len(C)):
        s = compute_regime_snapshot(C[i - 248:i + 1])
        pts.append({"t": C[i].close_time.isoformat(), "adx": float(s.adx), "raw": s.state,
                    "dist": float(s.distance_from_ema_pct) if s.distance_from_ema_pct is not None else None})
    return {"points": pts, "age_bars": compute_regime_age_bars(C[-249:])}


def regime_health(metrics):
    series = _cached("regime_series", 300, _regime_series)
    if "error" in series:
        return series
    pts = series["points"]
    adx, d3 = pts[-1]["adx"], pts[-1]["adx"] - pts[-4]["adx"]
    state = (metrics or {}).get("btc_regime") or pts[-1]["raw"]
    dist = pts[-1]["dist"]
    out = {"state": state, "adx": round(float(adx), 2), "d3": round(float(d3), 2),
           "dist": round(float(dist), 3) if dist is not None else None, "age_bars": series["age_bars"],
           "exit_below": 24.5, "enter_at": 25.5, "series": [{"t": p["t"], "adx": round(p["adx"], 2)} for p in pts]}
    if state in ("bull", "bear") and SURVIVAL_JSON.exists():
        surv = json.loads(SURVIVAL_JSON.read_text())
        band = next((b for b in surv["bands"] if float(b.split("-")[0]) <= adx < (999 if b.endswith("+") else float(b.split("-")[1]))), None)
        slope = "falling" if d3 <= -4 else ("rising" if d3 >= 4 else "flat")
        cell = surv["table"].get(f"{state}|{band}|{slope}")
        now = datetime.now(timezone.utc)
        odds = []
        for sess, end_h in SESSION_END_H.items():
            end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=end_h)
            if end <= now:
                continue
            # 4h closes (00,04,...,20 UTC) strictly between now and the session end
            k = sum(1 for hh in range(0, 24, 4) if now < now.replace(hour=hh, minute=0, second=0, microsecond=0) < end)
            p = 1.0 if k == 0 else (cell["p"][min(k, surv["kmax"]) - 1] if cell else None)
            odds.append({"session": sess, "closes_left": k, "p_hold": p})
        out.update({"bucket": f"{band} · ADX {slope}", "bucket_n": cell["n"] if cell else 0, "odds": odds,
                    "table_built": surv.get("built")})
    return out


# ---------------------------------------------------------------- why-no-trade funnel
_gate_base: dict = {}


def _log_summaries():
    if not BOT_LOG.exists():
        return []
    size = BOT_LOG.stat().st_size
    with open(BOT_LOG, "rb") as fh:
        fh.seek(max(0, size - 3_000_000))
        tail = fh.read().decode("utf-8", "replace").splitlines()
    out = []
    for line in tail:
        if '"Burst gate hourly summary"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        by = defaultdict(dict)
        for k, v in (d.get("counts") or {}).items():
            sess, _, reason = k.partition(":")
            by[sess][reason] = v
        out.append({"hour": d.get("hour"), "by_session": by})
    return out[-3:]


def gate_funnel(metrics):
    cum = ((metrics or {}).get("gate_reasons")) or {}
    now = datetime.now(timezone.utc)
    hk = now.strftime("%Y-%m-%dT%H")
    base = _gate_base.get(hk)
    flat = {(s, r): v for s, rs in cum.items() for r, v in rs.items()}
    if base is None or any(flat.get(k, 0) < v for k, v in base["counts"].items()):  # new hour, or bot restarted
        base = {"counts": dict(flat), "since": now.strftime("%H:%M")}
        _gate_base.clear()
        _gate_base[hk] = base
    delta = defaultdict(dict)
    for (s, r), v in flat.items():
        d = v - base["counts"].get((s, r), 0)
        if d:
            delta[s][r] = d
    return {"hour": hk, "since": base["since"], "current": delta,
            "recent": _cached("log_summaries", 60, _log_summaries)}


# ---------------------------------------------------------------- positions
def enrich_positions(positions):
    out = []
    for p in positions:
        q = dict(p)
        try:
            sd = json.loads(p.get("signal_data") or "{}")
            mark = float(_cached(f"px:{p['symbol']}", 10, lambda s=p["symbol"]: _http_json(
                f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={s}"))["price"])
            entry, stop0 = float(p["entry_price"]), float(p["initial_stop"] or p["stop_price"])
            sign = 1 if p["side"] == "LONG" else -1
            risk = abs(entry - stop0)
            t0 = datetime.fromisoformat(str(p["entry_time"]).replace("Z", "+00:00"))
            if t0.tzinfo is None:
                t0 = t0.replace(tzinfo=timezone.utc)
            left = int(sd.get("time_bars") or 0) * 300 - (datetime.now(timezone.utc) - t0).total_seconds()
            tp_atr, stop_atr = sd.get("tp_atr"), sd.get("stop_atr")
            q.update({"mark": mark, "u_r": round(sign * (mark - entry) / risk, 3) if risk else None,
                      "time_left_s": max(0, int(left)), "arm": sd.get("session"), "strategy": sd.get("shadow_strategy"),
                      "tp_r": round(tp_atr / stop_atr, 3) if tp_atr and stop_atr and tp_atr < 900 else None})
        except Exception as e:  # noqa: BLE001
            q["enrich_error"] = str(e)[:120]
        out.append(q)
    return out


# ---------------------------------------------------------------- risk context
def risk_context(conn, positions):
    import yaml
    with open(LIVE_YAML) as fh:
        y = yaml.safe_load(fh) or {}
    rs = dict(conn.execute("SELECT * FROM risk_state LIMIT 1").fetchone() or {})
    eq = float(rs.get("current_equity") or 0)
    open_risk = sum(abs(float(p["entry_price"]) - float(p["initial_stop"] or p["stop_price"])) * float(p["quantity"])
                    for p in positions) if positions else 0.0
    risk_cfg, brakes, port = y.get("risk", {}), y.get("brakes", {}), y.get("portfolio", {})
    dd = float(rs.get("current_drawdown_pct") or 0)
    return {"reduced_mode": dd > float(risk_cfg.get("drawdown_reduce_threshold", 0.15)),
            "reduce_at": risk_cfg.get("drawdown_reduce_threshold"), "restore_at": risk_cfg.get("drawdown_restore_threshold"),
            "pause_at": brakes.get("equity_pause_drawdown"), "shutdown_at": brakes.get("equity_shutdown_drawdown"),
            "dd": dd, "open_risk_pct": round(100 * open_risk / eq, 2) if eq else None,
            "cluster_budget_pct": port.get("max_cluster_risk_pct"), "max_positions": port.get("max_concurrent_positions"),
            "risk_pct_default": risk_cfg.get("default_risk_pct")}


# ---------------------------------------------------------------- ops health
def _ops():
    units = {}
    for u in UNITS:
        try:
            units[u] = subprocess.run(["systemctl", "is-active", u], capture_output=True, text=True, timeout=3).stdout.strip()
        except Exception:  # noqa: BLE001
            units[u] = "unknown"
    pm2 = {}
    try:
        r = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=5)
        pm2 = {p["name"]: p["pm2_env"]["status"] for p in json.loads(r.stdout or "[]")}
    except Exception:  # noqa: BLE001
        pm2 = {"pm2": "unavailable"}
    fresh = {k: (int(time.time() - p.stat().st_mtime) if p.exists() else None) for k, p in FRESHNESS.items()}
    du = shutil.disk_usage("/")
    jobs = []
    if HERMES_JOBS.exists():
        j = json.loads(HERMES_JOBS.read_text())
        items = j if isinstance(j, list) else (j.get("jobs") or j)
        items = items.values() if isinstance(items, dict) else items
        for x in items:
            if isinstance(x, dict):
                jobs.append({k: x.get(k) for k in ("name", "enabled", "last_run_at", "last_status", "next_run_at", "last_error")})
    return {"units": units, "pm2": pm2, "freshness_s": fresh, "disk_pct": round(100 * du.used / du.total, 1),
            "disk_free_gb": round(du.free / 1e9, 1), "cron": jobs}


def ops_health():
    return _cached("ops", 30, _ops)


def research_board():
    if not BOARD_JSON.exists():
        return None
    return json.loads(BOARD_JSON.read_text())


# ---------------------------------------------------------------- entry point used by server.py
def collect(conn, metrics, positions):
    blocks = {
        "performance": lambda: _cached("perf", 15, lambda: performance(conn)),
        "regime_health": lambda: regime_health(metrics),
        "gate_funnel": lambda: gate_funnel(metrics),
        "positions": lambda: enrich_positions(positions),
        "risk_context": lambda: risk_context(conn, positions),
        "ops": ops_health,
        "research_board": research_board,
    }
    out = {}
    for k, fn in blocks.items():
        try:
            out[k] = fn()
        except Exception as e:  # noqa: BLE001
            out[k] = {"error": f"{type(e).__name__}: {e}"[:200]}
    return out
