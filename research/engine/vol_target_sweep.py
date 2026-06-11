import pandas as pd
import numpy as np

def run_sweep(csv_path):
    df = pd.read_csv(csv_path)
    # Ensure entry_atr exists and is valid
    if 'entry_atr' not in df.columns:
        print("Error: entry_atr missing from CSV")
        return

    # ATR% normalization
    df['atr_pct'] = (df['entry_atr'] / df['entry_price']) * 100
    
    results = []
    
    # Baseline (Flat 4%)
    baseline_r = df['pnl_r'].sum()
    baseline_win_r = df[df['pnl_r'] > 0]['pnl_r'].sum()
    baseline_loss_r = abs(df[df['pnl_r'] < 0]['pnl_r'].sum())
    baseline_pf = baseline_win_r / baseline_loss_r if baseline_loss_r > 0 else float('inf')
    
    results.append({
        'target_atr_pct': 'Baseline (Flat)',
        'total_r': round(baseline_r, 2),
        'pf': round(baseline_pf, 2),
        'avg_r': round(df['pnl_r'].mean(), 3),
        'std_r': round(df['pnl_r'].std(), 3)
    })

    # Sweep Targets
    targets = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0]
    for target in targets:
        # Normalize risk: new_risk = 0.04 * (target / current_atr_pct)
        # Note: In a real environment, we'd cap this (e.g. max 10% risk)
        df['norm_weight'] = (target / df['atr_pct']).clip(0.1, 5.0) # avoid division by zero or extreme sizes
        
        # New PnL R is same but weighted by normalized size relative to original 0.04
        # Wait, PnL R IS already normalized by risk (1R = 4%).
        # So Normalized PnL R = pnl_r * weight_factor
        df['norm_pnl_r'] = df['pnl_r'] * df['norm_weight']
        
        total_r = df['norm_pnl_r'].sum()
        win_r = df[df['norm_pnl_r'] > 0]['norm_pnl_r'].sum()
        loss_r = abs(df[df['norm_pnl_r'] < 0]['norm_pnl_r'].sum())
        pf = win_r / loss_r if loss_r > 0 else float('inf')
        
        results.append({
            'target_atr_pct': f"{target}% ATR",
            'total_r': round(total_r, 2),
            'pf': round(pf, 2),
            'avg_r': round(df['norm_pnl_r'].mean(), 3),
            'std_r': round(df['norm_pnl_r'].std(), 3)
        })

    out_df = pd.DataFrame(results)
    print("\nVOL-TARGETING SWEEP RESULTS:")
    print(out_df.to_string(index=False))
    return out_df

if __name__ == "__main__":
    run_sweep('backtest_output/v5_full_backtest_trades.csv')
