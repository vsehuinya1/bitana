import sqlite3
conn = sqlite3.connect("/root/bitana/storage/v6_telemetry.db")
c = conn.cursor()

# Trade entries with their shadow exit outcomes
c.execute("""SELECT te.symbol, te.side, te.entry_time, te.decile, te.aggression,
           se.shadow_name, se.shadow_r, se.actual_exit_r, se.delta_r
FROM trade_entries te
LEFT JOIN shadow_exits se ON te.trade_uuid = se.trade_uuid
WHERE te.entry_time > '2026-06-01'
ORDER BY te.entry_time DESC LIMIT 50""")
print("Recent v6 entries with shadow exits:")
for row in c.fetchall():
    print(row)

# Shadow exits by shadow_name for last 30 days
import time
thirty_days_ago = int(time.time()) - 30*86400
c.execute("""SELECT shadow_name, COUNT(*) as n, AVG(shadow_r) as avg_shadow_r, 
           AVG(actual_exit_r) as avg_actual_r, AVG(delta_r) as avg_delta
FROM shadow_exits 
WHERE strftime('%s', trigger_time) > ? AND actual_exit_r IS NOT NULL
GROUP BY shadow_name ORDER BY n DESC""", (str(thirty_days_ago),))
print("\nshadow_exits last 30 days:")
for row in c.fetchall():
    print(row)

conn.close()