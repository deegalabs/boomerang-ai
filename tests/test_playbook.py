"""Regime-routed strategy selection (momentum / mean-reversion / DCA)."""
from __future__ import annotations

from boomerang.strategy.playbook import select_strategy, setup_strength

MOMENTUM = {"percent_change_1h": 3.0, "percent_change_24h": 6.0, "volume_change_24h_pct": 35}
MEANREV = {"percent_change_1h": -3.0, "percent_change_24h": 5.0, "volume_change_24h_pct": 10}
DCA = {"percent_change_1h": -4.0, "percent_change_24h": -12.0, "volume_change_24h_pct": 80}
FLAT = {"percent_change_1h": 0.3, "percent_change_24h": 0.5, "volume_change_24h_pct": 5}


def test_momentum_routing():
    assert select_strategy(50, MOMENTUM).key == "momentum"


def test_mean_reversion_routing():
    assert select_strategy(50, MEANREV).key == "mean_reversion"


def test_dca_only_in_panic():
    assert select_strategy(18, DCA).key == "dca"
    # Same panic regime, no -10% trigger → no trade (scalping forbidden in panic).
    assert select_strategy(18, MOMENTUM) is None


def test_no_setup_returns_none():
    assert select_strategy(50, FLAT) is None


def test_missing_metrics_returns_none():
    assert select_strategy(50, {"percent_change_1h": None, "percent_change_24h": 1}) is None


def test_setup_strength_orders_young_over_late():
    young = select_strategy(50, MOMENTUM)
    assert setup_strength(young, MOMENTUM) > 0
