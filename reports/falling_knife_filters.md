# Falling-Knife Filters for the Flush-Buy Arms — Shadow-Book Quant + Prereg Shapes

Written 2026-09-24 (overnight after the 2026-09-23 h14 trio −2.19R / h16 six-leg −1.10R, day −3.18R).
Scope: shadow book only (`/tmp/ss2.sqlite`, read-only copy). All rows: `would_live_accept=1`, closed, `entry_time >= '2026-08-24'`, era-parity cut `entry_time < '2026-09-23T12:36:54'`.
R convention (era-aware): ny_flush_buy_1h → `pnl_atr/6` pre-2026-09-06, `/5` on/after; burst_follow → `/6` always. Live fees ≈ 0.035R/leg — the book pays none, so **E_book must be ≥ +0.085 to be ≥ +0.05 net live**. "Top-day %" = share of total kept-side R from the best single day.

Status: COMPLETE — all sections computed from /tmp/ss2.sqlite (read-only) on 2026-09-24.

## 0. Baseline (no filter), WLA=1 era-split

Population (parity-verified): `ny_flush_buy_1h` ∪ `burst_follow`, WLA=1, closed, 2026-08-24 → cutoff. n=1312 (flush_1h 308, burst_follow 1004).

```
BASELINE all                n=1312 E=+0.037 SR=+48.6 WR=58% PF=1.40 days=22 topDay=15.5%
BASELINE pre0906            n=1105 E=+0.030 SR=+32.7 WR=59% PF=1.32 days=13 topDay=23.0%
BASELINE post0906           n= 207 E=+0.077 SR=+15.9 WR=57% PF=1.84 days= 9 topDay=46.0%
BASELINE flush_1h           n= 308 E=+0.100 SR=+30.7 WR=63% PF=2.05 days=20 topDay=23.8%
BASELINE flush_1h post0906  n=  53 E=+0.205 SR=+10.9 WR=62% PF=2.94 days= 7 topDay=67.3%
BASELINE burst_follow       n=1004 E=+0.018 SR=+18.0 WR=57% PF=1.20 days=14 topDay=20.3%
BASELINE burst_follow post  n= 154 E=+0.033 SR= +5.0 WR=56% PF=1.38 days= 5 topDay=35.8%
```

Read: the whole flush-buy book is only E+0.037 book / **≈ 0.00 net live after 0.035R fees** — a knife filter does not need to be subtle; anything that cuts E without losing >~0.06R/n on the kept side pays. flush_1h carries the edge (E+0.100 book); burst_follow is nearly net-flat. Note post0906 topDay=46% on the baseline — the kept-side ≤40% bar is against an already top-heavy book.

Knife-day case study (shadow mirror of yesterday's legs, outside the parity population, labeled as such): the losing knife rows (WLD 14:09Z stop −10 pnl_atr ≈ −2.0R, DOT −3.31/5 ≈ −0.66R, XRP/TAO/ETH 15:34Z, PEPE/FIL/ZEC 15:29–15:49Z) share **liq_imb 0.83–1.00 (mostly ≥0.97)** and several have **oi_delta_30m_pct ≤ −1.8** (XRP −2.61, PEPE −3.17, FIL −3.42, ZEC −4.14) with cascade_strength ≥1.0 and vol_z ≥2.5 on the worst (WLD 3.37). Winners same day had liq_imb ≤0.92 or low vol_z. This motivates candidates C1–C6 below.

Honest scope note: WLA over-accepts vs the live mirror; the live mirror now applies vol_z gating since 13:21Z tonight — every row here is pre-parity, i.e. book numbers are optimistic on the vol-z tail and these filters must be dark-wired before trusting kept-side E.

## 1. Method

- Cell = `(n, E_book, ΣR_book, WR, PF, days, topDay%)`. `topDay%` = best day's R / total R (n/m when ΣR<0). Fee bar: **kept side needs E_book ≥ +0.085 ⇒ net ≥ +0.05 after 0.035R/leg**; dropped slice must be the knife (E_book clearly worse than kept).
- Era split everywhere: pre-2026-09-06 (stop=6 ATR) vs post (stop=5 ATR). flush_1h rows /6 and /5 respectively; burst_follow /6.
- Knife-day audit (§3): rules replayed row-by-row on the post-cutoff 2026-09-23 mirror rows (outside parity population, labeled) to check whether yesterday's mode would have been caught.

## 2. Candidate discriminators — cells

### C1 `btc_distance_from_ema_pct` — **DEAD**
```
0<dist<=1     n=  8 E=+0.914 SR=+7.3 WR=88% PF=32.3 days= 1 topDay=100%   (post0906 only)
1<dist<=3     n= 33 E=+0.114 SR=+3.8 WR=61% PF= 2.01 days= 1 topDay=100%  (post0906 only)
3<dist<=6     n= 43 E=+0.052 (3d)   6<dist<=10  n=172 E=+0.067 (9d)
10<dist<=15   n=581 E=+0.019 (11d)  dist>15     n=475 E=+0.027 (5d)
```
No knife gradient: the bulk of the book sits at dist>10 with E+0.019/+0.027. The sub-3 tail only exists in the last 2 days (column only recently varies) — n too small to read. Not registrable.

### C2 `btc_realized_vol_24h` floor — **STRONGEST CLEAN DISCRIMINATOR**
rv is bimodal (0 rows in [0.07,0.09)); threshold set at **0.07**.
```
rv<0.07 (DROP)  n=138 E=-0.055 SR= -7.6 WR=49% PF=0.67 days= 5   [pre0906 n=90 E=-0.075 PF=0.57 | post0906 n=48 E=-0.016 PF=0.89]
rv>=0.07 (KEPT) n=1174 E=+0.048 SR=+56.2 WR=60% PF=1.58 days=19 topDay=13.0%  [pre0906 E+0.039 | post0906 n=159 E=+0.105 topDay=43.8%]
```
Threshold suggestion: **drop flush-buys when btc_realized_vol_24h < 0.07** (BTC-calm idiosyncratic flush = symmetric crash, not squeeze-continuation). Kept-side E+0.048 all-scope → **fails the +0.085 bar standalone**, but on flush_1h only: kept n=277 **E+0.119 (net +0.084 ✓)** topDay 22.3%, pre0906 E+0.085. Dropped slice is the only era-consistent negative bucket found (E−0.055, PF 0.67).

### C2b `entry_impulse_pct` × `btc_realized_vol_24h` (OWL hypothesis) — **WEAK/REVERSED**
```
imp>=1.0        n= 91 E=+0.047        post0906 n=19 E=-0.030
imp<0.2         n=577 E=+0.028
0.2<=imp<0.4    n=298 E=+0.067
0.4<=imp<0.7    n=242 E=+0.008  topDay=120%
```
Joint "huge impulse × high BTC vol" dropped slice: n=234 **E+0.040** — not a knife bucket (dropping it lowers kept E). OWL hypothesis C2 **fails as measured**. The impulse≥1.0 post0906 dip (n=19, E−0.030) is the only weak positive signal — n far too small to prereg.

### C3 `market_liq_flow_usd` floor — **MODERATE, stack-only**
```
flow<0        n=584 E=+0.030
0<=flow<250k  n=241 E=+0.018  post0906 n=25 E=-0.014
250k<=flow<1M n=233 E=+0.037
1M<=flow<3M   n=152 E=+0.076  post0906 E=+0.147
flow>=3M      n=102 E=+0.068
```
Direction is **inverted vs the OWL hypothesis**: high liquidation flow at entry is GOOD (real cascade = squeeze fuel); the dead zone is small/absent flow (0–250k, E+0.018; post0906 E−0.014). Threshold suggestion: **require flow ≥ 250k**; kept n=487 E+0.049 all-scope (fails bar alone). Stacked with C2 (below) it clears on flush_1h. `net_delta_at_entry` buckets point the other way (nd≥5 E+0.149 = squeeze signature) — consistent story: you want market-wide flow WITH the flush, and the "flow negative while symbol flushes" combo (nd≥2 & flow≤0) is actually book-positive (dropped-slice E+0.111) — hypothesis C3c is falsified, do not register.

### C4 `entry_lag_bars` / `hour` — **DEAD**
`entry_lag_bars` is 68% NaN (mirror fill timing, not a signal feature); where populated, lag<10 cells are n≤20. Hour buckets: h16–18 E+0.101 is the *best* cell, h14–16 E+0.063 — no knife gradient; yesterday's knives (h14–16) sit inside the best-scoring hours. Not registrable. Late-session re-flush is not measurable in this book (mirror lag contaminates it).

### C5 `entry_vol_z` × `liq_imb` (panic exhaustion) — **KNIFE-MODE-SPECIFIC, EDGE-NEGATIVE**
```
vz>=2            n=233 E=+0.052
liq_imb>=0.95    n=571 E+0.053 (0.95-1 n=278 E+0.050 | >=1 n=293 E+0.057)
DROP vz>=2 & li>=0.95: dropped n=198 E=+0.063 (book-positive → veto fails)
DROP vz>=2 & li>=0.97: dropped n=114 E=+0.080 (book-positive → veto fails)
```
**The exhaustion joint is not a knife at book level — it pays (E+0.063/+0.080).** But see §3: it is the only measured rule that engages yesterday's hard knife rows. Do not register as a veto; register as a **dark counter only**, especially because the live mirror now gates vol_z (NY-VOLZ-OFF pending read will confound this axis).

### C6 `cascade_active` / `entry_cascade_strength` / `oi_delta_30m_pct` / `burst_vol_30m` — **DEAD to NON-MONOTONE**
```
cascade_active=1 n=419 E+0.030  vs 0: n=893 E+0.040      (no separation)
cs 0.6-1.0       n=147 E+0.012 topDay=190%             (mildest dead zone, still positive)
oi<=-3           n= 40 E+0.023 topDay=80%   (post0906 n=6 — unmeasurable)
oi>=0            n=563 E+0.022                          (no-OI-move flushes weak, not negative)
burst_vol>=500k  n=112 E+0.055                          (big burst = fine)
DROP oi<=-1.5:   dropped n=123 E=+0.042  → dropping costs +5.1R book. FAILS.
DROP cs>=1 & oi<=-1.5: dropped n=33 E=+0.084 → wrong direction. FAILS.
```
OI-collapse flushes lose *relatively* but stay positive; vetoing them costs money. The knife-day oi≤−1.8 signature (case study) is a within-day mode, not a book-level bucket. Not registrable.

### C7 breadth-cap (supplementary check) — **FAILS**
Drop breadth≥9: dropped slice n=286 **E+0.067** — broad events pay; the cap would have cost +19.1R. Confirms OWL #1 and adds: breadth is monotone UP (br=1 E+0.012 → br≥9 E+0.064+). Yesterday's bleed was the exception, not the rule.

### C8 STACK — `rv ≥ 0.07` AND `flow ≥ 250k` — **the only bar-clearing cells**
```
KEPT all           n= 422 E=+0.071 SR=+29.9 WR=62% PF=1.93 days=18 topDay=19.5%  [pre0906 E+0.057 | post0906 n=59 E+0.159 topDay=62%]
KEPT flush1h-only  n= 114 E=+0.169 SR=+19.2 WR=72% PF=3.94 days=14 topDay=30.3%  [pre0906 n=101 E+0.112 | post0906 n=13 E+0.609*]
DROPPED all        n= 890 E=+0.021 SR=+18.8 WR=57% PF=1.21 days=22 topDay=29.9%
```
*post0906 flush1h n=13 PF=179 is an artifact of 3 days — do not trust; the pre0906 leg (n=101, E+0.112) is the honest anchor.
Kept all-scope E+0.071 → net +0.036, still short of the +0.05 net bar. Kept flush_1h-scope E+0.169 → **net +0.134 ✓**, topDay 30.3% ✓. The stack's dropped slice (n=890, E+0.021, net −0.014) is the fee-dead bulk of the book.

## 3. Knife-day audit — would any rule have caught 2026-09-23?

Replay of the 28 post-cutoff mirror rows (book R, net of 0.035R):

| rule | dropped n | dropped net R | kept net R |
|---|---|---|---|
| drop rv<0.07 (C2) | 0 | +0.00 | −3.39 |
| drop flow<250k (C3) | 4 | +0.26 | −3.65 |
| drop oi≤−1.5 (C6) | 10 | −0.60 | −2.79 |
| drop vz≥2 & li≥0.97 (C5) | 5 | −2.10 | −1.29 |
| drop vz≥2.5 & li≥0.97 | 4 | −2.18 | −1.21 |
| drop cs≥1 & oi≤−1.5 | 6 | −0.21 | −3.18 |

**Loud, honest finding: yesterday's knife mode would NOT have been blocked by the book's strongest discriminators.** BTC rv was 0.0966 (>0.07) and every leg had flow ≥ 250k — C2/C3 fire zero. The only rules that engage are the panic-exhaustion joint (catches WLD 14:09 stop −2.0R, DOT −0.66, TAO −0.15; but also UNI +0.12, PENDLE +0.77) and the oi-veto (which is book-negative to wire). Yesterday's mode = broad event (breadth 4–36) + saturated liq_imb (0.98–1.00) + heavy market flow + BTC rv ~0.097 — i.e. **BTC-volatile broad flush**, the mirror image of the book's knife bucket (BTC-calm). The two knife modes need different filters, and mode-2 (yesterday's) has no era-consistent pre-entry discriminator in the current columns.

## 4. Prereg shapes (G0 rows) — register → dark-wire → Sunday read only

### G0-FK1 — `FLUSH-RVFLOOR` (rank 1)
- **Knob/wire point**: engine pre-gate (signal-level veto before acceptance, before budget consumption). Condition: `btc_realized_vol_24h < 0.07` → reject, reason tag `FALLKNIFE-RVFLOOR`. Scope: `ny_flush_buy_1h` + `burst_follow` (both flush-buy arms).
- **Dark-wire**: feasible tonight with the new per-reason counters — add `would_have_blocked` counter keyed `session:reason` (e.g. `ny:FALLKNIFE-RVFLOOR`, `asia:FALLKNIFE-RVFLOOR`), incremented at the same evaluation point the gate would run. Zero behavior change; the OI-gate dark status (PREREG-OIGATE) is unaffected since this is a new reason key, not the OI one.
- **G0 row text**: `G0-FK1 FLUSH-RVFLOOR: pre-gate veto btc_rv24<0.07 on flush-buy arms. Metric: blocked-bucket rows (shadow-matched) E_book, PF, topDay; kept-side E_book on surviving rows. Registration 2026-09-24; dark counters session:FALLKNIFE-RVFLOOR from wire; first read Sunday 2026-09-27.`
- **Kill/revert bars**: wire→live only if, by Sunday, blocked bucket ≥30 counter hits with shadow-matched E_book ≤ +0.00 (net ≤ −0.035) AND kept-side E_book stays ≥ +0.085 with topDay ≤ 40%. Revert if blocked bucket shows E_book ≥ +0.05 (we're cutting edge, not knives) or kept-side topDay > 60%.
- **Interactions**: orthogonal to cap-3 budget (veto must be evaluated *before* budget so it doesn't consume a slot — state this in the registration). Does not touch vol_z axis (NY-VOLZ-OFF pending read unaffected). If NEUT-STOP reverts, R convention changes → re-baseline kept-side bars before any wire.

### G0-FK2 — `FLUSH-FLOWFLOOR` (rank 2, stack-only)
- **Knob/wire point**: engine pre-gate, second condition (AND with FK1): `market_liq_flow_usd < 250_000` → reject, reason `FALLKNIFE-FLOW`. Same arms.
- **Dark-wire**: same mechanism, own counter key `session:FALLKNIFE-FLOW`. Independent key so FK1/FK2 attribution stays clean (count overlap days explicitly).
- **G0 row text**: `G0-FK2 FLUSH-FLOWFLOOR: pre-gate veto liq_flow<250k on flush-buy arms (stacked with FK1). Metric: joint blocked bucket E_book; FK1-only vs FK1+FK2 kept-side deltas. Registration 2026-09-24; read Sunday 2026-09-27.`
- **Kill/revert bars**: live only if FK1+FK2 kept-side flush_1h E_book ≥ +0.12 with n_kept ≥ 60 over the dark window and the FK2-only marginal blocked rows show E_book ≤ 0. If FK2's marginal drop is E-positive (like the full-book stack suggests: dropped-all E+0.021 with most of it FK1-overlap), kill FK2, keep FK1.
- **Interactions**: same budget-order clause as FK1. Watch overlap with cluster/breadth logic — flow is market-wide, so on broad-event days it rarely fires (yesterday: 0 fires); this is a per-symbol dead-zone filter, not a regime filter.

### G0-FK3 — `FLUSH-PANICEXH` (rank 3 — **dark counter ONLY, no veto**)
- **Knob/wire point**: none wired. Observe-only counter keyed `session:FALLKNIFE-PANICEXH`, condition `entry_vol_z >= 2 AND liq_imb >= 0.97` at signal time. Rationale: book-positive bucket (E+0.080 dropped-slice) — a veto would cost +9.1R book — but it is the only measured rule that engages yesterday's hard knife rows (drops −2.10R net of the −3.39R knife day).
- **G0 row text**: `G0-FK3 PANICEXH dark counter: log-only hits of vol_z>=2 & liq_imb>=0.97 on flush-buy arms; no gate. Metric: counter-hit rows' realized R vs book mean; correlation with same-bucket stop-outs within 1 candle. Read Sunday 2026-09-27; promotion to gate requires NEW evidence (post-parity rows, era split), not today's cells.`
- **Kill/revert bars (to promote)**: only if post-parity blocked bucket flips to E_book ≤ 0 with n ≥ 40 while kept-side holds E_book ≥ +0.085. Otherwise it dies at Sunday read.
- **Interactions**: **hard dependency on NY-VOLZ-OFF pending read** — the mirror already gates vol_z since 13:21Z, so any FK3 verdict before that read lands is confounded. Do not let FK3's counter be interpreted as gate performance.

## 5. Ranking

1. **G0-FK1 FLUSH-RVFLOOR** — the only era-consistent negative dropped bucket (E−0.055, PF 0.67, n=138); bar-clearing on flush_1h scope (kept E+0.119 book / +0.084 net, topDay 22.3%). Cheap knob, clean counter, orthogonal to every pending prereg.
2. **G0-FK2 FLUSH-FLOWFLOOR** — real but stack-only: standalone kept E+0.049 fails the bar; stacked clears on flush_1h (E+0.169). Register as AND-condition, kill-ready at Sunday.
3. **G0-FK3 PANICEXH (dark only)** — engages yesterday's mode but is edge-negative book-wide; counter-only until post-parity evidence flips it.

**Explicitly rejected (do not register)**: btc_distance_from_ema_pct (no variation in-window), impulse×btc-vol (dropped slice positive), net_delta/liq-flow divergence (reversed), lag/hour (unmeasurable/contaminated), OI-veto and cascade-strength joints (dropped slices positive), breadth-cap (costs +19R).

**Net effect if FK1+FK2 wired as proposed**: flush-buy book goes from E+0.037/net≈0.00 (n=1312) to kept E+0.071/net +0.036 all-scope — or +0.134 net on flush_1h scope — at the cost of ~64% of signal count all-scope (n=422). That is a book-shape decision for the owner, not automatic: the all-scope kept cell is still below the +0.05 net bar, which is why both rows are registered as **dark-wire-first**.

## 6. What to watch in the first dark days; Sunday 2026-09-27 read

First dark days (counters only, zero behavior change):
- `session:FALLKNIFE-RVFLOOR` hit rate per day and per session — expect spikes on BTC-calm days (rv<0.07); if it never fires, the 0.07 threshold is wrong for the current regime (BTC rv has been 0.09–0.13 lately).
- `session:FALLKNIFE-FLOW` fires and their overlap with FK1 — flow<250k days are the mirror's dead zone (post0906 E−0.014).
- `session:FALLKNIFE-PANICEXH` fires on broad-event days (breadth≥4 + liq_imb≥0.97): how many turn into ≤1-candle stop-outs. This is the mode-2 (BTC-volatile broad flush) tracker that yesterday exposed and no book cell captures.
- Any live stop-out within 1 candle on flush-buy arms: record its (rv, flow, vol_z, liq_imb, oi_delta) — building the mode-2 knife ledger is the deliverable of the dark window.

Sunday Sep-27 loop reads, in order:
1. FK1/FK2 blocked-bucket shadow-matched E_book, PF, topDay vs the kill/revert bars in §4 (n≥30 blocked, E_book ≤ 0 blocked side; kept E_book ≥ +0.085, topDay ≤ 40%).
2. Kept-side all-scope vs flush_1h-scope split — decide whether FK1/FK2 arm scope should shrink to flush_1h only (the only bar-clearing scope today).
3. NY-VOLZ-OFF pending read must land BEFORE any FK3 interpretation (post-parity rows only).
4. Confirm PREREG-OIGATE dark status is unchanged and that no FK counter key collided with OI-gate reason keys.
5. Cap-3 budget accounting check: vetoed signals must not have consumed budget in the shadow replay — if the engine counts vetoes against the budget, FK wiring order changes and this G0 must be re-registered.

*All numbers: shadow book, WLA=1, era-aware R, fees NOT in book E (subtract 0.035R for live). Rows pre-parity (mirror vol_z gate live since 13:21Z tonight). Read-only /tmp copy of signal_shadow.db incl. WAL; no service touched; no config edited.*