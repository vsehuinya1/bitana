#!/usr/bin/env python3
"""Weekend Aug 1-2 research block.

1) Stop-variant first read: Asia + full-session NY s4/s6/s8 vs 10 ATR baseline
2) HMM OOS validation #1 (frozen train <= Jun 30) on Asia H2/H5 gate

Outputs under research/output/reports/
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hmm_latent_states import (  # noqa: E402
    K,
    adx_series,
    cost_atr,
    ema,
    fetch_klines,
    filtered_states,
    fit_hmm,
    rolling_std,
)

DB = ROOT / "storage" / "signal_shadow.db"
OUT = ROOT / "research" / "output" / "reports"
OUT.mkdir(parents=True, exist_ok=True)
COST_BPS = 12.0  # round-trip


def connect():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def weekday(entry_time: str) -> int:
    return parse_ts(entry_time).weekday()  # Mon=0


def day_key(entry_time: str) -> str:
    return entry_time[:10]


def net_pnl(row) -> float:
    gross = float(row["pnl_atr"] or 0.0)
    atr_pct = float(row["entry_atr_pct"] or 0.0)
    cost = (COST_BPS / 100.0) / atr_pct if atr_pct > 0 else 0.0
    return gross - cost


def load_closed(conn, strategies, start, end=None):
    q = (
        "SELECT * FROM shadow_trades WHERE strategy=? AND status='closed' "
        "AND entry_time>=? AND exit_time IS NOT NULL AND exit_time!=''"
    )
    params_base = [start]
    if end:
        q += " AND entry_time<?"
        params_base.append(end)
    out = {}
    for s in strategies:
        rows = [dict(r) for r in conn.execute(q, [s] + params_base)]
        for r in rows:
            r["net"] = net_pnl(r)
        out[s] = rows
    return out


def live_filter_asia(row) -> bool:
    # Live Asia: weekends off; neutral+bull only (Jul 22 retune)
    if int(row.get("is_weekend") or 0) == 1:
        return False
    if weekday(row["entry_time"]) >= 5:
        return False
    reg = (row.get("btc_trend_state") or "").lower()
    return reg in ("neutral", "bull")


def live_filter_ny(row) -> bool:
    # Live NY: Mon/Sat/Sun off; neutral+bear only
    wd = weekday(row["entry_time"])
    if wd == 0 or wd >= 5:  # Mon or weekend
        return False
    if int(row.get("is_weekend") or 0) == 1:
        return False
    reg = (row.get("btc_trend_state") or "").lower()
    return reg in ("neutral", "bear")


def cap3_accept(rows, cap=3):
    """Candidate-specific: 1 per symbol, max `cap` concurrent opens."""
    active = []
    accepted = []
    for row in sorted(rows, key=lambda r: (r["entry_time"], r.get("id") or 0)):
        et = row["entry_time"]
        active = [a for a in active if a["exit_time"] > et]
        if len(active) >= cap:
            continue
        if any(a["symbol"] == row["symbol"] for a in active):
            continue
        active.append(row)
        accepted.append(row)
    return accepted


def stats(rows, label=""):
    if not rows:
        return {
            "label": label,
            "n": 0,
            "days": 0,
            "sum_net": 0.0,
            "avg_net": 0.0,
            "med_net": 0.0,
            "wr": 0.0,
            "sum_gross": 0.0,
            "avg_gross": 0.0,
            "stop_n": 0,
            "time_n": 0,
            "top_day_share": 0.0,
            "top_day": "",
            "top_day_net": 0.0,
            "pf": 0.0,
        }
    nets = [r["net"] for r in rows]
    gross = [float(r["pnl_atr"] or 0) for r in rows]
    by_day = defaultdict(float)
    for r in rows:
        by_day[day_key(r["entry_time"])] += r["net"]
    top_day = max(by_day, key=by_day.get)
    top_net = by_day[top_day]
    sum_net = sum(nets)
    wins = sum(1 for x in nets if x > 0)
    gp = sum(x for x in nets if x > 0)
    gl = -sum(x for x in nets if x < 0)
    pf = (gp / gl) if gl > 0 else (999.0 if gp > 0 else 0.0)
    return {
        "label": label,
        "n": len(rows),
        "days": len(by_day),
        "sum_net": round(sum_net, 3),
        "avg_net": round(float(np.mean(nets)), 3),
        "med_net": round(float(np.median(nets)), 3),
        "wr": round(100.0 * wins / len(rows), 1),
        "sum_gross": round(sum(gross), 3),
        "avg_gross": round(float(np.mean(gross)), 3),
        "stop_n": sum(1 for r in rows if (r.get("exit_reason") or "") == "stop"),
        "time_n": sum(1 for r in rows if (r.get("exit_reason") or "") == "time"),
        "top_day_share": round(abs(top_net) / abs(sum_net), 3) if sum_net != 0 else 0.0,
        "top_day": top_day,
        "top_day_net": round(top_net, 3),
        "pf": round(pf, 3),
        "by_day": {k: round(v, 3) for k, v in sorted(by_day.items())},
    }


def by_symbol(rows):
    out = {}
    for r in rows:
        out.setdefault(r["symbol"], []).append(r)
    return {
        sym: {
            "n": len(v),
            "avg_net": round(float(np.mean([x["net"] for x in v])), 3),
            "sum_net": round(sum(x["net"] for x in v), 3),
        }
        for sym, v in sorted(out.items(), key=lambda kv: -len(kv[1]))
    }


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def stop_ladder_read(conn):
    print("\n" + "=" * 72)
    print("AUG 2 STOP-VARIANT FIRST READ")
    print("=" * 72)

    asia_strats = [
        "asia_pump_short_4h",
        "asia_pump_short_4h_s4",
        "asia_pump_short_4h_s6",
        "asia_pump_short_4h_s8",
    ]
    ny_strats = [
        "ny_flush_buy_4h",
        "ny_flush_buy_4h_s4",
        "ny_flush_buy_4h_s6",
        "ny_flush_buy_4h_s8",
    ]

    # Overlap windows from plan / deploy dates
    asia_start = "2026-07-20"
    ny_start = "2026-07-23"

    asia_raw = load_closed(conn, asia_strats, asia_start)
    ny_raw = load_closed(conn, ny_strats, ny_start)

    summary_rows = []
    day_rows = []
    detail = {}

    def run_book(name, raw, filt, start):
        print(f"\n--- {name} | window>={start} | live-like filter ---")
        book = {}
        for s, rows in raw.items():
            filt_rows = [r for r in rows if filt(r)]
            acc = cap3_accept(filt_rows)
            st = stats(acc, s)
            st["n_raw"] = len(rows)
            st["n_filt"] = len(filt_rows)
            st["by_symbol"] = by_symbol(acc)
            book[s] = st
            print(
                f"  {s:28s} raw={st['n_raw']:3d} filt={st['n_filt']:3d} "
                f"cap3={st['n']:3d} days={st['days']:2d} "
                f"avg_net={st['avg_net']:+.3f} sum={st['sum_net']:+.2f} "
                f"PF={st['pf']:.2f} WR={st['wr']:.0f}% stop={st['stop_n']} "
                f"top_day={st['top_day']}({st['top_day_net']:+.2f} share={st['top_day_share']:.0%})"
            )
            summary_rows.append(
                {
                    "book": name,
                    "strategy": s,
                    "start": start,
                    "n_raw": st["n_raw"],
                    "n_filt": st["n_filt"],
                    "n_cap3": st["n"],
                    "days": st["days"],
                    "avg_net": st["avg_net"],
                    "sum_net": st["sum_net"],
                    "med_net": st["med_net"],
                    "pf": st["pf"],
                    "wr": st["wr"],
                    "stop_n": st["stop_n"],
                    "time_n": st["time_n"],
                    "top_day": st["top_day"],
                    "top_day_net": st["top_day_net"],
                    "top_day_share": st["top_day_share"],
                }
            )
            for d, v in st.get("by_day", {}).items():
                day_rows.append(
                    {"book": name, "strategy": s, "day": d, "net_atr": v}
                )
            # symbol lines
            for sym, ss in list(st["by_symbol"].items())[:8]:
                print(
                    f"      {sym:16s} n={ss['n']:2d} avg={ss['avg_net']:+.3f} sum={ss['sum_net']:+.2f}"
                )
        # paired delta vs baseline on shared entries after filter+cap3 of baseline keys
        base_name = [k for k in raw if k.endswith("_4h") or k == list(raw.keys())[0]][0]
        # pick true baseline
        base_name = [k for k in raw if not any(k.endswith(x) for x in ("_s4", "_s6", "_s8"))][0]
        base_filt = [r for r in raw[base_name] if filt(r)]
        base_acc = cap3_accept(base_filt)
        base_keys = {(r["symbol"], r["entry_time"]) for r in base_acc}
        print(f"  paired vs {base_name} on cap3 keys n={len(base_keys)}")
        for s, rows in raw.items():
            if s == base_name:
                continue
            # map variant by key
            m = {(r["symbol"], r["entry_time"]): r for r in rows if filt(r)}
            paired_base = []
            paired_var = []
            missing = 0
            for r in base_acc:
                k = (r["symbol"], r["entry_time"])
                if k not in m:
                    missing += 1
                    continue
                paired_base.append(r)
                paired_var.append(m[k])
            if not paired_base:
                print(f"    {s}: no pairs")
                continue
            sb = stats(paired_base, "base")
            sv = stats(paired_var, s)
            delta = sv["avg_net"] - sb["avg_net"]
            print(
                f"    {s:28s} paired_n={sv['n']:3d} miss={missing} "
                f"base_avg={sb['avg_net']:+.3f} var_avg={sv['avg_net']:+.3f} "
                f"delta={delta:+.3f} base_sum={sb['sum_net']:+.2f} var_sum={sv['sum_net']:+.2f} "
                f"var_stops={sv['stop_n']}"
            )
            summary_rows.append(
                {
                    "book": name + "_paired",
                    "strategy": s,
                    "start": start,
                    "n_raw": len(rows),
                    "n_filt": len([r for r in rows if filt(r)]),
                    "n_cap3": sv["n"],
                    "days": sv["days"],
                    "avg_net": sv["avg_net"],
                    "sum_net": sv["sum_net"],
                    "med_net": sv["med_net"],
                    "pf": sv["pf"],
                    "wr": sv["wr"],
                    "stop_n": sv["stop_n"],
                    "time_n": sv["time_n"],
                    "top_day": sv["top_day"],
                    "top_day_net": sv["top_day_net"],
                    "top_day_share": sv["top_day_share"],
                    "base_avg": sb["avg_net"],
                    "base_sum": sb["sum_net"],
                    "delta_avg": round(delta, 3),
                }
            )
        detail[name] = book
        return book

    asia_book = run_book("asia", asia_raw, live_filter_asia, asia_start)
    ny_book = run_book("ny_full", ny_raw, live_filter_ny, ny_start)

    # Also all-sample (no regime filter) for transparency
    print("\n--- unfiltered (weekdays only, any regime) for sample size ---")
    def wd_only(r):
        return weekday(r["entry_time"]) < 5 and int(r.get("is_weekend") or 0) == 0

    for name, raw, start in (
        ("asia_wd", asia_raw, asia_start),
        ("ny_wd", ny_raw, ny_start),
    ):
        for s, rows in raw.items():
            frows = [r for r in rows if wd_only(r)]
            acc = cap3_accept(frows)
            st = stats(acc, s)
            print(
                f"  {name:8s} {s:28s} cap3={st['n']:3d} days={st['days']:2d} "
                f"avg={st['avg_net']:+.3f} sum={st['sum_net']:+.2f} PF={st['pf']:.2f} stops={st['stop_n']}"
            )

    write_csv(
        OUT / "stop_variant_first_read_2026-08-01.csv",
        summary_rows,
        [
            "book",
            "strategy",
            "start",
            "n_raw",
            "n_filt",
            "n_cap3",
            "days",
            "avg_net",
            "sum_net",
            "med_net",
            "pf",
            "wr",
            "stop_n",
            "time_n",
            "top_day",
            "top_day_net",
            "top_day_share",
            "base_avg",
            "base_sum",
            "delta_avg",
        ],
    )
    write_csv(
        OUT / "stop_variant_daily_2026-08-01.csv",
        day_rows,
        ["book", "strategy", "day", "net_atr"],
    )
    return {"asia": asia_book, "ny": ny_book, "summary_rows": summary_rows}


def hmm_oos_validation():
    print("\n" + "=" * 72)
    print("HMM OOS VALIDATION #1 (frozen train <= 2026-06-30)")
    print("=" * 72)

    print("Fetching BTC 4h...")
    klines = fetch_klines(total_bars=5000)
    close = np.asarray([k["close"] for k in klines])
    high = np.asarray([k["high"] for k in klines])
    low = np.asarray([k["low"] for k in klines])
    ret = np.zeros(len(close))
    ret[1:] = np.diff(np.log(close))
    vol6 = rolling_std(ret, 6)
    vol42 = rolling_std(ret, 42)
    vol_ratio = vol6 / np.maximum(vol42, 1e-8)
    range_pct = (high - low) / close
    ema50 = ema(close.tolist(), 50)
    trend = close / ema50 - 1
    raw_x = np.column_stack((ret, vol6, vol_ratio, range_pct, trend))
    feature_names = ("log_ret", "vol6", "vol_ratio", "range_pct", "trend50")

    valid_start = 100
    times = np.asarray([k["close_ms"] for k in klines])
    train_end = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
    train_mask = (np.arange(len(klines)) >= valid_start) & (times < train_end)
    train_raw = raw_x[train_mask]
    mean_x, std_x = train_raw.mean(axis=0), train_raw.std(axis=0)
    std_x = np.maximum(std_x, 1e-8)
    x = (raw_x - mean_x) / std_x
    x_train = x[train_mask]
    print(
        f"train bars={len(x_train)} "
        f"{datetime.fromtimestamp(times[train_mask][0]/1000, tz=timezone.utc):%Y-%m-%d} -> 2026-06-30"
    )

    best = None
    for seed in (3, 11, 29):
        fitted = fit_hmm(x_train, seed)
        print(f"  seed={seed} ll={fitted[0]:.1f} iter={fitted[-1]}")
        if best is None or fitted[0] > best[0]:
            best = fitted
    ll, pi, trans, means, variances, iterations = best
    labels, posterior = filtered_states(x[valid_start:], pi, trans, means, variances)
    labels_full = np.full(len(klines), -1)
    confidence_full = np.zeros(len(klines))
    labels_full[valid_start:] = labels
    confidence_full[valid_start:] = posterior.max(axis=1)

    raw_means = means * std_x + mean_x
    order = sorted(range(K), key=lambda s: (raw_means[s, 4], raw_means[s, 2]))
    display_id = {model_state: i for i, model_state in enumerate(order)}

    print("\nSTATE PROFILES (display order low->high trend)")
    profiles = {}
    for model_state in order:
        display = display_id[model_state]
        profile = dict(zip(feature_names, raw_means[model_state]))
        occ = float(np.mean(labels == model_state))
        profiles[display] = {**{k: float(v) for k, v in profile.items()}, "occ": occ}
        print(
            f"  H{display}: occ={100*occ:5.1f}% "
            f"ret={profile['log_ret']*100:+.3f}% vol6={profile['vol6']*100:.3f}% "
            f"vol_ratio={profile['vol_ratio']:.2f} range={profile['range_pct']*100:.2f}% "
            f"trend50={profile['trend50']*100:+.2f}%"
        )

    close_times = [k["close_ms"] for k in klines]

    def hmm_at(entry_time):
        ts = int(parse_ts(entry_time).timestamp() * 1000)
        idx = int(np.searchsorted(close_times, ts, side="right") - 1)
        if idx < valid_start:
            return None, None
        return int(display_id[int(labels_full[idx])]), float(confidence_full[idx])

    # Load Asia trades for OOS windows
    conn = connect()
    # Primary pre-registered: Jul 18-24
    # Extended: Jul 18 -> Jul 31 (still OOS vs selection Jul1-17; train still <=Jun30)
    windows = [
        ("primary_Jul18_24", "2026-07-18", "2026-07-25"),
        ("extended_Jul18_31", "2026-07-18", "2026-08-01"),
        ("post_fix_Jul23_31", "2026-07-23", "2026-08-01"),
    ]

    results = []
    for wname, start, end in windows:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM shadow_trades WHERE strategy='asia_pump_short_4h' "
                "AND status='closed' AND entry_time>=? AND entry_time<?",
                (start, end),
            )
        ]
        for r in rows:
            r["net"] = net_pnl(r)
            r["hmm_state"], r["hmm_conf"] = hmm_at(r["entry_time"])
        # live-like filter
        rows = [r for r in rows if live_filter_asia(r) and r["hmm_state"] is not None]

        print(f"\n-- window {wname} [{start},{end}) live-like Asia n_filt={len(rows)} --")
        # per state
        for st in range(K):
            sub = [r for r in rows if r["hmm_state"] == st]
            if len(sub) < 2:
                continue
            acc = cap3_accept(sub)
            s = stats(acc)
            conf = float(np.mean([r["hmm_conf"] for r in acc])) if acc else 0
            print(
                f"  H{st}: n_raw={len(sub):2d} cap3={s['n']:2d} days={s['days']} "
                f"avg={s['avg_net']:+.3f} sum={s['sum_net']:+.2f} conf={conf:.0%}"
            )

        def eval_filter(allowed, label):
            in_state = [r for r in rows if r["hmm_state"] in allowed]
            blocked = [r for r in rows if r["hmm_state"] not in allowed]
            acc_in = cap3_accept(in_state)
            acc_all = cap3_accept(rows)
            acc_blk = cap3_accept(blocked)
            si = stats(acc_in)
            sa = stats(acc_all)
            sb = stats(acc_blk)
            conf = float(np.mean([r["hmm_conf"] for r in acc_in])) if acc_in else 0.0
            print(
                f"  FILTER {label:12s} in_cap3={si['n']:2d} days={si['days']} "
                f"avg={si['avg_net']:+.3f} sum={si['sum_net']:+.2f} conf={conf:.0%} | "
                f"blocked_cap3={sb['n']:2d} avg={sb['avg_net']:+.3f} sum={sb['sum_net']:+.2f} | "
                f"all_cap3={sa['n']:2d} avg={sa['avg_net']:+.3f}"
            )
            # day breakdown in-state
            if si.get("by_day"):
                print(f"    in-state days: {si['by_day']}")
            return {
                "window": wname,
                "start": start,
                "end": end,
                "filter": label,
                "n_filt_universe": len(rows),
                "in_n": si["n"],
                "in_days": si["days"],
                "in_avg": si["avg_net"],
                "in_sum": si["sum_net"],
                "in_pf": si["pf"],
                "in_wr": si["wr"],
                "in_conf": round(conf, 3),
                "in_top_day": si["top_day"],
                "in_top_share": si["top_day_share"],
                "blocked_n": sb["n"],
                "blocked_avg": sb["avg_net"],
                "blocked_sum": sb["sum_net"],
                "all_n": sa["n"],
                "all_avg": sa["avg_net"],
                "all_sum": sa["sum_net"],
                "by_day_in": si.get("by_day", {}),
            }

        results.append(eval_filter(tuple(range(K)), "all"))
        results.append(eval_filter((2, 5), "H2+H5"))
        results.append(eval_filter((5,), "H5_only"))
        results.append(eval_filter((2,), "H2_only"))

        # secondary NY
        ny_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM shadow_trades WHERE strategy IN "
                "('ny_flush_buy_4h','ny_flush_buy_4h_open') AND status='closed' "
                "AND entry_time>=? AND entry_time<?",
                (start, end),
            )
        ]
        for r in ny_rows:
            r["net"] = net_pnl(r)
            r["hmm_state"], r["hmm_conf"] = hmm_at(r["entry_time"])
        for strat in ("ny_flush_buy_4h", "ny_flush_buy_4h_open"):
            sub = [
                r
                for r in ny_rows
                if r["strategy"] == strat
                and r["hmm_state"] is not None
                and live_filter_ny(r)
            ]
            if len(sub) < 3:
                continue
            print(f"  NY secondary {strat} n={len(sub)}")
            for label, allowed in (("all", tuple(range(K))), ("H4+H5", (4, 5))):
                acc = cap3_accept([r for r in sub if r["hmm_state"] in allowed])
                s = stats(acc)
                print(
                    f"    {label:8s} cap3={s['n']:2d} avg={s['avg_net']:+.3f} sum={s['sum_net']:+.2f}"
                )

    # Current state
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cur_idx = int(np.where(times <= now_ms)[0][-1])
    cur_h = int(display_id[int(labels_full[cur_idx])])
    cur_c = float(confidence_full[cur_idx])
    adx = adx_series(high, low, close)
    ema200 = ema(close.tolist(), 200)
    if cur_idx < 200 or adx[cur_idx] <= 25:
        adx_reg = "neutral"
    elif close[cur_idx] > ema200[cur_idx]:
        adx_reg = "bull"
    else:
        adx_reg = "bear"
    print(f"\nCURRENT: HMM=H{cur_h} conf={cur_c:.1%} ADX_regime={adx_reg}")

    write_csv(
        OUT / "hmm_oos_validation_2026-08-01.csv",
        results,
        [
            "window",
            "start",
            "end",
            "filter",
            "n_filt_universe",
            "in_n",
            "in_days",
            "in_avg",
            "in_sum",
            "in_pf",
            "in_wr",
            "in_conf",
            "in_top_day",
            "in_top_share",
            "blocked_n",
            "blocked_avg",
            "blocked_sum",
            "all_n",
            "all_avg",
            "all_sum",
        ],
    )

    # Decision helper on primary + extended H2+H5
    def pick(window, filt):
        for r in results:
            if r["window"] == window and r["filter"] == filt:
                return r
        return None

    primary = pick("primary_Jul18_24", "H2+H5")
    extended = pick("extended_Jul18_31", "H2+H5")
    all_ext = pick("extended_Jul18_31", "all")

    decision = {
        "current_hmm": f"H{cur_h}",
        "current_conf": round(cur_c, 3),
        "current_adx": adx_reg,
        "primary": primary,
        "extended": extended,
        "all_extended": all_ext,
        "profiles": profiles,
        "train_end": "2026-06-30",
        "model_version": "hmm6_train_le_20260630_seed_best_of_3_11_29",
    }

    # Apply pre-registered criteria
    # PASS: in-state net/trade >= +1.0 AND blocked net <= 0 AND conf >= 70%, n>=15
    # EXTEND: <15 accepted in-state
    # KILL: in-state net/trade < +0.2 on >=15 OR blocked netted > +10 ATR
    def decide(r, name):
        if r is None:
            return f"{name}: NO_DATA"
        n, avg, bsum, conf = r["in_n"], r["in_avg"], r["blocked_sum"], r["in_conf"]
        if n < 15:
            return f"{name}: EXTEND (in_cap3={n}<15; avg={avg:+.3f} blocked_sum={bsum:+.2f} conf={conf:.0%})"
        if avg < 0.2 or bsum > 10:
            return f"{name}: KILL (avg={avg:+.3f} blocked_sum={bsum:+.2f} n={n})"
        if avg >= 1.0 and bsum <= 0 and conf >= 0.70:
            return f"{name}: PASS (avg={avg:+.3f} blocked_sum={bsum:+.2f} conf={conf:.0%} n={n})"
        return (
            f"{name}: EXTEND/MIXED (avg={avg:+.3f} blocked_sum={bsum:+.2f} "
            f"conf={conf:.0%} n={n}; not full PASS)"
        )

    decisions = [
        decide(primary, "primary_Jul18_24"),
        decide(extended, "extended_Jul18_31"),
    ]
    for d in decisions:
        print("DECISION:", d)
    decision["decisions"] = decisions

    # Aug 1 retrain: only AFTER validation decision.
    # If EXTEND or MIXED/KILL without PASS → do NOT retrain into live path; can version research snapshot only.
    if any(d.endswith("PASS") or "PASS (" in d for d in decisions):
        retrain = "ALLOWED_after_PASS — run monthly retrain train<=Jul31 as separate versioned model"
    elif any("KILL" in d for d in decisions) and not any("EXTEND" in d for d in decisions):
        retrain = "SKIP_retrain_for_gate — hypothesis killed; monthly walk-forward still ok as measurement only"
    else:
        retrain = (
            "DEFER_retrain_for_gate — OOS not PASS; keep frozen Jun30 model for any further gate tests. "
            "Optional research-only Jul31 retrain OK if versioned and not used for live gate yet."
        )
    print("RETRAIN:", retrain)
    decision["retrain"] = retrain

    with open(OUT / "hmm_oos_decision_2026-08-01.json", "w") as f:
        json.dump(decision, f, indent=2, default=str)

    conn.close()
    return decision


def main():
    conn = connect()
    stop = stop_ladder_read(conn)
    conn.close()
    hmm = hmm_oos_validation()

    # Final machine-readable bundle
    bundle = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "stop_summary": stop["summary_rows"],
        "hmm_decisions": hmm.get("decisions"),
        "hmm_retrain": hmm.get("retrain"),
        "hmm_current": {
            "state": hmm.get("current_hmm"),
            "conf": hmm.get("current_conf"),
            "adx": hmm.get("current_adx"),
        },
    }
    with open(OUT / "weekend_aug1_bundle.json", "w") as f:
        json.dump(bundle, f, indent=2, default=str)
    print(f"\nWrote reports under {OUT}")


if __name__ == "__main__":
    main()
