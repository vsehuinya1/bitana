# v66 Liq-Burst Continuation (experimental branch)

**Branch:** `feature/liq-burst-v66`  
**Status:** Architecture ready · strict OOS **not yet passing** (see below)

Separate from **v65-revert** (daily cascade + V5 reversal). v66 uses **hourly liq buckets**
from WS force orders — **no same-day look-ahead**.

## Thesis

When an hourly liq bucket is **>= share_min** of trailing-24h and **short-dominated**,
price may **continue up** (squeeze). Opposite for long-dom cascades.

Phase A (characterization) showed real drift; Phase B + chronological replay show
**stop/fees eat the edge** under simple rules.

## Chronological OOS (Jan–May 2026, keep bar)

| Config | Full | OOS test | Verdict |
|--------|------|----------|---------|
| short_dom ≥35% → LONG, 3ATR, 8h | +0.028R | **−0.297R** | KILL |
| Exhaustive chrono sweep (tier/stop/hold/session) | — | — | **0 KEEP** |

Do **not** promote to real money. Paper bot runs for **live telemetry** only.

## vs v65

| | v65-revert | v66 burst |
|---|------------|-----------|
| Liq input | Daily bucket (+ look-ahead bug) | Hourly WS buckets |
| Entry | V5 reversal gates | Burst continuation |
| Engine | `liq_cluster_engine_v5` | `liq_burst_engine` |
| DB | `v5_forward_test.db` | `v66_burst_forward_test.db` |
| Service | `bitana-v5-paper` | `bitana-v66-burst` |

## Files

- `engines/liq_burst_engine.py` — burst detect + 8h/ATR exit
- `tools/v66_burst_forward_test.py` — paper runner
- `backtest_output/v66_burst_backtest.py` — keep-bar replay
- `deploy/vps_v66_burst_live.sh` — deploy experimental paper

## Deploy (parallel to v65)

```bash
bash deploy/vps_v66_burst_live.sh
```

v65-revert (`bitana-v5-paper`) is unchanged.
