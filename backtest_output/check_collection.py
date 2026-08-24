"""Ad-hoc: verify live data collection rate and research-table freshness."""
import sqlite3
from datetime import datetime, timezone

now = datetime.now(timezone.utc)
db = sqlite3.connect("/root/bitana/storage/v5_forward_test.db")

print("=== LIVE trades (v65-revert) ===")
n = db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
print("total:", n)
for r in db.execute(
    "SELECT symbol, entry_time, exit_time, pnl_r, exit_reason "
    "FROM trades ORDER BY entry_time DESC LIMIT 8"
).fetchall():
    print("  %-10s %s -> %s  R=%+.3f  %s" % (r[0], str(r[1])[:16], str(r[2])[:16], r[3] or 0, r[4]))

print("\n=== SHADOW trades (Asia) ===")
ns = db.execute("SELECT COUNT(*) FROM shadow_trades").fetchone()[0]
print("total shadow_trades:", ns)
for r in db.execute(
    "SELECT symbol, entry_time, exit_time, pnl_r "
    "FROM shadow_trades ORDER BY entry_time DESC LIMIT 8"
).fetchall():
    print("  %-10s %s -> %s  R=%+.3f" % (r[0], str(r[1])[:16], str(r[2])[:16], r[3] or 0))
print("open shadow_positions:", db.execute("SELECT COUNT(*) FROM shadow_positions").fetchone()[0])
print("open live positions:", db.execute("SELECT COUNT(*) FROM open_positions").fetchone()[0])

print("\n=== entries per day (live | shadow) ===")
live = dict(db.execute("SELECT substr(entry_time,1,10), COUNT(*) FROM trades GROUP BY 1").fetchall())
shad = dict(db.execute("SELECT substr(entry_time,1,10), COUNT(*) FROM shadow_trades GROUP BY 1").fetchall())
for d in sorted(set(live) | set(shad), reverse=True)[:18]:
    print("  %s: live=%-3d shadow=%-3d" % (d, live.get(d, 0), shad.get(d, 0)))

# liq_cache freshness — is daily liq context current?
print("\n=== liq_cache freshness (newest date per a few symbols) ===")
for sym in ("ETHUSDT", "ZECUSDT", "DASHUSDT", "BNBUSDT"):
    r = db.execute("SELECT MAX(date) FROM liq_cache WHERE symbol=?", (sym,)).fetchone()
    print("  %-10s newest liq_cache date: %s" % (sym, r[0]))
