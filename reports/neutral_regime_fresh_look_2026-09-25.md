# Neutral regime: a fresh look across all sessions (2026-09-25)

Owner order: a fresh look at trading BTC-neutral in every session; re-test killed or invalidated theories on
my own metrics; bring new theses and yaml suggestions.
Data: shadow copy 2026-09-25 06:49Z (80.5k closed rows since Jul-06; ~40k neutral), the live DB, and the live
regime classifier replayed on 4h BTC.
Rows still pending a read are NOT judged: NY-NEUT-KEEP, LON-NEUT-H13, ASIA-PUMP-NEUTRAL, NEUT-STOP, FK1–3, TUEASIA.

## Metrics used
- **Costs and units:** net R at **20 bps** (12 bps also shown). NY 1h is re-simulated at the live exit (SL5, 60m)
  and London at SL6/TP3/30m; everything else uses its own exits.
- **Gates:** live-like gates (vol_z ≥ 0, decile ≥ 1, n_confirms ≥ 1), weekdays only unless stated.
- **Robustness:** halves split by neutral days; top day's share of net; E without the top day; live-symbol subset.

## Why neutral matters
BTC was neutral for **61% / 44% / 59%** of 4h bars in Jul / Aug / Sep. Live trades it only in NY h16–17, which
produced 3 legs on the 1h arm so far.

## Re-tests of killed / invalidated neutral theories: all kills are justified
| Theory | My result (20 bps) | Verdict |
|---|---|---|
| LATEFADE (frozen pop: neutral, h22–23, dec ≥ 2, own exit) | n=299/35d E −0.012 (12 bps +0.010, top-day 192%), halves −0.044/+0.009, ex-top −0.004, live-sym −0.040 | kill justified |
| asia_burst_fade, neutral | n=2,121/44d E −0.228, 1/44 days positive | justified |
| Weekend neutral (ny_flush_1h / asia_pump_short_4h) | −0.052 (5d) / −0.118 (12d) | justified |
| NY neutral h14–15 widening | n=113/12d E +0.032, top-day 87%, **ex-top +0.004** | justified |
| NY neutral h18–20 / h21 "never-wire" | −0.203 (n=107) / −0.314 (n=32) | justified |
| London neutral exclusion (all hours) | n=303/12d E −0.105, 3/12 days positive; h8–9 −0.196, h10–11 −0.074 | justified (h12–13 = Row 2, not judged) |
| Tue neutral h16–17 exclusion | n=14/2d E −0.110 | too thin to reverse; stays |
| Regime-age gate (neutral 24–48h NY) | 1 leg in the bucket | untestable; kill stands |
| 24h hold ("TSL/24h enablement: ignore") in the neutral cell | paired n=17/5d: 1h +0.18 vs 24h +0.44; 24h wins 3/5 days | not overturned (5 days, beta-driven; see below) |

## The live neutral cell is endorsed
NY h16–17, Wed–Fri (Fri h16 only), live exit: n=28/5d **E +0.473**, 5/5 days positive, halves +0.27/+0.51,
top-day 46%, ex-top +0.34, live-sym +0.454.
- **Tue–Fri, n=44:** 60m +0.298 vs 45m +0.229 vs 30m +0.192. The current 1h hold is best.
- **Caveats:** pre-parity shadow over 5 days. Live-real neutral 1h legs so far: 3, −0.55R (Row 1 reads the parity-era
  cohort).

## Neutral scan (120 cells: session × hour band × strategy × side, n ≥ 30): 0 pass
The only near-miss is **NY h16–17 flush-buy held 24h (SL10)**: n=36/12d, E +0.526, halves +0.71/+0.39,
top-day 43%, ex-top +0.388.
- **Mostly beta:** corr(leg R, BTC 24h return) = **+0.63**.
- **BTC moves:** BTC rose +1.21% on average in the 24h after these entries, vs +0.55% from every h16–17 bar those days
  and +0.28% across all neutral weekdays.
- **Beta-adjusted intercept:** about **+0.15 R/leg**.
- **Concentration:** 3 up-days carry it (Jul-13 +8.1R, Sep-16 +7.4R, Sep-10 +3.5R).

## Theses and yaml suggestions
1. **Yaml: no change recommended for neutral.** The current shape (NY h16–17 only, London off, no late or weekend arm,
   60m hold) is exactly what survives. Every widening I tested fails.
2. **Thesis: "neutral NY-afternoon flush → next-day rebound" (24h).** Weak: beta-dominated, 12 days. At most a
   measure-only note; the paper harness already logs `ny_flush_buy_24h`, so nothing to wire. Registrable only with a
   beta-hedged metric (leg R minus β × BTC 24h return) at n ≥ 50 / ≥ 10 days.
3. **The natural next neutral arm is Asia pump-short (Row 3, pending, not judged here).** It's the only neutral book
   with live-real support: asia_pump_short_4h neutral **n=37, +1.14R (E +0.031)** before the Sep-3 disable. That
   disable was for regime flap, since fixed by ADXBAND, not for performance. Its conditional trigger (BTC dist < +5%,
   ≥ 3 neutral bars, two clean weekend tapes) is not met today (dist +7.85%).
