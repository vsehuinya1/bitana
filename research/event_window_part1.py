#!/usr/bin/env python3
"""Event-window read + cross-cluster loss-streak detector on shadow_trades. Research read: full closed set + live-accept variant both reported."""
import sqlite3, json, urllib.request, time
from datetime import datetime, timedelta, timezone

DB = 'storage/signal_shadow.db'
con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()
cur.row_factory = sqlite3.Row

def rows(q, *a):
    return cur.execute(q, a).fetchall()

# ---- 0. data quality: cluster_bucket coverage
q = rows("SELECT COUNT(*) n, SUM(CASE WHEN cluster_bucket IS NULL THEN 1 ELSE 0 END) nulls, COUNT(DISTINCT cluster_bucket) nb FROM shadow_trades WHERE status='closed' AND strategy IN ('burst_follow','ny_flush_buy_4h') AND side='LONG'")
r = q[0]
print(f"[DQ] closed LONG bf+nyflush: n={r['n']} cluster_bucket NULL={r['nulls']} distinct={r['nb']}")
# sample buckets
for x in rows("SELECT cluster_bucket, COUNT(*) n FROM shadow_trades WHERE status='closed' AND strategy='burst_follow' AND side='LONG' AND cluster_bucket IS NOT NULL GROUP BY cluster_bucket ORDER BY n DESC LIMIT 5"):
    print("   bucket sample:", x['cluster_bucket'][:60], "n=", x['n'])

def parse(ts):
    return datetime.fromisoformat(ts)

# ---- 1. fetch BTC 5m klines for spike-dating (empirical event anchors)
def fetch_klines(interval, start_ms, end_ms):
    out = []
    s = start_ms
    while s < end_ms:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval={interval}&startTime={s}&endTime={end_ms}&limit=1000"
        with urllib.request.urlopen(url, timeout=30) as f:
            data = json.loads(f.read())
        if not data: break
        out += data
        s = data[-1][6] + 1
        if len(data) < 1000: break
        time.sleep(0.15)
    return out

start = int(datetime(2026,6,27,tzinfo=timezone.utc).timestamp()*1000)
end   = int(datetime(2026,8,29,tzinfo=timezone.utc).timestamp()*1000)
k5 = fetch_klines('5m', start, end)
print(f"[DATA] BTC 5m bars fetched: {len(k5)}")
# top range spikes
spikes = []
for k in k5:
    o,h,l,c,vol = float(k[1]),float(k[2]),float(k[3]),float(k[4]),float(k[5])
    rng = (h-l)/c*100
    ts = datetime.fromtimestamp(k[0]/1000, tz=timezone.utc)
    spikes.append((rng, ts, vol))
spikes.sort(reverse=True)
print("[SPIKES] top 18 five-minute ranges (rng%, UTC, vol):")
for rng, ts, vol in spikes[:18]:
    print(f"   {rng:5.2f}%  {ts:%m-%d %H:%M}  vol={vol:,.0f}")

# candidate event anchors (formulaic schedule; verify against spikes)
events = [
    ("NFP-Jul3",       "2026-07-03T12:30:00"),
    ("CPI-Jul14",      "2026-07-14T12:30:00"),
    ("FOMC-Jul29",     "2026-07-29T18:00:00"),
    ("NFP-Aug7",       "2026-08-07T12:30:00"),
    ("CPI-Aug12",      "2026-08-12T12:30:00"),
    ("JH-Aug21~",      "2026-08-21T14:00:00"),
]
print("[EVENTS] spike within ±20min of anchor? (max 5m rng in window)")
anchors = []
for name, ts in events:
    t0 = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    best = max((s for s in spikes if abs((s[1]-t0).total_seconds()) <= 1200), default=None)
    ok = f"{best[0]:.2f}% @ {best[1]:%H:%M}" if best else "none"
    print(f"   {name:12s} {ts} -> {ok}")
    anchors.append((name, t0))
con.close()
with open('/tmp/anchors.json','w') as f:
    json.dump([[n, t.isoformat()] for n,t in anchors], f)
with open('/tmp/k5.json','w') as f:
    json.dump(k5, f)
print("anchors+klines saved")
