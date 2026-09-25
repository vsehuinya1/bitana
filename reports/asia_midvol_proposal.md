# ASIA-MIDVOL: a way back for Asia in neutral (2026-09-25, owner goal "find a profitable way to wire back Asia neutral")

Status: **proposal, nothing applied or registered.** Needs an owner order to register, or to wire early.

## Why Asia failed: the setup, not the idea
- Asia's arms reused the London/NY template: 5m-ATR stops and targets with fixed ~20 bps costs. Asia's median entry
  ATR is 0.30%, against London 0.61% and NY 0.72%, so 20 bps = **0.67 ATR in Asia vs 0.33 (London) and 0.28 (NY)**.
- Most Asia strategies are positive before costs (+0.02 to +0.09 R/leg). Costs of 0.06–0.23 R/leg make every one
  negative.
- 39% of Asia neutral legs come at ATR < 0.25%, where costs alone cost 0.13–0.2 R/leg (the cost sink). Above 0.5%,
  pump-shorts lose even before costs (the pumps keep running).

## Candidate
Old Asia arm, gates unchanged: `asia_pump_short_4h`, neg-imbalance ≥ 0.5, no Tue/Sat/Sun, neutral only,
BTC EMA dist < 5%, decile ≥ 2, vol_z ≥ 0, n_confirms ≥ 1, SL10, 4h time exit. **Plus** a band on the symbol's 5m
ATR at entry: **0.30% ≤ ATR < 0.50%**.

| paper, 2026-07 → 2026-09-25 | legs / days | E @20bps | E @12bps | halves | top day | days + |
|---|---|---|---|---|---|---|
| all symbols | 24 / 7 | **+0.157** | +0.177 | +0.110 / +0.222 | 47% | 5/7 |
| live-bot symbols | 15 / 6 | **+0.251** | – | +0.146 / +0.408 | 47% | **6/6** |
| same gates, no band (for contrast) | 80 / 15 | +0.044 | +0.073 | +0.101 / −0.046 | 68% | 8/15 |

- **Engine/paper consistency:** ATR% is identical live vs paper (20 matched Asia legs: corr 0.999, ratio 1.000,
  same side of both cuts).
- **Honest limits:**
  - 24 legs is small, and one day is 47% of net (plan bar ≤ 40%).
  - The band edges were chosen after seeing the data. At 0.25–0.50% it's +0.107 with halves +0.157 / +0.013.
  - Frequency is about 2 legs a week.

## What is ready (not applied)
- `reports/asia_midvol_gate.patch`: per-session `min_entry_atr_pct` / `max_entry_atr_pct` gate in the engine,
  mirrored in the WLA shadow, plus the structural-test map and 5 parity tests. Full suite 113 passed / 3 failed
  (the same pre-existing preflight failures). `git apply --check` clean. Inert until a rule sets a bound.
- `research/asia_midvol_reader.py`: draft reader. `--validate` reproduces the basis (PASS). The forward read counts
  from 2026-09-25T19:00Z.
  - Promote: n ≥ 50 over ≥ 10 days, E ≥ +0.03 @20bps, top-day ≤ 40%, both halves > 0, live-symbol subset ≥ 0.
  - Kill: E < 0 at n ≥ 30.
- Config to wire (uncomment the asia block and add the band):
```yaml
    asia:
      shadow_strategy: asia_pump_short_4h
      side_mode: follow
      neg_imb_only: true
      min_imb: 0.5
      exclude_weekdays: [1, 5, 6]
      allowed_btc_regimes: ["neutral"]
      btc_dist_max_pct: 5.0
      min_entry_atr_pct: 0.30   # ASIA-MIDVOL band (needs reports/asia_midvol_gate.patch)
      max_entry_atr_pct: 0.50
      stop_atr: 10.0
      tp_atr: 999.0
      time_bars: 48
      time_exit_only: true
      min_cascade_strength: 0.0
      min_vol_z: 0.0
      min_n_confirms: 1
      min_decile: 2
```

## Options (owner)
1. **Register + confirm forward (recommended).** Register as a dark row replacing Row 3's losing trigger (Row 3's
   dist<5 & age≥3 selection makes −0.075 R/leg over 224 legs). Wire when the reader promotes. At about 2 legs a
   week, that could take months.
2. **Early wire at owner discretion** (precedent: LON-H9 on 22 legs). Apply the patch, add the block above, dry-load,
   restart the live bot and v5-paper in a safe window. The reader keeps measuring and its kill bar applies.
