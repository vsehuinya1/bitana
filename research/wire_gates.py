#!/usr/bin/env python3
"""wire_gates.py — one-shot G0 wiring (2026-08-22).
Creates VIEW gate_g0 in the shadow DB with FROZEN pre-registered thresholds.
No writer changes, no service restart. Idempotent (CREATE OR REPLACE)."""
import sqlite3

DB = '/root/bitana/storage/signal_shadow.db'
# ---- FROZEN G0 CONSTANTS (pre-registered; do NOT re-fit) -------------------
Q1_RVOL   = 0.0648   # btc_realized_vol_24h q1 boundary = mean(0.0926)*0.7, sample thru 2026-08-22
ADX_HI    = 35.0     # BTC ADX high-trend boundary
OI_UP     = 1.0      # oi_delta_30m_pct >= +1% fade-block
FUND_BP   = 0.0001   # funding_rate_symbol >= 1bp asia_pump short block
FORWARD_FROM = '2026-08-22'  # reads count only trades opened after wiring

sql = f"""
CREATE VIEW IF NOT EXISTS gate_g0 AS
SELECT
  id, strategy, symbol, side, session, entry_time,
  pnl_atr, stop_atr, would_live_accept,
  btc_adx, btc_realized_vol_24h, oi_delta_30m_pct, funding_rate_symbol,
  (side='SHORT' AND btc_adx >= {ADX_HI})                                  AS arm_adx35,
  (side='SHORT' AND btc_realized_vol_24h <= {Q1_RVOL})                    AS arm_rvolq1,
  (side='SHORT' AND oi_delta_30m_pct >= {OI_UP})                          AS arm_oi_p1,
  (side='LONG'  AND oi_delta_30m_pct <= -1.0)                             AS arm_oi_flush_long,
  (side='SHORT' AND strategy LIKE 'asia_pump%' AND funding_rate_symbol >= {FUND_BP}) AS arm_fund1bp,
  (side='LONG'  AND session='late')                                       AS arm_late_long,
  (strategy='burst_follow' AND side='SHORT')                              AS arm_burst_s,
  status='closed'                                                         AS is_closed,
  entry_time >= '{FORWARD_FROM}'                                          AS is_forward
FROM shadow_trades;
"""
con = sqlite3.connect(DB, timeout=60)
con.execute("PRAGMA busy_timeout=60000")
con.executescript(sql)
c = con.cursor()

print("VIEW gate_g0 created/updated. Frozen constants:")
print(f"  Q1_RVOL={Q1_RVOL}  ADX_HI={ADX_HI}  OI_UP=+{OI_UP}%  FUND_BP={FUND_BP*10000:.0f}bp  FORWARD_FROM={FORWARD_FROM}")

# smoke test: reconcile against residual run (allow small live-DB drift)
checks = [
 ("arm_adx35 (shorts)", "arm_adx35=1"),
 ("arm_rvolq1 (shorts)", "arm_rvolq1=1"),
 ("arm_oi_p1 (shorts)", "arm_oi_p1=1"),
 ("arm_fund1bp", "arm_fund1bp=1"),
 ("arm_burst_s", "arm_burst_s=1"),
]
for tag, cond in checks:
    n, S = c.execute(f"SELECT COUNT(*), COALESCE(SUM(pnl_atr),0) FROM gate_g0 WHERE is_closed=1 AND {cond}").fetchone()
    print(f"  {tag:22s} n={n:6d} sum={S:+9.1f} E={S/n if n else 0:+.4f}")
n_all = c.execute("SELECT COUNT(*) FROM gate_g0 WHERE is_closed=1").fetchone()[0]
n_fwd = c.execute("SELECT COUNT(*) FROM gate_g0 WHERE is_forward=1").fetchone()[0]
print(f"  closed total={n_all}  forward-window rows={n_fwd} (expect ~0 now)")
con.close()
print("OK")
