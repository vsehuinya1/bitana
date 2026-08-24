#!/usr/bin/env python3
"""
Asia Session FINAL Live Trade Report — runs at 12:00 UTC (4h after Asia close)
Shows COMPLETE session PnL including trades that were still open at 08:00 report.
"""
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

DB_PATH = "/root/bitana/data/bitana-live-burst.db"

def get_asia_window():
    """Return (start_utc, end_utc) for today's Asia session 00:00-08:00 UTC."""
    now = datetime.now(timezone.utc)
    today = now.date()
    start = datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=8)
    return start, end

def fetch_all_session_trades(conn, start_utc, end_utc):
    """Fetch ALL trades where signal_time falls in Asia window (closed + open)."""
    query = """
    SELECT t.trade_uuid, t.symbol, t.side, t.entry_price, t.exit_price,
           t.pnl_r, t.exit_reason, t.timestamp as exit_time,
           s.signal_time, s.engine,
           p.state as position_state,
           p.unrealized_pnl, p.risk_r
    FROM trades t
    LEFT JOIN signals s ON t.trade_uuid = s.trade_uuid
    LEFT JOIN positions p ON t.trade_uuid = p.trade_uuid
    WHERE s.signal_time >= ? AND s.signal_time < ?
    ORDER BY s.signal_time
    """
    cur = conn.execute(query, (start_utc.isoformat(), end_utc.isoformat()))
    rows = cur.fetchall()
    return rows

def format_r(val):
    """Format R value with sign and 3 decimals."""
    if val is None:
        return "—"
    return f"{val:+.3f}"

def main():
    start_utc, end_utc = get_asia_window()
    now_utc = datetime.now(timezone.utc)
    
    print(f"=== ASIA SESSION FINAL REPORT ===")
    print(f"Window: {start_utc.strftime('%Y-%m-%d %H:%M')} – {end_utc.strftime('%H:%M')} UTC")
    print(f"Generated: {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC (4h post-close)")
    print()
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    all_trades = fetch_all_session_trades(conn, start_utc, end_utc)
    
    print(f"ALL TRADES (Asia session): {len(all_trades)}")
    total_r = 0.0
    if all_trades:
        by_symbol = {}
        for row in all_trades:
            sym = row['symbol']
            if sym not in by_symbol:
                by_symbol[sym] = []
            by_symbol[sym].append(row)
        
        for sym in sorted(by_symbol.keys()):
            trades = by_symbol[sym]
            sym_total = 0.0
            print(f"\n  {sym}:")
            for t in trades:
                sig_time = t['signal_time'][:19] if t['signal_time'] else '?'
                engine = t['engine'] or '?'
                
                # Determine final R: use realized pnl_r if closed, else unrealized
                if t['exit_price'] is not None and t['pnl_r'] is not None:
                    # Closed trade
                    final_r = t['pnl_r']
                    status = f"{t['exit_reason']}  exit={t['exit_time'][:19] if t['exit_time'] else '?'}"
                else:
                    # Still open - calculate from unrealized
                    risk_r = t['risk_r'] or 1.0
                    unrealized_usd = t['unrealized_pnl'] or 0.0
                    final_r = unrealized_usd / risk_r if risk_r != 0 else 0.0
                    status = f"OPEN  unrealized_usd={unrealized_usd:+.2f}  risk_R={risk_r:.3f}"
                
                sym_total += final_r
                total_r += final_r
                
                print(f"    {t['trade_uuid'][:8]}  {t['side']:5}  "
                      f"entry={t['entry_price']:.4f}  "
                      f"R={format_r(final_r)}  {status}  "
                      f"sig={sig_time}  [{engine}]")
            
            print(f"    → {sym} total: {format_r(sym_total)}")
    else:
        print("  (none)")
    
    print(f"\n  SESSION TOTAL R (Asia): {format_r(total_r)}")
    
    conn.close()

if __name__ == "__main__":
    main()