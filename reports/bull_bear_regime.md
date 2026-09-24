# BULL & BEAR Regime YAML Changes — Evidence Review (2026-09-24)

Written 2026-09-24 by OWL (GLM 5.3 flash) after two Claude subagent passes timed out; OWL completed the analysis directly (Claude's method, same read-only discipline). Companion to `reports/neutral_session_trading.md` (neutral rows already registered). All book numbers: `shadow_trades` WLA=1, closed, 2026-08-24 → parity cut 2026-09-23T12:36:54Z, era-aware R (ny flush_1h /6 pre-Sep-6, /5 after; burst_follow /6), fees ≈ 0.035R/leg not in book E.

**Headline: for BULL, the lattice is already at the evidence frontier — the only registrable items are three dark WATCH rows, not cuts. For BEAR, there is zero data since Aug-17 and the honest change is "prepare the ledger, touch nothing."**

## 1. BEAR — nothing registrable, roll forward

- **Zero bear-regime shadow rows since 2026-08-24** (both arms, all hours). Regime sequence since Aug-17: bull/neutral only. The yaml's bear cells (ny [14,15,20] stop 10 ATR; london h9/10/11/13) rest entirely on the frozen pre-Aug-24 prereg bases (PREREG-BEAR-NY n=21, PREREG-BEAR-LON n=77).
- Any bear change today would be speculation on a regime that hasn't printed a bar in 5+ weeks. **No YAML change proposed.** The roll-forward note in RESEARCH_PLAN stands; bear cohorts remain n=0.
- Prepared (no wire): the first bear bar auto-arms the same dark ledgers as FK3 (mode-2 knife ledger: rv/flow/vol_z/liq_imb at entry per bear leg), so when bear finally prints, the FIRST cohort is measured, not traded blind. Bear stop 10 ATR stays frozen per PREREG-BEAR-NY; its kill criteria are already in the plan.

## 2. BULL — evidence review by lever

### 2a. Hours (ny arm, bull, WLA=1, weekday × hour-band)
Every cell is n=3–17 carried by 1–3 days (top-day ≈ 100% on most) — **the weekday×hour grid is single-day noise; nothing clears a re-open or new-cut bar.** Selected cells:
```
Mon h14-20:  n=18 E≈0.00 (1d) — Mon already excluded live; historical rows confirm the exclusion
Tue h14-15:  n=15 E+0.047 (2d, top 87%)   Tue h18-20: n=7 E−0.256 (1d)  → Tue cut STAYS
Wed h14-20:  n=14 mixed, all 1-day cells  → Wed 18-20 cut STAYS
Thu h14-15:  n=12 E+0.277 (2d, top 52%)   Thu h18-20: n=11 E+0.183 (3d, top 77%)
Fri h14-15:  n=15 E+0.196 (2d, top 73%)   Fri h18-20: n=17 E+0.039 (4d)
```
- **Verdict: keep every current cut.** The Tue h18-20 re-bleed evidence is 1 day; the Aug-26/28 cells were owner post-hoc calls with known single-day concentration — reopening them now would be fit-to-last-week. Do not touch.

### 2b. London bull hours — the one structural finding
```
h09:  n=65  E+0.018 (12d, top 89% — one day carries it)
h10:  n=87  E−0.005 (12d, PF 0.95, top −261%)  fresh Sep-14+: n=40 E+0.003 (top 359%)
h11:  n=75  E+0.074 (12d, top 32%)
h13:  n=69  E+0.080 (11d, top 27%)
```
- **h10 is the only structurally weak lane in bull** — flat after fees for 12 days, and the fresh window is pure noise. h09's +ΣR is one day; h11/h13 carry the arm.
- **NOT a cut today** (E is ~0, not negative; cutting costs 152 legs of option value against the owner's capture preference). Registered below as a dark watch with a promotion bar.

### 2c. Decile (bull)
```
ny:   D1  n=128 E+0.062 (top 48%)   D2+  n=24 E+0.085 (top 147%)  → equal-ish; Sep-12 D1 unlock holds in bull
lon:  D1  n=673 E+0.017 (top 28%)   D2+ n=178 E+0.053 (top 46%)   → 3x delta, but D1 = 79% of legs
```
- A bull-scoped `min_decile 2` on london would remove ~500 legs to lift per-leg E by +0.036 — direct conflict with capture preference; the D1 unlock (Sep-12) was global and equal-E all-regime. **Watch row only** (below).

### 2d. Symbol pairs (bull, per-symbol on WLA=1)
```
ny bull (n≥10):    SOL +0.260 (n16, 10d, top 34%) · ETH +0.055 · XRP +0.024 · ENA +0.049 · ZEC −0.076 (n=13, PF 0.50, top −33%)
london bull (n≥15): ETH +0.042 · XRP +0.055 · PUMP +0.064 · SOL −0.004 (n=124, top −136%) · ENA −0.014 · PEPE −0.007 · ZEC +0.013 (top 84%)
```
- **No symbol blacklist is registrable.** The london flat names (SOL/ENA/PEPE) are noise around zero across 9–12 days each — cutting them saves ~0 and removes 225 legs. The one repeat offender (ny-ZEC, PF 0.50) is n=13 — below any prereg bar. Watch row only.

### 2e. vol_z in bull (interaction: NY-VOLZ-OFF pending)
```
ny bull vol_z<0:  n=68  E+0.089 (11d, top 42%)  ·  fresh Sep-14+: n=11 E+0.178
ny bull vol_z≥0:  n=84  E+0.046 (top 64%)
```
- The bull-only split **strengthens** the pending NY-VOLZ-OFF row: the gate removes a positive slice in bull too (the pending read's kill bars already cover the 42% top-day). No new row needed — add a note at Sunday's read that the bull-only slice is the supportive one.

### 2f. London bull arm — the big picture (owner awareness, not a change)
London bull all-scope: n=1004, E+0.018 book → **≈ net −0.017 after fees — breakeven**. The arm's edge is concentrated in h11/h13 (E+0.074/+0.080); h9/h10 are fee-drag. This is the actual bull-side money question, and it's a book-shape decision, not a filter: narrow hours (watch row below) vs keep breadth.

## 3. Registered below (3 dark watch rows, zero behavior change)

- **BULL-1 LON-BULL-NARROW watch:** candidate = narrow london bull hours to h11–13 (drop h9/h10). Dark read: parity-era h9/h10 cells; promote to a cut only if cut-lane E_book < 0 at n≥50/≥5d with top-day ≤40% AND kept-side (h11-13) holds ≥ +0.085. Kill: cut lanes E-positive.
- **BULL-2 NY-ZEC watch:** ny bull ZEC PF 0.50 (n=13) — blacklist candidate if parity-era n≥30 with E_book ≤ −0.02; too thin today.
- **BULL-3 LON-DECILE watch:** bull-scoped D1 vs D2+ split on london (3x E delta, 79% leg share) — post-parity accrual; any sizing-weighted variant comes only after the read.
- **BEAR: no rows** — data void. Roll-forward continues; bear ledger template attached to FK3's mode-2 ledger.

## 4. Ranked owner actions
1. Nothing wires today. Bull YAML stays as-is — the cuts are evidence-supported, the weak lanes are watch-rows.
2. Sunday Sep-27: BULL-1 read joins the queue (after NY-VOLZ-OFF, NEUT-STOP, FK1/2).
3. When bear prints its first bar: freeze, measure, apply the frozen PREREG-BEAR cells as registered — no improvising on n=1.