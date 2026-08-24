import sqlite3
conn = sqlite3.connect("/root/bitana/storage/v5_forward_test.db")
c = conn.cursor()

# v5 trades by session
c.execute("""SELECT 
    CASE 
        WHEN CAST(strftime('%H', entry_time) AS INTEGER) BETWEEN 0 AND 7 THEN 'asia'
        WHEN CAST(strftime('%H', entry_time) AS INTEGER) BETWEEN 8 AND 13 THEN 'london'
        WHEN CAST(strftime('%H', entry_time) AS INTEGER) BETWEEN 14 AND 20 THEN 'ny'
        ELSE 'late'
    END as session,
    COUNT(*) as n,
    AVG(pnl_r) as avg_r,
    SUM(pnl_r) as total_r,
    SUM(CASE WHEN pnl_r > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as wr
FROM trades WHERE pnl_r IS NOT NULL
GROUP BY session ORDER BY n DESC""")
print("v5 trades by session:")
for row in c.fetchall():
    print(row)

# Last 20 v5 trades detail
c.execute("""SELECT symbol, side, entry_time, exit_time, pnl_r, exit_reason, btc_aligned
FROM trades WHERE pnl_r IS NOT NULL ORDER BY exit_time DESC LIMIT 20""")
print("\nLast 20 v5 trades:")
for row in c.fetchall():
    print(row)

conn.close()