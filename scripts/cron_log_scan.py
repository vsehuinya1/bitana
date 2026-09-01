#!/usr/bin/env python3
"""Log evidence scan for daily discrepancy report."""
import json, gzip, collections, re

DAY = "2026-08-31"
BASE = "/root/bitana"
CUR = f"{BASE}/logs/bitana-live-burst.log"
ROT = f"{BASE}/logs/bitana-live-burst.log.1.gz.gz"

def parse(line):
    i = line.find("{")
    if i < 0:
        return None
    try:
        return json.loads(line[i:])
    except Exception:
        return None

def scan(fh, out):
    for line in fh:
        if "Candle closed" in line or "HTTP Request" in line or "Candle" in line[:80]:
            continue
        j = parse(line)
        if not j:
            continue
        ts = str(j.get("timestamp", ""))
        if not ts.startswith(DAY):
            continue
        out.append(j)
    return out

events = []
scan(open(CUR, errors="replace"), events)
with gzip.open(ROT, "rt", errors="replace") as gz:
    scan(gz, events)

events.sort(key=lambda j: j.get("timestamp", ""))
print("total today events:", len(events))
c = collections.Counter(j.get("event", "?") for j in events)
print("--- event counts today:")
for k, v in c.most_common(50):
    print(f"  {v:5d}  {k}")

# specific categories
print("\n--- PAUSE/BRAKE events today:")
for j in events:
    ev = (j.get("event") or "").lower()
    if any(k in ev for k in ("pause", "brake", "shutdown", "cooldown")):
        print(" ", j.get("timestamp"), j.get("event"), {k: v for k, v in j.items() if k not in ("event", "timestamp", "level")})

print("\n--- REJECT/GATE/SKIP/CAP/MIN/SLOT events today:")
for j in events:
    ev = (j.get("event") or "").lower()
    if any(k in ev for k in ("reject", "gate", "skip", "cap", "min", "slot", "reduc", "notional", "saturat", "limit", "blocked", "deny")):
        print(" ", j.get("timestamp"), j.get("event"), {k: v for k, v in j.items() if k not in ("event", "timestamp", "level")})

print("\n--- SIGNAL/ENTRY/FILL events today:")
for j in events:
    ev = (j.get("event") or "").lower()
    if any(k in ev for k in ("signal", "entry", "fill", "order", "burst detected", "candidate")):
        print(" ", j.get("timestamp"), j.get("event"), {k: v for k, v in j.items() if k not in ("event", "timestamp", "level")})

print("\n--- REGIME events today (non hourly-update):")
for j in events:
    ev = (j.get("event") or "")
    if "regime" in ev.lower() and ev != "BTC regime updated":
        print(" ", j.get("timestamp"), ev, {k: v for k, v in j.items() if k not in ("event", "timestamp", "level")})
