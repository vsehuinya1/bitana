import sqlite3
conn = sqlite3.connect("/root/bitana/storage/signal_shadow.db")
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type=table AND name LIKE %btc%")
print("BTC tables:", c.fetchall())
c.execute("SELECT name FROM sqlite_master WHERE type=table AND name LIKE %regime%")
print("Regime tables:", c.fetchall())
conn.close()
