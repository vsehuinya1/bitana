"""WLA-mirror parity guard (2026-09-01).

The WLA mirror's live gates were hand-synced constants that drifted twice
(flat {16,17} hour set zeroed live bull h18-20 WLA rows; asia [neutral,bear]
vs the session-level ["neutral"] override). The mirror now binds gates from
config/live_burst_ny_asia.yaml at import (research/signal_shadow._apply_
live_gates) through the SAME typed loader objects the live engine uses, and
the per-bar hour decision goes through SessionBurstRule.hour_gate_reason —
the one shared resolver the engine itself calls.

This test cross-checks the mirror against an INDEPENDENT derivation from the
raw yaml (yaml.safe_load, no typed loader) over the full
(arm x weekday x regime x hour x decile) grid, so a future desync — mirror
bug, loader bug, or yaml semantic change — fails here before it silently
skews live-mirrored stats.
"""
import os
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

LIVE_YAML = REPO / "config" / "live_burst_ny_asia.yaml"
# shadow strategy -> live yaml session arm (must mirror _LIVE_ARM_FOR_STRATEGY)
STRATEGY_FOR_ARM = {
    "london": "burst_follow",
    "ny": "ny_flush_buy_4h",
    "asia": "asia_pump_short_4h",
}
REGIMES = ("bull", "neutral", "bear")
WEEKDAYS = range(7)  # python convention: 0=Mon..6=Sun (same as the engine gate)
HOURS = range(24)


@pytest.fixture(scope="module", autouse=True)
def _scrub_env():
    saved = os.environ.pop("API_FOOTBALL_KEY", None)  # pydantic chokes on it
    yield
    if saved is not None:
        os.environ["API_FOOTBALL_KEY"] = saved


def _raw_cfg() -> dict:
    with open(LIVE_YAML) as fh:
        return yaml.safe_load(fh)


def _raw_gate_open(raw_rule: dict, bf: dict, wd: int, regime: str, hour: int, decile: int) -> bool:
    """Independent re-derivation of the engine's per-bar gate from RAW yaml.

    Mirrors engines/liq_burst_follow_engine.py evaluate order: session
    weekday -> regime (session override, else global fallback) -> hour
    (regime_hours override, else hours; falsy = no hour gate) -> flat
    weekday-hour exclusions -> regime-scoped weekday-hour exclusions ->
    min_decile (typed-loader default 2 when the key is absent).
    """
    if wd in (raw_rule.get("exclude_weekdays") or []):
        return False
    allowed = raw_rule.get("allowed_btc_regimes") or bf.get("allowed_btc_regimes")
    if regime not in (allowed or []):
        return False
    hours = raw_rule.get("hours")
    regime_hours = raw_rule.get("regime_hours") or {}
    if regime in regime_hours:
        hours = regime_hours[regime]
    if hours and hour not in hours:
        return False
    if hour in ((raw_rule.get("excluded_weekday_hours") or {}).get(wd) or []):
        return False
    exc_regime = (raw_rule.get("excluded_weekday_regime_hours") or {}).get(wd, {}).get(regime) or []
    if hour in exc_regime:
        return False
    if decile < raw_rule.get("min_decile", 2):  # 2 = typed-loader SessionBurstRule default
        return False
    return True


def _mirror_gate_open(spec, wd: int, regime: str, hour: int, decile: int) -> bool:
    """The production WLA block (research/signal_shadow.py) as a predicate.

    Keep in sync with the production block — it is intentionally a copy so a
    drift between the two is a test failure, not a silent stats skew.
    """
    g = spec.live_gates
    if g is None:
        return True
    if wd in g.exclude_weekdays:
        return False
    if regime not in g.allowed_regimes:
        return False
    if g.rule is not None and g.rule.hour_gate_reason(hour, wd, regime) is not None:
        return False
    if g.min_decile > 0 and decile < g.min_decile:
        return False
    return True


def test_every_session_arm_has_mirror_mapping():
    from research.signal_shadow import _LIVE_ARM_FOR_STRATEGY

    arms = set(_raw_cfg()["burst_follow"].get("session_rules") or {})
    mapped = set(_LIVE_ARM_FOR_STRATEGY.values())
    assert arms <= mapped, f"yaml arms without WLA mirror mapping: {sorted(arms - mapped)}"


def test_mirror_binds_expected_arms():
    from research.signal_shadow import _STRATEGY_BY_NAME

    for arm, strat in STRATEGY_FOR_ARM.items():
        spec = _STRATEGY_BY_NAME[strat]
        assert spec.live_gates is not None, f"{strat} has no live_gates binding"
        assert spec.live_gates.session == arm


def test_wla_parity_full_grid():
    """Mirror WLA == independent raw-yaml engine derivation, full grid."""
    from research.signal_shadow import _STRATEGY_BY_NAME

    bf_raw = _raw_cfg()["burst_follow"]
    raw_rules = bf_raw["session_rules"]
    for arm, strat in STRATEGY_FOR_ARM.items():
        spec = _STRATEGY_BY_NAME[strat]
        if arm not in raw_rules:
            # Session-disable pattern (arm commented out of live yaml):
            # grid parity is vacuous — the dark-pin contract is covered by
            # test_disabled_arm_pins_wla_to_zero.
            continue
        raw_rule = raw_rules[arm]
        mismatches = []
        for wd in WEEKDAYS:
            for regime in REGIMES:
                for hour in HOURS:
                    for decile in (0, 1, 2, 3):
                        mirror = _mirror_gate_open(spec, wd, regime, hour, decile)
                        engine = _raw_gate_open(raw_rule, bf_raw, wd, regime, hour, decile)
                        if mirror != engine:
                            mismatches.append((wd, regime, hour, decile, mirror, engine))
        assert not mismatches, (
            f"{arm}: {len(mismatches)} grid mismatches, first 5: {mismatches[:5]}"
        )


def test_tuesday_neutral_ny_dark():
    """2026-09-01 owner order: Tue neutral NY hours REMOVED completely.

    Tue is NOT session-excluded for the ny arm, so darkness must come from
    the hour gates alone (excluded_weekday_regime_hours {1: {neutral: [16,17]}}
    plus the flat Tue map) — assert every hour is closed via the shared
    resolver, and that Tue bull / Wed+Fri cells took no collateral damage.
    """
    from research.signal_shadow import _STRATEGY_BY_NAME

    g = _STRATEGY_BY_NAME["ny_flush_buy_4h"].live_gates
    for hour in HOURS:
        reason = g.rule.hour_gate_reason(hour, 1, "neutral")
        assert reason is not None, f"Tue neutral NY unexpectedly open at h{hour}"
    # collateral-damage checks on the shared resolver
    for wd, regime, hours_open in [
        (1, "bull", [14, 16]),        # Tue bull unchanged
        (2, "neutral", [16, 17]),     # Wed neutral unchanged
        (2, "bull", [14, 16, 17, 19, 20]),
        (4, "bull", [14, 16, 17, 18, 19, 20]),  # Fri bull incl. wired h18/h20
    ]:
        resolved = [h for h in HOURS if g.rule.hour_gate_reason(h, wd, regime) is None]
        assert resolved == hours_open, f"wd{wd}/{regime}: {resolved} != {hours_open}"


def test_oi_gate_binds_from_live_yaml():
    """PREREG-OIGATE parity: the WLA mirror's OI gate keys must equal the
    raw yaml values the live engine reads (independent yaml.safe_load)."""
    raw = yaml.safe_load(LIVE_YAML.read_text())
    bf = raw["burst_follow"]
    from research.signal_shadow import _STRATEGY_BY_NAME

    snap = _STRATEGY_BY_NAME["burst_follow"].live_gates
    assert snap is not None
    assert snap.oi_gate_enabled == bool(bf["oi_inflow_gate_enabled"])
    assert snap.oi_inflow_max_pct == float(bf["oi_inflow_max_pct"])
    # and the engine-side loader must agree with the same raw yaml
    from config.loader import LiqBurstFollowConfig

    cfg = LiqBurstFollowConfig(**{k: v for k, v in bf.items()
                                  if k in LiqBurstFollowConfig.model_fields})
    assert cfg.oi_inflow_gate_enabled == bool(bf["oi_inflow_gate_enabled"])
    assert cfg.oi_inflow_max_pct == float(bf["oi_inflow_max_pct"])


def test_dist_cap_bindings():
    """PREREG-ASIA-DISTCAP parity: NY/London rules carry no dist cap (None =
    inert); the field exists on SessionBurstRule and LiveGateSnapshot and the
    mirror binding copies the rule's value (asia's commented block carries
    5.0 for the future re-arm — verified by text, loader can't see comments)."""
    from config.loader import SessionBurstRule
    from research.signal_shadow import _STRATEGY_BY_NAME

    assert SessionBurstRule.model_fields["btc_dist_max_pct"].default is None
    # active arms bind None (no cap key in the live yaml session rules)
    for strat in ("burst_follow", "ny_flush_buy_4h"):
        g = _STRATEGY_BY_NAME[strat].live_gates
        assert g is not None and g.btc_dist_max_pct is None, strat
    # commented asia block documents the cap for the re-arm path
    assert "btc_dist_max_pct: 5.0" in LIVE_YAML.read_text()


def test_disabled_arm_pins_wla_to_zero():
    """A session rule commented out of the live yaml must dark-flag its
    shadow arm (WLA=0 on every cell), never silently keep stale gates."""
    from research.signal_shadow import LiveGateSnapshot, _STRATEGY_BY_NAME

    # build a disabled snapshot the way _apply_live_gates does
    disabled = LiveGateSnapshot(
        session="ghost", rule=None, exclude_weekdays=frozenset(range(7)),
        allowed_regimes=frozenset({"__arm_disabled__"}), min_decile=0,
    )
    spec = _STRATEGY_BY_NAME["ny_flush_buy_4h"]
    saved = spec.live_gates
    object.__setattr__(spec, "live_gates", disabled)
    try:
        for wd in WEEKDAYS:
            for regime in REGIMES:
                assert not _mirror_gate_open(spec, wd, regime, 12, 3)
    finally:
        object.__setattr__(spec, "live_gates", saved)
