#!/usr/bin/env python3
"""
Asia Session Live Trade Report — runs at 08:00 UTC
Lists closed trades from Asia session (00:00-08:00 UTC) by pair and R,
and lists open positions with current R.
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

def fetch_closed_asia_trades(conn, start_utc, end_utc):
    """Fetch trades where signal_time falls in Asia window."""
    query = """
    SELECT t.trade_uuid, t.symbol, t.side, t.entry_price, t.exit_price,
           t.pnl_r, t.exit_reason, t.timestamp as exit_time,
           s.signal_time, s.engine
    FROM trades t
    LEFT JOIN signals s ON t.trade_uuid = s.trade_uuid
    WHERE s.signal_time >= ? AND s.signal_time < ?
    ORDER BY s.signal_time
    """
    cur = conn.execute(query, (start_utc.isoformat(), end_utc.isoformat()))
    rows = cur.fetchall()
    return rows

def fetch_open_positions(conn):
    """Fetch open positions with current unrealized R."""
    query = """
    SELECT p.trade_uuid, p.symbol, p.side, p.entry_price, p.quantity,
           p.leverage, p.stop_price, p.initial_stop, p.risk_r,
           p.unrealized_pnl, p.entry_time, s.signal_time, s.engine
    FROM positions p
    LEFT JOIN signals s ON p.trade_uuid = s.trade_uuid
    WHERE p.state = 'OPEN'
    ORDER BY p.entry_time
    """
    cur = conn.execute(query)
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
    
    print(f"=== ASIA SESSION REPORT ===")
    print(f"Window: {start_utc.strftime('%Y-%m-%d %H:%M')} – {end_utc.strftime('%H:%M')} UTC")
    print(f"Generated: {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print()
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Closed trades in Asia session
    closed = fetch_closed_asia_trades(conn, start_utc, end_utc)
    
    print(f"CLOSED TRADES (Asia session): {len(closed)}")
    total_r = 0.0
    if closed:
        by_symbol = {}
        for row in closed:
            sym = row['symbol']
            if sym not in by_symbol:
                by_symbol[sym] = []
            by_symbol[sym].append(row)
        
        for sym in sorted(by_symbol.keys()):
            trades = by_symbol[sym]
            sym_total = sum(t['pnl_r'] for t in trades)
            total_r += sym_total
            print(f"\n  {sym} (total R={format_r(sym_total)}):")
            for t in trades:
                sig_time = t['signal_time'][:19] if t['signal_time'] else '?'
                exit_time = t['exit_time'][:19] if t['exit_time'] else '?'
                print(f"    {t['trade_uuid'][:8]}  {t['side']:5}  "
                      f"entry={t['entry_price']:.4f}  exit={t['exit_price']:.4f}  "
                      f"R={format_r(t['pnl_r'])}  {t['exit_reason']}  "
                      f"sig={sig_time}  exit={exit_time}  [{t['engine']}]")
    else:
        print("  (none)")
    print(f"\n  TOTAL R (Asia session): {format_r(total_r)}")
    
    print()
    
    # Open positions
    open_pos = fetch_open_positions(conn)
    
    print(f"OPEN POSITIONS: {len(open_pos)}")
    if open_pos:
        for p in open_pos:
            # Calculate current R from unrealized_pnl / risk_r
            risk_r = p['risk_r'] or 1.0
            unrealized_usd = p['unrealized_pnl'] or 0.0
            current_r = unrealized_usd / risk_r if risk_r != 0 else 0.0
            
            sig_time = p['signal_time'][:19] if p['signal_time'] else '?'
            entry_time = p['entry_time'][:19] if p['entry_time'] else '?'
            print(f"  {p['trade_uuid'][:8]}  {p['symbol']}  {p['side']:5}  "
                  f"entry={p['entry_price']:.4f}  stop={p['stop_price']:.4f}  "
                  f"risk_R={risk_r:.3f}  current_R={format_r(current_r)}  "
                  f"unrealized_usd={unrealized_usd:+.2f}  "
                  f"sig={sig_time}  entry={entry_time}  [{p['engine']}]")
    else:
        print("  (none)")
    
    conn.close()

if __name__ == "__main__":
    main()