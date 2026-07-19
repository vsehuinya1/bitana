import sqlite3
import pandas as pd
import numpy as np
import warnings
from datetime import datetime

# Silence groupby deprecation warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

DB_PATH = "storage/signal_shadow.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    
    # Load shadow trades joined with burst snapshots to get complete telemetry
    query = """
    SELECT 
        t.id, 
        t.strategy, 
        t.symbol, 
        t.side, 
        t.entry_time, 
        t.exit_time, 
        t.pnl_atr, 
        t.exit_reason,
        b.session, 
        b.hour, 
        b.decile, 
        b.aggression, 
        b.liq_imbalance_30m AS liq_imb, 
        b.burst_volume_30m AS burst_vol_30m,
        b.cascade_strength, 
        b.vol_z, 
        b.imb_z, 
        b.breakout_distance_pct, 
        b.body_ratio, 
        b.impulse_pct, 
        b.above_ema, 
        b.breakout, 
        b.n_confirms
    FROM shadow_trades t
    JOIN burst_snapshots b ON t.symbol = b.symbol AND t.entry_time = b.bar_time
    WHERE t.status = 'closed'
    """
    df = pd.read_sql_query(query, conn)
    
    if df.empty:
        print("No closed trades found in shadow_trades matching burst_snapshots.")
        return
        
    print(f"Total closed trades loaded: {len(df)}")
    
    # Parse entry time and extract day of week
    df['entry_time_dt'] = pd.to_datetime(df['entry_time'])
    df['day_of_week'] = df['entry_time_dt'].dt.day_name()
    df['day_num'] = df['entry_time_dt'].dt.dayofweek # 0 is Monday
    df['is_weekend'] = df['day_num'].isin([5, 6]).astype(int)
    
    # Win rate and statistics helper
    def get_stats(group):
        n = len(group)
        if n == 0:
            return pd.Series({'n': 0, 'sum_R': 0.0, 'mean_R': 0.0, 'win_rate': 0.0})
        win_rate = (group['pnl_atr'] > 0).sum() / n * 100
        return pd.Series({
            'n': n,
            'sum_R': group['pnl_atr'].sum(),
            'mean_R': group['pnl_atr'].mean(),
            'win_rate': win_rate
        })

    # Overall strategy breakdown
    print("\n======================================================================")
    print("OVERALL STRATEGY PERFORMANCE")
    print("======================================================================")
    print(df.groupby('strategy').apply(get_stats).round(3))
    
    # Strategy by Session breakdown
    print("\n======================================================================")
    print("STRATEGY BY SESSION BREAKDOWN")
    print("======================================================================")
    print(df.groupby(['strategy', 'session']).apply(get_stats).round(3))
    
    # Strategy by Day of Week breakdown
    print("\n======================================================================")
    print("STRATEGY BY DAY OF WEEK BREAKDOWN")
    print("======================================================================")
    # Order days logically Mon-Sun
    day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    dow_stats = df.groupby(['strategy', 'day_of_week']).apply(get_stats).reset_index()
    dow_stats['day_of_week'] = pd.Categorical(dow_stats['day_of_week'], categories=day_order, ordered=True)
    print(dow_stats.sort_values(['strategy', 'day_of_week']).to_string(index=False))
    
    # Let's inspect late_fade specifically
    lf_df = df[df['strategy'] == 'late_fade']
    if not lf_df.empty:
        print("\n======================================================================")
        print("LATE_FADE DEEPER DIVE")
        print("======================================================================")
        
        print("\n--- By Side ---")
        print(lf_df.groupby('side').apply(get_stats).round(3))
        
        print("\n--- By Decile ---")
        print(lf_df.groupby('decile').apply(get_stats).round(3))
        
        print("\n--- By Weekend (is_weekend) ---")
        print(lf_df.groupby('is_weekend').apply(get_stats).round(3))
        
        print("\n--- By Liquidation Imbalance ---")
        lf_df['liq_imb_bin'] = pd.cut(lf_df['liq_imb'], bins=[-1.1, -0.5, 0.0, 0.5, 1.1])
        print(lf_df.groupby('liq_imb_bin', observed=False).apply(get_stats).round(3))
        
        print("\n--- By Burst Vol 30m (quartiles) ---")
        if lf_df['burst_vol_30m'].nunique() > 1:
            lf_df['vol_q'] = pd.qcut(lf_df['burst_vol_30m'], q=4, labels=['Q1', 'Q2', 'Q3', 'Q4'])
            print(lf_df.groupby('vol_q', observed=False).apply(get_stats).round(3))

    # Let's inspect nony_momentum specifically
    mom_df = df[df['strategy'] == 'nony_momentum']
    if not mom_df.empty:
        print("\n======================================================================")
        print("NONY_MOMENTUM DEEPER DIVE")
        print("======================================================================")
        
        print("\n--- By Session ---")
        print(mom_df.groupby('session').apply(get_stats).round(3))
        
        print("\n--- By Side ---")
        print(mom_df.groupby('side').apply(get_stats).round(3))
        
        print("\n--- By Decile ---")
        print(mom_df.groupby('decile').apply(get_stats).round(3))
        
        print("\n--- By Weekend (is_weekend) ---")
        print(mom_df.groupby('is_weekend').apply(get_stats).round(3))
        
        print("\n--- By Hour ---")
        print(mom_df.groupby('hour').apply(get_stats).round(3))
        
        print("\n--- By Liquidation Imbalance ---")
        mom_df['liq_imb_bin'] = pd.cut(mom_df['liq_imb'], bins=[-1.1, -0.5, 0.0, 0.5, 1.1])
        print(mom_df.groupby('liq_imb_bin', observed=False).apply(get_stats).round(3))
        
        print("\n--- By Burst Vol 30m (quartiles) ---")
        if mom_df['burst_vol_30m'].nunique() > 1:
            mom_df['vol_q'] = pd.qcut(mom_df['burst_vol_30m'], q=4, labels=['Q1', 'Q2', 'Q3', 'Q4'])
            print(mom_df.groupby('vol_q', observed=False).apply(get_stats).round(3))

        print("\n--- By Cascade Strength (bins) ---")
        mom_df['cascade_bin'] = pd.cut(mom_df['cascade_strength'], bins=[0, 0.2, 0.5, 1.0, 5.0, 100.0])
        print(mom_df.groupby('cascade_bin', observed=False).apply(get_stats).round(3))

        print("\n--- By Number of Confirmations ---")
        print(mom_df.groupby('n_confirms').apply(get_stats).round(3))

if __name__ == "__main__":
    main()
