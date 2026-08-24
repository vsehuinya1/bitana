import sqlite3
conn = sqlite3.connect("/root/bitana/storage/v5_forward_test.db")
c = conn.cursor()

# Check shadow_trades table
c.execute("PRAGMA table_info(shadow_trades)")
print("shadow_trades schema:")
for row in c.fetchall():
    print(row)

# Recent shadow_trades
c.execute("""SELECT strategy_version, COUNT(*) as n, AVG(pnl_r) as avg_r, SUM(pnl_r) as total_r,
           SUM(CASE WHEN pnl_r > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as wr
FROM shadow_trades WHERE pnl_r IS NOT NULL
GROUP BY strategy_version ORDER BY n DESC""")
print("\nshadow_trades (closed):")
for row in c.fetchall():
    print(row)

# Check equity snapshots for recent days
c.execute("SELECT timestamp, equity FROM equity_snapshots ORDER BY timestamp DESC LIMIT 20")
print("\nequity_snapshots (recent):")
for row in c.fetchall():
    print(row)

conn.close()