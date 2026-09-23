# Dashboard v2 — notes (2026-09-23, OWL/GLM-5.3-flash; Claude authored the first cut, OWL fixed + verified)

## What was wrong
`main.py:_arms_snapshot()` resolved `regime_hours[cur_regime]` but never applied
`excluded_weekday_hours` / `excluded_weekday_regime_hours`, so NY-bull showed
[14..20] on every weekday while the engine actually cuts Wed 18-20, Tue 16-20,
Thu 18+20, Fri 17, and zeroes Mon/Sat/Sun. `armed_now` had the same gap.

## Changes (commit a9a1c30, "Dashboard v2")
1. **main.py `_arms_snapshot`** (+66/−12): weekday-effective hours per arm for the
   CURRENT regime — `hours_by_weekday` {Mon..Sun: [hours] or []}, raw exclusion
   maps exposed, `hours_now` = today's effective hours, `armed_now` corrected
   (regime allowed AND today's effective hours contains the current wall-clock
   hour). Docstring documents the 5m-bar close-hour semantics.
2. **dashboard/templates/index.html** (full restyle, hot-served via FileResponse —
   NO dashboard restart needed): dark mobile-first theme, weekday grid per arm
   (Mon..Sun cells, today highlighted, live-hour marker), cut hours struck out,
   regime-block state, asia/late absence notes, pre-restart client-side fallback
   (grid derived from hours_base/regime_hours/skip_days; cut labels hidden until
   the server fields exist), P&L curve SVG, colored trades/positions. Auth flow
   unchanged (?token= URL param).
3. **fmtP fix**: `Math.abs()` was applied to the LOCALE-FORMATTED string → NaN on
   any price ≥ 1000 (ZEC 1510, ETH 2655 rows). Abs the number first.

## Verification
- Effective-hours logic vs `rule.hour_gate_reason` (loader, single source of truth):
  **0 mismatches over 2 arms × 3 regimes × 7 weekdays × 24h = 1,008 cells**
  (`/tmp/effhours_test.py`, loader import with env scrubbed).
- HTML parses clean (html.parser, no unclosed tags); all JS data keys exist in the
  CURRENT `/api/dashboard` payload (checked live) — graceful fallback both pre- and
  post-restart (`hasGrid` branch).
- py_compile main.py OK. Patch applied via `git apply` against the patched tree.
- Visual render check on stubbed payloads (real today-data + synthetic post-restart
  arms): NY Wed shows **14–17** with 18/19/20 struck; grid Mon off / Tue 14–15 /
  Wed 14–17 / Thu 14–17,19 / Fri 14–16,18–20 / Sat+Sun off — matches the gate truth;
  trades table prices render (no NaN). Screenshot reviewed.

## Deployment
- **Template**: live NOW (FileResponse per request) — owner refreshes his dashboard
  URL and sees the new UI immediately. Until 21:15Z the weekday grid is derived
  client-side (raw window + skip_days; cut labels hidden).
- **main.py fields**: live at tonight's 21:15Z restart (rides the scheduled gap with
  the engine patch + OI-dark). After that the grid + cut labels come from the server.
- No dashboard-service restart needed (server.py untouched). No config/engine
  decision change — display only.

## Known cosmetic notes
- `armed_now` leads the engine by up to one bar at exact hour boundaries (documented
  in the snapshot docstring; `hour_gate_reason` remains the trading truth).
- The armed-hours data reflects the CURRENT regime only (regime change re-resolves
  automatically since the grid is computed per request from `self._btc_regime`).
- Dashboard token: rotate `.env.dashboard` DASHBOARD_TOKEN if desired (would need
  one dashboard-service restart; not required for this change).