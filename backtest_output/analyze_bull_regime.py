"""Analyze enriched shadow trades entered during the current BTC bull regime."""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict


DB = "storage/signal_shadow.db"
STRATEGIES = (
    "asia_pump_short_4h",
    "asia_pump_short_4h_tsl",
    "asia_pump_short_4h_limit15",
    "ny_flush_buy_4h",
    "ny_flush_buy_4h_open",
    "ny_flush_buy_4h_open_tsl",
    "london_burst_fade",
    "follow_3h_london",
    "fade_6h_late",
    "late_fade",
    "setup_follow",
    "setup_fade",
    "burst_follow",
)


def metrics(rows: list[sqlite3.Row]) -> str:
    pnl = [float(r["pnl_atr"]) for r in rows]
    if not pnl:
        return "n=0"
    wins = sum(v > 0 for v in pnl)
    stops = sum(r["exit_reason"] in {"stop", "stop_loss"} for r in rows)
    return (
        f"n={len(pnl):3d} total={sum(pnl):+8.2f} avg={statistics.mean(pnl):+6.3f} "
        f"med={statistics.median(pnl):+6.3f} wr={wins / len(pnl):5.1%} "
        f"stops={stops:2d} min={min(pnl):+6.2f} max={max(pnl):+6.2f}"
    )


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# Use the contiguous current bull run, not older historical bull labels.
latest = conn.execute(
    "SELECT entry_time, btc_regime_age_bars FROM shadow_trades "
    "WHERE btc_trend_state='bull' ORDER BY id DESC LIMIT 1"
).fetchone()
first = conn.execute(
    "SELECT MIN(entry_time) FROM shadow_trades WHERE btc_trend_state='bull' "
    "AND entry_time >= '2026-07-15'"
).fetchone()[0]
print("CURRENT_BULL_WINDOW", first, "to", latest["entry_time"], "latest_age_4h_bars", latest["btc_regime_age_bars"])

base_where = "btc_trend_state='bull' AND entry_time >= '2026-07-15' AND status='closed'"
all_rows = conn.execute(f"SELECT * FROM shadow_trades WHERE {base_where}").fetchall()
open_n = conn.execute(
    "SELECT COUNT(*) FROM shadow_trades WHERE btc_trend_state='bull' "
    "AND entry_time >= '2026-07-15' AND status='open'"
).fetchone()[0]
print("ALL_CLOSED", metrics(all_rows), "open", open_n)

print("\nSTRATEGIES")
for strategy in STRATEGIES:
    rows = [r for r in all_rows if r["strategy"] == strategy]
    if rows:
        print(f"{strategy:32s} {metrics(rows)}")

print("\nSTRATEGY_BY_DAY")
for strategy in STRATEGIES:
    rows = [r for r in all_rows if r["strategy"] == strategy]
    if not rows:
        continue
    by_day: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_day[row["entry_time"][:10]].append(row)
    parts = [f"{day[5:]} {metrics(day_rows)}" for day, day_rows in sorted(by_day.items())]
    print(strategy, " | ".join(parts))

print("\nPLAIN_VS_TSL_PAIRED")
for plain, tsl in (
    ("asia_pump_short_4h", "asia_pump_short_4h_tsl"),
    ("ny_flush_buy_4h_open", "ny_flush_buy_4h_open_tsl"),
):
    pairs = conn.execute(
        f"""
        SELECT p.symbol, p.entry_time, p.pnl_atr plain_pnl, t.pnl_atr tsl_pnl
        FROM shadow_trades p
        JOIN shadow_trades t
          ON t.symbol=p.symbol AND t.entry_time=p.entry_time
        WHERE p.strategy=? AND t.strategy=?
          AND p.btc_trend_state='bull' AND p.entry_time >= '2026-07-15'
          AND p.status='closed' AND t.status='closed'
        """,
        (plain, tsl),
    ).fetchall()
    diffs = [float(r["plain_pnl"]) - float(r["tsl_pnl"]) for r in pairs]
    if diffs:
        print(
            plain,
            "vs",
            tsl,
            f"pairs={len(diffs)} plain_total={sum(float(r['plain_pnl']) for r in pairs):+.2f}",
            f"tsl_total={sum(float(r['tsl_pnl']) for r in pairs):+.2f}",
            f"plain_better={sum(d > 0 for d in diffs)}/{len(diffs)}",
        )

print("\nSELECTED_SPLITS")
for strategy in ("asia_pump_short_4h", "ny_flush_buy_4h_open", "london_burst_fade", "setup_follow"):
    rows = [r for r in all_rows if r["strategy"] == strategy]
    if not rows:
        continue
    print("--", strategy)
    for field in ("symbol_trend_state", "hour", "cluster_breadth"):
        groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            value = row[field]
            if field == "cluster_breadth":
                value = "3+" if value is not None and value >= 3 else str(value)
            groups[str(value)].append(row)
        for value, group in sorted(groups.items()):
            print(f"  {field}={value:8s} {metrics(group)}")

print("\nONE_HOUR_CHECKPOINT")
for strategy in ("asia_pump_short_4h", "ny_flush_buy_4h_open", "fade_6h_late", "follow_3h_london"):
    rows = [r for r in all_rows if r["strategy"] == strategy and r["pnl_1h"] is not None]
    if not rows:
        continue
    up = [r for r in rows if r["pnl_1h"] > 0]
    down = [r for r in rows if r["pnl_1h"] <= 0]
    early_exit_total = sum(float(r["pnl_1h"]) if r["pnl_1h"] <= 0 else float(r["pnl_atr"]) for r in rows)
    final_total = sum(float(r["pnl_atr"]) for r in rows)
    print(
        strategy,
        "up@1h",
        metrics(up),
        "| down@1h",
        metrics(down),
        f"| final_total={final_total:+.2f} cut_nonpositive@1h={early_exit_total:+.2f}",
    )

print("\nEXECUTION_NET_ESTIMATE")
for strategy in ("asia_pump_short_4h", "ny_flush_buy_4h_open", "london_burst_fade", "setup_follow"):
    rows = [r for r in all_rows if r["strategy"] == strategy and r["entry_atr_pct"]]
    if not rows:
        continue
    # 12 bps round-trip estimate: 8 bps taker fees + 4 bps slippage.
    cost_atr = [0.12 / float(r["entry_atr_pct"]) for r in rows]
    gross = sum(float(r["pnl_atr"]) for r in rows)
    print(
        strategy,
        f"n={len(rows)} gross={gross:+.2f} est_cost={sum(cost_atr):.2f} net={gross-sum(cost_atr):+.2f}",
        f"avg_spread={statistics.mean(float(r['spread_bps']) for r in rows if r['spread_bps'] is not None):.2f}bps",
    )

print("\nTOP_SYMBOLS_ASIA")
asia = [r for r in all_rows if r["strategy"] == "asia_pump_short_4h"]
by_symbol: dict[str, list[sqlite3.Row]] = defaultdict(list)
for row in asia:
    by_symbol[row["symbol"]].append(row)
for symbol, rows in sorted(by_symbol.items(), key=lambda item: sum(float(r["pnl_atr"]) for r in item[1]), reverse=True):
    print(f"{symbol:14s} {metrics(rows)}")

print("\nROUGH_LIVE_CAP_SIMULATION")
for strategy in ("asia_pump_short_4h", "asia_pump_short_4h_tsl", "ny_flush_buy_4h_open", "london_burst_fade"):
    rows = sorted(
        [r for r in all_rows if r["strategy"] == strategy],
        key=lambda r: (r["entry_time"], r["id"]),
    )
    for cap in (2, 3):
        active: list[sqlite3.Row] = []
        accepted: list[sqlite3.Row] = []
        for row in rows:
            active = [r for r in active if r["exit_time"] > row["entry_time"]]
            if len(active) >= cap:
                continue
            if any(r["symbol"] == row["symbol"] for r in active):
                continue
            active.append(row)
            accepted.append(row)
        if accepted:
            gross = sum(float(r["pnl_atr"]) for r in accepted)
            costs = sum(0.12 / float(r["entry_atr_pct"]) for r in accepted if r["entry_atr_pct"])
            print(
                f"{strategy:28s} cap={cap} accepted={len(accepted):2d}/{len(rows):2d} "
                f"gross={gross:+7.2f} est_net={gross-costs:+7.2f}"
            )

print("\nLONDON_FADE_CONTEXT")
for r in conn.execute(
    """
    SELECT substr(entry_time,1,10) day, btc_trend_state regime, side,
           COUNT(*) n, ROUND(SUM(pnl_atr),2) total, ROUND(AVG(pnl_atr),3) avg
    FROM shadow_trades
    WHERE strategy='london_burst_fade' AND entry_time >= '2026-07-01' AND status='closed'
    GROUP BY day, regime, side ORDER BY day, regime, side
    """
):
    print(dict(r))

print("\nLONDON_BULL_CANDIDATE_CAPS")
london = sorted(
    [r for r in all_rows if r["strategy"] == "london_burst_fade"],
    key=lambda r: (r["entry_time"], r["id"]),
)
for label, candidate in (
    ("all", london),
    ("long_only", [r for r in london if r["side"] == "LONG"]),
    ("hours_8_10_12_long", [r for r in london if r["side"] == "LONG" and r["hour"] in (8, 10, 12)]),
    ("hour_12_long", [r for r in london if r["side"] == "LONG" and r["hour"] == 12]),
):
    for cap in (2, 3):
        active = []
        accepted = []
        for row in candidate:
            active = [r for r in active if r["exit_time"] > row["entry_time"]]
            if len(active) >= cap or any(r["symbol"] == row["symbol"] for r in active):
                continue
            active.append(row)
            accepted.append(row)
        gross = sum(float(r["pnl_atr"]) for r in accepted)
        costs = sum(0.12 / float(r["entry_atr_pct"]) for r in accepted if r["entry_atr_pct"])
        print(
            f"{label:22s} cap={cap} accepted={len(accepted):2d}/{len(candidate):2d} "
            f"gross={gross:+7.2f} est_net={gross-costs:+7.2f}"
        )

print("\nLONDON_LONG_HOUR_HISTORY")
for r in conn.execute(
    """
    SELECT hour, btc_trend_state regime, COUNT(*) n,
           ROUND(SUM(pnl_atr),2) total, ROUND(AVG(pnl_atr),3) avg,
           COUNT(DISTINCT substr(entry_time,1,10)) days
    FROM shadow_trades
    WHERE strategy='london_burst_fade' AND entry_time >= '2026-07-01'
      AND status='closed' AND side='LONG'
    GROUP BY hour, regime ORDER BY hour, regime
    """
):
    print(dict(r))

conn.close()
