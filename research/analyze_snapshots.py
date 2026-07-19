import sqlite3
import pandas as pd
import numpy as np
import warnings

warnings.filterwarnings("ignore")

DB_PATH = "storage/signal_shadow.db"

def pack(vals):
    if len(vals) == 0:
        return "n/a"
    s = sorted(vals)
    mean = sum(vals) / len(vals)
    med = s[len(s) // 2]
    wr = sum(1 for v in vals if v > 0) / len(vals) * 100
    return f"n={len(vals):4d} mean={mean:+.3f} med={med:+.3f} WR={wr:.0f}%"

def main():
    conn = sqlite3.connect(DB_PATH)
    
    # ----------------------------------------------------
    # ANALYZE BURST SNAPSHOTS (Intraday liquidations)
    # ----------------------------------------------------
    print("======================================================================")
    print("ANALYSIS OF BURST SNAPSHOTS (Intraday Liquidation Bursts)")
    print("======================================================================")
    
    # Load burst snapshots
    # Filter for significant imbalance
    query_burst = """
    SELECT session, fwd_atr_12, fwd_atr_24, fwd_atr_96, liq_imbalance_30m, burst_volume_30m
    FROM burst_snapshots
    WHERE bars_tracked >= 12
      AND liq_imbalance_30m IS NOT NULL
      AND abs(liq_imbalance_30m) >= 0.3
    """
    df_burst = pd.read_sql_query(query_burst, conn)
    print(f"Loaded {len(df_burst)} burst snapshots with |imbalance| >= 0.3")
    
    # Calculate follow and fade returns
    # FOLLOW: trade WITH imbalance (imb > 0 means long-liq dominated, which is sell-off pressure, 
    # but wait: in the codebase, follow_return is defined as:
    # if imb > 0: return fwd_long else: return -fwd_long
    # Let's verify this convention:
    # long-liq burst means LONG liquidations (buyers forced to sell). This is a bearish event.
    # In shadow_follow_fade_audit.py:
    # def follow_return(fwd_long, imb):
    #     if imb > 0: return fwd_long
    #     return -fwd_long
    # wait, fwd_long is the forward return of a LONG trade.
    # If we follow the liquidation flow: long-liq (imb > 0) is a short signal or a long signal?
    # Ah! In crypto, "liquidations are used as fuel for momentum" (follow the liquidations) or "fade the liquidations".
    # If long-liq occurs, it's a price dump. If we follow it, we should go SHORT.
    # Let's check the code in shadow_follow_fade_audit.py line 45:
    # def follow_return(fwd_long, imb):
    #     if imb > 0: return fwd_long
    #     return -fwd_long
    # Wait, if imb > 0 (more long liqs, price dumped), follow_return returns fwd_long (LONG return).
    # That means "follow" goes LONG on long liqs?
    # Ah! Let's check: "long after long-liq burst, short after short-liq burst."
    # Yes, "long after long-liq burst" (rebound/momentum) is the follow convention in that script!
    # Let's calculate both conventions just to be clear and match the audit script.
    
    sessions = ["asia", "london", "ny", "late"]
    horizons = [("1h (12 bars)", "fwd_atr_12"), ("2h (24 bars)", "fwd_atr_24"), ("8h (96 bars)", "fwd_atr_96")]
    
    for label, col in horizons:
        print(f"\n--- Horizon: {label} ---")
        for sess in sessions:
            sess_df = df_burst[df_burst['session'] == sess]
            
            # Follow returns: if imb > 0, return fwd_long. If imb < 0, return -fwd_long.
            follow_vals = []
            fade_vals = []
            for _, r in sess_df.iterrows():
                fwd = r[col]
                imb = r['liq_imbalance_30m']
                if pd.isna(fwd) or pd.isna(imb):
                    continue
                # follow
                fol_ret = fwd if imb > 0 else -fwd
                follow_vals.append(fol_ret)
                # fade
                fad_ret = -fwd if imb > 0 else fwd
                fade_vals.append(fad_ret)
                
            print(f"  Session: {sess:6s} | FOLLOW: {pack(follow_vals)} | FADE: {pack(fade_vals)}")

    # ----------------------------------------------------
    # ANALYZE SETUP SNAPSHOTS (Cascade setups)
    # ----------------------------------------------------
    print("\n======================================================================")
    print("ANALYSIS OF SETUP SNAPSHOTS (Cascade Setups)")
    print("======================================================================")
    
    query_setup = """
    SELECT session, fwd_atr_12, fwd_atr_24, fwd_atr_96, liq_direction_imb
    FROM setup_snapshots
    WHERE bars_tracked >= 12
    """
    df_setup = pd.read_sql_query(query_setup, conn)
    print(f"Loaded {len(df_setup)} setup snapshots")
    
    # We do the same check for setup snapshots
    for label, col in horizons:
        print(f"\n--- Horizon: {label} ---")
        for sess in sessions:
            sess_df = df_setup[df_setup['session'] == sess]
            
            # Follow and Fade based on liq_direction_imb
            follow_vals = []
            fade_vals = []
            for _, r in sess_df.iterrows():
                fwd = r[col]
                imb = r['liq_direction_imb']
                if pd.isna(fwd) or pd.isna(imb):
                    continue
                # follow
                fol_ret = fwd if imb > 0 else -fwd
                follow_vals.append(fol_ret)
                # fade
                fad_ret = -fwd if imb > 0 else fwd
                fade_vals.append(fad_ret)
                
            print(f"  Session: {sess:6s} | FOLLOW: {pack(follow_vals)} | FADE: {pack(fade_vals)}")

if __name__ == "__main__":
    main()
