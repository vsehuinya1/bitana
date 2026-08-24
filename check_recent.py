import sqlite3
conn = sqlite3.connect('storage/v5_forward_test.db')
c = conn.cursor()
c.execute('SELECT COUNT(*), AVG(pnl_r), SUM(pnl_r) FROM trades WHERE entry_time > datetime(now, -7 days)')
print('v5 live trades last 7d:', c.fetchall())
conn.close()
