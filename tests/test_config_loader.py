"""Config loader merge tests."""
from config.loader import load_config, resolve_symbol_config


def test_burst_follow_top_level_wins_over_symbol_defaults(tmp_path):
    yaml = tmp_path / "cfg.yaml"
    yaml.write_text(
        """
config_version: "1.0.0"
mode: live
symbols:
  active: [BTCUSDT]
  defaults:
    risk_pct: 4.0
burst_follow:
  risk_pct: 4.0
  btc_regime_gate_enabled: true
  allowed_btc_regimes: [neutral, bear]
  session_rules:
    asia:
      shadow_strategy: asia_pump_short_4h
      side_mode: follow
      neg_imb_only: true
      exclude_weekdays: [5, 6]
"""
    )
    cfg = load_config(yaml)
    resolved = resolve_symbol_config(cfg, "BTCUSDT")

    assert resolved.burst_follow.risk_pct == 4.0
    assert resolved.burst_follow.btc_regime_gate_enabled is True
    assert resolved.burst_follow.allowed_btc_regimes == ["neutral", "bear"]
    assert resolved.burst_follow.session_rules["asia"].exclude_weekdays == [5, 6]
