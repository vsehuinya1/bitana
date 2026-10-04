"""Sunday read of the live-Bitana watch rows from the shadow DB (2026-10-04; owner: "Can we read those arms from shadow
db? I'm not setting it live anytime soon"). Frozen bars from research/RESEARCH_PLAN.md (rows 1-10, OIGATE amendment,
LON-TAIL) and reports/audit_phase1_gates.md (NY-VOLZ-OFF). Read-only on a backup-API copy.
Population rules (RESEARCH_PLAN "gate-complete" note): parity era >= 2026-09-23T12:36:54Z; WLA=1 rows before mirror
cut-over 3 (2026-09-24T14:12:21Z) need n_confirms >= 1 via the exact burst_snapshots join (symbol + bar_time = entry
time), unmatched = UNKNOWN group (reported, never passes a bar); after cut-over 3, n_confirms column.
Book R: NY pnl_atr / 5 (bull, neutral) or / 10 (bear); London pnl_atr / 6.
Rows needing legs the live gates rejected (NY vol_z<0, London h9/h10, neutral cohorts) use a gate REPLICA built from the
mirror's bound config (the 2026-09-27 restart = config/live_burst_ny_asia.yaml.pre_london_no_bull_20261002) via
config.loader's SessionBurstRule; the replica is validated against the mirror's WLA tags and the agreement is printed.
REPLICA CAVEATS (first run 2026-10-04): it does not model the live book caps (8 slots / per-symbol / cluster) or the
burst floors the mirror applies (NY: replica 309 legs vs mirror 55), and the shadow btc_regime_age_bars column disagrees
with the mirror's own age check on London (19 mirror-accepted legs with column age 29-41 > 17). Replica cells are
therefore approximate (uncapped populations; like-for-like splits only); mirror (WLA) cells are exact.
Usage: venv/bin/python research/watch_rows_shadow_read.py <backup copy of storage/signal_shadow.db>"""
import os, sys, sqlite3, collections
os.environ.pop('API_FOOTBALL_KEY', None)
sys.path.insert(0, '/root/bitana')
from datetime import datetime
from config.loader import load_config

DB = sys.argv[1]
PARITY, CUT3 = '2026-09-23T12:36:54', '2026-09-24T14:12:21'
cfg = load_config('/root/bitana/config/live_burst_ny_asia.yaml.pre_london_no_bull_20261002')
RULES = cfg.burst_follow.session_rules
c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
rows = [dict(r) for r in c.execute("""
  SELECT t.*, b.n_confirms AS snap_nc FROM shadow_trades t
  LEFT JOIN burst_snapshots b ON b.symbol = t.symbol AND b.bar_time = t.entry_time
  WHERE t.status = 'closed' AND t.strategy IN ('ny_flush_buy_1h', 'burst_follow') AND t.entry_time >= ?""", (PARITY,))]
print(f"shadow copy: closed parity-era rows ny_flush_buy_1h + burst_follow = {len(rows)}; latest entry "
      f"{max(r['entry_time'] for r in rows)[:16]}Z")


def arm(r): return 'ny' if r['strategy'] == 'ny_flush_buy_1h' else 'london'


def R(r):
    if arm(r) == 'ny':
        return r['pnl_atr'] / (10.0 if r['btc_trend_state'] == 'bear' else 5.0)
    return r['pnl_atr'] / 6.0


def known(r):
    """gate-complete status: True / False / None (unknown group)."""
    nc = r['snap_nc'] if r['entry_time'] < CUT3 else r['n_confirms']
    return None if nc is None else nc >= 1


def replica(r, skip=()):
    """Live gates of the bound config, minus the ones named in skip ('hour', 'volz', 'regime')."""
    rule = RULES.get(arm(r))
    if rule is None or r['session'] != arm(r):
        return False
    t = datetime.fromisoformat(r['entry_time'])
    wd, reg = t.weekday(), r['btc_trend_state']
    if r['side'] != 'LONG' or (rule.allowed_side and r['side'] != rule.allowed_side):
        return False
    if (r['liq_imb'] or 0) < rule.min_imb:
        return False
    if 'regime' not in skip and reg not in (rule.allowed_btc_regimes or cfg.burst_follow.allowed_btc_regimes):
        return False
    if rule.max_regime_age_bars is not None and (r['btc_regime_age_bars'] is None or r['btc_regime_age_bars'] > rule.max_regime_age_bars):
        return False
    if rule.exclude_weekdays and wd in rule.exclude_weekdays:
        return False
    if 'hour' not in skip and rule.hour_gate_reason(int(r['hour']), wd, reg) is not None:
        return False
    if 'volz' not in skip and (r['entry_vol_z'] is None or r['entry_vol_z'] < rule.min_vol_z):
        return False
    if (r['decile'] or 0) < rule.min_decile:
        return False
    return known(r) is True


def st(xs, lab):
    if not xs:
        return f"{lab}: n=0"
    rs = [R(x) for x in xs]; d = collections.defaultdict(float)
    for x, v in zip(xs, rs): d[x['entry_time'][:10]] += v
    s = sum(rs); top = max(d.values())
    tops = f"{top / s:.0%}" if s > 0 else 'n/a (sum<=0)'
    return (f"{lab}: n={len(rs)} days={len(d)} E={s / len(rs):+.3f}R sumR={s:+.2f} top-day={tops}", len(rs), len(d), s / len(rs), (top / s if s > 0 else None))


def show(xs, lab):
    out = st(xs, lab); print('  ' + (out if isinstance(out, str) else out[0])); return out


# ---- replica validation against the mirror's WLA tags (known rows only)
for a in ('ny', 'london'):
    k = [r for r in rows if arm(r) == a and known(r) is True]
    agree = sum((r['would_live_accept'] == 1) == replica(r) for r in k)
    tp = sum(r['would_live_accept'] == 1 and replica(r) for r in k)
    print(f"replica vs mirror ({a}, known rows): agree {agree}/{len(k)} ({agree / max(1, len(k)):.1%}); WLA=1 {sum(r['would_live_accept'] == 1 for r in k)}, replica {sum(replica(r) for r in k)}, both {tp}")

book = [r for r in rows if r['would_live_accept'] == 1 and known(r) is True]
unknown = [r for r in rows if r['would_live_accept'] == 1 and known(r) is None]
print("\n== ARMS (mirror WLA=1, gate-complete), parity era")
for a in ('ny', 'london'):
    for reg in ('bull', 'bear', 'neutral'):
        xs = [r for r in book if arm(r) == a and r['btc_trend_state'] == reg]
        if xs: show(xs, f"{a} {reg}")
    show([r for r in book if arm(r) == a and r['entry_time'] >= '2026-10-03T05:46'], f"{a} since live arms off (10-03 05:46Z)")
show(unknown, "UNKNOWN group (never passes a bar)")

print("\n== NY-VOLZ-OFF (kill/keep gate: shadow vol_z<0 E < +0.02 at n>=40 / >=5d, or top-day > 40%)")
ny_novz = [r for r in rows if arm(r) == 'ny' and replica(r, skip=('volz',))]
lo = show([r for r in ny_novz if (r['entry_vol_z'] or 0) < 0], "ny vol_z<0 (replica)")
show([r for r in ny_novz if (r['entry_vol_z'] or 0) >= 0], "ny vol_z>=0 (replica)")

fl = [r for r in book if arm(r) == 'ny']
print("\n== FK1 (flush_1h; promote: blocked n>=30 E<=0 AND kept E>=+0.085 AND top<=40%; kill: kept top>60% or blocked E>=+0.05)")
b1 = [r for r in fl if (r['btc_realized_vol_24h'] or 1) < 0.07]; k1 = [r for r in fl if (r['btc_realized_vol_24h'] or 1) >= 0.07]
show(b1, "blocked rv<0.07")
for reg in ('bull', 'neutral', 'bear'):
    xs = [r for r in b1 if r['btc_trend_state'] == reg]
    if xs: show(xs, f"   blocked {reg}")
show(k1, "kept rv>=0.07")
print("== FK2 (marginal blocked = rv>=0.07 AND flow<250k: must be E<=0 at n>=20, else kill)")
show([r for r in k1 if (r['market_liq_flow_usd'] or 0) < 250_000], "FK2 marginal blocked")
print("== FK3 counter (vol_z>=2 AND liq_imb>=0.97; promote: hits E<=0 at n>=40 AND kept >= +0.085)")
h3 = [r for r in fl if (r['entry_vol_z'] or 0) >= 2 and (r['liq_imb'] or 0) >= 0.97]
show(h3, "FK3 hits"); show([r for r in fl if r not in h3], "FK3 kept")

print("\n== PREREG-OIGATE (re-arm: oi>1.0 E<-0.02 at n>=100/>=5d top<=40%; dark forever: oi>0.5 E >= pass E at n>=150)")
show([r for r in book if (r['oi_delta_30m_pct'] or 0) > 1.0], "oi > 1.0")
show([r for r in book if (r['oi_delta_30m_pct'] or 0) > 0.5], "oi > 0.5")
show([r for r in book if (r['oi_delta_30m_pct'] or 0) <= 0.5], "pass (oi <= 0.5)")

lon_nh = [r for r in rows if arm(r) == 'london' and replica(r, skip=('hour',)) and (r['entry_vol_z'] or 0) >= 0]
print("\n== Row 8 LON-BULL-NARROW (cut h9-10 if cut E<0 at n>=50/>=5d top<=40% AND kept h11-13 E>=+0.085 top<=40%; drop watch if cut E>=+0.05 at n>=50)")
lb = [r for r in lon_nh if r['btc_trend_state'] == 'bull']
show([r for r in lb if int(r['hour']) in (9, 10)], "cut lanes h9-10 (replica, no hour gate)")
show([r for r in lb if int(r['hour']) in (11, 12, 13)], "kept h11-13")
print("== LON-TAIL shadow clause (drop h9:00-29 if parity-era shadow E<-0.02 at n>=30/>=5d); live-paired clause: no live legs")
show([r for r in lon_nh if int(r['hour']) == 9 and int(r['entry_time'][14:16]) < 30], "london h9:00-29 (all live regimes)")

print("\n== Row 9 NY-ZEC (veto: n>=30 E<=-0.02 top<=40%; kill: E>=+0.02 at n>=30)")
show([r for r in book if arm(r) == 'ny' and r['btc_trend_state'] == 'bull' and r['symbol'] == 'ZECUSDT'], "ny bull ZEC")
print("== Row 10 LON-DECILE (evaluate at n(D2+)>=100)")
lbk = [r for r in book if arm(r) == 'london' and r['btc_trend_state'] == 'bull']
show([r for r in lbk if (r['decile'] or 0) <= 1], "london bull D1"); show([r for r in lbk if (r['decile'] or 0) >= 2], "london bull D2+")
print("\n== Row 1 NY-NEUT-KEEP (kill: parity-era neutral E<0 at n>=30/>=5d or top>40%) — NY neutral disabled since 09-27")
show([r for r in rows if arm(r) == 'ny' and r['btc_trend_state'] == 'neutral' and replica(r, skip=('regime',))], "ny neutral (replica, regime gate skipped)")
print("== Row 2 LON-NEUT-H13 (promote: E>+0.05 at n>=100/>=5d top<=40%; kill: E<0 at n>=100)")
show([r for r in rows if arm(r) == 'london' and r['btc_trend_state'] == 'neutral' and int(r['hour']) in (12, 13)
      and replica(r, skip=('regime', 'hour'))], "london neutral h12-13, vol_z>=0 (replica)")
print("== Row 4 NEUT-STOP: population = live 1h-arm legs only -> not readable from shadow (no live legs since arms off)")
