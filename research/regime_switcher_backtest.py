import sqlite3
import pandas as pd
import numpy as np
import warnings

warnings.filterwarnings("ignore")

DB_PATH = "storage/signal_shadow.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    
    # Load burst snapshots
    query = """
    SELECT 
        bar_time, session, hour, fwd_atr_12, fwd_atr_24, fwd_atr_96, 
        liq_imbalance_30m, burst_volume_30m, cascade_strength, vol_z, imb_z,
        above_ema, decile
    FROM burst_snapshots
    WHERE bars_tracked >= 12
      AND liq_imbalance_30m IS NOT NULL
      AND abs(liq_imbalance_30m) >= 0.3
    """
    df = pd.read_sql_query(query, conn)
    
    # Parse entry time
    df['entry_time_dt'] = pd.to_datetime(df['bar_time'])
    df['day_of_week'] = df['entry_time_dt'].dt.day_name()
    df['day_num'] = df['entry_time_dt'].dt.dayofweek
    df['is_weekend'] = df['day_num'].isin([5, 6]).astype(int)
    
    # Calculate signed returns
    # Standard convention:
    # - If we FOLLOW the momentum (trade with liquidations): when price dumps (long-liq, imb > 0), we go SHORT.
    #   So follow_return = -fwd_atr
    # - If we FADE the momentum (mean reversion): when price dumps (long-liq, imb > 0), we go LONG.
    #   So fade_return = +fwd_atr
    # Let's define the forward return relative to the imbalance sign:
    # If imb > 0 (long-liq, dump):
    #   FOLLOW: SHORT trade -> return is -fwd_atr
    #   FADE: LONG trade -> return is +fwd_atr
    # If imb < 0 (short-liq, pump):
    #   FOLLOW: LONG trade -> return is +fwd_atr
    #   FADE: SHORT trade -> return is -fwd_atr
    # So:
    # follow_ret = -fwd_atr if imb > 0 else +fwd_atr = -fwd_atr * sign(imb)
    # fade_ret = +fwd_atr if imb > 0 else -fwd_atr = +fwd_atr * sign(imb)
    
    # Let's check the sign(imb)
    df['imb_sign'] = np.sign(df['liq_imbalance_30m'])
    
    # Define returns for 1h, 2h, and 8h horizons
    for h in [12, 24, 96]:
        df[f'follow_ret_{h}'] = -df[f'fwd_atr_{h}'] * df['imb_sign']
        df[f'fade_ret_{h}'] = df[f'fwd_atr_{h}'] * df['imb_sign']
        
    print(f"Loaded {len(df)} snapshots for backtest.")
    
    # We will test different regime rules:
    # Rule 0: Always FADE
    # Rule 1: Always FOLLOW
    # Rule 2: User's Current Blanket Cover:
    #   - London: FADE
    #   - NY: FOLLOW
    #   - ASIA: FOLLOW
    #   - Late: FOLLOW
    # Rule 3: Session Optimized:
    #   - We pick the best action (FOLLOW or FADE) for each session based on historical data.
    # Rule 4: Session + Trend (EMA) Optimized:
    #   - We pick the best action based on Session AND whether above_ema is 0 or 1.
    # Rule 5: Session + Day of Week (Weekend vs Weekday) Optimized:
    #   - We pick the best action based on Session AND is_weekend.
    
    def evaluate_rules(horizon):
        ret_col_follow = f'follow_ret_{horizon}'
        ret_col_fade = f'fade_ret_{horizon}'
        
        # Helper to compute stats for a series of returns
        def stats(rets):
            rets = rets.dropna()
            n = len(rets)
            if n == 0:
                return 0.0, 0.0, 0.0
            return rets.sum(), rets.mean(), (rets > 0).sum() / n * 100
        
        print(f"\n==========================================================")
        print(f"HORIZON: {horizon // 12} Hour(s) ({horizon} bars)")
        print(f"==========================================================")
        
        # 1. ALWAYS FADE
        fade_sum, fade_mean, fade_wr = stats(df[ret_col_fade])
        print(f"Always FADE:   Sum PnL = {fade_sum:+.2f}R | Mean = {fade_mean:+.3f}R | WR = {fade_wr:.1f}%")
        
        # 2. ALWAYS FOLLOW
        fol_sum, fol_mean, fol_wr = stats(df[ret_col_follow])
        print(f"Always FOLLOW: Sum PnL = {fol_sum:+.2f}R | Mean = {fol_mean:+.3f}R | WR = {fol_wr:.1f}%")
        
        # 3. USER BLANKET: Fade London, Follow NY, ASIA + Late
        user_rets = []
        for _, row in df.iterrows():
            sess = row['session']
            if sess == 'london':
                user_rets.append(row[ret_col_fade])
            elif sess in ['ny', 'asia', 'late']:
                user_rets.append(row[ret_col_follow])
            else:
                user_rets.append(np.nan)
        user_sum, user_mean, user_wr = stats(pd.Series(user_rets))
        print(f"User Blanket:  Sum PnL = {user_sum:+.2f}R | Mean = {user_mean:+.3f}R | WR = {user_wr:.1f}%")
        
        # 4. OPTIMIZED SESSION-ONLY
        # Find best action per session
        print("\nSession-by-session breakdown:")
        opt_session_rets = []
        opt_rules = {}
        for sess in ['asia', 'london', 'ny', 'late']:
            sess_df = df[df['session'] == sess]
            f_sum, f_mean, _ = stats(sess_df[ret_col_follow])
            d_sum, d_mean, _ = stats(sess_df[ret_col_fade])
            
            best_action = 'FOLLOW' if f_sum > d_sum else 'FADE'
            opt_rules[sess] = best_action
            
            best_rets = sess_df[ret_col_follow] if best_action == 'FOLLOW' else sess_df[ret_col_fade]
            opt_session_rets.extend(best_rets.dropna().tolist())
            
            print(f"  {sess:6s} -> FOLLOW: {f_sum:+.1f}R | FADE: {d_sum:+.1f}R | Selected: {best_action}")
            
        opt_sum, opt_mean, opt_wr = stats(pd.Series(opt_session_rets))
        print(f"Session Opt:   Sum PnL = {opt_sum:+.2f}R | Mean = {opt_mean:+.3f}R | WR = {opt_wr:.1f}% (Rules: {opt_rules})")
        
        # 5. OPTIMIZED SESSION + TREND (above_ema)
        print("\nSession + Trend (above_ema) breakdown:")
        opt_trend_rets = []
        trend_rules = {}
        for sess in ['asia', 'london', 'ny', 'late']:
            for ema in [0, 1]:
                sub_df = df[(df['session'] == sess) & (df['above_ema'] == ema)]
                f_sum, _, _ = stats(sub_df[ret_col_follow])
                d_sum, _, _ = stats(sub_df[ret_col_fade])
                
                best_action = 'FOLLOW' if f_sum > d_sum else 'FADE'
                trend_rules[(sess, ema)] = best_action
                
                best_rets = sub_df[ret_col_follow] if best_action == 'FOLLOW' else sub_df[ret_col_fade]
                opt_trend_rets.extend(best_rets.dropna().tolist())
                
                ema_label = "Below EMA" if ema == 0 else "Above EMA"
                print(f"  {sess:6s} + {ema_label:9s} -> FOLLOW: {f_sum:+.1f}R | FADE: {d_sum:+.1f}R | Selected: {best_action}")
                
        trend_sum, trend_mean, trend_wr = stats(pd.Series(opt_trend_rets))
        print(f"Trend Opt:     Sum PnL = {trend_sum:+.2f}R | Mean = {trend_mean:+.3f}R | WR = {trend_wr:.1f}%")
        
        # 6. OPTIMIZED SESSION + WEEKEND (is_weekend)
        print("\nSession + Weekend breakdown:")
        opt_wk_rets = []
        wk_rules = {}
        for sess in ['asia', 'london', 'ny', 'late']:
            for wk in [0, 1]:
                sub_df = df[(df['session'] == sess) & (df['is_weekend'] == wk)]
                f_sum, _, _ = stats(sub_df[ret_col_follow])
                d_sum, _, _ = stats(sub_df[ret_col_fade])
                
                best_action = 'FOLLOW' if f_sum > d_sum else 'FADE'
                wk_rules[(sess, wk)] = best_action
                
                best_rets = sub_df[ret_col_follow] if best_action == 'FOLLOW' else sub_df[ret_col_fade]
                opt_wk_rets.extend(best_rets.dropna().tolist())
                
                wk_label = "Weekend" if wk == 1 else "Weekday"
                print(f"  {sess:6s} + {wk_label:7s} -> FOLLOW: {f_sum:+.1f}R | FADE: {d_sum:+.1f}R | Selected: {best_action}")
                
        wk_sum, wk_mean, wk_wr = stats(pd.Series(opt_wk_rets))
        print(f"Weekend Opt:   Sum PnL = {wk_sum:+.2f}R | Mean = {wk_mean:+.3f}R | WR = {wk_wr:.1f}%")

    for h in [12, 24, 96]:
        evaluate_rules(h)

if __name__ == "__main__":
    main()
