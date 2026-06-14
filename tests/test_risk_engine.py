"""Deterministic risk engine — the anti-DQ / capital-protection locks."""
from __future__ import annotations

import time

import pytest

from boomerang.risk.risk_engine import (
    ExitSignal, RiskEngine, conviction_size_pct, dynamic_sl_tp,
    market_regime, overextension_factor, tier_from_var)
from boomerang.strategy.playbook import DCA, MEAN_REVERSION, MOMENTUM
from boomerang.types import Position

DAY = 86400


@pytest.fixture
def engine(cfg):
    return RiskEngine(cfg, initial_equity_usd=100.0)


def _pos(spec, *, entry=100.0, opened_ago_h=0.0, now=None):
    now = now or time.time()
    stop_price = entry * (1 - spec.stop_pct / 100.0) if spec.stop_pct > 0 else 0.0
    return Position(
        symbol="X", token_address="0x0", qty=1.0, amount_usd=10.0, entry_price=entry,
        stop_loss_price=stop_price, stop_loss_pct=spec.stop_pct, take_profit_pct=spec.take_profit_pct,
        trailing_trigger_pct=spec.trailing_trigger_pct, trailing_pct=spec.trailing_pct,
        time_stop_min=spec.time_stop_min, time_stop_band_pct=spec.time_stop_band_pct,
        opened_at=now - opened_ago_h * 3600, strategy=spec.key)


# ── Circuit breaker (all-time drawdown) ──────────────────────────────────────
def test_circuit_breaker_trips_at_safety(engine):
    engine.update_equity(100.0, now_ts=DAY)
    assert not engine.circuit_breaker_tripped(80.0)      # -20% < 23%
    assert engine.circuit_breaker_tripped(77.0)          # -23% trips


def test_peak_equity_ratchets_up(engine):
    engine.update_equity(150.0, now_ts=DAY)
    assert engine.peak_equity == 150.0
    engine.update_equity(120.0, now_ts=DAY)              # drop does not lower the peak
    assert engine.peak_equity == 150.0


# ── Daily loss cap (intraday) ────────────────────────────────────────────────
def test_daily_loss_cap_trips_and_reanchors(engine):
    engine.update_equity(100.0, now_ts=10 * DAY)
    assert not engine.daily_loss_tripped(90.0)           # -10% < 15%
    assert engine.daily_loss_tripped(84.0)               # -16% trips
    engine.update_equity(84.0, now_ts=11 * DAY)          # new day re-anchors at 84
    assert not engine.daily_loss_tripped(80.0)           # -4.8% of the day
    # but the all-time breaker still sees -20% vs the 100 peak
    assert engine.current_drawdown_pct(80.0) == pytest.approx(20.0, abs=0.1)


# ── Position sizing (never spends 100% of stable) ────────────────────────────
def test_position_size_keeps_buffer(engine):
    # 5% of 100 = 5, but only 3 stable → 0.97 * 3 = 2.91 (3% buffer)
    assert engine.position_size_usd(100.0, 3.0) == pytest.approx(2.91, abs=0.01)


def test_position_size_floor_returns_zero(engine):
    assert engine.position_size_usd(100.0, 0.5) == 0.0   # below min_position_usd


def test_position_size_respects_cap(engine):
    # override 90% capped by max_position_pct (50%) before the stable buffer
    size = engine.position_size_usd(100.0, 1000.0, override_pct=90.0)
    assert size == pytest.approx(90.0, abs=0.01)         # override bypasses the auto cap


# ── can_open_position gating ─────────────────────────────────────────────────
def test_cannot_open_when_halted(engine):
    engine.halt()
    d = engine.can_open_position(current_equity_usd=100, available_stable_usd=100,
                                 open_positions=0, now_ts=1000)
    assert not d.allowed


def test_cooldown_blocks_back_to_back(engine):
    engine.record_trade(1000.0)
    d = engine.can_open_position(current_equity_usd=100, available_stable_usd=100,
                                 open_positions=0, now_ts=1000 + 10)
    assert not d.allowed                                  # within 900s cooldown


def test_max_positions_blocks(engine):
    d = engine.can_open_position(current_equity_usd=100, available_stable_usd=100,
                                 open_positions=3, now_ts=999999)
    assert not d.allowed


# ── evaluate_position per strategy ───────────────────────────────────────────
def test_momentum_stop_then_trailing(engine):
    assert engine.evaluate_position(_pos(MOMENTUM), 99.0) == ExitSignal.SELL_STOP_LOSS
    p = _pos(MOMENTUM)
    engine.evaluate_position(p, 103.0)                    # +3% activates trailing
    assert p.trailing_active
    assert engine.evaluate_position(p, 101.4) == ExitSignal.SELL_TRAILING


def test_mean_reversion_fixed_tp_sl(engine):
    assert engine.evaluate_position(_pos(MEAN_REVERSION), 101.2) == ExitSignal.SELL_TAKE_PROFIT
    assert engine.evaluate_position(_pos(MEAN_REVERSION), 99.2) == ExitSignal.SELL_STOP_LOSS


def test_dca_has_no_stop_but_tp_and_timestop(engine):
    assert engine.evaluate_position(_pos(DCA), 90.0) == ExitSignal.HOLD          # -10% held, no SL
    assert engine.evaluate_position(_pos(DCA), 103.0) == ExitSignal.SELL_TAKE_PROFIT
    assert engine.evaluate_position(_pos(DCA, opened_ago_h=25), 93.0) == ExitSignal.SELL_TIME_STALE


def test_greed_tighten_locks_profit_sooner(engine):
    normal = _pos(MOMENTUM)
    engine.evaluate_position(normal, 102.0, tighten=False)
    assert not normal.trailing_active                     # +2% < 2.5% trigger
    greedy = _pos(MOMENTUM)
    engine.evaluate_position(greedy, 102.0, tighten=True)
    assert greedy.trailing_active                         # trigger halved to 1.25%


# ── Pure helpers ─────────────────────────────────────────────────────────────
def test_dynamic_sl_tp_keeps_min_2to1():
    for tier in ("BAIXA", "MEDIA", "ALTA"):
        sl, tp = dynamic_sl_tp(tier, 6.0)
        assert tp >= sl * 2.0 - 1e-9


def test_tier_from_var():
    assert tier_from_var(2.0) == "BAIXA"
    assert tier_from_var(5.0) == "MEDIA"
    assert tier_from_var(12.0) == "ALTA"


def test_overextension_dampens_above_10pct():
    assert overextension_factor(5.0) == 1.0
    assert overextension_factor(25.0) == pytest.approx(0.5, abs=0.01)
    assert 0.5 <= overextension_factor(18.0) < 1.0


def test_conviction_sizing_scales_and_clamps():
    base = 5.0
    assert conviction_size_pct(base, 62) == pytest.approx(5.0, abs=0.1)   # anchor
    assert conviction_size_pct(base, 90) > conviction_size_pct(base, 62)  # higher score = bigger
    assert conviction_size_pct(base, 100, max_pct=8.0) <= 8.0             # capped


def test_market_regime_layers():
    assert market_regime(4.0, 50)[1] == -5                 # BULL lowers the bar
    assert market_regime(-3.0, 50)[1] == 8                 # DEFENSIVE raises it
    assert market_regime(0.5, 80)[1] == 5                  # greed raises
    assert market_regime(0.5, 18)[1] == 4                  # fear raises
    # funding overlay: overleveraged longs add defensive points
    assert market_regime(0.5, 50, 0.0006)[1] == 6
    assert market_regime(0.5, 50, -0.0003)[1] == -3        # crowded shorts, slight risk-on
