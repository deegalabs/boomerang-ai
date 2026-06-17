"""Per-trade EV projection — pure expected-value math."""
from __future__ import annotations

from boomerang.strategy.projection import est_win_from_conviction, project


def test_est_win_monotonic_and_capped():
    assert est_win_from_conviction(0) == 30.0
    assert est_win_from_conviction(50) < est_win_from_conviction(80)   # monotonic
    assert est_win_from_conviction(100) <= 70.0                        # never over-promises
    assert est_win_from_conviction(999) == est_win_from_conviction(100)  # clamps out-of-range


def test_fixed_target_breakeven_and_ev():
    # TP 8% / stop 4% / fee 0.5% → breakeven win = (4+0.5)/(8+4) = 37.5%
    p = project(target_pct=8.0, stop_pct=4.0, conviction=80, fee_pct=0.5)
    assert p.target_pct == 8.0 and p.stop_pct == 4.0
    assert p.rr == 2.0
    assert abs(p.breakeven_win - 37.5) < 0.1
    # est_win(80)=~55.6 > 37.5 → favorable, positive EV
    assert p.est_win > p.breakeven_win
    assert p.ev_pct > 0 and p.favorable is True
    assert "TP 8.0%" in p.basis


def test_low_conviction_is_unfavorable():
    # weak conviction on a tight target → EV negative
    p = project(target_pct=1.5, stop_pct=2.0, conviction=20, fee_pct=0.5)
    assert p.favorable is False and p.ev_pct < 0


def test_trailing_target_uses_atr_then_trigger():
    # TP 0 (trailing) with ATR → target ~2*ATR
    p = project(target_pct=0.0, stop_pct=1.0, conviction=60, atr_pct=1.2)
    assert p.target_pct == 2.4 and "ATR" in p.basis
    # TP 0, no ATR → falls back to the trailing trigger
    q = project(target_pct=0.0, stop_pct=1.0, conviction=60, trailing_trigger_pct=2.5)
    assert q.target_pct == 2.5 and "trail" in q.basis


def test_zero_stop_uses_nominal_risk_no_div_error():
    # DCA-style: stop_pct=0 (no fixed SL) must not divide by zero
    p = project(target_pct=3.0, stop_pct=0.0, conviction=50)
    assert p.breakeven_win > 0 and p.ev_pct == p.ev_pct  # not NaN
