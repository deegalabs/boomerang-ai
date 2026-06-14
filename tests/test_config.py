"""Config layers and the safety-critical numeric values (anti-DQ thresholds)."""
from __future__ import annotations


def test_safety_thresholds(cfg):
    # Drawdown circuit breaker trips BEFORE the competition DQ line.
    assert cfg.drawdown_safety_pct == 23.0
    assert cfg.drawdown_dq_pct == 30.0
    assert cfg.drawdown_safety_pct < cfg.drawdown_dq_pct
    # Intraday cap is tighter than the all-time breaker.
    assert cfg.daily_loss_cap_pct == 15.0
    assert cfg.daily_loss_cap_pct < cfg.drawdown_safety_pct


def test_confidence_cutoff_is_mode_aware(cfg):
    # The effective cutoff follows user.mode; the dev_safety base is the fallback.
    assert cfg.dev_safety["min_confidence_score"] == 55
    assert cfg.dev_safety["min_confidence_score_conservative"] == 58
    assert cfg.dev_safety["min_confidence_score_aggressive"] == 52
    mode = str(cfg.user.get("mode", "conservative")).lower()
    expected = cfg.dev_safety.get(f"min_confidence_score_{mode}", 55)
    assert cfg.min_confidence_score == expected


def test_sizing_defaults(cfg):
    # Effective base size follows the user override (25%); dev_safety base (5%) is the fallback.
    assert cfg.dev_safety["position_size_pct"] == 5.0
    assert cfg.position_size_pct == 25.0
    assert cfg.max_position_pct == 50.0
    assert cfg.min_position_usd == 1.0


def test_exit_and_guard_defaults(cfg):
    assert cfg.max_hold_hours == 6.0
    assert cfg.stale_pnl_band_pct == 1.5
    assert cfg.stable_depeg_bps == 100.0
    assert cfg.max_entry_24h_pct == 25.0
    assert cfg.max_concurrent_positions == 3
    assert cfg.trade_cooldown_seconds == 900
