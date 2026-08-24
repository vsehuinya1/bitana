import sqlite3
conn = sqlite3.connect("/root/bitana/storage/signal_shadow.db")
c = conn.cursor()

# setup_snapshots by strategy and session
c.execute("""SELECT session, 
    COUNT(*) as n,
    AVG(fwd_atr_96) as avg_96,
    SUM(fwd_atr_96) as sum_96,
    AVG(fwd_atr_24) as avg_24,
    SUM(fwd_atr_24) as sum_24
FROM setup_snapshots 
WHERE v_confirms3 = 1 AND fwd_atr_96 IS NOT NULL
GROUP BY session ORDER BY n DESC""")
print("setup_snapshots v_confirms3=1 by session:")
for row in c.fetchall():
    print(row)

# setup_snapshots by strategy (need to check what strategy columns exist)
c.execute("PRAGMA table_info(setup_snapshots)")
print("\nsetup_snapshots schema:")
for row in c.fetchall():
    print(row)

# Count by various flags
c.execute("""SELECT 
    SUM(CASE WHEN v_strict = 1 THEN 1 ELSE 0 END) as v_strict,
    SUM(CASE WHEN v_confirms3 = 1 THEN 1 ELSE 0 END) as v_confirms3,
    SUM(CASE WHEN cascade_active = 1 THEN 1 ELSE 0 END) as cascade_active,
    COUNT(*) as total
FROM setup_snapshots""")
print("\nsetup_snapshots flag counts:")
for row in c.fetchall():
    print(row)

conn.close()