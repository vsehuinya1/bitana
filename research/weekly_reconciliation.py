#!/usr/bin/env python3
"""
Weekly reconciliation & calibration audit (measurement-only, wires nothing).
Usage: python3 research/weekly_reconciliation.py [--days 7]

Modules:
  1. fill_reconciliation  live trades <-> shadow rows via (strategy, symbol, side, cluster_bucket)
  2. config_drift         live trades vs live config (regime allowed? stop_atr match?)
  3. stop_calibration     live |entry-initial_stop|/entry distribution vs stop_atr*entry_atr
  4. cost_drag            commission+funding+slippage vs gross pnl, by engine
  5. capacity             brake/risk/system state + journal reject counts
  6. regime_flip_lag      shadow expectancy by btc_regime_age_bars bucket (selector-lag cost)
"""
import sqlite3, json, sys, subprocess, statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

LIVE_DB = "/root/bitana/data/bitana-live-burst.db"
SHADOW_DB = "/root/bitana/storage/signal_shadow.db"
LIVE_CONFIG = "/root/bitana/config/live_burst_ny_asia.yaml"
LIVE_BOOK_STRATS = {"burst_follow", "ny_flush_buy_4h", "asia_pump_short_4h"}

def q(conn, sql, args=()):
    cur = conn.cursor()
    cur.execute(sql, args)
    return cur.fetchall()

def fix_ts(s):
    if s is None:
        return None
    s = str(s).strip().replace("Z", "+00:00")
    if len(s) == 19:
        s += "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None

def cb_norm(cb):
    """normalize cluster_bucket to minute precision ISO string."""
    if cb is None:
        return None
    t = fix_ts(cb)
    return t.strftime("%Y-%m-%dT%H:%M") if t else str(cb)[:16]

def parse_sd(sd):
    try:
        return json.loads(sd) if sd else {}
    except Exception:
        return {}

def med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 4) if xs else None

# ---------------------------------------------------------------- module 1
def fill_reconciliation(live, sh, days):
    print(f"\n=== 1. FILL RECONCILIATION (live<->shadow, {days}d) ===")
    live_rows = q(live, """
        SELECT timestamp, symbol, side, ROUND(pnl_r,3), engine, signal_data
        FROM trades WHERE exit_price IS NOT NULL AND date(timestamp) >= ?
    """, ((datetime.utcnow()-timedelta(days=days)).strftime("%Y-%m-%d"),))
    sh_rows = q(sh, """
        SELECT symbol, side, entry_time, strategy, pnl_atr, would_live_accept, cluster_bucket
        FROM shadow_trades
        WHERE status='closed' AND date(entry_time) >= ?
    """, ((datetime.now(timezone.utc)-timedelta(days=days)).strftime("%Y-%m-%d"),))
    sh_keys = set()
    sh_fuzzy = defaultdict(list)
    for sym, side, et, strat, pa, wla, cb in sh_rows:
        sh_keys.add((strat, sym, side, cb_norm(cb)))
        t = fix_ts(et)
        if t:
            sh_fuzzy[(strat, sym, side)].append(t)
    live_keys = set()
    for ts, sym, side, pr, eng, sd in live_rows:
        d = parse_sd(sd)
        strat, cb = d.get("shadow_strategy"), d.get("cluster_bucket")
        if strat:
            live_keys.add((strat, sym, side, cb_norm(cb)))
    matched, fuzzy, unmatched = 0, 0, []
    for ts, sym, side, pr, eng, sd in live_rows:
        d = parse_sd(sd)
        strat, cb = d.get("shadow_strategy"), d.get("cluster_bucket")
        key = (strat, sym, side, cb_norm(cb))
        if strat and key in sh_keys:
            matched += 1
        else:
            # fallback: same strategy+sym+side within 4h (bucket-grid drift tolerance)
            t = fix_ts(ts)
            near = [x for x in sh_fuzzy.get((strat, sym, side), []) if t and abs((t - x).total_seconds()) <= 4*3600]
            if near:
                fuzzy += 1
            else:
                unmatched.append((ts[:16], sym, side, pr, strat))
    n_live = len(live_rows)
    print(f"live closed: {n_live} | exact-bucket: {matched} | fuzzy<=4h: {fuzzy} | "
          f"no-shadow-counterpart: {len(unmatched)}")
    for u in unmatched[:8]:
        print("  LIVE-NO-MATCH:", u)
    # shadow-accepted live-book LONG fills with NO live counterpart (live universe only)
    lu_rows = q(live, "SELECT DISTINCT symbol FROM trades")
    live_syms = {r[0] for r in lu_rows}
    shadow_only = []
    for sym, side, et, strat, pa, wla, cb in sh_rows:
        if not wla or strat not in LIVE_BOOK_STRATS or side != "LONG" or sym not in live_syms:
            continue
        if (strat, sym, side, cb_norm(cb)) not in live_keys:
            shadow_only.append((et[:16], sym, strat, round(pa, 2) if pa is not None else None))
    print(f"shadow-accepted live-book LONG fills w/o live counterpart: {len(shadow_only)}")
    per_strat = defaultdict(int)
    for s in shadow_only:
        per_strat[s[2]] += 1
    print("  shadow-only by strategy: " + ", ".join(f"{k}={v}" for k, v in sorted(per_strat.items())))
    for s in shadow_only[:10]:
        print("  SHADOW-ONLY:", s)

# ---------------------------------------------------------------- module 2
def config_drift(live, days):
    print(f"\n=== 2. CONFIG DRIFT (live trades vs {LIVE_CONFIG.split('/')[-1]}) ===")
    try:
        import yaml
        cfg = yaml.safe_load(open(LIVE_CONFIG))
    except Exception as e:
        print(f"  [SKIP] config load failed: {e}")
        return
    blocks = {}
    def _collect(node, name):
        if isinstance(node, dict):
            if "allowed_btc_regimes" in node:
                blocks[str(name).lower()] = node
            for k, v in node.items():
                _collect(v, k if isinstance(v, dict) and "allowed_btc_regimes" in v else name)
    _collect(cfg, "?")
    allowed, stops = {}, {}
    for name, blk in blocks.items():
        allowed[name] = set(blk["allowed_btc_regimes"])
        rsa = blk.get("regime_stop_atr")
        if isinstance(rsa, dict):
            stops[name] = {k: float(v) for k, v in rsa.items()}
    print(f"parsed config: session blocks={sorted(allowed)}")
    if not allowed:
        print("  [WARN] no allowed_btc_regimes blocks parsed — check config structure")
    rows = q(live, """
        SELECT timestamp, symbol, signal_data FROM trades
        WHERE exit_price IS NOT NULL AND date(timestamp) >= ?
    """, ((datetime.utcnow()-timedelta(days=days)).strftime("%Y-%m-%d"),))
    bad, no_sd, ok = [], 0, 0
    for ts, sym, sd in [(r[0], r[1], r[2]) for r in rows]:
        d = parse_sd(sd)
        if not d:
            continue
        sess = (d.get("session") or "").lower()
        reg = d.get("btc_trend_state")
        satr = d.get("stop_atr")
        v = []
        if sess in allowed and reg and reg not in allowed[sess]:
            v.append(f"regime {reg} not in {sess} allowed {sorted(allowed[sess])}")
        if sess in stops and reg and satr is not None:
            exp = stops[sess].get(reg)
            if exp is not None and abs(float(satr) - exp) > 1e-6:
                v.append(f"stop_atr {satr} != config {exp}")
        if v:
            bad.append((ts[:16], sym, "; ".join(v)))
        else:
            ok += 1
    print(f"checked {len(rows)}: ok={ok} violations={len(bad)} (session map: {sorted(allowed)})")
    for b in bad[:5]:
        print("  VIOLATION:", b)

# ---------------------------------------------------------------- module 3
def stop_calibration(live, days):
    print(f"\n=== 3. STOP/R CALIBRATION ({days}d) ===")
    rows = q(live, """
        SELECT symbol, side, entry_price, initial_stop, exit_price, pnl_r, signal_data
        FROM trades WHERE exit_price IS NOT NULL AND date(timestamp) >= ?
    """, ((datetime.utcnow()-timedelta(days=days)).strftime("%Y-%m-%d"),))
    stop_pcts, r_errs = [], []
    for sym, side, ep, istop, xp, pr, sd in rows:
        if not ep or not istop:
            continue
        d = abs(istop - ep) / ep
        stop_pcts.append(d)
        d2 = parse_sd(sd)
        satr, eatr = d2.get("stop_atr"), d2.get("entry_atr")
        if satr and eatr and ep:
            implied = satr * eatr / ep
            if implied > 0:
                r_errs.append(abs(d - implied) / implied)
    if stop_pcts:
        sp = sorted(stop_pcts)
        print(f"stop dist %: median {100*med(stop_pcts):.2f}  p10 {100*sp[len(sp)//10]:.2f}  "
              f"p90 {100*sp[-max(1,len(sp)//10)]:.2f}")
    if r_errs:
        print(f"stop-distance vs stop_atr*entry_atr mismatch >20%: {sum(1 for e in r_errs if e > 0.2)}/{len(r_errs)}")

# ---------------------------------------------------------------- module 4
def cost_drag(live, days):
    print(f"\n=== 4. COST DRAG ({days}d + all-time) ===")
    cond_7d = f"date(timestamp) >= '{(datetime.utcnow()-timedelta(days=days)).strftime('%Y-%m-%d')}'"
    for label, cond in [(f"{days}d", cond_7d), ("all-time", "1=1")]:
        rows = q(live, f"""
            SELECT engine, commission, funding_fees, slippage_est,
                   COALESCE(entry_price,0)*COALESCE(quantity,0), ABS(pnl_usd)
            FROM trades WHERE exit_price IS NOT NULL AND {cond}
        """)
        agg = {}
        for eng, com, fund, slip, notion, apnl in rows:
            a = agg.setdefault(eng, [0, 0.0, 0.0, 0.0, 0.0])
            a[0] += 1
            a[1] += com or 0
            a[2] += fund or 0
            a[3] += (slip or 0) / 10000.0 * (notion or 0)   # per-row bps->usd
            a[4] += apnl or 0
        for eng, (n, com, fund, slip_usd, gross) in agg.items():
            tot = com + fund
            pct = 100.0 * tot / gross if gross else 0
            print(f"  [{label}] {eng:<18} n={n:<4} com={com:.2f} fund={fund} "
                  f"slip~{slip_usd:.2f}usd gross|pnl|={gross:.2f} -> cash-drag {pct:.1f}% (slip sep.)")

# ---------------------------------------------------------------- module 5
def capacity(live, days):
    print(f"\n=== 5. CAPACITY / BRAKE ({days}d) ===")
    for tbl in ("brake_state", "risk_state", "system_state"):
        try:
            rows = q(live, f"SELECT * FROM {tbl} ORDER BY rowid DESC LIMIT 1")
            if rows:
                cur = live.execute(f"SELECT * FROM {tbl} LIMIT 1")
                cols = [d[0] for d in cur.description]
                print(f"  {tbl}: " + ", ".join(f"{c}={v}" for c, v in zip(cols, rows[0])))
            else:
                print(f"  {tbl}: empty")
        except Exception as e:
            print(f"  {tbl}: {e}")
    try:
        out = subprocess.run(
            ["journalctl", "-u", "bitana-live-burst-follow.service", "--since", f"{days} days ago", "--no-pager"],
            capture_output=True, text=True, timeout=30).stdout
        cap_rej = len([l for l in out.splitlines() if "cap" in out.lower() and "full" in l.lower()])
        rej = len([l for l in out.splitlines() if "reject" in l.lower() or "skip" in l.lower()])
        brake = len([l for l in out.splitlines() if "brake" in l.lower()])
        print(f"journal {days}d: cap-full lines={cap_rej} reject/skip lines={rej} brake lines={brake}")
    except Exception as e:
        print(f"  journal: [SKIP] {e}")

# ---------------------------------------------------------------- module 6
def regime_flip_lag(sh, days=14):
    print(f"\n=== 6. REGIME-FLIP LAG COST (shadow, {days}d, closed) ===")
    rows = q(sh, """
        SELECT btc_regime_age_bars, pnl_atr
        FROM shadow_trades
        WHERE status='closed' AND date(entry_time) >= ? AND btc_regime_age_bars IS NOT NULL
    """, ((datetime.now(timezone.utc)-timedelta(days=days)).strftime("%Y-%m-%d"),))
    buckets = {"0-2": (0, 2), "3-6": (3, 6), "7-12": (7, 12), "13+": (13, 10**9)}
    agg = {k: [0, 0.0] for k in buckets}
    for age, pa in rows:
        if age is None or pa is None:
            continue
        for k, (lo, hi) in buckets.items():
            if lo <= age <= hi:
                agg[k][0] += 1
                agg[k][1] += pa
                break
    print(f"{'age_bars':<10} {'n':>6} {'sumR':>9} {'avgR':>8}")
    for k in ("0-2", "3-6", "7-12", "13+"):
        n, s = agg[k]
        if n:
            print(f"{k:<10} {n:>6} {s:>9.1f} {s/n:>8.3f}")
    y, o = agg["0-2"], agg["13+"]
    if y[0] >= 15 and o[0] >= 15:
        dy = y[1]/y[0] - o[1]/o[0]
        print(f"flip-lag cost proxy (age0-2 vs 13+): {dy:+.3f} R/trade, {dy*y[0]:+.1f} R over {y[0]} trades")

def main():
    days = 7
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    live = sqlite3.connect(LIVE_DB)
    sh = sqlite3.connect(SHADOW_DB)
    fill_reconciliation(live, sh, days)
    config_drift(live, days)
    stop_calibration(live, days)
    cost_drag(live, days)
    capacity(live, days)
    regime_flip_lag(sh, 14)

if __name__ == "__main__":
    main()
