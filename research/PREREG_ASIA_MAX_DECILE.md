# PREREG-ASIA-MAX-DECILE
**Registered**: 2026-08-31T02:30Z (this session)
**Owner**: Martin
**Status**: REGISTERED — forward-only from 2026-09-01T00:00Z

---

## Claim Tested
Decile gradient in the live-mirrored asia book (`asia_pump_short_4h`, neutral regime, closed, live-eligible weekdays Mon/Wed/Thu/Fri, decile≥2). Two candidate claims:

1. **Full-book claim (REJECTED at baseline)**: D4–9 is toxic (−0.97R shadow full-book) → cap at 3. **This does not survive the live-day scope**: on live-eligible days D4–9 is +0.35R (n=15, PF 1.17) and **D10 is the best band in the book**. Recorded here so the wrong conclusion is not re-derived.
2. **Live claim (REGISTERED)**: D10's live-day edge — in-sample +2.49R shadow, n=23, PF 5.64, ΣR +57.2 of the book's +94.8 — is **July-only** (22/23 trades; Aug n=1) and may be a one-month artifact. If it decays forward, a `max_decile=3` cap becomes the promotion candidate. Default action is **no cap** (Branch KEEP).

**Context**: owner call 2026-08-31 — `min_decile: 2` retained, relax-to-1 closed (D1 live-days ≈ +0.024R live-R, 1/5th of D2–3). Engine has `min_decile` only, no max (`liq_burst_follow_engine.py:229`).

---

## Frozen In-Sample (Jul 8 – Aug 30, shadow canonical R = pnl_atr, 10-ATR stop)

| Band | Scope | n | E (R) | PF | WR | ΣR |
|------|-------|---|-------|----|----|----|
| D2–3 | full book | 32 | +1.11 | 2.09 | 69% | +35.4 |
| D2–3 | live-days | 20 | +1.62 | 2.65 | 75% | +32.4 |
| D4–9 | full book | 28 | −0.97 | 0.66 | 50% | −27.1 |
| D4–9 | live-days | 15 | +0.35 | 1.17 | 53% | +5.2 |
| D10 | full book | 48 | +0.25 | 1.16 | 52% | +12.2 |
| D10 | live-days | 23 | **+2.49** | **5.64** | 74% | +57.2 |

D10 live-days fragility: Jul n=22 (E +2.70, PF 6.92) / Aug n=1 (−2.28). Excluding top-3 winners (SOL +9.67, XRP +9.05, SOL +7.64): n=20, E +1.54, PF 3.50 — broad within July, unconfirmed in August.

Live-R equivalents (3% stop, entry_atr_pct-weighted): D2–3 live-days +0.189R, D4–9 +0.038R, D10 +0.210R.

**Aug starvation caveat**: Aug asia live-days n = 5 (D2–3) + 1 (D4–9) + 1 (D10) = 7 total. Whole-book decay vs band-specific decay is NOT separable in-sample.

---

## Forward-Only Period
- **Start**: 2026-09-01T00:00Z (next Sunday loop)
- **Counts-only until**: 2026-09-13 (2 weeks)
- **First R-read**: 2026-09-13
- **Formal call**: 2026-09-27
- **One extension max**: 2026-10-11

Forward `would_live_accept=1` is valid only post 2026-08-31T01:39Z (commit `ad80627`, WLA weekday gate deployed); historical WLA (n=10) predates it and is baseline-only.

---

## Data Source
- `storage/signal_shadow.db` → `shadow_trades`
- Filter: `strategy='asia_pump_short_4h'`, `status='closed'`, `btc_trend_state` neutral, weekday ∈ {0,2,3,4} (0=Mon convention), `decile≥2` (live-mirror full-closed scope); forward WLA=1 as live-mirror confirm
- Canonical R: `pnl_atr` (10-ATR); live-R reported alongside (`pnl_atr × entry_atr_pct / 3`)

## Metrics (per band: D2–3, D4–9, D10)
`n`, `E` = mean R, `PF`, `WR`, `ΣR`, `days` — both scopes, both R conventions.

## Floors (per band, per read)
`n ≥ 10` AND `days ≥ 5` in live-days full-closed scope. Below floor → counts-only, no call.

---

## Promotion Criteria (Branch CAP — propose `max_decile: 3` wiring, owner gate required)
ALL of, at formal call:
1. D10 live-days `E ≤ 0` with n ≥ 10 (in-sample edge decayed)
2. D2–3 live-days `E > 0` with n ≥ 10 (book otherwise alive — cap must not be masking whole-book death)
3. Forward full-book D4–9 does not sign-flip vs live-day scope (no new weekday contamination)

→ Propose live `max_decile: 3` for `asia_pump_short_4h` only. Not a template for other books.

## Kill Criteria
1. **Branch KEEP closes**: D10 live-days `E ≥ +1.0R` with n ≥ 10 at formal call → decile question CLOSED, no cap, no re-registration of any max_decile claim
2. **Whole-book kill**: D2–3 AND D10 both `E ≤ 0` forward (n ≥ 10 each) → asia decile question moot; escalate whole-book decay to Sunday loop as separate item
3. **Starvation**: live-days asia n < 10 by formal call → extend once to 2026-10-11; still starved → drop, log

→ Drop, log, no re-registration of same claim.

---

## Notes
- Event-window prereg (PREREG-EVENT-WINDOW-EXPANSION) unaffected — different book, different filter layer.
- Sep D2–3 decay watch (Aug PF 0.91, n=5 live-days) resolves via criterion 2/kill 2 — no separate tracker needed.
