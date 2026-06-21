"""Regime-routed strategy selection, action matrix and expectancy arbiter."""
from __future__ import annotations

from boomerang.strategy.playbook import (
    expectancy_disabled, regime_posture, select_strategy, setup_strength)

MOMENTUM = {"percent_change_1h": 3.0, "percent_change_24h": 6.0, "volume_change_24h_pct": 35}
MEANREV = {"percent_change_1h": -3.0, "percent_change_24h": 5.0, "volume_change_24h_pct": 10}
# DCA = deep drop + bounce STARTED (1h > +0.5%) — the Crisis Rebound condition.
DCA = {"percent_change_1h": 1.0, "percent_change_24h": -12.0, "volume_change_24h_pct": 80}
DCA_FALLING = {"percent_change_1h": -4.0, "percent_change_24h": -12.0, "volume_change_24h_pct": 80}
FLAT = {"percent_change_1h": 0.3, "percent_change_24h": 0.5, "volume_change_24h_pct": 5}


def test_momentum_routing():
    assert select_strategy(50, MOMENTUM).key == "momentum"


def test_mean_reversion_routing():
    assert select_strategy(50, MEANREV).key == "mean_reversion"


def test_dca_only_after_bounce_started():
    assert select_strategy(18, DCA).key == "dca"
    # Crisis Rebound: still falling (1h negative) → don't catch the knife.
    assert select_strategy(18, DCA_FALLING) is None
    # Panic without the -10% drop → no trade (scalping forbidden in panic).
    assert select_strategy(18, MOMENTUM) is None


def test_fear_but_stable_tape_routes_normally():
    # Extreme fear but BTC flat/up (no crash) → don't idle: route momentum/mean-rev normally.
    assert select_strategy(18, MOMENTUM, btc_24h=0.5).key == "momentum"
    assert select_strategy(18, MEANREV, btc_24h=0.5).key == "mean_reversion"
    # A deep-drop token in a stable tape is NOT a crash-DCA setup → no trade.
    assert select_strategy(18, DCA, btc_24h=0.5) is None
    # A FALLING tape keeps the DCA-only lockdown.
    assert select_strategy(18, MOMENTUM, btc_24h=-3.0) is None
    assert select_strategy(18, DCA, btc_24h=-3.0).key == "dca"


def test_no_setup_returns_none():
    assert select_strategy(50, FLAT) is None


def test_missing_metrics_returns_none():
    assert select_strategy(50, {"percent_change_1h": None, "percent_change_24h": 1}) is None


def test_setup_strength_positive():
    assert setup_strength(select_strategy(50, MOMENTUM), MOMENTUM) > 0


# ── Action Matrix (regime → posture) ─────────────────────────────────────────
def test_posture_panic_only_dca():
    p = regime_posture(fng=18, btc_24h=-3.0, funding=None)
    assert p.allowed == frozenset({"dca"}) and p.max_positions == 2


def test_posture_fear_but_stable_allows_cautious_risk():
    # Fear (F&G<25) but a stable/up tape → cautious risk, not a full DCA-only lockdown.
    p = regime_posture(fng=18, btc_24h=0.5, funding=None)
    assert p.label == "FEAR_STABLE" and "momentum" in p.allowed and "mean_reversion" in p.allowed
    assert p.size_mult < 0.7 and p.max_positions == 2  # smaller than PANIC's 0.7
    # A falling tape (BTC <= -2%) still locks down to DCA-only.
    assert regime_posture(fng=18, btc_24h=-3.0, funding=None).allowed == frozenset({"dca"})


def test_posture_risk_off_stands_down():
    p = regime_posture(fng=50, btc_24h=-6.0, funding=None)
    assert p.size_mult == 0.0 and p.max_positions == 0 and not p.allowed


def test_posture_defensive_shrinks():
    p = regime_posture(fng=80, btc_24h=0.0, funding=None)  # extreme greed
    assert p.size_mult < 1.0 and p.max_positions == 2 and "momentum" in p.allowed


def test_posture_bull_full_weight():
    p = regime_posture(fng=50, btc_24h=4.0, funding=None)
    assert p.size_mult == 1.0 and p.max_positions == 3


def test_posture_overleveraged_funding_is_defensive():
    p = regime_posture(fng=50, btc_24h=0.0, funding=0.0006)
    assert p.size_mult < 1.0


# ── Expectancy arbiter ───────────────────────────────────────────────────────
def _closes(strategy, pnls):
    return [{"strategy": strategy, "pnl_pct": x} for x in pnls]


def test_arbiter_disables_negative_expectancy():
    # 8 trades, avg clearly negative → disabled (even if some are wins).
    closes = _closes("momentum", [-3, 2, -4, -2, 1, -5, -1, -3])
    assert "momentum" in expectancy_disabled(closes)


def test_arbiter_keeps_positive_expectancy():
    closes = _closes("mean_reversion", [2, -1, 3, 1, -1, 2, 1, 2])
    assert expectancy_disabled(closes) == set()


def test_arbiter_dormant_below_min_trades():
    closes = _closes("dca", [-9, -9])  # very negative but only 2 trades
    assert expectancy_disabled(closes) == set()
