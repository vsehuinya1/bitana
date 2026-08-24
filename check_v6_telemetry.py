import sqlite3
conn = sqlite3.connect("/root/bitana/storage/v6_telemetry.db")
c = conn.cursor()

# Check shadow_exits table
c.execute("PRAGMA table_info(shadow_exits)")
print("shadow_exits schema:")
for row in c.fetchall():
    print(row)

# Check regime_snapshots schema
c.execute("PRAGMA table_info(regime_snapshots)")
print("\nregime_snapshots schema:")
for row in c.fetchall():
    print(row)

# Recent shadow_exits
c.execute("""SELECT shadow_name, COUNT(*) as n, AVG(shadow_r) as avg_shadow_r, 
           AVG(actual_exit_r) as avg_actual_r, AVG(delta_r) as avg_delta, 
           AVG(post_trigger_mfe) as avg_mfe
FROM shadow_exits WHERE actual_exit_r IS NOT NULL
GROUP BY shadow_name ORDER BY n DESC""")
print("\nshadow_exits (closed):")
for row in c.fetchall():
    print(row)

# Recent regime snapshots
c.execute("SELECT * FROM regime_snapshots ORDER BY timestamp DESC LIMIT 10")
print("\nregime_snapshots (recent):")
for row in c.fetchall():
    print(row)

conn.close()