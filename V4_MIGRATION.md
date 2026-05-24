# V4 Migration Guide

## What Changed

### New files (V4):
- `engines/liq_cluster_engine_v4.py` — V4 engine with aggression-decile exits
- `tools/v4_forward_test.py` — V4 forward tester
- `config/v4_forward_test.yaml` — V4 config

### Untouched files (V3):
- `engines/liq_cluster_engine.py` — V3 engine (frozen, unchanged)
- `tools/v3_forward_test.py` — V3 forward tester (unchanged)
- `config/v3_forward_test.yaml` — V3 config (unchanged)

## V4 Changes from V3

1. **Aggression score** computed at entry (10-component composite, 0-100)
2. **Decile assignment** based on aggression score (D1=lowest, D10=highest)
3. **Decile-specific exit parameters**:
   - D1-D2: 3x trail, NO decay, 500 max hold
   - D3: 2x trail, NO decay, 288 max hold
   - D4-D5: 2x trail, relaxed decay (15 bars), 288 max hold
   - D6: 2x trail, standard decay (12 bars), 288 max hold
   - D7-D8: 2.5x trail, suppressed decay (20 bars), 358 max hold
   - D9-D10: 1.5x trail, moderate decay (8 bars), 100 max hold
4. **Consecutive red bar tracking** for decay conditions
5. **Telegram notifications** include aggression score and decile on entry, exit, and partial

## How to Revert to V3

### Option A: Run V3 forward tester directly
```bash
cd /root/bitana
source venv/bin/activate
python3 -u tools/v3_forward_test.py
```

### Option B: Replace V4 with V3 in the forward tester
In `tools/v4_forward_test.py`, change these two lines:
```python
# FROM:
from engines.liq_cluster_engine_v4 import LiqClusterEngineV4
# TO:
from engines.liq_cluster_engine import LiqClusterEngine

# FROM:
runner = V4ForwardTest()
# TO:
runner = V3ForwardTest()  # or rename class back to V3ForwardTest
```

### Option C: Swap the systemd service (if applicable)
```bash
# Stop V4
systemctl stop bitana-v4

# Start V3
systemctl start bitana-v3
```

## Database Separation
- V3 uses: `storage/v3_forward_test.db`
- V4 uses: `storage/v4_forward_test.db`
- No data conflict between versions

## Log Separation
- V3 logs: `logs/v3_forward_test.log`
- V4 logs: `logs/v4_forward_test.log`
