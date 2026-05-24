#!/usr/bin/env python3
"""
V6.2 Telemetry Analyzer — Observational Analysis Script.

Queries v6_telemetry.db to compare actual trade exits against the 6 shadow exit rules.
Outputs win rate, average win/loss, expectancy, and Kelly sizing comparisons.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from collections import defaultdict
import numpy as np

TELEMETRY_DB = Path(__file__).parent.parent / "storage" / "v6_telemetry.db"

def analyze_telemetry():
    if not TELEMETRY_DB.exists():
        print(f"Error: Telemetry database not found at {TELEMETRY_DB}")
        print("Please run some trades on the live bot to generate telemetry.")
        return

    conn = sqlite3.connect(str(TELEMETRY_DB))
    conn.row_factory = sqlite3.Row

    # 1. Fetch Actual Trades Stats
    actual_trades = conn.execute("""
        SELECT 
            t.trade_uuid, t.symbol, t.side, t.entry_price, t.decile,
            e.pnl_r, e.hold_bars, e.mae, e.mfe, e.exit_reason
        FROM trade_entries t
        JOIN exit_attribution e ON t.trade_uuid = e.trade_uuid
    """).fetchall()

    if not actual_trades:
        print("No closed trades found in exit_attribution yet. Waiting for trades...")
        conn.close()
        return

    n_actual = len(actual_trades)
    actual_pnls = [t["pnl_r"] for t in actual_trades]
    actual_wins = [p for p in actual_pnls if p > 0]
    actual_losses = [p for p in actual_pnls if p <= 0]

    wr_actual = len(actual_wins) / n_actual if n_actual > 0 else 0
    avg_win_actual = np.mean(actual_wins) if actual_wins else 0
    avg_loss_actual = np.mean(actual_losses) if actual_losses else 0
    expectancy_actual = np.mean(actual_pnls) if actual_pnls else 0
    std_actual = np.std(actual_pnls) if len(actual_pnls) > 1 else 0

    print("=" * 70)
    print(f" V6.2 OBSERVATIONAL ANALYSIS SUMMARY ({n_actual} Trades)")
    print("=" * 70)
    print(f"Actual System Performance:")
    print(f"  Trades:      {n_actual}")
    print(f"  Win Rate:    {wr_actual:.1%}")
    print(f"  Avg Win:     {avg_win_actual:+.3f}R")
    print(f"  Avg Loss:    {avg_loss_actual:+.3f}R")
    print(f"  Expectancy:  {expectancy_actual:+.3f}R / trade")
    print(f"  R Std Dev:   {std_actual:.3f}")
    print()

    # 2. Fetch Shadow Exits Stats
    # Get all shadow exit records
    shadow_records = conn.execute("""
        SELECT trade_uuid, shadow_name, trigger_bar, shadow_r, actual_exit_r
        FROM shadow_exits
    """).fetchall()

    # Group triggers by trade_uuid and shadow_name
    # Since a shadow can trigger on multiple bars (though usually we care about the FIRST trigger),
    # we take the first trigger (lowest trigger_bar) per shadow per trade.
    first_shadows = defaultdict(dict)
    for row in shadow_records:
        tuuid = row["trade_uuid"]
        sname = row["shadow_name"]
        bar = row["trigger_bar"]
        r_val = row["shadow_r"]
        
        if sname not in first_shadows[tuuid] or bar < first_shadows[tuuid][sname]["bar"]:
            first_shadows[tuuid][sname] = {
                "bar": bar,
                "r": r_val
            }

    # Evaluate each shadow strategy
    # If a shadow triggered, we assume we exited at its shadow_r.
    # If a shadow did NOT trigger, we assume we exited at the actual_exit_r.
    shadow_names = [
        "structural_invalidation",
        "momentum_reversal",
        "tight_atr_trail_1.5",
        "loose_runner_trail_5.0",
        "breakeven_after_1R",
        "early_dead_cut"
    ]

    print("Shadow Exit Strategy Comparisons (Simulated on Live Trades):")
    print(f"{'Exit Strategy':<25} | {'WR':<6} | {'Avg Win':<8} | {'Avg Loss':<8} | {'Expectancy':<10} | {'Delta':<6}")
    print("-" * 75)
    print(f"{'ACTUAL SYSTEM':<25} | {wr_actual:5.1%} | {avg_win_actual:+8.3f}R | {avg_loss_actual:+8.3f}R | {expectancy_actual:+10.3f}R | {'base':<6}")

    for sname in shadow_names:
        sim_pnls = []
        for t in actual_trades:
            tuuid = t["trade_uuid"]
            actual_r = t["pnl_r"]
            
            # Check if this shadow triggered for this trade
            if sname in first_shadows[tuuid]:
                sim_pnls.append(first_shadows[tuuid][sname]["r"])
            else:
                sim_pnls.append(actual_r)

        n_sim = len(sim_pnls)
        sim_wins = [p for p in sim_pnls if p > 0]
        sim_losses = [p for p in sim_pnls if p <= 0]
        
        wr_sim = len(sim_wins) / n_sim if n_sim > 0 else 0
        avg_win_sim = np.mean(sim_wins) if sim_wins else 0
        avg_loss_sim = np.mean(sim_losses) if sim_losses else 0
        expectancy_sim = np.mean(sim_pnls) if sim_pnls else 0
        delta = expectancy_sim - expectancy_actual

        print(f"{sname:<25} | {wr_sim:5.1%} | {avg_win_sim:+8.3f}R | {avg_loss_sim:+8.3f}R | {expectancy_sim:+10.3f}R | {delta:+5.3f}R")

    print("=" * 70)
    conn.close()

if __name__ == "__main__":
    analyze_telemetry()
